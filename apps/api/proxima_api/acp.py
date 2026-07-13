"""Agent Client Protocol (ACP) integration for the Hermes runner.

Proxima drives Hermes through ACP (the same standard editors like Zed/VS Code
use): a persistent `hermes acp` subprocess per profile, JSON-RPC 2.0 over stdio
(newline-delimited). This gives native session continuity (load/resume), token
streaming, and tool events — without coupling to any vendor-specific gateway.

One AcpProcess per HERMES_HOME hosts many ACP sessions (one per Proxima chat).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable

from .runners import augmented_path

logger = logging.getLogger("proxima.acp")

UpdateHandler = Callable[[dict[str, Any]], None]
READ_LIMIT = 16 * 1024 * 1024


def config_sig(hermes_home: str) -> tuple:
    """Signature of the profile's tool config (MCP + skills). When it changes the
    cached agent process is recycled so newly added MCP/skills load on the next run."""
    if not hermes_home:
        return ()
    base = Path(hermes_home)
    sig = []
    # Watch every runner's skill/MCP surface so activating skills or toggling MCP
    # (which mutates these in the profile home) recycles the cached agent process:
    #   hermes → config.yaml; claude → skills/ + .claude.json; codex → config.toml.
    for rel in ("config.yaml", "skills", ".skills_prompt_snapshot.json", ".claude.json", "config.toml"):
        try:
            sig.append(round((base / rel).stat().st_mtime, 3))
        except OSError:
            sig.append(0.0)
    return tuple(sig)


def _permission_timeout_outcome(options: list[dict[str, Any]]) -> dict[str, Any]:
    """What we tell the agent when the user didn't answer a permission prompt in
    time. Default: CANCEL — an unattended agent must not auto-proceed on a
    privileged action (the user opted into interactive review by turning OFF
    auto-approve; "no answer" should never silently become "yes"). Set
    PROXIMA_ACP_TIMEOUT_ACTION=allow to restore the old auto-allow-once
    behavior for a fully autonomous goal loop you trust."""
    action = os.environ.get("PROXIMA_ACP_TIMEOUT_ACTION", "cancel").strip().lower()
    if action in ("allow", "allow_once", "auto"):
        allow = next((o for o in options if o.get("kind") in ("allow_always", "allow_once")), None)
        if allow:
            return {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


class AcpError(Exception):
    pass


class AcpProcess:
    def __init__(self, spec, home: str, cwd: str):
        self.spec = spec
        self.home = home
        self.hermes_home = home  # alias kept for any external references
        self.cwd = cwd
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, UpdateHandler] = {}   # sessionId -> update handler
        self._perm_futures: dict[str, asyncio.Future] = {}  # request_id -> user choice
        self._permission_handlers: dict[str, Any] = {}  # sessionId -> callback(session_id, request_id, options, params)
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._stderr_lines: deque[str] = deque(maxlen=60)
        self._lock = asyncio.Lock()
        self._started = False
        self.config_sig: tuple = ()

    def recent_stderr(self, lines: int = 15, max_chars: int = 1500) -> str:
        """Last few stderr lines from the agent process — the real error behind
        an empty/failed run (auth, rate limit, etc.), otherwise lost."""
        tail = [ln for ln in self._stderr_lines if ln.strip()][-lines:]
        text = "\n".join(tail)
        return text[-max_chars:]

    async def start(self) -> None:
        if self._started:
            return
        env = os.environ.copy()
        if self.home and self.spec.home_env:
            env[self.spec.home_env] = self.home
            os.makedirs(self.home, exist_ok=True)
        env["PATH"] = augmented_path(env.get("PATH"))
        os.makedirs(self.cwd, exist_ok=True)
        # Resolve the launcher to a full path. On Windows `npx` is actually
        # `npx.cmd`; create_subprocess_exec won't apply PATHEXT, so spawning the
        # bare name fails. shutil.which honors PATHEXT (Windows) and PATH (all
        # OSes), so the agent launches cross-platform.
        argv = list(self.spec.spawn_argv)
        resolved = shutil.which(argv[0], path=env["PATH"])
        if resolved:
            argv[0] = resolved
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, cwd=self.cwd, limit=READ_LIMIT,
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_reader = asyncio.create_task(self._read_stderr())
        try:
            # Bound the handshake: a spawned-but-silent agent must not hang here
            # forever. On ANY failure tear down the subprocess + reader tasks —
            # we aren't tracked by the manager yet, so nothing else would reap them.
            init_res = await asyncio.wait_for(self._request("initialize", {"protocolVersion": 1, "clientCapabilities": {}}), timeout=60)
            # Does this agent accept image content blocks in session/prompt? Only send
            # them if it advertises the capability, else fall back to text-only.
            try:
                self._image_capable = bool((((init_res or {}).get("agentCapabilities") or {}).get("promptCapabilities") or {}).get("image"))
            except Exception:
                self._image_capable = False
        except BaseException:
            await self.stop()
            raise
        self.config_sig = config_sig(self.home)
        self._started = True

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                line = await self.proc.stdout.readline()
            except (asyncio.LimitOverrunError, ValueError):
                continue  # skip oversized frame
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._dispatch(msg)
        # process exited: fail any pending requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AcpError("hermes acp process exited"))
        self._pending.clear()
        self._started = False

    async def _read_stderr(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        while True:
            try:
                raw = await self.proc.stderr.readline()
            except (asyncio.LimitOverrunError, ValueError):
                continue
            if not raw:
                break
            self._stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(AcpError(str(msg["error"])))
                else:
                    fut.set_result(msg["result"])
            return
        method = msg.get("method")
        if not method:
            return
        if method == "session/update":
            params = msg.get("params", {})
            handler = self._handlers.get(params.get("sessionId"))
            if handler:
                try:
                    handler(params.get("update", {}))
                except Exception:
                    logger.exception("acp update handler failed")
            return
        # agent -> client request (needs a response by id)
        if "id" in msg:
            # Interactive permission: if a handler is registered, ask the user
            # (emit event + await their choice) instead of auto-allowing.
            if msg.get("method") == "session/request_permission":
                session_id = (msg.get("params") or {}).get("sessionId")
                handler = self._permission_handlers.get(session_id)
                if handler:
                    asyncio.create_task(self._handle_permission(msg, handler))
                    return
                self._respond_to_agent(msg)
                return
            self._respond_to_agent(msg)

    async def _handle_permission(self, msg: dict[str, Any], handler) -> None:
        rid = str(msg.get("id"))
        params = msg.get("params", {})
        options = params.get("options", [])
        session_id = params.get("sessionId")
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._perm_futures[rid] = fut
        try:
            handler(session_id, rid, options, params)
        except Exception:
            logger.exception("acp permission emitter failed")
        try:
            option_id = await asyncio.wait_for(fut, timeout=300)
            result: dict[str, Any] = {"outcome": {"outcome": "selected", "optionId": option_id}}
        except Exception:
            # Timeout / no answer: default to CANCEL (safer) — see
            # _permission_timeout_outcome. The agent never hangs, but it also never
            # auto-proceeds on a privileged action while the user is away.
            result = _permission_timeout_outcome(options)
        finally:
            self._perm_futures.pop(rid, None)
        try:
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        except Exception:
            logger.exception("acp permission reply failed")

    def resolve_permission(self, request_id: str, option_id: str) -> bool:
        fut = self._perm_futures.get(str(request_id))
        if fut and not fut.done():
            fut.set_result(option_id)
            return True
        return False

    def _respond_to_agent(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        result: dict[str, Any]
        if method == "session/request_permission":
            options = params.get("options", [])
            allow = next((o for o in options if o.get("kind") in ("allow_always", "allow_once")), None)
            if allow:
                result = {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}
            else:
                result = {"outcome": {"outcome": "cancelled"}}
        else:
            # Unsupported agent->client request (e.g. fs/* we didn't advertise).
            self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "unsupported"}})
            return
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        if self.proc and self.proc.stdin:
            await self.proc.stdin.drain()
        return await fut

    async def new_session(self, cwd: str) -> str:
        res = await self._request("session/new", {"cwd": cwd, "mcpServers": []})
        return res["sessionId"]

    async def load_session(self, session_id: str, cwd: str) -> None:
        await self._request("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []})

    async def prompt(self, session_id: str, text: str, on_update: UpdateHandler, on_permission=None, timeout: float = 600, images: list[tuple[bytes, str]] | None = None) -> str:
        self._handlers[session_id] = on_update
        if on_permission:
            self._permission_handlers[session_id] = on_permission
        try:
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            # Attach images as ACP image content blocks, but only when the agent said it
            # accepts them (capability from initialize) — otherwise a runner could choke.
            if images and getattr(self, "_image_capable", False):
                import base64 as _b64
                for raw, mime in images:
                    content.append({"type": "image", "mimeType": mime or "image/png", "data": _b64.b64encode(raw).decode()})
            res = await asyncio.wait_for(
                self._request("session/prompt", {"sessionId": session_id, "prompt": content}),
                timeout=timeout,
            )
            return res.get("stopReason", "end_turn")
        finally:
            self._handlers.pop(session_id, None)
            if self._permission_handlers.get(session_id) is on_permission:
                self._permission_handlers.pop(session_id, None)

    def cancel(self, session_id: str) -> None:
        try:
            self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
        except Exception:
            pass

    async def stop(self) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        for fut in list(self._perm_futures.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._perm_futures.clear()
        self._permission_handlers.clear()
        if self._reader:
            self._reader.cancel()
        if self._stderr_reader:
            self._stderr_reader.cancel()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("acp: process did not terminate, killing pid=%s", getattr(self.proc, "pid", None))
                self.proc.kill()
                with suppress(Exception):
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
        for task in (self._reader, self._stderr_reader):
            if task:
                with suppress(asyncio.CancelledError):
                    await task
        self._started = False


class AcpManager:
    """Owns one AcpProcess per (runner_id, home, cwd), started on demand.

    Keyed by cwd because the agent writes files relative to the agent process's
    working directory — so each project needs its own process rooted there.
    """

    def __init__(self) -> None:
        self._procs: dict[tuple[str, str, str], AcpProcess] = {}
        self._lock = asyncio.Lock()

    async def get(self, spec, home: str, cwd: str) -> AcpProcess:
        key = (spec.id, home, cwd)
        async with self._lock:
            proc = self._procs.get(key)
            if proc and proc._started:
                # Recycle if MCP/skill config changed since this process started,
                # so newly added tools load on the next run (no manual restart).
                if proc.config_sig == config_sig(home):
                    return proc
                logger.info("acp: tool config changed, recycling process for %s", home)
                await proc.stop()
                self._procs.pop(key, None)
            proc = AcpProcess(spec, home, cwd)
            await proc.start()
            self._procs[key] = proc
            return proc

    def resolve_permission(self, request_id: str, option_id: str) -> bool:
        """Deliver a user's interactive permission choice to whichever process is
        awaiting it. Single-user: usually one active process."""
        for proc in self._procs.values():
            if proc.resolve_permission(request_id, option_id):
                return True
        return False

    async def recycle(self, spec, home: str, cwd: str) -> None:
        """Kill and evict the cached process for (spec.id, home, cwd).

        Used when a run times out: `session/cancel` is fire-and-forget and a
        runner turn wedged inside a blocking tool call can't process it, so the
        cached process would carry the stuck turn into the next prompt (every
        later message returns "Queued for the next turn"). Terminating the
        process guarantees the next run spawns a fresh agent.
        """
        key = (spec.id, home, cwd)
        async with self._lock:
            proc = self._procs.pop(key, None)
        if proc:
            logger.info("acp: recycling process for %s (cwd=%s)", home, cwd)
            await proc.stop()

    async def shutdown(self) -> None:
        await asyncio.gather(*(proc.stop() for proc in list(self._procs.values())), return_exceptions=True)
        self._procs.clear()

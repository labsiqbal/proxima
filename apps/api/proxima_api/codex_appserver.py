"""Native Codex runner: drives the user's system `codex app-server` over stdio.

Why this exists (and why not the Zed ACP adapter): `@zed-industries/codex-acp`
statically compiles its own Codex core into the published binary. That bundled
core lags the fast-moving Codex releases, so the ChatGPT backend rejects newer
models against it with a misleading *"The '<model>' model requires a newer
version of Codex. Please upgrade …"* - even when the owner's own `codex` CLI is
current and runs the same model fine. The adapter exposes no hook to point at an
external Codex, and Codex ships no ACP mode of its own, so there is no way to
make the adapter track the system Codex.

Instead we drive Codex's own `codex app-server` (stdio JSON-RPC, the interface
editors use) directly. That always runs whatever `codex` is on PATH, so the
runner tracks the owner's up-to-date CLI and never falls behind a model release.

This class is a drop-in for `acp.AcpProcess`: it exposes the same surface the
worker/run layer already calls (`start`, `new_session`, `load_session`,
`prompt`, `cancel`, `resolve_permission`, `recent_stderr`, `stop`, plus the
`config_sig`/`_started` attributes), and `AcpManager` instantiates it for any
runner whose spec declares `protocol="codex-app-server"`. The app-server's
`thread`/`turn` events are translated into the small set of ACP-style
`sessionUpdate` shapes the worker consumes (`agent_message_chunk`,
`agent_thought_chunk`, `tool_call`, `tool_call_update`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections import deque
from contextlib import suppress
from typing import Any

from .acp import AcpError, UpdateHandler, config_sig, format_rpc_error
from .runners import subprocess_env

logger = logging.getLogger("proxima.codex")

READ_LIMIT = 16 * 1024 * 1024

# app-server item types that map onto an ACP-style tool call for the activity feed.
_TOOL_ITEM_TYPES = {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}

# Backend rejection emitted when the *driving* Codex is older than the model
# requires. With the Zed adapter this wrongly blamed the owner's CLI; here we
# drive the system CLI, so it is both honest and actionable (run `codex update`).
_VERSION_GATE_MARKERS = ("requires a newer version of Codex", "upgrade to the latest")


class CodexAppServerProcess:
    """One persistent `codex app-server` per (home, cwd), hosting many threads."""

    def __init__(self, spec, home: str, cwd: str):
        self.spec = spec
        self.home = home
        self.hermes_home = home  # alias kept for parity with AcpProcess
        self.cwd = cwd
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, UpdateHandler] = {}          # threadId -> update handler
        self._permission_handlers: dict[str, Any] = {}         # threadId -> on_permission
        self._perm_futures: dict[str, asyncio.Future] = {}     # request_id -> user choice
        self._perm_methods: dict[str, str] = {}                # request_id -> server method
        self._turn_done: dict[str, asyncio.Future] = {}        # threadId -> (status, error)
        self._active_turn: dict[str, str] = {}                 # threadId -> turnId
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._stderr_lines: deque[str] = deque(maxlen=60)
        self._started = False
        self._image_capable = False  # app-server input is text-only in this driver
        self.config_sig: tuple = ()
        self._codex_path = ""

    # ---- diagnostics -----------------------------------------------------
    def recent_stderr(self, lines: int = 15, max_chars: int = 1500) -> str:
        tail = [ln for ln in self._stderr_lines if ln.strip()][-lines:]
        return "\n".join(tail)[-max_chars:]

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        env = subprocess_env(
            provider_auth=True,
            allowlist_env="PROXIMA_RUNNER_ENV_ALLOWLIST",
            inherit_env="PROXIMA_RUNNER_INHERIT_ENV",
        )
        if self.home and self.spec.home_env:
            env[self.spec.home_env] = self.home
            os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.cwd, exist_ok=True)
        argv = list(self.spec.spawn_argv)
        resolved = shutil.which(argv[0], path=env["PATH"])
        if resolved:
            self._codex_path = resolved
            argv[0] = resolved
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, cwd=self.cwd, limit=READ_LIMIT,
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_reader = asyncio.create_task(self._read_stderr())
        try:
            # app-server handshake: initialize, then the required `initialized`
            # notification, before any thread/turn call.
            await asyncio.wait_for(
                self._request("initialize", {"clientInfo": {
                    "name": "proxima", "title": "Proxima", "version": "0.1.0"}}),
                timeout=60,
            )
            self._notify("initialized", {})
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
                continue
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
        # process exited: fail anything still waiting so callers don't hang.
        exc = AcpError("codex app-server process exited")
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        for fut in list(self._turn_done.values()):
            if not fut.done():
                fut.set_exception(exc)
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

    # ---- JSON-RPC plumbing ----------------------------------------------
    def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        self._send({"id": mid, "method": method, "params": params})
        if self.proc and self.proc.stdin:
            await self.proc.stdin.drain()
        return await fut

    def _dispatch(self, msg: dict[str, Any]) -> None:
        # response to one of our requests
        if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(AcpError(format_rpc_error(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return
        method = msg.get("method")
        if not method:
            return
        # server -> client request (needs a response by id): approvals, etc.
        if "id" in msg:
            self._handle_server_request(msg)
            return
        # notification (streaming thread/turn events)
        self._handle_notification(method, msg.get("params") or {})

    # ---- streaming events -> ACP-style updates --------------------------
    def _emit(self, thread_id: str | None, update: dict[str, Any]) -> None:
        handler = self._handlers.get(thread_id or "")
        if handler:
            try:
                handler(update)
            except Exception:
                logger.exception("codex update handler failed")

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        tid = params.get("threadId")
        if method == "item/agentMessage/delta":
            delta = params.get("delta") or ""
            if delta:
                self._emit(tid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": delta}})
        elif method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
            delta = params.get("delta") or ""
            if delta:
                self._emit(tid, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": delta}})
        elif method == "item/started":
            item = params.get("item") or {}
            if item.get("type") in _TOOL_ITEM_TYPES:
                self._emit(tid, {"sessionUpdate": "tool_call",
                                 "toolCallId": item.get("id"),
                                 "title": _tool_title(item), "kind": item.get("type")})
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") in _TOOL_ITEM_TYPES:
                status = "failed" if item.get("error") else "completed"
                self._emit(tid, {"sessionUpdate": "tool_call_update",
                                 "toolCallId": item.get("id"), "status": status})
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            fut = self._turn_done.get(tid or "")
            if fut and not fut.done():
                fut.set_result((turn.get("status") or "completed", turn.get("error")))

    # ---- approvals (server -> client) -----------------------------------
    def _handle_server_request(self, msg: dict[str, Any]) -> None:
        method = msg.get("method") or ""
        params = msg.get("params") or {}
        tid = params.get("threadId")
        handler = self._permission_handlers.get(tid or "")
        decisions = _approval_decisions(method)
        if handler and decisions:
            asyncio.create_task(self._handle_permission(msg, handler, decisions))
            return
        if decisions:
            # No interactive handler registered: approve once (matches the
            # non-interactive fallback the ACP path uses for permission prompts).
            self._reply(msg["id"], {"decision": decisions["allow_once"]})
            return
        # Anything else (user-input, elicitation, granular permission profiles):
        # decline politely so the turn continues rather than wedging.
        self._reply(msg["id"], None, error={"code": -32601, "message": "unsupported"})

    async def _handle_permission(self, msg: dict[str, Any], handler, decisions: dict[str, str]) -> None:
        rid = str(msg.get("id"))
        params = msg.get("params", {})
        tid = params.get("threadId")
        options = [
            {"optionId": decisions["allow_once"], "name": "Approve", "kind": "allow_once"},
            {"optionId": decisions["allow_always"], "name": "Approve for session", "kind": "allow_always"},
            {"optionId": decisions["reject"], "name": "Deny", "kind": "reject_once"},
        ]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._perm_futures[rid] = fut
        try:
            handler(tid, rid, options, {"toolCall": {"title": _approval_title(params)}, **params})
        except Exception:
            logger.exception("codex permission emitter failed")
        try:
            decision = await asyncio.wait_for(fut, timeout=300)
        except Exception:
            decision = decisions["reject"]  # timeout: safest is to deny, never auto-run
        finally:
            self._perm_futures.pop(rid, None)
        self._reply(msg["id"], {"decision": decision})

    def resolve_permission(self, request_id: str, option_id: str) -> bool:
        fut = self._perm_futures.get(str(request_id))
        if fut and not fut.done():
            fut.set_result(option_id)
            return True
        return False

    def _reply(self, mid: Any, result: dict[str, Any] | None, error: dict[str, Any] | None = None) -> None:
        try:
            payload = {"id": mid}
            if error is not None:
                payload["error"] = error
            else:
                payload["result"] = result
            self._send(payload)
        except Exception:
            logger.exception("codex server-request reply failed")

    # ---- session / turn API (AcpProcess-compatible) ---------------------
    async def new_session(self, cwd: str) -> str:
        res = await self._request("thread/start", {"cwd": cwd})
        return (res.get("thread") or {}).get("id") or res.get("threadId")

    async def load_session(self, session_id: str, cwd: str) -> None:
        # Raise on failure so the caller treats it as stale and starts fresh,
        # exactly like the ACP path's load_session contract.
        await self._request("thread/resume", {"threadId": session_id, "cwd": cwd})

    async def prompt(self, session_id: str, text: str, on_update: UpdateHandler,
                     on_permission=None, timeout: float = 600,
                     images: list[tuple[bytes, str]] | None = None) -> str:
        self._handlers[session_id] = on_update
        if on_permission:
            self._permission_handlers[session_id] = on_permission
        done: asyncio.Future = asyncio.get_event_loop().create_future()
        self._turn_done[session_id] = done
        try:
            res = await self._request("turn/start", {
                "threadId": session_id,
                "input": [{"type": "text", "text": text}],
            })
            turn_id = (res.get("turn") or {}).get("id")
            if turn_id:
                self._active_turn[session_id] = turn_id
            status, error = await asyncio.wait_for(done, timeout=timeout)
            if status == "failed":
                raise AcpError(self._explain_turn_error(error))
            if status in ("aborted", "cancelled"):
                return "cancelled"
            return "end_turn"
        finally:
            self._handlers.pop(session_id, None)
            self._turn_done.pop(session_id, None)
            self._active_turn.pop(session_id, None)
            if self._permission_handlers.get(session_id) is on_permission:
                self._permission_handlers.pop(session_id, None)

    def _explain_turn_error(self, error: Any) -> str:
        """Turn `turn.error` into a surfaced message. De-mislead the model
        version gate: with the Zed adapter it blamed the owner's CLI; here we
        drive the system CLI, so point precisely at that binary + `codex update`.
        """
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message") or error)
            # Backend errors arrive as a JSON string inside `message`.
            with suppress(Exception):
                inner = json.loads(message)
                message = str(((inner or {}).get("error") or {}).get("message") or message)
        else:
            message = str(error)
        if any(m in message for m in _VERSION_GATE_MARKERS):
            self._stderr_lines.append(message)
            where = self._codex_path or "codex"
            return (f"{message}\n\nProxima runs your system Codex CLI directly "
                    f"({where}). This means that Codex is behind the model's "
                    f"required version - update it (`codex update`) and retry.")
        return message

    def cancel(self, session_id: str) -> None:
        turn_id = self._active_turn.get(session_id)
        if not turn_id:
            return
        # turn/interrupt is a request method; send a proper request frame (with an
        # id, so the server acts on it) but fire-and-forget — this hook is sync and
        # the turn's own future resolves via the turn/completed(aborted) event.
        try:
            self._next_id += 1
            self._send({"id": self._next_id, "method": "turn/interrupt",
                        "params": {"threadId": session_id, "turnId": turn_id}})
        except Exception:
            pass

    async def stop(self) -> None:
        for bucket in (self._pending, self._perm_futures, self._turn_done):
            for fut in list(bucket.values()):
                if not fut.done():
                    fut.cancel()
            bucket.clear()
        self._permission_handlers.clear()
        self._handlers.clear()
        self._active_turn.clear()
        for task in (self._reader, self._stderr_reader):
            if task:
                task.cancel()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("codex: process did not terminate, killing pid=%s", getattr(self.proc, "pid", None))
                self.proc.kill()
                with suppress(Exception):
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
        for task in (self._reader, self._stderr_reader):
            if task:
                with suppress(asyncio.CancelledError):
                    await task
        self._started = False


def _tool_title(item: dict[str, Any]) -> str:
    t = item.get("type")
    if t == "commandExecution":
        cmd = item.get("command") or item.get("parsedCmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return str(cmd)[:120] or "command"
    if t == "fileChange":
        return "edit files"
    if t == "mcpToolCall":
        return str(item.get("toolName") or item.get("tool") or "tool")
    if t == "webSearch":
        return "web search"
    return str(t or "tool")


def _approval_title(params: dict[str, Any]) -> str:
    item = params.get("item") or {}
    if item:
        return _tool_title(item)
    cmd = params.get("command") or params.get("parsedCmd")
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    if cmd:
        return str(cmd)[:200]
    return str(params.get("reason") or params.get("callId") or "Permission required")


def _approval_decisions(method: str) -> dict[str, str] | None:
    """Decision vocabulary for each approval server-request, keyed by the
    ACP-style option kind Proxima presents. Returns None if `method` is not an
    approval we answer with a `{decision: ...}` reply."""
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"allow_once": "approved", "allow_always": "approved_for_session", "reject": "denied"}
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        return {"allow_once": "accept", "allow_always": "acceptForSession", "reject": "decline"}
    return None

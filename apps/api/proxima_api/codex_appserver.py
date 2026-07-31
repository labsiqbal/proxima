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
import re
import shutil
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from .acp import AcpError, UpdateHandler, config_sig, format_rpc_error
from .codex_master_proxy import CodexMasterModelProxy
from .master_tool_broker import TOOL_SCHEMAS, master_dynamic_tools
from .container_activity import (
    GuardedWriterTree,
    process_start_identity,
    retain_activity_lease,
)
from .process_containment import pid_namespace_argv, terminate_and_verify
from .runners import subprocess_env

logger = logging.getLogger("proxima.codex")

READ_LIMIT = 16 * 1024 * 1024

MASTER_APP_SERVER_CONFIG = (
    "approval_policy=\"never\"",
    "sandbox_mode=\"read-only\"",
    "web_search=\"disabled\"",
    "features.shell_tool=false",
    "features.multi_agent=false",
    "features.multi_agent_v2=false",
    "features.apps=false",
    "features.plugins=false",
    "features.hooks=false",
    "features.goals=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.browser_use_full_cdp_access=false",
    "features.in_app_browser=false",
    "features.image_generation=false",
    "features.code_mode=false",
    "features.code_mode_host=false",
    "features.enable_mcp_apps=false",
    "features.request_permissions_tool=false",
    "features.skill_search=false",
    "features.skill_mcp_dependency_install=false",
    "features.enable_request_compression=false",
    "features.remote_plugin=false",
    "features.shell_snapshot=false",
    "features.deferred_executor=false",
    "features.token_budget=false",
    "features.current_time_reminder=false",
    "skills.bundled.enabled=false",
    "tools.experimental_request_user_input.enabled=false",
    "orchestrator.skills.enabled=false",
    "orchestrator.mcp.enabled=false",
    "apps._default.enabled=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    "include_environment_context=false",
    "project_doc_max_bytes=0",
    "check_for_update_on_startup=false",
)

MASTER_CODEX_BASE_INSTRUCTIONS = """You are Master, Proxima's chat-only orchestrator.
You may chat and call only the Proxima product functions provided in this turn.
You have no shell, filesystem, browser, skill, MCP, plugin, permission, or
runner-native tool authority. Delegate all work through Proxima product tools.
"""

# app-server item types that map onto an ACP-style tool call for the activity feed.
_TOOL_ITEM_TYPES = {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}
_MASTER_NON_NATIVE_ITEM_TYPES = {
    "agentMessage",
    "dynamicToolCall",
    "reasoning",
    "userMessage",
}
_MASTER_HOST_PATH = re.compile(
    r"""(?:^|[\s"'(])(?:/[^\s"'<>]+|[A-Za-z]:\\[^\s"'<>]+|"""
    r"""(?:\.\.?[/\\]|~[/\\]|file://)[^\s"'<>]*)"""
)
_MASTER_SECRET_TEXT = re.compile(
    r"""(?i)(?:\bbearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----|"""
    r"""\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,})"""
)

# Backend rejection emitted when the *driving* Codex is older than the model
# requires. With the Zed adapter this wrongly blamed the owner's CLI; here we
# drive the system CLI, so it is both honest and actionable (run `codex update`).
_VERSION_GATE_MARKERS = ("requires a newer version of Codex", "upgrade to the latest")


class CodexAppServerProcess:
    """One persistent `codex app-server` per (home, cwd), hosting many threads."""

    def __init__(
        self,
        spec,
        home: str,
        cwd: str,
        *,
        master_chat_only: bool = False,
        contained: bool = False,
        activity_lease: Any = None,
    ):
        self.spec = spec
        self.home = home
        self.hermes_home = home  # alias kept for parity with AcpProcess
        self.cwd = cwd
        self.master_chat_only = master_chat_only
        self.contained = contained
        self.activity_lease = activity_lease
        self.proc: asyncio.subprocess.Process | None = None
        self.writer_tree: GuardedWriterTree | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, UpdateHandler] = {}          # threadId -> update handler
        self._permission_handlers: dict[str, Any] = {}         # threadId -> on_permission
        self._dynamic_tool_handlers: dict[
            str, Callable[[str, Any], dict[str, Any]]
        ] = {}
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
        self._master_proxy: CodexMasterModelProxy | None = None
        self._master_protected_values: tuple[str, ...] = ()
        self._master_contract_threads: set[str] = set()

    # ---- diagnostics -----------------------------------------------------
    def recent_stderr(self, lines: int = 15, max_chars: int = 1500) -> str:
        tail = [ln for ln in self._stderr_lines if ln.strip()][-lines:]
        return self._redact_master_text("\n".join(tail)[-max_chars:])

    def _redact_master_text(self, text: str) -> str:
        if not self.master_chat_only:
            return text
        redacted = text
        for value in sorted(
            self._master_protected_values,
            key=len,
            reverse=True,
        ):
            redacted = redacted.replace(value, "[protected]")
        redacted = _MASTER_HOST_PATH.sub(
            lambda match: (
                match.group(0)[:1] + "[protected-path]"
                if match.group(0)[:1].isspace()
                else "[protected-path]"
            ),
            redacted,
        )
        return _MASTER_SECRET_TEXT.sub("[protected]", redacted)

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        env = (
            subprocess_env(provider_auth=True)
            if self.master_chat_only
            else subprocess_env(
                provider_auth=True,
                allowlist_env="PROXIMA_RUNNER_ENV_ALLOWLIST",
                inherit_env="PROXIMA_RUNNER_INHERIT_ENV",
            )
        )
        if self.master_chat_only:
            original_paths = tuple(
                env.get(name, "")
                for name in (
                    "HOME",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "XDG_CACHE_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                    "XDG_RUNTIME_DIR",
                )
            )
            provider_names = (
                "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "OPENAI_API_KEY",
                "OPENROUTER_API_KEY",
                "XAI_API_KEY",
            )
            provider_values = tuple(env.get(name, "") for name in provider_names)
            restricted_home = Path(self.home).resolve()
            restricted_tmp = restricted_home / "tmp"
            restricted_tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name, relative in (
                ("XDG_CACHE_HOME", "cache"),
                ("XDG_CONFIG_HOME", "config"),
                ("XDG_DATA_HOME", "data"),
                ("XDG_RUNTIME_DIR", "runtime"),
            ):
                target = restricted_home / relative
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                env[name] = str(target)
            env["HOME"] = str(restricted_home)
            for name in ("TEMP", "TMP", "TMPDIR"):
                env[name] = str(restricted_tmp)
            for name in provider_names:
                if name != "OPENAI_API_KEY":
                    env.pop(name, None)
            for name in ("LOGNAME", "USER", "USERPROFILE"):
                env.pop(name, None)
            protected_paths = {
                self.home,
                self.cwd,
                str(Path(self.home).resolve().parent),
                str(Path(self.cwd).resolve().parent),
                str(Path(self.cwd).resolve().parent.parent),
                *original_paths,
            }
            self._master_protected_values = tuple(
                value
                for value in (*protected_paths, *provider_values)
                if value and value != "/"
            )
        if self.home and self.spec.home_env:
            env[self.spec.home_env] = self.home
            os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.cwd, exist_ok=True)
        argv = list(self.spec.spawn_argv)
        if self.master_chat_only:
            self._master_proxy = CodexMasterModelProxy(
                protected_values=self._master_protected_values
            )
            proxy_url = await self._master_proxy.start()
            argv.append("--strict-config")
            for setting in MASTER_APP_SERVER_CONFIG:
                argv.extend(("-c", setting))
            # Keep Codex's built-in provider so app-server preserves its
            # authenticated Responses Lite and dynamic-tool capabilities. The
            # base URL points only at our private request firewall.
            argv.extend(
                ("-c", f"openai_base_url={json.dumps(proxy_url)}")
            )
        resolved = shutil.which(argv[0], path=env["PATH"])
        if resolved:
            self._codex_path = resolved
            argv[0] = resolved
        if self.contained:
            argv = pid_namespace_argv(
                argv,
                cwd=self.cwd,
                label="runner",
            )
        guard_options: dict[str, Any] = {}
        if self.activity_lease is not None:
            argv, guard_options = self.activity_lease.guard_process(argv)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.cwd,
                limit=READ_LIMIT,
                **guard_options,
            )
            if self.activity_lease is not None:
                self.activity_lease.mark_process_started()
                proc_pid = int(self.proc.pid) if self.proc.pid is not None else None
                self.writer_tree = GuardedWriterTree.bind(
                    self.activity_lease,
                    launcher_pid=proc_pid,
                    launcher_start=(
                        process_start_identity(proc_pid)
                        if proc_pid is not None
                        else None
                    ),
                )
                try:
                    self.writer_tree.seed_live_members()
                except Exception:
                    pass
            self._reader = asyncio.create_task(self._read_loop())
            self._stderr_reader = asyncio.create_task(self._read_stderr())
            # app-server handshake: initialize, then the required `initialized`
            # notification, before any thread/turn call.
            await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "proxima",
                            "title": "Proxima",
                            "version": "0.1.0",
                        },
                        "capabilities": {
                            "experimentalApi": self.master_chat_only,
                        },
                    },
                ),
                timeout=60,
            )
            self._notify("initialized", {})
        except BaseException as start_exc:
            stop_exc: BaseException | None = None
            try:
                await self.stop()
            except BaseException as exc:
                stop_exc = exc
            if stop_exc is not None:
                raise start_exc from stop_exc
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
            item_type = item.get("type")
            if item_type in _TOOL_ITEM_TYPES or (
                self.master_chat_only
                and item_type not in _MASTER_NON_NATIVE_ITEM_TYPES
            ):
                self._emit(tid, {"sessionUpdate": "tool_call",
                                 "toolCallId": item.get("id"),
                                 "title": _tool_title(item), "kind": item_type})
        elif method == "item/completed":
            item = params.get("item") or {}
            item_type = item.get("type")
            if item_type in _TOOL_ITEM_TYPES or (
                self.master_chat_only
                and item_type not in _MASTER_NON_NATIVE_ITEM_TYPES
            ):
                status = "failed" if item.get("error") else "completed"
                if (
                    self.master_chat_only
                    and item_type not in _TOOL_ITEM_TYPES
                    and item_type not in _MASTER_NON_NATIVE_ITEM_TYPES
                ):
                    self._emit(tid, {"sessionUpdate": "tool_call",
                                     "toolCallId": item.get("id"),
                                     "title": _tool_title(item), "kind": item_type})
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
        if method == "item/tool/call":
            handler = self._dynamic_tool_handlers.get(tid or "")
            asyncio.create_task(
                self._handle_dynamic_tool(msg, handler)
            )
            return
        handler = self._permission_handlers.get(tid or "")
        decisions = _approval_decisions(method)
        if handler and decisions:
            asyncio.create_task(self._handle_permission(msg, handler, decisions))
            return
        if decisions:
            if self.master_chat_only:
                self._reply(msg["id"], {"decision": decisions["reject"]})
                return
            # No interactive handler registered: approve once (matches the
            # non-interactive fallback the ACP path uses for permission prompts).
            self._reply(msg["id"], {"decision": decisions["allow_once"]})
            return
        # Anything else (user-input, elicitation, granular permission profiles):
        # decline politely so the turn continues rather than wedging.
        self._reply(msg["id"], None, error={"code": -32601, "message": "unsupported"})

    async def _handle_dynamic_tool(
        self,
        msg: dict[str, Any],
        handler: Callable[[str, Any], dict[str, Any]] | None,
    ) -> None:
        params = msg.get("params") or {}
        if handler is None:
            self._reply(
                msg["id"],
                {
                    "success": False,
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": (
                                '{"ok":false,"tool":null,"error":'
                                '{"code":"tool_not_allowed",'
                                '"message":"Master tool is not registered"}}'
                            ),
                        }
                    ],
                },
            )
            return
        try:
            result = handler(
                str(params.get("tool") or ""),
                params.get("arguments"),
            )
        except Exception:
            logger.exception("Codex dynamic Master tool failed")
            result = {
                "ok": False,
                "tool": str(params.get("tool") or "") or None,
                "error": {
                    "code": "tool_failed",
                    "message": "Master tool failed inside Proxima",
                },
            }
        self._reply(
            msg["id"],
            {
                "success": True,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": json.dumps(
                            result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                ],
            },
        )

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

    def deny_permission(
        self, request_id: str, options: list[dict[str, Any]]
    ) -> bool:
        reject = next(
            (
                option
                for option in options
                if str(option.get("kind") or "").startswith(
                    ("reject", "deny", "cancel")
                )
            ),
            None,
        )
        return bool(
            reject
            and self.resolve_permission(
                request_id, str(reject["optionId"])
            )
        )

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

    async def new_master_session(
        self,
        cwd: str,
        dynamic_tools: list[dict[str, Any]],
    ) -> str:
        if not self.master_chat_only:
            raise AcpError("Codex process is not configured for chat-only Master")
        if self._master_proxy is None:
            raise AcpError("Codex Master request firewall is not running")
        names = {
            str(tool.get("name") or "")
            for tool in dynamic_tools
            if isinstance(tool, dict)
        }
        if len(names) != len(dynamic_tools):
            raise AcpError("Codex Master product tool list is invalid")
        if dynamic_tools != master_dynamic_tools():
            raise AcpError(
                "Codex Master product tool schemas do not match the broker"
            )
        self._master_proxy.set_product_tools(
            dynamic_tools,
            required_names=set(TOOL_SCHEMAS),
        )
        res = await self._request(
            "thread/start",
            {
                "cwd": cwd,
                "baseInstructions": MASTER_CODEX_BASE_INSTRUCTIONS,
                "developerInstructions": (
                    "Call only the supplied Proxima product functions."
                ),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "environments": [],
                "runtimeWorkspaceRoots": [],
                "selectedCapabilityRoots": [],
                "dynamicTools": dynamic_tools,
                "ephemeral": True,
            },
        )
        thread_id = (res.get("thread") or {}).get("id") or res.get("threadId")
        if not thread_id:
            raise AcpError("Codex Master thread attestation did not return an id")
        self._master_contract_threads.add(str(thread_id))
        return str(thread_id)

    async def load_session(self, session_id: str, cwd: str) -> None:
        # Raise on failure so the caller treats it as stale and starts fresh,
        # exactly like the ACP path's load_session contract.
        await self._request("thread/resume", {"threadId": session_id, "cwd": cwd})

    async def prompt(self, session_id: str, text: str, on_update: UpdateHandler,
                     on_permission=None, timeout: float = 600,
                     images: list[tuple[bytes, str]] | None = None,
                     on_dynamic_tool: Callable[
                         [str, Any], dict[str, Any]
                     ] | None = None) -> str:
        if self.master_chat_only and session_id not in self._master_contract_threads:
            raise AcpError(
                "Codex Master runtime contract was not attested before the turn"
            )
        self._handlers[session_id] = on_update
        if on_permission:
            self._permission_handlers[session_id] = on_permission
        if on_dynamic_tool:
            self._dynamic_tool_handlers[session_id] = on_dynamic_tool
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
            if self._dynamic_tool_handlers.get(session_id) is on_dynamic_tool:
                self._dynamic_tool_handlers.pop(session_id, None)

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
            where = (
                "the verified Codex binary"
                if self.master_chat_only
                else self._codex_path or "codex"
            )
            return self._redact_master_text(
                f"{message}\n\nProxima runs your system Codex CLI directly "
                f"({where}). This means that Codex is behind the model's "
                "required version - update it (`codex update`) and retry."
            )
        return self._redact_master_text(message)

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

    def _retain_activity_for_unproven_tree(self) -> None:
        if self.activity_lease is None:
            return
        tree = self.writer_tree
        if tree is not None:
            try:
                tree.seed_live_members()
            except Exception:
                pass
            if tree.exited() is True:
                return
        retain_activity_lease(
            self.activity_lease,
            tree=tree,
            pid=(
                tree.launcher_pid
                if tree is not None
                else (getattr(self.proc, "pid", None) if self.proc else None)
            ),
            start_identity=(
                tree.launcher_start if tree is not None else None
            ),
        )

    async def stop(self) -> None:
        for bucket in (self._pending, self._perm_futures, self._turn_done):
            for fut in list(bucket.values()):
                if not fut.done():
                    fut.cancel()
            bucket.clear()
        self._permission_handlers.clear()
        self._handlers.clear()
        self._active_turn.clear()
        self._master_contract_threads.clear()
        for task in (self._reader, self._stderr_reader):
            if task:
                task.cancel()
        failure: BaseException | None = None
        try:
            await terminate_and_verify(
                self.proc,
                label="Codex runner",
                tree=self.writer_tree,
            )
        except BaseException as exc:
            failure = exc
        for task in (self._reader, self._stderr_reader):
            if task:
                with suppress(asyncio.CancelledError):
                    await task
        if self._master_proxy is not None:
            try:
                await self._master_proxy.stop()
            except BaseException as exc:
                if failure is None:
                    failure = exc
            else:
                self._master_proxy = None
        self._started = bool(
            self.proc is not None and self.proc.returncode is None
        )
        tree_clear = True
        if self.writer_tree is not None:
            try:
                self.writer_tree.seed_live_members()
                tree_clear = self.writer_tree.exited() is True
            except Exception:
                tree_clear = False
        if failure is not None or not tree_clear:
            self._retain_activity_for_unproven_tree()
        if failure is not None:
            raise failure
        if not tree_clear:
            raise RuntimeError("Codex runner process tree exit was not verified")


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

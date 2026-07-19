"""Run a project's app (dev server) as a managed background process and proxy it.

Lets you preview something the agent built — e.g. `npm run dev` — live inside
Proxima. One managed process per project; the HTTP proxy forwards to its port so
relative assets resolve and no port is exposed directly.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import socket
import subprocess
import time
from typing import Any


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False

from .runners import subprocess_env

IS_WINDOWS = os.name == "nt"

# Dev servers often ignore $PORT and bind to their own (Vite→5173, etc.), printing
# the real address. Detect it from stdout so we can proxy to where it actually is.
_PORT_RE = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})", re.I)
_PORT_RE2 = re.compile(r"(?:listening|running|server).{0,20}?\bport\b[^\d]{0,4}(\d{2,5})", re.I)


class AppManager:
    def __init__(self) -> None:
        self._apps: dict[str, dict[str, Any]] = {}

    async def start(self, slug: str, cwd: str, command: str, port: int) -> None:
        await self.stop(slug)
        env = subprocess_env(
            allowlist_env="PROXIMA_APP_ENV_ALLOWLIST",
            inherit_env="PROXIMA_APP_INHERIT_ENV",
        )
        env["PORT"] = str(port)
        # Run the command string through the platform shell, in its own process
        # group so we can clean-kill the whole tree later.
        if IS_WINDOWS:
            shell_argv = ["cmd", "/c", command]
            extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            shell_argv = ["bash", "-lc", command]
            extra = {"start_new_session": True}
        proc = await asyncio.create_subprocess_exec(
            *shell_argv, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            **extra,
        )
        self._apps[slug] = {"proc": proc, "port": port, "command": command, "started_at": time.time(), "log": []}
        asyncio.create_task(self._drain(slug, proc))

    async def _drain(self, slug: str, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            app = self._apps.get(slug)
            if app and app.get("proc") is proc:
                text = line.decode("utf-8", "replace").rstrip()
                app["log"].append(text)
                del app["log"][:-200]
                # Sniff the real listening port from the server's own output.
                if not app.get("detected_port"):
                    m = _PORT_RE.search(text) or _PORT_RE2.search(text)
                    if m:
                        found = int(m.group(1))
                        if 1024 <= found <= 65535:
                            app["detected_port"] = found

    async def stop(self, slug: str) -> None:
        app = self._apps.pop(slug, None)
        if not app:
            return
        proc = app["proc"]
        if proc.returncode is None:
            try:
                if IS_WINDOWS:
                    # taskkill /T ends the child tree; fall back to terminate().
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, check=False)
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            # Wait for the process tree to actually die so the port is freed before
            # the next app starts on it — otherwise the new server fails to bind and
            # the preview keeps showing the old one.
            try:
                await asyncio.wait_for(proc.wait(), timeout=4)
            except (asyncio.TimeoutError, Exception):
                try:
                    if not IS_WINDOWS:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except Exception:
                    pass

    def status(self, slug: str) -> dict[str, Any]:
        app = self._apps.get(slug)
        if not app:
            return {"running": False}
        if app["proc"].returncode is not None:  # exited on its own
            self._apps.pop(slug, None)
            return {"running": False, "command": app["command"], "log": app["log"][-40:], "exited": True}
        # "ready" = the effective port actually accepts connections. Do not mark a
        # long-running but non-listening process as ready; that opens a blank preview
        # and hides the real startup failure from the user.
        eff_port = app.get("detected_port") or app["port"]
        ready = _port_open(eff_port)
        return {"running": True, "ready": ready, "port": eff_port, "command": app["command"], "log": app["log"][-40:]}

    def port(self, slug: str) -> int | None:
        app = self._apps.get(slug)
        if not app or app["proc"].returncode is not None:
            return None
        # Prefer the port the server actually printed; fall back to the requested one.
        return app.get("detected_port") or app["port"]

    async def shutdown(self) -> None:
        for slug in list(self._apps):
            await self.stop(slug)

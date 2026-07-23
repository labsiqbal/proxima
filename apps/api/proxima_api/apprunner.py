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


def _hex_addr_is_loopback(hex_addr: str) -> bool:
    """/proc/net/tcp{,6} local address (hex, per-word little-endian) → loopback?"""
    if len(hex_addr) == 8:  # IPv4: 127.0.0.0/8 → first octet is the last byte
        return hex_addr.endswith("7F")
    if len(hex_addr) == 32:  # IPv6: ::1, or IPv4-mapped ::ffff:127.x.x.x
        return (hex_addr == "00000000000000000000000001000000"
                or (hex_addr.startswith("0000000000000000FFFF0000") and hex_addr.endswith("7F")))
    return False


def port_bound_non_loopback(port: int) -> bool | None:
    """True if any socket LISTENs on `port` at a non-loopback address (including
    the 0.0.0.0/:: wildcards), False if every listener is loopback-only, None
    when it cannot be determined (no /proc/net on this platform)."""
    hex_port = f"{int(port):04X}"
    checked = False
    broad = False
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, encoding="ascii") as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        checked = True
        for row in rows:
            cols = row.split()
            # cols[1] = local "ADDR:PORT" in hex, cols[3] = state (0A = LISTEN)
            if len(cols) > 3 and cols[3] == "0A" and cols[1].endswith(":" + hex_port):
                if not _hex_addr_is_loopback(cols[1].split(":")[0].upper()):
                    broad = True
    return broad if checked else None

from .runners import subprocess_env

IS_WINDOWS = os.name == "nt"

# Dev servers often ignore $PORT and bind to their own (Vite→5173, etc.), printing
# the real address. Detect it from stdout so we can proxy to where it actually is.
_PORT_RE = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})", re.I)
_PORT_RE2 = re.compile(r"(?:listening|running|server).{0,20}?\bport\b[^\d]{0,4}(\d{2,5})", re.I)


class AppManager:
    def __init__(self) -> None:
        self._apps: dict[str, dict[str, Any]] = {}
        # Last self-exit payload per slug, kept until the next start so the UI
        # can show failure logs after the process is reaped (status polls every 2s).
        self._last_exit: dict[str, dict[str, Any]] = {}

    async def start(self, slug: str, cwd: str, command: str, port: int) -> None:
        await self.stop(slug)
        self._last_exit.pop(slug, None)
        env = subprocess_env(
            allowlist_env="PROXIMA_APP_ENV_ALLOWLIST",
            inherit_env="PROXIMA_APP_INHERIT_ENV",
        )
        env["PORT"] = str(port)
        # Default the dev server onto loopback: frameworks that honor $HOST
        # (webpack-dev-server/CRA and friends) then bind 127.0.0.1, keeping the
        # unauthenticated dev port off the LAN/tailnet - the gated preview relay
        # reaches it via 127.0.0.1 regardless. An allowlisted HOST or an explicit
        # --host flag in the command still wins.
        env.setdefault("HOST", "127.0.0.1")
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
            return self._last_exit.get(slug) or {"running": False}
        if app["proc"].returncode is not None:  # exited on its own
            self._apps.pop(slug, None)
            # exit_code + exited stay sticky across 2s polls so the UI can say
            # "Finished" vs "Failed" instead of a bare log dump after a short run.
            result = {
                "running": False,
                "command": app["command"],
                "log": app["log"][-40:],
                "exited": True,
                "exit_code": int(app["proc"].returncode),
            }
            self._last_exit[slug] = result
            return result
        # "ready" = the effective port actually accepts connections. Do not mark a
        # long-running but non-listening process as ready; that opens a blank preview
        # and hides the real startup failure from the user.
        eff_port = app.get("detected_port") or app["port"]
        ready = _port_open(eff_port)
        out = {"running": True, "ready": ready, "port": eff_port, "command": app["command"], "log": app["log"][-40:]}
        # A dev server listening beyond loopback is directly reachable by other
        # LAN/tailnet devices with no auth - the gated relay does not protect a
        # broadly-bound origin. Surface it so the UI can warn the owner.
        if ready and port_bound_non_loopback(eff_port):
            out["broad_bind"] = True
        return out

    def port(self, slug: str) -> int | None:
        app = self._apps.get(slug)
        if not app or app["proc"].returncode is not None:
            return None
        # Prefer the port the server actually printed; fall back to the requested one.
        return app.get("detected_port") or app["port"]

    async def shutdown(self) -> None:
        for slug in list(self._apps):
            await self.stop(slug)

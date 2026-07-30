"""Run a project's app (dev server) as a managed background process and proxy it.

Lets you preview something the agent built — e.g. `npm run dev` — live inside
Proxima. One managed process per project; the HTTP proxy forwards to its port so
relative assets resolve and no port is exposed directly.
"""
from __future__ import annotations

import asyncio
from enum import Enum
import os
import re
import signal
import socket
import subprocess
import time
from typing import Any, Protocol

from .process_containment import pid_namespace_argv
from .runners import subprocess_env

PROLONGED_START_SECONDS = 15


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


class PortInUseError(RuntimeError):
    """The requested preview port belongs to a process Proxima does not manage."""

    def __init__(self, port: int) -> None:
        self.port = int(port)
        super().__init__(f"Port {self.port} is already in use by another process. Choose a different port; Proxima did not stop it.")


def _listening_socket_inodes(port: int) -> set[str] | None:
    """Return Linux LISTEN socket inodes for ``port`` or None when unavailable."""
    hex_port = f"{int(port):04X}"
    checked = False
    inodes: set[str] = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, encoding="ascii") as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        checked = True
        for row in rows:
            cols = row.split()
            # inode is field 10 in proc_net_tcp; do not infer ownership from
            # connectivity alone because another process can answer the probe.
            if len(cols) > 9 and cols[3] == "0A" and cols[1].endswith(":" + hex_port):
                inodes.add(cols[9])
    return inodes if checked else None


class PortOwnership(str, Enum):
    NO_LISTENER = "no_listener"
    VERIFIED = "verified"
    FOREIGN = "foreign"
    DETACHED = "detached"
    UNKNOWN = "unknown"


def _process_table(
    listener_inodes: set[str],
) -> dict[int, tuple[int, int, set[str]]] | None:
    """Return pid -> (ppid, pgrp, matching socket inodes) from procfs."""
    try:
        raw_pids = [name for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return None
    processes: dict[int, tuple[int, int, set[str]]] = {}
    for raw_pid in raw_pids:
        try:
            pid = int(raw_pid)
            with open(f"/proc/{raw_pid}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            # comm may contain spaces/parens; fields after the final ') ' start
            # with state, ppid, pgrp.
            fields = stat.rsplit(") ", 1)[1].split()
            if len(fields) < 3:
                continue
            ppid = int(fields[1])
            pgrp = int(fields[2])
            sockets: set[str] = set()
            for fd in os.listdir(f"/proc/{raw_pid}/fd"):
                try:
                    target = os.readlink(f"/proc/{raw_pid}/fd/{fd}")
                except OSError:
                    continue
                if target.startswith("socket:["):
                    inode = target[8:-1]
                    if inode in listener_inodes:
                        sockets.add(inode)
            processes[pid] = (ppid, pgrp, sockets)
        except (OSError, ValueError, IndexError):
            continue
    return processes


def _is_descendant(
    pid: int,
    leader_pid: int,
    processes: dict[int, tuple[int, int, set[str]]],
) -> bool:
    seen: set[int] = set()
    current = pid
    while current > 1 and current not in seen:
        if current == leader_pid:
            return True
        seen.add(current)
        row = processes.get(current)
        if row is None:
            return False
        current = row[0]
    return current == leader_pid


def _listener_ownership(
    leader_pid: int,
    port: int,
    *,
    contained: bool,
) -> PortOwnership:
    """Classify a listener without equating reachability with ownership.

    A normal app server stays in the fresh process group created for the command.
    A detached descendant is accepted only inside Proxima's PID namespace, where
    namespace teardown owns its full lifetime. On non-procfs hosts, inaccessible
    procfs, or incomplete socket-to-process evidence, preview fails closed.
    """
    inodes = _listening_socket_inodes(port)
    if inodes is None:
        return PortOwnership.UNKNOWN
    if not inodes:
        return PortOwnership.NO_LISTENER
    try:
        group_id = os.getpgid(leader_pid)
    except OSError:
        return PortOwnership.UNKNOWN
    processes = _process_table(inodes)
    if processes is None:
        return PortOwnership.UNKNOWN
    owners: dict[str, set[int]] = {inode: set() for inode in inodes}
    for pid, (_ppid, _pgrp, sockets) in processes.items():
        for inode in sockets:
            owners[inode].add(pid)
    # hidepid, permissions, or a disappearing owner leave an incomplete proof.
    if any(not pids for pids in owners.values()):
        return PortOwnership.UNKNOWN
    owner_pids = {pid for pids in owners.values() for pid in pids}
    if all(processes[pid][1] == group_id for pid in owner_pids):
        return PortOwnership.VERIFIED
    if all(_is_descendant(pid, leader_pid, processes) for pid in owner_pids):
        return PortOwnership.VERIFIED if contained else PortOwnership.DETACHED
    return PortOwnership.FOREIGN


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

IS_WINDOWS = os.name == "nt"

# Dev servers often ignore $PORT and bind to their own (Vite→5173, etc.), printing
# the real address. Detect it from stdout so we can proxy to where it actually is.
_PORT_RE = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})", re.I)
_PORT_RE2 = re.compile(r"(?:listening|running|server).{0,20}?\bport\b[^\d]{0,4}(\d{2,5})", re.I)


class EffectLease(Protocol):
    def release(self) -> None: ...


class AppManager:
    def __init__(self, *, contained: bool = False) -> None:
        self.contained = contained
        self._apps: dict[str, dict[str, Any]] = {}
        # Last terminal/stopped payload per slug, kept until the next start so
        # status polling can recover the bounded command buffer after page reload
        # and after an explicit Stop.
        self._last_exit: dict[str, dict[str, Any]] = {}
        self._retained_effects: list[EffectLease] = []

    def _finish_effect(
        self,
        app: dict[str, Any],
        *,
        terminated: bool,
    ) -> None:
        lease = app.pop("effect_lease", None)
        if lease is None:
            return
        if terminated:
            lease.release()
        else:
            self._retained_effects.append(lease)

    async def start(
        self,
        slug: str,
        cwd: str,
        command: str,
        port: int,
        *,
        effect_lease: EffectLease | None = None,
    ) -> None:
        await self.stop(slug, preserve_status=False)
        # Fail before spawning when a user-owned preview has this port.  In
        # particular, never "fix" a collision by killing a process we do not own.
        if _port_open(port):
            self._last_exit[slug] = self._port_conflict_status(
                command=command,
                requested_port=port,
                log=[],
            )
            raise PortInUseError(port)
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
        try:
            if IS_WINDOWS:
                shell_argv = ["cmd", "/c", command]
                extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            else:
                shell_argv = ["bash", "-lc", command]
                extra = {"start_new_session": True}
            if self.contained:
                shell_argv = pid_namespace_argv(
                    shell_argv,
                    cwd=cwd,
                    label="project app",
                )
            proc = await asyncio.create_subprocess_exec(
                *shell_argv, cwd=cwd, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                **extra,
            )
        except BaseException:
            if effect_lease is not None:
                effect_lease.release()
            raise
        self._apps[slug] = {
            "proc": proc,
            "port": port,
            "command": command,
            "started_at": time.time(),
            "log": [],
            "effect_lease": effect_lease,
        }
        asyncio.create_task(self._drain(slug, proc))

    async def _drain(self, slug: str, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                app = self._apps.get(slug)
                if app and app.get("proc") is proc:
                    text = line.decode("utf-8", "replace").rstrip()
                    app["log"].append(text)
                    del app["log"][:-200]
                    if not app.get("detected_port"):
                        m = _PORT_RE.search(text) or _PORT_RE2.search(text)
                        if m:
                            found = int(m.group(1))
                            if 1024 <= found <= 65535:
                                app["detected_port"] = found
            wait = getattr(proc, "wait", None)
            if wait is not None:
                await wait()
        finally:
            app = self._apps.get(slug)
            if app and app.get("proc") is proc:
                self._finish_effect(
                    app,
                    terminated=proc.returncode is not None,
                )

    @staticmethod
    def _stopped_status(previous: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": "stopped",
            "running": False,
            "ready": False,
        }
        for key in ("requested_port", "command", "exit_code"):
            if key in previous:
                result[key] = previous[key]
        result["log"] = list(previous.get("log") or [])[-40:]
        return result

    async def stop(
        self,
        slug: str,
        *,
        preserve_status: bool = True,
    ) -> None:
        previous = self._last_exit.pop(slug, None)
        app = self._apps.get(slug)
        if not app:
            if preserve_status and previous:
                self._last_exit[slug] = self._stopped_status(previous)
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
        if self._apps.get(slug) is app:
            self._apps.pop(slug, None)
        self._finish_effect(
            app,
            terminated=proc.returncode is not None,
        )
        if preserve_status:
            stopped = {
                "requested_port": app["port"],
                "command": app["command"],
                "log": app["log"],
            }
            if proc.returncode is not None:
                stopped["exit_code"] = int(proc.returncode)
            self._last_exit[slug] = self._stopped_status(stopped)

    @staticmethod
    def _port_conflict_status(
        *,
        command: str,
        requested_port: int,
        log: list[str],
    ) -> dict[str, Any]:
        return {
            "state": "port_conflict",
            "running": False,
            "ready": False,
            "requested_port": requested_port,
            "command": command,
            "log": log[-40:],
            "message": (
                f"Port {requested_port} belongs to another process. "
                "Proxima did not open, proxy, or stop it."
            ),
        }

    @staticmethod
    def _signal_managed_process(app: dict[str, Any]) -> None:
        """Signal only the process identity Proxima spawned, never a port owner."""
        if app.get("termination_sent"):
            return
        app["termination_sent"] = True
        proc = app["proc"]
        if proc.returncode is not None:
            return
        try:
            if IS_WINDOWS:
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    @staticmethod
    def _starting_status(
        app: dict[str, Any],
        *,
        prolonged: bool,
    ) -> dict[str, Any]:
        return {
            "state": "starting",
            "running": True,
            "ready": False,
            "requested_port": app["port"],
            "command": app["command"],
            "log": app["log"][-40:],
            "prolonged_start": prolonged,
        }

    def status(self, slug: str) -> dict[str, Any]:
        app = self._apps.get(slug)
        if not app:
            return self._last_exit.get(slug) or {
                "state": "stopped",
                "running": False,
                "ready": False,
            }
        if app.get("terminal_state") == "port_conflict":
            result = self._port_conflict_status(
                command=app["command"],
                requested_port=app["port"],
                log=app["log"],
            )
            if app["proc"].returncode is not None:
                self._apps.pop(slug, None)
                self._finish_effect(app, terminated=True)
                self._last_exit[slug] = result
            return result
        if app["proc"].returncode is not None:  # exited on its own
            self._apps.pop(slug, None)
            self._finish_effect(app, terminated=True)
            # exit_code + exited stay sticky across 2s polls so the UI can say
            # "Finished" vs "Failed" instead of a bare log dump after a short run.
            result = {
                "state": "exited",
                "running": False,
                "ready": False,
                "requested_port": app["port"],
                "command": app["command"],
                "log": app["log"][-40:],
                "exited": True,
                "exit_code": int(app["proc"].returncode),
            }
            self._last_exit[slug] = result
            return result
        candidate_port = app.get("detected_port") or app["port"]
        if not _port_open(candidate_port):
            return self._starting_status(
                app,
                prolonged=(
                    time.time() - app["started_at"]
                    >= PROLONGED_START_SECONDS
                ),
            )
        ownership = _listener_ownership(
            app["proc"].pid,
            candidate_port,
            contained=self.contained,
        )
        if ownership == PortOwnership.FOREIGN:
            app["terminal_state"] = "port_conflict"
            self._signal_managed_process(app)
            return self._port_conflict_status(
                command=app["command"],
                requested_port=app["port"],
                log=app["log"],
            )
        if ownership in (PortOwnership.UNKNOWN, PortOwnership.DETACHED):
            reason = (
                "The listener detached from Proxima's managed process group, "
                "so its lifetime cannot be verified."
                if ownership == PortOwnership.DETACHED
                else "Proxima cannot verify who owns the listener on this host."
            )
            return {
                "state": "ownership_unknown",
                "running": True,
                "ready": False,
                "requested_port": app["port"],
                "command": app["command"],
                "log": app["log"][-40:],
                "message": reason,
            }
        if ownership == PortOwnership.NO_LISTENER:
            return self._starting_status(
                app,
                prolonged=(
                    time.time() - app["started_at"]
                    >= PROLONGED_START_SECONDS
                ),
            )
        out = {
            "state": "ready",
            "running": True,
            "ready": True,
            "requested_port": app["port"],
            "port": candidate_port,
            "command": app["command"],
            "log": app["log"][-40:],
        }
        # A dev server listening beyond loopback is directly reachable by other
        # LAN/tailnet devices with no auth - the gated relay does not protect a
        # broadly-bound origin. Surface it so the UI can warn the owner.
        if port_bound_non_loopback(candidate_port):
            out["broad_bind"] = True
        return out

    def preview_target(self, slug: str) -> int | None:
        """Return only a currently ownership-verified ready endpoint."""
        status = self.status(slug)
        if status.get("state") != "ready" or status.get("ready") is not True:
            return None
        port = status.get("port")
        return int(port) if isinstance(port, int) else None

    async def shutdown(self) -> None:
        for slug in list(self._apps):
            await self.stop(slug, preserve_status=False)

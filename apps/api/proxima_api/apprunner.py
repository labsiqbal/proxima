"""Run a project's app (dev server) as a managed background process and proxy it.

Lets you preview something the agent built — e.g. `npm run dev` — live inside
Proxima. One managed process per project; the HTTP proxy forwards to its port so
relative assets resolve and no port is exposed directly.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from enum import Enum
import json
import os
import re
import secrets
import socket
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .preview_output import (
    BrokerManagedProcess,
    OutputBroker,
    OutputBrokerUnavailable,
    OutputDelta,
    OutputSnapshot,
    process_start_time,
)
from .runners import subprocess_env

PROLONGED_START_SECONDS = 15
OUTPUT_POLL_SECONDS = 0.05
SHUTDOWN_GRACE_SECONDS = 14
_LINEAGE_ENV = "PROXIMA_APP_LINEAGE"


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


@dataclass(frozen=True)
class ProcessAuthority:
    leader_pid: int
    process_group: int | None
    lineage_token: str
    containment_required: bool
    containment_pid_namespace: int | None


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
    port: int,
    *,
    authority: ProcessAuthority,
) -> PortOwnership:
    """Classify a listener without equating reachability with ownership.

    A normal app server stays in the fresh process group created for the command.
    A detached descendant is accepted only inside Proxima's PID namespace, where
    namespace teardown owns its full lifetime. On non-procfs hosts, inaccessible
    procfs, or incomplete socket-to-process evidence, preview fails closed.
    """
    inodes = _listening_socket_inodes(port)
    return _socket_ownership(
        inodes,
        authority=authority,
    )


def _connected_socket_inodes(
    port: int,
    client_port: int,
) -> set[str] | None:
    server_port = f"{int(port):04X}"
    peer_port = f"{int(client_port):04X}"
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
            if (
                len(cols) > 9
                and cols[3] == "01"
                and cols[1].endswith(":" + server_port)
                and cols[2].endswith(":" + peer_port)
            ):
                inodes.add(cols[9])
    return inodes if checked else None


def _socket_ownership(
    inodes: set[str] | None,
    *,
    authority: ProcessAuthority,
) -> PortOwnership:
    if inodes is None:
        return PortOwnership.UNKNOWN
    if not inodes:
        return PortOwnership.NO_LISTENER
    group_id = authority.process_group
    if group_id is None:
        try:
            group_id = os.getpgid(authority.leader_pid)
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
    managed_group = all(
        processes[pid][1] == group_id
        for pid in owner_pids
    )
    descendants = all(
        _is_descendant(pid, authority.leader_pid, processes)
        for pid in owner_pids
    )
    live_lineage = all(
        processes[pid][1] == group_id
        or _is_descendant(pid, authority.leader_pid, processes)
        for pid in owner_pids
    )
    lineage_matches = all(
        _process_has_lineage(pid, authority.lineage_token)
        for pid in owner_pids
    )
    if not authority.containment_required:
        if managed_group:
            return PortOwnership.VERIFIED
        return (
            PortOwnership.DETACHED
            if descendants or lineage_matches
            else PortOwnership.FOREIGN
        )
    if not managed_group and not descendants and not lineage_matches:
        return PortOwnership.FOREIGN
    if (
        not live_lineage
        or not lineage_matches
        or authority.containment_pid_namespace is None
    ):
        return PortOwnership.DETACHED
    owner_namespaces = {
        _pid_namespace_id(pid)
        for pid in owner_pids
    }
    if None in owner_namespaces:
        return PortOwnership.UNKNOWN
    if owner_namespaces != {authority.containment_pid_namespace}:
        return PortOwnership.DETACHED
    return PortOwnership.VERIFIED


def _process_has_lineage(pid: int, lineage_token: str) -> bool:
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            environment = fh.read().split(b"\0")
    except OSError:
        return False
    marker = f"{_LINEAGE_ENV}={lineage_token}".encode()
    return marker in environment


def _pid_namespace_id(pid: int) -> int | None:
    try:
        identity = os.readlink(f"/proc/{pid}/ns/pid")
    except OSError:
        return None
    match = re.fullmatch(r"pid:\[(\d+)\]", identity)
    return int(match.group(1)) if match else None


def _connected_socket_ownership(
    port: int,
    client_port: int,
    *,
    authority: ProcessAuthority,
) -> PortOwnership:
    return _socket_ownership(
        _connected_socket_inodes(port, client_port),
        authority=authority,
    )


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


@dataclass
class LaunchReservation:
    generation: int
    cleanup_task: asyncio.Task[Any] | None = None
    active: bool = False


class AppManager:
    def __init__(
        self,
        *,
        contained: bool = False,
        output_broker_factory: (
            Callable[[], Awaitable[OutputBroker]] | None
        ) = None,
        state_root: str | Path | None = None,
        profile: str = "direct",
    ) -> None:
        self.contained = contained
        self._apps: dict[str, dict[str, Any]] = {}
        # Last terminal/stopped payload per slug, kept until the next start so
        # status polling can recover the bounded command buffer after page reload
        # and after an explicit Stop.
        self._last_exit: dict[str, dict[str, Any]] = {}
        self._retained_effects: list[EffectLease] = []
        self._output_broker_factory = (
            output_broker_factory or OutputBroker.open
        )
        self._cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._reservations: dict[str, LaunchReservation] = {}
        self._unadopted: set[str] = set()
        self._state_root = Path(state_root) if state_root else None
        self._profile = profile

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

    def _track_cleanup(
        self,
        awaitable: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable)
        self._cleanup_tasks.add(task)

        def settled(done: asyncio.Task[Any]) -> None:
            self._cleanup_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(settled)
        return task

    @staticmethod
    def _output_unavailable_status(
        *,
        command: str,
        requested_port: int,
        message: str,
        log: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": "stopped",
            "running": False,
            "ready": False,
            "requested_port": requested_port,
            "command": command,
            "log": list(log or [])[-40:],
            "reason": "output_sink_unavailable",
            "message": message,
        }

    def _lifecycle_lock(self, slug: str) -> asyncio.Lock:
        return self._lifecycle_locks.setdefault(slug, asyncio.Lock())

    async def _settle_reservation(self, slug: str) -> None:
        reservation = self._reservations.get(slug)
        if reservation is None or reservation.cleanup_task is None:
            return
        await asyncio.shield(reservation.cleanup_task)

    def _clear_reservation(
        self,
        slug: str,
        generation: int,
    ) -> None:
        reservation = self._reservations.get(slug)
        if (
            reservation is not None
            and reservation.generation == generation
        ):
            self._reservations.pop(slug, None)

    async def start(
        self,
        slug: str,
        cwd: str,
        command: str,
        port: int,
        *,
        effect_lease: EffectLease | None = None,
    ) -> None:
        reservation: LaunchReservation | None = None
        try:
            async with self._lifecycle_lock(slug):
                await self._settle_reservation(slug)
                await self._stop_locked(
                    slug,
                    preserve_status=False,
                )
                if slug in self._unadopted:
                    raise OutputBrokerUnavailable(
                        "A prior preview scope is still live without complete "
                        "adoption proof. Restart after removing that scope."
                    )
                generation = self._generations.get(slug, 0) + 1
                self._generations[slug] = generation
                reservation = LaunchReservation(generation)
                self._reservations[slug] = reservation
                try:
                    await self._start_reserved(
                        slug=slug,
                        cwd=cwd,
                        command=command,
                        port=port,
                        effect_lease=effect_lease,
                        reservation=reservation,
                    )
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    if reservation.cleanup_task is None:
                        self._clear_reservation(slug, generation)
                    raise
        except BaseException:
            if reservation is None and effect_lease is not None:
                effect_lease.release()
            raise

    async def _start_reserved(
        self,
        *,
        slug: str,
        cwd: str,
        command: str,
        port: int,
        effect_lease: EffectLease | None,
        reservation: LaunchReservation,
    ) -> None:
        if _port_open(port):
            self._last_exit[slug] = self._port_conflict_status(
                command=command,
                requested_port=port,
                log=[],
            )
            if effect_lease is not None:
                effect_lease.release()
            raise PortInUseError(port)
        self._last_exit.pop(slug, None)
        env = subprocess_env(
            allowlist_env="PROXIMA_APP_ENV_ALLOWLIST",
            inherit_env="PROXIMA_APP_INHERIT_ENV",
        )
        lineage_token = secrets.token_urlsafe(24)
        env["PORT"] = str(port)
        env[_LINEAGE_ENV] = lineage_token
        env.setdefault("HOST", "127.0.0.1")
        broker_task = asyncio.create_task(self._output_broker_factory())
        try:
            broker = await asyncio.shield(broker_task)
        except asyncio.CancelledError:
            cleanup = self._track_cleanup(
                self._dispose_opening_broker(
                    slug=slug,
                    generation=reservation.generation,
                    broker_task=broker_task,
                    effect_lease=effect_lease,
                )
            )
            reservation.cleanup_task = cleanup
            raise
        except OutputBrokerUnavailable as exc:
            self._last_exit[slug] = self._output_unavailable_status(
                command=command,
                requested_port=port,
                message=str(exc),
            )
            if effect_lease is not None:
                effect_lease.release()
            raise
        except BaseException:
            if effect_lease is not None:
                effect_lease.release()
            raise
        shell_argv = (
            ["cmd", "/c", command]
            if IS_WINDOWS
            else ["bash", "-lc", command]
        )
        spawn_task = asyncio.create_task(
            broker.spawn(
                shell_argv,
                cwd=cwd,
                env=env,
                contained=self.contained,
            )
        )
        try:
            proc = await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            cleanup = self._track_cleanup(
                self._complete_cancelled_spawn(
                    slug=slug,
                    generation=reservation.generation,
                    spawn_task=spawn_task,
                    broker=broker,
                    port=port,
                    command=command,
                    lineage_token=lineage_token,
                    effect_lease=effect_lease,
                )
            )
            reservation.cleanup_task = cleanup
            raise
        except BaseException:
            self._track_cleanup(broker.disconnect())
            if effect_lease is not None:
                effect_lease.release()
            raise
        try:
            self._register_app(
                slug=slug,
                generation=reservation.generation,
                proc=proc,
                broker=broker,
                port=port,
                command=command,
                lineage_token=lineage_token,
                effect_lease=effect_lease,
            )
        except BaseException as exc:
            cleanup = self._track_cleanup(
                self._dispose_failed_registration(
                    slug=slug,
                    generation=reservation.generation,
                    proc=proc,
                    broker=broker,
                    effect_lease=effect_lease,
                )
            )
            reservation.cleanup_task = cleanup
            self._last_exit[slug] = self._output_unavailable_status(
                command=command,
                requested_port=port,
                message=f"Preview authority could not be persisted: {exc}",
            )
            raise OutputBrokerUnavailable(
                "Preview authority could not be persisted"
            ) from exc
        reservation.active = True

    async def _dispose_opening_broker(
        self,
        *,
        slug: str,
        generation: int,
        broker_task: asyncio.Task[OutputBroker],
        effect_lease: EffectLease | None,
    ) -> None:
        try:
            try:
                broker = await broker_task
            except BaseException:
                broker = None
            if broker is not None:
                await broker.disconnect()
        except BaseException:
            pass
        finally:
            if effect_lease is not None:
                effect_lease.release()
            self._clear_reservation(slug, generation)

    async def _complete_cancelled_spawn(
        self,
        *,
        slug: str,
        generation: int,
        spawn_task: asyncio.Task[BrokerManagedProcess],
        broker: OutputBroker,
        port: int,
        command: str,
        lineage_token: str,
        effect_lease: EffectLease | None,
    ) -> None:
        try:
            proc = await spawn_task
        except BaseException:
            await broker.disconnect()
            if effect_lease is not None:
                effect_lease.release()
            self._clear_reservation(slug, generation)
            return
        reservation = self._reservations.get(slug)
        if reservation is None or reservation.generation != generation:
            try:
                await proc.terminate()
                if await self._wait_for_returncode(proc, 4) is False:
                    await proc.kill()
                    await self._wait_for_returncode(proc, 2)
            finally:
                await broker.disconnect()
                if effect_lease is not None:
                    effect_lease.release()
            return
        app = self._register_app(
            slug=slug,
            generation=generation,
            proc=proc,
            broker=broker,
            port=port,
            command=command,
            lineage_token=lineage_token,
            effect_lease=effect_lease,
        )
        reservation.active = True
        await self._stop_app(
            slug,
            app,
            preserve_status=False,
        )

    async def _dispose_failed_registration(
        self,
        *,
        slug: str,
        generation: int,
        proc: BrokerManagedProcess,
        broker: OutputBroker,
        effect_lease: EffectLease | None,
    ) -> None:
        app = self._apps.get(slug)
        if app is not None and int(app.get("generation") or 0) == generation:
            await self._stop_app(slug, app, preserve_status=False)
            return
        try:
            await proc.terminate()
            if not await self._wait_for_returncode(proc, 4):
                await proc.kill()
                await self._wait_for_returncode(proc, 2)
        finally:
            await broker.disconnect()
            if effect_lease is not None:
                effect_lease.release()
            self._clear_reservation(slug, generation)

    @staticmethod
    def _cgroup_identity(pid: int) -> str | None:
        try:
            return Path(f"/proc/{int(pid)}/cgroup").read_text(
                encoding="utf-8"
            )
        except OSError:
            return None

    def _record_path(
        self,
        slug: str,
        generation: int,
    ) -> Path | None:
        if self._state_root is None:
            return None
        if not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            slug,
        ):
            raise ValueError("Preview scope slug is invalid")
        return self._state_root / f"{slug}.{generation}.json"

    def _persist_app(
        self,
        slug: str,
        app: dict[str, Any],
    ) -> None:
        path = self._record_path(slug, int(app["generation"]))
        if path is None:
            return
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(path.parent, 0o700)
        proc: BrokerManagedProcess = app["proc"]
        broker: OutputBroker = app["output_broker"]
        payload = {
            "version": 1,
            "profile": self._profile,
            "slug": slug,
            "generation": app["generation"],
            "port": app["port"],
            "command": app["command"],
            "started_at": app["started_at"],
            "lineage_token": app["authority"].lineage_token,
            "contained": app["authority"].containment_required,
            "containment_pid_namespace": (
                app["authority"].containment_pid_namespace
            ),
            "process": {
                "pid": proc.pid,
                "start_time": proc.start_time,
                "cgroup": self._cgroup_identity(proc.pid),
            },
            "broker": broker.metadata,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _remove_app_record(
        self,
        slug: str,
        app: dict[str, Any],
    ) -> None:
        path = self._record_path(slug, int(app["generation"]))
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _register_app(
        self,
        *,
        slug: str,
        generation: int,
        proc: BrokerManagedProcess,
        broker: OutputBroker,
        port: int,
        command: str,
        lineage_token: str,
        effect_lease: EffectLease | None,
        started_at: float | None = None,
        containment_pid_namespace: int | None = None,
    ) -> dict[str, Any]:
        authority = ProcessAuthority(
            leader_pid=proc.pid,
            process_group=proc.pid if not IS_WINDOWS else None,
            lineage_token=lineage_token,
            containment_required=self.contained,
            containment_pid_namespace=containment_pid_namespace,
        )
        app = {
            "generation": generation,
            "proc": proc,
            "port": port,
            "command": command,
            "started_at": started_at or time.time(),
            "log": [],
            "log_complete": [],
            "log_pending": "",
            "output_version": -1,
            "output_line_cursor": 0,
            "effect_lease": effect_lease,
            "authority": authority,
            "output_broker": broker,
            "stop_lock": asyncio.Lock(),
            "stopped": False,
        }
        self._apps[slug] = app
        if self.contained and containment_pid_namespace is None:
            app["authority_task"] = asyncio.create_task(
                self._complete_containment_authority(slug, app)
            )
        app["output_task"] = asyncio.create_task(
            self._watch_output(slug, app)
        )
        app["exit_task"] = asyncio.create_task(
            self._watch_exit(slug, app)
        )
        self._persist_app(slug, app)
        return app

    async def _complete_containment_authority(
        self,
        slug: str,
        app: dict[str, Any],
    ) -> None:
        proc: BrokerManagedProcess = app["proc"]
        namespace: int | None = None
        while proc.returncode is None:
            await proc.refresh()
            namespace = proc.containment_pid_namespace
            if namespace is not None:
                break
            await asyncio.sleep(0.01)
        if self._apps.get(slug) is app:
            app["authority"] = replace(
                app["authority"],
                containment_pid_namespace=namespace,
            )
            self._persist_app(slug, app)

    @staticmethod
    async def _settle_authority_task(app: dict[str, Any]) -> None:
        task = app.get("authority_task")
        if not isinstance(task, asyncio.Task) or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _adoption_unknown_status(
        record: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        return {
            "state": "ownership_unknown",
            "running": True,
            "ready": False,
            "requested_port": int(record.get("port") or 0),
            "command": str(record.get("command") or ""),
            "log": [],
            "message": message,
        }

    @staticmethod
    def _ended_scope_status(
        record: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "state": "stopped",
            "running": False,
            "ready": False,
            "requested_port": int(record.get("port") or 0),
            "command": str(record.get("command") or ""),
            "log": [],
            "message": (
                "The previous supervised preview ended while the API was "
                "offline. Retry to start a new generation."
            ),
        }

    @staticmethod
    def _scope_identity_ended(record: dict[str, Any]) -> bool:
        broker = record.get("broker")
        process = record.get("process")
        if not isinstance(broker, dict) or not isinstance(process, dict):
            return False
        broker_pid = broker.get("pid")
        broker_started = broker.get("start_time")
        process_pid = process.get("pid")
        process_started = process.get("start_time")
        if not all(
            isinstance(value, int) and value > 0
            for value in (
                broker_pid,
                broker_started,
                process_pid,
                process_started,
            )
        ):
            return False
        return (
            process_start_time(broker_pid) != broker_started
            and process_start_time(process_pid) != process_started
        )

    async def reconcile(self) -> None:
        if self._state_root is None or not self._state_root.exists():
            return
        records: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
        for path in self._state_root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if (
                not isinstance(record, dict)
                or record.get("version") != 1
                or record.get("profile") != self._profile
                or not isinstance(record.get("slug"), str)
                or not isinstance(record.get("generation"), int)
                or record["generation"] <= 0
                or not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    record["slug"],
                )
            ):
                continue
            records.setdefault(record["slug"], []).append((path, record))
        for slug, candidates in records.items():
            newest = max(
                candidates,
                key=lambda item: int(item[1].get("generation") or 0),
            )[1]
            self._generations[slug] = max(
                self._generations.get(slug, 0),
                *(
                    int(record.get("generation") or 0)
                    for _path, record in candidates
                ),
            )
            remaining: list[tuple[Path, dict[str, Any]]] = []
            for path, record in candidates:
                if self._scope_identity_ended(record):
                    path.unlink(missing_ok=True)
                else:
                    remaining.append((path, record))
            candidates = remaining
            if not candidates:
                self._last_exit[slug] = self._ended_scope_status(newest)
                continue
            if len(candidates) != 1:
                self._unadopted.add(slug)
                newest = max(
                    candidates,
                    key=lambda item: int(
                        item[1].get("generation") or 0
                    ),
                )[1]
                self._last_exit[slug] = self._adoption_unknown_status(
                    newest,
                    "Multiple durable preview scopes exist for this project.",
                )
                continue
            path, record = candidates[0]
            broker: OutputBroker | None = None
            try:
                broker_record = record.get("broker")
                process_record = record.get("process")
                if (
                    not isinstance(broker_record, dict)
                    or not isinstance(process_record, dict)
                ):
                    raise OutputBrokerUnavailable(
                        "Preview scope record is incomplete"
                    )
                broker = await OutputBroker.reconnect(broker_record)
                proc = await broker.managed_process()
                await proc.refresh()
                if proc.returncode is not None:
                    snapshot = await broker.snapshot()
                    app = {
                        "port": int(record["port"]),
                        "command": str(record["command"]),
                        "log": snapshot.lines,
                        "proc": proc,
                    }
                    self._last_exit[slug] = self._exited_status(app)
                    await broker.disconnect()
                    path.unlink(missing_ok=True)
                    continue
                broker_pid = int(broker_record.get("pid") or 0)
                process_pid = int(process_record.get("pid") or 0)
                broker_start = broker_record.get("start_time")
                process_started = process_record.get("start_time")
                broker_cgroup = self._cgroup_identity(broker_pid)
                process_cgroup = self._cgroup_identity(process_pid)
                namespace = proc.containment_pid_namespace
                proof = (
                    proc.pid == process_pid
                    and proc.start_time == process_started
                    and process_start_time(process_pid) == process_started
                    and broker.pid == broker_pid
                    and process_start_time(broker_pid) == broker_start
                    and broker_cgroup is not None
                    and process_cgroup is not None
                    and broker_cgroup == process_cgroup
                    and broker_cgroup == broker_record.get("cgroup")
                    and process_cgroup == process_record.get("cgroup")
                    and broker_record.get("controller_cgroup")
                    == self._cgroup_identity(os.getpid())
                    and broker_record.get("profile") == self._profile
                    and bool(record.get("contained")) == self.contained
                    and _process_has_lineage(
                        process_pid,
                        str(record.get("lineage_token") or ""),
                    )
                    and (
                        not self.contained
                        or (
                            isinstance(namespace, int)
                            and namespace
                            == record.get("containment_pid_namespace")
                        )
                    )
                )
                if not proof:
                    raise OutputBrokerUnavailable(
                        "Durable preview scope proof is incomplete"
                    )
                generation = int(record["generation"])
                self._generations[slug] = max(
                    self._generations.get(slug, 0),
                    generation,
                )
                reservation = LaunchReservation(
                    generation,
                    active=True,
                )
                self._reservations[slug] = reservation
                app = self._register_app(
                    slug=slug,
                    generation=generation,
                    proc=proc,
                    broker=broker,
                    port=int(record["port"]),
                    command=str(record["command"]),
                    lineage_token=str(record["lineage_token"]),
                    effect_lease=None,
                    started_at=float(record["started_at"]),
                    containment_pid_namespace=(
                        int(namespace)
                        if isinstance(namespace, int)
                        else None
                    ),
                )
                snapshot = await broker.snapshot()
                self._apply_output_snapshot(app, snapshot)
                app["log_complete"] = []
                app["log_pending"] = ""
                app["output_version"] = -1
                app["output_line_cursor"] = 0
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                OutputBrokerUnavailable,
            ) as exc:
                if broker is not None:
                    try:
                        await broker.disconnect()
                    except OutputBrokerUnavailable:
                        pass
                if self._scope_identity_ended(record):
                    path.unlink(missing_ok=True)
                    self._last_exit[slug] = self._ended_scope_status(
                        record
                    )
                    continue
                self._unadopted.add(slug)
                self._last_exit[slug] = self._adoption_unknown_status(
                    record,
                    str(exc),
                )

    @staticmethod
    def _apply_output_snapshot(
        app: dict[str, Any],
        snapshot: OutputSnapshot,
    ) -> None:
        app["log"] = list(snapshot.lines[-200:])
        app["log_complete"] = list(snapshot.lines[-200:])
        app["log_pending"] = ""
        app["output_version"] = snapshot.version
        app["output_line_cursor"] = snapshot.line_cursor
        AppManager._detect_output_port(app)

    @staticmethod
    def _apply_output_delta(
        app: dict[str, Any],
        delta: OutputDelta,
    ) -> None:
        if not delta.changed:
            return
        complete = (
            list(delta.lines)
            if delta.reset
            else [
                *app.get("log_complete", []),
                *delta.lines,
            ]
        )[-200:]
        app["log_complete"] = complete
        app["log_pending"] = delta.pending
        rendered = list(complete)
        if delta.pending:
            rendered.append(delta.pending)
        app["log"] = rendered[-200:]
        app["output_version"] = delta.version
        app["output_line_cursor"] = delta.line_cursor
        AppManager._detect_output_port(app)

    @staticmethod
    def _detect_output_port(app: dict[str, Any]) -> None:
        if app.get("detected_port"):
            return
        for text in reversed(app["log"]):
            match = _PORT_RE.search(text) or _PORT_RE2.search(text)
            if not match:
                continue
            found = int(match.group(1))
            if 1024 <= found <= 65535:
                app["detected_port"] = found
                return

    @staticmethod
    async def _snapshot_output(
        app: dict[str, Any],
    ) -> OutputSnapshot | None:
        try:
            snapshot = await app["output_broker"].snapshot()
        except OutputBrokerUnavailable as exc:
            app["output_error"] = str(exc)
            return None
        AppManager._apply_output_snapshot(app, snapshot)
        return snapshot

    @staticmethod
    async def _poll_output(
        app: dict[str, Any],
    ) -> OutputDelta | None:
        try:
            delta = await app["output_broker"].changes(
                since_version=int(app.get("output_version", -1)),
                after_line=int(app.get("output_line_cursor", 0)),
            )
        except OutputBrokerUnavailable as exc:
            app["output_error"] = str(exc)
            return None
        AppManager._apply_output_delta(app, delta)
        return delta

    async def _watch_output(
        self,
        slug: str,
        app: dict[str, Any],
    ) -> None:
        while self._apps.get(slug) is app and not app.get("stopped"):
            if not app.get("output_error"):
                await self._poll_output(app)
            await asyncio.sleep(OUTPUT_POLL_SECONDS)

    async def _watch_exit(
        self,
        slug: str,
        app: dict[str, Any],
    ) -> None:
        await app["proc"].wait()
        async with app["stop_lock"]:
            if app.get("stopped") or app.get("stop_requested"):
                return
            output_task = app.get("output_task")
            if (
                isinstance(output_task, asyncio.Task)
                and not output_task.done()
            ):
                output_task.cancel()
                await asyncio.gather(output_task, return_exceptions=True)
            await self._settle_authority_task(app)
            if not app.get("output_error"):
                await self._snapshot_output(app)
            result = self._terminal_status_after_exit(app)
            try:
                await app["output_broker"].disconnect()
            except OutputBrokerUnavailable as exc:
                app["output_error"] = str(exc)
            if app.get("output_error"):
                result["reason"] = "output_sink_unavailable"
                result["message"] = app["output_error"]
            if self._apps.get(slug) is app:
                self._apps.pop(slug, None)
            self._remove_app_record(slug, app)
            self._clear_reservation(slug, int(app["generation"]))
            self._last_exit[slug] = result
            self._finish_effect(app, terminated=True)
            app["stopped"] = True

    @staticmethod
    def _stopped_status(previous: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": "stopped",
            "running": False,
            "ready": False,
        }
        for key in (
            "requested_port",
            "command",
            "exit_code",
            "reason",
            "message",
        ):
            if key in previous:
                result[key] = previous[key]
        result["log"] = list(previous.get("log") or [])[-40:]
        return result

    @staticmethod
    async def _wait_for_returncode(
        proc: BrokerManagedProcess,
        timeout: float,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while await proc.refresh() is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.02, remaining))
        return True

    async def stop(
        self,
        slug: str,
        *,
        preserve_status: bool = True,
    ) -> None:
        async with self._lifecycle_lock(slug):
            await self._settle_reservation(slug)
            await self._stop_locked(
                slug,
                preserve_status=preserve_status,
            )

    async def _stop_locked(
        self,
        slug: str,
        *,
        preserve_status: bool,
    ) -> None:
        if slug in self._unadopted:
            return
        previous = self._last_exit.pop(slug, None)
        app = self._apps.get(slug)
        if not app:
            if preserve_status and previous:
                self._last_exit[slug] = self._stopped_status(previous)
            return
        stop_task = app.get("stop_task")
        if not isinstance(stop_task, asyncio.Task):
            stop_task = self._track_cleanup(
                self._stop_app(
                    slug,
                    app,
                    preserve_status=preserve_status,
                )
            )
            app["stop_task"] = stop_task
        await asyncio.shield(stop_task)

    async def _stop_app(
        self,
        slug: str,
        app: dict[str, Any],
        *,
        preserve_status: bool,
    ) -> None:
        async with app["stop_lock"]:
            if app.get("stopped"):
                if preserve_status and slug in self._last_exit:
                    self._last_exit[slug] = self._stopped_status(
                        self._last_exit[slug]
                    )
                return
            app["stop_requested"] = True
            lifecycle_tasks = [
                task
                for task in (
                    app.get("exit_task"),
                    app.get("output_task"),
                )
                if (
                    isinstance(task, asyncio.Task)
                    and task is not asyncio.current_task()
                    and not task.done()
                )
            ]
            for task in lifecycle_tasks:
                task.cancel()
            if lifecycle_tasks:
                await asyncio.gather(
                    *lifecycle_tasks,
                    return_exceptions=True,
                )
            proc = app["proc"]
            await self._settle_authority_task(app)
            try:
                await proc.refresh()
                if proc.returncode is None:
                    await proc.terminate()
                    if not await self._wait_for_returncode(proc, 4):
                        await proc.kill()
                        await self._wait_for_returncode(proc, 2)
                    if proc.returncode is None:
                        await proc.kill()
                        await self._wait_for_returncode(proc, 2)
                if proc.returncode is not None:
                    await proc.wait()
            except OutputBrokerUnavailable as exc:
                app["output_error"] = str(exc)
                app["stop_requested"] = False
                app["output_task"] = asyncio.create_task(
                    self._watch_output(slug, app)
                )
                app["exit_task"] = asyncio.create_task(
                    self._watch_exit(slug, app)
                )
                if preserve_status:
                    self._last_exit[slug] = {
                        "state": "ownership_unknown",
                        "running": True,
                        "ready": False,
                        "requested_port": app["port"],
                        "command": app["command"],
                        "log": app["log"][-40:],
                        "reason": "output_sink_unavailable",
                        "message": str(exc),
                    }
                return
            await self._snapshot_output(app)
            try:
                await app["output_broker"].disconnect()
            except OutputBrokerUnavailable as exc:
                app["output_error"] = str(exc)
            if self._apps.get(slug) is app:
                self._apps.pop(slug, None)
            self._remove_app_record(slug, app)
            self._finish_effect(
                app,
                terminated=proc.returncode is not None,
            )
            app["stopped"] = True
            self._clear_reservation(slug, int(app["generation"]))
            if preserve_status:
                stopped = {
                    "requested_port": app["port"],
                    "command": app["command"],
                    "log": app["log"],
                }
                if proc.returncode is not None:
                    stopped["exit_code"] = int(proc.returncode)
                result = self._stopped_status(stopped)
                if app.get("output_error"):
                    result["reason"] = "output_sink_unavailable"
                    result["message"] = app["output_error"]
                self._last_exit[slug] = result

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
    def _exited_status(app: dict[str, Any]) -> dict[str, Any]:
        return {
            "state": "exited",
            "running": False,
            "ready": False,
            "requested_port": app["port"],
            "command": app["command"],
            "log": app["log"][-40:],
            "exited": True,
            "exit_code": int(app["proc"].returncode),
        }

    def _terminal_status_after_exit(
        self,
        app: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_port = app.get("detected_port") or app["port"]
        ownership = (
            _listener_ownership(
                candidate_port,
                authority=app["authority"],
            )
            if _port_open(candidate_port)
            else PortOwnership.NO_LISTENER
        )
        if (
            app.get("terminal_state") == "port_conflict"
            or ownership == PortOwnership.FOREIGN
        ):
            app["terminal_state"] = "port_conflict"
            return self._port_conflict_status(
                command=app["command"],
                requested_port=app["port"],
                log=app["log"],
            )
        if ownership in (PortOwnership.UNKNOWN, PortOwnership.DETACHED):
            return self._ownership_unknown_status(app, ownership)
        return self._exited_status(app)

    def _signal_managed_process(self, app: dict[str, Any]) -> None:
        """Signal only the process identity Proxima spawned, never a port owner."""
        if app.get("termination_sent"):
            return
        app["termination_sent"] = True
        proc = app["proc"]
        if proc.returncode is not None:
            return
        self._track_cleanup(proc.terminate())

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

    @staticmethod
    def _ownership_unknown_status(
        app: dict[str, Any],
        ownership: PortOwnership,
    ) -> dict[str, Any]:
        reason = (
            "The listener lacks complete live managed-lineage proof, "
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

    def status(self, slug: str) -> dict[str, Any]:
        app = self._apps.get(slug)
        if not app:
            return self._last_exit.get(slug) or {
                "state": "stopped",
                "running": False,
                "ready": False,
            }
        if app.get("output_error"):
            if app["proc"].returncode is not None:
                result = self._exited_status(app)
                result["reason"] = "output_sink_unavailable"
                result["message"] = app["output_error"]
                return result
            return {
                "state": "ownership_unknown",
                "running": app["proc"].returncode is None,
                "ready": False,
                "requested_port": app["port"],
                "command": app["command"],
                "log": app["log"][-40:],
                "reason": "output_sink_unavailable",
                "message": app["output_error"],
            }
        if app.get("terminal_state") == "port_conflict":
            result = self._port_conflict_status(
                command=app["command"],
                requested_port=app["port"],
                log=app["log"],
            )
            return result
        candidate_port = app.get("detected_port") or app["port"]
        port_open = _port_open(candidate_port)
        ownership = (
            _listener_ownership(
                candidate_port,
                authority=app["authority"],
            )
            if port_open
            else PortOwnership.NO_LISTENER
        )
        if ownership == PortOwnership.FOREIGN:
            app["terminal_state"] = "port_conflict"
            self._signal_managed_process(app)
            result = self._port_conflict_status(
                command=app["command"],
                requested_port=app["port"],
                log=app["log"],
            )
            return result
        if ownership in (PortOwnership.UNKNOWN, PortOwnership.DETACHED):
            return self._ownership_unknown_status(app, ownership)
        if app["proc"].returncode is not None:
            return self._starting_status(app, prolonged=False)
        if not port_open:
            return self._starting_status(
                app,
                prolonged=(
                    time.time() - app["started_at"]
                    >= PROLONGED_START_SECONDS
                ),
            )
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

    def verify_preview_connection(
        self,
        slug: str,
        port: int,
        client_port: int,
    ) -> bool:
        app = self._apps.get(slug)
        if (
            not app
            or app["proc"].returncode is not None
            or app.get("terminal_state") == "port_conflict"
        ):
            return False
        candidate_port = app.get("detected_port") or app["port"]
        if int(candidate_port) != int(port):
            return False
        ownership = _connected_socket_ownership(
            port,
            client_port,
            authority=app["authority"],
        )
        if ownership == PortOwnership.FOREIGN:
            app["terminal_state"] = "port_conflict"
            self._signal_managed_process(app)
        return ownership == PortOwnership.VERIFIED

    async def shutdown(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SHUTDOWN_GRACE_SECONDS
        stop_requests = [
            self._track_cleanup(
                self.stop(slug, preserve_status=False)
            )
            for slug in list(self._apps)
        ]
        if stop_requests:
            await asyncio.wait(
                stop_requests,
                timeout=max(0, deadline - loop.time()),
            )
        while self._cleanup_tasks and loop.time() < deadline:
            await asyncio.wait(
                list(self._cleanup_tasks),
                timeout=max(0, deadline - loop.time()),
            )

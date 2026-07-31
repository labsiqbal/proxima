from __future__ import annotations

import ctypes
import json
import os
import secrets
import signal
import sqlite3
import stat
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


QUIESCENCE_TIMEOUT_SECONDS = 5.0

_LOCKS_GUARD = threading.Lock()
_MUTATION_LOCKS: dict[str, Any] = {}
_MUTATION_LOCK_DEPTH = threading.local()
_ACTIVITY_STATES: dict[str, "_ContainerActivityState"] = {}
_RETAINED_ACTIVITY_GUARD = threading.Lock()
_RETAINED_ACTIVITY_LEASES: list["ContainerActivityLease"] = []


class ContainerBoundaryError(ValueError):
    """A Container or Area root is missing, ambiguous, or unsafe."""


@dataclass(frozen=True)
class ContainerActivityRecovery:
    active: int = 0
    recovered: int = 0
    unresolved: int = 0


def _platform_is_windows() -> bool:
    return os.name == "nt"


def _container_data(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(container, int):
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?",
            (container,),
        ).fetchone()
        if row is None:
            raise ContainerBoundaryError(f"Container {container} does not exist")
        return dict(row)
    data = dict(container)
    if "id" not in data or "path" not in data:
        raise ContainerBoundaryError("Container row must include id and path")
    return data


def _database_path(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        raw = row["file"] if isinstance(row, sqlite3.Row) else row[2]
        if name == "main" and raw:
            return Path(str(raw)).resolve()
    return None


def _windows_overlapped_type():
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("internal", ctypes.c_size_t),
            ("internal_high", ctypes.c_size_t),
            ("offset", wintypes.DWORD),
            ("offset_high", wintypes.DWORD),
            ("event", wintypes.HANDLE),
        ]

    return Overlapped


def _windows_lock_file(
    handle: int,
    *,
    shared: bool,
    fail_immediately: bool = False,
) -> None:
    from ctypes import wintypes

    overlapped = _windows_overlapped_type()()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lock_file = kernel32.LockFileEx
    lock_file.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(type(overlapped)),
    ]
    lock_file.restype = wintypes.BOOL
    flags = 0 if shared else 0x00000002
    if fail_immediately:
        flags |= 0x00000001
    if not lock_file(
        ctypes.c_void_p(handle),
        flags,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))


def _windows_unlock_file(handle: int) -> None:
    from ctypes import wintypes

    overlapped = _windows_overlapped_type()()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    unlock_file = kernel32.UnlockFileEx
    unlock_file.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(type(overlapped)),
    ]
    unlock_file.restype = wintypes.BOOL
    if not unlock_file(
        ctypes.c_void_p(handle),
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))


def _acquire_file_lock(
    path: Path,
    *,
    shared: bool = False,
    timeout: float | None = None,
) -> int:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ContainerBoundaryError("Container mutation lock directory is unsafe")
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            handle = msvcrt.get_osfhandle(fd)
            if timeout is None:
                _windows_lock_file(handle, shared=shared)
            else:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        _windows_lock_file(
                            handle,
                            shared=shared,
                            fail_immediately=True,
                        )
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ContainerBoundaryError(
                                "Container has active processes; stop them before retrying"
                            )
                        time.sleep(0.02)
        else:
            import fcntl

            operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            if timeout is None:
                fcntl.flock(fd, operation)
            else:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, operation | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ContainerBoundaryError(
                                "Container has active processes; stop them before retrying"
                            )
                        time.sleep(0.02)
    except Exception:
        os.close(fd)
        raise
    return fd


def _release_file_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            _windows_unlock_file(msvcrt.get_osfhandle(fd))
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _lock_key(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> tuple[dict[str, Any], str, Path | None]:
    data = _container_data(conn, container)
    database = _database_path(conn)
    if database is None:
        return data, f"memory:{data['id']}:{data['path']}", None
    key = f"{database}:{data['id']}"
    lock_dir = database.parent / f".{database.name}.container-locks"
    return data, key, lock_dir


class _ContainerActivityState:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.readers = 0
        self.writer = False

    def acquire(
        self,
        *,
        shared: bool,
        timeout: float | None = None,
    ) -> None:
        with self.condition:
            deadline = None if timeout is None else time.monotonic() + timeout

            def wait() -> None:
                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                if remaining == 0.0 or not self.condition.wait(remaining):
                    raise ContainerBoundaryError(
                        "Container has active processes; stop them before retrying"
                    )

            if shared:
                while self.writer:
                    wait()
                self.readers += 1
                return
            while self.writer or self.readers:
                wait()
            self.writer = True

    def release(self, *, shared: bool) -> None:
        with self.condition:
            if shared:
                self.readers -= 1
            else:
                self.writer = False
            self.condition.notify_all()


def process_start_identity(pid: int) -> str | None:
    return _process_start_identity(pid)


@dataclass
class GuardedWriterTree:
    """Identity-bound handle for a guarded Project writer tree.

    Launcher exit never implies tree exit. Prefer the guardian-record sentinel
    (or Windows job) as the durable root; fall back to the launcher only while
    that identity is still live. Known member identities seed fail-closed retain
    when stop cannot fully prove exit.
    """

    launcher_pid: int | None = None
    launcher_start: str | None = None
    guardian_record: Path | None = None
    known_identities: dict[int, str] = None  # type: ignore[assignment]
    job_name: str | None = None
    members_observed: bool = False

    def __post_init__(self) -> None:
        raw_identities = self.known_identities or {}
        cleaned: dict[int, str] = {}
        for raw_pid, raw_start in raw_identities.items():
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                continue
            start = str(raw_start or "")
            if pid > 1 and start:
                cleaned[pid] = start
        self.known_identities = cleaned
        if self.launcher_pid is not None:
            try:
                self.launcher_pid = int(self.launcher_pid)
            except (TypeError, ValueError):
                self.launcher_pid = None
        if self.launcher_start is not None:
            self.launcher_start = str(self.launcher_start) or None
        if self.guardian_record is not None:
            self.guardian_record = Path(self.guardian_record)
        self.members_observed = bool(self.members_observed)

    @classmethod
    def bind(
        cls,
        lease: Any | None = None,
        *,
        launcher_pid: int | None = None,
        launcher_start: str | None = None,
        known_pids: set[int] | None = None,
        guardian_record: Path | None = None,
        job_name: str | None = None,
    ) -> "GuardedWriterTree":
        record = guardian_record
        if record is None and lease is not None:
            cursor = lease
            seen: set[int] = set()
            while cursor is not None and id(cursor) not in seen:
                seen.add(id(cursor))
                raw = getattr(cursor, "_guardian_record", None)
                if raw is not None:
                    record = Path(raw)
                    break
                cursor = getattr(cursor, "_activity", None) or getattr(
                    cursor, "_inner", None
                )
        identities: dict[int, str] = {}
        if known_pids:
            for raw_pid in known_pids:
                try:
                    pid = int(raw_pid)
                except (TypeError, ValueError):
                    continue
                start = _process_start_identity(pid)
                if start:
                    identities[pid] = start
        if launcher_pid is not None and launcher_start and int(launcher_pid) > 1:
            identities.setdefault(int(launcher_pid), str(launcher_start))
        return cls(
            launcher_pid=launcher_pid,
            launcher_start=launcher_start,
            guardian_record=record,
            known_identities=identities,
            job_name=job_name,
        )

    def _read_record(self) -> dict[str, Any] | None:
        if self.guardian_record is None:
            return None
        try:
            record_stat = self.guardian_record.lstat()
            if stat.S_ISLNK(record_stat.st_mode) or not stat.S_ISREG(
                record_stat.st_mode
            ):
                return None
            return json.loads(self.guardian_record.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _sentinel_identity(self) -> tuple[int, str] | None:
        payload = self._read_record()
        if payload is None:
            return None
        try:
            pid = int(payload["sentinel_pid"])
            start = str(payload["sentinel_start"] or "")
        except (KeyError, TypeError, ValueError):
            return None
        if pid <= 1 or not start:
            return None
        job = payload.get("job_name")
        if job and not self.job_name:
            self.job_name = str(job)
        return pid, start

    def seed_live_members(self) -> None:
        """Capture currently visible tree members under launcher/sentinel.

        Observation is complete only when a stable process-tree walk records an
        identity for every live member, including at least one non-root writer.
        Partial, racy, unsupported, or sentinel-only walks leave
        ``members_observed`` unset so recovery fails closed.
        """
        from .process_containment import process_tree_pids

        roots: list[int] = []
        if self.launcher_pid is not None and self.launcher_start:
            if (
                _process_has_identity(
                    self.launcher_pid,
                    self.launcher_start,
                )
                is True
            ):
                roots.append(self.launcher_pid)
        sentinel = self._sentinel_identity()
        if sentinel is not None:
            pid, start = sentinel
            if _process_has_identity(pid, start) is True:
                roots.append(pid)
                self.known_identities[pid] = start
        if not roots:
            return

        complete = True
        inspected = False
        observed_non_root = False
        for root in roots:
            first = process_tree_pids(root)
            if first is None:
                complete = False
                continue
            # Stabilize against /proc TOCTOU: require two identical walks before
            # treating the member set as identity-complete.
            second = process_tree_pids(root)
            if second is None or second != first:
                complete = False
                for raw_pid in set(first) | set(second or ()):
                    start = _process_start_identity(int(raw_pid))
                    if start:
                        self.known_identities[int(raw_pid)] = start
                continue
            inspected = True
            for raw_pid in first:
                pid = int(raw_pid)
                if pid != int(root):
                    observed_non_root = True
                start = _process_start_identity(pid)
                if start:
                    self.known_identities[pid] = start
                    continue
                # Live member without a stable identity keeps observation open.
                if _process_exists(pid):
                    complete = False
        if complete and inspected and observed_non_root:
            self.members_observed = True

    def monitor_roots(self) -> list[tuple[int, str]]:
        """Identities whose liveness keeps the writer tree active."""
        roots: list[tuple[int, str]] = []
        seen: set[int] = set()
        sentinel = self._sentinel_identity()
        if sentinel is not None:
            pid, start = sentinel
            roots.append((pid, start))
            seen.add(pid)
            self.known_identities[pid] = start
        if (
            self.launcher_pid is not None
            and self.launcher_start
            and self.launcher_pid not in seen
        ):
            roots.append((self.launcher_pid, self.launcher_start))
            seen.add(self.launcher_pid)
        for pid, start in list(self.known_identities.items()):
            if pid in seen:
                continue
            roots.append((pid, start))
            seen.add(pid)
        return roots

    def has_binding(self) -> bool:
        if self.launcher_pid is not None and self.launcher_start:
            return True
        if self.known_identities:
            return True
        if self.guardian_record is not None:
            try:
                return self.guardian_record.exists()
            except OSError:
                return True
        return False

    def exited(self) -> bool | None:
        """True when every bound writer identity is gone.

        False when any bound identity is still live. None when the tree cannot
        be proven either way (caller must fail closed).

        Launcher/sentinel death never implies tree exit. A still-present
        guardian record is authoritative evidence that clean sentinel teardown
        did not finish, so exit stays unproven even when every previously
        observed identity looks dead.
        """
        from .process_containment import process_tree_pids

        roots = self.monitor_roots()
        if not roots:
            if self.guardian_record is not None:
                try:
                    exists = self.guardian_record.exists()
                except OSError:
                    return None
                if exists:
                    # Record present but sentinel identity unusable - unprovable.
                    return None
                # Absent record with no bound identities is not proof of a prior
                # clean tree exit (acquire always allocates a record path).
                if self.launcher_start or self.known_identities:
                    return True
                return None
            return None

        saw_dead = False
        for pid, start in roots:
            alive = _process_has_identity(pid, start)
            if alive is True:
                # Arm descendant identities while the root is still live so a
                # later sentinel crash cannot orphan writers unobserved.
                first = process_tree_pids(pid)
                second = process_tree_pids(pid) if first is not None else None
                if first is not None and second == first:
                    complete = True
                    observed_non_root = False
                    for child in first:
                        child_pid = int(child)
                        if child_pid != int(pid):
                            observed_non_root = True
                        child_start = _process_start_identity(child_pid)
                        if child_start:
                            self.known_identities[child_pid] = child_start
                        elif _process_exists(child_pid):
                            complete = False
                    if complete and observed_non_root:
                        self.members_observed = True
                elif first is not None:
                    for child in set(first) | set(second or ()):
                        child_start = _process_start_identity(int(child))
                        if child_start:
                            self.known_identities[int(child)] = child_start
                return False
            if alive is None:
                if _process_exists(pid):
                    return None
                saw_dead = True
                continue
            saw_dead = True

        for pid, start in list(self.known_identities.items()):
            alive = _process_has_identity(pid, start)
            if alive is True:
                return False
            if alive is None and _process_exists(pid):
                return None
            if alive is False:
                saw_dead = True

        # Clean guardian teardown removes the record before exit. A leftover
        # record after launcher/sentinel death means orphans may still exist
        # outside known_identities - fail closed rather than releasing.
        if self.guardian_record is not None:
            try:
                if self.guardian_record.exists():
                    return None
            except OSError:
                return None

        return True if saw_dead else None

    def terminate(
        self,
        *,
        grace_seconds: float = 4.0,
        kill_seconds: float = 2.0,
        initial_signal: int | None = None,
    ) -> bool:
        """Signal the identity-bound tree; True only when exit is proven."""
        from .process_containment import terminate_process_tree

        self.seed_live_members()
        if self.exited() is True:
            return True

        if not self.job_name:
            sentinel_payload = self._read_record()
            if sentinel_payload is not None:
                job = sentinel_payload.get("job_name")
                if job:
                    self.job_name = str(job)

        if self.job_name and _platform_is_windows():
            _terminate_windows_job(self.job_name)
            deadline = time.monotonic() + max(
                0.05,
                float(grace_seconds) + float(kill_seconds),
            )
            while time.monotonic() < deadline:
                if self.exited() is True:
                    return True
                time.sleep(0.05)
            return self.exited() is True

        preferred: list[int] = []
        seen: set[int] = set()
        sentinel = self._sentinel_identity()
        if sentinel is not None:
            pid, start = sentinel
            if _process_has_identity(pid, start) is True:
                preferred.append(pid)
                seen.add(pid)
        if (
            self.launcher_pid is not None
            and self.launcher_start
            and self.launcher_pid not in seen
            and _process_has_identity(
                self.launcher_pid,
                self.launcher_start,
            )
            is True
        ):
            preferred.append(self.launcher_pid)
            seen.add(self.launcher_pid)
        for pid, start in self.monitor_roots():
            if pid in seen:
                continue
            if _process_has_identity(pid, start) is True:
                preferred.append(pid)
                seen.add(pid)

        known = set(self.known_identities)
        for root in preferred:
            terminate_process_tree(
                root,
                grace_seconds=grace_seconds,
                kill_seconds=kill_seconds,
                initial_signal=initial_signal,
                known_pids=known,
            )
        self.seed_live_members()
        return self.exited() is True


def guarded_writer_tree_from_lease(
    lease: Any | None,
    *,
    launcher_pid: int | None = None,
    launcher_start: str | None = None,
    known_pids: set[int] | None = None,
) -> GuardedWriterTree:
    return GuardedWriterTree.bind(
        lease,
        launcher_pid=launcher_pid,
        launcher_start=launcher_start,
        known_pids=known_pids,
    )


def _process_start_identity(pid: int) -> str | None:
    if os.name == "nt":
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", wintypes.DWORD),
                ("high", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        get_times.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = FileTime()
            exited = FileTime()
            kernel = FileTime()
            user = FileTime()
            if not get_times(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return str((int(created.high) << 32) | int(created.low))
        finally:
            close_handle(handle)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except OSError:
        return None
    return fields[21] if len(fields) > 21 else None


def _windows_process_executable(pid: int) -> Path | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    query_name = kernel32.QueryFullProcessImageNameW
    query_name.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query_name.restype = wintypes.BOOL
    handle = open_process(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not query_name(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            return None
        return Path(buffer.value)
    finally:
        close_handle(handle)


def _process_exists(pid: int) -> bool:
    if sys.platform.startswith("linux"):
        return Path(f"/proc/{pid}").exists()
    if _platform_is_windows():
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x00100000, False, pid)
        if handle:
            close_handle(handle)
            return True
        return ctypes.get_last_error() != 87
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_has_identity(
    pid: int,
    expected: str,
) -> bool | None:
    if not expected:
        return False
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as fh:
                stat_text = fh.read()
            state = stat_text.rsplit(") ", 1)[1].split()[0]
            if state == "Z":
                return False
            fields = stat_text.split()
            current = fields[21] if len(fields) > 21 else None
        except (OSError, IndexError, ValueError, TypeError):
            return None if _process_exists(pid) else False
        if current is None:
            return None if _process_exists(pid) else False
        return current == expected
    current = _process_start_identity(pid)
    if current is None:
        return None if _process_exists(pid) else False
    return current == expected


def _owner_is_live(payload: Mapping[str, Any]) -> bool | None:
    try:
        owner_pid = int(payload["owner_pid"])
        expected = str(payload["owner_start"])
    except (KeyError, TypeError, ValueError):
        return None
    if owner_pid <= 1 or not expected:
        return None
    return _process_has_identity(owner_pid, expected)


def _verified_guardian(
    payload: Mapping[str, Any],
    guardian: Path,
) -> tuple[int, str] | None:
    try:
        pid = int(payload["sentinel_pid"])
        expected_start = str(payload["sentinel_start"])
        expected_python = Path(str(payload["python"])).resolve()
        recorded_guardian = Path(str(payload["guardian"])).resolve()
    except (KeyError, OSError, TypeError, ValueError):
        return None
    if pid <= 1 or recorded_guardian != guardian:
        return None
    current_matches = _process_has_identity(
        pid,
        expected_start,
    )
    if current_matches is not True:
        return None
    if sys.platform.startswith("linux"):
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            executable = Path(f"/proc/{pid}/exe").resolve()
        except OSError:
            return None
        if os.fsencode(str(guardian)) not in command or executable != expected_python:
            return None
    elif os.name == "nt":
        executable = _windows_process_executable(pid)
        if executable is None or os.path.normcase(
            str(executable.resolve())
        ) != os.path.normcase(str(expected_python)):
            return None
    else:
        return None
    return pid, expected_start


def _terminate_windows_job(job_name: str) -> bool:
    if os.name != "nt" or not job_name.startswith("Local\\ProximaActivity-"):
        return False
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_job = kernel32.OpenJobObjectW
    open_job.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    open_job.restype = wintypes.HANDLE
    terminate_job = kernel32.TerminateJobObject
    terminate_job.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate_job.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_job(0x0008, False, job_name)
    if not handle:
        return False
    try:
        return bool(terminate_job(ctypes.c_void_p(handle), 143))
    finally:
        close_handle(handle)


class ContainerActivityLease:
    def __init__(
        self,
        state: _ContainerActivityState,
        *,
        shared: bool,
        fd: int | None,
        lock_path: Path | None = None,
        guardian_record: Path | None = None,
        guardian_id: str | None = None,
    ) -> None:
        self._state = state
        self._shared = shared
        self._fd = fd
        self._lock_path = Path(lock_path) if lock_path is not None else None
        self._guardian_record = guardian_record
        self._guardian_id = guardian_id
        self._released = False
        self._transferred = False

    def guard_process(
        self,
        command: list[str],
    ) -> tuple[list[str], dict[str, Any]]:
        if self._fd is None or self._lock_path is None:
            raise ContainerBoundaryError(
                "durable process activity requires a file-backed database"
            )
        if not sys.platform.startswith("linux") and os.name != "nt":
            raise ContainerBoundaryError(
                "this platform cannot prove complete Project process-tree exit"
            )
        package_root = Path(__file__).resolve().parent
        guardian = package_root / "activity_guardian.py"
        try:
            guardian_stat = guardian.lstat()
        except OSError as exc:
            raise ContainerBoundaryError(
                "trusted activity guardian is unavailable"
            ) from exc
        if (
            guardian.is_symlink()
            or not stat.S_ISREG(guardian_stat.st_mode)
            or guardian.resolve().parent != package_root
        ):
            raise ContainerBoundaryError("trusted activity guardian is unsafe")
        owner_start = _process_start_identity(os.getpid())
        if owner_start is None or self._guardian_id is None:
            raise ContainerBoundaryError(
                "Project process owner identity is unavailable"
            )
        # Path mode lets a broker/supervisor that cannot inherit our FD
        # (preview output broker) re-open the lock. Direct spawners still pass
        # the live FD/handle so the shared flock stays continuous across parent
        # exit - path re-open alone races if the owner dies before adopt.
        inherited = f"path:{self._lock_path}"
        options: dict[str, Any] = {}
        if os.name == "nt":
            import msvcrt

            handle = msvcrt.get_osfhandle(self._fd)
            os.set_handle_inheritable(handle, True)
            options = {"close_fds": False}
        else:
            os.set_inheritable(self._fd, True)
            options = {"pass_fds": (self._fd,)}
        guarded = [
            sys.executable,
            "-I",
            "-S",
            str(guardian),
            inherited,
            str(self._guardian_record or ""),
            self._guardian_id,
            str(os.getpid()),
            owner_start,
            "--",
            *command,
        ]
        return guarded, options

    def mark_process_started(self) -> None:
        if self._fd is not None:
            if os.name == "nt":
                import msvcrt

                os.set_handle_inheritable(
                    msvcrt.get_osfhandle(self._fd),
                    False,
                )
            else:
                os.set_inheritable(self._fd, False)
            self._transferred = True

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            if self._fd is not None:
                if self._transferred:
                    os.close(self._fd)
                else:
                    _release_file_lock(self._fd)
        finally:
            self._state.release(shared=self._shared)


def retain_activity_lease(
    lease: ContainerActivityLease,
    *,
    pid: int | None = None,
    start_identity: str | None = None,
    tree: GuardedWriterTree | None = None,
    known_pids: set[int] | None = None,
) -> None:
    """Retain a writer-activity lease until the guarded writer tree exits.

    Ingress/maintenance leases are not handled here. Launcher death alone never
    releases the lease when a guardian sentinel or other bound tree member is
    still live. When no tree identity can be bound, the lease stays retained
    fail-closed so exclusive quiescence remains blocked instead of leaking
    without a holder or falsely reporting an idle Container.
    """
    if lease is None or getattr(lease, "_released", False):
        return
    if getattr(lease, "_retain_monitor_armed", False):
        try:
            lease._retained_for_writer_tree = True
        except Exception:
            pass
        return

    with _RETAINED_ACTIVITY_GUARD:
        if lease not in _RETAINED_ACTIVITY_LEASES:
            _RETAINED_ACTIVITY_LEASES.append(lease)
    try:
        lease._retained_for_writer_tree = True
    except Exception:
        pass

    handle = tree or GuardedWriterTree.bind(
        lease,
        launcher_pid=pid,
        launcher_start=start_identity,
        known_pids=known_pids,
    )
    if not handle.has_binding():
        return

    handle.seed_live_members()
    if handle.exited() is True:
        try:
            lease.release()
        finally:
            with _RETAINED_ACTIVITY_GUARD:
                try:
                    _RETAINED_ACTIVITY_LEASES.remove(lease)
                except ValueError:
                    pass
        return

    try:
        lease._retain_monitor_armed = True
    except Exception:
        pass

    def monitor() -> None:
        release = False
        try:
            while True:
                state = handle.exited()
                if state is True:
                    release = True
                    break
                if state is False:
                    time.sleep(0.05)
                    continue
                # Proof unavailable - keep the explicit blocker indefinitely.
                time.sleep(0.25)
        finally:
            if not release:
                return
            try:
                lease.release()
            finally:
                with _RETAINED_ACTIVITY_GUARD:
                    try:
                        _RETAINED_ACTIVITY_LEASES.remove(lease)
                    except ValueError:
                        pass

    label_pid = handle.launcher_pid or 0
    if not label_pid:
        roots = handle.monitor_roots()
        if roots:
            label_pid = roots[0][0]
    threading.Thread(
        target=monitor,
        name=f"container-activity-lease-{label_pid or 'tree'}",
        daemon=True,
    ).start()


def acquire_container_activity_lease(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    shared: bool = True,
    timeout: float | None = None,
) -> ContainerActivityLease:
    data, key, lock_dir = _lock_key(conn, container)
    with _LOCKS_GUARD:
        state = _ACTIVITY_STATES.setdefault(
            key,
            _ContainerActivityState(),
        )
    state.acquire(shared=shared, timeout=timeout)
    lock_path = (
        lock_dir / f"{key.rsplit(':', 1)[-1]}.activity.lock"
        if lock_dir is not None
        else None
    )
    try:
        fd = (
            _acquire_file_lock(
                lock_path,
                shared=shared,
                timeout=timeout,
            )
            if lock_path is not None
            else None
        )
    except Exception:
        state.release(shared=shared)
        raise
    guardian_id = secrets.token_hex(16) if shared else None
    guardian_record = (
        lock_dir / (f"{int(data['id'])}.activity.{guardian_id}.guardian.json")
        if shared and lock_dir is not None
        else None
    )
    return ContainerActivityLease(
        state,
        shared=shared,
        fd=fd,
        lock_path=lock_path,
        guardian_record=guardian_record,
        guardian_id=guardian_id,
    )


def _guardian_records_present(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> bool:
    """True when any durable guardian record remains for the Container.

    Live sentinels hold the shared activity flock. After exclusive quiescence
    acquisition succeeds, a leftover record is therefore stale or unverifiable
    and must keep exclusive work blocked (flock alone is not proof).
    """
    data, _, lock_dir = _lock_key(conn, container)
    if lock_dir is None or not lock_dir.is_dir() or lock_dir.is_symlink():
        return False
    pattern = f"{int(data['id'])}.activity.*.guardian.json"
    try:
        for record in lock_dir.glob(pattern):
            try:
                record_stat = record.lstat()
            except OSError:
                return True
            if stat.S_ISLNK(record_stat.st_mode):
                return True
            if stat.S_ISREG(record_stat.st_mode):
                return True
            return True
    except OSError:
        return True
    return False


def _observation_root_pids(tree: GuardedWriterTree) -> set[int]:
    """Launcher/sentinel roots that do not prove writer-descendant capture."""
    roots: set[int] = set()
    if tree.launcher_pid is not None:
        try:
            roots.add(int(tree.launcher_pid))
        except (TypeError, ValueError):
            pass
    sentinel = tree._sentinel_identity()
    if sentinel is not None:
        roots.add(int(sentinel[0]))
    payload = tree._read_record()
    if payload is not None:
        for key in ("sentinel_pid", "launcher_pid"):
            try:
                roots.add(int(payload[key]))
            except (KeyError, TypeError, ValueError):
                continue
    return roots


def _has_identity_proven_descendants(tree: GuardedWriterTree) -> bool:
    """True when known identities include a member beyond launcher/sentinel."""
    roots = _observation_root_pids(tree)
    return any(int(pid) not in roots for pid in tree.known_identities)


def _reconcile_recovered_guardian_record(tree: GuardedWriterTree) -> bool:
    """Clear a recovered guardian record only after writer-tree proof.

    Returns True when the durable record is gone. Clean sentinel teardown
    removes the record itself. Recovery may unlink only after every
    identity-bound writer descendant observed while the tree was live is
    proven exited. Sentinel-only or incomplete observation never clears the
    record.
    """
    record = tree.guardian_record
    if record is None:
        return True
    try:
        if not record.exists():
            return True
    except OSError:
        return False

    tree.seed_live_members()
    if not tree.known_identities:
        return False

    for pid, start in list(tree.known_identities.items()):
        if _process_has_identity(pid, start) is not False:
            return False

    # Without complete descendant observation (or Windows job authority),
    # identity is incomplete - retain the durable blocker. Sentinel/launcher
    # death alone is never enough.
    job_authoritative = bool(tree.job_name) and _platform_is_windows()
    if not job_authoritative:
        if not tree.members_observed:
            return False
        if not _has_identity_proven_descendants(tree):
            return False

    try:
        record_stat = record.lstat()
        if stat.S_ISLNK(record_stat.st_mode) or not stat.S_ISREG(record_stat.st_mode):
            return False
        record.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def container_quiescence_lock(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Iterator[None]:
    lease = acquire_container_activity_lease(
        conn,
        container,
        shared=False,
        timeout=QUIESCENCE_TIMEOUT_SECONDS,
    )
    try:
        # Exclusive flock is necessary but not sufficient: a dead sentinel
        # releases the activity lock while leaving a durable record and
        # possibly live reparented writers. Fail closed on any leftover record.
        if _guardian_records_present(conn, container):
            raise ContainerBoundaryError(
                "Project process ownership could not be verified; "
                "exclusive work is blocked until process identity is reconciled"
            )
        yield
    finally:
        lease.release()


@contextmanager
def container_mutation_lock(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Iterator[None]:
    data, key, lock_dir = _lock_key(conn, container)
    lock_path = lock_dir / f"{int(data['id'])}.lock" if lock_dir is not None else None
    with _LOCKS_GUARD:
        local_lock = _MUTATION_LOCKS.setdefault(
            key,
            threading.RLock(),
        )
    with local_lock:
        depths = getattr(_MUTATION_LOCK_DEPTH, "values", None)
        if depths is None:
            depths = {}
            _MUTATION_LOCK_DEPTH.values = depths
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        fd = _acquire_file_lock(lock_path) if lock_path is not None else None
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
            if fd is not None:
                _release_file_lock(fd)


def recover_container_activity_guardians(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    timeout: float = QUIESCENCE_TIMEOUT_SECONDS,
) -> ContainerActivityRecovery:
    """Reconcile durable guardian records for one Container.

    Every unverifiable, malformed, or stale record stays unresolved. Dead
    launcher/sentinel pids never imply a clear Container - only a verified live
    owner or exact recovered exit clears the blocker. Callers that mutate under
    exclusive quiescence must still require ``active == 0`` and
    ``unresolved == 0``; flock acquisition alone is not proof.
    """
    data, _, lock_dir = _lock_key(conn, container)
    if lock_dir is None or not lock_dir.is_dir() or lock_dir.is_symlink():
        return ContainerActivityRecovery()
    guardian = Path(__file__).resolve().with_name("activity_guardian.py")
    recovered = 0
    waiting: list[tuple[int, str, GuardedWriterTree]] = []
    pattern = f"{int(data['id'])}.activity.*.guardian.json"
    container_id = int(data["id"])
    for record in sorted(lock_dir.glob(pattern)):
        try:
            record_prefix = f"{container_id}.activity."
            record_suffix = ".guardian.json"
            guardian_id = record.name[len(record_prefix) : -len(record_suffix)]
            if len(guardian_id) != 32 or any(
                character not in "0123456789abcdef" for character in guardian_id
            ):
                continue
            record_stat = record.lstat()
            if stat.S_ISLNK(record_stat.st_mode) or not stat.S_ISREG(
                record_stat.st_mode
            ):
                continue
            payload = json.loads(record.read_text(encoding="utf-8"))
            if (
                _platform_is_windows()
                and payload.get("job_name") != f"Local\\ProximaActivity-{guardian_id}"
            ):
                continue
            verified = _verified_guardian(payload, guardian)
            if verified is None:
                # Unverifiable / stale / dead-sentinel records are counted in
                # the authoritative final scan below - never inferred clear.
                continue
            owner_live = _owner_is_live(payload)
            if owner_live is not False:
                continue
            pid, start = verified
            job_name = str(payload.get("job_name") or "") or None
            tree = GuardedWriterTree.bind(
                guardian_record=record,
                job_name=job_name,
            )
            tree.known_identities[int(pid)] = str(start)
            # Seed writers while the sentinel is still live so later unclean
            # sentinel death cannot drop identity-bound descendants.
            tree.seed_live_members()
            if _platform_is_windows():
                if not job_name or not _terminate_windows_job(job_name):
                    continue
            else:
                # Incomplete / sentinel-only observation must not signal or
                # clear - retain the durable blocker for owner intervention.
                if not tree.members_observed or not _has_identity_proven_descendants(
                    tree
                ):
                    continue
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            waiting.append((pid, start, tree))
            recovered += 1
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            continue
    deadline = time.monotonic() + timeout
    while waiting and time.monotonic() < deadline:
        next_waiting: list[tuple[int, str, GuardedWriterTree]] = []
        for pid, start, tree in waiting:
            if _reconcile_recovered_guardian_record(tree):
                continue
            alive = _process_has_identity(pid, start)
            if alive is False:
                # Sentinel is gone but the record remains: signal any still-live
                # identity-bound members. Never unlink from sentinel death alone.
                tree.seed_live_members()
                live_members = [
                    member_pid
                    for member_pid, member_start in tree.known_identities.items()
                    if _process_has_identity(member_pid, member_start) is True
                ]
                if live_members:
                    tree.terminate(
                        grace_seconds=0.2,
                        kill_seconds=0.2,
                    )
            next_waiting.append((pid, start, tree))
        waiting = next_waiting
        if waiting:
            time.sleep(0.02)
    for pid, start, tree in waiting:
        # Timed out while identity still unproven - leave the record and count
        # it unresolved in the final scan. Never clear on timeout alone.
        del pid, start, tree

    # Authoritative recount: any remaining record without a verified live owner
    # preserves the explicit active-process / ownership blocker.
    active = 0
    unresolved = 0
    for record in sorted(lock_dir.glob(pattern)):
        try:
            record_prefix = f"{container_id}.activity."
            record_suffix = ".guardian.json"
            guardian_id = record.name[len(record_prefix) : -len(record_suffix)]
            if len(guardian_id) != 32 or any(
                character not in "0123456789abcdef" for character in guardian_id
            ):
                unresolved += 1
                continue
            record_stat = record.lstat()
            if stat.S_ISLNK(record_stat.st_mode) or not stat.S_ISREG(
                record_stat.st_mode
            ):
                unresolved += 1
                continue
            payload = json.loads(record.read_text(encoding="utf-8"))
            if (
                _platform_is_windows()
                and payload.get("job_name") != f"Local\\ProximaActivity-{guardian_id}"
            ):
                unresolved += 1
                continue
            verified = _verified_guardian(payload, guardian)
            if verified is None:
                unresolved += 1
                continue
            owner_live = _owner_is_live(payload)
            if owner_live is True:
                active += 1
                continue
            unresolved += 1
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            unresolved += 1
    return ContainerActivityRecovery(
        active=active,
        recovered=recovered,
        unresolved=unresolved,
    )

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
            raise ContainerBoundaryError(
                f"Container {container} does not exist"
            )
        return dict(row)
    data = dict(container)
    if "id" not in data or "path" not in data:
        raise ContainerBoundaryError(
            "Container row must include id and path"
        )
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
        raise ContainerBoundaryError(
            "Container mutation lock directory is unsafe"
        )
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
            deadline = (
                None if timeout is None else time.monotonic() + timeout
            )

            def wait() -> None:
                remaining = (
                    None
                    if deadline is None
                    else max(0.0, deadline - time.monotonic())
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
            return str(
                (int(created.high) << 32) | int(created.low)
            )
        finally:
            close_handle(handle)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(
            encoding="utf-8"
        ).split()
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
            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(
                b"\0"
            )
            executable = Path(f"/proc/{pid}/exe").resolve()
        except OSError:
            return None
        if (
            os.fsencode(str(guardian)) not in command
            or executable != expected_python
        ):
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
    if os.name != "nt" or not job_name.startswith(
        "Local\\ProximaActivity-"
    ):
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
        return bool(
            terminate_job(ctypes.c_void_p(handle), 143)
        )
    finally:
        close_handle(handle)


class ContainerActivityLease:
    def __init__(
        self,
        state: _ContainerActivityState,
        *,
        shared: bool,
        fd: int | None,
        guardian_record: Path | None = None,
        guardian_id: str | None = None,
    ) -> None:
        self._state = state
        self._shared = shared
        self._fd = fd
        self._guardian_record = guardian_record
        self._guardian_id = guardian_id
        self._released = False
        self._transferred = False

    def guard_process(
        self,
        command: list[str],
    ) -> tuple[list[str], dict[str, Any]]:
        if self._fd is None:
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
            raise ContainerBoundaryError(
                "trusted activity guardian is unsafe"
            )
        owner_start = _process_start_identity(os.getpid())
        if owner_start is None or self._guardian_id is None:
            raise ContainerBoundaryError(
                "Project process owner identity is unavailable"
            )
        inherited = str(self._fd)
        if os.name == "nt":
            import msvcrt

            handle = msvcrt.get_osfhandle(self._fd)
            os.set_handle_inheritable(handle, True)
            inherited = f"handle:{handle}"
        else:
            os.set_inheritable(self._fd, True)
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
        if os.name == "nt":
            return guarded, {"close_fds": False}
        return guarded, {"pass_fds": (self._fd,)}

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
    try:
        fd = (
            _acquire_file_lock(
                lock_dir / f"{key.rsplit(':', 1)[-1]}.activity.lock",
                shared=shared,
                timeout=timeout,
            )
            if lock_dir is not None
            else None
        )
    except Exception:
        state.release(shared=shared)
        raise
    guardian_id = secrets.token_hex(16) if shared else None
    guardian_record = (
        lock_dir
        / (
            f"{int(data['id'])}.activity."
            f"{guardian_id}.guardian.json"
        )
        if shared and lock_dir is not None
        else None
    )
    return ContainerActivityLease(
        state,
        shared=shared,
        fd=fd,
        guardian_record=guardian_record,
        guardian_id=guardian_id,
    )


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
        yield
    finally:
        lease.release()


@contextmanager
def container_mutation_lock(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Iterator[None]:
    data, key, lock_dir = _lock_key(conn, container)
    lock_path = (
        lock_dir / f"{int(data['id'])}.lock"
        if lock_dir is not None
        else None
    )
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
        fd = (
            _acquire_file_lock(lock_path)
            if lock_path is not None
            else None
        )
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
    data, _, lock_dir = _lock_key(conn, container)
    if (
        lock_dir is None
        or not lock_dir.is_dir()
        or lock_dir.is_symlink()
    ):
        return ContainerActivityRecovery()
    guardian = Path(__file__).resolve().with_name(
        "activity_guardian.py"
    )
    active = 0
    recovered = 0
    unresolved = 0
    waiting: list[tuple[int, str]] = []
    pattern = f"{int(data['id'])}.activity.*.guardian.json"
    for record in sorted(lock_dir.glob(pattern)):
        try:
            record_prefix = f"{int(data['id'])}.activity."
            record_suffix = ".guardian.json"
            guardian_id = record.name[
                len(record_prefix) : -len(record_suffix)
            ]
            if (
                len(guardian_id) != 32
                or any(
                    character not in "0123456789abcdef"
                    for character in guardian_id
                )
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
                and payload.get("job_name")
                != f"Local\\ProximaActivity-{guardian_id}"
            ):
                unresolved += 1
                continue
            verified = _verified_guardian(payload, guardian)
            if verified is None:
                try:
                    unverified_pid = int(payload["sentinel_pid"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    unverified_pid > 1
                    and _process_exists(unverified_pid)
                ):
                    unresolved += 1
                continue
            owner_live = _owner_is_live(payload)
            if owner_live is True:
                active += 1
                continue
            if owner_live is None:
                unresolved += 1
                continue
            pid, start = verified
            if _platform_is_windows():
                job_name = str(payload.get("job_name") or "")
                if not _terminate_windows_job(job_name):
                    unresolved += 1
                    continue
            else:
                os.kill(pid, signal.SIGTERM)
            waiting.append((pid, start))
            recovered += 1
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
        ):
            unresolved += 1
    deadline = time.monotonic() + timeout
    while waiting and time.monotonic() < deadline:
        waiting = [
            item
            for item in waiting
            if _process_has_identity(item[0], item[1])
            is not False
        ]
        if waiting:
            time.sleep(0.02)
    unresolved += len(waiting)
    return ContainerActivityRecovery(
        active=active,
        recovered=recovered,
        unresolved=unresolved,
    )

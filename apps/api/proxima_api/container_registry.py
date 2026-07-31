"""Container filesystem boundaries, Ops migration, and registry projection.

The existing ``projects`` and ``project_areas`` tables remain the persistence
backbone. This module is the one place that turns those rows into physical
Container and Area roots.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import stat
import sys
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from .auth import iso_now
from .directory_handles import directory_backend

logger = logging.getLogger("proxima.container_registry")
_directory_backend = directory_backend()

OPS_RELPATH = "ops"
CONTAINER_DOC = "container.md"
OPS_MIGRATION_VERSION = 5
KNOWN_OPS_DIRS = (
    "wiki",
    "artifacts",
    "reports",
    "exports",
    "scripts",
    "tasks",
    "uploads",
)
KNOWN_OPS_FILES = (CONTAINER_DOC, "design.md")
OPS_VIRTUAL_NAMES = frozenset((*KNOWN_OPS_DIRS, *KNOWN_OPS_FILES))
DEFAULT_STARTER_DIRS = ("wiki", "tasks", "artifacts")
MAX_CONTAINER_DOC_BYTES = 64 * 1024
MAX_IDENTITY_LABEL_CHARS = 120
MAX_SUMMARY_CHARS = 500
RECOVERY_TEMP_PREFIX = ".proxima-ops-migration-container-"

_CONTAINER_LOCKS_GUARD = threading.Lock()
_CONTAINER_LOCKS: dict[str, Any] = {}
_CONTAINER_LOCK_DEPTH = threading.local()
_CONTAINER_ACTIVITY_STATES: dict[str, "_ContainerActivityState"] = {}


class ContainerBoundaryError(ValueError):
    """A Container or Area root is missing, ambiguous, or unsafe."""


class OpsMigrationCollision(ContainerBoundaryError):
    """A legacy Ops layout cannot be moved without owner intervention."""


def _platform_is_windows() -> bool:
    return os.name == "nt"


def _database_path(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        raw = row["file"] if isinstance(row, sqlite3.Row) else row[2]
        if name == "main" and raw:
            return Path(str(raw)).resolve()
    return None


def _acquire_file_lock(path: Path, *, shared: bool = False) -> int:
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
            _windows_lock_file(
                msvcrt.get_osfhandle(fd),
                shared=shared,
            )
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
    except Exception:
        os.close(fd)
        raise
    return fd


class _ContainerActivityState:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.readers = 0
        self.writer = False

    def acquire(self, *, shared: bool) -> None:
        with self.condition:
            if shared:
                while self.writer:
                    self.condition.wait()
                self.readers += 1
                return
            while self.writer or self.readers:
                self.condition.wait()
            self.writer = True

    def release(self, *, shared: bool) -> None:
        with self.condition:
            if shared:
                self.readers -= 1
            else:
                self.writer = False
            self.condition.notify_all()


class ContainerActivityLease:
    def __init__(
        self,
        state: _ContainerActivityState,
        *,
        shared: bool,
        fd: int | None,
    ) -> None:
        self._state = state
        self._shared = shared
        self._fd = fd
        self._released = False
        self._transferred = False

    def guard_process(
        self,
        command: list[str],
    ) -> tuple[list[str], dict[str, Any]]:
        if self._fd is None:
            return command, {}
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
            "-m",
            "proxima_api.activity_guardian",
            inherited,
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


def _container_lock_key(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> tuple[dict[str, Any], str, Path | None]:
    data = get_container(conn, container)
    database = _database_path(conn)
    if database is None:
        return data, f"memory:{data['id']}:{data['path']}", None
    key = f"{database}:{data['id']}"
    lock_dir = database.parent / f".{database.name}.container-locks"
    return data, key, lock_dir


def acquire_container_activity_lease(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    shared: bool = True,
) -> ContainerActivityLease:
    _, key, lock_dir = _container_lock_key(conn, container)
    with _CONTAINER_LOCKS_GUARD:
        state = _CONTAINER_ACTIVITY_STATES.setdefault(
            key,
            _ContainerActivityState(),
        )
    state.acquire(shared=shared)
    try:
        fd = (
            _acquire_file_lock(
                lock_dir / f"{key.rsplit(':', 1)[-1]}.activity.lock",
                shared=shared,
            )
            if lock_dir is not None
            else None
        )
    except Exception:
        state.release(shared=shared)
        raise
    return ContainerActivityLease(state, shared=shared, fd=fd)


@contextmanager
def container_quiescence_lock(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Iterator[None]:
    lease = acquire_container_activity_lease(conn, container, shared=False)
    try:
        yield
    finally:
        lease.release()


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


def _windows_lock_file(handle: int, *, shared: bool) -> None:
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


@contextmanager
def container_mutation_lock(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Iterator[None]:
    data, key, lock_dir = _container_lock_key(conn, container)
    lock_path = lock_dir / f"{int(data['id'])}.lock" if lock_dir is not None else None
    with _CONTAINER_LOCKS_GUARD:
        local_lock = _CONTAINER_LOCKS.setdefault(key, threading.RLock())
    with local_lock:
        depths = getattr(_CONTAINER_LOCK_DEPTH, "values", None)
        if depths is None:
            depths = {}
            _CONTAINER_LOCK_DEPTH.values = depths
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


def _as_dict(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def get_container(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(container, int):
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (container,)).fetchone()
        if row is None:
            raise ContainerBoundaryError(f"Container {container} does not exist")
        return dict(row)
    data = _as_dict(container)
    if "id" not in data or "path" not in data:
        raise ContainerBoundaryError("Container row must include id and path")
    return data


def container_root(container: sqlite3.Row | Mapping[str, Any]) -> Path:
    data = _as_dict(container)
    raw = str(data.get("path") or "").strip()
    if not raw:
        raise ContainerBoundaryError("Container path is unavailable")
    root = Path(raw)
    try:
        if root.is_symlink():
            raise ContainerBoundaryError("Container root cannot be a symlink")
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise ContainerBoundaryError("Container root is not reachable") from exc
    if not resolved.is_dir():
        raise ContainerBoundaryError("Container root is missing")
    expected_identity = str(data.get("path_identity") or "").strip()
    if not expected_identity:
        raise ContainerBoundaryError("Container root identity is unavailable")
    try:
        verification_root = (
            root.absolute()
            if _directory_backend.platform == "windows"
            else resolved
        )
        handle = _directory_backend.open_absolute(verification_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerBoundaryError(
            "Container root identity cannot be verified"
        ) from exc
    try:
        if handle.identity != expected_identity:
            raise ContainerBoundaryError("Container root identity changed")
    finally:
        _directory_backend.close(handle)
    return resolved


def _safe_rel_path(raw: str) -> PurePosixPath:
    text = str(raw or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or any(part in ("", "..") for part in path.parts)
        or (text != "." and any(part == "." for part in path.parts))
    ):
        raise ContainerBoundaryError(f"invalid Area path: {raw!r}")
    return path


def _contains(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _reject_symlinks(root: Path, *, deep: bool = True) -> None:
    if root.is_symlink():
        raise ContainerBoundaryError("physical Ops root cannot be a symlink")
    if not deep:
        return
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ContainerBoundaryError(
                    f"physical Ops root contains a symlink: {path.relative_to(root).as_posix()}"
                )
    except OSError as exc:
        raise ContainerBoundaryError(f"physical Ops root cannot be inspected: {exc}") from exc


def validated_area_roots(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    deep_ops_scan: bool = False,
) -> dict[int, Path]:
    """Resolve every active Area and reject unsafe or ambiguous layouts.

    ``deep_ops_scan`` controls whether the physical Ops root is walked for
    descendant symlinks. Hot read paths (project lists, Home, file resolution)
    leave it off and rely on the O(1) Ops-root symlink check plus per-access
    realpath jailing in ``fsapi``; creation, migration, and Area-mutation
    boundaries opt in to the full fail-closed recursive scan.
    """
    data = get_container(conn, container)
    root = container_root(data)
    rows = conn.execute(
        "SELECT id, kind, rel_path, source FROM project_areas "
        "WHERE project_id = ? AND source != 'excluded' ORDER BY id",
        (data["id"],),
    ).fetchall()
    ops_rows = [row for row in rows if row["kind"] == "ops"]
    if len(ops_rows) != 1:
        raise ContainerBoundaryError("Container must have exactly one active Ops Area")

    resolved: dict[int, Path] = {}
    by_root: dict[Path, sqlite3.Row] = {}
    for row in rows:
        rel = _safe_rel_path(row["rel_path"])
        candidate = root if rel.as_posix() == "." else root.joinpath(*rel.parts)
        if row["kind"] == "ops" and rel.as_posix() == OPS_RELPATH:
            _reject_symlinks(candidate, deep=deep_ops_scan)
        target = candidate.resolve()
        if not target.is_dir():
            raise ContainerBoundaryError(
                f"Area '{row['rel_path']}' is missing or is not a directory"
            )
        if not _contains(root, target):
            raise ContainerBoundaryError(
                f"Area '{row['rel_path']}' escapes its Container"
            )
        prior = by_root.get(target)
        if prior is not None:
            legacy_pair = (
                {prior["kind"], row["kind"]} == {"code", "ops"}
                and prior["rel_path"] == "."
                and row["rel_path"] == "."
            )
            if not legacy_pair:
                raise ContainerBoundaryError(
                    f"Areas '{prior['rel_path']}' and '{row['rel_path']}' resolve to the same root"
                )
        else:
            by_root[target] = row
        resolved[int(row["id"])] = target

    active = list(rows)
    for index, left in enumerate(active):
        left_root = resolved[int(left["id"])]
        for right in active[index + 1 :]:
            right_root = resolved[int(right["id"])]
            if left_root == right_root:
                continue
            if left_root not in right_root.parents and right_root not in left_root.parents:
                continue
            pair = {left["kind"], right["kind"]}
            rels = {left["rel_path"], right["rel_path"]}
            root_repo_with_ops = pair == {"code", "ops"} and "." in rels
            legacy_ops = (
                (left["kind"] == "ops" and left["rel_path"] == ".")
                or (right["kind"] == "ops" and right["rel_path"] == ".")
            )
            if root_repo_with_ops or legacy_ops:
                continue
            raise ContainerBoundaryError(
                f"Areas '{left['rel_path']}' and '{right['rel_path']}' overlap"
            )
    return resolved


def resolve_area_root(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    area_id: int,
    *,
    deep_ops_scan: bool = False,
) -> Path:
    roots = validated_area_roots(conn, container, deep_ops_scan=deep_ops_scan)
    try:
        return roots[int(area_id)]
    except KeyError as exc:
        raise ContainerBoundaryError("Area is not active in this Container") from exc


def ops_root(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    deep_ops_scan: bool = False,
) -> Path:
    """Return the canonical Ops root, including the legacy ``.`` fallback."""
    data = get_container(conn, container)
    row = conn.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (data["id"],),
    ).fetchone()
    if row is None:
        raise ContainerBoundaryError("Container has no active Ops Area")
    return resolve_area_root(conn, data, int(row["id"]), deep_ops_scan=deep_ops_scan)


def try_ops_root(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    deep_ops_scan: bool = False,
) -> Path | None:
    """Best-effort Ops root for cross-Container reads; None when unavailable.

    Returns None instead of raising when the Container is unavailable or
    boundary-invalid on this machine (missing root or Area folder, unsafe
    layout), so multi-Container list, dashboard, and history aggregations skip it
    rather than failing the whole read. Direct single-Container access keeps
    using ``ops_root`` and stays fail-closed.
    """
    try:
        return ops_root(conn, container, deep_ops_scan=deep_ops_scan)
    except ContainerBoundaryError:
        return None


def root_for_virtual_path(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    rel_path: str,
) -> Path:
    """Choose the Container or Ops root while keeping historical virtual paths."""
    data = get_container(conn, container)
    first = next(iter(PurePosixPath((rel_path or "").replace("\\", "/")).parts), "")
    if first in OPS_VIRTUAL_NAMES:
        return ops_root(conn, data)
    return container_root(data)


def _container_doc_text(name: str) -> str:
    label = (name or "Container").strip()[:120] or "Container"
    return (
        "---\n"
        "identity: General\n"
        f"summary: Work and durable context for {label}.\n"
        "---\n\n"
        f"# {label}\n"
    )


def _planned_recovery_temp(
    expected_hash: str,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    token = token or secrets.token_hex(16)
    return {
        "path": f"{RECOVERY_TEMP_PREFIX}{token}.tmp",
        "sha256": expected_hash,
        "phase": "planned",
        "identity": None,
        "ownership_token": secrets.token_hex(32),
    }


def _directory_open_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise OpsMigrationCollision(
            "this platform cannot guarantee stable no-follow directory access"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _directory_fd_path(fd: int) -> Path:
    for prefix in ("/proc/self/fd", "/dev/fd"):
        candidate = Path(prefix) / str(fd)
        if candidate.exists():
            return candidate
    raise OpsMigrationCollision(
        "this platform cannot address an opened migration directory"
    )


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _stat_identity(value: os.stat_result) -> dict[str, int]:
    return {
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
    }


def _valid_identity(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("device"), int)
        and isinstance(value.get("inode"), int)
    )


def _identity_matches(value: Any, current: os.stat_result) -> bool:
    return (
        _valid_identity(value)
        and int(value["device"]) == int(current.st_dev)
        and int(value["inode"]) == int(current.st_ino)
    )


def _windows_directory_identity(
    handle: int,
    display: str,
) -> tuple[int, int]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time_low", wintypes.DWORD),
            ("creation_time_high", wintypes.DWORD),
            ("access_time_low", wintypes.DWORD),
            ("access_time_high", wintypes.DWORD),
            ("write_time_low", wintypes.DWORD),
            ("write_time_high", wintypes.DWORD),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    information = ByHandleFileInformation()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    if not get_information(ctypes.c_void_p(handle), ctypes.byref(information)):
        raise OSError(
            ctypes.get_last_error(),
            f"cannot inspect directory: {display}",
        )
    if not information.attributes & 0x00000010 or information.attributes & 0x00000400:
        raise ContainerBoundaryError(
            f"directory is missing or is a reparse point: {display}"
        )
    return (
        int(information.volume_serial),
        (int(information.file_index_high) << 32) | int(information.file_index_low),
    )


def _windows_open_directory(path: Path) -> tuple[int, tuple[int, int]]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x0080,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise OSError(ctypes.get_last_error(), f"cannot open directory: {path}")
    try:
        identity = _windows_directory_identity(int(handle), str(path))
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    return int(handle), identity


def _windows_create_directory_at(
    parent_handle: int,
    name: str,
) -> tuple[int, tuple[int, int]]:
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_qos", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status", wintypes.LPVOID),
            ("information", ctypes.c_size_t),
        ]

    buffer = ctypes.create_unicode_buffer(name)
    encoded = name.encode("utf-16-le")
    unicode_name = UnicodeString(
        len(encoded),
        len(encoded) + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    status_block = IoStatusBlock()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_file = ntdll.NtCreateFile
    create_file.restype = ctypes.c_long
    status = create_file(
        ctypes.byref(handle),
        0x00100000 | 0x00000001 | 0x00000002 | 0x00000004 | 0x00000080,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000010,
        0x00000001 | 0x00000002 | 0x00000004,
        3,
        0x00000001 | 0x00000020 | 0x00200000,
        None,
        0,
    )
    if status < 0:
        raise OSError(int(status), f"cannot create directory: {name}")
    value = int(handle.value)
    try:
        identity = _windows_directory_identity(value, name)
    except Exception:
        _windows_close_handle(value)
        raise
    return value, identity


def _windows_close_handle(handle: int) -> None:
    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(ctypes.c_void_p(handle))


def _windows_create_file_at(
    parent_handle: int,
    name: str,
    content: bytes,
) -> None:
    from ctypes import wintypes
    import msvcrt

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_qos", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status", wintypes.LPVOID),
            ("information", ctypes.c_size_t),
        ]

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(name.encode("utf-16-le")),
        len(name.encode("utf-16-le")) + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    status_block = IoStatusBlock()
    handle = wintypes.HANDLE()
    status = ntdll.NtCreateFile(
        ctypes.byref(handle),
        0x40000000 | 0x00100000 | 0x00000080,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000080,
        0x00000001 | 0x00000002 | 0x00000004,
        2,
        0x00000020 | 0x00000040 | 0x00200000,
        None,
        0,
    )
    if status < 0:
        raise FileExistsError(name)
    fd = msvcrt.open_osfhandle(int(handle.value), os.O_WRONLY)
    try:
        _write_all(fd, content)
    finally:
        os.close(fd)


def _create_physical_ops_root_windows(
    container: Path,
    name: str,
    starter_dirs: tuple[str, ...],
) -> Path:
    physical = container / OPS_RELPATH
    physical.mkdir(mode=0o700, exist_ok=True)
    root_handle, root_identity = _windows_open_directory(container)
    physical_handle: int | None = None
    try:
        physical_handle, physical_identity = _windows_open_directory(physical)
        for dirname in starter_dirs:
            rel = _safe_rel_path(dirname)
            if not rel.parts:
                raise ContainerBoundaryError(
                    f"Ops starter path is unsafe: {dirname!r}"
                )
            parent_handle = physical_handle
            opened: list[int] = []
            try:
                for part in rel.parts:
                    child_handle, _ = _windows_create_directory_at(
                        parent_handle,
                        part,
                    )
                    opened.append(child_handle)
                    parent_handle = child_handle
            finally:
                for child_handle in reversed(opened):
                    _windows_close_handle(child_handle)
            check_handle, check_identity = _windows_open_directory(physical)
            _windows_close_handle(check_handle)
            if check_identity != physical_identity:
                raise ContainerBoundaryError(
                    "physical Ops root changed during creation"
                )
        document = _container_doc_text(name).encode("utf-8")
        try:
            _windows_create_file_at(
                physical_handle,
                CONTAINER_DOC,
                document,
            )
        except FileExistsError:
            document_path = physical / CONTAINER_DOC
            if (
                document_path.is_symlink()
                or not document_path.is_file()
                or document_path.read_bytes() != document
            ):
                raise ContainerBoundaryError(
                    "ops/container.md exists with unexpected content or type"
                ) from None
        check_root, current_root_identity = _windows_open_directory(container)
        check_physical, current_physical_identity = _windows_open_directory(physical)
        _windows_close_handle(check_root)
        _windows_close_handle(check_physical)
        if (
            current_root_identity != root_identity
            or current_physical_identity != physical_identity
        ):
            raise ContainerBoundaryError("Container root changed during creation")
    finally:
        if physical_handle is not None:
            _windows_close_handle(physical_handle)
        _windows_close_handle(root_handle)
    _reject_symlinks(physical)
    return physical


def _revalidate_root_identity(root: Path, root_fd: int) -> None:
    current = root.lstat()
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_identity(current, os.fstat(root_fd))
    ):
        raise OpsMigrationCollision("Container root changed during migration")


def _revalidate_physical_identity(root_fd: int, physical_fd: int) -> None:
    current = os.stat(OPS_RELPATH, dir_fd=root_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or not _same_identity(current, os.fstat(physical_fd))
    ):
        raise OpsMigrationCollision("physical Ops root changed during migration")


@contextmanager
def _stable_migration_directories(
    root: Path,
) -> Iterator[tuple[int, int, Path, Path]]:
    root_fd = os.open(root, _directory_open_flags())
    physical_fd: int | None = None
    try:
        _revalidate_root_identity(root, root_fd)
        try:
            os.mkdir(OPS_RELPATH, mode=0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        except FileExistsError:
            pass
        physical_fd = os.open(
            OPS_RELPATH,
            _directory_open_flags(),
            dir_fd=root_fd,
        )
        _revalidate_root_identity(root, root_fd)
        _revalidate_physical_identity(root_fd, physical_fd)
        yield (
            root_fd,
            physical_fd,
            _directory_fd_path(root_fd),
            _directory_fd_path(physical_fd),
        )
    finally:
        if physical_fd is not None:
            os.close(physical_fd)
        os.close(root_fd)


def _path_state_at(directory_fd: int, name: str) -> str:
    try:
        mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _hash_file_at(directory_fd: int, name: str) -> str:
    digest, _ = _read_stable_file_at(
        directory_fd,
        name,
        display=name,
    )
    return digest


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise OSError("short write while creating container.md")
        offset += written
    os.fsync(fd)


def _publish_anonymous_file(
    temp_fd: int,
    parent_fd: int,
    target_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOTSUP, "anonymous file publication is unsupported")
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = getattr(libc, "linkat", None)
    if linkat is None:
        raise OSError(errno.ENOTSUP, "linkat is unavailable")
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    if linkat(temp_fd, b"", parent_fd, os.fsencode(target_name), 0x1000) == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number), target_name)


def _exact_regular_hash_at(
    parent_fd: int,
    name: str,
    expected_hash: str,
) -> bool:
    return (
        _path_state_at(parent_fd, name) == "file"
        and _hash_file_at(parent_fd, name) == expected_hash
    )


def _recovery_token_matches(fd: int, recovery: Mapping[str, Any]) -> bool:
    token = recovery.get("ownership_token")
    if not isinstance(token, str) or len(token) != 64:
        return False
    try:
        current = os.getxattr(fd, "user.proxima.ops_migration")
    except (AttributeError, OSError):
        return False
    return current == token.encode("ascii")


def _creating_recovery_matches_at(
    parent_fd: int,
    name: str,
    recovery: Mapping[str, Any],
) -> bool:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError:
        return False
    try:
        opened = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return (
            stat.S_ISREG(opened.st_mode)
            and opened.st_size == 0
            and opened.st_nlink == 1
            and _same_identity(opened, named)
            and _recovery_token_matches(fd, recovery)
        )
    except OSError:
        return False
    finally:
        os.close(fd)


def _creating_recovery_matches(
    path: Path,
    recovery: Mapping[str, Any],
) -> bool:
    try:
        parent_fd = os.open(path.parent, _directory_open_flags())
    except OSError:
        return False
    try:
        return _creating_recovery_matches_at(parent_fd, path.name, recovery)
    finally:
        os.close(parent_fd)


def _unlink_exact_file_at(
    parent_fd: int,
    name: str,
    expected_hash: str,
    expected_identity: Mapping[str, Any],
) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise OpsMigrationCollision(
                "container.md recovery file has ambiguous ownership"
            )
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            digest.hexdigest() != expected_hash
            or not _same_identity(opened, current)
            or not _identity_matches(expected_identity, opened)
        ):
            raise OpsMigrationCollision(
                "container.md recovery file has ambiguous ownership"
            )
        os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(fd)


def _atomic_write_if_missing(
    path: Path,
    text: str,
    *,
    expected_hash: str | None = None,
    recovery_temp: dict[str, Any] | None = None,
    parent_fd: int | None = None,
    persist_recovery: Callable[[], None] | None = None,
) -> None:
    content = text.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    expected_hash = expected_hash or digest
    if digest != expected_hash:
        raise OpsMigrationCollision("planned container.md content hash is invalid")
    recovery = recovery_temp or _planned_recovery_temp(expected_hash)
    if not _valid_recovery_temp(recovery, expected_hash):
        raise OpsMigrationCollision("planned container.md recovery file is invalid")
    recovery_name = recovery["path"]

    def advance(
        phase: str,
        identity: Mapping[str, Any] | None,
    ) -> None:
        recovery["phase"] = phase
        recovery["identity"] = dict(identity) if identity is not None else None
        if persist_recovery is not None:
            persist_recovery()

    owns_parent_fd = parent_fd is None
    if parent_fd is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        parent_fd = os.open(path.parent, _directory_open_flags())
    try:
        target_state = _path_state_at(parent_fd, path.name)
        recovery_state = _path_state_at(parent_fd, recovery_name)
        if target_state != "missing":
            if not _exact_regular_hash_at(parent_fd, path.name, expected_hash):
                raise OpsMigrationCollision(
                    f"{path.name} exists with unexpected content or type"
                )
            target_stat = os.stat(
                path.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if persist_recovery is None:
                return
            if recovery["phase"] not in {"ready", "published", "complete"}:
                raise OpsMigrationCollision(
                    "existing generated container.md has ambiguous ownership"
                )
            if not _identity_matches(recovery["identity"], target_stat):
                if recovery["phase"] != "ready" or recovery_state == "missing":
                    raise OpsMigrationCollision(
                        "existing generated container.md has ambiguous ownership"
                    )
                recovery_stat = os.stat(
                    recovery_name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if not _identity_matches(
                    recovery["identity"], recovery_stat
                ) or not _same_identity(recovery_stat, target_stat):
                    raise OpsMigrationCollision(
                        "existing generated container.md has ambiguous ownership"
                    )
                advance("published", _stat_identity(target_stat))
            if recovery_state != "missing":
                _unlink_exact_file_at(
                    parent_fd,
                    recovery_name,
                    expected_hash,
                    recovery["identity"],
                )
                os.fsync(parent_fd)
            advance("complete", _stat_identity(target_stat))
            return
        if recovery_state != "missing":
            if recovery["phase"] == "planned":
                raise OpsMigrationCollision(
                    "container.md recovery file has ambiguous ownership"
                )
            recovery_stat = os.stat(
                recovery_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if recovery["phase"] == "creating":
                flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                recovery_fd = os.open(
                    recovery_name,
                    flags,
                    dir_fd=parent_fd,
                )
                try:
                    opened = os.fstat(recovery_fd)
                    if (
                        not _creating_recovery_matches_at(
                            parent_fd,
                            recovery_name,
                            recovery,
                        )
                        or not _same_identity(opened, recovery_stat)
                    ):
                        raise OpsMigrationCollision(
                            "container.md recovery file has ambiguous ownership"
                        )
                    advance("created", _stat_identity(opened))
                finally:
                    os.close(recovery_fd)
            elif not _identity_matches(recovery["identity"], recovery_stat):
                raise OpsMigrationCollision(
                    "container.md recovery file has ambiguous ownership"
                )
            if recovery["phase"] == "prepared":
                if not _exact_regular_hash_at(
                    parent_fd,
                    recovery_name,
                    expected_hash,
                ):
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                os.fsync(parent_fd)
                advance("ready", recovery["identity"])
        else:
            if recovery["phase"] in {"prepared", "creating"}:
                advance("planned", None)
            elif recovery["phase"] != "planned":
                raise OpsMigrationCollision(
                    "container.md recovery file has ambiguous ownership"
                )
            anonymous_fd: int | None = None
            if hasattr(os, "O_TMPFILE"):
                try:
                    anonymous_fd = os.open(
                        ".",
                        os.O_TMPFILE
                        | os.O_WRONLY
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    _write_all(anonymous_fd, content)
                    anonymous_identity = _stat_identity(os.fstat(anonymous_fd))
                    advance("prepared", anonymous_identity)
                    _publish_anonymous_file(
                        anonymous_fd,
                        parent_fd,
                        recovery_name,
                    )
                    os.fsync(parent_fd)
                    advance("ready", anonymous_identity)
                except FileExistsError:
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                except OSError as exc:
                    if exc.errno not in {
                        errno.EINVAL,
                        errno.EISDIR,
                        errno.ENOSYS,
                        errno.ENOTSUP,
                        errno.EOPNOTSUPP,
                        errno.EPERM,
                    }:
                        raise
                    if (
                        recovery["phase"] == "prepared"
                        and _path_state_at(parent_fd, recovery_name)
                        == "missing"
                    ):
                        advance("planned", None)
                finally:
                    if anonymous_fd is not None:
                        os.close(anonymous_fd)
            if recovery["phase"] == "planned":
                advance("creating", None)
                flags = (
                    os.O_CREAT
                    | os.O_EXCL
                    | os.O_WRONLY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0)
                )
                recovery_fd = os.open(
                    recovery_name,
                    flags,
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    try:
                        os.setxattr(
                            recovery_fd,
                            "user.proxima.ops_migration",
                            recovery["ownership_token"].encode("ascii"),
                        )
                    except (AttributeError, OSError) as exc:
                        current = os.stat(
                            recovery_name,
                            dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                        opened = os.fstat(recovery_fd)
                        if _same_identity(current, opened):
                            os.unlink(recovery_name, dir_fd=parent_fd)
                            os.fsync(parent_fd)
                            advance("planned", None)
                        raise OpsMigrationCollision(
                            "this filesystem cannot prove recovery-file ownership"
                        ) from exc
                    recovery_identity = _stat_identity(os.fstat(recovery_fd))
                    os.fsync(recovery_fd)
                    os.fsync(parent_fd)
                    advance("created", recovery_identity)
                finally:
                    os.close(recovery_fd)
        if recovery["phase"] == "created":
            flags = os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            recovery_fd = os.open(
                recovery_name,
                flags,
                dir_fd=parent_fd,
            )
            try:
                if not _identity_matches(recovery["identity"], os.fstat(recovery_fd)):
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                os.ftruncate(recovery_fd, 0)
                _write_all(recovery_fd, content)
            finally:
                os.close(recovery_fd)
            advance("ready", recovery["identity"])
        if recovery["phase"] != "ready" or not _exact_regular_hash_at(
            parent_fd,
            recovery_name,
            expected_hash,
        ):
            raise OpsMigrationCollision(
                "container.md recovery file has ambiguous ownership"
            )
        try:
            os.link(
                recovery_name,
                path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            if not _exact_regular_hash_at(parent_fd, path.name, expected_hash):
                raise OpsMigrationCollision(
                    f"{path.name} appeared with unexpected content"
                )
        os.fsync(parent_fd)
        target_stat = os.stat(
            path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not _identity_matches(recovery["identity"], target_stat):
            raise OpsMigrationCollision(
                "published container.md has ambiguous ownership"
            )
        advance("published", _stat_identity(target_stat))
        _unlink_exact_file_at(
            parent_fd,
            recovery_name,
            expected_hash,
            recovery["identity"],
        )
        os.fsync(parent_fd)
        advance("complete", _stat_identity(target_stat))
    finally:
        if owns_parent_fd:
            os.close(parent_fd)


def _rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
    expected_identity: Mapping[str, Any] | None = None,
) -> None:
    if source_dir_fd is not None and expected_identity is not None:
        current_source = os.stat(
            source.name,
            dir_fd=source_dir_fd,
            follow_symlinks=False,
        )
        if not _identity_matches(expected_identity, current_source):
            raise OpsMigrationCollision(
                f"source identity changed before move: {source.name}"
            )
    source_bytes = os.fsencode(source.name if source_dir_fd is not None else source)
    destination_bytes = os.fsencode(
        destination.name if destination_dir_fd is not None else destination
    )
    source_at = source_dir_fd if source_dir_fd is not None else -100
    destination_at = (
        destination_dir_fd if destination_dir_fd is not None else -100
    )
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OpsMigrationCollision(
                "this platform cannot guarantee a no-clobber Ops migration"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_at,
            source_bytes,
            destination_at,
            destination_bytes,
            1,
        )
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise OpsMigrationCollision(
                "this platform cannot guarantee a no-clobber Ops migration"
            )
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_at,
            source_bytes,
            destination_at,
            destination_bytes,
            4,
        )
    elif os.name == "nt":
        if source_dir_fd is not None or destination_dir_fd is not None:
            raise OpsMigrationCollision(
                "this platform cannot guarantee stable no-clobber migration"
            )
        try:
            os.rename(source, destination)
        except FileExistsError as exc:
            raise OpsMigrationCollision(
                f"destination already exists: {destination.name}"
            ) from exc
        return
    else:
        raise OpsMigrationCollision(
            "this platform cannot guarantee a no-clobber Ops migration"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise OpsMigrationCollision(
            f"destination already exists: {destination.name}"
        )
    raise OSError(error_number, os.strerror(error_number), str(source))


def create_physical_ops_root(
    root: Path,
    name: str,
    starter_dirs: tuple[str, ...] = DEFAULT_STARTER_DIRS,
) -> Path:
    """Create the physical Ops layout for a fresh Container."""
    raw_root = Path(root)
    if raw_root.is_symlink():
        raise ContainerBoundaryError("Container root cannot be a symlink")
    if _platform_is_windows():
        if not raw_root.is_dir():
            raise ContainerBoundaryError("Container root is missing")
        return _create_physical_ops_root_windows(
            raw_root.absolute(),
            name,
            starter_dirs,
        )
    container = raw_root.resolve()
    if not container.is_dir():
        raise ContainerBoundaryError("Container root is missing")
    physical = container / OPS_RELPATH
    if physical.is_symlink():
        raise ContainerBoundaryError("physical Ops root cannot be a symlink")
    if physical.exists() and not physical.is_dir():
        raise ContainerBoundaryError("physical Ops root must be a directory")
    physical.mkdir(parents=True, exist_ok=True)
    for dirname in starter_dirs:
        rel = _safe_rel_path(dirname)
        if not rel.parts:
            raise ContainerBoundaryError(f"Ops starter path is unsafe: {dirname!r}")
        target = physical
        for part in rel.parts:
            target = target / part
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise ContainerBoundaryError(f"Ops starter path is unsafe: {dirname}")
        target.mkdir(parents=True, exist_ok=True)
    with _stable_migration_directories(container) as (
        root_fd,
        physical_fd,
        _,
        _,
    ):
        _atomic_write_if_missing(
            physical / CONTAINER_DOC,
            _container_doc_text(name),
            parent_fd=physical_fd,
        )
        _revalidate_root_identity(container, root_fd)
        _revalidate_physical_identity(root_fd, physical_fd)
    _reject_symlinks(physical)
    return physical


def _exclude_ops_from_root_repo_at(root_fd: int) -> None:
    git_fd: int | None = None
    info_fd: int | None = None
    exclude_fd: int | None = None
    try:
        git_fd = os.open(".git", _directory_open_flags(), dir_fd=root_fd)
        try:
            os.mkdir("info", mode=0o700, dir_fd=git_fd)
            os.fsync(git_fd)
        except FileExistsError:
            pass
        info_fd = os.open("info", _directory_open_flags(), dir_fd=git_fd)
        flags = (
            os.O_CREAT
            | os.O_RDWR
            | os.O_APPEND
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        exclude_fd = os.open("exclude", flags, 0o600, dir_fd=info_fd)
        current = os.fstat(exclude_fd)
        if not stat.S_ISREG(current.st_mode) or current.st_size > 4 * 1024 * 1024:
            return
        existing = b""
        while chunk := os.read(exclude_fd, 1024 * 1024):
            existing += chunk
        text = existing.decode("utf-8", errors="surrogateescape")
        if "/ops/" in text.splitlines():
            return
        prefix = "" if not text or text.endswith("\n") else "\n"
        addition = f"{prefix}/ops/\n".encode(
            "utf-8",
            errors="surrogateescape",
        )
        _write_all(exclude_fd, addition)
        os.fsync(info_fd)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return
    finally:
        if exclude_fd is not None:
            os.close(exclude_fd)
        if info_fd is not None:
            os.close(info_fd)
        if git_fd is not None:
            os.close(git_fd)


def exclude_ops_from_root_repo(
    root: Path,
    *,
    root_fd: int | None = None,
) -> None:
    """Keep physical Ops content out of a root repo's local git status."""
    if root_fd is not None:
        _exclude_ops_from_root_repo_at(root_fd)
        return
    if os.name != "posix":
        return
    try:
        opened_root = os.open(root, _directory_open_flags())
    except OSError:
        return
    try:
        _revalidate_root_identity(Path(root), opened_root)
        _exclude_ops_from_root_repo_at(opened_root)
        _revalidate_root_identity(Path(root), opened_root)
    except (ContainerBoundaryError, OSError):
        return
    finally:
        os.close(opened_root)


def _hash_file(path: Path) -> str:
    snapshot = _entry_snapshot(path)
    if snapshot["kind"] != "file":
        raise OpsMigrationCollision(f"{path.name} is not a regular file")
    return str(snapshot["sha256"])


def _hash_entry(path: Path) -> tuple[str, list[dict[str, Any]]]:
    snapshot = _entry_snapshot(path)
    return str(snapshot["sha256"]), list(snapshot["files"])


def _read_stable_file_at(
    directory_fd: int,
    name: str,
    *,
    display: str,
) -> tuple[str, os.stat_result]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise OpsMigrationCollision(
                f"legacy Ops path has an unsupported entry: {display}"
            )
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            not _same_identity(before, after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise OpsMigrationCollision(
                f"legacy Ops path changed while being inspected: {display}"
            )
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_identity(after, named):
            raise OpsMigrationCollision(
                f"legacy Ops path changed while being inspected: {display}"
            )
        return digest.hexdigest(), after
    finally:
        os.close(fd)


def _entry_snapshot_at(
    directory_fd: int,
    name: str,
) -> dict[str, Any]:
    initial = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if stat.S_ISLNK(initial.st_mode):
        raise OpsMigrationCollision(f"legacy Ops path is a symlink: {name}")
    if stat.S_ISREG(initial.st_mode):
        digest, opened = _read_stable_file_at(
            directory_fd,
            name,
            display=name,
        )
        identity = _stat_identity(opened)
        return {
            "kind": "file",
            "sha256": digest,
            "files": [
                {
                    "path": name,
                    "sha256": digest,
                    "identity": identity,
                }
            ],
            "nodes": [
                {
                    "path": ".",
                    "kind": "file",
                    "identity": identity,
                }
            ],
            "identity": identity,
        }
    if not stat.S_ISDIR(initial.st_mode):
        raise OpsMigrationCollision(f"legacy Ops path has an unsupported type: {name}")
    root_fd = os.open(
        name,
        _directory_open_flags(),
        dir_fd=directory_fd,
    )
    try:
        root_before = os.fstat(root_fd)
        files: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = [
            {
                "path": ".",
                "kind": "directory",
                "identity": _stat_identity(root_before),
            }
        ]
        aggregate = hashlib.sha256()

        def walk(parent_fd: int, prefix: str) -> None:
            for child_name in sorted(os.listdir(parent_fd)):
                rel = f"{prefix}/{child_name}" if prefix else child_name
                child = os.stat(
                    child_name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(child.st_mode):
                    raise OpsMigrationCollision(
                        f"legacy Ops path contains a symlink: {name}/{rel}"
                    )
                if stat.S_ISDIR(child.st_mode):
                    child_fd = os.open(
                        child_name,
                        _directory_open_flags(),
                        dir_fd=parent_fd,
                    )
                    try:
                        opened = os.fstat(child_fd)
                        if not _same_identity(child, opened):
                            raise OpsMigrationCollision(
                                f"legacy Ops path changed while being inspected: {name}/{rel}"
                            )
                        nodes.append(
                            {
                                "path": rel,
                                "kind": "directory",
                                "identity": _stat_identity(opened),
                            }
                        )
                        aggregate.update(f"D\0{rel}\0".encode())
                        walk(child_fd, rel)
                        current = os.stat(
                            child_name,
                            dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                        if not _same_identity(opened, current):
                            raise OpsMigrationCollision(
                                f"legacy Ops path changed while being inspected: {name}/{rel}"
                            )
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(child.st_mode):
                    raise OpsMigrationCollision(
                        f"legacy Ops path has an unsupported entry: {name}/{rel}"
                    )
                digest, opened = _read_stable_file_at(
                    parent_fd,
                    child_name,
                    display=f"{name}/{rel}",
                )
                identity = _stat_identity(opened)
                files.append(
                    {
                        "path": rel,
                        "sha256": digest,
                        "identity": identity,
                    }
                )
                nodes.append(
                    {
                        "path": rel,
                        "kind": "file",
                        "identity": identity,
                    }
                )
                aggregate.update(f"F\0{rel}\0{digest}\0".encode())

        walk(root_fd, "")
        root_after = os.fstat(root_fd)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not _same_identity(root_before, root_after)
            or not _same_identity(root_after, named)
            or root_before.st_mtime_ns != root_after.st_mtime_ns
            or root_before.st_ctime_ns != root_after.st_ctime_ns
        ):
            raise OpsMigrationCollision(
                f"legacy Ops path changed while being inspected: {name}"
            )
        return {
            "kind": "directory",
            "sha256": aggregate.hexdigest(),
            "files": files,
            "nodes": nodes,
            "identity": _stat_identity(root_after),
        }
    finally:
        os.close(root_fd)


def _entry_snapshot(path: Path) -> dict[str, Any]:
    if _platform_is_windows():
        return _entry_snapshot_windows(path)
    parent_fd = os.open(path.parent, _directory_open_flags())
    try:
        return _entry_snapshot_at(parent_fd, path.name)
    finally:
        os.close(parent_fd)


def _entry_snapshot_windows(path: Path) -> dict[str, Any]:
    initial = path.lstat()
    if (
        stat.S_ISLNK(initial.st_mode)
        or getattr(
            initial,
            "st_file_attributes",
            0,
        )
        & 0x00000400
    ):
        raise OpsMigrationCollision(f"legacy Ops path is a reparse point: {path.name}")
    if stat.S_ISREG(initial.st_mode):
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(fd)
            if not _same_identity(initial, opened):
                raise OpsMigrationCollision(
                    f"legacy Ops path changed while being inspected: {path.name}"
                )
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if (
            not _same_identity(opened, after)
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
        ):
            raise OpsMigrationCollision(
                f"legacy Ops path changed while being inspected: {path.name}"
            )
        identity = _stat_identity(after)
        value = digest.hexdigest()
        return {
            "kind": "file",
            "sha256": value,
            "files": [
                {
                    "path": path.name,
                    "sha256": value,
                    "identity": identity,
                }
            ],
            "nodes": [
                {
                    "path": ".",
                    "kind": "file",
                    "identity": identity,
                }
            ],
            "identity": identity,
        }
    if not stat.S_ISDIR(initial.st_mode):
        raise OpsMigrationCollision(
            f"legacy Ops path has an unsupported type: {path.name}"
        )
    files: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = [
        {
            "path": ".",
            "kind": "directory",
            "identity": _stat_identity(initial),
        }
    ]
    aggregate = hashlib.sha256()
    for child in sorted(
        path.rglob("*"),
        key=lambda item: item.relative_to(path).as_posix(),
    ):
        rel = child.relative_to(path).as_posix()
        current = child.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or getattr(
                current,
                "st_file_attributes",
                0,
            )
            & 0x00000400
        ):
            raise OpsMigrationCollision(
                f"legacy Ops path contains a reparse point: {path.name}/{rel}"
            )
        identity = _stat_identity(current)
        if stat.S_ISDIR(current.st_mode):
            nodes.append(
                {
                    "path": rel,
                    "kind": "directory",
                    "identity": identity,
                }
            )
            aggregate.update(f"D\0{rel}\0".encode())
            continue
        if not stat.S_ISREG(current.st_mode):
            raise OpsMigrationCollision(
                f"legacy Ops path has an unsupported entry: {path.name}/{rel}"
            )
        child_snapshot = _entry_snapshot_windows(child)
        digest = child_snapshot["sha256"]
        files.append(
            {
                "path": rel,
                "sha256": digest,
                "identity": child_snapshot["identity"],
            }
        )
        nodes.append(
            {
                "path": rel,
                "kind": "file",
                "identity": child_snapshot["identity"],
            }
        )
        aggregate.update(f"F\0{rel}\0{digest}\0".encode())
    current_root = path.lstat()
    if (
        not _same_identity(initial, current_root)
        or initial.st_mtime_ns != current_root.st_mtime_ns
    ):
        raise OpsMigrationCollision(
            f"legacy Ops path changed while being inspected: {path.name}"
        )
    return {
        "kind": "directory",
        "sha256": aggregate.hexdigest(),
        "files": files,
        "nodes": nodes,
        "identity": _stat_identity(current_root),
    }


def _snapshot_matches(
    snapshot: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> bool:
    return (
        snapshot.get("kind") == entry.get("kind")
        and snapshot.get("sha256") == entry.get("sha256")
        and snapshot.get("identity") == entry.get("identity")
        and snapshot.get("files") == entry.get("files")
        and snapshot.get("nodes") == entry.get("nodes")
    )


def _manifest_digest(manifest: dict[str, Any]) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _build_manifest(
    conn: sqlite3.Connection,
    container: Mapping[str, Any],
) -> dict[str, Any]:
    root = container_root(container)
    physical = root / OPS_RELPATH
    if physical.is_symlink():
        raise OpsMigrationCollision("physical Ops root is a symlink")
    if physical.exists() and not physical.is_dir():
        raise OpsMigrationCollision("physical Ops root collides with a non-directory")
    if physical.exists():
        _reject_symlinks(physical)
        try:
            if next(physical.iterdir(), None) is not None:
                raise OpsMigrationCollision("physical Ops root is not empty")
        except OSError as exc:
            raise OpsMigrationCollision(
                f"physical Ops root cannot be inspected: {exc}"
            ) from exc
    if (physical / CONTAINER_DOC).is_symlink():
        raise OpsMigrationCollision("ops/container.md is a symlink")

    code_paths = {
        str(row["rel_path"])
        for row in conn.execute(
            "SELECT rel_path FROM project_areas "
            "WHERE project_id = ? AND kind = 'code' AND source != 'excluded'",
            (container["id"],),
        ).fetchall()
    }
    if any(
        path == OPS_RELPATH or path.startswith(f"{OPS_RELPATH}/")
        for path in code_paths
    ):
        raise OpsMigrationCollision("the requested physical Ops root is an active repo Area")

    entries: list[dict[str, Any]] = []
    for name in (*KNOWN_OPS_DIRS, *KNOWN_OPS_FILES):
        source = root / name
        if not source.exists() and not source.is_symlink():
            continue
        if name in code_paths or any(path.startswith(f"{name}/") for path in code_paths):
            raise OpsMigrationCollision(f"legacy Ops path overlaps a repo Area: {name}")
        destination = physical / name
        if destination.exists() or destination.is_symlink():
            raise OpsMigrationCollision(f"destination already exists: ops/{name}")
        expected_dir = name in KNOWN_OPS_DIRS
        if expected_dir and not source.is_dir():
            raise OpsMigrationCollision(f"legacy Ops directory has an unexpected type: {name}")
        if not expected_dir and not source.is_file():
            raise OpsMigrationCollision(f"legacy Ops file has an unexpected type: {name}")
        snapshot = _entry_snapshot(source)
        entries.append(
            {
                "name": name,
                "kind": "directory" if expected_dir else "file",
                "sha256": snapshot["sha256"],
                "files": snapshot["files"],
                "nodes": snapshot["nodes"],
                "identity": snapshot["identity"],
            }
        )
    owner_doc = next(
        (entry for entry in entries if entry["name"] == CONTAINER_DOC),
        None,
    )
    if owner_doc is not None:
        container_doc: dict[str, Any] = {
            "path": CONTAINER_DOC,
            "strategy": "move",
            "sha256": owner_doc["sha256"],
        }
    else:
        content = _container_doc_text(
            str(container.get("name") or container.get("slug") or "Container")
        )
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        container_doc = {
            "path": CONTAINER_DOC,
            "strategy": "generate",
            "content": content,
            "sha256": expected_hash,
            "recovery_temp": _planned_recovery_temp(expected_hash),
        }
    return {
        "version": OPS_MIGRATION_VERSION,
        "container_root": str(root),
        "ops_root": str(physical),
        "entries": entries,
        "container_doc": container_doc,
    }


def _upsert_marker(
    conn: sqlite3.Connection,
    container_id: int,
    status: str,
    manifest: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    manifest_json = json.dumps(manifest, sort_keys=True) if manifest is not None else None
    digest = _manifest_digest(manifest) if manifest is not None else None
    conn.execute(
        """
        INSERT INTO container_ops_migrations(
          container_id, migration_version, status, manifest_json, manifest_hash,
          last_error, started_at, completed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                  CASE WHEN ? = 'complete' THEN CURRENT_TIMESTAMP ELSE NULL END,
                  CURRENT_TIMESTAMP)
        ON CONFLICT(container_id) DO UPDATE SET
          migration_version = excluded.migration_version,
          status = excluded.status,
          manifest_json = excluded.manifest_json,
          manifest_hash = excluded.manifest_hash,
          last_error = excluded.last_error,
          started_at = COALESCE(container_ops_migrations.started_at, excluded.started_at),
          completed_at = CASE
            WHEN excluded.status = 'complete' THEN CURRENT_TIMESTAMP
            ELSE container_ops_migrations.completed_at
          END,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            container_id,
            OPS_MIGRATION_VERSION,
            status,
            manifest_json,
            digest,
            error,
            status,
        ),
    )


def _attention(
    conn: sqlite3.Connection,
    container: Mapping[str, Any],
    reason: str,
) -> None:
    target = json.dumps(
        {
            "container_id": int(container["id"]),
            "container_slug": container.get("slug"),
            "reason": reason,
        },
        sort_keys=True,
    )
    source_key = f"container-ops-migration:{container['id']}"
    conn.execute(
        """
        INSERT INTO attention_items(
          kind, title, target_json, inline_ok, actions_json, status, source_key
        ) VALUES (
          'container_ops_migration',
          'Container Ops migration needs attention',
          ?, 0, '[]', 'open', ?
        )
        ON CONFLICT(source_key) DO UPDATE SET
          title = excluded.title,
          target_json = excluded.target_json,
          status = 'open',
          resolved_at = NULL
        """,
        (target, source_key),
    )


def _resolve_attention(conn: sqlite3.Connection, container_id: int) -> None:
    conn.execute(
        "UPDATE attention_items SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP "
        "WHERE source_key = ? AND status = 'open'",
        (f"container-ops-migration:{container_id}",),
    )


def _apply_manifest(
    manifest: Mapping[str, Any],
    *,
    root_fd: int,
    physical_fd: int,
    stable_root: Path,
    stable_physical: Path,
) -> Path:
    root = Path(str(manifest["container_root"]))
    physical = Path(str(manifest["ops_root"]))
    _revalidate_root_identity(root, root_fd)
    _revalidate_physical_identity(root_fd, physical_fd)

    for entry in manifest["entries"]:
        name = str(entry["name"])
        source = root / name
        destination = physical / name
        source_state = _path_state_at(root_fd, name)
        destination_state = _path_state_at(physical_fd, name)
        source_exists = source_state != "missing"
        destination_exists = destination_state != "missing"
        if source_exists and destination_exists:
            raise OpsMigrationCollision(
                f"both source and destination exist for {name}"
            )
        if not source_exists and not destination_exists:
            raise OpsMigrationCollision(
                f"both source and destination are missing for {name}"
            )
        current_state = source_state if source_exists else destination_state
        expected_state = (
            "directory" if entry["kind"] == "directory" else "file"
        )
        if current_state != expected_state:
            raise OpsMigrationCollision(
                f"Ops migration path has an unexpected type: {name}"
            )
        current_fd = root_fd if source_exists else physical_fd
        snapshot = _entry_snapshot_at(current_fd, name)
        if not _snapshot_matches(snapshot, entry):
            raise OpsMigrationCollision(
                f"content changed after migration planning: {name}"
            )
        if source_exists:
            source_stat = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if source_stat.st_dev != os.fstat(physical_fd).st_dev:
                raise OpsMigrationCollision(
                    f"source and destination are on different filesystems: {name}"
                )
            _revalidate_root_identity(root, root_fd)
            _revalidate_physical_identity(root_fd, physical_fd)
            _rename_noreplace(
                source,
                destination,
                source_dir_fd=root_fd,
                destination_dir_fd=physical_fd,
                expected_identity=entry["identity"],
            )
            os.fsync(root_fd)
            os.fsync(physical_fd)
            _revalidate_root_identity(root, root_fd)
            _revalidate_physical_identity(root_fd, physical_fd)
            moved_snapshot = _entry_snapshot_at(physical_fd, name)
            if not _snapshot_matches(moved_snapshot, entry):
                raise OpsMigrationCollision(
                    f"content hash changed during move: {name}"
                )
    return physical


def _parse_container_doc(path: Path) -> tuple[str | None, str | None, str | None]:
    if not path.is_file() or path.is_symlink():
        return None, None, None
    source_hash = _hash_file(path)
    with path.open("rb") as handle:
        data = handle.read(MAX_CONTAINER_DOC_BYTES)
    text = data.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    identity: str | None = None
    summary: str | None = None
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            for line in text[4:end].splitlines():
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                if key.strip() == "identity":
                    identity = value.strip()[:MAX_IDENTITY_LABEL_CHARS] or None
                elif key.strip() == "summary":
                    summary = value.strip()[:MAX_SUMMARY_CHARS] or None
    if summary is None:
        body = text.split("\n---\n", 1)[-1] if text.startswith("---\n") else text
        summary = next(
            (
                line.strip()[:MAX_SUMMARY_CHARS]
                for line in body.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ),
            None,
        )
    return identity, summary, source_hash


def refresh_registry_projection(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> None:
    data = get_container(conn, container)
    root = ops_root(conn, data)
    identity, summary, source_hash = _parse_container_doc(root / CONTAINER_DOC)
    conn.execute(
        """
        INSERT INTO container_registry(
          container_id, identity_label, summary, source_hash, indexed_at,
          last_activity_at
        ) VALUES (
          ?, ?, ?, ?, ?,
          (
            SELECT MAX(activity_at)
            FROM (
              SELECT created_at AS activity_at FROM projects WHERE id = ?
              UNION ALL
              SELECT MAX(updated_at) FROM sessions WHERE project_id = ?
              UNION ALL
              SELECT MAX(updated_at) FROM jobs WHERE project_id = ?
            )
          )
        )
        ON CONFLICT(container_id) DO UPDATE SET
          identity_label = CASE
            WHEN excluded.source_hash IS NOT container_registry.source_hash
              THEN excluded.identity_label
            ELSE container_registry.identity_label
          END,
          summary = CASE
            WHEN excluded.source_hash IS NOT container_registry.source_hash
              THEN excluded.summary
            ELSE container_registry.summary
          END,
          source_hash = excluded.source_hash,
          indexed_at = CASE
            WHEN excluded.source_hash IS NOT container_registry.source_hash
              THEN excluded.indexed_at
            ELSE container_registry.indexed_at
          END,
          last_activity_at = excluded.last_activity_at
        """,
        (
            data["id"],
            identity,
            summary,
            source_hash,
            iso_now(),
            data["id"],
            data["id"],
            data["id"],
        ),
    )


def refresh_registry_projections(conn: sqlite3.Connection) -> dict[str, int]:
    """Refresh every active Container projection outside request hot paths."""
    result = {"refreshed": 0, "unchanged": 0, "unavailable": 0}
    rows = conn.execute(
        """
        SELECT p.id, p.slug, p.name, p.path, p.path_identity, cr.source_hash
        FROM projects p
        LEFT JOIN container_registry cr ON cr.container_id = p.id
        WHERE p.archived_at IS NULL
        ORDER BY p.id
        """
    ).fetchall()
    for row in rows:
        before = row["source_hash"]
        try:
            refresh_registry_projection(conn, row)
        except (ContainerBoundaryError, OSError, sqlite3.Error):
            result["unavailable"] += 1
            continue
        after = conn.execute(
            "SELECT source_hash FROM container_registry WHERE container_id = ?",
            (row["id"],),
        ).fetchone()
        if after is not None and after["source_hash"] != before:
            result["refreshed"] += 1
        else:
            result["unchanged"] += 1
    return result


_FLEET_QUERY = """
WITH
job_live AS (
  SELECT
    project_id AS container_id,
    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_tasks,
    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_tasks
  FROM jobs
  WHERE archived_at IS NULL AND status IN ('running', 'queued')
  GROUP BY project_id
),
session_activity AS (
  SELECT project_id AS container_id, MAX(updated_at) AS activity_at
  FROM sessions
  WHERE project_id IS NOT NULL
  GROUP BY project_id
),
job_activity AS (
  SELECT project_id AS container_id, MAX(updated_at) AS activity_at
  FROM jobs
  WHERE project_id IS NOT NULL
  GROUP BY project_id
),
area_inventory AS (
  SELECT
    project_id AS container_id,
    COUNT(*) AS area_count,
    SUM(CASE WHEN kind = 'code' THEN 1 ELSE 0 END) AS code_area_count,
    SUM(CASE WHEN kind = 'ops' THEN 1 ELSE 0 END) AS ops_area_count,
    SUM(
      CASE WHEN kind = 'ops' AND rel_path = 'ops' THEN 1 ELSE 0 END
    ) AS physical_ops_area_count
  FROM project_areas
  WHERE source != 'excluded'
  GROUP BY project_id
),
attention_parsed AS (
  SELECT
    id,
    created_at,
    CASE WHEN json_valid(target_json)
      THEN CAST(json_extract(target_json, '$.container_id') AS INTEGER)
    END AS direct_container_id,
    CASE WHEN json_valid(target_json)
      THEN CAST(json_extract(target_json, '$.project_id') AS INTEGER)
    END AS legacy_project_id,
    CASE WHEN json_valid(target_json)
      THEN CAST(json_extract(target_json, '$.job_id') AS INTEGER)
    END AS job_id,
    CASE WHEN json_valid(target_json)
      THEN json_extract(target_json, '$.container_slug')
    END AS container_slug,
    CASE WHEN json_valid(target_json)
      THEN json_extract(target_json, '$.project_slug')
    END AS legacy_project_slug
  FROM attention_items
  WHERE status = 'open'
),
attention_scoped AS (
  SELECT
    ap.id,
    ap.created_at,
    COALESCE(
      ap.direct_container_id,
      ap.legacy_project_id,
      j.project_id,
      container_slug.id,
      project_slug.id
    ) AS container_id
  FROM attention_parsed ap
  LEFT JOIN jobs j ON j.id = ap.job_id
  LEFT JOIN projects container_slug ON container_slug.slug = ap.container_slug
  LEFT JOIN projects project_slug ON project_slug.slug = ap.legacy_project_slug
),
attention_live AS (
  SELECT container_id, COUNT(*) AS open_attention, MAX(created_at) AS activity_at
  FROM attention_scoped
  WHERE container_id IS NOT NULL
  GROUP BY container_id
)
SELECT
  p.id,
  p.slug,
  p.name,
  p.path,
  p.visibility,
  p.owner_user_id,
  p.created_at,
  u.username AS owner,
  cr.identity_label,
  cr.summary,
  cr.source_hash,
  cr.indexed_at,
  cr.last_activity_at AS projected_last_activity_at,
  COALESCE(jl.running_tasks, 0) AS running_tasks,
  COALESCE(jl.queued_tasks, 0) AS queued_tasks,
  COALESCE(al.open_attention, 0) AS open_attention,
  COALESCE(ai.area_count, 0) AS area_count,
  COALESCE(ai.code_area_count, 0) AS code_area_count,
  COALESCE(ai.ops_area_count, 0) AS ops_area_count,
  COALESCE(ai.physical_ops_area_count, 0) AS physical_ops_area_count,
  com.status AS ops_migration_status,
  MAX(
    p.created_at,
    COALESCE(cr.last_activity_at, p.created_at),
    COALESCE(sa.activity_at, p.created_at),
    COALESCE(ja.activity_at, p.created_at),
    COALESCE(al.activity_at, p.created_at)
  ) AS live_last_activity_at
FROM projects p
JOIN users u ON u.id = p.owner_user_id
LEFT JOIN container_registry cr ON cr.container_id = p.id
LEFT JOIN container_ops_migrations com ON com.container_id = p.id
LEFT JOIN job_live jl ON jl.container_id = p.id
LEFT JOIN session_activity sa ON sa.container_id = p.id
LEFT JOIN job_activity ja ON ja.container_id = p.id
LEFT JOIN area_inventory ai ON ai.container_id = p.id
LEFT JOIN attention_live al ON al.container_id = p.id
WHERE p.archived_at IS NULL
  AND p.owner_user_id = ?
  {slug_filter}
ORDER BY live_last_activity_at DESC, p.id DESC
"""


def _fleet_rows(
    conn: sqlite3.Connection,
    owner_user_id: int,
    slug: str | None = None,
) -> list[dict[str, Any]]:
    slug_filter = "AND p.slug = ?" if slug is not None else ""
    query = _FLEET_QUERY.format(slug_filter=slug_filter)
    params: tuple[Any, ...] = (
        (owner_user_id, slug) if slug is not None else (owner_user_id,)
    )
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _fleet_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    data = _as_dict(row)
    ops_count = int(data.get("ops_area_count") or 0)
    physical_ops_count = int(data.get("physical_ops_area_count") or 0)
    migration = data.get("ops_migration_status")
    if migration == "attention" or ops_count != 1:
        area_health = "attention"
    elif migration == "complete" or physical_ops_count == 1:
        area_health = "ready"
    else:
        area_health = "pending"
    migration_health = migration or (
        "not_required" if physical_ops_count == 1 else "pending"
    )
    return {
        "id": int(data["id"]),
        "slug": data["slug"],
        "name": data["name"],
        "path": data["path"],
        "owner": data["owner"],
        "visibility": data.get("visibility") or "private",
        "identity_label": data.get("identity_label"),
        "summary": data.get("summary"),
        "source_hash": data.get("source_hash"),
        "indexed_at": data.get("indexed_at"),
        "last_activity_at": data.get("live_last_activity_at"),
        "live": {
            "running_tasks": int(data.get("running_tasks") or 0),
            "queued_tasks": int(data.get("queued_tasks") or 0),
            "open_attention": int(data.get("open_attention") or 0),
        },
        "area_inventory": {
            "total": int(data.get("area_count") or 0),
            "code": int(data.get("code_area_count") or 0),
            "ops": ops_count,
        },
        "health": {
            "registry": "ready" if data.get("source_hash") else "unavailable",
            "areas": area_health,
            "ops_migration": migration_health,
            "graph_freshness": None,
        },
    }


def list_fleet_containers(
    conn: sqlite3.Connection,
    owner_user_id: int,
) -> list[dict[str, Any]]:
    """Return the Fleet from one bounded SQLite statement and no file reads."""
    return [_fleet_payload(row) for row in _fleet_rows(conn, owner_user_id)]


def get_fleet_container(
    conn: sqlite3.Connection,
    owner_user_id: int,
    slug: str,
) -> dict[str, Any] | None:
    """Return one owner-scoped Container with directly aggregated Live state."""
    rows = _fleet_rows(conn, owner_user_id, slug)
    return _fleet_payload(rows[0]) if rows else None


def compatibility_project_payload(
    container: Mapping[str, Any],
    areas: Mapping[str, Any],
) -> dict[str, Any]:
    """Render the one-release Project reader alias from Container truth."""
    data = _as_dict(container)
    return {
        "slug": data["slug"],
        "name": data["name"],
        "path": data["path"],
        "owner": data.get("owner"),
        "role": data.get("role") or "owner",
        "visibility": data.get("visibility") or "private",
        "code_areas": list(areas.get("code_areas") or []),
        "ops_area": areas.get("ops_area"),
    }


def list_compatibility_projects(
    conn: sqlite3.Connection,
    owner_user_id: int,
) -> list[dict[str, Any]]:
    """Render legacy Project readers from the same Fleet and Area queries."""
    rows = _fleet_rows(conn, owner_user_id)
    rows.sort(
        key=lambda row: (str(row.get("created_at") or ""), int(row["id"])),
        reverse=True,
    )
    containers = [_fleet_payload(row) for row in rows]
    areas_by_container: dict[int, dict[str, Any]] = {
        int(container["id"]): {"code_areas": [], "ops_area": None}
        for container in containers
    }
    rows = conn.execute(
        """
        SELECT
          pa.id,
          pa.project_id,
          pa.kind,
          pa.rel_path,
          pa.source,
          pa.push_on_merge,
          pa.push_remote_url
        FROM project_areas pa
        JOIN projects p ON p.id = pa.project_id
        WHERE p.owner_user_id = ?
          AND p.archived_at IS NULL
          AND pa.source != 'excluded'
        ORDER BY pa.project_id, pa.kind, pa.rel_path, pa.id
        """,
        (owner_user_id,),
    ).fetchall()
    for row in rows:
        areas = areas_by_container.get(int(row["project_id"]))
        if areas is None:
            continue
        if row["kind"] == "code":
            areas["code_areas"].append(
                {
                    "id": int(row["id"]),
                    "rel_path": row["rel_path"],
                    "source": row["source"],
                    "push_on_merge": bool(row["push_on_merge"]),
                    "push_remote_url": row["push_remote_url"],
                }
            )
        elif row["kind"] == "ops":
            areas["ops_area"] = {
                "id": int(row["id"]),
                "rel_path": row["rel_path"],
            }
    return [
        compatibility_project_payload(
            container,
            areas_by_container[int(container["id"])],
        )
        for container in containers
    ]


def _path_state(path: Path) -> str:
    """Classify one path without following it."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _path_device(path: Path) -> int:
    return int(path.lstat().st_dev)


def _physical_ops_state(
    path: Path,
    internal_files: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    state = _path_state(path)
    entries: list[dict[str, str]] = []
    if state == "directory":
        try:
            with os.scandir(path) as children:
                for child in sorted(children, key=lambda item: item.name.casefold()):
                    expected_internal = (
                        internal_files.get(child.name)
                        if internal_files is not None
                        else None
                    )
                    exact_internal_file = False
                    if expected_internal is not None:
                        try:
                            exact_internal_file = (
                                child.is_file(follow_symlinks=False)
                                and not child.is_symlink()
                                and _identity_matches(
                                    expected_internal["identity"],
                                    child.stat(follow_symlinks=False),
                                )
                                and (
                                    expected_internal["phase"] == "created"
                                    or _hash_file(Path(child.path))
                                    == expected_internal["sha256"]
                                )
                            )
                        except OSError:
                            exact_internal_file = False
                    if exact_internal_file:
                        continue
                    if child.is_symlink():
                        kind = "symlink"
                    elif child.is_dir(follow_symlinks=False):
                        kind = "directory"
                    elif child.is_file(follow_symlinks=False):
                        kind = "file"
                    else:
                        kind = "other"
                    entries.append({"path": f"{OPS_RELPATH}/{child.name}", "kind": kind})
        except OSError:
            state = "unavailable"
            entries = []
        else:
            state = "populated" if entries else "empty"
    return {"path": OPS_RELPATH, "state": state, "entries": entries}


def _validate_manifest_area_ownership(
    conn: sqlite3.Connection,
    container: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    code_paths = {
        str(row["rel_path"])
        for row in conn.execute(
            "SELECT rel_path FROM project_areas "
            "WHERE project_id = ? AND kind = 'code' AND source != 'excluded'",
            (container["id"],),
        ).fetchall()
    }
    for code_path in code_paths:
        if code_path == OPS_RELPATH or code_path.startswith(f"{OPS_RELPATH}/"):
            raise OpsMigrationCollision(
                f"Ops migration path overlaps a repo Area: {code_path}"
            )
    for entry in manifest.get("entries") or []:
        name = str(entry.get("name") or "")
        physical_name = f"{OPS_RELPATH}/{name}"
        for code_path in code_paths:
            if (
                code_path == name
                or code_path.startswith(f"{name}/")
                or code_path == physical_name
                or code_path.startswith(f"{physical_name}/")
            ):
                raise OpsMigrationCollision(
                    f"Ops migration path overlaps a repo Area: {code_path}"
                )


def _upgrade_manifest(
    container: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    version = manifest.get("version")
    if version == OPS_MIGRATION_VERSION:
        return dict(manifest)
    if version not in {1, 2, 3, 4}:
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an unsupported version"
        )
    upgraded = json.loads(json.dumps(manifest))
    root = Path(str(upgraded.get("container_root") or ""))
    physical = Path(str(upgraded.get("ops_root") or ""))
    entries = upgraded.get("entries")
    if not isinstance(entries, list):
        raise OpsMigrationCollision("stored Ops migration manifest has invalid entries")

    if version == 4:
        planned = upgraded.get("container_doc")
        if not isinstance(planned, dict):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has no planned container.md"
            )
        if planned.get("strategy") == "generate":
            expected_hash = planned.get("sha256")
            recovery = planned.get("recovery_temp")
            if (
                not isinstance(expected_hash, str)
                or not isinstance(recovery, dict)
                or not _valid_legacy_recovery_temp(recovery, expected_hash)
            ):
                raise OpsMigrationCollision(
                    "stored Ops migration manifest has an invalid recovery file"
                )
            recovery_path = physical / str(recovery["path"])
            target_path = physical / CONTAINER_DOC
            recovery_state = _path_state(recovery_path)
            target_state = _path_state(target_path)
            if recovery["phase"] == "planned":
                if recovery_state != "missing" or target_state != "missing":
                    raise OpsMigrationCollision(
                        "legacy recovery artifact has ambiguous ownership"
                    )
                planned["recovery_temp"] = _planned_recovery_temp(expected_hash)
            else:
                bound_path = (
                    recovery_path
                    if recovery_state == "file"
                    else target_path
                    if target_state == "file"
                    else None
                )
                if (
                    bound_path is None
                    or not _identity_matches(
                        recovery["identity"],
                        bound_path.lstat(),
                    )
                    or (
                        recovery["phase"] != "created"
                        and _hash_file(bound_path) != expected_hash
                    )
                ):
                    raise OpsMigrationCollision(
                        "legacy recovery artifact has ambiguous ownership"
                    )
                recovery["ownership_token"] = secrets.token_hex(32)
        elif planned.get("strategy") != "move":
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid planned container.md"
            )
        upgraded["version"] = OPS_MIGRATION_VERSION
        return upgraded

    if version in {2, 3}:
        planned = upgraded.get("container_doc")
        if not isinstance(planned, dict):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has no planned container.md"
            )
        strategy = planned.get("strategy")
        expected_hash = planned.get("sha256")
        if (
            strategy not in {"generate", "move"}
            or not isinstance(expected_hash, str)
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid planned container.md"
            )
        if strategy == "generate":
            content = planned.get("content")
            if (
                not isinstance(content, str)
                or hashlib.sha256(content.encode("utf-8")).hexdigest()
                != expected_hash
            ):
                raise OpsMigrationCollision(
                    "stored Ops migration manifest has an invalid generated container.md"
                )
            prior_recovery = planned.get("recovery_temp")
            if isinstance(prior_recovery, Mapping):
                prior_path = str(prior_recovery.get("path") or "")
                if prior_path and _path_state(physical / prior_path) != "missing":
                    raise OpsMigrationCollision(
                        "legacy recovery artifact has ambiguous ownership"
                    )
            if _path_state(physical / CONTAINER_DOC) != "missing":
                raise OpsMigrationCollision(
                    "existing generated container.md has no durable ownership identity"
                )
            planned["recovery_temp"] = _planned_recovery_temp(expected_hash)
        elif planned.get("recovery_temp") is not None:
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid owner container.md"
            )
    else:
        if any(
            isinstance(entry, Mapping) and entry.get("name") == CONTAINER_DOC
            for entry in entries
        ):
            raise OpsMigrationCollision(
                "version 1 Ops migration manifest has ambiguous container.md ownership"
            )
        legacy_doc = root / CONTAINER_DOC
        physical_doc = physical / CONTAINER_DOC
        legacy_state = _path_state(legacy_doc)
        physical_state = _path_state(physical_doc)
        if legacy_state not in {"missing", "file"}:
            raise OpsMigrationCollision(
                "legacy container.md is not an unambiguous regular file"
            )
        if physical_state not in {"missing", "file"}:
            raise OpsMigrationCollision(
                "ops/container.md is not an unambiguous regular file"
            )
        if legacy_state == "file" and physical_state == "file":
            raise OpsMigrationCollision(
                "both legacy and physical container.md exist; owner intervention is required"
            )

        prior = upgraded.get("container_doc")
        if isinstance(prior, Mapping):
            content = prior.get("content")
            expected_hash = prior.get("sha256")
            if (
                prior.get("path") != CONTAINER_DOC
                or not isinstance(content, str)
                or not isinstance(expected_hash, str)
                or hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash
            ):
                raise OpsMigrationCollision(
                    "stored Ops migration manifest has an invalid planned container.md"
                )
            if legacy_state == "file":
                raise OpsMigrationCollision(
                    "version 1 generated container.md ownership is ambiguous"
                )
            recovery = _planned_recovery_temp(expected_hash)
            if physical_state == "file":
                if _hash_file(physical_doc) != expected_hash:
                    raise OpsMigrationCollision(
                        "version 1 generated container.md content is ambiguous"
                    )
                recovery["phase"] = "complete"
                recovery["identity"] = _stat_identity(physical_doc.lstat())
            planned_doc: dict[str, Any] = {
                "path": CONTAINER_DOC,
                "strategy": "generate",
                "content": content,
                "sha256": expected_hash,
                "recovery_temp": recovery,
            }
        elif legacy_state == "file":
            snapshot = _entry_snapshot(legacy_doc)
            entries.append(
                {
                    "name": CONTAINER_DOC,
                    "kind": "file",
                    "sha256": snapshot["sha256"],
                    "files": snapshot["files"],
                    "nodes": snapshot["nodes"],
                    "identity": snapshot["identity"],
                }
            )
            planned_doc = {
                "path": CONTAINER_DOC,
                "strategy": "move",
                "sha256": snapshot["sha256"],
            }
        else:
            content = _container_doc_text(
                str(container.get("name") or container.get("slug") or "Container")
            )
            expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            recovery = _planned_recovery_temp(expected_hash)
            if physical_state == "file":
                if _hash_file(physical_doc) != expected_hash:
                    raise OpsMigrationCollision(
                        "version 1 generated container.md content is ambiguous"
                    )
                recovery["phase"] = "complete"
                recovery["identity"] = _stat_identity(physical_doc.lstat())
            planned_doc = {
                "path": CONTAINER_DOC,
                "strategy": "generate",
                "content": content,
                "sha256": expected_hash,
                "recovery_temp": recovery,
            }
        upgraded["container_doc"] = planned_doc

    for entry in entries:
        if not isinstance(entry, dict):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
            )
        name = str(entry.get("name") or "")
        source = root / name
        destination = physical / name
        source_exists = _path_state(source) != "missing"
        destination_exists = _path_state(destination) != "missing"
        if source_exists == destination_exists:
            raise OpsMigrationCollision(
                f"legacy manifest path has ambiguous ownership: {name}"
            )
        snapshot = _entry_snapshot(source if source_exists else destination)
        old_files = entry.get("files")
        if (
            snapshot["kind"] != entry.get("kind")
            or snapshot["sha256"] != entry.get("sha256")
            or not isinstance(old_files, list)
            or [
                {
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                }
                for item in snapshot["files"]
            ]
            != [
                {
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                }
                for item in old_files
                if isinstance(item, Mapping)
            ]
        ):
            raise OpsMigrationCollision(
                f"legacy manifest content is no longer unambiguous: {name}"
            )
        entry["files"] = snapshot["files"]
        entry["nodes"] = snapshot["nodes"]
        entry["identity"] = snapshot["identity"]

    upgraded["version"] = OPS_MIGRATION_VERSION
    return upgraded


def _manifest_container_doc(
    manifest: Mapping[str, Any],
) -> tuple[str, str | None, str]:
    if manifest.get("version") != OPS_MIGRATION_VERSION:
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an unsupported version"
        )
    planned = manifest.get("container_doc")
    if not isinstance(planned, Mapping):
        raise OpsMigrationCollision(
            "stored Ops migration manifest has no planned container.md"
        )
    strategy = planned.get("strategy")
    content = planned.get("content")
    expected_hash = planned.get("sha256")
    if (
        planned.get("path") != CONTAINER_DOC
        or strategy not in {"generate", "move"}
        or not isinstance(expected_hash, str)
    ):
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid planned container.md"
        )
    doc_entries = [
        entry
        for entry in manifest.get("entries") or []
        if isinstance(entry, Mapping) and entry.get("name") == CONTAINER_DOC
    ]
    if strategy == "generate":
        recovery_temp = planned.get("recovery_temp")
        if (
            not isinstance(content, str)
            or hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash
            or doc_entries
            or not isinstance(recovery_temp, dict)
            or not _valid_recovery_temp(recovery_temp, expected_hash)
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid generated container.md"
            )
        return strategy, content, expected_hash
    if (
        content is not None
        or planned.get("recovery_temp") is not None
        or len(doc_entries) != 1
    ):
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid owner container.md"
        )
    entry = doc_entries[0]
    if entry.get("kind") != "file" or entry.get("sha256") != expected_hash:
        raise OpsMigrationCollision(
            "stored Ops migration manifest does not bind owner container.md"
        )
    return strategy, None, expected_hash


def _manifest_recovery_temp(
    manifest: Mapping[str, Any],
) -> dict[str, Any] | None:
    strategy, _, expected_hash = _manifest_container_doc(manifest)
    if strategy != "generate":
        return None
    planned = manifest["container_doc"]
    recovery = planned["recovery_temp"]
    if not isinstance(recovery, dict) or not _valid_recovery_temp(
        recovery, expected_hash
    ):
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid recovery file"
        )
    return recovery


def _valid_recovery_temp(
    recovery: Mapping[str, Any],
    expected_hash: str,
) -> bool:
    path = recovery.get("path")
    phase = recovery.get("phase")
    identity = recovery.get("identity")
    return (
        isinstance(path, str)
        and path.startswith(RECOVERY_TEMP_PREFIX)
        and path.endswith(".tmp")
        and "/" not in path
        and "\\" not in path
        and recovery.get("sha256") == expected_hash
        and isinstance(recovery.get("ownership_token"), str)
        and len(recovery["ownership_token"]) == 64
        and all(
            character in "0123456789abcdef"
            for character in recovery["ownership_token"]
        )
        and phase
        in {
            "planned",
            "creating",
            "prepared",
            "created",
            "ready",
            "published",
            "complete",
        }
        and (
            (phase in {"planned", "creating"} and identity is None)
            or (
                phase not in {"planned", "creating"}
                and _valid_identity(identity)
            )
        )
    )


def _valid_legacy_recovery_temp(
    recovery: Mapping[str, Any],
    expected_hash: str,
) -> bool:
    path = recovery.get("path")
    phase = recovery.get("phase")
    identity = recovery.get("identity")
    return (
        isinstance(path, str)
        and path.startswith(RECOVERY_TEMP_PREFIX)
        and path.endswith(".tmp")
        and "/" not in path
        and "\\" not in path
        and recovery.get("sha256") == expected_hash
        and phase in {"planned", "created", "ready", "published", "complete"}
        and (
            (phase == "planned" and identity is None)
            or (phase != "planned" and _valid_identity(identity))
        )
    )


def _validate_manifest_entries(manifest: Mapping[str, Any]) -> None:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise OpsMigrationCollision("stored Ops migration manifest has invalid entries")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
            )
        name = entry.get("name")
        digest = entry.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
            or name in seen
            or entry.get("kind") not in {"directory", "file"}
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not _valid_identity(entry.get("identity"))
            or not isinstance(entry.get("files"), list)
            or not isinstance(entry.get("nodes"), list)
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
            )
        for node in entry["nodes"]:
            if (
                not isinstance(node, Mapping)
                or not isinstance(node.get("path"), str)
                or node.get("kind") not in {"directory", "file"}
                or not _valid_identity(node.get("identity"))
            ):
                raise OpsMigrationCollision(
                    "stored Ops migration manifest has an invalid inode binding"
                )
        for file_entry in entry["files"]:
            if (
                not isinstance(file_entry, Mapping)
                or not isinstance(file_entry.get("path"), str)
                or not isinstance(file_entry.get("sha256"), str)
                or not _valid_identity(file_entry.get("identity"))
            ):
                raise OpsMigrationCollision(
                    "stored Ops migration manifest has an invalid file binding"
                )
        seen.add(name)


def _validated_retry_manifest(
    conn: sqlite3.Connection,
    container: Mapping[str, Any],
    marker: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build or verify a retry manifest without moving or creating anything."""
    root = container_root(container)
    physical = root / OPS_RELPATH
    manifest: dict[str, Any]
    if marker and marker.get("status") == "moving" and marker.get("manifest_json"):
        try:
            manifest = json.loads(str(marker["manifest_json"]))
        except (TypeError, ValueError) as exc:
            raise OpsMigrationCollision("stored Ops migration manifest is invalid") from exc
        if _manifest_digest(manifest) != marker.get("manifest_hash"):
            raise OpsMigrationCollision(
                "stored Ops migration manifest failed its integrity check"
            )
        if (
            manifest.get("container_root") != str(root)
            or manifest.get("ops_root") != str(physical)
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest no longer matches this Container"
            )
        manifest = _upgrade_manifest(container, manifest)
    else:
        manifest = _build_manifest(conn, container)

    _validate_manifest_entries(manifest)
    validated_area_roots(conn, container, deep_ops_scan=True)
    _validate_manifest_area_ownership(conn, container, manifest)

    physical_state = _path_state(physical)
    if physical_state == "symlink":
        raise OpsMigrationCollision("physical Ops root is a symlink")
    if physical_state not in {"missing", "directory"}:
        raise OpsMigrationCollision("physical Ops root collides with a non-directory")

    allowed_physical = {
        str(entry.get("name") or "")
        for entry in manifest.get("entries") or []
    } | {CONTAINER_DOC}
    recovery_temp = _manifest_recovery_temp(manifest)
    if recovery_temp is not None:
        allowed_physical.add(recovery_temp["path"])
    if physical_state == "directory":
        try:
            with os.scandir(physical) as children:
                unexpected = sorted(
                    child.name
                    for child in children
                    if child.name not in allowed_physical
                )
        except OSError as exc:
            raise OpsMigrationCollision(
                f"physical Ops root cannot be inspected: {exc}"
            ) from exc
        if unexpected:
            raise OpsMigrationCollision(
                f"physical Ops root contains unplanned content: {unexpected[0]}"
            )
        if recovery_temp is not None:
            recovery_path = physical / recovery_temp["path"]
            recovery_state = _path_state(recovery_path)
            if recovery_state != "missing":
                phase = recovery_temp["phase"]
                if recovery_state != "file" or phase == "planned":
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                if phase == "creating":
                    if not _creating_recovery_matches(
                        recovery_path,
                        recovery_temp,
                    ):
                        raise OpsMigrationCollision(
                            "container.md recovery file has ambiguous ownership"
                        )
                    recovery_stat = None
                else:
                    recovery_stat = recovery_path.lstat()
                if recovery_stat is not None and not _identity_matches(
                    recovery_temp["identity"],
                    recovery_stat,
                ):
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                if (
                    phase not in {"creating", "created"}
                    and _hash_file(recovery_path) != recovery_temp["sha256"]
                ):
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )

    container_doc_strategy, _, expected_container_doc_hash = _manifest_container_doc(
        manifest
    )
    container_doc_path = physical / CONTAINER_DOC
    container_doc_state = _path_state(container_doc_path)
    if container_doc_state == "symlink":
        raise OpsMigrationCollision("ops/container.md is a symlink")
    if container_doc_state not in {"missing", "file"}:
        raise OpsMigrationCollision("ops/container.md is not a regular file")
    if (
        container_doc_state == "file"
        and _hash_file(container_doc_path) != expected_container_doc_hash
    ):
        raise OpsMigrationCollision("ops/container.md changed after migration planning")
    if container_doc_state == "file" and container_doc_strategy == "generate":
        assert recovery_temp is not None
        target_stat = container_doc_path.lstat()
        if recovery_temp["phase"] not in {
            "ready",
            "published",
            "complete",
        } or not _identity_matches(recovery_temp["identity"], target_stat):
            if (
                recovery_temp["phase"] != "ready"
                or _path_state(physical / recovery_temp["path"]) != "file"
            ):
                raise OpsMigrationCollision(
                    "existing generated container.md has ambiguous ownership"
                )
            recovery_stat = (physical / recovery_temp["path"]).lstat()
            if not _identity_matches(
                recovery_temp["identity"],
                recovery_stat,
            ) or not _same_identity(recovery_stat, target_stat):
                raise OpsMigrationCollision(
                    "existing generated container.md has ambiguous ownership"
                )
    if (
        container_doc_state == "missing"
        and recovery_temp is not None
        and _path_state(physical / recovery_temp["path"]) == "missing"
        and recovery_temp["phase"] not in {"planned", "creating", "prepared"}
    ):
        raise OpsMigrationCollision(
            "container.md recovery file has ambiguous ownership"
        )
    if (
        container_doc_strategy == "generate"
        and _path_state(root / CONTAINER_DOC) != "missing"
    ):
        raise OpsMigrationCollision(
            "legacy container.md appeared after migration planning"
        )

    destination_device = (
        _path_device(physical) if physical_state == "directory" else _path_device(root)
    )
    for entry in manifest.get("entries") or []:
        name = str(entry.get("name") or "")
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise OpsMigrationCollision("stored Ops migration manifest has an unsafe path")
        source = root / name
        destination = physical / name
        source_state = _path_state(source)
        destination_state = _path_state(destination)
        source_exists = source_state != "missing"
        destination_exists = destination_state != "missing"
        if source_exists and destination_exists:
            raise OpsMigrationCollision(f"both source and destination exist for {name}")
        if not source_exists and not destination_exists:
            raise OpsMigrationCollision(
                f"both source and destination are missing for {name}"
            )
        current = source if source_exists else destination
        expected_kind = "directory" if entry.get("kind") == "directory" else "file"
        current_state = source_state if source_exists else destination_state
        if current_state == "symlink":
            raise OpsMigrationCollision(f"Ops migration path is a symlink: {name}")
        if current_state != expected_kind:
            raise OpsMigrationCollision(
                f"Ops migration path has an unexpected type: {name}"
            )
        snapshot = _entry_snapshot(current)
        if not _snapshot_matches(snapshot, entry):
            raise OpsMigrationCollision(
                f"content changed after migration planning: {name}"
            )
        if source_exists and _path_device(source) != destination_device:
            raise OpsMigrationCollision(
                f"source and destination are on different filesystems: {name}"
            )
    return manifest


def inspect_ops_migration(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """Return an owner-facing, non-mutating view of one Ops migration."""
    data = get_container(conn, container)
    area_rows = conn.execute(
        """
        SELECT id, rel_path
        FROM project_areas
        WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'
        ORDER BY id
        """,
        (data["id"],),
    ).fetchall()
    active_ops_path = (
        str(area_rows[0]["rel_path"]) if len(area_rows) == 1 else None
    )
    marker_row = conn.execute(
        """
        SELECT migration_version, status, manifest_json, manifest_hash, last_error,
               started_at, completed_at, updated_at
        FROM container_ops_migrations
        WHERE container_id = ?
        """,
        (data["id"],),
    ).fetchone()
    marker = dict(marker_row) if marker_row is not None else None
    attention_row = conn.execute(
        """
        SELECT target_json, status, created_at, resolved_at
        FROM attention_items
        WHERE source_key = ?
        """,
        (f"container-ops-migration:{data['id']}",),
    ).fetchone()
    attention_target: dict[str, Any] = {}
    if attention_row is not None:
        try:
            parsed = json.loads(str(attention_row["target_json"]))
            if isinstance(parsed, dict):
                attention_target = parsed
        except (TypeError, ValueError):
            pass
    stored_reason = (
        str(marker.get("last_error"))
        if marker and marker.get("last_error")
        else str(attention_target.get("reason") or "")
    ) or None

    root: Path | None
    root_error: str | None = None
    try:
        root = container_root(data)
    except ContainerBoundaryError as exc:
        root = None
        root_error = str(exc)
    physical = root / OPS_RELPATH if root is not None else None
    manifest_names: set[str] = set()
    stored_manifest: dict[str, Any] | None = None
    if marker and marker.get("manifest_json"):
        try:
            parsed_manifest = json.loads(str(marker["manifest_json"]))
            if isinstance(parsed_manifest, dict):
                stored_manifest = parsed_manifest
            manifest_names = {
                str(entry.get("name") or "")
                for entry in (stored_manifest or {}).get("entries") or []
                if isinstance(entry, dict)
            }
        except (TypeError, ValueError):
            pass
    internal_files: dict[str, Mapping[str, Any]] = {}
    if (
        physical is not None
        and stored_manifest is not None
        and stored_manifest.get("version") == OPS_MIGRATION_VERSION
        and _manifest_digest(stored_manifest) == marker.get("manifest_hash")
    ):
        try:
            recovery_temp = _manifest_recovery_temp(stored_manifest)
        except OpsMigrationCollision:
            recovery_temp = None
        if recovery_temp is not None:
            recovery_path = physical / recovery_temp["path"]
            try:
                phase = recovery_temp["phase"]
                state = _path_state(recovery_path)
                exact_recovery = (
                    state == "file"
                    and (
                        (
                            phase == "creating"
                            and _creating_recovery_matches(
                                recovery_path,
                                recovery_temp,
                            )
                        )
                        or (
                            phase not in {"planned", "creating"}
                            and _identity_matches(
                                recovery_temp["identity"],
                                recovery_path.lstat(),
                            )
                            and (
                                phase == "created"
                                or _hash_file(recovery_path)
                                == recovery_temp["sha256"]
                            )
                        )
                    )
                )
            except OSError:
                exact_recovery = False
            if exact_recovery:
                internal_files[recovery_temp["path"]] = recovery_temp
    physical_ops = (
        _physical_ops_state(physical, internal_files)
        if physical is not None
        else {"path": OPS_RELPATH, "state": "unavailable", "entries": []}
    )
    owned_paths: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    available: list[str] = []
    unavailable: list[str] = []
    for name in (*KNOWN_OPS_DIRS, *KNOWN_OPS_FILES):
        if root is None or physical is None:
            if name not in manifest_names:
                continue
            source_state = "unavailable"
            destination_state = "unavailable"
        else:
            source_state = _path_state(root / name)
            destination_state = _path_state(physical / name)
        if source_state == "missing" and destination_state == "missing":
            continue
        if root is None:
            layout = "unavailable"
        elif source_state != "missing" and destination_state != "missing":
            layout = "both"
            conflicts.append(
                {
                    "path": name,
                    "reason": f"Both {name} and {OPS_RELPATH}/{name} exist.",
                }
            )
        elif source_state != "missing":
            layout = "legacy"
        else:
            layout = "physical"
        expected = "directory" if name in KNOWN_OPS_DIRS else "file"
        active_state = source_state if active_ops_path == "." else destination_state
        if active_state == expected:
            available.append(name)
        else:
            unavailable.append(name)
        if source_state == "symlink" or destination_state == "symlink":
            conflicts.append(
                {
                    "path": name,
                    "reason": f"{name} includes a symlink and will not be followed.",
                }
            )
        owned_paths.append(
            {
                "path": name,
                "destination": f"{OPS_RELPATH}/{name}",
                "expected_kind": expected,
                "legacy_state": source_state,
                "physical_state": destination_state,
                "layout": layout,
                "usable_from_active_ops": active_state == expected,
            }
        )

    retry_safe = False
    validation_reason: str | None = None
    if root_error:
        validation_reason = root_error
    elif active_ops_path == ".":
        try:
            _validated_retry_manifest(conn, data, marker)
        except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
            validation_reason = str(exc)
        else:
            retry_safe = True
    elif active_ops_path == OPS_RELPATH:
        try:
            validated_area_roots(conn, data, deep_ops_scan=True)
        except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
            validation_reason = str(exc)
        else:
            if attention_row is not None and attention_row["status"] == "open":
                retry_safe = True
            else:
                validation_reason = "Migration is already complete; no retry is needed."
    elif active_ops_path is None:
        validation_reason = "Container must have exactly one active Ops Area."
    else:
        validation_reason = f"unsupported legacy Ops Area path: {active_ops_path}"
    if validation_reason and active_ops_path != OPS_RELPATH and not any(
        item["reason"] == validation_reason for item in conflicts
    ):
        conflicts.append({"path": OPS_RELPATH, "reason": validation_reason})

    if marker is not None:
        phase = str(marker["status"])
    elif active_ops_path == OPS_RELPATH:
        phase = "not_required"
    else:
        phase = "unplanned"
    legacy_active = active_ops_path == "."
    physical_active = active_ops_path == OPS_RELPATH
    if physical_active:
        usability_summary = "Physical ops/ is active and Ops features remain usable."
    elif legacy_active and not unavailable:
        usability_summary = (
            "The legacy Ops layout remains active. Existing Ops features continue "
            "to use the legacy paths until a safe retry completes."
        )
    elif legacy_active:
        usability_summary = (
            "The legacy Ops layout is still active, but paths already moved to ops/ "
            "are unavailable through normal Ops features until recovery completes."
        )
    else:
        usability_summary = "Ops features are unavailable until the Area layout is repaired."
    return {
        "project": {
            "id": int(data["id"]),
            "slug": data.get("slug"),
            "name": data.get("name"),
        },
        "phase": phase,
        "migration_version": (
            int(marker["migration_version"]) if marker is not None else OPS_MIGRATION_VERSION
        ),
        "stored_reason": stored_reason,
        "active_ops_path": active_ops_path,
        "legacy_owned_paths": owned_paths,
        "physical_ops": physical_ops,
        "inspection": {
            "legacy_root": {
                "inspectable": root is not None,
                "reason": root_error,
            },
            "physical_root": {
                "inspectable": physical_ops["state"] in {"empty", "populated"},
                "reason": (
                    None
                    if physical_ops["state"] in {"empty", "populated"}
                    else f"Physical {OPS_RELPATH}/ is {physical_ops['state']}."
                ),
            },
        },
        "conflicts": conflicts,
        "retry_safe": retry_safe,
        "validation_reason": validation_reason,
        "what_remains_usable": {
            "legacy_ops_active": legacy_active,
            "physical_ops_active": physical_active,
            "available_paths": available,
            "unavailable_paths": unavailable,
            "summary": usability_summary,
        },
        "attention": {
            "status": attention_row["status"] if attention_row is not None else "none",
            "created_at": attention_row["created_at"] if attention_row is not None else None,
            "resolved_at": attention_row["resolved_at"] if attention_row is not None else None,
        },
        "timestamps": {
            "started_at": marker.get("started_at") if marker else None,
            "completed_at": marker.get("completed_at") if marker else None,
            "updated_at": marker.get("updated_at") if marker else None,
        },
    }


def _migrate_container_ops_locked(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> bool:
    """Migrate one legacy ``.`` Ops Area. Returns True only when complete."""
    data = get_container(conn, container)
    row = conn.execute(
        "SELECT id, rel_path FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (data["id"],),
    ).fetchone()
    if row is None:
        reason = "Container has no active Ops Area"
        _attention(conn, data, reason)
        return False
    if row["rel_path"] == OPS_RELPATH:
        try:
            validated_area_roots(conn, data, deep_ops_scan=True)
            refresh_registry_projection(conn, data)
        except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
            reason = str(exc)
            _attention(conn, data, reason)
            return False
        _resolve_attention(conn, int(data["id"]))
        return True
    if row["rel_path"] != ".":
        reason = f"unsupported legacy Ops Area path: {row['rel_path']}"
        _attention(conn, data, reason)
        return False

    try:
        validated_area_roots(conn, data, deep_ops_scan=True)
    except (ContainerBoundaryError, OSError) as exc:
        reason = str(exc)
        _upsert_marker(conn, int(data["id"]), "attention", None, reason)
        _attention(conn, data, reason)
        return False

    marker = conn.execute(
        "SELECT status, manifest_json, manifest_hash "
        "FROM container_ops_migrations WHERE container_id = ?",
        (data["id"],),
    ).fetchone()
    manifest: dict[str, Any] | None = None
    resuming = bool(
        marker
        and marker["status"] == "moving"
        and marker["manifest_json"]
    )
    if not resuming:
        try:
            manifest = _build_manifest(conn, data)
        except (ContainerBoundaryError, OSError) as exc:
            reason = str(exc)
            _upsert_marker(conn, int(data["id"]), "attention", None, reason)
            _attention(conn, data, reason)
            return False
        _upsert_marker(conn, int(data["id"]), "planned", manifest)
        _upsert_marker(conn, int(data["id"]), "moving", manifest)

    try:
        current_marker = conn.execute(
            "SELECT status, manifest_json, manifest_hash "
            "FROM container_ops_migrations WHERE container_id = ?",
            (data["id"],),
        ).fetchone()
        manifest = _validated_retry_manifest(
            conn,
            data,
            dict(current_marker) if current_marker is not None else None,
        )
        _upsert_marker(conn, int(data["id"]), "moving", manifest)
        strategy, container_doc, expected_container_doc_hash = (
            _manifest_container_doc(manifest)
        )
        root = Path(str(manifest["container_root"]))
        physical_container_doc = Path(str(manifest["ops_root"])) / CONTAINER_DOC
        with _stable_migration_directories(root) as (
            root_fd,
            physical_fd,
            stable_root,
            stable_physical,
        ):
            if strategy == "generate":
                assert container_doc is not None
                recovery_temp = _manifest_recovery_temp(manifest)
                assert recovery_temp is not None
                _atomic_write_if_missing(
                    physical_container_doc,
                    container_doc,
                    expected_hash=expected_container_doc_hash,
                    recovery_temp=recovery_temp,
                    parent_fd=physical_fd,
                    persist_recovery=lambda: _upsert_marker(
                        conn,
                        int(data["id"]),
                        "moving",
                        manifest,
                    ),
                )
                if (
                    _hash_file_at(physical_fd, CONTAINER_DOC)
                    != expected_container_doc_hash
                ):
                    raise OpsMigrationCollision(
                        "ops/container.md changed after migration planning"
                    )
            _apply_manifest(
                manifest,
                root_fd=root_fd,
                physical_fd=physical_fd,
                stable_root=stable_root,
                stable_physical=stable_physical,
            )
            if (
                _hash_file_at(physical_fd, CONTAINER_DOC)
                != expected_container_doc_hash
            ):
                raise OpsMigrationCollision(
                    "ops/container.md changed during migration"
                )
            exclude_ops_from_root_repo(root, root_fd=root_fd)
            _revalidate_root_identity(root, root_fd)
            _revalidate_physical_identity(root_fd, physical_fd)
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "UPDATE project_areas SET rel_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (OPS_RELPATH, row["id"]),
                )
                validated_area_roots(conn, data, deep_ops_scan=True)
                _revalidate_root_identity(root, root_fd)
                _revalidate_physical_identity(root_fd, physical_fd)
                _upsert_marker(conn, int(data["id"]), "complete", manifest)
                refresh_registry_projection(conn, data)
                _resolve_attention(conn, int(data["id"]))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
        reason = str(exc)
        if manifest is not None:
            _upsert_marker(conn, int(data["id"]), "moving", manifest, reason)
        else:
            conn.execute(
                "UPDATE container_ops_migrations "
                "SET last_error = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE container_id = ?",
                (reason, data["id"]),
            )
        _attention(conn, data, reason)
        return False
    return True


def migrate_container_ops(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> bool:
    """Migrate one legacy ``.`` Ops Area. Returns True only when complete."""
    with container_mutation_lock(conn, container):
        with container_quiescence_lock(conn, container):
            return _migrate_container_ops_locked(conn, container)


def migrate_legacy_ops_containers(conn: sqlite3.Connection) -> dict[str, int]:
    """Run the resumable physical Ops migration for every current Container."""
    summary = {"complete": 0, "attention": 0}
    rows = conn.execute(
        "SELECT id, slug, name, path, path_identity "
        "FROM projects WHERE archived_at IS NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        try:
            if migrate_container_ops(conn, row):
                summary["complete"] += 1
            else:
                summary["attention"] += 1
        except Exception as exc:
            summary["attention"] += 1
            if conn.in_transaction:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            try:
                _attention(conn, get_container(conn, row), str(exc))
            except (ContainerBoundaryError, OSError, sqlite3.Error):
                logger.exception("attention record failed for container %s", row["id"])
    return summary

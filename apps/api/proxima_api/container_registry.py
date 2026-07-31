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
import sqlite3
import stat
import sys
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .auth import iso_now
from .directory_handles import directory_backend

logger = logging.getLogger("proxima.container_registry")
_directory_backend = directory_backend()

OPS_RELPATH = "ops"
CONTAINER_DOC = "container.md"
OPS_MIGRATION_VERSION = 3
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


class ContainerBoundaryError(ValueError):
    """A Container or Area root is missing, ambiguous, or unsafe."""


class OpsMigrationCollision(ContainerBoundaryError):
    """A legacy Ops layout cannot be moved without owner intervention."""


def _database_path(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        raw = row["file"] if isinstance(row, sqlite3.Row) else row[2]
        if name == "main" and raw:
            return Path(str(raw)).resolve()
    return None


def _acquire_file_lock(path: Path) -> int:
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
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
    except Exception:
        os.close(fd)
        raise
    return fd


def _release_file_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def container_mutation_lock(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Iterator[None]:
    data = get_container(conn, container)
    database = _database_path(conn)
    if database is None:
        key = f"memory:{data['id']}:{data['path']}"
        lock_path = None
    else:
        key = f"{database}:{data['id']}"
        lock_path = (
            database.parent
            / f".{database.name}.container-locks"
            / f"{int(data['id'])}.lock"
        )
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


def _planned_recovery_temp(expected_hash: str) -> dict[str, str]:
    return {
        "path": f"{RECOVERY_TEMP_PREFIX}{expected_hash[:16]}.tmp",
        "sha256": expected_hash,
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
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OpsMigrationCollision(f"{name} is not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


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


def _unlink_exact_file_at(
    parent_fd: int,
    name: str,
    expected_hash: str,
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
    recovery_temp: Mapping[str, str] | None = None,
    parent_fd: int | None = None,
) -> None:
    content = text.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    expected_hash = expected_hash or digest
    if digest != expected_hash:
        raise OpsMigrationCollision("planned container.md content hash is invalid")
    recovery = dict(recovery_temp or _planned_recovery_temp(expected_hash))
    if recovery != _planned_recovery_temp(expected_hash):
        raise OpsMigrationCollision("planned container.md recovery file is invalid")
    recovery_name = recovery["path"]
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
            if recovery_state != "missing":
                if not _exact_regular_hash_at(
                    parent_fd, recovery_name, expected_hash
                ):
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                _unlink_exact_file_at(
                    parent_fd,
                    recovery_name,
                    expected_hash,
                )
                os.fsync(parent_fd)
            return
        if recovery_state != "missing":
            if not _exact_regular_hash_at(parent_fd, recovery_name, expected_hash):
                raise OpsMigrationCollision(
                    "container.md recovery file has ambiguous ownership"
                )
        else:
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
                    _publish_anonymous_file(anonymous_fd, parent_fd, path.name)
                    os.fsync(parent_fd)
                    return
                except FileExistsError:
                    if _exact_regular_hash_at(
                        parent_fd, path.name, expected_hash
                    ):
                        return
                    raise OpsMigrationCollision(
                        f"{path.name} appeared with unexpected content"
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
                finally:
                    if anonymous_fd is not None:
                        os.close(anonymous_fd)
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
                _write_all(recovery_fd, content)
            finally:
                os.close(recovery_fd)
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
        _unlink_exact_file_at(parent_fd, recovery_name, expected_hash)
        os.fsync(parent_fd)
    finally:
        if owns_parent_fd:
            os.close(parent_fd)


def _rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
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


def exclude_ops_from_root_repo(root: Path) -> None:
    """Keep physical Ops content out of a root repo's local git status."""
    git_dir = Path(root) / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return
    exclude = git_dir / "info" / "exclude"
    try:
        if exclude.is_symlink():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        lines = existing.splitlines()
        if "/ops/" in lines:
            return
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(f"{existing}{prefix}/ops/\n", encoding="utf-8")
    except OSError:
        return


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_entry(path: Path) -> tuple[str, list[dict[str, str]]]:
    if path.is_symlink():
        raise OpsMigrationCollision(f"legacy Ops path is a symlink: {path.name}")
    if path.is_file():
        digest = _hash_file(path)
        return digest, [{"path": path.name, "sha256": digest}]
    if not path.is_dir():
        raise OpsMigrationCollision(f"legacy Ops path has an unsupported type: {path.name}")
    files: list[dict[str, str]] = []
    aggregate = hashlib.sha256()
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        rel = child.relative_to(path).as_posix()
        if child.is_symlink():
            raise OpsMigrationCollision(f"legacy Ops path contains a symlink: {path.name}/{rel}")
        if child.is_dir():
            aggregate.update(f"D\0{rel}\0".encode())
            continue
        if not child.is_file():
            raise OpsMigrationCollision(f"legacy Ops path has an unsupported entry: {path.name}/{rel}")
        digest = _hash_file(child)
        files.append({"path": rel, "sha256": digest})
        aggregate.update(f"F\0{rel}\0{digest}\0".encode())
    return aggregate.hexdigest(), files


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
        digest, files = _hash_entry(source)
        entries.append(
            {
                "name": name,
                "kind": "directory" if expected_dir else "file",
                "sha256": digest,
                "files": files,
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
        current = (
            stable_root / name
            if source_exists
            else stable_physical / name
        )
        digest, _ = _hash_entry(current)
        if digest != entry["sha256"]:
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
            )
            os.fsync(root_fd)
            os.fsync(physical_fd)
            _revalidate_root_identity(root, root_fd)
            _revalidate_physical_identity(root_fd, physical_fd)
            moved_digest, _ = _hash_entry(stable_physical / name)
            if moved_digest != entry["sha256"]:
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
    internal_files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    state = _path_state(path)
    entries: list[dict[str, str]] = []
    if state == "directory":
        try:
            with os.scandir(path) as children:
                for child in sorted(children, key=lambda item: item.name.casefold()):
                    expected_internal_hash = (
                        internal_files.get(child.name)
                        if internal_files is not None
                        else None
                    )
                    exact_internal_file = False
                    if expected_internal_hash is not None:
                        try:
                            exact_internal_file = (
                                child.is_file(follow_symlinks=False)
                                and not child.is_symlink()
                                and _hash_file(Path(child.path))
                                == expected_internal_hash
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
    if version not in {1, 2}:
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an unsupported version"
        )
    upgraded = json.loads(json.dumps(manifest))
    if version == 2:
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
            planned["recovery_temp"] = _planned_recovery_temp(expected_hash)
        elif planned.get("recovery_temp") is not None:
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid owner container.md"
            )
        upgraded["version"] = OPS_MIGRATION_VERSION
        return upgraded

    entries = upgraded.get("entries")
    if not isinstance(entries, list):
        raise OpsMigrationCollision("stored Ops migration manifest has invalid entries")
    if any(
        isinstance(entry, Mapping) and entry.get("name") == CONTAINER_DOC
        for entry in entries
    ):
        raise OpsMigrationCollision(
            "version 1 Ops migration manifest has ambiguous container.md ownership"
        )
    root = Path(str(upgraded.get("container_root") or ""))
    physical = Path(str(upgraded.get("ops_root") or ""))
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
                "legacy container.md appeared after migration planning"
            )
        if physical_state == "file" and _hash_file(physical_doc) != expected_hash:
            raise OpsMigrationCollision(
                "ops/container.md changed after migration planning"
            )
        planned_doc: dict[str, Any] = {
            "path": CONTAINER_DOC,
            "strategy": "generate",
            "content": content,
            "sha256": expected_hash,
            "recovery_temp": _planned_recovery_temp(expected_hash),
        }
    elif legacy_state == "file":
        digest, files = _hash_entry(legacy_doc)
        entries.append(
            {
                "name": CONTAINER_DOC,
                "kind": "file",
                "sha256": digest,
                "files": files,
            }
        )
        planned_doc = {
            "path": CONTAINER_DOC,
            "strategy": "move",
            "sha256": digest,
        }
    else:
        content = _container_doc_text(
            str(container.get("name") or container.get("slug") or "Container")
        )
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if physical_state == "file" and _hash_file(physical_doc) != expected_hash:
            raise OpsMigrationCollision(
                "existing ops/container.md has ambiguous authority"
            )
        planned_doc = {
            "path": CONTAINER_DOC,
            "strategy": "generate",
            "content": content,
            "sha256": expected_hash,
            "recovery_temp": _planned_recovery_temp(expected_hash),
        }
    upgraded["version"] = OPS_MIGRATION_VERSION
    upgraded["container_doc"] = planned_doc
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
            or not isinstance(recovery_temp, Mapping)
            or dict(recovery_temp) != _planned_recovery_temp(expected_hash)
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
) -> dict[str, str] | None:
    strategy, _, expected_hash = _manifest_container_doc(manifest)
    if strategy != "generate":
        return None
    planned = manifest["container_doc"]
    recovery = dict(planned["recovery_temp"])
    if recovery != _planned_recovery_temp(expected_hash):
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid recovery file"
        )
    return recovery


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
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
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
            if recovery_state != "missing" and (
                recovery_state != "file"
                or _hash_file(recovery_path) != recovery_temp["sha256"]
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
        digest, _ = _hash_entry(current)
        if digest != entry.get("sha256"):
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
    internal_files: dict[str, str] = {}
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
                exact_recovery = (
                    _path_state(recovery_path) == "file"
                    and _hash_file(recovery_path) == recovery_temp["sha256"]
                )
            except OSError:
                exact_recovery = False
            if exact_recovery:
                internal_files[recovery_temp["path"]] = recovery_temp["sha256"]
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
            exclude_ops_from_root_repo(root)
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

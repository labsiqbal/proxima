"""Container root resolution, Ops migration orchestration, and projection.

The existing ``projects`` and ``project_areas`` tables remain the persistence
backbone. Activity leases, platform primitives, and publication are delegated
to their ownership modules.
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
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from . import ops_publication
from . import refusals
from .auth import iso_now
from .directory_handles import directory_backend
from .container_activity import (
    ContainerBoundaryError,
    GuardedWriterTree,
    acquire_container_activity_lease,
    container_mutation_lock,
    container_quiescence_lock,
    process_start_identity,
    recover_container_activity_guardians,
    retain_activity_lease,
)
from .ops_filesystem import (
    OpsMigrationCollision,
    directory_fd_path as _directory_fd_path,
    directory_open_flags as _directory_open_flags,
    identity_matches as _identity_matches,
    publish_open_regular_file as _publish_open_regular_file,
    rename_noreplace as _rename_noreplace,
    same_identity as _same_identity,
    stat_identity as _stat_identity,
    valid_identity as _valid_identity,
    windows_close_handle as _windows_close_handle,
    windows_create_directory_at as _windows_create_directory_at,
    windows_open_directory as _windows_open_directory,
)

logger = logging.getLogger("proxima.container_registry")
_directory_backend = directory_backend()

__all__ = (
    "ContainerBoundaryError",
    "GuardedWriterTree",
    "OpsMigrationCollision",
    "acquire_container_activity_lease",
    "container_mutation_lock",
    "container_quiescence_lock",
    "process_start_identity",
    "recover_container_activity_guardians",
    "retain_activity_lease",
)

OPS_RELPATH = "ops"
CONTAINER_DOC = "container.md"
OPS_MIGRATION_VERSION = 6
# The legacy top-level Ops entries the explicit, previewed opt-in migration
# offers to move into the chosen Ops folder. This list exists ONLY for that
# migration's manifest/inventory - the names carry no routing meaning anywhere
# else (reserved-name virtual rerouting was removed by prune #138; paths mean
# exactly what they say on disk, decision #121).
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
DEFAULT_STARTER_DIRS = ("wiki", "tasks", "artifacts")
MAX_CONTAINER_DOC_BYTES = 64 * 1024
MAX_IDENTITY_LABEL_CHARS = 120
MAX_SUMMARY_CHARS = 500
# Identity comes from the docs the folder already has (prune C5, #137):
# these names are probed at the container root first, then at the Ops root,
# with the legacy ops/container.md still honored after them. None of these
# is required - a folder with no docs falls back to its own name.
IDENTITY_DOC_NAMES = ("AGENTS.md", "README.md", "HANDOFF.md")
IDENTITY_DOC_BASENAMES = frozenset((*IDENTITY_DOC_NAMES, CONTAINER_DOC))
RECOVERY_TEMP_PREFIX = ".proxima-ops-migration-container-"
RETAINED_SOURCE_PREFIX = ".proxima-ops-migration-retained-"


def _platform_is_windows() -> bool:
    return os.name == "nt"


def _as_dict(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def get_container(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(container, int):
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (container,)
        ).fetchone()
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
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_gone",
                "This project has no folder path recorded",
            )
        )
    root = Path(raw)
    try:
        if root.is_symlink():
            raise ContainerBoundaryError(
                refusals.refusal_message(
                    "container_root_symlink",
                    "This project's folder is a symlink, and Proxima never "
                    "anchors a project on a link",
                )
            )
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_gone",
                "This project's folder cannot be reached on disk",
            )
        ) from exc
    if not resolved.is_dir():
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_gone",
                "This project's folder is not on disk any more",
            )
        )
    expected_identity = str(data.get("path_identity") or "").strip()
    if not expected_identity:
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_identity_changed",
                "This project has no recorded folder identity to verify against",
            )
        )
    try:
        verification_root = (
            root.absolute() if _directory_backend.platform == "windows" else resolved
        )
        handle = _directory_backend.open_absolute(verification_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_identity_changed",
                "Proxima cannot read the identity of the folder at this "
                "project's path, so it cannot confirm this is the right one",
            )
        ) from exc
    try:
        if handle.identity != expected_identity:
            raise ContainerBoundaryError(
                refusals.refusal_message(
                    "container_root_identity_changed",
                    "The folder at this project's path is not the one it was "
                    "linked to - it was moved, renamed, or replaced",
                )
            )
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
    """Fail closed on a symlinked Ops root, and optionally on any descendant.

    After prune C7 the DEEP walk means one thing only: "Proxima is about to
    move or create content here, and a move must never traverse a link."  It
    therefore runs at exactly two boundaries - physical Ops root creation and
    the move-based legacy migration (manifest, retry manifest, commit) - and
    nowhere else.  Linking, as-is adoption, the boot settle sweep, layout
    inspection, Area registration, and every read path skip it: they move
    nothing, and ``fsapi`` resolves no-follow on each access anyway, so a
    stray link in the owner's real tree no longer refuses a whole link,
    listing, adoption, or Area change.
    """
    if root.is_symlink():
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "ops_root_symlink",
                "This project's Ops folder is a symlink, and Proxima never "
                "anchors on a link",
            )
        )
    if not deep:
        return
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ContainerBoundaryError(
                    refusals.refusal_message(
                        "ops_root_symlink",
                        "This project's Ops folder contains a symlink "
                        f"({path.relative_to(root).as_posix()}), and moving "
                        "content across a link is refused",
                    )
                )
    except OSError as exc:
        raise ContainerBoundaryError(
            f"physical Ops root cannot be inspected: {exc}"
        ) from exc


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
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_no_ops_area",
                "This project does not have exactly one active Ops folder",
            )
        )

    resolved: dict[int, Path] = {}
    by_root: dict[Path, sqlite3.Row] = {}
    for row in rows:
        rel = _safe_rel_path(row["rel_path"])
        candidate = root if rel.as_posix() == "." else root.joinpath(*rel.parts)
        if row["kind"] == "ops" and rel.as_posix() != ".":
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
            if (
                left_root not in right_root.parents
                and right_root not in left_root.parents
            ):
                continue
            pair = {left["kind"], right["kind"]}
            rels = {left["rel_path"], right["rel_path"]}
            root_repo_with_ops = pair == {"code", "ops"} and "." in rels
            legacy_ops = (left["kind"] == "ops" and left["rel_path"] == ".") or (
                right["kind"] == "ops" and right["rel_path"] == "."
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
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_no_ops_area",
                "This project has no active Ops folder",
            )
        )
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


def detect_ops_path(root: Path) -> str:
    """The link-time default for a Container's Ops path (prune C3).

    A real ``ops/`` directory on disk is the detected default; anything else
    (missing, file, symlink) falls back to the Container root ``.``. The
    result is only a default - the owner may override it at link time, and
    the chosen value persists on the Ops Area row.
    """
    candidate = Path(root) / OPS_RELPATH
    try:
        if candidate.is_dir() and not candidate.is_symlink():
            return OPS_RELPATH
    except OSError:
        pass
    return "."


def validate_ops_path_choice(root: Path, raw: str) -> str:
    """Validate an owner-chosen Ops path against the real folder.

    Returns the normalized relative path (``.`` for the Container root).
    Raises :class:`ContainerBoundaryError` when the choice cannot be an Ops
    root: unsafe/escaping paths, or a missing/non-directory/symlinked target.
    A symlink *inside* the chosen folder no longer refuses the link (prune
    C7): linking writes nothing, and every later access is no-follow, so the
    stray link is skipped in listings instead of bricking adoption (#131).
    """
    rel = _safe_rel_path(raw)
    normalized = rel.as_posix()
    if normalized == ".":
        return "."
    candidate = Path(root).joinpath(*rel.parts)
    state = _path_state(candidate)
    if state == "symlink":
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "ops_root_symlink",
                "The chosen Ops folder is a symlink, and Proxima never anchors "
                "on a link",
            )
        )
    if state != "directory":
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "ops_root_missing",
                "The chosen Ops folder does not exist inside this project",
            )
        )
    _reject_symlinks(candidate, deep=False)
    return normalized


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
    }


def _create_physical_ops_root_windows(
    container: Path,
    starter_dirs: tuple[str, ...],
) -> Path:
    physical = container / OPS_RELPATH
    root_handle, root_identity = _windows_open_directory(container)
    physical_handle: int | None = None
    try:
        physical_handle, physical_identity = _windows_create_directory_at(
            root_handle,
            OPS_RELPATH,
        )
        for dirname in starter_dirs:
            rel = _safe_rel_path(dirname)
            if not rel.parts:
                raise ContainerBoundaryError(f"Ops starter path is unsafe: {dirname!r}")
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
        # No identity document is generated (prune C5, #137): identity is
        # read from the docs the folder already has, so a fresh Ops root
        # carries only its starter directories.
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
    if not stat.S_ISDIR(current.st_mode) or not _same_identity(
        current, os.fstat(physical_fd)
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
        if persist_recovery is None:
            if target_state != "missing":
                if not _exact_regular_hash_at(
                    parent_fd,
                    path.name,
                    expected_hash,
                ):
                    raise OpsMigrationCollision(
                        f"{path.name} exists with unexpected content or type"
                    )
                return
            anonymous_fd: int | None = None
            if hasattr(os, "O_TMPFILE"):
                try:
                    anonymous_fd = os.open(
                        ".",
                        os.O_TMPFILE | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=parent_fd,
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
            if anonymous_fd is not None:
                try:
                    _write_all(anonymous_fd, content)
                    os.fsync(anonymous_fd)
                    _publish_anonymous_file(
                        anonymous_fd,
                        parent_fd,
                        path.name,
                    )
                    os.fsync(parent_fd)
                finally:
                    os.close(anonymous_fd)
                return
            flags = (
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            target_fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
            try:
                _write_all(target_fd, content)
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
            os.fsync(parent_fd)
            return
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
            if recovery["phase"] in {"creating", "created"}:
                raise OpsMigrationCollision(
                    "legacy recovery artifact has ambiguous ownership"
                )
            if not _identity_matches(recovery["identity"], recovery_stat):
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
            if recovery["phase"] == "prepared":
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
                        os.O_TMPFILE | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    _write_all(anonymous_fd, content)
                    os.fsync(anonymous_fd)
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
                        and _path_state_at(parent_fd, recovery_name) == "missing"
                    ):
                        advance("planned", None)
                finally:
                    if anonymous_fd is not None:
                        os.close(anonymous_fd)
            if recovery["phase"] == "planned":
                raise OpsMigrationCollision(
                    "this filesystem cannot publish recovery data anonymously"
                )
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
        advance("complete", _stat_identity(target_stat))
    finally:
        if owns_parent_fd:
            os.close(parent_fd)


def create_physical_ops_root(
    root: Path,
    starter_dirs: tuple[str, ...] = DEFAULT_STARTER_DIRS,
) -> Path:
    """Create the physical Ops layout for a fresh Container."""
    raw_root = Path(root)
    if raw_root.is_symlink():
        raise ContainerBoundaryError(
                refusals.refusal_message(
                    "container_root_symlink",
                    "This project's folder is a symlink, and Proxima never "
                    "anchors a project on a link",
                )
            )
    if _platform_is_windows():
        if not raw_root.is_dir():
            raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_gone",
                "This project's folder is not on disk any more",
            )
        )
        return _create_physical_ops_root_windows(
            raw_root.absolute(),
            starter_dirs,
        )
    container = raw_root.resolve()
    if not container.is_dir():
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "container_root_gone",
                "This project's folder is not on disk any more",
            )
        )
    physical = container / OPS_RELPATH
    if physical.is_symlink():
        raise ContainerBoundaryError(
            refusals.refusal_message(
                "ops_root_symlink",
                "This project's Ops folder is a symlink, and Proxima never "
                "anchors on a link",
            )
        )
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
    # No ops/container.md is generated and no existing file is inspected
    # (prune C5, #137): identity comes from the docs the folder already has,
    # so Proxima has no metadata requirement to enforce here.
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


def _snapshot_content_matches(
    snapshot: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return (
        snapshot.get("kind") == expected.get("kind")
        and snapshot.get("sha256") == expected.get("sha256")
        and [
            {
                "path": item.get("path"),
                "sha256": item.get("sha256"),
            }
            for item in snapshot.get("files") or []
            if isinstance(item, Mapping)
        ]
        == [
            {
                "path": item.get("path"),
                "sha256": item.get("sha256"),
            }
            for item in expected.get("files") or []
            if isinstance(item, Mapping)
        ]
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
        path == OPS_RELPATH or path.startswith(f"{OPS_RELPATH}/") for path in code_paths
    ):
        raise OpsMigrationCollision(
            "the requested physical Ops root is an active repo Area"
        )

    entries: list[dict[str, Any]] = []
    for name in (*KNOWN_OPS_DIRS, *KNOWN_OPS_FILES):
        source = root / name
        if not source.exists() and not source.is_symlink():
            continue
        if name in code_paths or any(
            path.startswith(f"{name}/") for path in code_paths
        ):
            raise OpsMigrationCollision(f"legacy Ops path overlaps a repo Area: {name}")
        destination = physical / name
        if destination.exists() or destination.is_symlink():
            raise OpsMigrationCollision(f"destination already exists: ops/{name}")
        expected_dir = name in KNOWN_OPS_DIRS
        if expected_dir and not source.is_dir():
            raise OpsMigrationCollision(
                f"legacy Ops directory has an unexpected type: {name}"
            )
        if not expected_dir and not source.is_file():
            raise OpsMigrationCollision(
                f"legacy Ops file has an unexpected type: {name}"
            )
        snapshot = _entry_snapshot(source)
        entries.append(
            {
                "name": name,
                "kind": "directory" if expected_dir else "file",
                "sha256": snapshot["sha256"],
                "files": snapshot["files"],
                "nodes": snapshot["nodes"],
                "identity": snapshot["identity"],
                "publication": {
                    "phase": "planned",
                    "retained_name": (
                        f"{RETAINED_SOURCE_PREFIX}{secrets.token_hex(16)}-{name}"
                    ),
                    "destination_snapshot": None,
                    "destination_directories": {},
                },
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
        # No identity document is generated anymore (prune C5, #137): a
        # migration without a legacy container.md plans NO container.md at
        # all. (Stored manifests with the historical "generate" strategy
        # still execute; only new plans stopped imposing the file.)
        container_doc = {"path": CONTAINER_DOC, "strategy": "none"}
    return {
        "version": OPS_MIGRATION_VERSION,
        "container_root": str(root),
        "ops_root": str(physical),
        "entries": entries,
        "container_doc": container_doc,
    }


def _adoption_inventory(
    conn: sqlite3.Connection,
    container: Mapping[str, Any],
    *,
    rel_path: str = OPS_RELPATH,
    allow_empty: bool = False,
) -> list[dict[str, str]] | None:
    """Inventory a pre-existing Ops folder that can be adopted as-is.

    A project is the folder as it exists on disk: when the owner links a
    folder whose Ops folder (the detected ``ops/`` default, or a per-project
    path chosen at link time - prune C3) already exists, Proxima adopts that
    content instead of demanding an empty directory (prune C1). Returns the
    top-level inventory when ``root/<rel_path>`` is a real directory; returns
    None when there is nothing to adopt (missing, or empty unless
    ``allow_empty`` - the link-time flow adopts an empty chosen folder too,
    while the legacy sweep keeps the move-based migration available for empty
    ``ops/``). Raises for layouts that stay fail-closed: a symlinked or
    non-directory target, unreadable content, or overlap with an active repo
    Area. A *descendant* symlink no longer refuses adoption (prune C7):
    adoption writes nothing, so the link is simply skipped when the folder is
    later read - which is what unblocks the owner's real client folders (#131).
    """
    root = container_root(container)
    physical = root.joinpath(*_safe_rel_path(rel_path).parts)
    state = _path_state(physical)
    if state == "missing":
        return None
    if state == "symlink":
        raise OpsMigrationCollision("physical Ops root is a symlink")
    if state != "directory":
        raise OpsMigrationCollision("physical Ops root collides with a non-directory")
    code_paths = {
        str(row["rel_path"])
        for row in conn.execute(
            "SELECT rel_path FROM project_areas "
            "WHERE project_id = ? AND kind = 'code' AND source != 'excluded'",
            (container["id"],),
        ).fetchall()
    }
    if any(
        path == rel_path or path.startswith(f"{rel_path}/") for path in code_paths
    ):
        raise OpsMigrationCollision(
            "the requested physical Ops root is an active repo Area"
        )
    inventory = _physical_ops_state(physical, rel_path=rel_path)
    if inventory["state"] == "empty" and not allow_empty:
        return None
    if inventory["state"] not in {"populated", "empty"}:
        raise OpsMigrationCollision("physical Ops root cannot be inspected")
    return list(inventory["entries"])


def _adoption_manifest(
    container: Mapping[str, Any],
    inventory: list[dict[str, str]],
    *,
    rel_path: str = OPS_RELPATH,
) -> dict[str, Any]:
    """The durable record of an as-is adoption: what existed, nothing planned."""
    root = container_root(container)
    return {
        "version": OPS_MIGRATION_VERSION,
        "mode": "adopted",
        "container_root": str(root),
        "ops_root": str(root.joinpath(*_safe_rel_path(rel_path).parts)),
        "entries": [],
        "inventory": inventory,
    }


def _upsert_marker(
    conn: sqlite3.Connection,
    container_id: int,
    status: str,
    manifest: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    manifest_json = (
        json.dumps(manifest, sort_keys=True) if manifest is not None else None
    )
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


_hash_open_regular_file = ops_publication.hash_open_regular_file


def _publish_bound_file_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
    expected_identity: Mapping[str, Any],
    expected_hash: str,
) -> None:
    ops_publication.publish_bound_file_at(
        source_parent_fd,
        source_name,
        destination_parent_fd,
        destination_name,
        expected_identity,
        expected_hash,
        publish_open_file=_publish_open_regular_file,
    )


def _publish_bound_directory_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
    entry: Mapping[str, Any],
    publication: dict[str, Any],
    *,
    persist_manifest: Callable[[], None],
) -> None:
    ops_publication.publish_bound_directory_at(
        source_parent_fd,
        source_name,
        destination_parent_fd,
        destination_name,
        entry,
        publication,
        persist_manifest=persist_manifest,
        publish_open_file=_publish_open_regular_file,
    )


def _apply_manifest(
    manifest: Mapping[str, Any],
    *,
    root_fd: int,
    physical_fd: int,
    persist_manifest: Callable[[], None],
) -> Path:
    root = Path(str(manifest["container_root"]))
    physical = Path(str(manifest["ops_root"]))
    _revalidate_root_identity(root, root_fd)
    _revalidate_physical_identity(root_fd, physical_fd)

    for entry in manifest["entries"]:
        name = str(entry["name"])
        source = root / name
        publication = entry["publication"]
        retained_name = str(publication["retained_name"])
        source_state = _path_state_at(root_fd, name)
        destination_state = _path_state_at(physical_fd, name)
        source_exists = source_state != "missing"
        destination_exists = destination_state != "missing"
        if publication["phase"] == "complete":
            if source_exists or not destination_exists:
                raise OpsMigrationCollision(f"published Ops layout changed: {name}")
            destination_snapshot = _entry_snapshot_at(physical_fd, name)
            if not _snapshot_matches(
                destination_snapshot,
                publication["destination_snapshot"],
            ):
                raise OpsMigrationCollision(f"published Ops content changed: {name}")
            continue
        if not source_exists:
            if (
                publication["phase"] == "ready"
                and destination_exists
                and _path_state_at(physical_fd, retained_name) != "missing"
            ):
                if not _snapshot_matches(
                    _entry_snapshot_at(physical_fd, name),
                    publication["destination_snapshot"],
                ):
                    raise OpsMigrationCollision(
                        f"published Ops content changed: {name}"
                    )
                publication["phase"] = "complete"
                persist_manifest()
                continue
            raise OpsMigrationCollision(
                f"migration source is missing before publication: {name}"
            )
        expected_state = "directory" if entry["kind"] == "directory" else "file"
        if publication["phase"] in {"planned", "publishing"} and (
            source_state != expected_state
            or not _snapshot_matches(
                _entry_snapshot_at(root_fd, name),
                entry,
            )
        ):
            raise OpsMigrationCollision(
                f"content changed after migration planning: {name}"
            )
        if (
            os.stat(name, dir_fd=root_fd, follow_symlinks=False).st_dev
            != os.fstat(physical_fd).st_dev
        ):
            raise OpsMigrationCollision(
                f"source and destination are on different filesystems: {name}"
            )
        if publication["phase"] in {"planned", "publishing"}:
            if publication["phase"] == "planned":
                publication["phase"] = "publishing"
                persist_manifest()
            _revalidate_root_identity(root, root_fd)
            _revalidate_physical_identity(root_fd, physical_fd)
            if entry["kind"] == "file":
                _publish_bound_file_at(
                    root_fd,
                    name,
                    physical_fd,
                    name,
                    entry["identity"],
                    str(entry["sha256"]),
                )
            else:
                _publish_bound_directory_at(
                    root_fd,
                    name,
                    physical_fd,
                    name,
                    entry,
                    publication,
                    persist_manifest=persist_manifest,
                )
            os.fsync(physical_fd)
            _revalidate_root_identity(root, root_fd)
            _revalidate_physical_identity(root_fd, physical_fd)
            destination_snapshot = _entry_snapshot_at(physical_fd, name)
            if not _snapshot_content_matches(destination_snapshot, entry):
                raise OpsMigrationCollision(
                    f"content hash changed during publication: {name}"
                )
            publication["destination_snapshot"] = destination_snapshot
            publication["phase"] = "ready"
            persist_manifest()
        if publication["phase"] != "ready":
            raise OpsMigrationCollision(f"Ops publication state is invalid: {name}")
        destination_snapshot = _entry_snapshot_at(physical_fd, name)
        if not _snapshot_matches(
            destination_snapshot,
            publication["destination_snapshot"],
        ):
            raise OpsMigrationCollision(f"published Ops content changed: {name}")
        if _path_state_at(physical_fd, retained_name) != "missing":
            raise OpsMigrationCollision(
                f"retained migration source already exists: {retained_name}"
            )
        if not _snapshot_matches(
            _entry_snapshot_at(root_fd, name),
            entry,
        ):
            raise OpsMigrationCollision(
                f"migration source changed before retention: {name}"
            )
        try:
            _rename_noreplace(
                source,
                physical / retained_name,
                source_dir_fd=root_fd,
                destination_dir_fd=physical_fd,
            )
        except FileNotFoundError:
            pass
        os.fsync(root_fd)
        os.fsync(physical_fd)
        if _path_state_at(root_fd, name) != "missing":
            raise OpsMigrationCollision(
                f"legacy Ops path reappeared during publication: {name}"
            )
        if not _snapshot_matches(
            _entry_snapshot_at(physical_fd, name),
            publication["destination_snapshot"],
        ):
            raise OpsMigrationCollision(f"published Ops content changed: {name}")
        publication["phase"] = "complete"
        persist_manifest()
    return physical


def _parse_identity_doc(text: str) -> tuple[str | None, str | None]:
    """Label + one-line summary from a doc's OPTIONAL frontmatter, else its
    heading and first body line. Nothing here is ever required (prune C5)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    identity: str | None = None
    summary: str | None = None
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            for line in text[4:end].splitlines():
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                key = key.strip().lower()
                if key in ("identity", "title", "name") and identity is None:
                    identity = value.strip()[:MAX_IDENTITY_LABEL_CHARS] or None
                elif key in ("summary", "description") and summary is None:
                    summary = value.strip()[:MAX_SUMMARY_CHARS] or None
            body = text[end + len("\n---\n"):]
    if identity is None:
        identity = next(
            (
                line[2:].strip()[:MAX_IDENTITY_LABEL_CHARS] or None
                for line in body.splitlines()
                if line.startswith("# ")
            ),
            None,
        )
    if summary is None:
        summary = next(
            (
                line.strip()[:MAX_SUMMARY_CHARS]
                for line in body.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ),
            None,
        )
    return identity, summary


def _identity_doc_candidates(
    root: Path,
    ops: Path | None,
) -> list[tuple[str, Path]]:
    """(container-root-relative label, absolute path) probe order: each known
    doc name at the container root, then under the Ops root, with the legacy
    ops/container.md honored last."""
    candidates: list[tuple[str, Path]] = []
    ops_rel: str | None = None
    if ops is not None and ops != root:
        try:
            ops_rel = ops.relative_to(root).as_posix()
        except ValueError:
            ops_rel = None
    for name in IDENTITY_DOC_NAMES:
        candidates.append((name, root / name))
        if ops_rel is not None:
            candidates.append((f"{ops_rel}/{name}", ops / name))
    if ops_rel is not None:
        candidates.append((f"{ops_rel}/{CONTAINER_DOC}", ops / CONTAINER_DOC))
    else:
        candidates.append((CONTAINER_DOC, root / CONTAINER_DOC))
    return candidates


def resolve_container_identity(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> tuple[str | None, str | None, str | None, str]:
    """(identity_label, summary, source_rel, source_hash) from the docs the
    folder already has - AGENTS.md / README.md / HANDOFF.md (root first, then
    the Ops root), legacy ops/container.md after them. No Proxima frontmatter
    is required anywhere: a doc's optional frontmatter is honored, else its
    heading/first line, and a folder with no docs at all is identified by its
    own name (decision #121, prune C5). Read-only toward the tree."""
    data = get_container(conn, container)
    root = container_root(data)
    try:
        ops: Path | None = ops_root(conn, data)
    except ContainerBoundaryError:
        ops = None
    for rel, path in _identity_doc_candidates(root, ops):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            with path.open("rb") as handle:
                raw = handle.read(MAX_CONTAINER_DOC_BYTES)
        except OSError:
            continue
        identity, summary = _parse_identity_doc(
            raw.decode("utf-8", errors="replace")
        )
        if identity is None and summary is None:
            continue
        digest = hashlib.sha256()
        digest.update(rel.encode("utf-8", errors="replace"))
        digest.update(b"\x00")
        digest.update(raw)
        if identity is None:
            identity = _folder_name_identity(data)
        return identity, summary, rel, digest.hexdigest()
    # No docs anywhere: the folder's own name IS the identity. The hash keeps
    # the projection "ready" and change-detection working (it flips as soon
    # as a doc appears).
    label = _folder_name_identity(data)
    fallback = hashlib.sha256(
        b"proxima-identity-folder-name\x00" + label.encode("utf-8", errors="replace")
    ).hexdigest()
    return label, None, None, fallback


def _folder_name_identity(data: Mapping[str, Any]) -> str:
    name = Path(str(data.get("path") or "")).name or str(data.get("name") or "")
    return name.strip()[:MAX_IDENTITY_LABEL_CHARS] or "Container"


def refresh_registry_projection(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> None:
    data = get_container(conn, container)
    identity, summary, source_rel, source_hash = resolve_container_identity(
        conn, data
    )
    conn.execute(
        """
        INSERT INTO container_registry(
          container_id, identity_label, summary, identity_source, source_hash,
          indexed_at, last_activity_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?,
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
          identity_source = CASE
            WHEN excluded.source_hash IS NOT container_registry.source_hash
              THEN excluded.identity_source
            ELSE container_registry.identity_source
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
            source_rel,
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
      CASE WHEN kind = 'ops' AND rel_path != '.' THEN 1 ELSE 0 END
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
  cr.identity_source,
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
        "identity_source": data.get("identity_source"),
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


BINDING_BOUND = "bound"
BINDING_MISSING = "missing"
BINDING_MOVED = "moved"
BINDING_UNAVAILABLE = "unavailable"


def container_binding(container: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    """Classify the link between a Container record and its folder on disk.

    The read-only basis of the relocate flow (prune C6, #141). A folder that
    was moved, renamed, deleted, or restored in place used to turn every
    project operation into a raw ``ContainerBoundaryError`` with no way out
    (audit #120 part 2, item 6). Classifying the binding turns that dead end
    into a state the UI can act on:

    - ``bound`` - the folder is where the record says, with the same
      filesystem identity it was pinned to.
    - ``missing`` - nothing is at the stored path any more (moved/renamed).
    - ``moved`` - something IS at the stored path, but it is not the same
      directory (restored from backup, recreated, or a different folder).
    - ``unavailable`` - the path cannot be inspected here (permissions, or a
      non-directory now occupies it).

    Costs one ``lstat`` plus one directory-handle open; never writes.
    """
    data = _as_dict(container)
    path = str(data.get("path") or "")
    try:
        container_root(data)
        return {"state": BINDING_BOUND, "path": path, "message": ""}
    except ContainerBoundaryError as exc:
        reason = str(exc)
    state = _path_state(Path(path)) if path else "missing"
    if state == "missing":
        return {
            "state": BINDING_MISSING,
            "path": path,
            "message": (
                "This project's folder is no longer at its stored location. "
                "Point Proxima at its new location, or unlink the project."
            ),
        }
    if state == "directory":
        return {
            "state": BINDING_MOVED,
            "path": path,
            "message": (
                "A different folder now sits at this project's stored "
                "location. Confirm where the project lives to re-pin it."
            ),
        }
    return {
        "state": BINDING_UNAVAILABLE,
        "path": path,
        "message": f"This project's folder cannot be used: {reason}.",
    }


def compatibility_project_payload(
    container: Mapping[str, Any],
    areas: Mapping[str, Any],
    *,
    location: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the one-release Project reader alias from Container truth.

    ``location`` is the folder-binding state (prune C6): passed in by callers
    that already computed it for a whole list, else derived from this row.
    A caller holding a projection WITHOUT ``path_identity`` must pass it -
    identity cannot be verified from a partial row.
    """
    data = _as_dict(container)
    return {
        "slug": data["slug"],
        "name": data["name"],
        "path": data["path"],
        "owner": data.get("owner"),
        "visibility": data.get("visibility") or "private",
        "code_areas": list(areas.get("code_areas") or []),
        "ops_area": areas.get("ops_area"),
        "location": dict(location) if location is not None else container_binding(data),
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
    # The Fleet projection carries no path_identity (it is internal), so the
    # folder-binding state is computed from the owner's project rows once for
    # the whole list (prune C6): two syscalls per project, never a write.
    bindings = {
        int(row["id"]): container_binding(row)
        for row in conn.execute(
            "SELECT id, path, path_identity FROM projects "
            "WHERE owner_user_id = ? AND archived_at IS NULL",
            (owner_user_id,),
        ).fetchall()
    }
    return [
        compatibility_project_payload(
            container,
            areas_by_container[int(container["id"])],
            location=bindings.get(int(container["id"])),
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
    *,
    rel_path: str = OPS_RELPATH,
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
                    entries.append(
                        {"path": f"{rel_path}/{child.name}", "kind": kind}
                    )
        except OSError:
            state = "unavailable"
            entries = []
        else:
            state = "populated" if entries else "empty"
    return {"path": rel_path, "state": state, "entries": entries}


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


def _snapshot_directory_identities(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if snapshot.get("kind") != "directory":
        return {}
    identities: dict[str, Any] = {
        ".": snapshot.get("identity"),
    }
    for node in snapshot.get("nodes") or []:
        if (
            isinstance(node, Mapping)
            and node.get("kind") == "directory"
            and isinstance(node.get("path"), str)
        ):
            identities[str(node["path"])] = node.get("identity")
    return identities


def _upgrade_publication_directory_identities(
    manifest: dict[str, Any],
    physical: Path,
) -> None:
    for entry in manifest.get("entries") or []:
        if not isinstance(entry, dict):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
            )
        publication = entry.get("publication")
        if not isinstance(publication, dict):
            continue
        if entry.get("kind") != "directory":
            publication["destination_directories"] = {}
            continue
        phase = publication.get("phase")
        destination = physical / str(entry.get("name") or "")
        if phase == "planned":
            if _path_state(destination) != "missing":
                raise OpsMigrationCollision(
                    "legacy directory publication has ambiguous ownership"
                )
            publication["destination_directories"] = {}
            continue
        if phase == "publishing":
            if _path_state(destination) != "missing":
                raise OpsMigrationCollision(
                    "legacy directory publication has ambiguous ownership"
                )
            publication["destination_directories"] = {}
            continue
        if phase in {"ready", "complete"}:
            snapshot = publication.get("destination_snapshot")
            if not isinstance(snapshot, Mapping):
                raise OpsMigrationCollision(
                    "legacy directory publication has no destination identity"
                )
            publication["destination_directories"] = _snapshot_directory_identities(
                snapshot
            )
            continue
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid publication"
        )


def _upgrade_manifest(
    container: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    version = manifest.get("version")
    if version == OPS_MIGRATION_VERSION:
        return dict(manifest)
    if version not in {1, 2, 3, 4, 5}:
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an unsupported version"
        )
    upgraded = json.loads(json.dumps(manifest))
    root = Path(str(upgraded.get("container_root") or ""))
    physical = Path(str(upgraded.get("ops_root") or ""))
    entries = upgraded.get("entries")
    if not isinstance(entries, list):
        raise OpsMigrationCollision("stored Ops migration manifest has invalid entries")

    if version in {4, 5}:
        planned = upgraded.get("container_doc")
        if not isinstance(planned, dict):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has no planned container.md"
            )
        if version == 4 and planned.get("strategy") == "generate":
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
        elif planned.get("strategy") not in {"generate", "move"}:
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid planned container.md"
            )
        _upgrade_publication_directory_identities(
            upgraded,
            physical,
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
        if strategy not in {"generate", "move"} or not isinstance(expected_hash, str):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid planned container.md"
            )
        if strategy == "generate":
            content = planned.get("content")
            if (
                not isinstance(content, str)
                or hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash
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

    _upgrade_publication_directory_identities(
        upgraded,
        physical,
    )
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
    if planned.get("path") != CONTAINER_DOC or strategy not in {
        "generate",
        "move",
        "none",
    }:
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid planned container.md"
        )
    doc_entries = [
        entry
        for entry in manifest.get("entries") or []
        if isinstance(entry, Mapping) and entry.get("name") == CONTAINER_DOC
    ]
    if strategy == "none":
        # Prune C5: nothing is planned for container.md - no content, no
        # recovery artifact, no bound owner document.
        if (
            content is not None
            or expected_hash is not None
            or planned.get("recovery_temp") is not None
            or doc_entries
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid planned container.md"
            )
        return strategy, None, ""
    if not isinstance(expected_hash, str):
        raise OpsMigrationCollision(
            "stored Ops migration manifest has an invalid planned container.md"
        )
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
            or (phase not in {"planned", "creating"} and _valid_identity(identity))
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


def _ensure_publication_plans(
    manifest: dict[str, Any],
    root: Path,
    physical: Path,
) -> None:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict) or isinstance(
            entry.get("publication"),
            Mapping,
        ):
            continue
        name = str(entry.get("name") or "")
        source_state = _path_state(root / name)
        destination_state = _path_state(physical / name)
        if source_state != "missing" and destination_state == "missing":
            phase = "planned"
            destination_snapshot = None
        elif source_state == "missing" and destination_state != "missing":
            phase = "complete"
            destination_snapshot = _entry_snapshot(physical / name)
        else:
            raise OpsMigrationCollision(
                f"legacy manifest path has ambiguous ownership: {name}"
            )
        entry["publication"] = {
            "phase": phase,
            "retained_name": (
                f"{RETAINED_SOURCE_PREFIX}{secrets.token_hex(16)}-{name}"
            ),
            "destination_snapshot": destination_snapshot,
            "destination_directories": (
                _snapshot_directory_identities(destination_snapshot)
                if isinstance(destination_snapshot, Mapping)
                else {}
            ),
        }


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_snapshot_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _valid_manifest_snapshot(value: Any) -> bool:
    if (
        not isinstance(value, Mapping)
        or value.get("kind") not in {"directory", "file"}
        or not _valid_sha256(value.get("sha256"))
        or not _valid_identity(value.get("identity"))
        or not isinstance(value.get("files"), list)
        or not isinstance(value.get("nodes"), list)
    ):
        return False
    file_paths: set[str] = set()
    for file_entry in value["files"]:
        path = file_entry.get("path") if isinstance(file_entry, Mapping) else None
        if (
            not isinstance(file_entry, Mapping)
            or not _valid_snapshot_path(path)
            or path in file_paths
            or not _valid_sha256(file_entry.get("sha256"))
            or not _valid_identity(file_entry.get("identity"))
        ):
            return False
        file_paths.add(str(path))
    node_paths: set[str] = set()
    for node in value["nodes"]:
        path = node.get("path") if isinstance(node, Mapping) else None
        if (
            not isinstance(node, Mapping)
            or not _valid_snapshot_path(path)
            or path in node_paths
            or node.get("kind") not in {"directory", "file"}
            or not _valid_identity(node.get("identity"))
        ):
            return False
        node_paths.add(str(path))
    return True


def _validate_manifest_entries(manifest: Mapping[str, Any]) -> None:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise OpsMigrationCollision("stored Ops migration manifest has invalid entries")
    seen: set[str] = set()
    retained_seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
            )
        publication = entry.get("publication")
        retained_name = (
            publication.get("retained_name")
            if isinstance(publication, Mapping)
            else None
        )
        destination_snapshot = (
            publication.get("destination_snapshot")
            if isinstance(publication, Mapping)
            else None
        )
        if (
            not isinstance(publication, Mapping)
            or publication.get("phase")
            not in {"planned", "publishing", "ready", "complete"}
            or not isinstance(retained_name, str)
            or not retained_name.startswith(RETAINED_SOURCE_PREFIX)
            or "/" in retained_name
            or "\\" in retained_name
            or retained_name in retained_seen
            or (
                publication.get("phase") in {"ready", "complete"}
                and not _valid_manifest_snapshot(destination_snapshot)
            )
            or (
                publication.get("phase") in {"planned", "publishing"}
                and destination_snapshot is not None
            )
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid publication"
            )
        name = entry.get("name")
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
            or name in seen
            or entry.get("kind") not in {"directory", "file"}
            or not _valid_manifest_snapshot(entry)
        ):
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an invalid entry"
            )
        directory_identities = publication.get("destination_directories")
        if (
            not isinstance(directory_identities, Mapping)
            or any(
                not _valid_snapshot_path(path) or not _valid_identity(identity)
                for path, identity in directory_identities.items()
            )
            or (entry.get("kind") == "file" and bool(directory_identities))
            or (publication.get("phase") == "planned" and bool(directory_identities))
        ):
            raise OpsMigrationCollision(
                "stored Ops migration has invalid directory ownership"
            )
        if entry.get("kind") == "directory":
            expected_paths = set(_snapshot_directory_identities(entry))
            if not set(directory_identities).issubset(expected_paths):
                raise OpsMigrationCollision(
                    "stored Ops migration has unplanned directory ownership"
                )
            if publication.get("phase") in {"ready", "complete"}:
                expected_destination = (
                    _snapshot_directory_identities(destination_snapshot)
                    if isinstance(
                        destination_snapshot,
                        Mapping,
                    )
                    else {}
                )
                if dict(directory_identities) != expected_destination:
                    raise OpsMigrationCollision(
                        "stored Ops migration has incomplete directory ownership"
                    )
        seen.add(name)
        retained_seen.add(retained_name)


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
            raise OpsMigrationCollision(
                "stored Ops migration manifest is invalid"
            ) from exc
        if _manifest_digest(manifest) != marker.get("manifest_hash"):
            raise OpsMigrationCollision(
                "stored Ops migration manifest failed its integrity check"
            )
        if manifest.get("container_root") != str(root) or manifest.get(
            "ops_root"
        ) != str(physical):
            raise OpsMigrationCollision(
                "stored Ops migration manifest no longer matches this Container"
            )
        manifest = _upgrade_manifest(container, manifest)
    else:
        manifest = _build_manifest(conn, container)

    _ensure_publication_plans(manifest, root, physical)
    _validate_manifest_entries(manifest)
    validated_area_roots(conn, container, deep_ops_scan=True)
    _validate_manifest_area_ownership(conn, container, manifest)

    physical_state = _path_state(physical)
    if physical_state == "symlink":
        raise OpsMigrationCollision("physical Ops root is a symlink")
    if physical_state not in {"missing", "directory"}:
        raise OpsMigrationCollision("physical Ops root collides with a non-directory")

    allowed_physical = {
        str(entry.get("name") or "") for entry in manifest.get("entries") or []
    } | {CONTAINER_DOC}
    allowed_physical.update(
        str(entry["publication"]["retained_name"])
        for entry in manifest.get("entries") or []
        if isinstance(entry, Mapping) and isinstance(entry.get("publication"), Mapping)
    )
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
                if phase in {"creating", "created"}:
                    raise OpsMigrationCollision(
                        "legacy recovery artifact has ambiguous ownership"
                    )
                recovery_stat = recovery_path.lstat()
                if not _identity_matches(
                    recovery_temp["identity"],
                    recovery_stat,
                ):
                    raise OpsMigrationCollision(
                        "container.md recovery file has ambiguous ownership"
                    )
                if _hash_file(recovery_path) != recovery_temp["sha256"]:
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
    if container_doc_state == "file" and (
        container_doc_strategy == "none"
        or _hash_file(container_doc_path) != expected_container_doc_hash
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
        container_doc_strategy in {"generate", "none"}
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
            raise OpsMigrationCollision(
                "stored Ops migration manifest has an unsafe path"
            )
        source = root / name
        destination = physical / name
        source_state = _path_state(source)
        destination_state = _path_state(destination)
        source_exists = source_state != "missing"
        destination_exists = destination_state != "missing"
        publication = entry["publication"]
        publication_phase = publication["phase"]
        if (
            source_exists
            and destination_exists
            and publication_phase
            not in {
                "publishing",
                "ready",
            }
        ):
            raise OpsMigrationCollision(f"both source and destination exist for {name}")
        if not source_exists and not destination_exists:
            raise OpsMigrationCollision(
                f"both source and destination are missing for {name}"
            )
        expected_kind = "directory" if entry.get("kind") == "directory" else "file"
        if source_exists and publication_phase in {
            "planned",
            "publishing",
            "ready",
        }:
            if source_state == "symlink":
                raise OpsMigrationCollision(f"Ops migration path is a symlink: {name}")
            if source_state != expected_kind:
                raise OpsMigrationCollision(
                    f"Ops migration path has an unexpected type: {name}"
                )
            if not _snapshot_matches(_entry_snapshot(source), entry):
                raise OpsMigrationCollision(
                    f"content changed after migration planning: {name}"
                )
        if destination_exists and publication_phase in {"ready", "complete"}:
            expected_destination = publication["destination_snapshot"]
            if destination_state != expected_kind or not _snapshot_matches(
                _entry_snapshot(destination),
                expected_destination,
            ):
                raise OpsMigrationCollision(f"published Ops content changed: {name}")
        if (
            destination_exists
            and publication_phase == "publishing"
            and entry.get("kind") == "directory"
        ):
            if destination_state != "directory":
                raise OpsMigrationCollision(f"published Ops path changed type: {name}")
            actual_directories = _snapshot_directory_identities(
                _entry_snapshot(destination)
            )
            if actual_directories != dict(publication["destination_directories"]):
                raise OpsMigrationCollision(
                    f"destination directory ownership changed: {name}"
                )
        if (
            not destination_exists
            and publication_phase == "publishing"
            and entry.get("kind") == "directory"
            and publication["destination_directories"]
        ):
            raise OpsMigrationCollision(
                f"migration-owned destination directory is missing: {name}"
            )
        if publication_phase == "complete" and source_exists:
            raise OpsMigrationCollision(
                f"legacy Ops path reappeared after publication: {name}"
            )
        if publication_phase in {"ready", "complete"} and not destination_exists:
            raise OpsMigrationCollision(f"published Ops destination is missing: {name}")
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
        SELECT id, rel_path, source
        FROM project_areas
        WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'
        ORDER BY id
        """,
        (data["id"],),
    ).fetchall()
    active_ops_path = str(area_rows[0]["rel_path"]) if len(area_rows) == 1 else None
    active_ops_source = str(area_rows[0]["source"]) if len(area_rows) == 1 else None
    # The Ops folder this view inspects: the persisted per-project path when
    # one is active (prune C3), else the default ``ops/`` a legacy ``.``
    # migration would target.
    inspected_rel = (
        active_ops_path if active_ops_path not in (None, ".") else OPS_RELPATH
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
    physical = (
        root.joinpath(*_safe_rel_path(inspected_rel).parts)
        if root is not None
        else None
    )
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
                exact_recovery = state == "file" and (
                    phase not in {"planned", "creating", "created"}
                    and _identity_matches(
                        recovery_temp["identity"],
                        recovery_path.lstat(),
                    )
                    and _hash_file(recovery_path) == recovery_temp["sha256"]
                )
            except OSError:
                exact_recovery = False
            if exact_recovery:
                internal_files[recovery_temp["path"]] = recovery_temp
    physical_ops = (
        _physical_ops_state(physical, internal_files, rel_path=inspected_rel)
        if physical is not None
        else {"path": inspected_rel, "state": "unavailable", "entries": []}
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
                    "reason": f"Both {name} and {inspected_rel}/{name} exist.",
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
                "destination": f"{inspected_rel}/{name}",
                "expected_kind": expected,
                "legacy_state": source_state,
                "physical_state": destination_state,
                "layout": layout,
                "usable_from_active_ops": active_state == expected,
            }
        )

    retry_safe = False
    retry_action: str | None = None
    validation_reason: str | None = None
    planned_writes: dict[str, Any] | None = None
    if root_error:
        validation_reason = root_error
    elif active_ops_path == ".":
        # Adoption never applies mid-move: a durable "moving" manifest means
        # the migration owns whatever already sits inside ops/.
        resuming = bool(
            marker and marker.get("status") == "moving" and marker.get("manifest_json")
        )
        adoption: list[dict[str, str]] | None = None
        adoption_error: str | None = None
        # An explicit root choice (a 'manual' '.' row, prune C3) is never
        # adopted away from the owner; only detection-sourced legacy rows
        # offer the adopt retry.
        if not resuming and active_ops_source == "auto":
            try:
                adoption = _adoption_inventory(conn, data)
            except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
                adoption_error = str(exc)
        if adoption is not None:
            # A populated ops/ is adopted as-is on retry - nothing is moved.
            retry_safe = True
            retry_action = "adopt"
        elif adoption_error is not None:
            validation_reason = adoption_error
        else:
            try:
                planned_manifest = _validated_retry_manifest(conn, data, marker)
            except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
                validation_reason = str(exc)
            else:
                retry_safe = True
                retry_action = "migrate"
                # The full preview contract (prune C2): a migrate never runs
                # implicitly, and its opt-in names every write beyond the
                # planned moves - the container.md strategy and whether the
                # root repo's .git/info/exclude gains an /ops/ line.
                container_doc_plan = planned_manifest.get("container_doc")
                strategy = (
                    str(container_doc_plan.get("strategy") or "")
                    if isinstance(container_doc_plan, Mapping)
                    else ""
                )
                planned_writes = {
                    # "none" (prune C5) means no container.md write at all -
                    # the preview reports only real writes.
                    "container_doc": (
                        strategy if strategy in {"move", "generate"} else None
                    ),
                    # The migration's exclude append opens `.git` as a
                    # directory only; a gitfile checkout is left untouched.
                    "git_exclude": root is not None and (root / ".git").is_dir(),
                }
    elif active_ops_path is not None:
        # Any persisted non-'.' Ops path is a settled, first-class layout
        # (prune C3) - validate it, never call it unsupported. Read-only
        # inspection, so no deep symlink walk (prune C7).
        try:
            validated_area_roots(conn, data)
        except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
            validation_reason = str(exc)
        else:
            if attention_row is not None and attention_row["status"] == "open":
                retry_safe = True
                retry_action = "revalidate"
            else:
                validation_reason = "Migration is already complete; no retry is needed."
    else:
        validation_reason = "Container must have exactly one active Ops Area."
    if (
        validation_reason
        and active_ops_path in (None, ".")
        and not any(item["reason"] == validation_reason for item in conflicts)
    ):
        conflicts.append({"path": inspected_rel, "reason": validation_reason})

    if marker is not None:
        phase = str(marker["status"])
    elif active_ops_path not in (None, "."):
        phase = "not_required"
    else:
        phase = "unplanned"
    legacy_active = active_ops_path == "."
    physical_active = active_ops_path is not None and active_ops_path != "."
    if physical_active:
        usability_summary = (
            f"The Ops folder {active_ops_path}/ is active and Ops features "
            "remain usable."
        )
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
        usability_summary = (
            "Ops features are unavailable until the Area layout is repaired."
        )
    return {
        "project": {
            "id": int(data["id"]),
            "slug": data.get("slug"),
            "name": data.get("name"),
        },
        "phase": phase,
        "migration_version": (
            int(marker["migration_version"])
            if marker is not None
            else OPS_MIGRATION_VERSION
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
                    else f"Physical {inspected_rel}/ is {physical_ops['state']}."
                ),
            },
        },
        "conflicts": conflicts,
        "retry_safe": retry_safe,
        "retry_action": retry_action,
        "planned_writes": planned_writes,
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
            "created_at": attention_row["created_at"]
            if attention_row is not None
            else None,
            "resolved_at": attention_row["resolved_at"]
            if attention_row is not None
            else None,
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
    *,
    allow_moves: bool = True,
    ops_path: str | None = None,
) -> bool:
    """Settle or migrate one Container's Ops Area. Returns True when settled.

    Any persisted non-``.`` Ops path is a first-class layout (prune C3): it is
    validated and refreshed, never "unsupported". The remaining machinery
    handles legacy ``.`` rows.

    With ``allow_moves=False`` (link and the startup settle sweep, prune C2)
    only zero-write outcomes run: adopting an existing Ops folder and resuming
    a previously authorized in-flight move (a durable ``moving`` manifest).
    A ``.`` layout with nothing to adopt is left exactly as it is - active,
    readable, no marker, no attention - until the owner opts into the
    previewed migration through the retry route.

    ``ops_path`` is the link-time choice (prune C3): the detected default or
    the owner's override. It targets adoption at that folder (accepting an
    empty one) and ``.`` pins the Container root explicitly, skipping
    adoption. ``None`` (the startup sweep) keeps the legacy behavior:
    auto-adopt a populated ``ops/`` for detection-sourced rows only, so an
    owner's explicit root choice is never adopted away.
    """
    data = get_container(conn, container)
    row = conn.execute(
        "SELECT id, rel_path, source FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (data["id"],),
    ).fetchone()
    if row is None:
        reason = refusals.refusal_message(
            "container_no_ops_area", "This project has no active Ops folder"
        )
        _attention(conn, data, reason)
        return False
    if row["rel_path"] != ".":
        # A settled layout is only re-validated here (this runs on every boot
        # sweep) - nothing moves, so no deep symlink walk (prune C7). Walking
        # it would re-raise the same attention item forever for any real
        # folder that happens to contain a link (audit #120 path 2).
        try:
            validated_area_roots(conn, data)
            refresh_registry_projection(conn, data)
        except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
            reason = str(exc)
            _attention(conn, data, reason)
            return False
        _resolve_attention(conn, int(data["id"]))
        return True

    try:
        # Only a real move needs the fail-closed descendant walk (prune C7).
        validated_area_roots(conn, data, deep_ops_scan=allow_moves)
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
    resuming = bool(marker and marker["status"] == "moving" and marker["manifest_json"])
    if not resuming:
        # Adopt-as-is comes first (prune C1): an Ops folder that already
        # exists on disk is registered exactly as it is - no moves, no
        # generated files - instead of colliding with the planned migration.
        # The adoption target is per project (prune C3): the link-time choice
        # when given, else the ``ops/`` default - and only for
        # detection-sourced rows, so a persisted explicit root choice stays.
        target_rel = ops_path if ops_path not in (None, ".") else OPS_RELPATH
        attempt_adoption = ops_path not in (None, ".") or (
            ops_path is None and row["source"] == "auto"
        )
        adoption: list[dict[str, str]] | None = None
        if attempt_adoption:
            try:
                adoption = _adoption_inventory(
                    conn,
                    data,
                    rel_path=target_rel,
                    allow_empty=ops_path is not None,
                )
            except (ContainerBoundaryError, OSError) as exc:
                reason = str(exc)
                _upsert_marker(conn, int(data["id"]), "attention", None, reason)
                _attention(conn, data, reason)
                return False
        if adoption is not None:
            try:
                adopted_manifest = _adoption_manifest(
                    data,
                    adoption,
                    rel_path=target_rel,
                )
                conn.execute("BEGIN")
                try:
                    conn.execute(
                        "UPDATE project_areas SET rel_path = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (target_rel, row["id"]),
                    )
                    # Adoption is zero-write: register the folder as it is,
                    # links included (prune C7) - they are skipped on read.
                    validated_area_roots(conn, data)
                    _upsert_marker(
                        conn,
                        int(data["id"]),
                        "complete",
                        adopted_manifest,
                    )
                    refresh_registry_projection(conn, data)
                    _resolve_attention(conn, int(data["id"]))
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
                reason = str(exc)
                _upsert_marker(conn, int(data["id"]), "attention", None, reason)
                _attention(conn, data, reason)
                return False
            return True
        if not allow_moves:
            # Non-mutating settle (prune C2): nothing to adopt, and moving
            # files needs the owner's explicit, previewed opt-in. The legacy
            # "." Ops Area stays fully readable through the virtual mapping;
            # any stored marker/attention is owner-facing state we leave for
            # the retry surface to resolve.
            return True
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
        strategy, container_doc, expected_container_doc_hash = _manifest_container_doc(
            manifest
        )
        root = Path(str(manifest["container_root"]))
        physical_container_doc = Path(str(manifest["ops_root"])) / CONTAINER_DOC
        with _stable_migration_directories(root) as (
            root_fd,
            physical_fd,
            _,
            _,
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
                persist_manifest=lambda: _upsert_marker(
                    conn,
                    int(data["id"]),
                    "moving",
                    manifest,
                ),
            )
            if strategy == "none":
                # Nothing was planned for container.md (prune C5) - one
                # appearing mid-flight is concurrent modification.
                if _path_state_at(physical_fd, CONTAINER_DOC) != "missing":
                    raise OpsMigrationCollision(
                        "ops/container.md changed during migration"
                    )
            elif (
                _hash_file_at(physical_fd, CONTAINER_DOC)
                != expected_container_doc_hash
            ):
                raise OpsMigrationCollision("ops/container.md changed during migration")
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
    *,
    allow_moves: bool = True,
    ops_path: str | None = None,
) -> bool:
    """Migrate one legacy ``.`` Ops Area. Returns True only when complete."""
    with container_mutation_lock(conn, container):
        try:
            # Exclusive flock is not enough: dead sentinels release the activity
            # lock while durable guardian records (and reparented writers) remain.
            # Recover first, then require zero active/unresolved identity-bound
            # records before mutation under exclusive quiescence.
            recovery = recover_container_activity_guardians(conn, container)
            if recovery.active or recovery.unresolved:
                data = get_container(conn, container)
                reason = (
                    "Container has active processes; stop them before retrying"
                    if recovery.active
                    else (
                        "Project process ownership could not be verified; "
                        "exclusive work is blocked until process identity is reconciled"
                    )
                )
                _attention(conn, data, reason)
                return False
            with container_quiescence_lock(conn, container):
                recovery = recover_container_activity_guardians(conn, container)
                if recovery.active or recovery.unresolved:
                    raise ContainerBoundaryError(
                        "Project process ownership could not be verified; "
                        "exclusive work is blocked until process identity is reconciled"
                        if recovery.unresolved
                        else "Container has active processes; stop them before retrying"
                    )
                return _migrate_container_ops_locked(
                    conn,
                    container,
                    allow_moves=allow_moves,
                    ops_path=ops_path,
                )
        except ContainerBoundaryError as exc:
            data = get_container(conn, container)
            reason = str(exc)
            _attention(conn, data, reason)
            return False


def settle_container_ops(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    ops_path: str | None = None,
) -> bool:
    """Register one Container's Ops layout without ever moving its files.

    The zero-write companion to :func:`migrate_container_ops` used at link
    time and by the startup sweep (prune C2): an existing Ops folder is
    adopted as-is, an interrupted authorized move resumes from its durable
    manifest, and every other layout is left byte-identical on disk.

    ``ops_path`` carries the link-time per-project choice (prune C3): the
    detected default or the owner's override. The startup sweep passes None
    and only ever re-validates persisted choices.
    """
    return migrate_container_ops(
        conn,
        container,
        allow_moves=False,
        ops_path=ops_path,
    )


def migrate_legacy_ops_containers(conn: sqlite3.Connection) -> dict[str, int]:
    """Settle every current Container's Ops layout without moving files.

    Startup sweep (prune C2): adopts populated ``ops/`` layouts, resumes only
    a previously authorized in-flight move, and never plans new moves - the
    move-based migration is exclusively the owner's previewed opt-in.
    """
    summary = {"complete": 0, "attention": 0}
    rows = conn.execute(
        "SELECT id, slug, name, path, path_identity "
        "FROM projects WHERE archived_at IS NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        try:
            if settle_container_ops(conn, row):
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

"""Container filesystem boundaries, Ops migration, and registry projection.

The existing ``projects`` and ``project_areas`` tables remain the persistence
backbone. This module is the one place that turns those rows into physical
Container and Area roots.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .auth import iso_now

logger = logging.getLogger("proxima.container_registry")

OPS_RELPATH = "ops"
CONTAINER_DOC = "container.md"
OPS_MIGRATION_VERSION = 1
KNOWN_OPS_DIRS = (
    "wiki",
    "artifacts",
    "reports",
    "exports",
    "scripts",
    "tasks",
    "uploads",
)
KNOWN_OPS_FILES = ("design.md",)
OPS_VIRTUAL_NAMES = frozenset((*KNOWN_OPS_DIRS, *KNOWN_OPS_FILES, CONTAINER_DOC))
DEFAULT_STARTER_DIRS = ("wiki", "tasks", "artifacts")
MAX_CONTAINER_DOC_BYTES = 64 * 1024


class ContainerBoundaryError(ValueError):
    """A Container or Area root is missing, ambiguous, or unsafe."""


class OpsMigrationCollision(ContainerBoundaryError):
    """A legacy Ops layout cannot be moved without owner intervention."""


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
    if root.is_symlink():
        raise ContainerBoundaryError("Container root cannot be a symlink")
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ContainerBoundaryError("Container root is missing")
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


def _atomic_write_if_missing(path: Path, text: str) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise OpsMigrationCollision(f"{path.name} exists but is not a regular file")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


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
    _atomic_write_if_missing(physical / CONTAINER_DOC, _container_doc_text(name))
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
    if OPS_RELPATH in code_paths:
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
    return {
        "version": OPS_MIGRATION_VERSION,
        "container_root": str(root),
        "ops_root": str(physical),
        "entries": entries,
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


def _apply_manifest(manifest: Mapping[str, Any]) -> Path:
    root = Path(str(manifest["container_root"]))
    physical = Path(str(manifest["ops_root"]))
    if root.resolve() != root or not root.is_dir():
        raise OpsMigrationCollision("Container root changed after migration planning")
    if physical.is_symlink():
        raise OpsMigrationCollision("physical Ops root became a symlink")
    if physical.exists() and not physical.is_dir():
        raise OpsMigrationCollision("physical Ops root became a non-directory")
    physical.mkdir(exist_ok=True)

    for entry in manifest["entries"]:
        source = root / entry["name"]
        destination = physical / entry["name"]
        source_exists = source.exists() or source.is_symlink()
        destination_exists = destination.exists() or destination.is_symlink()
        if source_exists and destination_exists:
            raise OpsMigrationCollision(
                f"both source and destination exist for {entry['name']}"
            )
        if not source_exists and not destination_exists:
            raise OpsMigrationCollision(
                f"both source and destination are missing for {entry['name']}"
            )
        current = source if source_exists else destination
        digest, _ = _hash_entry(current)
        if digest != entry["sha256"]:
            raise OpsMigrationCollision(
                f"content changed after migration planning: {entry['name']}"
            )
        if source_exists:
            if source.stat().st_dev != physical.stat().st_dev:
                raise OpsMigrationCollision(
                    f"source and destination are on different filesystems: {entry['name']}"
                )
            os.replace(source, destination)
            moved_digest, _ = _hash_entry(destination)
            if moved_digest != entry["sha256"]:
                raise OpsMigrationCollision(
                    f"content hash changed during move: {entry['name']}"
                )
    return physical


def _parse_container_doc(path: Path) -> tuple[str | None, str | None, str | None]:
    if not path.is_file() or path.is_symlink():
        return None, None, None
    data = path.read_bytes()
    if len(data) > MAX_CONTAINER_DOC_BYTES:
        data = data[:MAX_CONTAINER_DOC_BYTES]
    source_hash = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", errors="replace")
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
                    identity = value.strip()[:120] or None
                elif key.strip() == "summary":
                    summary = value.strip()[:500] or None
    if summary is None:
        body = text.split("\n---\n", 1)[-1] if text.startswith("---\n") else text
        summary = next(
            (
                line.strip()[:500]
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
          (SELECT MAX(updated_at) FROM sessions WHERE project_id = ?)
        )
        ON CONFLICT(container_id) DO UPDATE SET
          identity_label = excluded.identity_label,
          summary = excluded.summary,
          source_hash = excluded.source_hash,
          indexed_at = excluded.indexed_at,
          last_activity_at = excluded.last_activity_at
        """,
        (
            data["id"],
            identity,
            summary,
            source_hash,
            iso_now(),
            data["id"],
        ),
    )


def migrate_container_ops(
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
            validated_area_roots(conn, data)
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
        "SELECT status, manifest_json FROM container_ops_migrations WHERE container_id = ?",
        (data["id"],),
    ).fetchone()
    manifest: dict[str, Any] | None = None
    if marker and marker["status"] == "moving" and marker["manifest_json"]:
        manifest = json.loads(marker["manifest_json"])
        if _manifest_digest(manifest) != conn.execute(
            "SELECT manifest_hash FROM container_ops_migrations WHERE container_id = ?",
            (data["id"],),
        ).fetchone()["manifest_hash"]:
            reason = "stored Ops migration manifest failed its integrity check"
            _attention(conn, data, reason)
            return False
    if manifest is None:
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
        physical = _apply_manifest(manifest)
        _atomic_write_if_missing(
            physical / CONTAINER_DOC,
            _container_doc_text(str(data.get("name") or data.get("slug") or "Container")),
        )
        exclude_ops_from_root_repo(container_root(data))
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE project_areas SET rel_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (OPS_RELPATH, row["id"]),
            )
            validated_area_roots(conn, data, deep_ops_scan=True)
            _upsert_marker(conn, int(data["id"]), "complete", manifest)
            refresh_registry_projection(conn, data)
            _resolve_attention(conn, int(data["id"]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    except (ContainerBoundaryError, OSError, sqlite3.Error) as exc:
        reason = str(exc)
        _upsert_marker(conn, int(data["id"]), "moving", manifest, reason)
        _attention(conn, data, reason)
        return False
    return True


def migrate_legacy_ops_containers(conn: sqlite3.Connection) -> dict[str, int]:
    """Run the resumable physical Ops migration for every current Container."""
    summary = {"complete": 0, "attention": 0}
    rows = conn.execute(
        "SELECT id, slug, name, path FROM projects WHERE archived_at IS NULL ORDER BY id"
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

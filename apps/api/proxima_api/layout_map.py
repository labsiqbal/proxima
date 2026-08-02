"""Per-project layout map: where wiki/artifacts/scripts/uploads live (prune C4).

A project is the folder as it exists on disk - Proxima detects and adapts, it
never imposes (decision #121). This module owns the per-project map of the
four well-known Ops locations:

- **Seeded by zero-write detection** from the real tree at link time (and by
  the startup settle sweep / lazily for projects linked before the map
  existed). Detection looks for the standard name under the persisted Ops
  root first, then at the container root; a real, non-symlink directory is
  recorded as ``detected``. When nothing exists, today's fixed name under the
  Ops root is recorded as the ``default`` - so behavior is unchanged until
  detection says otherwise.
- **Persisted per project** in the ``project_layout`` table (one row per
  area). The map is state, not a per-boot scan: a persisted entry in the
  default position stays authoritative even if the folder is deleted later.
  An entry OUTSIDE the Ops root stays authoritative only while its folder
  exists; when it disappears (e.g. the explicit opt-in migration moved it
  into Ops) the area re-detects, so the map self-heals without hooks.
- **Features resolve through the map** instead of hardcoding the names
  (audit #120 sites): wiki read surfaces, the run preamble, the script
  library, uploads, artifact/design/moodboard scanning.

Scope seams left deliberately clean:

- :func:`wiki_memory_write_root` is the #137 seam - the automatic memory
  writers (``log.md`` append, ``index.md`` regeneration) only ever target the
  wiki while the map agrees with its DEFAULT location. A wiki detected away
  from the default is read-only for them until #137 ships adaptive memory
  writes (per-project toggleable). Note a ``.`` project's root ``wiki/``
  (BIP) IS its default location, so it keeps memory writes exactly as today.
- Reserved-name virtual rerouting still exists (removed by #138); the
  Knowledge-graph allowlist keeps the fixed ops-relative names, which
  coincide with every detectable map today (see graph_context.py).
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .container_registry import (
    ContainerBoundaryError,
    container_root,
    get_container,
    ops_root,
)

LAYOUT_AREAS = ("wiki", "artifacts", "scripts", "uploads")
SOURCE_DETECTED = "detected"
SOURCE_DEFAULT = "default"

logger = logging.getLogger("proxima.layout_map")


def _join_rel(base: str, name: str) -> str:
    """Join a container-relative base (``.`` for the root) with a child name."""
    return name if base in ("", ".") else f"{base}/{name}"


def default_rel(ops_rel: str, area: str) -> str:
    """Today's fixed location for an area: ``<ops>/<area>``."""
    return _join_rel(ops_rel, area)


def _safe_rel(raw: str) -> str | None:
    """A stored rel path, validated: container-relative, no escapes."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def _is_real_directory(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def detect_area(root: Path, ops_rel: str, area: str) -> tuple[str, str]:
    """Detect one area's location relative to the container root. Zero-write:
    only inspects the tree, never creates anything (C2 discipline)."""
    ops_candidate = _join_rel(ops_rel, area)
    if _is_real_directory(Path(root).joinpath(*PurePosixPath(ops_candidate).parts)):
        return ops_candidate, SOURCE_DETECTED
    if ops_rel not in ("", ".") and _is_real_directory(Path(root) / area):
        return area, SOURCE_DETECTED
    return ops_candidate, SOURCE_DEFAULT


def detect_layout(root: Path, ops_rel: str) -> dict[str, tuple[str, str]]:
    """Detect every area: ``{area: (rel_path, source)}``. Zero-write."""
    return {area: detect_area(root, ops_rel, area) for area in LAYOUT_AREAS}


@dataclass(frozen=True)
class ProjectLayout:
    """One project's resolved layout: container root, Ops root, and the four
    area locations (container-root-relative)."""

    container_root: Path
    ops_root: Path
    ops_rel: str
    rel_paths: Mapping[str, str]
    sources: Mapping[str, str]

    def dir(self, area: str) -> Path:
        """The area's absolute directory (may not exist yet)."""
        rel = self.rel_paths[area]
        return self.container_root.joinpath(*PurePosixPath(rel).parts)

    def ops_rel_path(self, area: str) -> str | None:
        """The area's path relative to the Ops root, or None when it lives
        outside the Ops root (a root-level detection with Ops elsewhere)."""
        rel = self.rel_paths[area]
        if self.ops_rel in ("", "."):
            return rel
        prefix = f"{self.ops_rel}/"
        if rel.startswith(prefix):
            return rel[len(prefix):]
        return None

    def anchored(self, area: str) -> tuple[Path, str]:
        """(root, root-relative rel) for jailed access: the Ops root when the
        area lives inside it, else the container root."""
        ops_rel_path = self.ops_rel_path(area)
        if ops_rel_path is not None:
            return self.ops_root, ops_rel_path
        return self.container_root, self.rel_paths[area]

    def wiki_memory_write_root(self) -> Path | None:
        """The #137 seam: where the AUTOMATIC memory writers (log.md append,
        index.md regeneration) may write. Only the wiki's default location -
        a wiki detected elsewhere is read-only for them until #137 enables
        adaptive memory writes. (A '.' project's root wiki IS its default.)"""
        if self.rel_paths["wiki"] == default_rel(self.ops_rel, "wiki"):
            return self.dir("wiki")
        return None


def _ops_rel(conn: sqlite3.Connection, project_id: int) -> str:
    row = conn.execute(
        "SELECT rel_path FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ContainerBoundaryError("Container has no active Ops Area")
    return str(row["rel_path"] or ".")


def _stored_rows(conn: sqlite3.Connection, project_id: int) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT area, rel_path, source FROM project_layout WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    return {
        str(row["area"]): (str(row["rel_path"]), str(row["source"]))
        for row in rows
        if str(row["area"]) in LAYOUT_AREAS
    }


def _persist(
    conn: sqlite3.Connection,
    project_id: int,
    area: str,
    rel: str,
    source: str,
) -> None:
    """Best-effort upsert - resolution must keep working when the row can't
    be written (the detected value is still returned in-memory)."""
    try:
        conn.execute(
            """
            INSERT INTO project_layout(project_id, area, rel_path, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, area) DO UPDATE SET
              rel_path = excluded.rel_path,
              source = excluded.source,
              updated_at = CURRENT_TIMESTAMP
            """,
            (project_id, area, rel, source),
        )
    except sqlite3.Error:
        logger.exception("project_layout persist failed (non-fatal)")


def project_layout(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> ProjectLayout:
    """Resolve one project's layout map, lazily backfilling missing areas.

    Projects linked before the map existed get their rows on first
    resolution (detection against the real tree - today's names when nothing
    is detected, so behavior is unchanged). A stored entry outside the Ops
    root whose folder is gone re-detects (self-healing after the explicit
    migration moved content into Ops). Detection never writes to the tree.
    """
    data = get_container(conn, container)
    root = container_root(data)
    project_id = int(data["id"])
    ops_rel = _ops_rel(conn, project_id)
    resolved_ops_root = ops_root(conn, data)
    stored = _stored_rows(conn, project_id)

    rel_paths: dict[str, str] = {}
    sources: dict[str, str] = {}
    for area in LAYOUT_AREAS:
        entry = stored.get(area)
        rel = _safe_rel(entry[0]) if entry else None
        source = entry[1] if entry else SOURCE_DEFAULT
        if rel is not None and rel != default_rel(ops_rel, area):
            # A non-default entry stays authoritative only while it exists.
            inside_ops = ops_rel in ("", ".") or rel == ops_rel or rel.startswith(
                f"{ops_rel}/"
            )
            if not inside_ops and not _is_real_directory(
                root.joinpath(*PurePosixPath(rel).parts)
            ):
                rel = None
        if rel is None:
            rel, source = detect_area(root, ops_rel, area)
            _persist(conn, project_id, area, rel, source)
        rel_paths[area] = rel
        sources[area] = source
    return ProjectLayout(
        container_root=root,
        ops_root=resolved_ops_root,
        ops_rel=ops_rel,
        rel_paths=rel_paths,
        sources=sources,
    )


def seed_project_layout(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> None:
    """Seed missing map rows from detection (link time / startup sweep).

    Idempotent: existing rows are never overwritten here - persistence wins
    over re-detection (the self-heal rule lives in :func:`project_layout`).
    Zero-write toward the tree.
    """
    data = get_container(conn, container)
    project_id = int(data["id"])
    root = container_root(data)
    ops_rel = _ops_rel(conn, project_id)
    stored = _stored_rows(conn, project_id)
    for area in LAYOUT_AREAS:
        if area in stored:
            continue
        rel, source = detect_area(root, ops_rel, area)
        _persist(conn, project_id, area, rel, source)


def try_seed_project_layout(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> None:
    """Sweep-safe seeding: an unavailable container is skipped, not fatal."""
    try:
        seed_project_layout(conn, container)
    except (ContainerBoundaryError, OSError):
        logger.debug("layout seed skipped for unavailable container")


def try_project_layout(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> ProjectLayout | None:
    """Best-effort resolution for aggregation paths; None when unavailable."""
    try:
        return project_layout(conn, container)
    except (ContainerBoundaryError, OSError):
        return None


def layout_dir(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    area: str,
) -> Path:
    """One area's absolute directory, resolved through the map."""
    return project_layout(conn, container).dir(area)


def wiki_memory_write_root(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> Path | None:
    """Where automatic memory writes may go (see the #137 seam above)."""
    return project_layout(conn, container).wiki_memory_write_root()

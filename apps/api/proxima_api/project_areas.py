"""Container Areas backed by the compatibility ``project_areas`` table.

A Container is a root folder holding zero or more repo Areas plus exactly one
physical Ops Area. New Containers use ``ops/``. Legacy rows at ``.`` remain
readable until the resumable compatibility migration completes.

Identification is **hybrid** (T1 decision): subfolders containing `.git` are
auto-detected as code areas, and the owner may manually register, correct, or
remove areas via the project-areas API. Manual rows always win: re-detection
only ever adds/removes rows whose `source` is `'auto'`, and a removed area
leaves an `'excluded'` tombstone so re-detection cannot resurrect it.

Root resolution and boundary validation live in ``container_registry.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .container_registry import (
    OPS_RELPATH,
    container_mutation_lock,
    create_physical_ops_root,
    validated_area_roots,
)

# Mirrors the detect_apps scan in routes/files.py: bounded depth, skip heavy
# and tooling dirs, never follow hidden folders.
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    "__pycache__",
    ".cache",
    "target",
    ".hermes",
    ".claude",
    OPS_RELPATH,
}
MAX_DEPTH = 2  # scan the root + two subfolder levels
MAX_AREAS = 50


def detect_code_areas(root: Path, *, ops_rel_path: str | None = None) -> list[str]:
    """Scan a container root for git repos and return their relative paths.

    A dir counts as a repo when `.git` exists - as a directory (normal clone)
    or a file (linked worktree / submodule pointer). The root itself counts
    (returned as `.`). The scan never descends *into* a detected repo: nested
    `.git`s under it are that repo's submodules/vendored checkouts, not
    separate code areas of the container.

    ``ops_rel_path`` is the container's persisted Ops path (prune C3): its
    subtree holds Ops content, never code areas, so the scan skips it - the
    per-project counterpart of the fixed ``ops`` name in SKIP_DIRS.
    """
    root = Path(root)
    found: list[str] = []
    skip_rel = (
        ops_rel_path if ops_rel_path not in (None, ".", OPS_RELPATH) else None
    )

    def scan(d: Path, depth: int) -> None:
        if depth > MAX_DEPTH or len(found) >= MAX_AREAS:
            return
        try:
            if (d / ".git").exists():
                found.append("." if d == root else d.relative_to(root).as_posix())
                return
            children = sorted(d.iterdir(), key=lambda c: c.name.lower())
        except OSError:
            return
        for c in children:
            try:
                if (
                    c.is_dir()
                    and c.name not in SKIP_DIRS
                    and not c.name.startswith(".")
                    and (
                        skip_rel is None
                        or c.relative_to(root).as_posix() != skip_rel
                    )
                ):
                    scan(c, depth + 1)
            except OSError:
                pass

    scan(root, 0)
    return sorted(found)


def ensure_ops_area(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    rel_path: str = OPS_RELPATH,
    source: str = "auto",
) -> None:
    """Ensure the Container has one Ops Area.

    Workspace-created Containers (Proxima's own data dir) use the physical
    ``ops/`` boundary and scaffold it. Linked folders register at ``.`` and
    settle to their per-project path without writes (prune C2/C3).
    ``source`` records how the path was picked: ``'auto'`` for detection,
    ``'manual'`` for an owner override chosen at link time - a manual row is
    never adopted away by the settle sweep.
    """
    with container_mutation_lock(conn, project_id):
        _ensure_ops_area_locked(conn, project_id, rel_path=rel_path, source=source)


def _ensure_ops_area_locked(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    rel_path: str,
    source: str,
) -> None:
    existing = conn.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (project_id,),
    ).fetchone()
    if existing is not None:
        return
    if rel_path == OPS_RELPATH:
        project = conn.execute(
            "SELECT name, slug, path, path_identity FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            raise ValueError("Container does not exist")
        create_physical_ops_root(Path(project["path"]))
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "SELECT ?, 'ops', ?, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM project_areas WHERE project_id = ? AND kind = 'ops')",
        (project_id, rel_path, source, project_id),
    )


def sync_code_areas(
    conn: sqlite3.Connection,
    project_id: int,
    root: str | Path,
    *,
    validate: bool = True,
) -> dict:
    """Reconcile auto-detected code areas with the filesystem.

    Only `source='auto'` rows follow detection (added when a repo appears,
    dropped when its `.git` vanishes). `'manual'` rows are never touched and
    `'excluded'` tombstones keep blocking their rel_path; a tombstone whose
    repo marker is gone has nothing left to block and is garbage-collected.
    A missing/unreadable root simply detects nothing - valid (zero code areas).
    """
    with container_mutation_lock(conn, project_id):
        return _sync_code_areas_locked(
            conn,
            project_id,
            root,
            validate=validate,
        )


def _sync_code_areas_locked(
    conn: sqlite3.Connection,
    project_id: int,
    root: str | Path,
    *,
    validate: bool,
) -> dict:
    # Detection is read-only (prune C2). The one .git/info/exclude write that
    # keeps a created ops/ out of a root repo's status happens exclusively
    # inside the explicit, previewed Ops migration - never here.
    root = Path(root)
    ops_row = conn.execute(
        "SELECT rel_path FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (project_id,),
    ).fetchone()
    ops_rel_path = str(ops_row["rel_path"]) if ops_row is not None else None
    detected = (
        set(detect_code_areas(root, ops_rel_path=ops_rel_path))
        if root.is_dir()
        else set()
    )
    rows = conn.execute(
        "SELECT id, rel_path, source FROM project_areas WHERE project_id = ? AND kind = 'code'",
        (project_id,),
    ).fetchall()
    known = {r["rel_path"]: r for r in rows}
    added: list[str] = []
    for rel in sorted(detected):
        if rel not in known:
            conn.execute(
                "INSERT INTO project_areas(project_id, kind, rel_path, source) VALUES (?, 'code', ?, 'auto')",
                (project_id, rel),
            )
            added.append(rel)
    removed: list[str] = []
    for rel, row in known.items():
        if rel in detected:
            continue
        if row["source"] == "auto":
            conn.execute("DELETE FROM project_areas WHERE id = ?", (row["id"],))
            removed.append(rel)
        elif row["source"] == "excluded":
            conn.execute("DELETE FROM project_areas WHERE id = ?", (row["id"],))
    if validate:
        # Registration only - no content is moved or created here, so the deep
        # descendant-symlink walk does not apply (prune C7). Overlap, escape,
        # and the symlinked-Ops-root check still run.
        validated_area_roots(conn, project_id)
    return {"detected": sorted(detected), "added": added, "removed": sorted(removed)}


def areas_payload(conn: sqlite3.Connection, project_id: int) -> dict:
    """The read surface later slices and the UI consume: active areas only
    (excluded tombstones are bookkeeping, not part of the container).
    push_on_merge is the T9 per-area opt-in; the settings route pairs it with
    the area's detected remote so the UI knows whether to offer the toggle."""
    rows = conn.execute(
        "SELECT id, kind, rel_path, source, push_on_merge, push_remote_url FROM project_areas "
        "WHERE project_id = ? AND source != 'excluded' ORDER BY kind, rel_path",
        (project_id,),
    ).fetchall()
    code = [
        {
            "id": r["id"],
            "rel_path": r["rel_path"],
            "source": r["source"],
            "push_on_merge": bool(r["push_on_merge"]),
            # The URL pinned at opt-in (audit F3) - what a push will insist on.
            "push_remote_url": r["push_remote_url"],
        }
        for r in rows
        if r["kind"] == "code"
    ]
    ops = next(
        (
            {"id": r["id"], "rel_path": r["rel_path"]}
            for r in rows
            if r["kind"] == "ops"
        ),
        None,
    )
    return {"code_areas": code, "ops_area": ops}

"""Project work-container areas (Phase-1 slice 1, ticket T1).

A project is a **work container**: a root folder holding zero-or-more *code
areas* (relative subfolder paths, each expected to contain a git repo; `.`
means the root itself is the repo) plus exactly one *ops area* (the non-code
output space; the conventional `artifacts/ reports/ exports/ wiki/` subdirs
belong to it). This module owns the detection + persistence helpers; the rows
live in the `project_areas` table (see db.py / migration 18).

Identification is **hybrid** (T1 decision): subfolders containing `.git` are
auto-detected as code areas, and the owner may manually register, correct, or
remove areas via the project-areas API. Manual rows always win: re-detection
only ever adds/removes rows whose `source` is `'auto'`, and a removed area
leaves an `'excluded'` tombstone so re-detection cannot resurrect it.

Slice 1 is additive metadata only - nothing here changes execution, cwd
selection, or artifact scanning. Later slices consume it: worktree machinery
(slice 2) cuts a worktree from a job's target code area, and the slicer
(slice 3) binds each job to exactly one area.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Mirrors the detect_apps scan in routes/files.py: bounded depth, skip heavy
# and tooling dirs, never follow hidden folders.
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", ".next", "__pycache__", ".cache", "target", ".hermes", ".claude"}
MAX_DEPTH = 2  # scan the root + two subfolder levels
MAX_AREAS = 50


def detect_code_areas(root: Path) -> list[str]:
    """Scan a container root for git repos and return their relative paths.

    A dir counts as a repo when `.git` exists - as a directory (normal clone)
    or a file (linked worktree / submodule pointer). The root itself counts
    (returned as `.`). The scan never descends *into* a detected repo: nested
    `.git`s under it are that repo's submodules/vendored checkouts, not
    separate code areas of the container.
    """
    root = Path(root)
    found: list[str] = []

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
                if c.is_dir() and c.name not in SKIP_DIRS and not c.name.startswith("."):
                    scan(c, depth + 1)
            except OSError:
                pass

    scan(root, 0)
    return sorted(found)


def ensure_ops_area(conn: sqlite3.Connection, project_id: int) -> None:
    """Ensure the container's single ops area row exists (rel_path '.': the
    conventional output subdirs live directly under the container root)."""
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "SELECT ?, 'ops', '.', 'auto' "
        "WHERE NOT EXISTS (SELECT 1 FROM project_areas WHERE project_id = ? AND kind = 'ops')",
        (project_id, project_id),
    )


def sync_code_areas(conn: sqlite3.Connection, project_id: int, root: str | Path) -> dict:
    """Reconcile auto-detected code areas with the filesystem.

    Only `source='auto'` rows follow detection (added when a repo appears,
    dropped when its `.git` vanishes). `'manual'` rows are never touched and
    `'excluded'` tombstones keep blocking their rel_path; a tombstone whose
    repo marker is gone has nothing left to block and is garbage-collected.
    A missing/unreadable root simply detects nothing - valid (zero code areas).
    """
    root = Path(root)
    detected = set(detect_code_areas(root)) if root.is_dir() else set()
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
    return {"detected": sorted(detected), "added": added, "removed": sorted(removed)}


def areas_payload(conn: sqlite3.Connection, project_id: int) -> dict:
    """The read surface later slices and the UI consume: active areas only
    (excluded tombstones are bookkeeping, not part of the container)."""
    rows = conn.execute(
        "SELECT id, kind, rel_path, source FROM project_areas "
        "WHERE project_id = ? AND source != 'excluded' ORDER BY kind, rel_path",
        (project_id,),
    ).fetchall()
    code = [
        {"id": r["id"], "rel_path": r["rel_path"], "source": r["source"]}
        for r in rows if r["kind"] == "code"
    ]
    ops = next(
        ({"id": r["id"], "rel_path": r["rel_path"]} for r in rows if r["kind"] == "ops"),
        None,
    )
    return {"code_areas": code, "ops_area": ops}

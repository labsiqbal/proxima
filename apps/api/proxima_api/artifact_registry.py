"""Durable deliverable registry (Phase 1 slice 8, T4).

The scanner (artifacts.py) DISCOVERS files; this registry REMEMBERS them. One
row per deliverable version: name, type, path, size, lineage (session -> job/
node -> run), approval status, and a version chain. Records survive file moves
and deletion - a missing file flips ``file_missing``, the record stays.

Approval is one status with two doors: approving a job in its Tasks review
auto-approves the records that job produced (``approve_records_for_job``), and
the Archive page edits the SAME status field (``set_status``). Never two
separate approval states.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth import iso_now

log = logging.getLogger("proxima.archive")

STATUSES = ("draft", "review", "approved", "superseded")

# Scanner types that a deterministic script step re-labels: a generic file
# produced by a script run is a "script output" deliverable; richer types
# (page, image, doc, ...) keep their identity.
SCRIPT_RUN_KIND = "wf_script_node"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "record"


def _unique_slug(conn: sqlite3.Connection, project_id: int, base: str, version: int) -> str:
    candidate = f"{base}-v{version}"
    n = 2
    while conn.execute(
        "SELECT 1 FROM artifact_records WHERE project_id = ? AND slug = ?", (project_id, candidate)
    ).fetchone():
        candidate = f"{base}-v{version}-{n}"
        n += 1
    return candidate


def _stat(root: Path | None, rel_path: str) -> tuple[int | None, float | None]:
    """(size_bytes, mtime) for a record's file; (None, None) when unavailable."""
    if root is None or not rel_path:
        return None, None
    try:
        st = (root / rel_path).stat()
        return (st.st_size if (root / rel_path).is_file() else None), st.st_mtime
    except OSError:
        return None, None


def _latest_for_identity(
    conn: sqlite3.Connection, project_id: int, typ: str, path: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM artifact_records WHERE project_id = ? AND type = ? AND path = ? "
        "ORDER BY version DESC, id DESC LIMIT 1",
        (project_id, typ, path),
    ).fetchone()


def record_artifacts(
    conn: sqlite3.Connection,
    project_id: int,
    project_root: Path | None,
    items: list[dict[str, Any]],
    *,
    session_id: int | None = None,
    job_id: int | None = None,
    node_id: str | None = None,
    run_id: int | None = None,
    run_kind: str | None = None,
    produced_at: str | None = None,
) -> list[int]:
    """Upsert scanned artifacts as durable records; returns the touched row ids.

    Identity is (project, type, path). A re-scan by the SAME run - or a later
    step of the same still-draft job - refreshes the existing record (feeding
    is idempotent). A new producer at the same identity creates v(n+1) and
    marks every prior version superseded (the automatic version chain).
    """
    now = iso_now()
    produced = produced_at or now
    touched: list[int] = []
    for item in items:
        typ = str(item.get("type") or "")
        path = str(item.get("path") or "")
        if not typ or not path:
            continue
        if run_kind == SCRIPT_RUN_KIND and typ == "file":
            typ = "script-output"
        name = str(item.get("title") or Path(path).name)
        size, _ = _stat(project_root, path)
        latest = _latest_for_identity(conn, project_id, typ, path)
        refresh = latest is not None and (
            (run_id is not None and latest["run_id"] == run_id)
            # Later steps of the same job rewriting the same file are one
            # deliverable in progress, not a new version.
            or (job_id is not None and latest["job_id"] == job_id and latest["status"] == "draft")
        )
        if refresh and latest is not None:
            conn.execute(
                "UPDATE artifact_records SET name = ?, size = ?, produced_at = ?, run_id = ?, "
                "file_missing = 0, updated_at = ? WHERE id = ?",
                (name, size, produced, run_id if run_id is not None else latest["run_id"], now, latest["id"]),
            )
            touched.append(int(latest["id"]))
            continue
        version = (int(latest["version"]) + 1) if latest is not None else 1
        slug = _unique_slug(conn, project_id, slugify(name), version)
        cur = conn.execute(
            "INSERT INTO artifact_records(project_id, slug, name, type, path, size, status, version, "
            "session_id, job_id, node_id, run_id, produced_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, slug, name, typ, path, size, version, session_id, job_id, node_id, run_id, produced, now, now),
        )
        new_id = int(cur.lastrowid or 0)
        if latest is not None:
            conn.execute(
                "UPDATE artifact_records SET status = 'superseded', superseded_by = ?, updated_at = ? "
                "WHERE project_id = ? AND type = ? AND path = ? AND id != ? AND status != 'superseded'",
                (new_id, now, project_id, typ, path, new_id),
            )
        touched.append(new_id)
    return touched


def record_run_outputs(
    conn: sqlite3.Connection,
    run_id: int,
    session_id: int,
    project_id: int | None,
    output_links: list[dict[str, Any]],
) -> None:
    """Feed the registry from a finished run's scanned output links.

    Resolves lineage from what the run already knows: graph runs map to their
    (job, node) via node_states.run_id; linear job runs via sessions.job_id.
    """
    if not project_id or not output_links:
        return
    prow = conn.execute("SELECT path FROM projects WHERE id = ?", (project_id,)).fetchone()
    root = Path(prow["path"]) if prow and prow["path"] else None
    rrow = conn.execute("SELECT kind FROM runs WHERE id = ?", (run_id,)).fetchone()
    run_kind = rrow["kind"] if rrow else None
    node = conn.execute(
        "SELECT job_id, node_id FROM node_states WHERE run_id = ?", (run_id,)
    ).fetchone()
    if node is not None:
        job_id, node_id = int(node["job_id"]), node["node_id"]
    else:
        srow = conn.execute("SELECT job_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        job_id = int(srow["job_id"]) if srow and srow["job_id"] else None
        node_id = None
    record_artifacts(
        conn,
        project_id,
        root,
        output_links,
        session_id=session_id,
        job_id=job_id,
        node_id=node_id,
        run_id=run_id,
        run_kind=run_kind,
    )


def approve_records_for_job(conn: sqlite3.Connection, job_id: int) -> int:
    """One status, two doors (T4): the job-review approve door. Auto-approves
    every draft/review record this job produced; superseded stays superseded."""
    now = iso_now()
    return conn.execute(
        "UPDATE artifact_records SET status = 'approved', approved_at = ?, updated_at = ? "
        "WHERE job_id = ? AND status IN ('draft', 'review')",
        (now, now, job_id),
    ).rowcount


def set_status(conn: sqlite3.Connection, record_id: int, status: str) -> bool:
    """The Archive-page door: edits the same status field the job door writes.
    Covers the late/batch/supersede cases; approving stamps approved_at."""
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = iso_now()
    if status == "approved":
        cur = conn.execute(
            "UPDATE artifact_records SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ?",
            (now, now, record_id),
        )
    else:
        cur = conn.execute(
            "UPDATE artifact_records SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, record_id),
        )
    return cur.rowcount > 0


def refresh_file_presence(
    conn: sqlite3.Connection, rows: list[dict[str, Any]], roots: dict[int, Path | None]
) -> None:
    """Durable-record contract: a moved/deleted file flips file_missing on the
    record instead of removing it (and flips back if the file returns). Cheap -
    called for one page of rows at a time."""
    now = iso_now()
    for row in rows:
        root = roots.get(int(row["project_id"]))
        if root is None:
            continue
        missing = 0 if (root / str(row["path"])).exists() else 1
        if missing != int(row["file_missing"] or 0):
            conn.execute(
                "UPDATE artifact_records SET file_missing = ?, updated_at = ? WHERE id = ?",
                (missing, now, row["id"]),
            )
        row["file_missing"] = bool(missing)


def seed_project(conn: sqlite3.Connection, project_id: int, root: Path, *, cap: int = 1000) -> int:
    """Migration seed: register the current scanner output as draft records so
    existing projects' artifacts appear in the registry on upgrade. Skips
    identities that already have a record, so re-running is harmless."""
    from .artifacts import scan_project_artifacts

    inserted = 0
    now = iso_now()
    for item in scan_project_artifacts(root, 0.0, cap=cap):
        typ = str(item.get("type") or "")
        path = str(item.get("path") or "")
        if not typ or not path:
            continue
        if _latest_for_identity(conn, project_id, typ, path) is not None:
            continue
        name = str(item.get("title") or Path(path).name)
        size, mtime = _stat(root, path)
        produced = (
            datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else now
        )
        slug = _unique_slug(conn, project_id, slugify(name), 1)
        conn.execute(
            "INSERT INTO artifact_records(project_id, slug, name, type, path, size, status, version, "
            "produced_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?)",
            (project_id, slug, name, typ, path, size, produced, now, now),
        )
        inserted += 1
    return inserted

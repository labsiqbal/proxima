"""Archive routes: the durable deliverable registry (Phase-1 slice 8, T4).

Paginated registry queries replace the capped mtime scan for the Archive
screen; each record has a permanent per-project address
(``/api/archive/{project}/{slug}``) and the ONE approval status field the
job-review door also writes.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Query

from .. import artifact_registry
from ..schemas import ArchiveStatusRequest

_TYPES = ("design", "app", "page", "image", "doc", "video-file", "file", "script-output")


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]

    def _record_payload(row: Any) -> dict[str, Any]:
        d = dict(row)
        path = str(d.get("path") or "")
        parent = str(Path(path).parent)
        d["area"] = "" if parent in (".", "") else parent + "/"
        d["file_missing"] = bool(d.get("file_missing"))
        return d

    _SELECT = (
        "SELECT ar.*, p.slug AS project_slug, p.name AS project_name, "
        "s.title AS session_title, j.title AS job_title, j.engine AS job_engine "
        "FROM artifact_records ar "
        "JOIN projects p ON p.id = ar.project_id "
        "LEFT JOIN sessions s ON s.id = ar.session_id "
        "LEFT JOIN jobs j ON j.id = ar.job_id "
    )

    def _filters(
        user: dict[str, Any],
        project: str,
        type_: str,
        status: str,
        q: str,
        days: int,
        path: str,
        *,
        skip_type_status: bool = False,
    ) -> tuple[str, list[Any]]:
        where = ["p.archived_at IS NULL", "p.owner_user_id = ?"]
        params: list[Any] = [user["id"]]
        if project:
            where.append("p.slug = ?")
            params.append(project)
        if path:
            where.append("ar.path = ?")
            params.append(path)
        if not skip_type_status:
            if type_:
                where.append("ar.type = ?")
                params.append(type_)
            if status:
                where.append("ar.status = ?")
                params.append(status)
        if q:
            where.append("(ar.name LIKE ? OR ar.path LIKE ? OR ar.slug LIKE ? OR j.title LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like, like])
        if days > 0:
            where.append("ar.produced_at >= datetime('now', ?)")
            params.append(f"-{days} days")
        return " WHERE " + " AND ".join(where), params

    @app.get("/api/archive")
    def list_archive(
        project: str = "",
        type: str = "",
        status: str = "",
        q: str = "",
        days: int = Query(default=0, ge=0, le=3650),
        path: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: dict[str, Any] = Depends(current_user),
    ):
        """Paginated deliverable records, newest first, with filter facet counts.
        No item cap: the whole registry is reachable page by page."""
        conn = db()
        where, params = _filters(user, project, type, status, q, days, path)
        total = conn.execute(f"SELECT COUNT(*) FROM artifact_records ar JOIN projects p ON p.id = ar.project_id LEFT JOIN jobs j ON j.id = ar.job_id{where}", params).fetchone()[0]
        rows = conn.execute(
            f"{_SELECT}{where} ORDER BY ar.produced_at DESC, ar.id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = [_record_payload(r) for r in rows]
        # Durable-record contract: reflect file presence on the page we return.
        roots: dict[int, Path | None] = {}
        for it in items:
            pid = int(it["project_id"])
            if pid not in roots:
                prow = conn.execute("SELECT path FROM projects WHERE id = ?", (pid,)).fetchone()
                roots[pid] = Path(prow["path"]) if prow and prow["path"] else None
        try:
            artifact_registry.refresh_file_presence(conn, items, roots)
        except Exception:
            logging.getLogger("proxima.archive").exception("file presence refresh failed (non-fatal)")
        # Facet counts share every filter EXCEPT type/status, so the chips stay
        # stable while one of them is selected (matches the ratified mockup).
        cwhere, cparams = _filters(user, project, type, status, q, days, path, skip_type_status=True)
        cbase = f"FROM artifact_records ar JOIN projects p ON p.id = ar.project_id LEFT JOIN jobs j ON j.id = ar.job_id{cwhere}"
        by_type = {r[0]: r[1] for r in conn.execute(f"SELECT ar.type, COUNT(*) {cbase} GROUP BY ar.type", cparams)}
        by_status = {r[0]: r[1] for r in conn.execute(f"SELECT ar.status, COUNT(*) {cbase} GROUP BY ar.status", cparams)}
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": {"by_type": by_type, "by_status": by_status},
        }

    @app.get("/api/archive/{slug}/{record_slug}")
    def get_archive_record(slug: str, record_slug: str, user: dict[str, Any] = Depends(current_user)):
        """One full record by its permanent address: metadata, lineage,
        version history, and prev/next within the project (newest first)."""
        p = visible_project(slug, user)
        conn = db()
        row = conn.execute(
            f"{_SELECT} WHERE ar.project_id = ? AND ar.slug = ?", (p["id"], record_slug)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="record not found")
        record = _record_payload(row)
        try:
            artifact_registry.refresh_file_presence(
                conn, [record], {int(p["id"]): Path(p["path"]) if p.get("path") else None}
            )
        except Exception:
            logging.getLogger("proxima.archive").exception("file presence refresh failed (non-fatal)")
        versions = [
            dict(v)
            for v in conn.execute(
                "SELECT id, slug, version, status, produced_at, approved_at, superseded_by "
                "FROM artifact_records WHERE project_id = ? AND type = ? AND path = ? "
                "ORDER BY version DESC, id DESC",
                (p["id"], record["type"], record["path"]),
            ).fetchall()
        ]
        nav = {}
        for key, cmp_, order in (("prev", ">", "ASC"), ("next", "<", "DESC")):
            n = conn.execute(
                "SELECT slug FROM artifact_records WHERE project_id = ? "
                f"AND (produced_at, id) {cmp_} (?, ?) ORDER BY produced_at {order}, id {order} LIMIT 1",
                (p["id"], record["produced_at"], record["id"]),
            ).fetchone()
            nav[key] = n["slug"] if n else None
        superseded_by_slug = None
        if record.get("superseded_by"):
            srow = conn.execute(
                "SELECT slug FROM artifact_records WHERE id = ?", (record["superseded_by"],)
            ).fetchone()
            superseded_by_slug = srow["slug"] if srow else None
        return {
            **record,
            "versions": versions,
            "prev_slug": nav["prev"],
            "next_slug": nav["next"],
            "superseded_by_slug": superseded_by_slug,
        }

    @app.post("/api/archive/records/{record_id}/status")
    def set_archive_status(
        record_id: int, payload: ArchiveStatusRequest, user: dict[str, Any] = Depends(current_user)
    ):
        """The Archive door of the ONE approval status (late/batch/supersede
        cases). Writes the same field the job-review approve writes."""
        if payload.status not in artifact_registry.STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(artifact_registry.STATUSES)}")
        conn = db()
        row = conn.execute(
            "SELECT ar.id, p.slug AS project_slug FROM artifact_records ar "
            "JOIN projects p ON p.id = ar.project_id WHERE ar.id = ?",
            (record_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="record not found")
        visible_project(row["project_slug"], user)
        artifact_registry.set_status(conn, record_id, payload.status)
        updated = conn.execute(f"{_SELECT} WHERE ar.id = ?", (record_id,)).fetchone()
        return _record_payload(updated)

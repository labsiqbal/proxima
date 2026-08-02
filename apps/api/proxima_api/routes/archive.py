"""Deliverable-record routes: the durable registry behind the Files
"Deliverables" lens (Phase-1 slice 8 T4; merged into Files by prune Part D,
#139).

Paginated registry queries power the lens list; each record has a permanent
per-project address (``/api/archive/{project}/{slug}``) and the ONE approval
status field the job-review door also writes. Record paths are
container-relative real paths (#139) - the same paths the Files tree
browses. The lens adds ``missing`` (the history filter: records whose file
is gone from disk) and ``/api/archive/badges`` (latest record per path, for
badges on the Files tree).
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
        d.pop("project_path")
        d["target"] = None
        return d

    _SELECT = (
        "SELECT ar.*, p.slug AS project_slug, p.name AS project_name, "
        "p.path AS project_path, "
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
        missing: int = -1,
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
        if missing in (0, 1):
            where.append("ar.file_missing = ?")
            params.append(missing)
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

    # Bound for the pre-filter presence sweep and the badge listing: enough
    # for any realistic single-owner registry while staying cheap.
    _PRESENCE_SWEEP_CAP = 2000

    def _refresh_presence_scope(
        conn: Any,
        user: dict[str, Any],
        project: str,
        q: str,
        days: int,
        path: str,
    ) -> None:
        """Refresh ``file_missing`` for every record in scope BEFORE a
        presence-filtered query, so the history lens sees a just-deleted
        file without needing a prior unfiltered listing."""
        where, params = _filters(user, project, "", "", q, days, path)
        rows = conn.execute(
            "SELECT ar.id, ar.project_id, ar.path, ar.file_missing "
            "FROM artifact_records ar JOIN projects p ON p.id = ar.project_id "
            f"LEFT JOIN jobs j ON j.id = ar.job_id{where} "
            "ORDER BY ar.produced_at DESC, ar.id DESC LIMIT ?",
            [*params, _PRESENCE_SWEEP_CAP],
        ).fetchall()
        artifact_registry.refresh_file_presence(conn, [dict(r) for r in rows])

    @app.get("/api/archive")
    def list_archive(
        project: str = "",
        type: str = "",
        status: str = "",
        q: str = "",
        days: int = Query(default=0, ge=0, le=3650),
        path: str = "",
        missing: int = Query(default=-1, ge=-1, le=1),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: dict[str, Any] = Depends(current_user),
    ):
        """Paginated deliverable records, newest first, with filter facet counts.
        No item cap: the whole registry is reachable page by page. ``missing``
        is the Files history filter: 1 lists only records whose file is gone
        from disk, 0 only records whose file exists, -1 (default) all."""
        conn = db()
        if missing in (0, 1):
            try:
                _refresh_presence_scope(conn, user, project, q, days, path)
            except Exception:
                logging.getLogger("proxima.archive").exception(
                    "presence sweep failed (non-fatal)"
                )
        where, params = _filters(user, project, type, status, q, days, path, missing=missing)
        total = conn.execute(f"SELECT COUNT(*) FROM artifact_records ar JOIN projects p ON p.id = ar.project_id LEFT JOIN jobs j ON j.id = ar.job_id{where}", params).fetchone()[0]
        rows = conn.execute(
            f"{_SELECT}{where} ORDER BY ar.produced_at DESC, ar.id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = [_record_payload(r) for r in rows]
        # Durable-record contract: reflect file presence on the page we return.
        try:
            artifact_registry.refresh_file_presence(conn, items)
        except Exception:
            logging.getLogger("proxima.archive").exception("file presence refresh failed (non-fatal)")
        # Facet counts share every filter EXCEPT type/status, so the chips stay
        # stable while one of them is selected (matches the ratified mockup).
        cwhere, cparams = _filters(user, project, type, status, q, days, path, missing=missing, skip_type_status=True)
        cbase = f"FROM artifact_records ar JOIN projects p ON p.id = ar.project_id LEFT JOIN jobs j ON j.id = ar.job_id{cwhere}"
        by_type = {r[0]: r[1] for r in conn.execute(f"SELECT ar.type, COUNT(*) {cbase} GROUP BY ar.type", cparams)}
        by_status = {r[0]: r[1] for r in conn.execute(f"SELECT ar.status, COUNT(*) {cbase} GROUP BY ar.status", cparams)}
        # The history-lens count ignores the presence filter itself, so the
        # History chip stays stable whichever lens is active.
        mwhere, mparams = _filters(user, project, "", "", q, days, path, missing=1, skip_type_status=True)
        missing_count = conn.execute(
            "SELECT COUNT(*) FROM artifact_records ar JOIN projects p ON p.id = ar.project_id "
            f"LEFT JOIN jobs j ON j.id = ar.job_id{mwhere}",
            mparams,
        ).fetchone()[0]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": {"by_type": by_type, "by_status": by_status, "missing": missing_count},
        }

    @app.get("/api/archive/badges")
    def archive_badges(project: str, user: dict[str, Any] = Depends(current_user)):
        """Badge data for the Files tree (the Deliverables lens, #139): the
        LATEST record per container-relative path in one project, with its
        approval status and file presence."""
        p = visible_project(project, user)
        conn = db()
        rows = conn.execute(
            "SELECT ar.id, ar.project_id, ar.slug, ar.name, ar.type, ar.path, "
            "ar.status, ar.version, ar.file_missing "
            "FROM artifact_records ar WHERE ar.project_id = ? AND ar.id = ("
            "  SELECT ar2.id FROM artifact_records ar2 "
            "  WHERE ar2.project_id = ar.project_id AND ar2.path = ar.path "
            "  ORDER BY ar2.version DESC, ar2.id DESC LIMIT 1) "
            "ORDER BY ar.produced_at DESC, ar.id DESC LIMIT ?",
            (p["id"], _PRESENCE_SWEEP_CAP),
        ).fetchall()
        items = [dict(r) for r in rows]
        try:
            artifact_registry.refresh_file_presence(conn, items)
        except Exception:
            logging.getLogger("proxima.archive").exception(
                "file presence refresh failed (non-fatal)"
            )
        for item in items:
            item.pop("project_id", None)
            item.pop("target", None)
            item["file_missing"] = bool(item.get("file_missing"))
        return {"items": items}

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
                conn,
                [record],
            )
        except Exception:
            logging.getLogger("proxima.archive").exception("file presence refresh failed (non-fatal)")
        versions = [
            dict(v)
            for v in conn.execute(
                "SELECT id, slug, version, status, produced_at, approved_at, superseded_by "
                "FROM artifact_records WHERE project_id = ? AND path = ? "
                "ORDER BY version DESC, id DESC",
                (p["id"], record["path"]),
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
        """The record-panel door of the ONE approval status (late/batch/
        supersede cases). Writes the same field the job-review approve writes."""
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
        record = _record_payload(updated)
        try:
            artifact_registry.refresh_file_presence(conn, [record])
        except Exception:
            logging.getLogger("proxima.archive").exception("file presence refresh failed (non-fatal)")
        return record

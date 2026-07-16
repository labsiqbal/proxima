"""Feature-gated graph workflow job and correction routes (ADR-0001)."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, HTTPException, status

from .. import features, state
from ..graph import descendant_node_ids, normalize_graph
from ..graph_advancers import NodeOutputError, validate_node_output  # pyright: ignore[reportMissingImports]
from ..schemas import (
    GraphDefinitionUpdateRequest,
    GraphJobCreateRequest,
    GraphNodeOutputEditRequest,
    GraphTemplateSaveRequest,
)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


def _decode_json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="stored graph data is invalid") from exc


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def _graph_node(graph: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return dict(node)
    raise HTTPException(status_code=404, detail="graph node not found")


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    profile_for_user = deps["profile_for_user"]
    _can_access = deps["_can_access"]
    _member_project_id = deps["_member_project_id"]

    def require_graph() -> None:
        features.require(cfg, features.WORKFLOW_GRAPH)

    def graph_job_or_404(job_id: int, user: dict[str, Any]) -> sqlite3.Row:
        row = db().execute(
            "SELECT * FROM jobs WHERE id = ? AND engine = 'graph'", (job_id,)
        ).fetchone()
        if row and not _can_access(row["created_by"], row["project_id"], user):
            row = None
        if not row:
            raise HTTPException(status_code=404, detail="graph job not found")
        return row

    def graph_job_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["graph"] = _decode_json(payload.get("graph"), {"nodes": [], "edges": []})
        payload["input"] = _decode_json(payload.get("input"), {})
        node_rows = db().execute(
            "SELECT * FROM node_states WHERE job_id = ? ORDER BY id", (payload["id"],)
        ).fetchall()
        nodes: list[dict[str, Any]] = []
        for raw in node_rows:
            node = dict(raw)
            node["inputs"] = _decode_json(node.get("inputs"), None)
            node["output"] = _decode_json(node.get("output"), None)
            node["checkpoint"] = _decode_json(node.get("checkpoint"), None)
            nodes.append(node)
        payload["node_states"] = nodes
        if payload.get("project_id"):
            project = db().execute(
                "SELECT slug FROM projects WHERE id = ?", (payload["project_id"],)
            ).fetchone()
            payload["project_slug"] = project["slug"] if project else None
        return payload

    def graph_template_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["graph"] = normalize_graph(payload.get("graph") or "")
        payload["steps"] = []
        payload["inputs"] = _decode_json(payload.get("inputs"), [])
        if payload.get("project_id"):
            project = db().execute(
                "SELECT slug FROM projects WHERE id = ?", (payload["project_id"],)
            ).fetchone()
            payload["project_slug"] = project["slug"] if project else None
        return payload

    def workflow_or_404(workflow_id: int, user: dict[str, Any]) -> sqlite3.Row:
        row = db().execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if row and (
            row["graph"] is None
            or not _can_access(row["created_by"], row["project_id"], user)
        ):
            row = None
        if not row:
            raise HTTPException(status_code=404, detail="workflow not found")
        return row

    def insert_node_states(
        conn: sqlite3.Connection, job_id: int, graph: Mapping[str, Any]
    ) -> None:
        for node in graph["nodes"]:
            conn.execute(
                """
                INSERT INTO node_states(job_id, node_id, status, output_kind)
                VALUES (?, ?, 'pending', ?)
                """,
                (job_id, node["id"], node["output_kind"]),
            )

    def ensure_reviewable(job: sqlite3.Row) -> None:
        if job["status"] != "review":
            raise HTTPException(
                status_code=409,
                detail="graph corrections require a job paused in review",
            )
        active = db().execute(
            "SELECT 1 FROM node_states WHERE job_id = ? AND status IN ('ready','running') LIMIT 1",
            (job["id"],),
        ).fetchone()
        if active:
            raise HTTPException(status_code=409, detail="graph job still has an active node")

    def mark_descendants_stale(
        conn: sqlite3.Connection,
        graph: Mapping[str, Any],
        job_id: int,
        node_id: str,
    ) -> list[str]:
        changed: list[str] = []
        for descendant_id in descendant_node_ids(graph, node_id):
            row = conn.execute(
                "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                (job_id, descendant_id),
            ).fetchone()
            if not row or row["status"] == "stale":
                continue
            transitioned = state.guarded_node_transition(
                conn,
                _as_int(row["id"]),
                "stale",
                (str(row["status"]),),
                _as_int(row["version"]),
                run_id=None,
                error=None,
                clear_started=True,
                clear_finished=True,
            )
            if transitioned:
                changed.append(descendant_id)
        return changed

    def corrected_value(
        job: sqlite3.Row,
        graph: Mapping[str, Any],
        node_id: str,
        value: Any,
    ) -> tuple[dict[str, Any], str]:
        node = _graph_node(graph, node_id)
        if node["output_kind"] == "text":
            if not isinstance(value, str):
                raise HTTPException(status_code=422, detail="text node output must be a string")
            answer = value
        else:
            answer = json.dumps(value, ensure_ascii=False)
        try:
            canonical = validate_node_output(
                app,
                {"job_id": job["id"], "project_id": job["project_id"]},
                node,
                answer,
            )
        except NodeOutputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return node, json.dumps(canonical, ensure_ascii=False)

    @app.post("/api/graph/jobs", status_code=status.HTTP_201_CREATED)
    def create_graph_job(
        payload: GraphJobCreateRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        graph = normalize_graph(payload.graph)
        project_id = _member_project_id(payload.project_id, payload.project_slug, user)
        profile = profile_for_user(payload.profile_id, user)
        workflow_id = None
        if payload.workflow_id is not None:
            workflow = workflow_or_404(payload.workflow_id, user)
            workflow_id = workflow["id"]
            if project_id is None:
                project_id = workflow["project_id"]
        visibility = "project" if project_id else "private"
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                session_cur = conn.execute(
                    """
                    INSERT INTO sessions(
                      title, project_id, owner_user_id, profile_id, runner_id,
                      visibility, mode
                    ) VALUES (?, ?, ?, ?, ?, ?, 'chat')
                    """,
                    (
                        payload.title[:200],
                        project_id,
                        user["id"],
                        profile["id"],
                        profile["runner_id"],
                        visibility,
                    ),
                )
                session_id = _as_int(session_cur.lastrowid)
                job_cur = conn.execute(
                    """
                    INSERT INTO jobs(
                      project_id, workflow_id, session_id, title, status, input,
                      steps_state, engine, graph, created_by
                    ) VALUES (?, ?, ?, ?, 'queued', ?, '[]', 'graph', ?, ?)
                    """,
                    (
                        project_id,
                        workflow_id,
                        session_id,
                        payload.title,
                        json.dumps(payload.input or {}, ensure_ascii=False),
                        json.dumps(graph, ensure_ascii=False),
                        user["id"],
                    ),
                )
                job_id = _as_int(job_cur.lastrowid)
                conn.execute(
                    "UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id)
                )
                insert_node_states(conn, job_id, graph)
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.get("/api/graph/jobs")
    def list_graph_jobs(
        project_id: int | None = None,
        project_slug: str | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        resolved_project_id = (
            _member_project_id(project_id, project_slug, user)
            if project_id is not None or project_slug
            else None
        )
        if resolved_project_id is None:
            rows = db().execute(
                "SELECT * FROM jobs WHERE engine = 'graph' AND archived_at IS NULL "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"]),
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT * FROM jobs WHERE engine = 'graph' AND archived_at IS NULL "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "AND project_id = ? ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"], resolved_project_id),
            ).fetchall()
        return {"items": [graph_job_payload(row) for row in rows]}

    @app.get("/api/graph/templates")
    def list_graph_templates(
        project_id: int | None = None,
        project_slug: str | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        resolved_project_id = (
            _member_project_id(project_id, project_slug, user)
            if project_id is not None or project_slug
            else None
        )
        if resolved_project_id is None:
            rows = db().execute(
                "SELECT * FROM workflows WHERE graph IS NOT NULL AND status != 'archived' "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"]),
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT * FROM workflows WHERE graph IS NOT NULL AND status != 'archived' "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "AND project_id = ? ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"], resolved_project_id),
            ).fetchall()
        return {"items": [graph_template_payload(row) for row in rows]}

    @app.get("/api/graph/jobs/{job_id}")
    def get_graph_job(
        job_id: int, user: dict[str, Any] = Depends(current_user)
    ):
        require_graph()
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/save-template", status_code=status.HTTP_201_CREATED)
    def save_graph_template(
        job_id: int,
        payload: GraphTemplateSaveRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        graph = normalize_graph(job["graph"] or "")
        name = (payload.name or str(job["title"] or "Graph workflow")).strip()
        if not name:
            raise HTTPException(status_code=422, detail="template name must not be blank")
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    """
                    INSERT INTO workflows(
                      project_id, name, description, category, status,
                      steps, graph, inputs, created_by
                    ) VALUES (?, ?, ?, ?, 'active', '[]', ?, '[]', ?)
                    """,
                    (
                        job["project_id"],
                        name,
                        payload.description,
                        payload.category,
                        json.dumps(graph, ensure_ascii=False),
                        user["id"],
                    ),
                )
                workflow_id = _as_int(cur.lastrowid)
                conn.execute(
                    "UPDATE jobs SET workflow_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (workflow_id, job_id),
                )
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        return {
            "id": workflow_id,
            "project_id": job["project_id"],
            "name": name,
            "description": payload.description,
            "category": payload.category,
            "status": "active",
            "steps": [],
            "graph": graph,
            "inputs": [],
        }

    @app.patch("/api/graph/jobs/{job_id}/graph")
    def update_graph_definition(
        job_id: int,
        payload: GraphDefinitionUpdateRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        if job["status"] != "queued":
            raise HTTPException(status_code=409, detail="only queued graph plans are editable")
        graph = normalize_graph(payload.graph)
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                claimed = conn.execute(
                    "UPDATE jobs SET graph = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'queued' AND engine = 'graph'",
                    (json.dumps(graph, ensure_ascii=False), job_id),
                )
                if claimed.rowcount == 0:
                    conn.execute("ROLLBACK")
                    raise HTTPException(status_code=409, detail="graph plan is no longer editable")
                conn.execute("DELETE FROM node_states WHERE job_id = ?", (job_id,))
                insert_node_states(conn, job_id, graph)
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/start")
    def start_graph_job(
        job_id: int, user: dict[str, Any] = Depends(current_user)
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        if job["status"] != "queued":
            return graph_job_payload(job)
        claimed = db().execute(
            "UPDATE jobs SET status='running', started_at=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued' AND engine='graph'",
            (job_id,),
        )
        if claimed.rowcount == 0:
            return graph_job_payload(graph_job_or_404(job_id, user))
        try:
            run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)
        except Exception as exc:
            db().execute(
                "UPDATE jobs SET status='queued', started_at=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='running'",
                (job_id,),
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not run_ids:
            # No runs is not automatically a failure: a trigger resolves without a
            # runner, so a graph of nothing but a trigger is already finished and
            # belongs in final review rather than reset to queued.
            unfinished = db().execute(
                "SELECT 1 FROM node_states WHERE job_id = ? AND status != 'done' LIMIT 1",
                (job_id,),
            ).fetchone()
            if unfinished:
                db().execute(
                    "UPDATE jobs SET status='queued', started_at=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='running'",
                    (job_id,),
                )
                raise HTTPException(status_code=409, detail="graph job has no dispatchable node")
            state.guarded_transition(
                db(),
                "jobs",
                job_id,
                "review",
                ("running",),
                set_extra="finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
            )
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.patch("/api/graph/jobs/{job_id}/nodes/{node_id}/output")
    def edit_node_output(
        job_id: int,
        node_id: str,
        payload: GraphNodeOutputEditRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_reviewable(job)
        graph = normalize_graph(job["graph"] or "")
        node, serialized = corrected_value(job, graph, node_id, payload.value)
        conn = db()
        dispatch = False
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                    (job_id, node_id),
                ).fetchone()
                if not row or row["status"] not in {"done", "review", "failed"}:
                    raise HTTPException(status_code=409, detail="node output is not editable")
                corrected = state.guarded_node_transition(
                    conn,
                    _as_int(row["id"]),
                    "done",
                    (str(row["status"]),),
                    _as_int(row["version"]),
                    output_kind=str(node["output_kind"]),
                    output=serialized,
                    error=None,
                    mark_finished=True,
                )
                if not corrected:
                    raise HTTPException(status_code=409, detail="node changed concurrently")
                descendants = mark_descendants_stale(conn, graph, job_id, node_id)
                if descendants:
                    resumed = state.guarded_transition(
                        conn,
                        "jobs",
                        job_id,
                        "running",
                        ("review",),
                        set_extra="updated_at=CURRENT_TIMESTAMP, finished_at=NULL",
                    )
                    dispatch = resumed
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        if dispatch:
            app.state.worker.graph_executor.dispatch_ready(job_id)
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/nodes/{node_id}/rerun")
    def rerun_node(
        job_id: int,
        node_id: str,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_reviewable(job)
        graph = normalize_graph(job["graph"] or "")
        _graph_node(graph, node_id)
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                    (job_id, node_id),
                ).fetchone()
                if not row or row["status"] not in {"done", "review", "failed"}:
                    raise HTTPException(status_code=409, detail="node is not rerunnable")
                stale = state.guarded_node_transition(
                    conn,
                    _as_int(row["id"]),
                    "stale",
                    (str(row["status"]),),
                    _as_int(row["version"]),
                    run_id=None,
                    error=None,
                    clear_started=True,
                    clear_finished=True,
                )
                if not stale:
                    raise HTTPException(status_code=409, detail="node changed concurrently")
                mark_descendants_stale(conn, graph, job_id, node_id)
                resumed = state.guarded_transition(
                    conn,
                    "jobs",
                    job_id,
                    "running",
                    ("review",),
                    set_extra="updated_at=CURRENT_TIMESTAMP, finished_at=NULL",
                )
                if not resumed:
                    raise HTTPException(status_code=409, detail="job changed concurrently")
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        app.state.worker.graph_executor.dispatch_ready(job_id)
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/nodes/{node_id}/approve")
    def approve_node(
        job_id: int,
        node_id: str,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_reviewable(job)
        conn = db()
        dispatch = False
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                    (job_id, node_id),
                ).fetchone()
                if not row or row["status"] != "review":
                    raise HTTPException(status_code=409, detail="node is not awaiting review")
                approved = state.guarded_node_transition(
                    conn,
                    _as_int(row["id"]),
                    "done",
                    ("review",),
                    _as_int(row["version"]),
                    error=None,
                )
                if not approved:
                    raise HTTPException(status_code=409, detail="node changed concurrently")
                remaining = conn.execute(
                    "SELECT COUNT(*) AS c FROM node_states WHERE job_id = ? AND status != 'done'",
                    (job_id,),
                ).fetchone()["c"]
                if remaining:
                    dispatch = state.guarded_transition(
                        conn,
                        "jobs",
                        job_id,
                        "running",
                        ("review",),
                        set_extra="updated_at=CURRENT_TIMESTAMP",
                    )
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        if dispatch:
            app.state.worker.graph_executor.dispatch_ready(job_id)
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/approve")
    def approve_graph_job(
        job_id: int, user: dict[str, Any] = Depends(current_user)
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_reviewable(job)
        incomplete = db().execute(
            "SELECT 1 FROM node_states WHERE job_id = ? AND status != 'done' LIMIT 1",
            (job_id,),
        ).fetchone()
        if incomplete:
            raise HTTPException(status_code=409, detail="all graph nodes must be done")
        approved = state.guarded_transition(
            db(),
            "jobs",
            job_id,
            "done",
            ("review",),
            set_extra="finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
        )
        if not approved:
            raise HTTPException(status_code=409, detail="graph job changed concurrently")
        return graph_job_payload(graph_job_or_404(job_id, user))

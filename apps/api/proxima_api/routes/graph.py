"""Feature-gated graph workflow job and correction routes (ADR-0001)."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, status

from .. import artifact_registry, features, repo_remote, satpam, scripts_library, state, worktrees
from ..graph import (
    GraphValidationError,
    descendant_node_ids,
    normalize_graph,
    plan_target_problems,
    repo_target_paths,
)
from ..graph_advancers import NodeOutputError, validate_node_output  # pyright: ignore[reportMissingImports]
from ..job_checkpoints import create_checkpoint
from ..schemas import (
    GraphDefinitionUpdateRequest,
    GraphJobCreateRequest,
    GraphNodeAnswerRequest,
    GraphNodeOutputEditRequest,
    GraphScriptApproveRequest,
    GraphTemplateSaveRequest,
)

# The approval card renders the script body; cap what one response carries so a
# runaway file cannot flood the UI. The sha256 always covers the WHOLE file.
MAX_SCRIPT_PREVIEW_BYTES = 100_000


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
        # Repo plans (slice 2): surface the worktree lifecycle exactly as the
        # linear job payload does. Absent row (flag-off installs, non-repo
        # plans) ⇒ payload unchanged.
        wt = worktrees.job_worktree_row(db(), payload["id"])
        if wt:
            payload["worktree"] = worktrees.worktree_payload(wt)
        # Satpam interventions (slice 12): the plan's supervision timeline,
        # incl. any pending restart approval card. Attached only when non-empty.
        satpam_rows = satpam.interventions_payload(db(), payload["id"])
        if satpam_rows:
            payload["satpam"] = satpam_rows
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

    def require_valid_targets(graph: Mapping[str, Any], project_id: int | None) -> None:
        """Reject a plan whose job targets cannot bind to this project (T1/T2).

        The target is pinned at slice time precisely so it CANNOT be discovered
        at runtime — so a target naming a non-registered area is a hard 422 at
        the moment the plan is created or edited, when the owner can still fix
        it. Ambiguous targets pass here: they are surfaced questions, and they
        block start instead (see start_graph_job).
        """
        code_paths: list[str] = []
        if project_id is not None:
            code_paths = [
                r["rel_path"] for r in db().execute(
                    "SELECT rel_path FROM project_areas WHERE project_id = ? "
                    "AND kind = 'code' AND source != 'excluded'",
                    (project_id,),
                ).fetchall()
            ]
        elif repo_target_paths(graph):
            raise HTTPException(
                status_code=422,
                detail="this plan has repo jobs but no project - link it to a project so its code areas exist",
            )
        problems = plan_target_problems(graph, code_paths)
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))

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

    def ensure_correctable(job: sqlite3.Row) -> None:
        """Corrections (edit a node's output, rerun a node) are allowed while the job
        is paused in review AND after final approval: 'done' is just an approved
        review, and a correction re-runs the affected slice the same way either way.
        What stays frozen after start is the graph itself, not its outputs."""
        if job["status"] not in ("review", "done"):
            raise HTTPException(
                status_code=409,
                detail="graph corrections require a job paused in review or completed",
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
        try:
            graph = normalize_graph(payload.graph)
        except GraphValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project_id = _member_project_id(payload.project_id, payload.project_slug, user)
        profile = profile_for_user(payload.profile_id, user)
        workflow_id = None
        if payload.workflow_id is not None:
            workflow = workflow_or_404(payload.workflow_id, user)
            workflow_id = workflow["id"]
            if project_id is None:
                project_id = workflow["project_id"]
        require_valid_targets(graph, project_id)
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
                    ) VALUES (?, ?, ?, ?, 'active', '[]', ?, ?, ?)
                    """,
                    (
                        job["project_id"],
                        name,
                        payload.description,
                        payload.category,
                        json.dumps(graph, ensure_ascii=False),
                        # Stored as declared, exactly as the linear route does it: a
                        # graph-only validator would let the two surfaces disagree.
                        json.dumps(payload.inputs or [], ensure_ascii=False),
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
            "inputs": payload.inputs or [],
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
        try:
            graph = normalize_graph(payload.graph)
        except GraphValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        require_valid_targets(graph, job["project_id"])
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
        # Repo jobs reserve their path (slice 2 wiring, flag-gated): cut the
        # plan's worktree BEFORE claiming running, so a refused cut (dirty
        # repo, detached HEAD, no commits) surfaces loudly and leaves the plan
        # queued for a clean retry - same ordering as the linear start. Shared
        # with the scheduler so cron / Run-now cannot skip isolation.
        try:
            worktrees.bind_graph_job_repo_worktree(db(), app.state.config, job)
        except worktrees.WorktreeError as exc:
            raise HTTPException(
                status_code=409, detail=f"cannot start repo plan: {exc}"
            ) from exc
        job = graph_job_or_404(job_id, user)
        # Alpha graph plans capture their latest queued node state after any
        # isolated worktree exists and before ready nodes are dispatched.
        if job["alpha_session_id"] is not None:
            create_checkpoint(db(), job_id)
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

    # Alpha invokes these proven route services in-process. They remain private
    # Python callables, not loopback HTTP endpoints or prompt-granted authority.
    app.state.alpha_create_graph_job = create_graph_job
    app.state.alpha_start_graph_job = start_graph_job

    @app.patch("/api/graph/jobs/{job_id}/nodes/{node_id}/output")
    def edit_node_output(
        job_id: int,
        node_id: str,
        payload: GraphNodeOutputEditRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_correctable(job)
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
                        ("review", "done"),
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
        ensure_correctable(job)
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
                    ("review", "done"),
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
        ensure_correctable(job)
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

    @app.post("/api/graph/jobs/{job_id}/nodes/{node_id}/answer")
    def answer_node_decision(
        job_id: int,
        node_id: str,
        payload: GraphNodeAnswerRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Answer a decision-held node's question (slice 12, T10 #4). The node
        parked in review via its DECISION_NEEDED output; the owner's answer is
        stored and the node re-runs with the decision in its prompt. Unlike the
        correction routes this works while the plan is still RUNNING - that is
        the point: independent branches kept dispatching during the hold."""
        require_graph()
        job = graph_job_or_404(job_id, user)
        graph = normalize_graph(job["graph"] or "")
        _graph_node(graph, node_id)
        answer_text = payload.answer.strip()
        if not answer_text:
            raise HTTPException(status_code=400, detail="an answer is required")
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                    (job_id, node_id),
                ).fetchone()
                if not row or row["status"] != "review" or not row["question"]:
                    raise HTTPException(status_code=409, detail="node has no open decision")
                staled = state.guarded_node_transition(
                    conn,
                    _as_int(row["id"]),
                    "stale",
                    ("review",),
                    _as_int(row["version"]),
                    run_id=None,
                    error=None,
                    clear_started=True,
                    clear_finished=True,
                )
                if not staled:
                    raise HTTPException(status_code=409, detail="node changed concurrently")
                conn.execute(
                    "UPDATE node_states SET answer = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (answer_text, _as_int(row["id"])),
                )
                mark_descendants_stale(conn, graph, job_id, node_id)
                if job["status"] in ("review", "done"):
                    state.guarded_transition(
                        conn,
                        "jobs",
                        job_id,
                        "running",
                        (str(job["status"]),),
                        set_extra="updated_at=CURRENT_TIMESTAMP, finished_at=NULL",
                    )
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        app.state.worker.graph_executor.dispatch_ready(job_id)
        return graph_job_payload(graph_job_or_404(job_id, user))

    def _script_node_file(job: sqlite3.Row, node_id: str) -> tuple[str, bytes]:
        """Resolve a graph job's script node to (rel_path, current bytes) or
        raise the shared 4xx ladder. One read — hash and display must come
        from the same bytes (audit F4)."""
        graph = normalize_graph(job["graph"] or "")
        node = _graph_node(graph, node_id)
        if node.get("type") != "script":
            raise HTTPException(status_code=422, detail="this job step does not run a script")
        if not job["project_id"]:
            raise HTTPException(status_code=409, detail="script steps need a project container")
        project = db().execute(
            "SELECT path FROM projects WHERE id = ?", (job["project_id"],)
        ).fetchone()
        if not project or not project["path"]:
            raise HTTPException(status_code=409, detail="this plan's project path is unavailable")
        try:
            rel = scripts_library.normalize_script_rel_path(str(node["command"]))
            script_path = scripts_library.resolve_script(Path(project["path"]), rel)
            return rel, script_path.read_bytes()
        except (scripts_library.ScriptResolutionError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/graph/jobs/{job_id}/nodes/{node_id}/script")
    def read_node_script(
        job_id: int,
        node_id: str,
        user: dict[str, Any] = Depends(current_user),
    ):
        """What the approval card shows (audit F4): the script's CURRENT bytes
        and their sha256, read together, so the owner reviews the actual
        content — never just a filename. The returned sha256 is what the
        approve request must echo back."""
        require_graph()
        job = graph_job_or_404(job_id, user)
        rel, data = _script_node_file(job, node_id)
        shown = data[:MAX_SCRIPT_PREVIEW_BYTES]
        return {
            "script": f"scripts/{rel}",
            "sha256": scripts_library.hash_bytes(data),
            "content": shown.decode("utf-8", errors="replace"),
            "truncated": len(data) > len(shown),
            "trusted_sha256": scripts_library.trusted_hash(db(), _as_int(job["project_id"]), rel),
        }

    @app.post("/api/graph/jobs/{job_id}/nodes/{node_id}/approve-script")
    def approve_node_script(
        job_id: int,
        node_id: str,
        payload: GraphScriptApproveRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """The one-time, hash-bound script approval (T6 #5, captain's decision).

        A script step blocked on trust and the plan is paused in review; this
        records the script's CURRENT content hash as approved — recomputed from
        the file now, never taken from the stored error. The request carries
        the sha256 the owner actually reviewed (the approval card fetched
        content + hash together via GET .../script); if the file on disk no
        longer matches, the approval is refused with 409 instead of silently
        trusting whatever an agent wrote in the meantime (audit F4). Then the
        node reruns the same way an ordinary rerun does. Unchanged scripts
        never come back here; an edited script's hash mismatch does.
        """
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_correctable(job)
        rel, data = _script_node_file(job, node_id)
        digest = scripts_library.hash_bytes(data)
        if payload.expected_sha256 != digest:
            raise HTTPException(
                status_code=409,
                detail=(
                    "the script's content changed on disk after you reviewed it "
                    f"(reviewed sha256 {payload.expected_sha256[:12]}…, current {digest[:12]}…) "
                    "— re-open the approval card to review the current version"
                ),
            )
        graph = normalize_graph(job["graph"] or "")
        conn = db()
        approval_run_id: int | None = None
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                    (job_id, node_id),
                ).fetchone()
                if not row or row["status"] != "failed":
                    raise HTTPException(
                        status_code=409,
                        detail="this step is not blocked on a script approval",
                    )
                scripts_library.record_trust(
                    conn, _as_int(job["project_id"]), rel, digest, _as_int(user["id"])
                )
                conn.execute(
                    "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                    "VALUES (?, 'script.trust.approve', 'script', ?, ?)",
                    (
                        user["id"],
                        f"{job['project_id']}:{rel}",
                        json.dumps({"content_hash": digest, "job_id": job_id, "node_id": node_id}),
                    ),
                )
                approval_run_id = row["run_id"]
                stale = state.guarded_node_transition(
                    conn,
                    _as_int(row["id"]),
                    "stale",
                    ("failed",),
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
                    ("review", "done"),
                    set_extra="updated_at=CURRENT_TIMESTAMP, finished_at=NULL",
                )
                if not resumed:
                    raise HTTPException(status_code=409, detail="job changed concurrently")
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        # The approval belongs in the job timeline (T6 #4): attach it to the
        # attempt that was blocked, whose session is the step's own thread.
        if approval_run_id:
            run_row = db().execute(
                "SELECT session_id FROM runs WHERE id = ?", (approval_run_id,)
            ).fetchone()
            if run_row:
                app.state.worker.add_event(
                    _as_int(approval_run_id),
                    _as_int(run_row["session_id"]),
                    job["project_id"],
                    "script.trust.approved",
                    {
                        "job_id": job_id,
                        "node_id": node_id,
                        "script": f"scripts/{rel}",
                        "content_hash": digest,
                    },
                )
        app.state.worker.graph_executor.dispatch_ready(job_id)
        return graph_job_payload(graph_job_or_404(job_id, user))

    # Global Attention reuses the exact hash-visible read/approve services in
    # process rather than duplicating trust transitions or calling loopback HTTP.
    app.state.alpha_read_node_script = read_node_script
    app.state.alpha_approve_node_script = approve_node_script

    @app.post("/api/graph/jobs/{job_id}/approve")
    def approve_graph_job(
        job_id: int, user: dict[str, Any] = Depends(current_user)
    ):
        require_graph()
        job = graph_job_or_404(job_id, user)
        ensure_correctable(job)
        incomplete = db().execute(
            "SELECT 1 FROM node_states WHERE job_id = ? AND status != 'done' LIMIT 1",
            (job_id,),
        ).fetchone()
        if incomplete:
            raise HTTPException(status_code=409, detail="all graph nodes must be done")
        # Repo plan (slice 2, flag-gated): the final approve is the merge point
        # (T1 local-first) - land the plan's branch on its base branch before
        # the plan closes. Refusals and conflicts surface as 409 and PARK the
        # plan in review (worktree kept for resolution); approve again after
        # resolving to retry. Same contract as the linear approve.
        if features.enabled(app.state.config, features.REPO_WORKTREES):
            wt = worktrees.job_worktree_row(db(), job_id)
            if wt and wt["status"] in ("active", "conflict", "merging"):
                try:
                    merged = worktrees.merge_job_worktree(db(), job, wt)
                except worktrees.WorktreeError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail=f"merge blocked - plan stays in review: {exc}",
                    ) from exc
                # T9 (slice 11): push AFTER the local merge, only on explicit
                # per-area opt-in. A failed push never un-merges and never
                # fails the approve - it surfaces on the worktree row as a
                # job-level blocker (retry: POST /api/jobs/{id}/push).
                try:
                    repo_remote.push_after_merge(db(), merged)
                except Exception:
                    logging.getLogger("proxima.graph").exception("push after merge failed unexpectedly (plan stays merged)")
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
        # One status, two doors (T4): approving the plan auto-approves the
        # deliverable records its nodes produced. Best-effort - the verdict
        # stands even if the registry write fails.
        try:
            artifact_registry.approve_records_for_job(db(), job_id)
        except Exception:
            logging.getLogger("proxima.graph").exception("registry approve sync failed (non-fatal)")
        return graph_job_payload(graph_job_or_404(job_id, user))

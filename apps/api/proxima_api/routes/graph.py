"""Graph workflow job and correction routes (ADR-0001)."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, HTTPException, status

from .. import (
    artifact_registry,
    master_decisions,
    layout_map,
    repo_remote,
    schedule_policy,
    satpam,
    scripts_library,
    state,
    workflows as wf,
    worktrees,
)
from ..graph import (
    GraphValidationError,
    descendant_node_ids,
    normalize_graph,
    plan_target_problems,
    resolved_manual_trigger_input,
    repo_target_paths,
)
from ..graph_advancers import NodeOutputError, validate_node_output  # pyright: ignore[reportMissingImports]
from ..job_checkpoints import create_checkpoint
from ..schemas import (
    GraphDefinitionUpdateRequest,
    GraphJobCreateRequest,
    GraphJobStartRequest,
    GraphNodeAnswerRequest,
    GraphNodeOutputEditRequest,
    GraphScriptApproveRequest,
    GraphTemplateSaveRequest,
)
from ..master_persistence import canonical_job_payload
from ..task_state_events import append_task_update

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


def _hydrate_trigger_contract(
    graph: Mapping[str, Any],
    legacy_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project legacy workflow metadata onto the trigger node.

    New graphs store the intake form and mode directly on their trigger. Existing
    workflow rows keep ``inputs`` in their original column, so the API adds the
    node field on read. Schedule rows stay separate: the workflow's availability
    and manual trigger contract must not change merely because automation exists.
    A legacy graph with declared inputs but no trigger gets a no-op trigger
    connected to every former root.
    """
    hydrated = {
        "nodes": [dict(node) for node in graph.get("nodes", [])],
        "edges": [dict(edge) for edge in graph.get("edges", [])],
    }
    trigger = next(
        (node for node in hydrated["nodes"] if node.get("type") == "trigger"),
        None,
    )
    if trigger is None and legacy_inputs:
        node_ids = {str(node.get("id")) for node in hydrated["nodes"]}
        trigger_id = "trigger"
        suffix = 2
        while trigger_id in node_ids:
            trigger_id = f"trigger-{suffix}"
            suffix += 1
        incoming = {str(edge.get("to")) for edge in hydrated["edges"]}
        roots = [
            str(node["id"])
            for node in hydrated["nodes"]
            if str(node.get("id")) not in incoming
        ]
        trigger = {
            "id": trigger_id,
            "type": "trigger",
            "trigger_kind": "manual",
            "name": "When I run it",
            "instruction": "",
            "output_kind": "json",
        }
        hydrated["nodes"].insert(0, trigger)
        hydrated["edges"] = [
            {"from": trigger_id, "to": root}
            for root in roots
        ] + hydrated["edges"]
    if trigger is None:
        return normalize_graph(hydrated)
    if "inputs" not in trigger:
        trigger["inputs"] = legacy_inputs
    return normalize_graph(hydrated)


def _trigger_node(graph: Mapping[str, Any]) -> dict[str, Any] | None:
    return next(
        (dict(node) for node in graph.get("nodes", []) if node.get("type") == "trigger"),
        None,
    )


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    profile_for_user = deps["profile_for_user"]
    _can_access = deps["_can_access"]
    _member_project_id = deps["_member_project_id"]

    def _process_task_projection(task_event: dict[str, int]) -> None:
        outbox_id = task_event.get("projection_outbox_id")
        projection = getattr(app.state, "master_projection", None)
        if outbox_id is not None and projection is not None:
            projection.safe_process_task_outbox(outbox_id)

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
                "SELECT slug, name FROM projects WHERE id = ?", (payload["project_id"],)
            ).fetchone()
            payload["project_slug"] = project["slug"] if project else None
            payload["project_name"] = project["name"] if project else None
        return canonical_job_payload(payload, connection=db())

    def graph_template_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["steps"] = []
        payload["inputs"] = _decode_json(payload.get("inputs"), [])
        payload["graph"] = _hydrate_trigger_contract(
            normalize_graph(payload.get("graph") or ""),
            payload["inputs"],
        )
        trigger = _trigger_node(payload["graph"])
        if trigger is not None and "inputs" in trigger:
            payload["inputs"] = trigger["inputs"]
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
        include_archived: bool = False,
        user: dict[str, Any] = Depends(current_user),
    ):
        resolved_project_id = (
            _member_project_id(project_id, project_slug, user)
            if project_id is not None or project_slug
            else None
        )
        archive_filter = "" if include_archived else "AND status != 'archived' "
        if resolved_project_id is None:
            rows = db().execute(
                f"SELECT * FROM workflows WHERE graph IS NOT NULL {archive_filter}"
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"]),
            ).fetchall()
        else:
            rows = db().execute(
                f"SELECT * FROM workflows WHERE graph IS NOT NULL {archive_filter}"
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
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/save-template", status_code=status.HTTP_201_CREATED)
    def save_graph_template(
        job_id: int,
        payload: GraphTemplateSaveRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        job = graph_job_or_404(job_id, user)
        graph = normalize_graph(job["graph"] or "")
        trigger = _trigger_node(graph)
        # Old clients declared inputs in this request. Keep accepting that shape
        # only for pre-migration graphs whose trigger has no canonical field.
        declared_inputs = (
            trigger["inputs"]
            if trigger is not None and "inputs" in trigger
            else payload.inputs or []
        )
        graph = _hydrate_trigger_contract(graph, declared_inputs)
        trigger = _trigger_node(graph)
        schedule_config = trigger.get("schedule") if trigger is not None else None
        if schedule_config is not None and not wf.cron_valid(schedule_config["cron"]):
            raise HTTPException(
                status_code=422,
                detail="invalid trigger schedule cron; expected five valid fields",
            )
        if schedule_config is not None and not schedule_policy.timezone_valid(
            schedule_config["timezone"]
        ):
            raise HTTPException(
                status_code=422,
                detail="invalid trigger schedule timezone; use an IANA timezone name",
            )
        if (
            schedule_config is not None
            and schedule_config["enabled"]
            and schedule_policy.unresolved_required_inputs(
                {"graph": graph, "inputs": declared_inputs}, {}
            )
        ):
            raise HTTPException(
                status_code=422,
                detail=schedule_policy.missing_sources_detail(
                    {"graph": graph, "inputs": declared_inputs}, {}
                ),
            )
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
                        # Compatibility projection for RunModal and old clients.
                        # The trigger node is the canonical authoring location.
                        json.dumps(declared_inputs, ensure_ascii=False),
                        user["id"],
                    ),
                )
                workflow_id = _as_int(cur.lastrowid)
                if schedule_config is not None:
                    conn.execute(
                        """
                        INSERT INTO schedules(
                          workflow_id, project_id, cron, timezone, input, overlap_policy,
                          enabled, created_by
                        ) VALUES (?, ?, ?, ?, '{}', ?, ?, ?)
                        """,
                        (
                            workflow_id,
                            job["project_id"],
                            schedule_config["cron"],
                            schedule_config["timezone"],
                            schedule_config["overlap_policy"],
                            1 if schedule_config["enabled"] else 0,
                            user["id"],
                        ),
                    )
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
            "inputs": declared_inputs,
        }

    @app.patch("/api/graph/jobs/{job_id}/graph")
    def update_graph_definition(
        job_id: int,
        payload: GraphDefinitionUpdateRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        job = graph_job_or_404(job_id, user)
        if payload.graph is None and payload.title is None:
            raise HTTPException(status_code=422, detail="graph or title is required")
        title = payload.title.strip() if payload.title is not None else None
        if payload.title is not None and not title:
            raise HTTPException(status_code=422, detail="plan title must not be blank")
        if payload.graph is not None and job["status"] != "queued":
            raise HTTPException(status_code=409, detail="only queued graph plans are editable")
        graph = None
        if payload.graph is not None:
            try:
                graph = normalize_graph(payload.graph)
            except GraphValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            require_valid_targets(graph, job["project_id"])
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if graph is not None:
                    claimed = conn.execute(
                        "UPDATE jobs SET graph = ?, title = COALESCE(?, title), "
                        "updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND status = 'queued' AND engine = 'graph'",
                        (json.dumps(graph, ensure_ascii=False), title, job_id),
                    )
                    if claimed.rowcount == 0:
                        conn.execute("ROLLBACK")
                        raise HTTPException(
                            status_code=409,
                            detail="graph plan is no longer editable",
                        )
                    conn.execute("DELETE FROM node_states WHERE job_id = ?", (job_id,))
                    insert_node_states(conn, job_id, graph)
                else:
                    conn.execute(
                        "UPDATE jobs SET title = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND engine = 'graph'",
                        (title, job_id),
                    )
                if title is not None and job["session_id"] is not None:
                    # The plan's authoring thread shares its identity. Mark this as a
                    # deliberate title so the chat auto-title path can never replace it.
                    conn.execute(
                        "UPDATE sessions SET title = ?, manual_title = 1, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (title, job["session_id"]),
                    )
                conn.execute("COMMIT")
            except Exception as exc:
                _rollback(conn)
                raise exc
        return graph_job_payload(graph_job_or_404(job_id, user))

    @app.post("/api/graph/jobs/{job_id}/start")
    def start_graph_job(
        job_id: int,
        payload: GraphJobStartRequest | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        job = graph_job_or_404(job_id, user)
        if job["status"] != "queued":
            return graph_job_payload(job)
        graph_source = str(job["graph"] or "")
        input_source = str(job["input"] or "{}")
        try:
            graph = normalize_graph(graph_source)
            stored_input = _decode_json(input_source, {})
            if not isinstance(stored_input, dict):
                raise GraphValidationError("stored graph input must be an object")
            candidate_input = dict(stored_input)
            if payload is not None and payload.input is not None:
                candidate_input.update(payload.input)
            resolved_input = resolved_manual_trigger_input(graph, candidate_input)
        except GraphValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        if (
            str(job["graph"] or "") != graph_source
            or str(job["input"] or "{}") != input_source
        ):
            raise HTTPException(
                status_code=409,
                detail="workflow changed while starting; review the saved graph and try again",
            )
        # Master graph plans capture their latest queued node state after any
        # isolated worktree exists and before ready nodes are dispatched.
        if job["origin_master_session_id"] is not None:
            create_checkpoint(db(), job_id)
        claimed = db().execute(
            "UPDATE jobs SET input=?, status='running', started_at=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued' AND engine='graph' "
            "AND graph=? AND input=?",
            (
                json.dumps(resolved_input, ensure_ascii=False),
                job_id,
                graph_source,
                input_source,
            ),
        )
        if claimed.rowcount == 0:
            current = graph_job_or_404(job_id, user)
            if current["status"] == "queued":
                raise HTTPException(
                    status_code=409,
                    detail="workflow changed while starting; review the saved graph and try again",
                )
            return graph_job_payload(current)

        def restore_preclaim_queued() -> None:
            db().execute(
                "UPDATE jobs SET input=?, status='queued', started_at=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                (input_source, job_id),
            )

        try:
            run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)
        except Exception as exc:
            restore_preclaim_queued()
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
                restore_preclaim_queued()
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
            "SELECT id, path, path_identity FROM projects WHERE id = ?",
            (job["project_id"],),
        ).fetchone()
        if not project or not project["path"]:
            raise HTTPException(status_code=409, detail="this plan's project path is unavailable")
        try:
            rel = scripts_library.normalize_script_rel_path(str(node["command"]))
            # The script library folder is per-project (layout map, prune C4).
            script_path = scripts_library.resolve_script(
                layout_map.project_layout(db(), project).dir("scripts"), rel
            )
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
    app.state.master_read_node_script = read_node_script
    app.state.master_approve_node_script = approve_node_script

    @app.post("/api/graph/jobs/{job_id}/approve")
    def approve_graph_job(
        job_id: int, user: dict[str, Any] = Depends(current_user)
    ):
        job = graph_job_or_404(job_id, user)
        pending_decision = master_decisions.pending_decision_for_job(db(), job_id)
        if pending_decision:
            raise HTTPException(
                status_code=409,
                detail=master_decisions.pending_decision_conflict(
                    int(pending_decision["id"])
                ),
            )
        ensure_correctable(job)
        incomplete = db().execute(
            "SELECT 1 FROM node_states WHERE job_id = ? AND status != 'done' LIMIT 1",
            (job_id,),
        ).fetchone()
        if incomplete:
            raise HTTPException(status_code=409, detail="all graph nodes must be done")
        # Repo plan (slice 2): claim a durable approval generation
        # before any Git side effect so decision creation cannot land mid-merge.
        # Never hold db_lock across external Git work. Same contract as linear.
        wt = worktrees.job_worktree_row(db(), job_id)
        needs_git_merge = bool(
            wt and wt["status"] in ("active", "conflict", "merging")
        )
        use_approval_intent = bool(
            wt is not None
            and wt["status"] in ("active", "conflict", "merging", "merged")
        )
        approval_intent = None
        resume_merged = False
        if use_approval_intent:
            conn = db()
            with app.state.db_lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    approval_intent, resume_merged = (
                        master_decisions.claim_final_approval_intent(
                            conn,
                            job_id=job_id,
                            actor_user_id=int(user["id"]),
                            allow_resume_merged=True,
                        )
                    )
                    conn.execute("COMMIT")
                except master_decisions.MasterDecisionError as exc:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    if exc.code == "master_decision_pending":
                        pending = master_decisions.pending_decision_for_job(
                            db(), job_id
                        )
                        detail = (
                            master_decisions.pending_decision_conflict(
                                int(pending["id"])
                            )
                            if pending is not None
                            else {"code": exc.code, "message": str(exc)}
                        )
                    else:
                        detail = {"code": exc.code, "message": str(exc)}
                    raise HTTPException(
                        status_code=exc.status_code, detail=detail
                    ) from exc
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            if needs_git_merge and not resume_merged:
                try:
                    merged = worktrees.merge_job_worktree(db(), job, wt)
                except worktrees.WorktreeError as exc:
                    conn = db()
                    with app.state.db_lock:
                        conn.execute("BEGIN IMMEDIATE")
                        try:
                            master_decisions.release_final_approval_intent(
                                conn,
                                job_id=job_id,
                                generation=int(approval_intent["generation"]),
                                error=str(exc),
                            )
                            conn.execute("COMMIT")
                        except Exception:
                            if conn.in_transaction:
                                conn.execute("ROLLBACK")
                            raise
                    raise HTTPException(
                        status_code=409,
                        detail=f"merge blocked - plan stays in review: {exc}",
                    ) from exc
                try:
                    from ..code_graph_lifecycle import notify_task_merged
                    notify_task_merged(app, merged)
                except Exception:
                    logging.getLogger("proxima.graph").exception(
                        "Code graph post-merge hook failed (plan stays merged)"
                    )
                try:
                    repo_remote.push_after_merge(db(), merged)
                except Exception:
                    logging.getLogger("proxima.graph").exception(
                        "push after merge failed unexpectedly (plan stays merged)"
                    )
        conn = db()
        notify_sessions: set[int] = set()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if approval_intent is not None:
                    if not master_decisions.intent_is_live_generation(
                        conn,
                        job_id=job_id,
                        generation=int(approval_intent["generation"]),
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "final_approval_generation_mismatch",
                                "message": (
                                    "Final approval generation is no longer live; "
                                    "retry approve if the plan is still in review"
                                ),
                            },
                        )
                else:
                    pending_decision = master_decisions.pending_decision_for_job(
                        conn, job_id
                    )
                    if pending_decision:
                        raise HTTPException(
                            status_code=409,
                            detail=master_decisions.pending_decision_conflict(
                                int(pending_decision["id"])
                            ),
                        )
                approved = state.guarded_transition(
                    conn,
                    "jobs",
                    job_id,
                    "done",
                    ("review",),
                    set_extra=(
                        "finished_at=CURRENT_TIMESTAMP, "
                        "updated_at=CURRENT_TIMESTAMP"
                    ),
                )
                if not approved:
                    if approval_intent is not None:
                        master_decisions.release_final_approval_intent(
                            conn,
                            job_id=job_id,
                            generation=int(approval_intent["generation"]),
                            error="graph job left review before finalize",
                        )
                        conn.execute("COMMIT")
                    else:
                        conn.execute("ROLLBACK")
                    raise HTTPException(
                        status_code=409,
                        detail="graph job changed concurrently",
                    )
                try:
                    artifact_registry.approve_records_for_job(conn, job_id)
                except Exception:
                    logging.getLogger("proxima.graph").exception(
                        "registry approve sync failed (non-fatal)"
                    )
                task_event = append_task_update(
                    conn,
                    job_id=job_id,
                    mutation="review_approved",
                )
                notify_sessions.add(task_event["session_id"])
                if approval_intent is not None:
                    if not master_decisions.finalize_final_approval_intent(
                        conn,
                        job_id=job_id,
                        generation=int(approval_intent["generation"]),
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "final_approval_generation_mismatch",
                                "message": (
                                    "Final approval generation changed during "
                                    "finalize"
                                ),
                            },
                        )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        for session_id in notify_sessions:
            app.state.hub.notify(session_id)
        _process_task_projection(task_event)
        app.state.task_delegation.prerequisite_changed(
            job_id, connection=conn
        )
        return graph_job_payload(graph_job_or_404(job_id, user))

"""Workflow / job / schedule routes for the Proxima API.

Extracted from main.py via the register() pattern: handler bodies move VERBATIM;
register() rebinds the shared create_app closures from `deps` as locals. These
three domains share helpers (_workflow_or_404, _member_project_id) so they live
together. No behavior change.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import Depends, HTTPException

from ..auth import iso_now
from .. import workflows as wf
from ..schemas import (
    JobApproveRequest, JobCreateRequest, ScheduleCreateRequest,
    ScheduleUpdateRequest, WorkflowCreateRequest, WorkflowUpdateRequest,
)


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    profile_for_user = deps["profile_for_user"]
    session_payload = deps["session_payload"]
    _can_access = deps["_can_access"]
    _member_project_id = deps["_member_project_id"]

    def _workflow_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["steps"] = json.loads(d.get("steps") or "[]")
        d["inputs"] = json.loads(d.get("inputs") or "[]")
        return d

    def _workflow_or_404(workflow_id: int, user: dict[str, Any]) -> sqlite3.Row:
        row = db().execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if not row or not _can_access(row["created_by"], row["project_id"], user):
            raise HTTPException(status_code=404, detail="workflow not found")
        return row

    @app.post("/api/workflows")
    def create_workflow(payload: WorkflowCreateRequest, user: dict[str, Any] = Depends(current_user)):
        steps = wf.normalize_steps(payload.steps)
        project_id = _member_project_id(payload.project_id, payload.project_slug, user)
        cur = db().execute(
            "INSERT INTO workflows(project_id, name, description, category, steps, inputs, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, payload.name, payload.description, payload.category, json.dumps(steps), json.dumps(payload.inputs or []), user["id"]),
        )
        return _workflow_payload(_workflow_or_404(int(cur.lastrowid), user))

    @app.get("/api/workflows")
    def list_workflows(project_id: int | None = None, project_slug: str | None = None, user: dict[str, Any] = Depends(current_user)):
        # Single-user: scope to rows the owner created or that belong to a project they own.
        scope = "(created_by = ? OR project_id IN (SELECT id FROM projects WHERE owner_user_id = ?))"
        params: list[Any] = [user["id"], user["id"]]
        extra = ""
        project_filter_id = _member_project_id(project_id, project_slug, user) if (project_id is not None or project_slug) else None
        if project_filter_id is not None:
            extra = " AND project_id = ?"
            params.append(project_filter_id)
        rows = db().execute(
            f"SELECT * FROM workflows WHERE status != 'archived' AND {scope}{extra} ORDER BY updated_at DESC, id DESC",
            tuple(params),
        ).fetchall()
        return [_workflow_payload(r) for r in rows]

    @app.get("/api/workflows/{workflow_id}")
    def get_workflow(workflow_id: int, user: dict[str, Any] = Depends(current_user)):
        return _workflow_payload(_workflow_or_404(workflow_id, user))

    @app.patch("/api/workflows/{workflow_id}")
    def update_workflow(workflow_id: int, payload: WorkflowUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        _workflow_or_404(workflow_id, user)
        fields: list[str] = []
        vals: list[Any] = []
        for col in ("name", "description", "category", "status"):
            v = getattr(payload, col)
            if v is not None:
                fields.append(f"{col} = ?")
                vals.append(v)
        if payload.steps is not None:
            fields.append("steps = ?")
            vals.append(json.dumps(wf.normalize_steps(payload.steps)))
        if payload.inputs is not None:
            fields.append("inputs = ?")
            vals.append(json.dumps(payload.inputs))
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            db().execute(f"UPDATE workflows SET {', '.join(fields)} WHERE id = ?", (*vals, workflow_id))
        return _workflow_payload(_workflow_or_404(workflow_id, user))

    @app.delete("/api/workflows/{workflow_id}")
    def delete_workflow(workflow_id: int, user: dict[str, Any] = Depends(current_user)):
        _workflow_or_404(workflow_id, user)
        # Permanent delete. FKs cascade: schedules are removed (ON DELETE CASCADE) and
        # past jobs keep their frozen step snapshot (jobs.workflow_id -> NULL).
        db().execute("DELETE FROM schedules WHERE workflow_id = ?", (workflow_id,))
        db().execute("DELETE FROM sessions WHERE workflow_id = ?", (workflow_id,))
        db().execute("UPDATE jobs SET workflow_id = NULL WHERE workflow_id = ?", (workflow_id,))
        db().execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        return {"ok": True, "id": workflow_id}

    @app.post("/api/workflows/{workflow_id}/iterate")
    def iterate_workflow(workflow_id: int, user: dict[str, Any] = Depends(current_user)):
        """Get-or-create the workflow's iterate/test chat — a sandbox session linked to
        the workflow where you dry-test + refine the recipe, then 'Save to workflow'."""
        wfrow = _workflow_or_404(workflow_id, user)
        existing = db().execute(
            "SELECT s.*, p.slug AS project_slug, p.name AS project_name, pr.slug AS profile_slug, pr.name AS profile_name "
            "FROM sessions s LEFT JOIN projects p ON p.id=s.project_id LEFT JOIN profiles pr ON pr.id=s.profile_id "
            "WHERE s.workflow_id = ? AND s.owner_user_id = ? ORDER BY s.id DESC LIMIT 1",
            (workflow_id, user["id"]),
        ).fetchone()
        if existing:
            return session_payload(dict(existing))
        profile = profile_for_user(None, user)
        project_id = wfrow["project_id"]
        cur = db().execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility, workflow_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"⚙ {wfrow['name']}", project_id, user["id"], profile["id"], profile["runner_id"], "project" if project_id else "private", workflow_id),
        )
        row = db().execute(
            "SELECT s.*, p.slug AS project_slug, p.name AS project_name, pr.slug AS profile_slug, pr.name AS profile_name "
            "FROM sessions s LEFT JOIN projects p ON p.id=s.project_id LEFT JOIN profiles pr ON pr.id=s.profile_id WHERE s.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return session_payload(dict(row))

    # --- Jobs (executions: a workflow run, or an ad-hoc 1-step task) ---

    def _job_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["input"] = json.loads(d["input"]) if d.get("input") else None
        d["steps_state"] = json.loads(d.get("steps_state") or "[]")
        if d.get("project_id") and not d.get("project_slug"):
            pr = db().execute("SELECT slug FROM projects WHERE id = ?", (d["project_id"],)).fetchone()
            d["project_slug"] = pr["slug"] if pr else None
        return d

    def _job_or_404(job_id: int, user: dict[str, Any]) -> sqlite3.Row:
        row = db().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row and not _can_access(row["created_by"], row["project_id"], user):
            row = None
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        return row

    def _rollback(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    @app.post("/api/jobs")
    def create_job(payload: JobCreateRequest, user: dict[str, Any] = Depends(current_user)):
        profile = profile_for_user(None, user)
        req_project_id = _member_project_id(payload.project_id, payload.project_slug, user)
        if payload.workflow_id:
            wfrow = _workflow_or_404(payload.workflow_id, user)
            steps = json.loads(wfrow["steps"] or "[]")
            title = payload.title or wfrow["name"]
            project_id = req_project_id if req_project_id is not None else wfrow["project_id"]
        else:
            brief = (payload.input or {}).get("brief") or "Task"
            steps = wf.normalize_steps([{"name": "Task", "instruction": brief}])
            title = payload.title or brief[:80]
            project_id = req_project_id
        steps_state = [wf.step_state_from(s, payload.input or {}) for s in steps]
        visibility = "project" if project_id else "private"
        scur = db().execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility) VALUES (?, ?, ?, ?, ?, ?)",
            (title[:80], project_id, user["id"], profile["id"], profile["runner_id"], visibility),
        )
        session_id = int(scur.lastrowid)
        jcur = db().execute(
            "INSERT INTO jobs(project_id, workflow_id, session_id, title, input, steps_state, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, payload.workflow_id, session_id, title, json.dumps(payload.input or {}), json.dumps(steps_state), user["id"]),
        )
        job_id = int(jcur.lastrowid)
        db().execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
        return _job_payload(_job_or_404(job_id, user))

    @app.post("/api/jobs/{job_id}/start")
    def start_job(job_id: int, user: dict[str, Any] = Depends(current_user)):
        job = _job_or_404(job_id, user)
        steps = json.loads(job["steps_state"] or "[]")
        if not steps:
            raise HTTPException(status_code=400, detail="job has no steps")
        if not job["session_id"]:
            raise HTTPException(status_code=409, detail="job session missing")
        profile = profile_for_user(None, user)
        inputs = json.loads(job["input"]) if job["input"] else {}
        prompt = wf.build_step_prompt(steps[0], 0, len(steps), inputs)
        conn = db()
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Claim + enqueue atomically. Without this transaction, a failure
                # after the status update can leave the job stuck as running with no run.
                claimed = conn.execute(
                    "UPDATE jobs SET status='running', started_at=CURRENT_TIMESTAMP, current_step_idx=0, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='queued'",
                    (job_id,),
                )
                if claimed.rowcount == 0:
                    conn.execute("ROLLBACK")
                    return _job_payload(_job_or_404(job_id, user))  # already started; idempotent
                cur = conn.execute(
                    "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
                    "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (job["session_id"], job["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"]),
                )
                run_id = int(cur.lastrowid)
                steps[0]["status"] = "running"
                steps[0]["run_id"] = run_id
                steps[0]["started_at"] = iso_now()
                conn.execute(
                    "UPDATE jobs SET steps_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(steps), job_id),
                )
                conn.execute("COMMIT")
            except Exception:
                _rollback(conn)
                raise
        app.state.worker.add_event(run_id, job["session_id"], job["project_id"], "run.queued", {"runner": profile["runner_id"], "job": job_id})
        return _job_payload(_job_or_404(job_id, user))

    @app.get("/api/jobs")
    def list_jobs(
        status: str | None = None,
        workflow_id: int | None = None,
        project_id: int | None = None,
        project_slug: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
        user: dict[str, Any] = Depends(current_user),
    ):
        # Single-user: scope to the owner's own jobs + jobs in projects they own.
        where = ["(created_by = ? OR project_id IN (SELECT id FROM projects WHERE owner_user_id = ?))"]
        args: list[Any] = [user["id"], user["id"]]
        # Coexistence (ADR-0001): the linear Activity list only shows linear jobs.
        # Graph jobs (engine='graph') carry no steps_state and are served by the
        # graph engine's own surface — keep them out of the classic screen so it
        # can't choke on the node/edge model it doesn't understand.
        where.append("COALESCE(engine,'linear') = 'linear'")
        if not include_archived:
            where.append("archived_at IS NULL")
        if status:
            where.append("status = ?")
            args.append(status)
        if workflow_id is not None:
            where.append("workflow_id = ?")
            args.append(workflow_id)
        project_filter_id = _member_project_id(project_id, project_slug, user) if (project_id is not None or project_slug) else None
        if project_filter_id is not None:
            where.append("project_id = ?")
            args.append(project_filter_id)
        clause = " AND ".join(where)
        total = db().execute(f"SELECT COUNT(*) AS c FROM jobs WHERE {clause}", args).fetchone()["c"]
        rows = db().execute(
            f"SELECT * FROM jobs WHERE {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return {"items": [_job_payload(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int, user: dict[str, Any] = Depends(current_user)):
        return _job_payload(_job_or_404(job_id, user))

    @app.post("/api/jobs/{job_id}/approve")
    def approve_job(job_id: int, payload: JobApproveRequest | None = None, user: dict[str, Any] = Depends(current_user)):
        job = _job_or_404(job_id, user)
        if job["status"] != "review":
            return _job_payload(job)  # nothing to approve; idempotent
        if not job["session_id"]:
            raise HTTPException(status_code=409, detail="job session missing")
        steps = json.loads(job["steps_state"] or "[]")
        idx = int(job["current_step_idx"])
        # Optional "edit & continue": replace the reviewed step's output before resuming.
        if payload and payload.edited_output is not None and 0 <= idx < len(steps):
            steps[idx]["output_summary"] = payload.edited_output
        if idx + 1 < len(steps):
            nxt = idx + 1
            profile = profile_for_user(None, user)
            inputs = json.loads(job["input"]) if job["input"] else {}
            prompt = wf.build_step_prompt(steps[nxt], nxt, len(steps), inputs)
            conn = db()
            with app.state.db_lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Mid-workflow gate cleared. Claim review->running and enqueue the
                    # next run atomically so a failed insert cannot strand the job.
                    claimed = conn.execute(
                        "UPDATE jobs SET status='running', current_step_idx=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='review'",
                        (nxt, job_id),
                    )
                    if claimed.rowcount == 0:
                        conn.execute("ROLLBACK")
                        return _job_payload(_job_or_404(job_id, user))  # another approve already resumed it
                    cur = conn.execute(
                        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
                        "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                        (job["session_id"], job["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"]),
                    )
                    run_id = int(cur.lastrowid)
                    steps[nxt]["status"] = "running"
                    steps[nxt]["run_id"] = run_id
                    steps[nxt]["started_at"] = iso_now()
                    conn.execute("UPDATE jobs SET steps_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(steps), job_id))
                    conn.execute("COMMIT")
                except Exception:
                    _rollback(conn)
                    raise
            app.state.worker.add_event(run_id, job["session_id"], job["project_id"], "run.queued", {"runner": profile["runner_id"], "job": job_id})
        else:
            # Final review -> done (atomic claim).
            claimed = db().execute("UPDATE jobs SET status='done', finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='review'", (job_id,))
            if claimed.rowcount == 0:
                return _job_payload(_job_or_404(job_id, user))
            db().execute("UPDATE jobs SET steps_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(steps), job_id))
        return _job_payload(_job_or_404(job_id, user))

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int, user: dict[str, Any] = Depends(current_user)):
        job = _job_or_404(job_id, user)
        # Remove the run record + its thread (messages/runs/events cascade). Produced
        # artifacts (Design Studio designs, project files) are deliverables and are
        # deliberately left in place.
        if job["session_id"]:
            active = db().execute(
                "SELECT id, project_id FROM runs WHERE session_id = ? AND status IN ('queued','running')",
                (job["session_id"],),
            ).fetchall()
            db().execute(
                "UPDATE runs SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
                "WHERE session_id = ? AND status IN ('queued','running')",
                (job["session_id"],),
            )
            for r in active:
                app.state.worker.add_event(int(r["id"]), job["session_id"], r["project_id"], "run.cancelled", {})
                app.state.worker.cancel(int(r["id"]))
            db().execute("DELETE FROM sessions WHERE id = ?", (job["session_id"],))
        db().execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return {"ok": True, "id": job_id}

    # --- Schedules (recurring workflow triggers / cron) ---

    def _schedule_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["input"] = json.loads(d["input"]) if d.get("input") else None
        d["enabled"] = bool(d.get("enabled"))
        return d

    def _schedule_or_404(schedule_id: int, user: dict[str, Any]) -> sqlite3.Row:
        row = db().execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if row and not _can_access(row["created_by"], row["project_id"], user):
            row = None
        if not row:
            raise HTTPException(status_code=404, detail="schedule not found")
        return row

    @app.post("/api/schedules")
    def create_schedule(payload: ScheduleCreateRequest, user: dict[str, Any] = Depends(current_user)):
        if not wf.cron_valid(payload.cron):
            raise HTTPException(status_code=422, detail="invalid cron — need 5 valid fields (min hour dom mon dow)")
        wfrow = _workflow_or_404(payload.workflow_id, user)
        sched_project = _member_project_id(payload.project_id, None, user) if payload.project_id is not None else wfrow["project_id"]
        cur = db().execute(
            "INSERT INTO schedules(workflow_id, project_id, cron, input, overlap_policy, enabled, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload.workflow_id, sched_project, payload.cron, json.dumps(payload.input or {}), payload.overlap_policy, 1 if payload.enabled else 0, user["id"]),
        )
        return _schedule_payload(_schedule_or_404(int(cur.lastrowid), user))

    @app.get("/api/schedules")
    def list_schedules(workflow_id: int | None = None, user: dict[str, Any] = Depends(current_user)):
        scope = "(created_by = ? OR project_id IN (SELECT id FROM projects WHERE owner_user_id = ?))"
        params: list[Any] = [user["id"], user["id"]]
        if workflow_id is not None:
            scope += " AND workflow_id = ?"; params.append(workflow_id)
        rows = db().execute(f"SELECT * FROM schedules WHERE {scope} ORDER BY id DESC", tuple(params)).fetchall()
        return [_schedule_payload(r) for r in rows]

    @app.patch("/api/schedules/{schedule_id}")
    def update_schedule(schedule_id: int, payload: ScheduleUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        _schedule_or_404(schedule_id, user)
        fields: list[str] = []
        vals: list[Any] = []
        if payload.cron is not None:
            if not wf.cron_valid(payload.cron):
                raise HTTPException(status_code=422, detail="invalid cron — need 5 valid fields (min hour dom mon dow)")
            fields.append("cron = ?"); vals.append(payload.cron)
        if payload.overlap_policy is not None:
            fields.append("overlap_policy = ?"); vals.append(payload.overlap_policy)
        if payload.enabled is not None:
            fields.append("enabled = ?"); vals.append(1 if payload.enabled else 0)
        if payload.input is not None:
            fields.append("input = ?"); vals.append(json.dumps(payload.input))
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            db().execute(f"UPDATE schedules SET {', '.join(fields)} WHERE id = ?", (*vals, schedule_id))
        return _schedule_payload(_schedule_or_404(schedule_id, user))

    @app.delete("/api/schedules/{schedule_id}")
    def delete_schedule(schedule_id: int, user: dict[str, Any] = Depends(current_user)):
        _schedule_or_404(schedule_id, user)
        db().execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return {"ok": True, "id": schedule_id}

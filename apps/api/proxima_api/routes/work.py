"""Workflow / job / schedule routes for the Proxima API.

Extracted from main.py via the register() pattern: handler bodies move VERBATIM;
register() rebinds the shared create_app closures from `deps` as locals. These
three domains share helpers (_workflow_or_404, _member_project_id) so they live
together. No behavior change.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import Depends, HTTPException

from ..auth import iso_now
from .. import artifact_registry
from .. import features
from .. import scheduler
from .. import workflows as wf
from .. import worktrees
from ..schemas import (
    JobApproveRequest, JobCreateRequest, JobRejectRequest, JobRunLinkRequest,
    ScheduleCreateRequest, ScheduleUpdateRequest, WorkflowCreateRequest,
    WorkflowUpdateRequest,
)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


def _decode_json(value: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("expected valid JSON") from exc


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    profile_for_user = deps["profile_for_user"]
    session_payload = deps["session_payload"]
    _can_access = deps["_can_access"]
    _member_project_id = deps["_member_project_id"]

    def _workflow_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["steps"] = _decode_json(d.get("steps") or "[]")
        d["inputs"] = _decode_json(d.get("inputs") or "[]")
        return d

    def _workflow_or_404(workflow_id: int, user: dict[str, Any]) -> sqlite3.Row:
        """A LINEAR workflow, for the linear editor/iterate/job routes. Rejecting
        graph-backed rows here is what stops a graph template being edited or run as
        if it were an ordered recipe."""
        row = db().execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if (
            not row
            or row["graph"] is not None
            or not _can_access(row["created_by"], row["project_id"], user)
        ):
            raise HTTPException(status_code=404, detail="workflow not found")
        return row

    def _any_workflow_or_404(workflow_id: int, user: dict[str, Any]) -> sqlite3.Row:
        """Either engine — for operations that treat the workflows row as a whole
        (scheduling it, deleting it), where linear-vs-graph makes no difference."""
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
        return _workflow_payload(_workflow_or_404(_as_int(cur.lastrowid), user))

    @app.get("/api/workflows")
    def list_workflows(project_id: int | None = None, project_slug: str | None = None, user: dict[str, Any] = Depends(current_user)):
        # Single-user: scope to rows the owner created or that belong to a project they own.
        project_filter_id = _member_project_id(project_id, project_slug, user) if (project_id is not None or project_slug) else None
        if project_filter_id is None:
            rows = db().execute(
                "SELECT * FROM workflows WHERE graph IS NULL AND status != 'archived' "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"]),
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT * FROM workflows WHERE graph IS NULL AND status != 'archived' "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "AND project_id = ? ORDER BY updated_at DESC, id DESC",
                (user["id"], user["id"], project_filter_id),
            ).fetchall()
        return [_workflow_payload(r) for r in rows]

    @app.get("/api/workflows/{workflow_id}")
    def get_workflow(workflow_id: int, user: dict[str, Any] = Depends(current_user)):
        return _workflow_payload(_workflow_or_404(workflow_id, user))

    @app.patch("/api/workflows/{workflow_id}")
    def update_workflow(workflow_id: int, payload: WorkflowUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        row = _any_workflow_or_404(workflow_id, user)
        if row["graph"] is not None:
            # Lifecycle only for graph templates: pause (draft) ⇄ resume (active) ⇄
            # archive. The scheduler fires none but 'active', so pausing a template is
            # how its schedules stop while it is being revised.
            if any(value is not None for value in (
                payload.name, payload.description, payload.category, payload.steps, payload.inputs
            )):
                raise HTTPException(status_code=422, detail="graph templates are authored on the canvas; only status can be changed here")
            if payload.status is not None:
                db().execute(
                    "UPDATE workflows SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (payload.status, workflow_id),
                )
            return _workflow_payload(_any_workflow_or_404(workflow_id, user))
        steps = json.dumps(wf.normalize_steps(payload.steps)) if payload.steps is not None else None
        inputs = json.dumps(payload.inputs) if payload.inputs is not None else None
        if any(value is not None for value in (
            payload.name, payload.description, payload.category, payload.status, steps, inputs
        )):
            db().execute(
                "UPDATE workflows SET name=COALESCE(?,name), "
                "description=COALESCE(?,description), category=COALESCE(?,category), "
                "status=COALESCE(?,status), steps=COALESCE(?,steps), "
                "inputs=COALESCE(?,inputs), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    payload.name, payload.description, payload.category, payload.status,
                    steps, inputs, workflow_id,
                ),
            )
        return _workflow_payload(_workflow_or_404(workflow_id, user))

    @app.delete("/api/workflows/{workflow_id}")
    def delete_workflow(workflow_id: int, user: dict[str, Any] = Depends(current_user)):
        # Either engine: deleting a graph template is the same row-level operation as
        # deleting a linear recipe — schedules go with it, past jobs keep their frozen
        # snapshot.
        _any_workflow_or_404(workflow_id, user)
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
        d["input"] = _decode_json(d["input"]) if d.get("input") else None
        d["steps_state"] = _decode_json(d.get("steps_state") or "[]")
        if d.get("project_id") and not d.get("project_slug"):
            pr = db().execute("SELECT slug FROM projects WHERE id = ?", (d["project_id"],)).fetchone()
            d["project_slug"] = pr["slug"] if pr else None
        # Repo jobs (slice 2): surface the worktree lifecycle for the review UI.
        # Only when a row exists - flag-off installs have none, so their job
        # payloads are unchanged.
        wt = worktrees.job_worktree_row(db(), d["id"])
        if wt:
            d["worktree"] = worktrees.worktree_payload(wt)
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
        except sqlite3.OperationalError as _exc:
            pass

    @app.post("/api/jobs")
    def create_job(payload: JobCreateRequest, user: dict[str, Any] = Depends(current_user)):
        profile = profile_for_user(payload.profile_id, user)
        req_project_id = _member_project_id(payload.project_id, payload.project_slug, user)
        if payload.workflow_id:
            wfrow = _workflow_or_404(payload.workflow_id, user)
            steps = _decode_json(wfrow["steps"] or "[]")
            title = payload.title or wfrow["name"]
            project_id = req_project_id if req_project_id is not None else wfrow["project_id"]
        else:
            brief = (payload.input or {}).get("brief") or "Task"
            steps = wf.normalize_steps([{"name": "Task", "instruction": brief}])
            title = payload.title or brief[:80]
            project_id = req_project_id
        steps_state = [wf.step_state_from(s, payload.input or {}) for s in steps]
        # Job -> target binding (T1, slice 2): the target must be one of THIS
        # project's live areas, pinned before the job runs. Code area = repo
        # job; ops area (or None) = today's behavior.
        target_area_id = None
        if payload.target_area_id is not None:
            area = db().execute(
                "SELECT * FROM project_areas WHERE id = ?", (payload.target_area_id,)
            ).fetchone()
            if not area or area["source"] == "excluded" or project_id is None or area["project_id"] != project_id:
                raise HTTPException(status_code=422, detail="target area not found in this job's project")
            target_area_id = _as_int(area["id"])
        visibility = "project" if project_id else "private"
        scur = db().execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility) VALUES (?, ?, ?, ?, ?, ?)",
            (title[:80], project_id, user["id"], profile["id"], profile["runner_id"], visibility),
        )
        session_id = _as_int(scur.lastrowid)
        jcur = db().execute(
            "INSERT INTO jobs(project_id, workflow_id, session_id, title, input, steps_state, target_area_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, payload.workflow_id, session_id, title, json.dumps(payload.input or {}), json.dumps(steps_state), target_area_id, user["id"]),
        )
        job_id = _as_int(jcur.lastrowid)
        db().execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
        return _job_payload(_job_or_404(job_id, user))

    @app.post("/api/jobs/{job_id}/start")
    def start_job(job_id: int, user: dict[str, Any] = Depends(current_user)):
        job = _job_or_404(job_id, user)
        steps = _decode_json(job["steps_state"] or "[]")
        if not steps:
            raise HTTPException(status_code=400, detail="job has no steps")
        if not job["session_id"]:
            raise HTTPException(status_code=409, detail="job session missing")
        session = db().execute("SELECT profile_id FROM sessions WHERE id = ?", (job["session_id"],)).fetchone()
        profile = profile_for_user(session["profile_id"] if session else None, user)
        inputs = _decode_json(job["input"]) if job["input"] else {}
        prompt = wf.build_step_prompt(steps[0], 0, len(steps), inputs)
        # Repo job (slice 2, flag-gated): cut the isolated worktree BEFORE the
        # job claims running, so a refused cut (dirty repo, detached HEAD, no
        # commits) surfaces loudly and leaves the job queued for a clean retry.
        if features.enabled(app.state.config, features.REPO_WORKTREES):
            try:
                worktrees.ensure_job_worktree(db(), app.state.config, job)
            except worktrees.WorktreeError as exc:
                raise HTTPException(status_code=409, detail=f"cannot start repo job: {exc}") from exc
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
                run_id = _as_int(cur.lastrowid)
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

    @app.post("/api/jobs/{job_id}/link-run")
    def link_job_run(job_id: int, payload: JobRunLinkRequest, user: dict[str, Any] = Depends(current_user)):
        """Attach a project-scoped media run to a queued ad-hoc task.

        Chat and Ops share the proven /image and /design execution path, while the
        job remains the durable lifecycle owner (running -> review -> done).
        """
        job = _job_or_404(job_id, user)
        if job["workflow_id"] is not None or not job["project_id"] or not job["session_id"]:
            raise HTTPException(status_code=409, detail="only project-scoped ad-hoc tasks can link media runs")
        run = db().execute(
            "SELECT * FROM runs WHERE id=? AND session_id=? AND project_id=? AND user_id=?",
            (payload.run_id, job["session_id"], job["project_id"], user["id"]),
        ).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="task media run not found")
        if run["kind"] not in {"media_image", "media_image-studio"}:
            raise HTTPException(status_code=422, detail="run is not an image or design task")
        steps = _decode_json(job["steps_state"] or "[]")
        if len(steps) != 1:
            raise HTTPException(status_code=409, detail="media task must have exactly one step")
        linked_id = steps[0].get("run_id")
        if linked_id is not None and _as_int(linked_id) != payload.run_id:
            raise HTTPException(status_code=409, detail="task already has a different run")
        if job["status"] == "queued":
            steps[0]["status"] = "running"
            steps[0]["run_id"] = payload.run_id
            steps[0]["started_at"] = run["started_at"] or iso_now()
            with app.state.db_lock:
                claimed = db().execute(
                    "UPDATE jobs SET status='running', started_at=CURRENT_TIMESTAMP, current_step_idx=0, steps_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                    (json.dumps(steps), job_id),
                )
            if claimed.rowcount == 0:
                job = _job_or_404(job_id, user)
        elif job["status"] not in {"running", "review", "done"}:
            raise HTTPException(status_code=409, detail=f"task cannot link a run while {job['status']}")
        # Re-read after linking: a fast provider may have completed between the
        # initial authorization lookup and the queued -> running claim.
        run = db().execute("SELECT * FROM runs WHERE id=?", (payload.run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="task media run disappeared")
        run_dict = dict(run)
        if run["status"] in {"completed", "failed", "cancelled"}:
            message = db().execute(
                "SELECT content FROM messages WHERE run_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                (payload.run_id,),
            ).fetchone()
            answer = message["content"] if message else "Agent produced no output"
            if run["status"] != "completed":
                answer = f"BLOCKED: {answer}"
            app.state.worker._advance_job(run_dict, answer)
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
        # Coexistence (ADR-0001): classic Activity is linear-only. Nullable
        # parameters keep this query static while retaining optional filters.
        project_filter_id = _member_project_id(project_id, project_slug, user) if (project_id is not None or project_slug) else None
        safe_limit = max(0, min(_as_int(limit), 200))
        safe_offset = max(0, _as_int(offset))
        status_filter = status or None
        filters = (
            user["id"], user["id"], 1 if include_archived else 0,
            status_filter, status_filter, workflow_id, workflow_id,
            project_filter_id, project_filter_id,
        )
        total = db().execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE "
            "(created_by = ? OR project_id IN "
            "(SELECT id FROM projects WHERE owner_user_id = ?)) "
            "AND COALESCE(engine,'linear') = 'linear' "
            "AND (? = 1 OR archived_at IS NULL) "
            "AND (? IS NULL OR status = ?) "
            "AND (? IS NULL OR workflow_id = ?) "
            "AND (? IS NULL OR project_id = ?)",
            filters,
        ).fetchone()["c"]
        rows = db().execute(
            "SELECT * FROM jobs WHERE "
            "(created_by = ? OR project_id IN "
            "(SELECT id FROM projects WHERE owner_user_id = ?)) "
            "AND COALESCE(engine,'linear') = 'linear' "
            "AND (? = 1 OR archived_at IS NULL) "
            "AND (? IS NULL OR status = ?) "
            "AND (? IS NULL OR workflow_id = ?) "
            "AND (? IS NULL OR project_id = ?) "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (*filters, safe_limit, safe_offset),
        ).fetchall()
        return {"items": [_job_payload(r) for r in rows], "total": _as_int(total), "limit": safe_limit, "offset": safe_offset}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int, user: dict[str, Any] = Depends(current_user)):
        """One job with its execution state (plus worktree state for repo jobs)."""
        return _job_payload(_job_or_404(job_id, user))

    @app.get("/api/jobs/{job_id}/diff")
    def get_job_diff(job_id: int, user: dict[str, Any] = Depends(current_user)):
        """The repo job's before/after change (worktree branch vs its base):
        per-file status + unified patch, the shape the slice-4 review UI
        renders. After a merge, the same change read off the base branch."""
        features.require(app.state.config, features.REPO_WORKTREES)
        _job_or_404(job_id, user)
        wt = worktrees.job_worktree_row(db(), job_id)
        if not wt or wt["status"] == "discarded":
            raise HTTPException(status_code=409, detail="job has no worktree - not a repo job, or it has not started yet")
        try:
            diff = worktrees.job_diff(wt)
        except worktrees.WorktreeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "job_id": job_id,
            "branch": wt["branch"],
            "base_branch": wt["base_branch"],
            "worktree_status": wt["status"],
            **diff,
        }

    @app.post("/api/jobs/{job_id}/approve")
    def approve_job(job_id: int, payload: JobApproveRequest | None = None, user: dict[str, Any] = Depends(current_user)):
        job = _job_or_404(job_id, user)
        if job["status"] != "review":
            return _job_payload(job)  # nothing to approve; idempotent
        if not job["session_id"]:
            raise HTTPException(status_code=409, detail="job session missing")
        steps = _decode_json(job["steps_state"] or "[]")
        idx = _as_int(job["current_step_idx"])
        # Optional "edit & continue": replace the reviewed step's output before resuming.
        if payload and payload.edited_output is not None and 0 <= idx < len(steps):
            steps[idx]["output_summary"] = payload.edited_output
        if idx + 1 < len(steps):
            nxt = idx + 1
            profile = profile_for_user(None, user)
            inputs = _decode_json(job["input"]) if job["input"] else {}
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
                    run_id = _as_int(cur.lastrowid)
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
            # Repo job (slice 2, flag-gated): the final approve is the merge
            # point (T1 local-first) - land the job branch on its base branch
            # before the job closes. Refusals and conflicts surface as 409 and
            # PARK the job in review (worktree kept for resolution); approve
            # again after resolving to retry. Never forced, never silent.
            if features.enabled(app.state.config, features.REPO_WORKTREES):
                wt = worktrees.job_worktree_row(db(), job_id)
                if wt and wt["status"] in ("active", "conflict", "merging"):
                    try:
                        worktrees.merge_job_worktree(db(), job, wt)
                    except worktrees.WorktreeError as exc:
                        raise HTTPException(status_code=409, detail=f"merge blocked - job stays in review: {exc}") from exc
            # Final review -> done (atomic claim).
            claimed = db().execute("UPDATE jobs SET status='done', finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='review'", (job_id,))
            if claimed.rowcount == 0:
                return _job_payload(_job_or_404(job_id, user))
            db().execute("UPDATE jobs SET steps_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(steps), job_id))
            # One status, two doors (T4): approving the job auto-approves the
            # deliverable records it produced. Best-effort - the verdict stands
            # even if the registry write fails.
            try:
                artifact_registry.approve_records_for_job(db(), job_id)
            except Exception:
                logging.getLogger("proxima.work").exception("registry approve sync failed (non-fatal)")
        return _job_payload(_job_or_404(job_id, user))

    @app.post("/api/jobs/{job_id}/reject")
    def reject_job(job_id: int, payload: JobRejectRequest, user: dict[str, Any] = Depends(current_user)):
        """The review surface's other door (slice 4, T1): rejecting a job at
        review marks it failed with the owner's one-line why, and for a repo
        job tears down its isolated worktree WITHOUT merging - the primary
        tree never sees the discarded change. Either engine: the row-level
        verdict is the same for a classic task and a graph plan."""
        job = _job_or_404(job_id, user)
        if job["status"] != "review":
            raise HTTPException(status_code=409, detail="only a job waiting for review can be rejected")
        # Claim first (this is the review verdict mutex - a concurrent approve
        # and reject cannot both win), tear down after.
        claimed = db().execute(
            "UPDATE jobs SET status='failed', rejected_reason=?, finished_at=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='review'",
            (payload.reason, job_id),
        )
        if claimed.rowcount == 0:
            raise HTTPException(status_code=409, detail="job is no longer waiting for review")
        # Flag-independent, like delete: a flag flip must not orphan a
        # worktree. Cleanup trouble is logged, not raised - the verdict stands
        # either way and discard_job_worktree is idempotent on retry.
        try:
            worktrees.discard_job_worktree(db(), job_id)
        except worktrees.WorktreeError:
            logging.getLogger("proxima.worktrees").exception(
                "job %s worktree cleanup failed (job rejected anyway)", job_id
            )
        return _job_payload(_job_or_404(job_id, user))

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int, user: dict[str, Any] = Depends(current_user)):
        job = _job_or_404(job_id, user)
        # Remove the run record + its threads (messages/runs/events cascade). Produced
        # artifacts (Design Studio designs, project files) are deliverables and are
        # deliberately left in place. "Threads" is plural for a graph job: every node
        # runs in its own session tied to the job by sessions.job_id, and that FK is
        # ON DELETE SET NULL — without sweeping them here they would linger as orphans.
        session_rows = db().execute(
            "SELECT id FROM sessions WHERE job_id = ? OR id = ?",
            (job_id, job["session_id"] or 0),
        ).fetchall()
        session_ids = [_as_int(r["id"]) for r in session_rows]
        for session_id in session_ids:
            active = db().execute(
                "SELECT id, project_id FROM runs WHERE session_id = ? AND status IN ('queued','running')",
                (session_id,),
            ).fetchall()
            db().execute(
                "UPDATE runs SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
                "WHERE session_id = ? AND status IN ('queued','running')",
                (session_id,),
            )
            for r in active:
                app.state.worker.add_event(_as_int(r["id"]), session_id, r["project_id"], "run.cancelled", {})
                app.state.worker.cancel(_as_int(r["id"]))
            db().execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        # Repo job worktree (slice 2): tear down the isolated worktree + branch
        # (never the primary tree). Deliberately flag-independent so a flag
        # flip can't orphan a worktree; a job without one is a no-op. Cleanup
        # trouble is logged, not raised - it must not block the delete.
        try:
            worktrees.discard_job_worktree(db(), job_id)
        except worktrees.WorktreeError:
            logging.getLogger("proxima.worktrees").exception("job %s worktree cleanup failed (job deleted anyway)", job_id)
        db().execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return {"ok": True, "id": job_id}

    # --- Schedules (recurring workflow triggers / cron) ---

    def _schedule_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["input"] = _decode_json(d["input"]) if d.get("input") else None
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
        wfrow = _any_workflow_or_404(payload.workflow_id, user)
        sched_project = _member_project_id(payload.project_id, None, user) if payload.project_id is not None else wfrow["project_id"]
        cur = db().execute(
            "INSERT INTO schedules(workflow_id, project_id, cron, input, overlap_policy, enabled, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload.workflow_id, sched_project, payload.cron, json.dumps(payload.input or {}), payload.overlap_policy, 1 if payload.enabled else 0, user["id"]),
        )
        return _schedule_payload(_schedule_or_404(_as_int(cur.lastrowid), user))

    @app.get("/api/schedules")
    def list_schedules(workflow_id: int | None = None, user: dict[str, Any] = Depends(current_user)):
        if workflow_id is None:
            rows = db().execute(
                "SELECT * FROM schedules WHERE (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) ORDER BY id DESC",
                (user["id"], user["id"]),
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT * FROM schedules WHERE (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) "
                "AND workflow_id = ? ORDER BY id DESC",
                (user["id"], user["id"], workflow_id),
            ).fetchall()
        return [_schedule_payload(r) for r in rows]

    @app.patch("/api/schedules/{schedule_id}")
    def update_schedule(schedule_id: int, payload: ScheduleUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        _schedule_or_404(schedule_id, user)
        if payload.cron is not None and not wf.cron_valid(payload.cron):
            raise HTTPException(status_code=422, detail="invalid cron — need 5 valid fields (min hour dom mon dow)")
        enabled = (1 if payload.enabled else 0) if payload.enabled is not None else None
        schedule_input = json.dumps(payload.input) if payload.input is not None else None
        if any(value is not None for value in (
            payload.cron, payload.overlap_policy, enabled, schedule_input
        )):
            db().execute(
                "UPDATE schedules SET cron=COALESCE(?,cron), "
                "overlap_policy=COALESCE(?,overlap_policy), enabled=COALESCE(?,enabled), "
                "input=COALESCE(?,input), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (payload.cron, payload.overlap_policy, enabled, schedule_input, schedule_id),
            )
        return _schedule_payload(_schedule_or_404(schedule_id, user))

    @app.post("/api/schedules/{schedule_id}/run")
    def run_schedule(schedule_id: int, user: dict[str, Any] = Depends(current_user)):
        """Fire a schedule now, without waiting for its cron.

        Goes through the scheduler's own spawn, so what runs is what the cron would
        have run — same workflow, project, profile and stored input. A disabled
        schedule still runs: 'enabled' governs the tick, and the whole point here is
        to try a schedule before trusting it to fire on its own.
        """
        row = _schedule_or_404(schedule_id, user)
        sched = dict(row)
        # Honour the stored overlap policy, but say so. Silently doing nothing is the
        # one thing a "run now" button must never do.
        if sched["overlap_policy"] == "skip" and scheduler.schedule_has_active_job(app, schedule_id):
            raise HTTPException(
                status_code=409,
                detail="this schedule already has a run in flight and its overlap policy is 'skip' — wait for it to finish, or set overlap to 'allow'",
            )
        job_id = scheduler.run_schedule_now(app, sched)
        if job_id is None:
            raise HTTPException(
                status_code=409,
                detail="schedule could not run — its workflow is not active, has no steps, or has no agent profile",
            )
        return _job_payload(_job_or_404(job_id, user))

    @app.delete("/api/schedules/{schedule_id}")
    def delete_schedule(schedule_id: int, user: dict[str, Any] = Depends(current_user)):
        _schedule_or_404(schedule_id, user)
        db().execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return {"ok": True, "id": schedule_id}

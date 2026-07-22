"""Scheduler + job-archival helpers for the Proxima API.

Cron tick, scheduled-job spawn, and old-job archival. All reach shared state via
the passed `app`/`conn`, not via create_app closures.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import FastAPI

from .auth import iso_now
from . import features, worktrees, workflows as wf
from .graph import normalize_graph


def archive_old_jobs(conn: sqlite3.Connection, days: int = 30) -> int:
    """Mark finished jobs older than `days` as archived so default views stay
    clean even with thousands of (incl. future cron-spawned) runs. Archived jobs
    remain queryable via the include_archived filter. Returns rows archived."""
    cur = conn.execute(
        "UPDATE jobs SET archived_at = CURRENT_TIMESTAMP "
        "WHERE archived_at IS NULL AND status IN ('done', 'cancelled', 'failed') "
        "AND created_at < datetime('now', ?)",
        (f"-{int(days)} days",),
    )
    return cur.rowcount


def _spawn_scheduled_job(app: FastAPI, sched: dict[str, Any], minute_key: str | None) -> int | None:
    """Create + start a job for a due schedule, using the workflow owner's default
    profile. Worker-side (worker_db). Returns the new job id, or None if skipped.

    `minute_key` is the scheduler's claim on the current minute. Pass None for a
    manual "run now": the job is spawned identically, but the schedule's
    last_run_minute is left alone so a manual run at 09:00 cannot swallow the real
    09:00 tick.
    """
    db = app.state.worker_db

    def _claim_minute() -> None:
        if minute_key is None:
            return
        db.execute(
            "UPDATE schedules SET last_run_minute = ?, last_tick_at = CURRENT_TIMESTAMP WHERE id = ?",
            (minute_key, sched["id"]),
        )

    with app.state.db_lock:
        wfrow = db.execute("SELECT * FROM workflows WHERE id = ?", (sched["workflow_id"],)).fetchone()
        uid = sched["created_by"] or (wfrow["created_by"] if wfrow else None)
        prof = db.execute(
            "SELECT * FROM profiles WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1", (uid,)
        ).fetchone() if uid else None
        if not wfrow or wfrow["status"] != "active" or not prof:
            _claim_minute()
            return None
        inp = json.loads(sched["input"]) if sched["input"] else {}

        # A graph template's `steps` is '[]', so the linear path below would build an
        # empty steps_state and give up — scheduling a graph used to do nothing at all,
        # silently. Spawn the engine the workflow actually declares.
        graph_job_id: int | None = None
        if wfrow['graph']:
            graph_job_id = _insert_scheduled_graph_job(app, sched, dict(wfrow), dict(prof), uid, inp)
            _claim_minute()
        else:
            steps_state = [wf.step_state_from(s, inp) for s in json.loads(wfrow['steps'] or '[]')]
            if not steps_state:
                _claim_minute()
                return None
            project_id = sched["project_id"] if sched["project_id"] is not None else wfrow["project_id"]
            title = wfrow["name"]
            scur = db.execute(
                "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility) VALUES (?, ?, ?, ?, ?, ?)",
                (title[:80], project_id, uid, prof["id"], prof["runner_id"], "project" if project_id else "private"),
            )
            session_id = int(scur.lastrowid)
            status = "queued"
            run_id = None
            if steps_state:
                prompt = wf.build_step_prompt(steps_state[0], 0, len(steps_state), inp)
                rcur = db.execute(
                    "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
                    "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (session_id, project_id, uid, prof["id"], prof["runner_id"], prompt, prof["default_model"], prof["hermes_home"]),
                )
                run_id = int(rcur.lastrowid)
                steps_state[0]["status"] = "running"
                steps_state[0]["run_id"] = run_id
                steps_state[0]["started_at"] = iso_now()
                status = "running"
            jcur = db.execute(
                "INSERT INTO jobs(project_id, workflow_id, session_id, title, status, current_step_idx, input, steps_state, schedule_id, created_by, started_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (project_id, wfrow["id"], session_id, title, status, json.dumps(inp), json.dumps(steps_state), sched["id"], uid),
            )
            job_id = int(jcur.lastrowid)
            db.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
            _claim_minute()

    if graph_job_id is not None:
        # Outside the lock: dispatch_ready takes it itself and opens its own transaction.
        # Skip dispatch when bind_graph_job_repo_worktree already failed the job
        # (still return the id so Run now / Tasks can surface the refusal).
        status = app.state.worker_db.execute(
            "SELECT status FROM jobs WHERE id = ?", (graph_job_id,)
        ).fetchone()
        if status and status["status"] == "running":
            app.state.worker.graph_executor.dispatch_ready(graph_job_id)
        return graph_job_id
    if wfrow["graph"]:
        return None          # a graph the executor refused to spawn; it logged why
    if run_id is not None:
        app.state.worker.add_event(run_id, session_id, project_id, "run.queued", {"runner": prof["runner_id"], "job": job_id, "scheduled": True, "manual": minute_key is None})
    return job_id


def _insert_scheduled_graph_job(
    app: FastAPI,
    sched: dict[str, Any],
    wfrow: dict[str, Any],
    prof: dict[str, Any],
    uid: Any,
    inp: dict[str, Any],
) -> int | None:
    """Insert a queued->running graph job for a due schedule. Caller holds the db lock
    and dispatches afterwards.

    A scheduled graph is the same job `POST /api/graph/jobs` + `/start` would create —
    same frozen snapshot, same node_states, same executor — so a cron run and a manual
    run cannot drift apart.
    """
    db = app.state.worker_db
    if not features.enabled(app.state.config, features.WORKFLOW_GRAPH):
        # The master switch is off, so the executor would never dispatch this job. Skip
        # rather than leave a 'running' job nothing will ever advance.
        logging.getLogger("proxima.scheduler").warning(
            "schedule %s targets a graph workflow while %s is off; skipped",
            sched["id"], features.WORKFLOW_GRAPH,
        )
        return None
    try:
        graph = normalize_graph(wfrow["graph"] or "")
    except Exception:
        logging.getLogger("proxima.scheduler").exception(
            "schedule %s targets an invalid graph workflow %s", sched["id"], wfrow["id"]
        )
        return None

    project_id = sched["project_id"] if sched["project_id"] is not None else wfrow["project_id"]
    title = wfrow["name"]
    scur = db.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, 'chat')",
        (title[:200], project_id, uid, prof["id"], prof["runner_id"], "project" if project_id else "private"),
    )
    session_id = int(scur.lastrowid)
    jcur = db.execute(
        "INSERT INTO jobs(project_id, workflow_id, session_id, title, status, input, steps_state, "
        "engine, graph, schedule_id, created_by, started_at) "
        "VALUES (?, ?, ?, ?, 'running', ?, '[]', 'graph', ?, ?, ?, CURRENT_TIMESTAMP)",
        (
            project_id, wfrow["id"], session_id, title,
            json.dumps(inp, ensure_ascii=False),
            json.dumps(graph, ensure_ascii=False),
            sched["id"], uid,
        ),
    )
    job_id = int(jcur.lastrowid)
    db.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
    for node in graph["nodes"]:
        db.execute(
            "INSERT INTO node_states(job_id, node_id, status, output_kind) VALUES (?, ?, 'pending', ?)",
            (job_id, node["id"], node["output_kind"]),
        )
    # Same repo isolation as POST /api/graph/jobs/{id}/start: pin target_area_id
    # and cut the worktree BEFORE dispatch, so a scheduled recipe never writes
    # into the live code area. A refused cut fails the job in place (visible in
    # Tasks) rather than leaving a running plan with no isolation.
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    try:
        worktrees.bind_graph_job_repo_worktree(db, app.state.config, job)
    except worktrees.WorktreeError as exc:
        logging.getLogger("proxima.scheduler").warning(
            "schedule %s graph job %s could not bind worktree: %s",
            sched["id"], job_id, exc,
        )
        db.execute(
            "UPDATE jobs SET status='failed', rejected_reason=?, finished_at=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (f"cannot start repo plan: {exc}", job_id),
        )
        return job_id
    return job_id


def schedule_has_active_job(app: FastAPI, schedule_id: int) -> bool:
    """True while this schedule still has a job the overlap policy would collide with."""
    with app.state.db_lock:
        return app.state.worker_db.execute(
            "SELECT 1 FROM jobs WHERE schedule_id = ? AND status IN ('queued','running','review') LIMIT 1",
            (schedule_id,),
        ).fetchone() is not None


def run_schedule_now(app: FastAPI, sched: dict[str, Any]) -> int | None:
    """Fire a schedule immediately through the exact path the tick uses, so a manual
    run proves the stored cron target — workflow, project, profile and input — rather
    than a lookalike. Does not claim the scheduler's minute; overlap is the caller's
    call so it can report a skip instead of silently doing nothing."""
    return _spawn_scheduled_job(app, sched, None)


def _scheduler_tick(app: FastAPI, now: datetime | None = None) -> list[int]:
    """One scheduler pass: spawn a job for every enabled schedule whose cron matches
    the current minute (once per minute, honoring overlap policy). Returns job ids."""
    now = now or datetime.now()
    minute_key = now.strftime("%Y-%m-%dT%H:%M")
    db = app.state.worker_db
    with app.state.db_lock:
        scheds = [dict(r) for r in db.execute("SELECT * FROM schedules WHERE enabled = 1").fetchall()]
    spawned: list[int] = []
    for s in scheds:
        if s["last_run_minute"] == minute_key or not wf.cron_matches(s["cron"], now):
            continue
        # Atomically claim this minute for this schedule BEFORE any work. A second
        # tick (or a second scheduler) that already claimed it gets rowcount 0 and
        # skips — no double-spawn. Replaces the old read-snapshot-then-set-later
        # window that was safe only under a single scheduler task.
        with app.state.db_lock:
            claimed = db.execute(
                "UPDATE schedules SET last_run_minute = ?, last_tick_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND IFNULL(last_run_minute, '') != ?",
                (minute_key, s["id"], minute_key),
            ).rowcount > 0
        if not claimed:
            continue
        if s["overlap_policy"] == "skip":
            active = db.execute(
                "SELECT 1 FROM jobs WHERE schedule_id = ? AND status IN ('queued','running','review') LIMIT 1", (s["id"],)
            ).fetchone()
            if active:
                continue  # minute already claimed above; just don't spawn
        try:
            jid = _spawn_scheduled_job(app, s, minute_key)
            if jid:
                spawned.append(jid)
        except Exception:
            logging.getLogger("proxima.scheduler").exception("scheduled job spawn failed")
    return spawned

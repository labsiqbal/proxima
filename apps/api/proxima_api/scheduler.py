"""Scheduler + job-archival helpers for the Proxima API.

Extracted verbatim from main.py (no behavior change): cron tick, scheduled-job
spawn, old-job archival, and Tailscale URL detection. All reach shared state via
the passed `app`/`conn`, not via create_app closures.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import datetime
from typing import Any

from fastapi import FastAPI

from .auth import iso_now
from . import workflows as wf


def tailscale_base_url() -> str | None:
    """Best-effort detection of this host's Tailscale MagicDNS HTTPS URL."""
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=4)
        if out.returncode != 0:
            return None
        name = (json.loads(out.stdout).get("Self") or {}).get("DNSName", "").rstrip(".")
        return f"https://{name}" if name else None
    except Exception:
        return None


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


def _spawn_scheduled_job(app: FastAPI, sched: dict[str, Any], minute_key: str) -> int | None:
    """Create + start a job for a due schedule, using the workflow owner's default
    profile. Worker-side (worker_db). Returns the new job id, or None if skipped."""
    db = app.state.worker_db
    with app.state.db_lock:
        wfrow = db.execute("SELECT * FROM workflows WHERE id = ?", (sched["workflow_id"],)).fetchone()
        uid = sched["created_by"] or (wfrow["created_by"] if wfrow else None)
        prof = db.execute(
            "SELECT * FROM profiles WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1", (uid,)
        ).fetchone() if uid else None
        if not wfrow or wfrow["status"] == "archived" or not prof:
            db.execute("UPDATE schedules SET last_run_minute = ?, last_tick_at = CURRENT_TIMESTAMP WHERE id = ?", (minute_key, sched["id"]))
            return None
        inp = json.loads(sched["input"]) if sched["input"] else {}
        steps_state = [wf.step_state_from(s, inp) for s in json.loads(wfrow["steps"] or "[]")]
        if not steps_state:
            db.execute("UPDATE schedules SET last_run_minute = ?, last_tick_at = CURRENT_TIMESTAMP WHERE id = ?", (minute_key, sched["id"]))
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
        db.execute("UPDATE schedules SET last_run_minute = ?, last_tick_at = CURRENT_TIMESTAMP WHERE id = ?", (minute_key, sched["id"]))
    if run_id is not None:
        app.state.worker.add_event(run_id, session_id, project_id, "run.queued", {"runner": prof["runner_id"], "job": job_id, "scheduled": True})
    return job_id


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
        if s["overlap_policy"] == "skip":
            active = db.execute(
                "SELECT 1 FROM jobs WHERE schedule_id = ? AND status IN ('queued','running','review') LIMIT 1", (s["id"],)
            ).fetchone()
            if active:
                with app.state.db_lock:
                    db.execute("UPDATE schedules SET last_run_minute = ?, last_tick_at = CURRENT_TIMESTAMP WHERE id = ?", (minute_key, s["id"]))
                continue
        try:
            jid = _spawn_scheduled_job(app, s, minute_key)
            if jid:
                spawned.append(jid)
        except Exception:
            logging.getLogger("proxima.scheduler").exception("scheduled job spawn failed")
    return spawned

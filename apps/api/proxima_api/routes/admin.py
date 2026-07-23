"""Admin routes for the Proxima API.

Single-user cockpit: user management, invite links, and roles are gone (one
owner, no in-app accounts). What remains is the audit/activity log.
"""
from __future__ import annotations

import subprocess
from typing import Any

from fastapi import Depends

from ..run_state import active_run_clause, stale_params
from ..settings import systemd_user_unit


def register(app, deps):
    db = deps["db"]
    admin_user = deps["admin_user"]

    @app.get("/api/audit")
    def list_audit(limit: int = 300, user: dict[str, Any] = Depends(admin_user)):
        rows = db().execute(
            "SELECT a.id, a.action, a.target_type, a.target_id, a.metadata, a.created_at, u.username AS actor "
            "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id ORDER BY a.id DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        return {"entries": [dict(r) for r in rows]}

    @app.get("/api/debug/logs")
    def debug_logs(limit: int = 240, user: dict[str, Any] = Depends(admin_user)):
        line_limit = max(20, min(int(limit or 240), 1000))
        log_text = ""
        log_error = ""
        log_hint = ""
        cfg = getattr(app.state, "config", {}) or {}
        unit = systemd_user_unit(cfg.get("service_name"))

        try:
            proc = subprocess.run(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    unit,
                    "-n",
                    str(line_limit),
                    "--no-pager",
                    "--output",
                    "short-iso",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            log_text = (proc.stdout or "")[-120_000:]
            log_error = (proc.stderr or "").strip()
            if proc.returncode != 0 and not log_error:
                log_error = f"journalctl exited with code {proc.returncode}"
        except Exception as exc:
            log_error = str(exc)

        stripped = log_text.strip()
        if not log_error and (not stripped or stripped == "-- No entries --"):
            log_hint = (
                f"No journal entries for user unit {unit}. "
                "If Proxima runs under a different systemd unit, set PROXIMA_SERVICE_NAME "
                "to that unit name (without .service) and restart."
            )

        runs = db().execute(
            "SELECT r.id, r.session_id, r.status, r.runner_id, r.kind, r.prompt, "
            "r.started_at, r.finished_at, r.heartbeat_at, r.created_at, "
            "s.title AS session_title, p.name AS profile_name "
            "FROM runs r "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "LEFT JOIN profiles p ON p.id = r.profile_id "
            "ORDER BY r.id DESC LIMIT 40"
        ).fetchall()
        stale_seconds = int(getattr(app.state, "config", {}).get("run_stale_seconds") or 60)
        fresh_clause = active_run_clause()
        fresh_clause_r = active_run_clause("r")
        fresh_clause_x = active_run_clause("x")
        fresh_params = stale_params(stale_seconds)
        active_rows = db().execute(
            f"SELECT DISTINCT session_id FROM runs WHERE {fresh_clause} ORDER BY session_id",
            fresh_params,
        ).fetchall()
        active_run_rows = db().execute(
            "SELECT r.id, r.session_id, r.status, r.runner_id, r.kind, r.prompt, "
            "r.started_at, r.finished_at, r.heartbeat_at, r.created_at, "
            "s.title AS session_title, p.name AS profile_name "
            "FROM runs r "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "LEFT JOIN profiles p ON p.id = r.profile_id "
            "WHERE "
            + fresh_clause_r +
            " ORDER BY r.id DESC LIMIT 20",
            fresh_params,
        ).fetchall()
        stale_rows = db().execute(
            "SELECT r.id, r.session_id, r.status, r.runner_id, r.kind, r.prompt, "
            "r.started_at, r.finished_at, r.heartbeat_at, r.created_at, "
            "s.title AS session_title, p.name AS profile_name "
            "FROM runs r "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "LEFT JOIN profiles p ON p.id = r.profile_id "
            "WHERE r.status IN ('queued', 'running') AND NOT ("
            + fresh_clause_r +
            ") AND NOT (r.status = 'queued' AND EXISTS ("
            "SELECT 1 FROM runs x WHERE x.session_id = r.session_id AND x.id != r.id AND "
            + fresh_clause_x +
            ")) ORDER BY r.id DESC LIMIT 20",
            (*fresh_params, *fresh_params),
        ).fetchall()
        orphaned_jobs = db().execute(
            "SELECT j.id, j.session_id, j.title, j.status, j.current_step_idx, j.workflow_id, j.schedule_id, "
            "j.created_at, j.updated_at, s.title AS session_title "
            "FROM jobs j "
            "LEFT JOIN sessions s ON s.id = j.session_id "
            "WHERE j.status = 'running' AND NOT EXISTS ("
            "SELECT 1 FROM runs r WHERE r.session_id = j.session_id AND r.status IN ('queued', 'running')"
            ") ORDER BY j.id DESC LIMIT 20"
        ).fetchall()

        return {
            "logs": log_text,
            "logError": log_error,
            "logHint": log_hint,
            "serviceUnit": unit,
            "runs": [dict(r) for r in runs],
            "rawActiveSessionIds": [r["session_id"] for r in active_rows],
            "activeRuns": [dict(r) for r in active_run_rows],
            "staleRuns": [dict(r) for r in stale_rows],
            "orphanedJobs": [dict(j) for j in orphaned_jobs],
        }

    @app.post("/api/debug/reap-orphaned-jobs")
    def reap_orphaned_jobs(user: dict[str, Any] = Depends(admin_user)):
        count = app.state.worker.reap_orphaned_jobs()
        return {"ok": True, "count": count}

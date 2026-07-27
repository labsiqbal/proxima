"""Budgeted unattended Master queue supervisor.

The supervisor only starts already-created Master jobs. It never performs stuck
recovery (satpam owns that) and never invokes destructive product administration.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import app_settings
from .master_runtime import MASTER_MAX_PARALLEL, master_capacity, start_master_job


class MasterSupervisor:
    def __init__(self, app: Any):
        self.app = app

    def _stop_for_budget(self, conn, origin_master_session_id: int, reason: str) -> None:
        app_settings.set_master_settings(conn, unattended=False)
        conn.execute(
            "INSERT OR IGNORE INTO attention_items(kind, title, target_json, inline_ok, status, source_key) "
            "VALUES ('master_budget', 'Master unattended work stopped', ?, 0, 'open', ?)",
            (
                json.dumps({"view": "master", "section": "budgets", "origin_master_session_id": origin_master_session_id, "reason": reason}),
                f"master-budget:{origin_master_session_id}:{reason}",
            ),
        )

    def tick(self) -> dict[str, Any]:
        conn = self.app.state.worker_db
        settings = app_settings.get_master_settings(conn)
        if not settings["unattended"]:
            return {"active": False, "started": []}
        session = conn.execute(
            "SELECT id, owner_user_id FROM sessions WHERE mode = 'master' ORDER BY id LIMIT 1"
        ).fetchone()
        if not session:
            app_settings.set_master_settings(conn, unattended=False)
            return {"active": False, "started": []}
        started_raw = app_settings.get_setting(conn, "master.budget.started_at") or ""
        try:
            started_at = datetime.fromisoformat(started_raw)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            started_at = datetime.now(timezone.utc)
            app_settings.set_setting(conn, "master.budget.started_at", started_at.isoformat())
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        try:
            turns_used = max(0, int(app_settings.get_setting(conn, "master.budget.turns_used", "0") or 0))
        except (TypeError, ValueError):
            turns_used = 0
        if elapsed >= settings["budget_wall_seconds"]:
            self._stop_for_budget(conn, session["id"], "wall-clock budget exhausted")
            return {"active": False, "stopped": "wall-clock budget exhausted", "started": []}
        if turns_used >= settings["budget_turns"]:
            self._stop_for_budget(conn, session["id"], "turn budget exhausted")
            return {"active": False, "stopped": "turn budget exhausted", "started": []}
        capacity = master_capacity(conn, session["id"])
        available = min(
            max(0, MASTER_MAX_PARALLEL - capacity["running"]),
            settings["budget_turns"] - turns_used,
        )
        if available <= 0:
            return {"active": True, "started": [], "capacity": capacity}
        rows = conn.execute(
            "SELECT id, created_by FROM jobs WHERE origin_master_session_id = ? AND status = 'queued' "
            "ORDER BY created_at, id LIMIT ?",
            (session["id"], available),
        ).fetchall()
        started: list[int] = []
        for row in rows:
            try:
                job = start_master_job(
                    conn, self.app, {"id": row["created_by"]}, row["id"]
                )
            except Exception as exc:
                conn.execute(
                    "INSERT OR IGNORE INTO attention_items(kind, title, target_json, inline_ok, status, source_key) "
                    "VALUES ('master_decision', 'Master could not start queued work', ?, 0, 'open', ?)",
                    (
                        json.dumps({"view": "master", "job_id": row["id"], "error": str(exc), "origin_master_session_id": session["id"]}),
                        f"master-start:{row['id']}",
                    ),
                )
                continue
            if job["status"] == "queued":
                # Dependency blockers are durable on the Task and are not start
                # failures. A prerequisite transition will retry this Task.
                continue
            started.append(row["id"])
            turns_used += 1
            app_settings.set_setting(conn, "master.budget.turns_used", str(turns_used))
        return {"active": True, "started": started, "capacity": master_capacity(conn, session["id"])}

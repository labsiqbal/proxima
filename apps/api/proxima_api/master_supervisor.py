"""Budgeted unattended Master queue supervisor.

The supervisor only starts already-created Master jobs. It never performs stuck
recovery (satpam owns that) and never invokes destructive product administration.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from . import app_settings
from .master_runtime import (
    master_active_slots,
    master_capacity,
    master_parallel_limit,
    start_master_job,
)


class MasterSupervisor:
    def __init__(self, app: Any):
        self.app = app
        self._tick_lock = threading.Lock()

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
        row = conn.execute(
            "SELECT id FROM attention_items WHERE source_key = ?",
            (f"master-budget:{origin_master_session_id}:{reason}",),
        ).fetchone()
        if row and self.app.state.master_projection is not None:
            self.app.state.master_projection.safe_project_attention(
                int(row["id"])
            )

    def tick(self) -> dict[str, Any]:
        if not self._tick_lock.acquire(blocking=False):
            return {"active": True, "started": [], "busy": True}
        try:
            return self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self) -> dict[str, Any]:
        conn = self.app.state.worker_db
        settings = app_settings.get_master_settings(conn)
        if not settings["unattended"]:
            return {"active": False, "started": []}
        session = conn.execute(
            "SELECT id, owner_user_id FROM sessions "
            "WHERE mode = 'master' ORDER BY id LIMIT 1"
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
        max_parallel = master_parallel_limit(self.app.state.config)
        capacity = master_capacity(
            conn, session["id"], max_parallel=max_parallel
        )
        available = min(
            max(0, max_parallel - master_active_slots(conn, session["id"])),
            settings["budget_turns"] - turns_used,
        )
        if available <= 0:
            return {"active": True, "started": [], "capacity": capacity}
        rows = conn.execute(
            "SELECT j.id, j.created_by FROM jobs j "
            "WHERE j.origin_master_session_id = ? "
            "AND j.created_by = ? AND j.status = 'queued' "
            "ORDER BY j.created_at, j.id",
            (session["id"], session["owner_user_id"]),
        ).fetchall()
        started: list[int] = []
        for row in rows:
            if (
                len(started) >= available
                or master_active_slots(conn, session["id"]) >= max_parallel
            ):
                break
            try:
                job = start_master_job(
                    conn,
                    self.app,
                    {"id": row["created_by"]},
                    row["id"],
                    supervisor_budget_turns=settings["budget_turns"],
                )
            except Exception as exc:
                if getattr(exc, "code", None) == "master_capacity_full":
                    break
                if getattr(exc, "code", None) == "master_budget_exhausted":
                    self._stop_for_budget(
                        conn,
                        session["id"],
                        "turn budget exhausted",
                    )
                    break
                conn.execute(
                    "INSERT OR IGNORE INTO attention_items(kind, title, target_json, inline_ok, status, source_key) "
                    "VALUES ('master_decision', 'Master could not start queued work', ?, 0, 'open', ?)",
                    (
                        json.dumps({"view": "master", "job_id": row["id"], "error": str(exc), "origin_master_session_id": session["id"]}),
                        f"master-start:{row['id']}",
                    ),
                )
                attention = conn.execute(
                    "SELECT id FROM attention_items WHERE source_key = ?",
                    (f"master-start:{row['id']}",),
                ).fetchone()
                if (
                    attention
                    and self.app.state.master_projection is not None
                ):
                    self.app.state.master_projection.safe_project_attention(
                        int(attention["id"])
                    )
                continue
            if self.app.state.master_projection is not None:
                self.app.state.master_projection.safe_project_task(
                    int(row["id"])
                )
            if job["status"] == "queued":
                # Dependency blockers are durable on the Task and are not start
                # failures. A prerequisite transition will retry this Task.
                continue
            started.append(row["id"])
            turns_used += 1
        if self.app.state.master_projection is not None:
            self.app.state.master_projection.safe_reconcile()
        return {
            "active": True,
            "started": started,
            "capacity": master_capacity(
                conn, session["id"], max_parallel=max_parallel
            ),
        }

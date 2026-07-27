"""Durable, idempotent projections into the canonical Master conversation.

Jobs, runs, checkpoints, Attention, node state, and Satpam rows remain
authoritative. This service writes only a concise Master message, one typed
session event, and the ledger row that links both back to their source.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from typing import Any, Mapping

from .event_types import MASTER_PROJECTION_EVENT_TYPES

log = logging.getLogger("proxima.master_projection")


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer identifier")
    return int(value)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


class MasterProjectionService:
    """One projection boundary for Task and supervision state."""

    def __init__(self, app: Any):
        self.app = app

    def safe_project_task(self, job_id: int) -> dict[str, Any] | None:
        try:
            return self.project_task(job_id)
        except Exception:
            log.exception("Master Task projection failed for Task %s", job_id)
            return None

    def safe_project_attention(
        self, attention_id: int
    ) -> dict[str, Any] | None:
        try:
            return self.project_attention(attention_id)
        except Exception:
            log.exception(
                "Master Attention projection failed for row %s",
                attention_id,
            )
            return None

    def safe_reconcile(self) -> dict[str, int]:
        try:
            return self.reconcile()
        except Exception:
            log.exception("Master projection reconciliation failed")
            return {"observed": 0, "created": 0}

    @property
    def conn(self) -> sqlite3.Connection:
        return self.app.state.worker_db

    def _insert(
        self,
        *,
        owner_user_id: int,
        master_session_id: int,
        projection_key: str,
        projection_type: str,
        source_table: str,
        source_id: int,
        content: str,
        payload: dict[str, Any],
        task_id: int | None = None,
    ) -> dict[str, Any]:
        if projection_type not in MASTER_PROJECTION_EVENT_TYPES:
            raise ValueError(f"unknown Master projection type {projection_type!r}")
        conn = self.conn
        created = False
        event_id: int | None = None
        with self.app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO master_projections("
                    "owner_user_id, master_session_id, projection_key, "
                    "projection_type, source_table, source_id, task_id"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner_user_id,
                        master_session_id,
                        projection_key,
                        projection_type,
                        source_table,
                        source_id,
                        task_id,
                    ),
                )
                if cursor.rowcount == 0:
                    row = conn.execute(
                        "SELECT * FROM master_projections "
                        "WHERE owner_user_id = ? AND projection_key = ?",
                        (owner_user_id, projection_key),
                    ).fetchone()
                    conn.execute("COMMIT")
                    return dict(row) if row else {}

                projection_id = _as_int(cursor.lastrowid)
                message_cursor = conn.execute(
                    "INSERT INTO messages(session_id, role, content, author) "
                    "VALUES (?, 'assistant', ?, 'Master')",
                    (master_session_id, content[:2000]),
                )
                message_id = _as_int(message_cursor.lastrowid)
                event_payload = {
                    "projection_id": projection_id,
                    "projection_key": projection_key,
                    "message_id": message_id,
                    "owner_user_id": owner_user_id,
                    "master_session_id": master_session_id,
                    "source": {"table": source_table, "id": source_id},
                    **payload,
                }
                event_cursor = conn.execute(
                    "INSERT INTO events("
                    "run_id, session_id, project_id, seq, type, payload"
                    ") VALUES (NULL, ?, ?, ?, ?, ?)",
                    (
                        master_session_id,
                        payload.get("container_id"),
                        projection_id,
                        projection_type,
                        json.dumps(
                            event_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
                event_id = _as_int(event_cursor.lastrowid)
                event_payload["event_id"] = event_id
                conn.execute(
                    "UPDATE master_projections SET message_id = ?, event_id = ?, "
                    "payload_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        message_id,
                        event_id,
                        json.dumps(
                            event_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        projection_id,
                    ),
                )
                conn.execute(
                    "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (master_session_id,),
                )
                conn.execute("COMMIT")
                created = True
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if created and event_id is not None:
            self.app.state.hub.notify(master_session_id)
        row = conn.execute(
            "SELECT * FROM master_projections WHERE owner_user_id = ? "
            "AND projection_key = ?",
            (owner_user_id, projection_key),
        ).fetchone()
        return dict(row) if row else {}

    def _task(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT j.*, ms.owner_user_id AS master_owner_user_id, "
            "ms.mode AS master_mode, p.name AS container_name, "
            "p.slug AS container_slug, pa.kind AS area_kind, "
            "pa.rel_path AS area_rel_path "
            "FROM jobs j "
            "JOIN sessions ms ON ms.id = j.origin_master_session_id "
            "LEFT JOIN projects p ON p.id = j.project_id "
            "LEFT JOIN project_areas pa ON pa.id = j.target_area_id "
            "WHERE j.id = ? AND ms.mode = 'master' "
            "AND j.created_by = ms.owner_user_id",
            (job_id,),
        ).fetchone()

    def project_task(self, job_id: int) -> dict[str, Any] | None:
        job = self._task(job_id)
        if job is None:
            return None
        status = str(job["status"])
        reason = str(job["blocked_reason"] or "").strip()
        projection_type: str
        verb: str
        key_suffix: str
        if status == "running":
            projection_type = "master.task.started"
            verb = "Started"
            key_suffix = "started"
        elif status == "done":
            projection_type = "master.task.completed"
            verb = "Completed"
            key_suffix = "completed"
        elif status == "failed":
            projection_type = "master.task.failed"
            verb = "Failed"
            key_suffix = "failed"
        elif status == "cancelled":
            projection_type = "master.task.cancelled"
            verb = "Cancelled"
            key_suffix = "cancelled"
        elif status == "review":
            projection_type = "master.task.review_ready"
            verb = "Ready for review"
            key_suffix = "review"
        elif status == "queued" and reason.startswith("Blocked by prerequisite"):
            projection_type = "master.task.blocked"
            verb = "Blocked"
            digest = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:16]
            key_suffix = f"blocked:{digest}"
        else:
            return None

        checkpoint = None
        if status == "review":
            checkpoint = self.conn.execute(
                "SELECT id FROM job_checkpoints WHERE job_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        failure_reason = ""
        if status == "failed":
            failure_reason = str(job["rejected_reason"] or "").strip()
            if not failure_reason and job["session_id"] is not None:
                last_run = self.conn.execute(
                    "SELECT error FROM runs WHERE session_id = ? "
                    "AND error IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (job["session_id"],),
                ).fetchone()
                failure_reason = str(last_run["error"] or "").strip() if last_run else ""

        title = str(job["title"] or f"Task {job_id}")
        content = f'{verb} Task #{job_id} "{title}".'
        if status == "review":
            content = f'Task #{job_id} "{title}" is ready for review.'
            if checkpoint:
                content += f" Checkpoint #{checkpoint['id']} is available."
        elif status == "failed" and failure_reason:
            content += f" {failure_reason[:500]}"
        elif status == "queued":
            content += f" {reason[:500]}"

        payload = {
            "task": {
                "id": job_id,
                "title": title,
                "status": status,
                "blocked_reason": reason or None,
                "container_id": job["project_id"],
                "area_id": job["target_area_id"],
                "origin_master_session_id": job["origin_master_session_id"],
            },
            "task_id": job_id,
            "task_status": status,
            "container_id": job["project_id"],
            "container_slug": job["container_slug"],
            "area_id": job["target_area_id"],
            "area_kind": job["area_kind"],
            "checkpoint_id": checkpoint["id"] if checkpoint else None,
            "attention_required": status == "review" or bool(reason),
            "toast_key": f"task:{job_id}:{key_suffix}",
        }
        return self._insert(
            owner_user_id=_as_int(job["master_owner_user_id"]),
            master_session_id=_as_int(job["origin_master_session_id"]),
            projection_key=f"task:{job_id}:{key_suffix}",
            projection_type=projection_type,
            source_table="jobs",
            source_id=job_id,
            task_id=job_id,
            content=content,
            payload=payload,
        )

    def project_satpam(self, intervention_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT si.*, j.title, j.project_id, j.target_area_id, "
            "j.origin_master_session_id, ms.owner_user_id "
            "FROM satpam_interventions si "
            "JOIN jobs j ON j.id = si.job_id "
            "JOIN sessions ms ON ms.id = j.origin_master_session_id "
            "WHERE si.id = ? AND ms.mode = 'master' "
            "AND j.created_by = ms.owner_user_id",
            (intervention_id,),
        ).fetchone()
        if row is None:
            return None
        action = str(row["action"])
        status = str(row["status"])
        if action == "steer":
            projection_type = "master.satpam.steered"
            label = "steered"
        elif action == "escalate":
            projection_type = "master.satpam.escalated"
            label = "escalated"
        elif action == "restart" and status == "pending":
            projection_type = "master.satpam.restart_queued"
            label = "needs approval to restart"
        elif action == "restart" and status in {"applied", "approved"}:
            projection_type = "master.satpam.restarted"
            label = "restarted"
        else:
            return None
        job_id = _as_int(row["job_id"])
        content = (
            f'Satpam {label} Task #{job_id} "{row["title"]}". '
            f'{str(row["reason"])[:700]}'
        )
        return self._insert(
            owner_user_id=_as_int(row["owner_user_id"]),
            master_session_id=_as_int(row["origin_master_session_id"]),
            projection_key=f"satpam:{intervention_id}:{projection_type}",
            projection_type=projection_type,
            source_table="satpam_interventions",
            source_id=intervention_id,
            task_id=job_id,
            content=content,
            payload={
                "task_id": job_id,
                "container_id": row["project_id"],
                "area_id": row["target_area_id"],
                "intervention_id": intervention_id,
                "action": action,
                "detection": row["detection"],
                "intervention_status": status,
                "attention_required": status == "pending" or action == "escalate",
                "toast_key": f"satpam:{intervention_id}:{projection_type}",
            },
        )

    def project_attention(self, attention_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM attention_items WHERE id = ? AND status = 'open'",
            (attention_id,),
        ).fetchone()
        if row is None:
            return None
        target = _json_object(row["target_json"])
        master_session_id = target.get("origin_master_session_id")
        if master_session_id is None:
            source_key = str(row["source_key"] or "")
            parts = source_key.split(":")
            if len(parts) >= 3 and parts[0] == "master":
                master_session_id = parts[1]
        task_id = target.get("job_id")
        task = None
        if task_id is not None:
            task = self.conn.execute(
                "SELECT j.origin_master_session_id, j.created_by, "
                "ms.owner_user_id FROM jobs j JOIN sessions ms "
                "ON ms.id = j.origin_master_session_id "
                "WHERE j.id = ? AND ms.mode = 'master' "
                "AND j.created_by = ms.owner_user_id",
                (_as_int(task_id),),
            ).fetchone()
            if task is None:
                return None
            if (
                master_session_id is not None
                and _as_int(master_session_id)
                != _as_int(task["origin_master_session_id"])
            ):
                return None
            master_session_id = task["origin_master_session_id"]
        if master_session_id is None:
            return None
        session = self.conn.execute(
            "SELECT id, owner_user_id FROM sessions "
            "WHERE id = ? AND mode = 'master'",
            (_as_int(master_session_id),),
        ).fetchone()
        if session is None:
            return None
        message = str(target.get("message") or "").strip()
        content = f'Attention needed: {row["title"]}.'
        if message:
            content += f" {message[:700]}"
        projection_type = (
            "master.satpam.recovery_failed"
            if row["kind"] == "satpam_recovery_failed"
            else (
                "master.supervisor.outcome"
                if str(row["kind"]).startswith("master_")
                and row["kind"] != "master_decision"
                else "master.attention.required"
            )
        )
        return self._insert(
            owner_user_id=_as_int(session["owner_user_id"]),
            master_session_id=_as_int(session["id"]),
            projection_key=f"attention:{attention_id}:{projection_type}",
            projection_type=projection_type,
            source_table="attention_items",
            source_id=attention_id,
            task_id=_as_int(task_id) if task_id is not None else None,
            content=content,
            payload={
                "attention_id": attention_id,
                "attention_kind": row["kind"],
                "task_id": task_id,
                "container_id": target.get("container_id"),
                "intervention_id": target.get("intervention_id"),
                "attention_required": True,
                "toast_key": f"attention:{attention_id}",
            },
        )

    def observe_worker_event(
        self,
        *,
        event_id: int,
        run_id: int,
        session_id: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Project a committed control event. Raw stream deltas are ignored."""
        if event_type in {
            "message.delta",
            "reasoning.delta",
            "tool.start",
            "tool.complete",
        }:
            return
        try:
            job_id = payload.get("job_id") or payload.get("job")
            if job_id is None:
                session = self.conn.execute(
                    "SELECT job_id FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                job_id = session["job_id"] if session else None
            if job_id is not None:
                self.project_task(_as_int(job_id))
            if event_type.startswith("satpam."):
                intervention_id = payload.get("intervention_id")
                if intervention_id is not None:
                    self.project_satpam(_as_int(intervention_id))
                elif job_id is not None:
                    row = self.conn.execute(
                        "SELECT id FROM satpam_interventions WHERE job_id = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (_as_int(job_id),),
                    ).fetchone()
                    if row:
                        self.project_satpam(_as_int(row["id"]))
        except Exception:
            log.exception(
                "Master projection failed for worker event %s (%s)",
                event_id,
                event_type,
            )

    def reconcile(self) -> dict[str, int]:
        """Low-frequency restart/reconnect safety over authoritative rows."""
        before = self.conn.execute(
            "SELECT COUNT(*) AS c FROM master_projections"
        ).fetchone()["c"]
        jobs = self.conn.execute(
            "SELECT j.id FROM jobs j JOIN sessions ms "
            "ON ms.id = j.origin_master_session_id "
            "WHERE ms.mode = 'master' AND j.created_by = ms.owner_user_id "
            "ORDER BY COALESCE(j.updated_at, j.created_at), j.id"
        ).fetchall()
        for row in jobs:
            self.project_task(_as_int(row["id"]))
        interventions = self.conn.execute(
            "SELECT si.id FROM satpam_interventions si "
            "JOIN jobs j ON j.id = si.job_id "
            "JOIN sessions ms ON ms.id = j.origin_master_session_id "
            "WHERE ms.mode = 'master' AND j.created_by = ms.owner_user_id "
            "ORDER BY si.id"
        ).fetchall()
        for row in interventions:
            self.project_satpam(_as_int(row["id"]))
        attentions = self.conn.execute(
            "SELECT id FROM attention_items WHERE status = 'open' ORDER BY id"
        ).fetchall()
        for row in attentions:
            self.project_attention(_as_int(row["id"]))
        after = self.conn.execute(
            "SELECT COUNT(*) AS c FROM master_projections"
        ).fetchone()["c"]
        return {
            "observed": len(jobs) + len(interventions) + len(attentions),
            "created": _as_int(after) - _as_int(before),
        }

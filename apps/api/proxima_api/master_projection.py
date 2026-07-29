"""Durable, idempotent projections into the canonical Master conversation.

Jobs, runs, checkpoints, Attention, node state, and Satpam rows remain
authoritative. This service writes only a concise Master message, one typed
session event, and the ledger row that links both back to their source.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, Mapping

from .event_types import MASTER_PROJECTION_EVENT_TYPES
from . import master_focus

log = logging.getLogger("proxima.master_projection")
MAX_PROJECTION_PAYLOAD_BYTES = 16 * 1024
_SAFE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,199}$")
_BASE_PAYLOAD_FIELDS = {
    "event_id",
    "focus_container_id",
    "focus_epoch_id",
    "master_session_id",
    "message_id",
    "owner_user_id",
    "projection_id",
    "projection_key",
    "source",
    "subject_container_id",
}
_TASK_PAYLOAD_FIELDS = {
    "area_id",
    "area_kind",
    "attention_required",
    "checkpoint_id",
    "container_id",
    "container_slug",
    "task_id",
    "task_status",
    "toast_key",
}
_SATPAM_PAYLOAD_FIELDS = {
    "action",
    "area_id",
    "attention_required",
    "container_id",
    "detection",
    "intervention_id",
    "intervention_status",
    "task_id",
    "toast_key",
}
_ATTENTION_PAYLOAD_FIELDS = {
    "area_id",
    "attention_id",
    "attention_kind",
    "attention_required",
    "container_id",
    "intervention_id",
    "task_id",
    "toast_key",
}


def _source_table_for(projection_type: str) -> str:
    if projection_type.startswith("master.task."):
        return "jobs"
    if (
        projection_type.startswith("master.satpam.")
        and projection_type != "master.satpam.recovery_failed"
    ):
        return "satpam_interventions"
    return "attention_items"


def _event_payload_fields(projection_type: str) -> set[str]:
    if projection_type.startswith("master.task."):
        return _TASK_PAYLOAD_FIELDS
    if (
        projection_type.startswith("master.satpam.")
        and projection_type != "master.satpam.recovery_failed"
    ):
        return _SATPAM_PAYLOAD_FIELDS
    return _ATTENTION_PAYLOAD_FIELDS


def _positive_id(value: Any, *, optional: bool = False) -> bool:
    return (optional and value is None) or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    )


def _validate_event_payload(
    projection_type: str,
    payload: Mapping[str, Any],
) -> None:
    if set(payload) != _event_payload_fields(projection_type):
        raise ValueError("Master projection payload fields are invalid")
    if not isinstance(payload.get("attention_required"), bool):
        raise ValueError("Master projection attention flag is invalid")
    if not isinstance(payload.get("toast_key"), str) or not _SAFE_TOKEN.fullmatch(
        str(payload["toast_key"])
    ):
        raise ValueError("Master projection toast key is invalid")
    if not _positive_id(payload.get("task_id"), optional=True):
        raise ValueError("Master projection Task link is invalid")
    if not _positive_id(payload.get("container_id"), optional=True):
        raise ValueError("Master projection Container link is invalid")
    if not _positive_id(payload.get("area_id"), optional=True):
        raise ValueError("Master projection Area link is invalid")
    if projection_type.startswith("master.task."):
        if payload["task_id"] is None:
            raise ValueError("Master Task projection has no Task")
        if payload.get("task_status") not in {
            "queued",
            "running",
            "review",
            "done",
            "failed",
            "cancelled",
        }:
            raise ValueError("Master projection Task status is invalid")
        for key in ("container_slug", "area_kind"):
            value = payload.get(key)
            if not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
                raise ValueError(f"Master projection {key} is invalid")
        if not _positive_id(payload.get("checkpoint_id"), optional=True):
            raise ValueError("Master projection checkpoint link is invalid")
    elif projection_type.startswith("master.satpam.") and (
        projection_type != "master.satpam.recovery_failed"
    ):
        if (
            payload["task_id"] is None
            or not _positive_id(payload.get("intervention_id"))
            or payload.get("action") not in {"steer", "restart", "escalate"}
            or payload.get("detection")
            not in {"stalled", "looping", "confused"}
            or payload.get("intervention_status")
            not in {"applied", "pending", "approved", "dismissed"}
        ):
            raise ValueError("Master Satpam projection payload is invalid")
    else:
        if (
            not _positive_id(payload.get("attention_id"))
            or payload.get("attention_kind")
            not in {
                "master_budget",
                "master_decision",
                "permission_job",
                "satpam_recovery_failed",
            }
            or not _positive_id(
                payload.get("intervention_id"),
                optional=True,
            )
        ):
            raise ValueError("Master Attention projection payload is invalid")


def _safe_projection_content(
    projection_type: str,
    payload: Mapping[str, Any],
) -> str:
    task_id = payload.get("task_id")
    if projection_type == "master.task.review_ready":
        content = f"Task #{task_id} is ready for review."
        if payload.get("checkpoint_id") is not None:
            content += f" Checkpoint #{payload['checkpoint_id']} is available."
        return content
    task_verbs = {
        "master.task.started": "Started",
        "master.task.completed": "Completed",
        "master.task.failed": "Failed",
        "master.task.cancelled": "Cancelled",
    }
    if projection_type in task_verbs:
        return f"{task_verbs[projection_type]} Task #{task_id}."
    if projection_type == "master.task.blocked":
        return f"Task #{task_id} is blocked by a prerequisite."
    satpam_labels = {
        "master.satpam.steered": "steered",
        "master.satpam.restart_queued": "needs approval to restart",
        "master.satpam.restarted": "restarted",
        "master.satpam.escalated": "escalated",
    }
    if projection_type in satpam_labels:
        return f"Satpam {satpam_labels[projection_type]} Task #{task_id}."
    kind = payload.get("attention_kind")
    if kind == "permission_job":
        return f"Task #{task_id} needs an owner permission decision."
    if kind == "satpam_recovery_failed":
        return (
            "Satpam could not complete the approved recovery for "
            f"Task #{task_id}."
        )
    if kind == "master_budget":
        return "Master unattended work stopped at its configured budget."
    if task_id is not None:
        return f"Master needs an owner decision for Task #{task_id}."
    return "Master needs an owner decision."


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


def assert_master_projection_ledger(conn: sqlite3.Connection) -> None:
    """Validate committed projection rows and their immutable delivery links."""
    table = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'master_projections'"
    ).fetchone()
    if table is None:
        raise RuntimeError("Master projection ledger is missing")
    schema_sql = " ".join(str(table["sql"] or "").lower().split())
    required_schema = (
        "on delete restrict",
        "check (projection_type in",
        "source_table in",
        "source_id > 0",
    )
    if any(token not in schema_sql for token in required_schema):
        raise RuntimeError("Master projection ledger schema is incomplete")
    expected_foreign_keys = {
        "owner_user_id": ("users", "CASCADE"),
        "master_session_id": ("sessions", "CASCADE"),
        "task_id": ("jobs", "SET NULL"),
        "message_id": ("messages", "RESTRICT"),
        "event_id": ("events", "RESTRICT"),
    }
    foreign_keys = {
        str(row[3]): (str(row[2]), str(row[6]).upper())
        for row in conn.execute(
            "PRAGMA foreign_key_list(master_projections)"
        ).fetchall()
    }
    if foreign_keys != expected_foreign_keys:
        raise RuntimeError("Master projection ledger foreign keys are incomplete")
    indexes = {
        str(row[1]): bool(row[2])
        for row in conn.execute(
            "PRAGMA index_list(master_projections)"
        ).fetchall()
    }
    for name in (
        "uq_master_projections_source_type",
        "uq_master_projections_message",
        "uq_master_projections_event",
    ):
        if not indexes.get(name):
            raise RuntimeError("Master projection ledger indexes are incomplete")

    rows = conn.execute(
        "SELECT projection.*, "
        "master.mode AS master_mode, "
        "master.owner_user_id AS session_owner_user_id, "
        "master.project_id AS master_project_id, "
        "message.session_id AS message_session_id, "
        "message.role AS message_role, "
        "message.author AS message_author, "
        "message.content AS message_content, "
        "event.session_id AS event_session_id, "
        "event.run_id AS event_run_id, "
        "event.seq AS event_seq, "
        "event.type AS event_type, "
        "event.payload AS event_payload "
        "FROM master_projections projection "
        "LEFT JOIN sessions master "
        "ON master.id = projection.master_session_id "
        "LEFT JOIN messages message ON message.id = projection.message_id "
        "LEFT JOIN events event ON event.id = projection.event_id "
        "ORDER BY projection.id"
    ).fetchall()
    for row in rows:
        valid_links = (
            row["message_id"] is not None
            and row["event_id"] is not None
            and row["master_mode"] == "master"
            and row["master_project_id"] is None
            and row["session_owner_user_id"] == row["owner_user_id"]
            and row["message_session_id"] == row["master_session_id"]
            and row["message_role"] == "assistant"
            and row["message_author"] == "Master"
            and row["event_session_id"] == row["master_session_id"]
            and row["event_run_id"] is None
            and row["event_seq"] == row["id"]
            and row["event_type"] == row["projection_type"]
            and row["source_table"]
            == _source_table_for(str(row["projection_type"]))
        )
        if not valid_links:
            raise RuntimeError("Master projection ledger has incomplete links")
        try:
            payload = json.loads(str(row["payload_json"]))
            event_payload = json.loads(str(row["event_payload"]))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Master projection ledger contains malformed payload JSON"
            ) from exc
        expected_fields = _BASE_PAYLOAD_FIELDS | _event_payload_fields(
            str(row["projection_type"])
        )
        if (
            not isinstance(payload, dict)
            or payload != event_payload
            or set(payload) != expected_fields
            or len(str(row["payload_json"]).encode("utf-8"))
            > MAX_PROJECTION_PAYLOAD_BYTES
            or payload.get("projection_id") != row["id"]
            or payload.get("projection_key") != row["projection_key"]
            or payload.get("message_id") != row["message_id"]
            or payload.get("event_id") != row["event_id"]
            or payload.get("owner_user_id") != row["owner_user_id"]
            or payload.get("master_session_id") != row["master_session_id"]
            or (
                row["task_id"] is not None
                and payload.get("task_id") != row["task_id"]
            )
            or payload.get("source")
            != {"table": row["source_table"], "id": row["source_id"]}
        ):
            raise RuntimeError("Master projection ledger payload links are incomplete")
        event_payload_data = {
            key: payload[key]
            for key in _event_payload_fields(str(row["projection_type"]))
        }
        try:
            _validate_event_payload(
                str(row["projection_type"]),
                event_payload_data,
            )
        except ValueError as exc:
            raise RuntimeError(
                "Master projection ledger payload is unsafe"
            ) from exc
        if row["message_content"] != _safe_projection_content(
            str(row["projection_type"]),
            event_payload_data,
        ):
            raise RuntimeError("Master projection message is not canonical")
        if row["source_table"] == "jobs":
            source = conn.execute(
                "SELECT id FROM jobs WHERE id = ?", (row["source_id"],)
            ).fetchone()
            valid_source = (
                (
                    source is not None
                    and row["task_id"] == row["source_id"]
                )
                or (source is None and row["task_id"] is None)
            )
        elif row["source_table"] == "satpam_interventions":
            source = conn.execute(
                "SELECT job_id FROM satpam_interventions WHERE id = ?",
                (row["source_id"],),
            ).fetchone()
            valid_source = (
                (
                    source is not None
                    and source["job_id"] == row["task_id"]
                )
                or (source is None and row["task_id"] is None)
            )
        else:
            source = conn.execute(
                "SELECT id FROM attention_items WHERE id = ?",
                (row["source_id"],),
            ).fetchone()
            valid_source = source is not None or row["task_id"] is None
        if not valid_source:
            raise RuntimeError("Master projection ledger source link is incomplete")


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

    def safe_project_satpam(
        self, intervention_id: int
    ) -> dict[str, Any] | None:
        try:
            return self.project_satpam(intervention_id)
        except Exception:
            log.exception(
                "Master Satpam projection failed for intervention %s",
                intervention_id,
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
        origin_message_id: int | None = None,
    ) -> dict[str, Any]:
        if projection_type not in MASTER_PROJECTION_EVENT_TYPES:
            raise ValueError(f"unknown Master projection type {projection_type!r}")
        if source_table != _source_table_for(projection_type):
            raise ValueError(
                f"{projection_type!r} cannot project {source_table!r}"
            )
        conn = self.conn
        created = False
        event_id: int | None = None
        with self.app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                session = conn.execute(
                    "SELECT owner_user_id, mode, project_id FROM sessions "
                    "WHERE id = ?",
                    (master_session_id,),
                ).fetchone()
                if (
                    session is None
                    or session["mode"] != "master"
                    or session["project_id"] is not None
                    or _as_int(session["owner_user_id"]) != owner_user_id
                ):
                    raise ValueError("projection Master session ownership is invalid")
                _validate_event_payload(projection_type, payload)
                if content != _safe_projection_content(
                    projection_type,
                    payload,
                ):
                    raise ValueError(
                        "Master projection message is not canonical"
                    )
                encoded_payload = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(encoded_payload) > MAX_PROJECTION_PAYLOAD_BYTES:
                    raise ValueError("Master projection payload is too large")
                try:
                    cursor = conn.execute(
                        "INSERT INTO master_projections("
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
                except sqlite3.IntegrityError:
                    row = conn.execute(
                        "SELECT * FROM master_projections "
                        "WHERE owner_user_id = ? AND projection_key = ?",
                        (owner_user_id, projection_key),
                    ).fetchone()
                    if row is None:
                        raise
                    expected = {
                        "master_session_id": master_session_id,
                        "projection_type": projection_type,
                        "source_table": source_table,
                        "source_id": source_id,
                        "task_id": task_id,
                    }
                    if any(row[key] != value for key, value in expected.items()):
                        raise ValueError(
                            "projection key is bound to a different source"
                        )
                    if row["message_id"] is None or row["event_id"] is None:
                        raise ValueError(
                            "existing Master projection is incomplete"
                        )
                    conn.execute("COMMIT")
                    return dict(row)

                projection_id = _as_int(cursor.lastrowid)
                message_cursor = conn.execute(
                    "INSERT INTO messages(session_id, role, content, author) "
                    "VALUES (?, 'assistant', ?, 'Master')",
                    (master_session_id, content[:2000]),
                )
                message_id = _as_int(message_cursor.lastrowid)
                focus_epoch_id = None
                subject_container_id = payload.get("container_id")
                if task_id is not None:
                    source = conn.execute(
                        "SELECT delegation.origin_focus_epoch_id, "
                        "delegation.origin_focus_captured, "
                        "delegation.container_id, "
                        "epoch.master_session_id AS epoch_session_id "
                        "FROM task_delegations AS delegation "
                        "LEFT JOIN master_focus_epochs AS epoch "
                        "ON epoch.id = delegation.origin_focus_epoch_id "
                        "WHERE delegation.job_id = ?",
                        (task_id,),
                    ).fetchone()
                    if (
                        source is None
                        or not source["origin_focus_captured"]
                        or (
                            source["origin_focus_epoch_id"] is not None
                            and source["epoch_session_id"] != master_session_id
                        )
                    ):
                        raise ValueError(
                            "projection Focus origin is unavailable"
                        )
                    subject_container_id = source["container_id"]
                    focus_epoch_id = source["origin_focus_epoch_id"]
                elif origin_message_id is not None:
                    captured = conn.execute(
                        "SELECT focus.focus_epoch_id "
                        "FROM message_focus AS focus "
                        "JOIN messages AS message "
                        "ON message.id = focus.message_id "
                        "WHERE focus.message_id = ? "
                        "AND message.session_id = ?",
                        (origin_message_id, master_session_id),
                    ).fetchone()
                    if captured is None:
                        raise ValueError(
                            "projection Focus origin is unavailable"
                        )
                    focus_epoch_id = captured["focus_epoch_id"]
                # Notifications retain originating Focus while their subject
                # Container remains separate. They never swap current Focus.
                master_focus.stamp_message(
                    conn,
                    message_id=message_id,
                    focus_epoch_id=focus_epoch_id,
                    subject_container_id=subject_container_id,
                )
                focus_container_id = None
                if focus_epoch_id is not None:
                    epoch = conn.execute(
                        "SELECT container_id FROM master_focus_epochs WHERE id = ?",
                        (focus_epoch_id,),
                    ).fetchone()
                    if epoch is None:
                        raise ValueError(
                            "projection Focus origin is unavailable"
                        )
                    focus_container_id = _as_int(epoch["container_id"])
                event_payload = {
                    "projection_id": projection_id,
                    "projection_key": projection_key,
                    "message_id": message_id,
                    "owner_user_id": owner_user_id,
                    "master_session_id": master_session_id,
                    "source": {"table": source_table, "id": source_id},
                    "focus_epoch_id": focus_epoch_id,
                    "focus_container_id": focus_container_id,
                    "subject_container_id": subject_container_id,
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
                final_payload_json = json.dumps(
                    event_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if (
                    len(final_payload_json.encode("utf-8"))
                    > MAX_PROJECTION_PAYLOAD_BYTES
                ):
                    raise ValueError("Master projection event payload is too large")
                conn.execute(
                    "UPDATE events SET payload = ? WHERE id = ?",
                    (final_payload_json, event_id),
                )
                conn.execute(
                    "UPDATE master_projections SET message_id = ?, event_id = ?, "
                    "payload_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        message_id,
                        event_id,
                        final_payload_json,
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
            "AND ms.project_id IS NULL "
            "AND j.created_by = ms.owner_user_id "
            "AND p.owner_user_id = ms.owner_user_id "
            "AND p.archived_at IS NULL "
            "AND pa.project_id = j.project_id "
            "AND pa.source != 'excluded'",
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
            key_suffix = "blocked"
        else:
            return None

        checkpoint = None
        if status == "review":
            checkpoint = self.conn.execute(
                "SELECT id FROM job_checkpoints WHERE job_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        content = f"{verb} Task #{job_id}."
        if status == "review":
            content = f"Task #{job_id} is ready for review."
            if checkpoint:
                content += f" Checkpoint #{checkpoint['id']} is available."
        elif status == "queued":
            content = f"Task #{job_id} is blocked by a prerequisite."

        payload = {
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
            "JOIN projects p ON p.id = j.project_id "
            "JOIN project_areas pa ON pa.id = j.target_area_id "
            "WHERE si.id = ? AND ms.mode = 'master' "
            "AND ms.project_id IS NULL "
            "AND j.created_by = ms.owner_user_id "
            "AND p.owner_user_id = ms.owner_user_id "
            "AND p.archived_at IS NULL "
            "AND pa.project_id = j.project_id "
            "AND pa.source != 'excluded'",
            (intervention_id,),
        ).fetchone()
        if row is None:
            return None
        action = str(row["action"])
        status = str(row["status"])
        detection = str(row["detection"])
        if detection not in {"stalled", "looping", "confused"}:
            return None
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
        content = f"Satpam {label} Task #{job_id}."
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
                "detection": detection,
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
        if row["kind"] not in {
            "master_budget",
            "master_decision",
            "permission_job",
            "satpam_recovery_failed",
        }:
            return None
        target = _json_object(row["target_json"])
        master_session_id = target.get("origin_master_session_id")
        source_key = str(row["source_key"] or "")
        if master_session_id is None:
            parts = source_key.split(":")
            if len(parts) >= 3 and parts[0] == "master":
                master_session_id = parts[1]
        task_id = target.get("job_id")
        origin_message_id = target.get("origin_message_id")
        task = None
        if task_id is not None:
            task = self.conn.execute(
                "SELECT j.origin_master_session_id, j.created_by, j.project_id, "
                "j.target_area_id, ms.owner_user_id FROM jobs j JOIN sessions ms "
                "ON ms.id = j.origin_master_session_id "
                "JOIN projects p ON p.id = j.project_id "
                "JOIN project_areas pa ON pa.id = j.target_area_id "
                "WHERE j.id = ? AND ms.mode = 'master' "
                "AND ms.project_id IS NULL "
                "AND j.created_by = ms.owner_user_id "
                "AND p.owner_user_id = ms.owner_user_id "
                "AND p.archived_at IS NULL "
                "AND pa.project_id = j.project_id "
                "AND pa.source != 'excluded'",
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
        master_session_id = _as_int(master_session_id)
        origin_message_id_value = (
            _as_int(origin_message_id)
            if origin_message_id is not None
            else None
        )
        if task is None:
            valid_source = (
                row["kind"] == "master_decision"
                and source_key.startswith(f"master:{master_session_id}:")
            ) or (
                row["kind"] == "master_budget"
                and source_key.startswith(
                    f"master-budget:{master_session_id}:"
                )
            )
            if not valid_source:
                return None
        session = self.conn.execute(
            "SELECT id, owner_user_id FROM sessions "
            "WHERE id = ? AND mode = 'master' AND project_id IS NULL",
            (master_session_id,),
        ).fetchone()
        if session is None:
            return None
        task_id_value = _as_int(task_id) if task_id is not None else None
        intervention_id = None
        if row["kind"] == "satpam_recovery_failed":
            if task_id_value is None or target.get("intervention_id") is None:
                return None
            intervention_id = _as_int(target["intervention_id"])
            if self.conn.execute(
                "SELECT 1 FROM satpam_interventions "
                "WHERE id = ? AND job_id = ?",
                (intervention_id, task_id_value),
            ).fetchone() is None:
                return None
        if row["kind"] == "permission_job":
            content = f"Task #{task_id_value} needs an owner permission decision."
        elif row["kind"] == "satpam_recovery_failed":
            content = (
                "Satpam could not complete the approved recovery for "
                f"Task #{task_id_value}."
            )
        elif row["kind"] == "master_budget":
            content = "Master unattended work stopped at its configured budget."
        elif task_id_value is not None:
            content = f"Master needs an owner decision for Task #{task_id_value}."
        else:
            content = "Master needs an owner decision."
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
            task_id=task_id_value,
            origin_message_id=origin_message_id_value,
            content=content,
            payload={
                "attention_id": attention_id,
                "attention_kind": row["kind"],
                "task_id": task_id_value,
                "container_id": (
                    task["project_id"] if task is not None else None
                ),
                "area_id": (
                    task["target_area_id"] if task is not None else None
                ),
                "intervention_id": intervention_id,
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
        """Low-frequency restart/reconnect safety over authoritative rows.

        Each authoritative source row is matched against the projection type
        its current state would produce, and only rows still missing that
        projection are selected. A steady-state tick therefore reads its
        already-projected history through an index but opens no projection
        write transactions for rows that are already up to date.
        """
        before = self.conn.execute(
            "SELECT COUNT(*) AS c FROM master_projections"
        ).fetchone()["c"]
        jobs = self.conn.execute(
            "WITH candidate AS ("
            "  SELECT j.id AS source_id, "
            "    COALESCE(j.updated_at, j.created_at) AS ordering, "
            "    CASE "
            "      WHEN j.status = 'running' THEN 'master.task.started' "
            "      WHEN j.status = 'done' THEN 'master.task.completed' "
            "      WHEN j.status = 'failed' THEN 'master.task.failed' "
            "      WHEN j.status = 'cancelled' THEN 'master.task.cancelled' "
            "      WHEN j.status = 'review' THEN 'master.task.review_ready' "
            "      WHEN j.status = 'queued' "
            "        AND j.blocked_reason LIKE 'Blocked by prerequisite%' "
            "        THEN 'master.task.blocked' "
            "      ELSE NULL "
            "    END AS expected_type "
            "  FROM jobs j JOIN sessions ms "
            "    ON ms.id = j.origin_master_session_id "
            "  WHERE ms.mode = 'master' AND j.created_by = ms.owner_user_id"
            ") "
            "SELECT source_id FROM candidate c "
            "WHERE c.expected_type IS NOT NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM master_projections mp "
            "  WHERE mp.source_table = 'jobs' "
            "  AND mp.source_id = c.source_id "
            "  AND mp.projection_type = c.expected_type"
            ") "
            "ORDER BY c.ordering, c.source_id"
        ).fetchall()
        for row in jobs:
            self.safe_project_task(_as_int(row["source_id"]))
        interventions = self.conn.execute(
            "WITH candidate AS ("
            "  SELECT si.id AS source_id, "
            "    CASE "
            "      WHEN si.detection NOT IN ('stalled', 'looping', 'confused') "
            "        THEN NULL "
            "      WHEN si.action = 'steer' THEN 'master.satpam.steered' "
            "      WHEN si.action = 'escalate' THEN 'master.satpam.escalated' "
            "      WHEN si.action = 'restart' AND si.status = 'pending' "
            "        THEN 'master.satpam.restart_queued' "
            "      WHEN si.action = 'restart' "
            "        AND si.status IN ('applied', 'approved') "
            "        THEN 'master.satpam.restarted' "
            "      ELSE NULL "
            "    END AS expected_type "
            "  FROM satpam_interventions si "
            "  JOIN jobs j ON j.id = si.job_id "
            "  JOIN sessions ms ON ms.id = j.origin_master_session_id "
            "  WHERE ms.mode = 'master' AND j.created_by = ms.owner_user_id"
            ") "
            "SELECT source_id FROM candidate c "
            "WHERE c.expected_type IS NOT NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM master_projections mp "
            "  WHERE mp.source_table = 'satpam_interventions' "
            "  AND mp.source_id = c.source_id "
            "  AND mp.projection_type = c.expected_type"
            ") "
            "ORDER BY c.source_id"
        ).fetchall()
        for row in interventions:
            self.safe_project_satpam(_as_int(row["source_id"]))
        attentions = self.conn.execute(
            "WITH candidate AS ("
            "  SELECT ai.id AS source_id, "
            "    CASE "
            "      WHEN ai.kind = 'satpam_recovery_failed' "
            "        THEN 'master.satpam.recovery_failed' "
            "      WHEN ai.kind = 'master_budget' "
            "        THEN 'master.supervisor.outcome' "
            "      WHEN ai.kind IN ('master_decision', 'permission_job') "
            "        THEN 'master.attention.required' "
            "      ELSE NULL "
            "    END AS expected_type "
            "  FROM attention_items ai WHERE ai.status = 'open'"
            ") "
            "SELECT source_id FROM candidate c "
            "WHERE c.expected_type IS NOT NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM master_projections mp "
            "  WHERE mp.source_table = 'attention_items' "
            "  AND mp.source_id = c.source_id "
            "  AND mp.projection_type = c.expected_type"
            ") "
            "ORDER BY c.source_id"
        ).fetchall()
        for row in attentions:
            self.safe_project_attention(_as_int(row["source_id"]))
        after = self.conn.execute(
            "SELECT COUNT(*) AS c FROM master_projections"
        ).fetchone()["c"]
        return {
            "observed": len(jobs) + len(interventions) + len(attentions),
            "created": _as_int(after) - _as_int(before),
        }

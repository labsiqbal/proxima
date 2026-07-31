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
from .event_payloads import (
    MAX_DURABLE_EVENT_PAYLOAD_BYTES,
    encode_bounded_event_payload,
)
from .master_persistence import canonical_job_payload
from .run_projection import effective_job_status_sql
from .task_state_events import (
    RecoveryAttributionError,
    claim_task_projection_generation,
    publish_master_recovery,
    publish_master_recovery_correction,
    task_projection_epoch,
    task_projection_state,
)
from . import master_focus

log = logging.getLogger("proxima.master_projection")
MAX_PROJECTION_PAYLOAD_BYTES = MAX_DURABLE_EVENT_PAYLOAD_BYTES
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


class ProjectionAttributionError(ValueError):
    def __init__(self, code: str):
        if code not in {
            "focus_attribution_unavailable",
            "projection_scope_unavailable",
        }:
            raise ValueError("invalid projection attribution failure")
        self.code = code
        super().__init__(code.replace("_", " "))
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


def _task_projection_key(
    job_id: int,
    key_suffix: str,
    projection_epoch: int,
    projection_revision: int,
) -> str:
    if projection_revision > 0:
        return (
            f"task:{job_id}:revision:{projection_revision}:{key_suffix}"
        )
    if projection_epoch <= 0:
        return f"task:{job_id}:{key_suffix}"
    return f"task:{job_id}:epoch:{projection_epoch}:{key_suffix}"


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
    if indexes.get("idx_master_projections_source") is not False:
        raise RuntimeError("Master projection ledger indexes are incomplete")
    for name in (
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


def assert_task_projection_outbox(
    conn: sqlite3.Connection,
    *,
    require_ordered: bool | None = None,
    require_state_generation: bool | None = None,
    require_legacy_ordering: bool | None = None,
) -> None:
    table = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'task_projection_outbox'"
    ).fetchone()
    if table is None:
        raise RuntimeError("Task projection outbox is missing")
    schema_sql = " ".join(str(table["sql"] or "").lower().split())
    version_row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version "
        "FROM schema_migrations"
    ).fetchone()
    ordered = (
        require_ordered
        if require_ordered is not None
        else _as_int(version_row["version"]) >= 45
    )
    state_generation = (
        require_state_generation
        if require_state_generation is not None
        else _as_int(version_row["version"]) >= 46
    )
    legacy_ordering = (
        require_legacy_ordering
        if require_legacy_ordering is not None
        else _as_int(version_row["version"]) >= 47
    )
    if not ordered:
        if (
            "unique(task_event_id)" not in schema_sql
            or "state = 'projected' and projection_id is not null"
            not in schema_sql
        ):
            raise RuntimeError("Task projection outbox schema is incomplete")
        return
    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(task_projection_outbox)"
        ).fetchall()
    }
    if "projection_revision" not in columns:
        raise RuntimeError("Task projection revision is missing")
    required_schema = (
        "'failed_attribution', 'superseded'",
        "references jobs(id) on delete cascade",
        "references events(id) on delete cascade",
        "references master_projections(id) on delete set null",
        "unique(task_event_id)",
        "state = 'projected' and projection_id is not null",
        "superseded_by_event_id is not null",
    )
    if any(token not in schema_sql for token in required_schema):
        raise RuntimeError("Task projection outbox schema is incomplete")
    expected_foreign_keys = {
        "job_id": ("jobs", "CASCADE"),
        "task_event_id": ("events", "CASCADE"),
        "projection_id": ("master_projections", "SET NULL"),
        "superseded_by_event_id": ("events", "SET NULL"),
    }
    foreign_keys = {
        str(row[3]): (str(row[2]), str(row[6]).upper())
        for row in conn.execute(
            "PRAGMA foreign_key_list(task_projection_outbox)"
        ).fetchall()
    }
    if foreign_keys != expected_foreign_keys:
        raise RuntimeError("Task projection outbox foreign keys are incomplete")
    indexes = {
        str(row[1]): bool(row[2])
        for row in conn.execute(
            "PRAGMA index_list(task_projection_outbox)"
        ).fetchall()
    }
    if indexes.get("idx_task_projection_outbox_state") is not False:
        raise RuntimeError("Task projection outbox indexes are incomplete")
    if not indexes.get("uq_task_projection_outbox_revision"):
        raise RuntimeError("Task projection outbox indexes are incomplete")
    recovery = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'task_recovery_outbox'"
    ).fetchone()
    if recovery is None:
        raise RuntimeError("Task recovery outbox is missing")
    recovery_schema = " ".join(
        str(recovery["sql"] or "").lower().split()
    )
    recovery_columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_outbox)"
        ).fetchall()
    }
    recovery_tokens = (
        "unique(task_event_id)",
        "state = 'projected' and message_id is not null",
    )
    if any(token not in recovery_schema for token in recovery_tokens):
        raise RuntimeError("Task recovery outbox schema is incomplete")
    has_ordering_gap = "ordering_successor_id" in recovery_columns
    if state_generation:
        if (
            not all(
                state in recovery_schema
                for state in (
                    "'pending'",
                    "'projected'",
                    "'failed_attribution'",
                )
            )
            or "superseded_by_event_id" in recovery_columns
            or "projection_revision" in recovery_columns
        ):
            raise RuntimeError("Task recovery outbox schema is incomplete")
        if legacy_ordering and (
            not has_ordering_gap
            or "'legacy_ordering_gap'" not in recovery_schema
            or "ordering_successor_id is not null" not in recovery_schema
        ):
            raise RuntimeError("Task recovery ordering gaps are incomplete")
    else:
        old_recovery = "superseded_by_event_id" in recovery_columns
        if old_recovery and (
            "'failed_attribution', 'superseded'" not in recovery_schema
            or "superseded_by_event_id is not null" not in recovery_schema
        ):
            raise RuntimeError("Task recovery outbox schema is incomplete")
        if not old_recovery and not all(
            state in recovery_schema
            for state in ("'pending'", "'projected'", "'failed_attribution'")
        ):
            raise RuntimeError("Task recovery outbox schema is incomplete")
    recovery_foreign_keys = {
        str(row[3]): (str(row[2]), str(row[6]).upper())
        for row in conn.execute(
            "PRAGMA foreign_key_list(task_recovery_outbox)"
        ).fetchall()
    }
    expected_recovery_foreign_keys = {
        "job_id": ("jobs", "CASCADE"),
        "task_event_id": ("events", "CASCADE"),
        "master_session_id": ("sessions", "SET NULL"),
        "message_id": ("messages", "RESTRICT"),
        "event_id": ("events", "RESTRICT"),
    }
    if has_ordering_gap:
        expected_recovery_foreign_keys["ordering_successor_id"] = (
            "task_recovery_outbox",
            "CASCADE",
        )
    if not state_generation and "superseded_by_event_id" in recovery_columns:
        expected_recovery_foreign_keys["superseded_by_event_id"] = (
            "events",
            "SET NULL",
        )
    if recovery_foreign_keys != expected_recovery_foreign_keys:
        raise RuntimeError("Task recovery outbox foreign keys are incomplete")
    recovery_indexes = {
        str(row[1]): bool(row[2])
        for row in conn.execute(
            "PRAGMA index_list(task_recovery_outbox)"
        ).fetchall()
    }
    if recovery_indexes.get("idx_task_recovery_outbox_state") is not False:
        raise RuntimeError("Task recovery outbox indexes are incomplete")
    if legacy_ordering:
        correction = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'task_recovery_corrections'"
        ).fetchone()
        if correction is None:
            raise RuntimeError("Task recovery corrections are missing")
        correction_schema = " ".join(
            str(correction["sql"] or "").lower().split()
        )
        correction_tokens = (
            "unique(successor_outbox_id)",
            "state = 'projected' and message_id is not null",
            "gap_count > 0",
        )
        if any(
            token not in correction_schema for token in correction_tokens
        ):
            raise RuntimeError("Task recovery corrections are incomplete")
        expected_correction_foreign_keys = {
            "job_id": ("jobs", "CASCADE"),
            "successor_outbox_id": (
                "task_recovery_outbox",
                "CASCADE",
            ),
            "master_session_id": ("sessions", "SET NULL"),
            "message_id": ("messages", "RESTRICT"),
            "event_id": ("events", "RESTRICT"),
        }
        correction_foreign_keys = {
            str(row[3]): (str(row[2]), str(row[6]).upper())
            for row in conn.execute(
                "PRAGMA foreign_key_list(task_recovery_corrections)"
            ).fetchall()
        }
        if correction_foreign_keys != expected_correction_foreign_keys:
            raise RuntimeError("Task recovery correction links are incomplete")
        correction_indexes = {
            str(row[1]): bool(row[2])
            for row in conn.execute(
                "PRAGMA index_list(task_recovery_corrections)"
            ).fetchall()
        }
        if (
            correction_indexes.get("idx_task_recovery_corrections_state")
            is not False
        ):
            raise RuntimeError("Task recovery correction indexes are incomplete")
        trigger = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'task_recovery_ordering_gap_immutable'"
        ).fetchone()
        if trigger is None:
            raise RuntimeError("Task recovery ordering gaps are mutable")
        if conn.execute(
            "SELECT 1 FROM task_recovery_outbox AS predecessor "
            "WHERE predecessor.state IN ('pending', 'failed_attribution') "
            "AND EXISTS ("
            "SELECT 1 FROM task_recovery_outbox AS successor "
            "WHERE successor.job_id = predecessor.job_id "
            "AND successor.task_event_id > predecessor.task_event_id "
            "AND successor.state = 'projected'"
            ") LIMIT 1"
        ).fetchone():
            raise RuntimeError("Task recovery ordering gap is unresolved")
        if conn.execute(
            "SELECT 1 FROM task_recovery_outbox AS gap "
            "LEFT JOIN task_recovery_outbox AS successor "
            "ON successor.id = gap.ordering_successor_id "
            "WHERE gap.state = 'legacy_ordering_gap' AND ("
            "successor.id IS NULL OR successor.job_id != gap.job_id "
            "OR successor.task_event_id <= gap.task_event_id "
            "OR successor.state != 'projected'"
            ") LIMIT 1"
        ).fetchone():
            raise RuntimeError("Task recovery ordering gap link is invalid")
        if conn.execute(
            "SELECT 1 FROM task_recovery_corrections AS correction "
            "JOIN task_recovery_outbox AS successor "
            "ON successor.id = correction.successor_outbox_id "
            "LEFT JOIN ("
            "SELECT ordering_successor_id, job_id, COUNT(*) AS gap_count, "
            "MIN(task_event_id) AS first_task_event_id, "
            "MAX(task_event_id) AS last_task_event_id "
            "FROM task_recovery_outbox "
            "WHERE state = 'legacy_ordering_gap' "
            "GROUP BY ordering_successor_id, job_id"
            ") AS gaps "
            "ON gaps.ordering_successor_id = correction.successor_outbox_id "
            "AND gaps.job_id = correction.job_id "
            "WHERE successor.state != 'projected' "
            "OR successor.job_id != correction.job_id "
            "OR gaps.gap_count IS NULL "
            "OR gaps.gap_count != correction.gap_count "
            "OR gaps.first_task_event_id != correction.first_task_event_id "
            "OR gaps.last_task_event_id != correction.last_task_event_id "
            "LIMIT 1"
        ).fetchone():
            raise RuntimeError("Task recovery correction audit is invalid")
        if conn.execute(
            "SELECT 1 FROM task_recovery_outbox AS gap "
            "WHERE gap.state = 'legacy_ordering_gap' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM task_recovery_corrections AS correction "
            "WHERE correction.job_id = gap.job_id "
            "AND correction.successor_outbox_id "
            "= gap.ordering_successor_id"
            ") LIMIT 1"
        ).fetchone():
            raise RuntimeError("Task recovery correction intent is missing")
    job_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "projection_revision" not in job_columns or (
        state_generation and "projection_state" not in job_columns
    ):
        raise RuntimeError("Task projection revision is missing")
    if state_generation:
        legacy_triggers = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN ("
                "'jobs_projection_revision_update', "
                "'node_states_projection_revision_insert', "
                "'node_states_projection_revision_update', "
                "'node_states_projection_revision_delete'"
                ")"
            ).fetchall()
        }
        if legacy_triggers:
            raise RuntimeError("Task projection revision still follows raw progress")
        if conn.execute(
            "SELECT 1 FROM task_recovery_outbox "
            "WHERE state = 'superseded' LIMIT 1"
        ).fetchone():
            raise RuntimeError("Task recovery audit history was superseded")
    projection_indexes = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA index_list(master_projections)"
        ).fetchall()
    }
    if "uq_master_projections_source_type" in projection_indexes:
        raise RuntimeError("Task projection lifecycle history is unavailable")


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

    def safe_process_task_outbox(
        self,
        outbox_id: int,
    ) -> dict[str, Any] | None:
        try:
            return self.process_task_outbox(outbox_id)
        except Exception:
            log.exception(
                "Master Task projection outbox failed for row %s",
                outbox_id,
            )
            return None

    def safe_process_recovery_outbox(
        self,
        outbox_id: int,
    ) -> dict[str, Any] | None:
        try:
            return self.process_recovery_outbox(outbox_id)
        except Exception:
            log.exception(
                "Master recovery outbox failed for row %s",
                outbox_id,
            )
            return None

    def safe_process_recovery_correction(
        self,
        correction_id: int,
    ) -> dict[str, Any] | None:
        try:
            return self.process_recovery_correction(correction_id)
        except Exception:
            log.exception(
                "Master recovery correction failed for row %s",
                correction_id,
            )
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

    def _record_outbox_attempt(
        self,
        outbox_id: int,
        *,
        state: str,
        projection_id: int | None,
        failure_code: str | None,
    ) -> None:
        with self.app.state.db_lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    "UPDATE task_projection_outbox SET state = ?, "
                    "projection_id = ?, failure_code = ?, "
                    "attempt_count = attempt_count + 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND state != 'superseded'",
                    (
                        state,
                        projection_id,
                        failure_code,
                        outbox_id,
                    ),
                )
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _has_earlier_unresolved(
        conn: sqlite3.Connection,
        *,
        job_id: int,
        task_event_id: int,
    ) -> bool:
        return conn.execute(
            "SELECT 1 FROM ("
            "SELECT task_event_id FROM task_projection_outbox "
            "WHERE job_id = ? AND task_event_id < ? "
            "AND state IN ('pending', 'failed_attribution') "
            "UNION ALL "
            "SELECT task_event_id FROM task_recovery_outbox "
            "WHERE job_id = ? AND task_event_id < ? "
            "AND state IN ('pending', 'failed_attribution')"
            ") ORDER BY task_event_id LIMIT 1",
            (job_id, task_event_id, job_id, task_event_id),
        ).fetchone() is not None

    def process_task_outbox(
        self,
        outbox_id: int,
    ) -> dict[str, Any] | None:
        notify_sessions: set[int] = set()
        try:
            with self.app.state.db_lock:
                self.conn.execute("BEGIN IMMEDIATE")
                row = self.conn.execute(
                    "SELECT * FROM task_projection_outbox WHERE id = ?",
                    (outbox_id,),
                ).fetchone()
                if row is None or row["state"] == "superseded":
                    self.conn.execute("COMMIT")
                    return None
                if (
                    row["state"] == "projected"
                    and row["projection_id"] is not None
                ):
                    projection = self.conn.execute(
                        "SELECT * FROM master_projections WHERE id = ?",
                        (row["projection_id"],),
                    ).fetchone()
                    self.conn.execute("COMMIT")
                    return dict(projection) if projection else None
                if self._has_earlier_unresolved(
                    self.conn,
                    job_id=_as_int(row["job_id"]),
                    task_event_id=_as_int(row["task_event_id"]),
                ):
                    self.conn.execute("COMMIT")
                    return None
                projection = self._project_task(
                    _as_int(row["job_id"]),
                    connection=self.conn,
                    notify_sessions=notify_sessions,
                    status_override=str(row["task_status"]),
                    projection_epoch_override=_as_int(row["projection_epoch"]),
                    projection_revision_override=_as_int(
                        row["projection_revision"]
                    ),
                )
                if projection is None:
                    raise ProjectionAttributionError(
                        "projection_scope_unavailable"
                    )
                self.conn.execute(
                    "UPDATE task_projection_outbox SET state = 'projected', "
                    "projection_id = ?, failure_code = NULL, "
                    "attempt_count = attempt_count + 1, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (_as_int(projection["id"]), outbox_id),
                )
                self.conn.execute("COMMIT")
        except ProjectionAttributionError as exc:
            with self.app.state.db_lock:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self._record_outbox_attempt(
                outbox_id,
                state="failed_attribution",
                projection_id=None,
                failure_code=exc.code,
            )
            return None
        except Exception:
            with self.app.state.db_lock:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self._record_outbox_attempt(
                outbox_id,
                state="pending",
                projection_id=None,
                failure_code="projection_failed",
            )
            raise
        for session_id in notify_sessions:
            self.app.state.hub.notify(session_id)
        return projection

    def _record_recovery_attempt(
        self,
        outbox_id: int,
        *,
        state: str,
        failure_code: str | None,
    ) -> None:
        with self.app.state.db_lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    "UPDATE task_recovery_outbox SET state = ?, "
                    "failure_code = ?, attempt_count = attempt_count + 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND state != 'legacy_ordering_gap'",
                    (state, failure_code, outbox_id),
                )
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    def process_recovery_outbox(
        self,
        outbox_id: int,
    ) -> dict[str, Any] | None:
        result: dict[str, Any] | None = None
        try:
            with self.app.state.db_lock:
                self.conn.execute("BEGIN IMMEDIATE")
                row = self.conn.execute(
                    "SELECT * FROM task_recovery_outbox WHERE id = ?",
                    (outbox_id,),
                ).fetchone()
                if row is None or row["state"] == "legacy_ordering_gap":
                    self.conn.execute("COMMIT")
                    return None
                if (
                    row["state"] == "projected"
                    and row["message_id"] is not None
                    and row["event_id"] is not None
                ):
                    result = {
                        "session_id": row["master_session_id"],
                        "message_id": row["message_id"],
                        "event_id": row["event_id"],
                    }
                    self.conn.execute("COMMIT")
                    return result
                if self._has_earlier_unresolved(
                    self.conn,
                    job_id=_as_int(row["job_id"]),
                    task_event_id=_as_int(row["task_event_id"]),
                ):
                    self.conn.execute("COMMIT")
                    return None
                result = publish_master_recovery(
                    self.conn,
                    outbox=dict(row),
                )
                self.conn.execute(
                    "UPDATE task_recovery_outbox SET state = 'projected', "
                    "master_session_id = ?, message_id = ?, event_id = ?, "
                    "failure_code = NULL, "
                    "attempt_count = attempt_count + 1, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        result["session_id"],
                        result["message_id"],
                        result["event_id"],
                        outbox_id,
                    ),
                )
                self.conn.execute("COMMIT")
        except RecoveryAttributionError as exc:
            with self.app.state.db_lock:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self._record_recovery_attempt(
                outbox_id,
                state="failed_attribution",
                failure_code=exc.code,
            )
            return None
        except Exception:
            with self.app.state.db_lock:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self._record_recovery_attempt(
                outbox_id,
                state="pending",
                failure_code="projection_failed",
            )
            raise
        assert result is not None
        self.app.state.hub.notify(_as_int(result["session_id"]))
        return result

    def _record_recovery_correction_attempt(
        self,
        correction_id: int,
        *,
        state: str,
        failure_code: str | None,
    ) -> None:
        with self.app.state.db_lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    "UPDATE task_recovery_corrections SET state = ?, "
                    "failure_code = ?, attempt_count = attempt_count + 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (state, failure_code, correction_id),
                )
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _recovery_correction_ready(
        conn: sqlite3.Connection,
        *,
        job_id: int,
    ) -> bool:
        if conn.execute(
            "SELECT 1 FROM ("
            "SELECT id FROM task_projection_outbox "
            "WHERE job_id = ? "
            "AND state IN ('pending', 'failed_attribution') "
            "UNION ALL "
            "SELECT id FROM task_recovery_outbox "
            "WHERE job_id = ? "
            "AND state IN ('pending', 'failed_attribution')"
            ") LIMIT 1",
            (job_id, job_id),
        ).fetchone():
            return False
        job = conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if job is None:
            return False
        status = str(
            canonical_job_payload(
                dict(job),
                connection=conn,
            )["run_projection"]["status"]
        )
        expected_state = task_projection_state(
            status,
            blocked_reason=job["blocked_reason"],
        )
        if str(job["projection_state"]) != expected_state:
            return False
        if expected_state == "none":
            return True
        revision = _as_int(job["projection_revision"])
        if revision > 0:
            projection_key = (
                f"task:{job_id}:revision:{revision}:{expected_state}"
            )
            return conn.execute(
                "SELECT 1 FROM master_projections "
                "WHERE source_table = 'jobs' AND source_id = ? "
                "AND projection_key = ?",
                (job_id, projection_key),
            ).fetchone() is not None
        projection_type = {
            "started": "master.task.started",
            "review": "master.task.review_ready",
            "completed": "master.task.completed",
            "failed": "master.task.failed",
            "cancelled": "master.task.cancelled",
            "blocked": "master.task.blocked",
        }[expected_state]
        return conn.execute(
            "SELECT 1 FROM master_projections "
            "WHERE source_table = 'jobs' AND source_id = ? "
            "AND projection_type = ?",
            (job_id, projection_type),
        ).fetchone() is not None

    def process_recovery_correction(
        self,
        correction_id: int,
    ) -> dict[str, Any] | None:
        result: dict[str, Any] | None = None
        try:
            with self.app.state.db_lock:
                self.conn.execute("BEGIN IMMEDIATE")
                row = self.conn.execute(
                    "SELECT * FROM task_recovery_corrections WHERE id = ?",
                    (correction_id,),
                ).fetchone()
                if row is None:
                    self.conn.execute("COMMIT")
                    return None
                if (
                    row["state"] == "projected"
                    and row["message_id"] is not None
                    and row["event_id"] is not None
                ):
                    result = {
                        "session_id": row["master_session_id"],
                        "message_id": row["message_id"],
                        "event_id": row["event_id"],
                    }
                    self.conn.execute("COMMIT")
                    return result
                if not self._recovery_correction_ready(
                    self.conn,
                    job_id=_as_int(row["job_id"]),
                ):
                    self.conn.execute("COMMIT")
                    return None
                result = publish_master_recovery_correction(
                    self.conn,
                    correction=dict(row),
                )
                self.conn.execute(
                    "UPDATE task_recovery_corrections "
                    "SET state = 'projected', master_session_id = ?, "
                    "message_id = ?, event_id = ?, failure_code = NULL, "
                    "attempt_count = attempt_count + 1, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        result["session_id"],
                        result["message_id"],
                        result["event_id"],
                        correction_id,
                    ),
                )
                self.conn.execute("COMMIT")
        except RecoveryAttributionError as exc:
            with self.app.state.db_lock:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self._record_recovery_correction_attempt(
                correction_id,
                state="failed_attribution",
                failure_code=exc.code,
            )
            return None
        except Exception:
            with self.app.state.db_lock:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self._record_recovery_correction_attempt(
                correction_id,
                state="pending",
                failure_code="projection_failed",
            )
            raise
        assert result is not None
        self.app.state.hub.notify(_as_int(result["session_id"]))
        return result

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
        connection: sqlite3.Connection | None = None,
        notify_sessions: set[int] | None = None,
    ) -> dict[str, Any]:
        if projection_type not in MASTER_PROJECTION_EVENT_TYPES:
            raise ValueError(f"unknown Master projection type {projection_type!r}")
        if source_table != _source_table_for(projection_type):
            raise ValueError(
                f"{projection_type!r} cannot project {source_table!r}"
            )
        conn = connection or self.conn
        owns_transaction = not conn.in_transaction
        if not owns_transaction and notify_sessions is None:
            raise ValueError(
                "transactional Master projection requires deferred notifications"
            )
        created = False
        event_id: int | None = None
        with self.app.state.db_lock:
            if owns_transaction:
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
                encode_bounded_event_payload(
                    payload,
                    max_bytes=MAX_PROJECTION_PAYLOAD_BYTES,
                )
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
                    if owns_transaction:
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
                        raise ProjectionAttributionError(
                            "focus_attribution_unavailable"
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
                        raise ProjectionAttributionError(
                            "focus_attribution_unavailable"
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
                        raise ProjectionAttributionError(
                            "focus_attribution_unavailable"
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
                        encode_bounded_event_payload(
                            event_payload,
                            max_bytes=MAX_PROJECTION_PAYLOAD_BYTES,
                        ),
                    ),
                )
                event_id = _as_int(event_cursor.lastrowid)
                event_payload["event_id"] = event_id
                final_payload_json = encode_bounded_event_payload(
                    event_payload,
                    max_bytes=MAX_PROJECTION_PAYLOAD_BYTES,
                )
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
                if owns_transaction:
                    conn.execute("COMMIT")
                created = True
            except Exception:
                if owns_transaction and conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if created and event_id is not None:
            if owns_transaction:
                self.app.state.hub.notify(master_session_id)
            else:
                assert notify_sessions is not None
                notify_sessions.add(master_session_id)
        row = conn.execute(
            "SELECT * FROM master_projections WHERE owner_user_id = ? "
            "AND projection_key = ?",
            (owner_user_id, projection_key),
        ).fetchone()
        return dict(row) if row else {}

    def _task(
        self,
        job_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> sqlite3.Row | None:
        conn = connection or self.conn
        return conn.execute(
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

    def _next_unresolved_outbox(
        self,
        job_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> sqlite3.Row | None:
        conn = connection or self.conn
        return conn.execute(
            "SELECT kind, id, state FROM ("
            "SELECT 'status' AS kind, id, state, task_event_id "
            "FROM task_projection_outbox "
            "WHERE job_id = ? "
            "AND state IN ('pending', 'failed_attribution') "
            "UNION ALL "
            "SELECT 'recovery' AS kind, id, state, task_event_id "
            "FROM task_recovery_outbox "
            "WHERE job_id = ? "
            "AND state IN ('pending', 'failed_attribution')"
            ") ORDER BY task_event_id LIMIT 1",
            (job_id, job_id),
        ).fetchone()

    def project_task(self, job_id: int) -> dict[str, Any] | None:
        notify_sessions: set[int] = set()
        with self.app.state.db_lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if self._next_unresolved_outbox(
                    job_id,
                    connection=self.conn,
                ) is not None:
                    self.conn.execute("COMMIT")
                    return None
                job = self._task(job_id, connection=self.conn)
                if job is None:
                    self.conn.execute("COMMIT")
                    return None
                status = str(
                    canonical_job_payload(
                        dict(job),
                        connection=self.conn,
                    )["run_projection"]["status"]
                )
                revision, projection_state, _changed = (
                    claim_task_projection_generation(
                        self.conn,
                        job_id=job_id,
                        status=status,
                        blocked_reason=job["blocked_reason"],
                    )
                )
                projection = self._project_task(
                    job_id,
                    connection=self.conn,
                    notify_sessions=notify_sessions,
                    status_override=status,
                    projection_revision_override=revision,
                    projection_state_override=projection_state,
                )
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        for session_id in notify_sessions:
            self.app.state.hub.notify(session_id)
        return projection

    def _project_task(
        self,
        job_id: int,
        *,
        connection: sqlite3.Connection | None = None,
        notify_sessions: set[int] | None = None,
        status_override: str | None = None,
        projection_epoch_override: int | None = None,
        projection_revision_override: int | None = None,
        projection_state_override: str | None = None,
    ) -> dict[str, Any] | None:
        conn = connection or self.conn
        job = self._task(job_id, connection=conn)
        if job is None:
            return None
        status = (
            str(status_override)
            if status_override is not None
            else str(
                canonical_job_payload(
                    dict(job),
                    connection=conn,
                )["run_projection"]["status"]
            )
        )
        if status not in {
            "queued",
            "running",
            "review",
            "done",
            "failed",
            "cancelled",
        }:
            raise ValueError("Task projection status is invalid")
        projection_epoch = (
            _as_int(projection_epoch_override)
            if projection_epoch_override is not None
            else task_projection_epoch(
                conn,
                job_id=job_id,
            )
        )
        if projection_epoch < 0:
            raise ValueError("Task projection epoch is invalid")
        projection_revision = (
            _as_int(projection_revision_override)
            if projection_revision_override is not None
            else _as_int(job["projection_revision"])
        )
        if projection_revision < 0:
            raise ValueError("Task projection revision is invalid")
        reason = str(job["blocked_reason"] or "").strip()
        projection_state = (
            str(projection_state_override)
            if projection_state_override is not None
            else task_projection_state(
                status,
                blocked_reason=reason,
                queued_is_blocked=(
                    status_override is not None and status == "queued"
                ),
            )
        )
        projected_blocker = projection_state == "blocked"
        projection_type: str
        verb: str
        if projection_state == "started":
            projection_type = "master.task.started"
            verb = "Started"
        elif projection_state == "completed":
            projection_type = "master.task.completed"
            verb = "Completed"
        elif projection_state == "failed":
            projection_type = "master.task.failed"
            verb = "Failed"
        elif projection_state == "cancelled":
            projection_type = "master.task.cancelled"
            verb = "Cancelled"
        elif projection_state == "review":
            projection_type = "master.task.review_ready"
            verb = "Ready for review"
        elif projected_blocker:
            projection_type = "master.task.blocked"
            verb = "Blocked"
        else:
            return None

        checkpoint = None
        if status == "review":
            checkpoint = conn.execute(
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

        projection_key = _task_projection_key(
            job_id,
            projection_state,
            projection_epoch,
            projection_revision,
        )
        payload = {
            "task_id": job_id,
            "task_status": status,
            "container_id": job["project_id"],
            "container_slug": job["container_slug"],
            "area_id": job["target_area_id"],
            "area_kind": job["area_kind"],
            "checkpoint_id": checkpoint["id"] if checkpoint else None,
            "attention_required": (
                status == "review" or projected_blocker or bool(reason)
            ),
            "toast_key": projection_key,
        }
        return self._insert(
            owner_user_id=_as_int(job["master_owner_user_id"]),
            master_session_id=_as_int(job["origin_master_session_id"]),
            projection_key=projection_key,
            projection_type=projection_type,
            source_table="jobs",
            source_id=job_id,
            task_id=job_id,
            content=content,
            payload=payload,
            connection=conn,
            notify_sessions=notify_sessions,
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
        outbox_rows = self.conn.execute(
            "SELECT kind, id FROM ("
            "SELECT 'status' AS kind, outbox.id, outbox.task_event_id "
            "FROM task_projection_outbox AS outbox "
            "LEFT JOIN task_delegations AS delegation "
            "ON delegation.job_id = outbox.job_id "
            "WHERE outbox.state = 'pending' OR ("
            "outbox.state = 'failed_attribution' "
            "AND outbox.failure_code = 'focus_attribution_unavailable' "
            "AND delegation.origin_focus_captured = 1"
            ") UNION ALL "
            "SELECT 'recovery' AS kind, outbox.id, outbox.task_event_id "
            "FROM task_recovery_outbox AS outbox "
            "LEFT JOIN task_delegations AS delegation "
            "ON delegation.job_id = outbox.job_id "
            "WHERE outbox.state = 'pending' OR ("
            "outbox.state = 'failed_attribution' "
            "AND outbox.failure_code = 'focus_attribution_unavailable' "
            "AND delegation.origin_focus_captured = 1"
            ")) ORDER BY task_event_id"
        ).fetchall()
        for row in outbox_rows:
            if row["kind"] == "status":
                self.safe_process_task_outbox(_as_int(row["id"]))
            else:
                self.safe_process_recovery_outbox(_as_int(row["id"]))
        effective_status = effective_job_status_sql("j")
        jobs = self.conn.execute(
            "WITH effective AS ("
            "  SELECT j.*, "
            f"    {effective_status} AS effective_status, "
            "    COALESCE(("
            "      SELECT MAX(event.id) FROM events AS event "
            "      WHERE event.session_id = j.session_id "
            "      AND event.type = 'job.update' "
            "      AND json_extract(event.payload, '$.job_id') = j.id "
            "      AND json_extract(event.payload, '$.mutation') "
            "        = 'checkpoint_restored'"
            "    ), 0) AS projection_epoch "
            "  FROM jobs j"
            "), candidate AS ("
            "  SELECT j.id AS source_id, "
            "    COALESCE(j.updated_at, j.created_at) AS ordering, "
            "    j.projection_epoch, "
            "    j.projection_revision, "
            "    CASE "
            "      WHEN j.effective_status = 'running' "
            "        THEN 'master.task.started' "
            "      WHEN j.effective_status = 'done' "
            "        THEN 'master.task.completed' "
            "      WHEN j.effective_status = 'failed' "
            "        THEN 'master.task.failed' "
            "      WHEN j.effective_status = 'cancelled' "
            "        THEN 'master.task.cancelled' "
            "      WHEN j.effective_status = 'review' "
            "        THEN 'master.task.review_ready' "
            "      WHEN j.effective_status = 'queued' "
            "        AND j.blocked_reason LIKE 'Blocked by prerequisite%' "
            "        THEN 'master.task.blocked' "
            "      ELSE NULL "
            "    END AS expected_type, "
            "    CASE "
            "      WHEN j.effective_status = 'running' THEN 'started' "
            "      WHEN j.effective_status = 'done' THEN 'completed' "
            "      WHEN j.effective_status = 'failed' THEN 'failed' "
            "      WHEN j.effective_status = 'cancelled' THEN 'cancelled' "
            "      WHEN j.effective_status = 'review' THEN 'review' "
            "      WHEN j.effective_status = 'queued' "
            "        AND j.blocked_reason LIKE 'Blocked by prerequisite%' "
            "        THEN 'blocked' "
            "      ELSE NULL "
            "    END AS expected_suffix "
            "  FROM effective j JOIN sessions ms "
            "    ON ms.id = j.origin_master_session_id "
            "  WHERE ms.mode = 'master' AND j.created_by = ms.owner_user_id"
            ") "
            "SELECT source_id FROM candidate c "
            "WHERE c.expected_type IS NOT NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM master_projections mp "
            "  WHERE mp.source_table = 'jobs' "
            "  AND mp.source_id = c.source_id "
            "  AND mp.projection_key = CASE "
            "    WHEN c.projection_revision > 0 THEN "
            "      'task:' || c.source_id || ':revision:' "
            "      || c.projection_revision || ':' || c.expected_suffix "
            "    WHEN c.projection_epoch > 0 THEN "
            "      'task:' || c.source_id || ':epoch:' "
            "      || c.projection_epoch || ':' || c.expected_suffix "
            "    ELSE 'task:' || c.source_id || ':' || c.expected_suffix "
            "  END"
            ") "
            "ORDER BY c.ordering, c.source_id"
        ).fetchall()
        for row in jobs:
            self.safe_project_task(_as_int(row["source_id"]))
        correction_rows = self.conn.execute(
            "SELECT correction.id "
            "FROM task_recovery_corrections AS correction "
            "WHERE correction.state = 'pending' OR ("
            "correction.state = 'failed_attribution' "
            "AND correction.failure_code = 'focus_attribution_unavailable'"
            ") ORDER BY correction.id"
        ).fetchall()
        for row in correction_rows:
            self.safe_process_recovery_correction(_as_int(row["id"]))
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
            "observed": (
                len(outbox_rows)
                + len(jobs)
                + len(correction_rows)
                + len(interventions)
                + len(attentions)
            ),
            "created": _as_int(after) - _as_int(before),
        }

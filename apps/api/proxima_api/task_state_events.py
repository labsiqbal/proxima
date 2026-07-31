"""Durable invalidation events for Task state changed outside its worker run."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping

from . import master_focus
from .event_payloads import encode_bounded_event_payload
from .master_persistence import canonical_job_payload


_ACTOR_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_DISCARDED_LABELS = (
    re.compile(r"^[1-9][0-9]* runs? created after the checkpoint$"),
    re.compile(r"^Task step progress changed after the checkpoint$"),
    re.compile(
        r"^[1-9][0-9]* Recipe node progress records? changed after the checkpoint$"
    ),
    re.compile(r"^[1-9][0-9]* Task worktrees? reset to the checkpoint$"),
)
_CONFLICT_LABEL = re.compile(
    r"^Task #[1-9][0-9]* \((queued|running|review|done|failed|cancelled)\)$"
)
_JOB_STATUSES = {"queued", "running", "review", "done", "failed", "cancelled"}
_MAX_RECOVERY_ITEMS = 4
_PROJECTABLE_TASK_STATUSES = {
    "running",
    "review",
    "done",
    "failed",
    "cancelled",
}
_PROJECTION_STATES = {
    "none",
    "started",
    "review",
    "completed",
    "failed",
    "cancelled",
    "blocked",
}


class RecoveryAttributionError(ValueError):
    def __init__(self, code: str):
        if code not in {
            "focus_attribution_unavailable",
            "projection_scope_unavailable",
        }:
            raise ValueError("invalid recovery attribution failure")
        self.code = code
        super().__init__(code.replace("_", " "))


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer identifier")
    return int(value)


def _next_session_seq(conn: sqlite3.Connection, session_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM events "
        "WHERE session_id = ? AND run_id IS NULL",
        (session_id,),
    ).fetchone()
    return _as_int(row["seq"])


def task_projection_epoch(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    session_id: int | None = None,
    through_event_id: int | None = None,
) -> int:
    if session_id is None:
        job = conn.execute(
            "SELECT session_id FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if job is None or job["session_id"] is None:
            return 0
        session_id = _as_int(job["session_id"])
    clauses = [
        "session_id = ?",
        "type = 'job.update'",
        "json_extract(payload, '$.job_id') = ?",
        "json_extract(payload, '$.mutation') = 'checkpoint_restored'",
    ]
    params: list[int] = [session_id, job_id]
    if through_event_id is not None:
        clauses.append("id <= ?")
        params.append(through_event_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS projection_epoch FROM events WHERE "
        + " AND ".join(clauses),
        tuple(params),
    ).fetchone()
    return _as_int(row["projection_epoch"])


def task_projection_state(
    status: str,
    *,
    blocked_reason: Any = None,
    queued_is_blocked: bool = False,
) -> str:
    normalized = str(status).lower()
    if normalized == "running":
        return "started"
    if normalized == "review":
        return "review"
    if normalized == "done":
        return "completed"
    if normalized == "failed":
        return "failed"
    if normalized == "cancelled":
        return "cancelled"
    if normalized == "queued" and (
        queued_is_blocked
        or str(blocked_reason or "").startswith("Blocked by prerequisite")
    ):
        return "blocked"
    if normalized == "queued":
        return "none"
    raise ValueError("Task projection status is invalid")


def claim_task_projection_generation(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    status: str,
    blocked_reason: Any = None,
    queued_is_blocked: bool = False,
) -> tuple[int, str, bool]:
    state = task_projection_state(
        status,
        blocked_reason=blocked_reason,
        queued_is_blocked=queued_is_blocked,
    )
    if state not in _PROJECTION_STATES:
        raise ValueError("Task projection state is invalid")
    row = conn.execute(
        "SELECT projection_revision, projection_state FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Task is unavailable")
    revision = _as_int(row["projection_revision"])
    if revision < 0:
        raise ValueError("Task projection revision is invalid")
    if str(row["projection_state"]) == state:
        return revision, state, False
    revision += 1
    changed = conn.execute(
        "UPDATE jobs SET projection_revision = ?, projection_state = ? "
        "WHERE id = ? AND projection_revision = ? AND projection_state = ?",
        (
            revision,
            state,
            job_id,
            _as_int(row["projection_revision"]),
            str(row["projection_state"]),
        ),
    )
    if changed.rowcount != 1:
        raise RuntimeError("Task projection generation changed concurrently")
    return revision, state, True


def append_task_update(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    mutation: str,
    checkpoint_id: int | None = None,
) -> dict[str, int]:
    """Append the shared Task-session invalidation in the caller's transaction."""
    job = conn.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job is None or job["session_id"] is None:
        raise ValueError("Task session is unavailable")
    payload: dict[str, Any] = {
        "job_id": job_id,
        "status": str(job["status"]),
        "mutation": mutation,
    }
    if checkpoint_id is not None:
        payload["checkpoint_id"] = checkpoint_id
    session_id = _as_int(job["session_id"])
    cursor = conn.execute(
        "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) "
        "VALUES (NULL, ?, ?, ?, 'job.update', ?)",
        (
            session_id,
            job["project_id"],
            _next_session_seq(conn, session_id),
            encode_bounded_event_payload(payload),
        ),
    )
    event_id = _as_int(cursor.lastrowid)
    result = {"session_id": session_id, "event_id": event_id}
    if job["origin_master_session_id"] is not None:
        task_status = str(
            canonical_job_payload(
                dict(job),
                connection=conn,
            )["run_projection"]["status"]
        )
        projection_revision, projection_state, changed = (
            claim_task_projection_generation(
                conn,
                job_id=job_id,
                status=task_status,
                blocked_reason=job["blocked_reason"],
            )
        )
        projectable = (
            task_status in _PROJECTABLE_TASK_STATUSES
            or projection_state == "blocked"
        )
        if projectable and changed:
            outbox = conn.execute(
                "INSERT INTO task_projection_outbox("
                "job_id, task_event_id, projection_epoch, projection_revision, "
                "task_status, mutation"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    event_id,
                    task_projection_epoch(
                        conn,
                        job_id=job_id,
                        session_id=session_id,
                        through_event_id=event_id,
                    ),
                    projection_revision,
                    task_status,
                    mutation,
                ),
            )
            result["projection_outbox_id"] = _as_int(outbox.lastrowid)
    return result


def _safe_recovery_items(
    value: Any,
    *,
    patterns: tuple[re.Pattern[str], ...],
    fallback: str,
) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    rejected = 0
    for item in value[:_MAX_RECOVERY_ITEMS]:
        text = str(item)
        if len(text) <= 160 and any(pattern.fullmatch(text) for pattern in patterns):
            result.append(text)
        else:
            rejected += 1
    omitted = max(0, len(value) - _MAX_RECOVERY_ITEMS)
    if rejected:
        result.append(f"{rejected} {fallback}")
    if omitted:
        result.append(f"{omitted} additional recovery records omitted")
    return result


def _bounded_recovery(recovery: dict[str, Any]) -> dict[str, Any]:
    actor = recovery.get("actor")
    actor_id = actor.get("id") if isinstance(actor, dict) else None
    try:
        actor_id = _as_int(actor_id) if actor_id is not None else None
    except (TypeError, ValueError, OverflowError):
        actor_id = None
    username = str(actor.get("username") or "") if isinstance(actor, dict) else ""
    if not _ACTOR_LABEL.fullmatch(username):
        username = "local-owner"

    prior_status = str(recovery.get("prior_status") or "").lower()
    restored_status = str(recovery.get("restored_status") or "").lower()
    if prior_status not in _JOB_STATUSES or restored_status not in _JOB_STATUSES:
        raise ValueError("checkpoint recovery status is invalid")
    return {
        "job_id": _as_int(recovery["job_id"]),
        "checkpoint_id": _as_int(recovery["checkpoint_id"]),
        "actor": {"id": actor_id, "username": username},
        "prior_status": prior_status,
        "restored_status": restored_status,
        "discarded_progress": _safe_recovery_items(
            recovery.get("discarded_progress"),
            patterns=_DISCARDED_LABELS,
            fallback="additional discarded progress records",
        ),
        "conflicting_progress": _safe_recovery_items(
            recovery.get("conflicting_progress"),
            patterns=(_CONFLICT_LABEL,),
            fallback="additional conflicting progress records",
        ),
    }


def _recovery_content(recovery: dict[str, Any]) -> str:
    actor = str(recovery["actor"]["username"])
    task_id = _as_int(recovery["job_id"])
    checkpoint_id = _as_int(recovery["checkpoint_id"])
    prior = str(recovery["prior_status"]).capitalize()
    restored = str(recovery["restored_status"]).capitalize()
    discarded = recovery["discarded_progress"]
    conflicting = recovery["conflicting_progress"]
    discarded_text = (
        "No later progress was discarded."
        if not discarded
        else "Discarded progress: " + "; ".join(str(item) for item in discarded) + "."
    )
    conflict_text = (
        "No conflicting progress was present."
        if not conflicting
        else "Conflicting progress: "
        + "; ".join(str(item) for item in conflicting)
        + "."
    )
    return (
        f"{actor} restored Task #{task_id} from checkpoint #{checkpoint_id}: "
        f"{prior} to {restored}. {discarded_text} {conflict_text}"
    )


def _recovery_scope(
    conn: sqlite3.Connection,
    *,
    job_id: int,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT j.origin_master_session_id, j.project_id, j.target_area_id, "
        "p.slug AS container_slug, d.origin_focus_epoch_id, "
        "d.origin_focus_captured, e.master_session_id AS epoch_session_id "
        "FROM jobs j "
        "LEFT JOIN projects p ON p.id = j.project_id "
        "LEFT JOIN task_delegations d ON d.job_id = j.id "
        "LEFT JOIN master_focus_epochs e ON e.id = d.origin_focus_epoch_id "
        "WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if row is None or row["origin_master_session_id"] is None:
        raise RecoveryAttributionError("projection_scope_unavailable")
    master_session_id = _as_int(row["origin_master_session_id"])
    if not row["origin_focus_captured"] or (
        row["origin_focus_epoch_id"] is not None
        and row["epoch_session_id"] != master_session_id
    ):
        raise RecoveryAttributionError("focus_attribution_unavailable")
    return row


def enqueue_master_recovery(
    conn: sqlite3.Connection,
    *,
    recovery: dict[str, Any],
    task_event_id: int,
) -> dict[str, Any] | None:
    safe_recovery = _bounded_recovery(recovery)
    job_id = _as_int(safe_recovery["job_id"])
    job = conn.execute(
        "SELECT origin_master_session_id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job is None or job["origin_master_session_id"] is None:
        return None
    failure_code = None
    state = "pending"
    try:
        _recovery_scope(conn, job_id=job_id)
    except RecoveryAttributionError as exc:
        state = "failed_attribution"
        failure_code = exc.code
    conn.execute(
        "UPDATE task_projection_outbox SET state = 'superseded', "
        "projection_id = NULL, superseded_by_event_id = ?, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE job_id = ? AND task_event_id <= ? "
        "AND state IN ('pending', 'failed_attribution')",
        (task_event_id, job_id, task_event_id),
    )
    cursor = conn.execute(
        "INSERT INTO task_recovery_outbox("
        "job_id, task_event_id, recovery_json, state, "
        "master_session_id, failure_code"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            job_id,
            task_event_id,
            encode_bounded_event_payload(safe_recovery),
            state,
            _as_int(job["origin_master_session_id"]),
            failure_code,
        ),
    )
    return {
        "id": _as_int(cursor.lastrowid),
        "session_id": _as_int(job["origin_master_session_id"]),
        "state": state,
        "failure_code": failure_code,
    }


def publish_master_recovery(
    conn: sqlite3.Connection,
    *,
    outbox: Mapping[str, Any],
) -> dict[str, int]:
    safe_recovery = _bounded_recovery(
        json.loads(str(outbox["recovery_json"]))
    )
    job_id = _as_int(safe_recovery["job_id"])
    row = _recovery_scope(conn, job_id=job_id)
    master_session_id = _as_int(row["origin_master_session_id"])

    content = _recovery_content(safe_recovery)
    message = conn.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'assistant', ?, 'Master')",
        (master_session_id, content),
    )
    message_id = _as_int(message.lastrowid)
    master_focus.stamp_message(
        conn,
        message_id=message_id,
        focus_epoch_id=row["origin_focus_epoch_id"],
        subject_container_id=row["project_id"],
    )
    focus_container_id = None
    if row["origin_focus_epoch_id"] is not None:
        epoch = conn.execute(
            "SELECT container_id FROM master_focus_epochs WHERE id = ?",
            (row["origin_focus_epoch_id"],),
        ).fetchone()
        if epoch is None:
            raise ValueError("recovery Master Focus origin is unavailable")
        focus_container_id = epoch["container_id"]
    payload = {
        "message_id": message_id,
        "task_id": job_id,
        "task_status": safe_recovery["restored_status"],
        "container_id": row["project_id"],
        "container_slug": row["container_slug"],
        "area_id": row["target_area_id"],
        "checkpoint_id": safe_recovery["checkpoint_id"],
        "actor": safe_recovery["actor"],
        "prior_status": safe_recovery["prior_status"],
        "restored_status": safe_recovery["restored_status"],
        "discarded_progress": safe_recovery["discarded_progress"],
        "conflicting_progress": safe_recovery["conflicting_progress"],
        "focus_epoch_id": row["origin_focus_epoch_id"],
        "focus_container_id": focus_container_id,
        "subject_container_id": row["project_id"],
    }
    event = conn.execute(
        "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) "
        "VALUES (NULL, ?, ?, ?, 'master.task.recovered', ?)",
        (
            master_session_id,
            row["project_id"],
            _next_session_seq(conn, master_session_id),
            encode_bounded_event_payload(payload),
        ),
    )
    event_id = _as_int(event.lastrowid)
    conn.execute(
        "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (master_session_id,),
    )
    return {
        "session_id": master_session_id,
        "event_id": event_id,
        "message_id": message_id,
    }


def publish_master_recovery_correction(
    conn: sqlite3.Connection,
    *,
    correction: Mapping[str, Any],
) -> dict[str, int]:
    correction_id = _as_int(correction["id"])
    row = conn.execute(
        "SELECT c.*, successor.task_event_id AS successor_task_event_id, "
        "successor.master_session_id AS successor_master_session_id, "
        "successor.message_id AS successor_message_id, "
        "successor.event_id AS successor_event_id, "
        "event.session_id AS successor_event_session_id, "
        "event.type AS successor_event_type, "
        "event.payload AS successor_event_payload "
        "FROM task_recovery_corrections AS c "
        "JOIN task_recovery_outbox AS successor "
        "ON successor.id = c.successor_outbox_id "
        "JOIN events AS event ON event.id = successor.event_id "
        "WHERE c.id = ? AND successor.state = 'projected'",
        (correction_id,),
    ).fetchone()
    if row is None:
        raise RecoveryAttributionError("projection_scope_unavailable")
    master_session_id = _as_int(row["successor_master_session_id"])
    if _as_int(row["successor_event_session_id"]) != master_session_id or str(
        row["successor_event_type"]
    ) != "master.task.recovered":
        raise RecoveryAttributionError("projection_scope_unavailable")
    try:
        successor_payload = json.loads(str(row["successor_event_payload"]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RecoveryAttributionError(
            "projection_scope_unavailable"
        ) from exc
    if not isinstance(successor_payload, dict):
        raise RecoveryAttributionError("projection_scope_unavailable")
    try:
        successor_task_id = _as_int(successor_payload.get("task_id"))
        successor_message_id = _as_int(
            successor_payload.get("message_id")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecoveryAttributionError(
            "projection_scope_unavailable"
        ) from exc
    if (
        successor_task_id != _as_int(row["job_id"])
        or successor_message_id != _as_int(row["successor_message_id"])
    ):
        raise RecoveryAttributionError("projection_scope_unavailable")
    focus_epoch_id = successor_payload.get("focus_epoch_id")
    subject_container_id = successor_payload.get("subject_container_id")
    focus_container_id = successor_payload.get("focus_container_id")
    try:
        focus_epoch_id = (
            None if focus_epoch_id is None else _as_int(focus_epoch_id)
        )
        subject_container_id = (
            None
            if subject_container_id is None
            else _as_int(subject_container_id)
        )
        focus_container_id = (
            None
            if focus_container_id is None
            else _as_int(focus_container_id)
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecoveryAttributionError(
            "focus_attribution_unavailable"
        ) from exc
    if focus_epoch_id is None:
        if focus_container_id is not None:
            raise RecoveryAttributionError("focus_attribution_unavailable")
    else:
        epoch = conn.execute(
            "SELECT master_session_id, container_id "
            "FROM master_focus_epochs WHERE id = ?",
            (focus_epoch_id,),
        ).fetchone()
        if epoch is None or (
            _as_int(epoch["master_session_id"]) != master_session_id
            or _as_int(epoch["container_id"]) != focus_container_id
        ):
            raise RecoveryAttributionError("focus_attribution_unavailable")
    job_id = _as_int(row["job_id"])
    gap_count = _as_int(row["gap_count"])
    first_task_event_id = _as_int(row["first_task_event_id"])
    last_task_event_id = _as_int(row["last_task_event_id"])
    first_successor_task_event_id = _as_int(
        row["first_successor_task_event_id"]
    )
    last_successor_task_event_id = _as_int(
        row["last_successor_task_event_id"]
    )
    successor_task_event_id = _as_int(row["successor_task_event_id"])
    noun = "audit" if gap_count == 1 else "audits"
    pronoun = "It was" if gap_count == 1 else "They were"
    gap_label = (
        "a legacy ordering gap"
        if gap_count == 1
        else "legacy ordering gaps"
    )
    content = (
        f"Retained {gap_count} checkpoint recovery {noun} for Task "
        f"#{job_id} as {gap_label} across Task events "
        f"#{first_task_event_id}-#{last_task_event_id} and successor "
        f"events #{first_successor_task_event_id}-"
        f"#{last_successor_task_event_id}. {pronoun} contained without "
        "replaying older history after a later publication."
    )
    message = conn.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'assistant', ?, 'Master')",
        (master_session_id, content),
    )
    message_id = _as_int(message.lastrowid)
    master_focus.stamp_message(
        conn,
        message_id=message_id,
        focus_epoch_id=focus_epoch_id,
        subject_container_id=subject_container_id,
    )
    job = conn.execute(
        "SELECT project_id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        raise RecoveryAttributionError("projection_scope_unavailable")
    payload = {
        "message_id": message_id,
        "task_id": job_id,
        "gap_count": gap_count,
        "first_task_event_id": first_task_event_id,
        "last_task_event_id": last_task_event_id,
        "successor_task_event_id": successor_task_event_id,
        "first_successor_task_event_id": first_successor_task_event_id,
        "last_successor_task_event_id": last_successor_task_event_id,
        "focus_epoch_id": focus_epoch_id,
        "focus_container_id": focus_container_id,
        "subject_container_id": subject_container_id,
    }
    event = conn.execute(
        "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) "
        "VALUES (NULL, ?, ?, ?, "
        "'master.task.recovery_history_corrected', ?)",
        (
            master_session_id,
            job["project_id"],
            _next_session_seq(conn, master_session_id),
            encode_bounded_event_payload(payload),
        ),
    )
    event_id = _as_int(event.lastrowid)
    conn.execute(
        "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (master_session_id,),
    )
    return {
        "session_id": master_session_id,
        "event_id": event_id,
        "message_id": message_id,
    }

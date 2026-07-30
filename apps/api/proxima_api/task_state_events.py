"""Durable invalidation events for Task state changed outside its worker run."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

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
        projectable = task_status in _PROJECTABLE_TASK_STATUSES or (
            task_status == "queued"
            and str(job["blocked_reason"] or "").startswith("Blocked by prerequisite")
        )
        if projectable:
            outbox = conn.execute(
                "INSERT INTO task_projection_outbox("
                "job_id, task_event_id, projection_epoch, task_status, mutation"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    event_id,
                    task_projection_epoch(
                        conn,
                        job_id=job_id,
                        session_id=session_id,
                        through_event_id=event_id,
                    ),
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


def append_master_recovery(
    conn: sqlite3.Connection,
    *,
    recovery: dict[str, Any],
) -> dict[str, int] | None:
    """Append one human-readable Master recovery message and SSE event."""
    safe_recovery = _bounded_recovery(recovery)
    job_id = _as_int(safe_recovery["job_id"])
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
        return None
    master_session_id = _as_int(row["origin_master_session_id"])
    if not row["origin_focus_captured"] or (
            row["origin_focus_epoch_id"] is not None
            and row["epoch_session_id"] != master_session_id
    ):
        raise ValueError("recovery Master Focus origin is unavailable")

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

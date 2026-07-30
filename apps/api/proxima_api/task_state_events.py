"""Durable invalidation events for Task state changed outside its worker run."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import master_focus


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


def append_task_update(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    mutation: str,
    checkpoint_id: int | None = None,
) -> dict[str, int]:
    """Append the shared Task-session invalidation in the caller's transaction."""
    job = conn.execute(
        "SELECT id, session_id, project_id, status FROM jobs WHERE id = ?",
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
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return {"session_id": session_id, "event_id": _as_int(cursor.lastrowid)}


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
    job_id = _as_int(recovery["job_id"])
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
    if (
        not row["origin_focus_captured"]
        or (
            row["origin_focus_epoch_id"] is not None
            and row["epoch_session_id"] != master_session_id
        )
    ):
        raise ValueError("recovery Master Focus origin is unavailable")

    content = _recovery_content(recovery)
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
        "task_status": recovery["restored_status"],
        "container_id": row["project_id"],
        "container_slug": row["container_slug"],
        "area_id": row["target_area_id"],
        "checkpoint_id": recovery["checkpoint_id"],
        "actor": recovery["actor"],
        "prior_status": recovery["prior_status"],
        "restored_status": recovery["restored_status"],
        "discarded_progress": recovery["discarded_progress"],
        "conflicting_progress": recovery["conflicting_progress"],
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
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
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

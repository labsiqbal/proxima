"""Durable Master Focus epochs and immutable message attribution.

Focus is a context-isolation boundary, not a presentation preference.  This
module is deliberately database-only so a Focus transition can be composed
with a message enqueue in the caller's transaction.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


class MasterFocusError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise MasterFocusError("invalid_focus_version", "Focus version must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MasterFocusError("invalid_focus_version", "Focus version must be an integer") from exc


def ensure_state(conn: sqlite3.Connection, master_session_id: int) -> None:
    """Create fleet-mode state for the one existing Master session if absent."""
    conn.execute(
        "INSERT OR IGNORE INTO master_focus_state("
        "master_session_id, current_epoch_id, pending_container_id, version"
        ") VALUES (?, NULL, NULL, 0)",
        (master_session_id,),
    )


def state_payload(conn: sqlite3.Connection, master_session_id: int) -> dict[str, Any]:
    ensure_state(conn, master_session_id)
    row = conn.execute(
        "SELECT state.current_epoch_id, state.pending_container_id, state.version, "
        "epoch.container_id AS current_container_id "
        "FROM master_focus_state state "
        "LEFT JOIN master_focus_epochs epoch ON epoch.id = state.current_epoch_id "
        "WHERE state.master_session_id = ?",
        (master_session_id,),
    ).fetchone()
    if row is None:
        raise MasterFocusError("focus_state_missing", "Master Focus state is unavailable")
    return {
        "current_epoch_id": row["current_epoch_id"],
        "current_container_id": row["current_container_id"],
        "pending_container_id": row["pending_container_id"],
        "version": _as_int(row["version"]),
    }


def _insert_event(
    conn: sqlite3.Connection,
    *,
    master_session_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    # Focus events are session events, not runner events.  Their sequence is
    # independent because SQLite UNIQUE permits multiple NULL run ids.
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events "
        "WHERE session_id = ? AND run_id IS NULL",
        (master_session_id,),
    ).fetchone()["next_seq"]
    cur = conn.execute(
        "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) "
        "VALUES (NULL, ?, ?, ?, ?, ?)",
        (
            master_session_id,
            payload.get("container_id"),
            seq,
            event_type,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return _as_int(cur.lastrowid)


def stamp_message(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    focus_epoch_id: int | None,
    subject_container_id: int | None = None,
) -> None:
    container_id = None
    if focus_epoch_id is not None:
        row = conn.execute(
            "SELECT container_id FROM master_focus_epochs WHERE id = ?",
            (focus_epoch_id,),
        ).fetchone()
        if row is None:
            raise MasterFocusError("focus_epoch_missing", "Captured Focus epoch is unavailable")
        container_id = _as_int(row["container_id"])
    conn.execute(
        "INSERT INTO message_focus(message_id, focus_epoch_id, focus_container_id, subject_container_id) "
        "VALUES (?, ?, ?, ?)",
        (message_id, focus_epoch_id, container_id, subject_container_id),
    )


def stamp_message_for_run(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    run_id: int,
) -> None:
    """Stamp only Master-thread messages, including fleet turns with NULL epoch."""
    row = conn.execute(
        "SELECT r.focus_epoch_id, s.mode FROM runs r JOIN sessions s ON s.id = r.session_id "
        "WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if row is not None and row["mode"] == "master":
        stamp_message(
            conn,
            message_id=message_id,
            focus_epoch_id=row["focus_epoch_id"],
        )


def _append_boundary(
    conn: sqlite3.Connection,
    *,
    master_session_id: int,
    focus_epoch_id: int | None,
    container_id: int | None,
    version: int,
) -> tuple[int, int]:
    label = (
        f"Master Focus changed to Container {container_id}."
        if container_id is not None
        else "Master Focus changed to Fleet mode."
    )
    message = conn.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'system', ?, 'Proxima')",
        (master_session_id, label),
    )
    message_id = _as_int(message.lastrowid)
    stamp_message(
        conn,
        message_id=message_id,
        focus_epoch_id=focus_epoch_id,
    )
    event_id = _insert_event(
        conn,
        master_session_id=master_session_id,
        event_type="master.focus.changed",
        payload={
            "message_id": message_id,
            "focus_epoch_id": focus_epoch_id,
            "container_id": container_id,
            "version": version,
        },
    )
    return message_id, event_id


def change_focus(
    conn: sqlite3.Connection,
    *,
    master_session_id: int,
    container_id: int | None,
    expected_version: int,
) -> dict[str, Any]:
    """Apply an idle Focus change inside the caller's transaction.

    The version compare-and-set is the serialization point for browser retries,
    reconnects, and concurrent sends.  A no-op still verifies the version but
    does not manufacture a boundary.
    """
    ensure_state(conn, master_session_id)
    current = state_payload(conn, master_session_id)
    expected_version = _as_int(expected_version)
    if current["version"] != expected_version:
        raise MasterFocusError("focus_version_conflict", "Master Focus changed elsewhere; refresh and retry")
    if current["current_container_id"] == container_id and current["pending_container_id"] is None:
        return {**current, "changed": False, "boundary_message_id": None, "event_id": None}

    old_epoch_id = current["current_epoch_id"]
    if old_epoch_id is not None:
        conn.execute(
            "UPDATE master_focus_epochs SET ended_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND master_session_id = ? AND ended_at IS NULL",
            (old_epoch_id, master_session_id),
        )
    new_epoch_id = None
    if container_id is not None:
        epoch = conn.execute(
            "INSERT INTO master_focus_epochs(master_session_id, container_id, version) "
            "VALUES (?, ?, ?)",
            (master_session_id, container_id, expected_version + 1),
        )
        new_epoch_id = _as_int(epoch.lastrowid)
    updated = conn.execute(
        "UPDATE master_focus_state SET current_epoch_id = ?, pending_container_id = NULL, "
        "version = version + 1, updated_at = CURRENT_TIMESTAMP "
        "WHERE master_session_id = ? AND version = ?",
        (new_epoch_id, master_session_id, expected_version),
    )
    if updated.rowcount != 1:
        raise MasterFocusError("focus_version_conflict", "Master Focus changed elsewhere; refresh and retry")
    next_state = state_payload(conn, master_session_id)
    message_id, event_id = _append_boundary(
        conn,
        master_session_id=master_session_id,
        focus_epoch_id=new_epoch_id,
        container_id=container_id,
        version=next_state["version"],
    )
    return {
        **next_state,
        "changed": True,
        "boundary_message_id": message_id,
        "event_id": event_id,
    }


def request_pending_focus(
    conn: sqlite3.Connection,
    *,
    master_session_id: int,
    container_id: int | None,
    expected_version: int,
) -> dict[str, Any]:
    ensure_state(conn, master_session_id)
    expected_version = _as_int(expected_version)
    updated = conn.execute(
        "UPDATE master_focus_state SET pending_container_id = ?, version = version + 1, "
        "updated_at = CURRENT_TIMESTAMP WHERE master_session_id = ? AND version = ?",
        (container_id, master_session_id, expected_version),
    )
    if updated.rowcount != 1:
        raise MasterFocusError("focus_version_conflict", "Master Focus changed elsewhere; refresh and retry")
    return {**state_payload(conn, master_session_id), "pending": True}


def apply_pending_if_idle(conn: sqlite3.Connection, *, master_session_id: int) -> dict[str, Any] | None:
    """Apply exactly once after the last queued/running Master run closes."""
    active = conn.execute(
        "SELECT 1 FROM runs WHERE session_id = ? AND status IN ('queued', 'running') LIMIT 1",
        (master_session_id,),
    ).fetchone()
    if active is not None:
        return None
    state = state_payload(conn, master_session_id)
    if state["pending_container_id"] is None:
        return None
    return change_focus(
        conn,
        master_session_id=master_session_id,
        container_id=_as_int(state["pending_container_id"]),
        expected_version=state["version"],
    )

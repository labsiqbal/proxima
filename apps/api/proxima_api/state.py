"""Central state machines for Proxima's durable status columns.

Every status transition lives here so the legal moves are defined in ONE place,
and every write goes through a *guarded* conditional UPDATE that reports whether
it actually fired. A guarded transition that returns ``False`` means another
writer changed the row first — the caller lost the race and must not assume its
write landed. (The pre-refactor code did blind ``UPDATE ... SET status=?`` and
silently overwrote a concurrent cancel; guarding the write makes the loss
detectable instead.)

This module is pure and side-effect-free apart from the single UPDATE it issues
on the connection you hand it — so it is trivially unit-testable and safe to wire
into the worker/request paths incrementally.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping

# --- legal transitions per entity ------------------------------------------
# Keys are current status; values are the set of statuses reachable from it.
# A key mapping to an empty set is a terminal state.

RUN: Mapping[str, set[str]] = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}

JOB: Mapping[str, set[str]] = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"review", "done", "failed", "cancelled"},
    "review": {"running", "done", "failed", "cancelled"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}

COLLABORATION: Mapping[str, set[str]] = {
    "queued": {"running", "done", "failed", "cancelled"},
    "running": {"done", "failed", "cancelled"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}

REVIEW: Mapping[str, set[str]] = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"completed", "applied", "failed", "cancelled"},
    "completed": {"applied"},
    "applied": set(),
    "failed": set(),
    "cancelled": set(),
}

# Graph workflow node lifecycle (ADR-0001). Unlike RUN/JOB rows, node states
# carry an optimistic-concurrency version and use guarded_versioned_transition.
NODE: Mapping[str, set[str]] = {
    "pending": {"ready", "stale", "skipped"},
    "ready": {"running", "stale", "skipped"},
    "running": {"done", "review", "failed", "stale"},
    "review": {"done", "failed", "stale"},
    "done": {"done", "stale"},  # done->done = human output correction + version bump
    "failed": {"ready", "done", "stale"},
    "stale": {"ready", "skipped"},
    "skipped": {"stale"},
}

# The set of statuses across all machines that no transition may leave.
TERMINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled", "done"})


def non_terminal(machine: Mapping[str, set[str]]) -> frozenset[str]:
    """The statuses in ``machine`` that still have outgoing transitions."""
    return frozenset(s for s, outs in machine.items() if outs)


def can(machine: Mapping[str, set[str]], frm: str, to: str) -> bool:
    """True iff ``frm -> to`` is a declared legal transition in ``machine``."""
    return to in machine.get(frm, set())


def is_terminal(status: str) -> bool:
    return status in TERMINAL


def guarded_transition(
    cx: sqlite3.Connection,
    table: str,
    row_id: int,
    to: str,
    allowed_from: Iterable[str],
    *,
    set_extra: str | None = None,
    set_params: tuple = (),
) -> bool:
    """Atomically move ``table.status`` to ``to`` only if the row is currently in
    one of ``allowed_from``.

    Returns ``True`` iff exactly the row transitioned (rowcount > 0). ``False``
    means the row was in some other status — typically a concurrent writer got
    there first — and the caller lost the race.

    ``set_extra`` is an optional extra SQL SET fragment (e.g. ``"error=?,
    finished_at=CURRENT_TIMESTAMP"``); its bound values go in ``set_params`` and
    are spliced in immediately after ``status``.
    """
    source_statuses = list(dict.fromkeys(allowed_from))  # de-dupe, preserve order
    if not source_statuses:
        raise ValueError("allowed_from must be non-empty")
    placeholders = ",".join("?" for _ in source_statuses)
    extra = f", {set_extra}" if set_extra else ""
    sql = (
        f"UPDATE {table} SET status = ?{extra} "
        f"WHERE id = ? AND status IN ({placeholders})"
    )
    cur = cx.execute(sql, (to, *set_params, row_id, *source_statuses))
    return (cur.rowcount or 0) > 0


_UNSET = object()
_NODE_STATUS_SLOTS = 8


def guarded_node_transition(
    cx: sqlite3.Connection,
    row_id: int,
    to: str,
    allowed_from: Iterable[str],
    expected_version: int,
    *,
    run_id: int | None | object = _UNSET,
    inputs: str | None | object = _UNSET,
    output_kind: str | None | object = _UNSET,
    output: str | None | object = _UNSET,
    checkpoint: str | None | object = _UNSET,
    error: str | None | object = _UNSET,
    mark_started: bool = False,
    clear_started: bool = False,
    mark_finished: bool = False,
    clear_finished: bool = False,
    expected_run_id: int | None | object = _UNSET,
) -> bool:
    """CAS one ``node_states`` row and increment ``version`` exactly once.

    The SQL shape is fixed deliberately: node state is a security/correctness
    boundary written by both request and worker threads, so callers pass values,
    never table names or SQL fragments. ``expected_run_id`` rejects late callbacks
    from older rerun attempts.
    """
    source_statuses = list(dict.fromkeys(allowed_from))
    if not source_statuses:
        raise ValueError("allowed_from must be non-empty")
    if len(source_statuses) > _NODE_STATUS_SLOTS:
        raise ValueError("too many allowed node statuses")
    illegal_sources = [status for status in source_statuses if not can(NODE, status, to)]
    if illegal_sources:
        raise ValueError(
            f"illegal node transition to {to!r} from: {', '.join(illegal_sources)}"
        )
    padded_source_statuses = [
        *source_statuses,
        *([""] * (_NODE_STATUS_SLOTS - len(source_statuses))),
    ]

    def optional(value: object) -> tuple[int, object | None]:
        return (0, None) if value is _UNSET else (1, value)

    set_run_id, run_id_value = optional(run_id)
    set_inputs, inputs_value = optional(inputs)
    set_output_kind, output_kind_value = optional(output_kind)
    set_output, output_value = optional(output)
    set_checkpoint, checkpoint_value = optional(checkpoint)
    set_error, error_value = optional(error)
    guard_run_id, expected_run_id_value = optional(expected_run_id)

    cur = cx.execute(
        """
        UPDATE node_states
        SET status = ?,
            version = version + 1,
            run_id = CASE WHEN ? THEN ? ELSE run_id END,
            inputs = CASE WHEN ? THEN ? ELSE inputs END,
            output_kind = CASE WHEN ? THEN ? ELSE output_kind END,
            output = CASE WHEN ? THEN ? ELSE output END,
            checkpoint = CASE WHEN ? THEN ? ELSE checkpoint END,
            error = CASE WHEN ? THEN ? ELSE error END,
            started_at = CASE
                WHEN ? THEN CURRENT_TIMESTAMP
                WHEN ? THEN NULL
                ELSE started_at
            END,
            finished_at = CASE
                WHEN ? THEN CURRENT_TIMESTAMP
                WHEN ? THEN NULL
                ELSE finished_at
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND version = ?
          AND status IN (?, ?, ?, ?, ?, ?, ?, ?)
          AND (? = 0 OR run_id IS ?)
        """,
        (
            to,
            set_run_id, run_id_value,
            set_inputs, inputs_value,
            set_output_kind, output_kind_value,
            set_output, output_value,
            set_checkpoint, checkpoint_value,
            set_error, error_value,
            mark_started, clear_started,
            mark_finished, clear_finished,
            row_id,
            expected_version,
            *padded_source_statuses,
            guard_run_id, expected_run_id_value,
        ),
    )
    return (cur.rowcount or 0) > 0

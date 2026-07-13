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
    froms = list(dict.fromkeys(allowed_from))  # de-dupe, preserve order
    if not froms:
        raise ValueError("allowed_from must be non-empty")
    placeholders = ",".join("?" for _ in froms)
    extra = f", {set_extra}" if set_extra else ""
    sql = (
        f"UPDATE {table} SET status = ?{extra} "
        f"WHERE id = ? AND status IN ({placeholders})"
    )
    cur = cx.execute(sql, (to, *set_params, row_id, *froms))
    return (cur.rowcount or 0) > 0

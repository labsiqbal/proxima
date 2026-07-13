"""Unit tests for the central state machine (proxima_api.state)."""
from __future__ import annotations

import sqlite3

import pytest

from proxima_api import state


def _db() -> sqlite3.Connection:
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, status TEXT, error TEXT, finished_at TEXT)")
    cx.execute("INSERT INTO runs(id, status) VALUES (1, 'running')")
    return cx


def test_can_reports_declared_transitions():
    assert state.can(state.RUN, "running", "completed")
    assert state.can(state.RUN, "queued", "running")
    assert not state.can(state.RUN, "completed", "running")  # terminal is a sink
    assert not state.can(state.RUN, "running", "queued")     # no going back


def test_terminal_helpers():
    assert state.is_terminal("completed")
    assert state.is_terminal("done")
    assert not state.is_terminal("running")
    assert state.non_terminal(state.COLLABORATION) == frozenset({"queued", "running"})


def test_guarded_transition_fires_from_allowed_status():
    cx = _db()
    fired = state.guarded_transition(cx, "runs", 1, "completed", ("running",),
                                     set_extra="finished_at=CURRENT_TIMESTAMP")
    assert fired is True
    assert cx.execute("SELECT status FROM runs WHERE id=1").fetchone()[0] == "completed"


def test_guarded_transition_is_noop_from_disallowed_status():
    cx = _db()
    cx.execute("UPDATE runs SET status='cancelled' WHERE id=1")  # someone cancelled first
    fired = state.guarded_transition(cx, "runs", 1, "completed", ("running",))
    assert fired is False                                        # we lost the race
    assert cx.execute("SELECT status FROM runs WHERE id=1").fetchone()[0] == "cancelled"  # not overwritten


def test_guarded_transition_binds_extra_params_in_order():
    cx = _db()
    fired = state.guarded_transition(cx, "runs", 1, "failed", ("queued", "running"),
                                     set_extra="error=?, finished_at=CURRENT_TIMESTAMP",
                                     set_params=("boom",))
    assert fired is True
    row = cx.execute("SELECT status, error FROM runs WHERE id=1").fetchone()
    assert row[0] == "failed" and row[1] == "boom"


def test_guarded_transition_rejects_empty_allowed_from():
    cx = _db()
    with pytest.raises(ValueError):
        state.guarded_transition(cx, "runs", 1, "completed", ())

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
    assert fired
    assert cx.execute("SELECT status FROM runs WHERE id=1").fetchone()[0] == "completed"


def test_guarded_transition_is_noop_from_disallowed_status():
    cx = _db()
    cx.execute("UPDATE runs SET status='cancelled' WHERE id=1")  # someone cancelled first
    fired = state.guarded_transition(cx, "runs", 1, "completed", ("running",))
    assert not fired                                             # we lost the race
    assert cx.execute("SELECT status FROM runs WHERE id=1").fetchone()[0] == "cancelled"  # not overwritten


def test_guarded_transition_binds_extra_params_in_order():
    cx = _db()
    fired = state.guarded_transition(cx, "runs", 1, "failed", ("queued", "running"),
                                     set_extra="error=?, finished_at=CURRENT_TIMESTAMP",
                                     set_params=("boom",))
    assert fired
    row = cx.execute("SELECT status, error FROM runs WHERE id=1").fetchone()
    assert row[0] == "failed" and row[1] == "boom"


def test_guarded_transition_rejects_empty_allowed_from():
    cx = _db()
    with pytest.raises(ValueError):
        state.guarded_transition(cx, "runs", 1, "completed", ())


def _node_db() -> sqlite3.Connection:
    cx = sqlite3.connect(":memory:")
    cx.execute(
        """
        CREATE TABLE node_states (
          id INTEGER PRIMARY KEY,
          status TEXT NOT NULL,
          run_id INTEGER,
          inputs TEXT,
          output_kind TEXT,
          output TEXT,
          checkpoint TEXT,
          error TEXT,
          version INTEGER NOT NULL DEFAULT 0,
          started_at TEXT,
          finished_at TEXT,
          updated_at TEXT
        )
        """
    )
    cx.execute("INSERT INTO node_states(id, status) VALUES (1, 'pending')")
    return cx


def test_guarded_node_transition_versions_and_sets_attempt_fields():
    cx = _node_db()

    ready_from = ("pending",)
    became_ready = state.guarded_node_transition(cx, 1, "ready", ready_from, 0)
    assert became_ready
    running_from = ("ready",)
    became_running = state.guarded_node_transition(
        cx,
        1,
        "running",
        running_from,
        1,
        run_id=42,
        inputs='{"brief":"x"}',
        error=None,
        mark_started=True,
        clear_finished=True,
    )
    assert became_running

    row = cx.execute(
        "SELECT status, run_id, inputs, error, version, started_at, finished_at "
        "FROM node_states WHERE id=1"
    ).fetchone()
    expected = ("running", 42, '{"brief":"x"}', None, 2)
    assert row[:5] == expected
    assert row[5] is not None
    assert row[6] is None


def test_guarded_node_transition_rejects_stale_version_and_old_attempt():
    cx = _node_db()
    cx.execute("UPDATE node_states SET status='running', run_id=9, version=3 WHERE id=1")

    allowed = ("running",)
    stale_version = state.guarded_node_transition(
        cx, 1, "done", allowed, 2, expected_run_id=9
    )
    old_attempt = state.guarded_node_transition(
        cx, 1, "done", allowed, 3, expected_run_id=8
    )
    current_attempt = state.guarded_node_transition(
        cx, 1, "done", allowed, 3, expected_run_id=9, output='"ok"'
    )
    assert not stale_version
    assert not old_attempt
    assert current_attempt


def test_guarded_node_transition_rejects_illegal_state_machine_edge():
    cx = _node_db()
    allowed = ("pending",)
    with pytest.raises(ValueError, match="illegal node transition"):
        state.guarded_node_transition(cx, 1, "done", allowed, 0)

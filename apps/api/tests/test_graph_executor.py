from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from proxima_api import state
from proxima_api.graph import normalize_graph
from proxima_api.graph_executor import (  # pyright: ignore[reportMissingImports]
    GRAPH_NODE_RUN_KIND,
    GraphExecutor,
)
from proxima_api.main import create_app


def _app(tmp_path, *, enabled: bool):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [
                {"username": "bob", "role": "member", "os_user": "bob"}
            ],
            "feature_workflow_graph": enabled,
            "start_worker": False,
        }
    )


def _client(app) -> TestClient:
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _diamond_graph() -> dict[str, Any]:
    return normalize_graph(
        {
            "nodes": [
                {"id": "collect", "name": "Collect", "instruction": "Collect facts"},
                {"id": "draft-a", "name": "Draft A", "depends_on": ["collect"]},
                {"id": "draft-b", "name": "Draft B", "depends_on": ["collect"]},
                {
                    "id": "merge",
                    "name": "Merge",
                    "instruction": "Merge the drafts",
                    "depends_on": ["draft-a", "draft-b"],
                },
            ]
        }
    )


def _create_graph_job(app, graph: dict[str, Any]) -> int:
    db = app.state.worker_db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    profile = db.execute(
        "SELECT * FROM profiles WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1",
        (owner,),
    ).fetchone()
    session_id = db.execute(
        """
        INSERT INTO sessions(
          title, owner_user_id, profile_id, runner_id, visibility, mode
        ) VALUES ('Graph parent', ?, ?, ?, 'private', 'chat')
        """,
        (owner, profile["id"], profile["runner_id"]),
    ).lastrowid
    job_id = db.execute(
        """
        INSERT INTO jobs(
          session_id, title, status, input, steps_state, engine, graph, created_by
        ) VALUES (?, 'Graph job', 'running', ?, '[]', 'graph', ?, ?)
        """,
        (
            session_id,
            json.dumps({"brief": "Launch plan"}),
            json.dumps(graph),
            owner,
        ),
    ).lastrowid
    db.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
    if job_id is None:
        raise AssertionError("graph job insert did not return an id")
    return job_id


def _state(app, job_id: int, node_id: str) -> dict[str, Any]:
    row = app.state.worker_db.execute(
        "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
        (job_id, node_id),
    ).fetchone()
    return dict(row)


def _complete_current_node(app, job_id: int, node_id: str, output: Any) -> None:
    db = app.state.worker_db
    row = _state(app, job_id, node_id)
    db.execute(
        "UPDATE runs SET status='completed', finished_at=CURRENT_TIMESTAMP WHERE id=?",
        (row["run_id"],),
    )
    allowed = ("running",)
    transitioned = state.guarded_node_transition(
        db,
        row["id"],
        "done",
        allowed,
        row["version"],
        output=json.dumps(output),
        error=None,
        mark_finished=True,
        expected_run_id=row["run_id"],
    )
    assert transitioned


def test_dispatch_creates_one_isolated_hidden_node_run(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job_id = _create_graph_job(app, _diamond_graph())

    run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)

    assert len(run_ids) == 1
    run = dict(
        app.state.worker_db.execute(
            "SELECT * FROM runs WHERE id = ?", (run_ids[0],)
        ).fetchone()
    )
    node = _state(app, job_id, "collect")
    assert run["kind"] == GRAPH_NODE_RUN_KIND
    assert node["status"] == "running"
    assert node["run_id"] == run["id"]
    assert node["version"] == 2
    assert "Launch plan" in run["prompt"]
    assert "untrusted_upstream_outputs" in run["prompt"]
    assert "build on the prior steps already in this conversation" not in run["prompt"]

    node_session = app.state.worker_db.execute(
        "SELECT * FROM sessions WHERE id = ?", (run["session_id"],)
    ).fetchone()
    assert node_session["job_id"] == job_id
    assert node_session["workflow_id"] is None
    app.state.worker_db.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, 'assistant', 'hidden')",
        (run["session_id"],),
    )
    visible_ids = {session["id"] for session in client.get("/api/sessions").json()["sessions"]}
    assert run["session_id"] not in visible_ids

    no_second_run = app.state.worker.graph_executor.dispatch_ready(job_id)
    assert no_second_run == []


def test_phase1_dispatches_diamond_deterministically_with_fresh_sessions(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    graph = _diamond_graph()
    job_id = _create_graph_job(app, graph)
    executor: GraphExecutor = app.state.worker.graph_executor

    dispatched_nodes: list[str] = []
    session_ids: list[int] = []
    outputs = {
        "collect": "facts",
        "draft-a": "alpha",
        "draft-b": "beta",
    }
    for expected_node in ("collect", "draft-a", "draft-b", "merge"):
        run_ids = executor.dispatch_ready(job_id)
        assert len(run_ids) == 1
        node_row = app.state.worker_db.execute(
            "SELECT node_id FROM node_states WHERE run_id = ?", (run_ids[0],)
        ).fetchone()
        node_id = node_row["node_id"]
        dispatched_nodes.append(node_id)
        run = app.state.worker_db.execute(
            "SELECT session_id, prompt FROM runs WHERE id = ?", (run_ids[0],)
        ).fetchone()
        session_ids.append(run["session_id"])
        assert node_id == expected_node
        if node_id == "merge":
            assert "alpha" in run["prompt"]
            assert "beta" in run["prompt"]
        _complete_current_node(app, job_id, node_id, outputs.get(node_id, "merged"))

    assert dispatched_nodes == ["collect", "draft-a", "draft-b", "merge"]
    assert len(set(session_ids)) == len(session_ids)


def test_dispatch_is_inert_when_graph_feature_is_off(tmp_path):
    app = _app(tmp_path, enabled=False)
    _client(app)
    job_id = _create_graph_job(app, _diamond_graph())
    before = {
        table: app.state.worker_db.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - test allowlist below
        ).fetchone()["c"]
        for table in ("node_states", "runs", "sessions")
    }

    run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)

    after = {
        table: app.state.worker_db.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - test allowlist below
        ).fetchone()["c"]
        for table in ("node_states", "runs", "sessions")
    }
    assert run_ids == []
    assert after == before

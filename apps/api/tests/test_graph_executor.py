from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from proxima_api import state
from proxima_api.graph import normalize_graph
from proxima_api.graph_advancers import (  # pyright: ignore[reportMissingImports]
    NodeOutputError,
    validate_node_output,
)
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


def _decode_json(value: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AssertionError("expected valid persisted JSON") from exc


def _single_node_graph(**node_fields: Any) -> dict[str, Any]:
    node = {"id": "only", "name": "Only", "instruction": "Produce output"}
    node.update(node_fields)
    return normalize_graph({"nodes": [node]})


def _finish_run_row(app, run_id: int) -> dict[str, Any]:
    app.state.worker_db.execute(
        "UPDATE runs SET status='completed', finished_at=CURRENT_TIMESTAMP WHERE id=?",
        (run_id,),
    )
    return dict(
        app.state.worker_db.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    )


def test_graph_advancer_validates_output_and_dispatches_next_node(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(app, _diamond_graph())
    first_run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]
    first_run = _finish_run_row(app, first_run_id)

    app.state.worker._advance_job(first_run, "facts")

    assert _state(app, job_id, "collect")["status"] == "done"
    assert _decode_json(_state(app, job_id, "collect")["output"]) == "facts"
    assert _state(app, job_id, "draft-a")["status"] == "running"
    assert _state(app, job_id, "draft-b")["status"] == "pending"
    job_status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert job_status == "running"


def test_invalid_json_output_fails_node_and_pauses_job(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    graph = _single_node_graph(
        output_kind="json",
        output_schema={
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        },
    )
    job_id = _create_graph_job(app, graph)
    run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]
    run = _finish_run_row(app, run_id)

    app.state.worker._advance_job(run, "not json")

    node = _state(app, job_id, "only")
    assert node["status"] == "failed"
    assert "invalid JSON" in node["error"]
    job_status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert job_status == "review"


def test_final_node_completes_then_moves_job_to_final_review(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(app, _single_node_graph(output_kind="text"))
    run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]
    run = _finish_run_row(app, run_id)

    app.state.worker._advance_job(run, "finished")

    node = _state(app, job_id, "only")
    assert node["status"] == "done"
    assert _decode_json(node["output"]) == "finished"
    job_status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert job_status == "review"


def test_review_gate_persists_valid_output_without_dispatching(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(
        app, _single_node_graph(review_required=True, output_kind="text")
    )
    run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]
    run = _finish_run_row(app, run_id)

    app.state.worker._advance_job(run, "ready for review")

    node = _state(app, job_id, "only")
    assert node["status"] == "review"
    assert _decode_json(node["output"]) == "ready for review"
    job_status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert job_status == "review"


def test_runner_failure_marks_current_graph_node_failed_and_reviewable(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(app, _single_node_graph())
    run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]
    run = dict(
        app.state.worker_db.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    )
    app.state.worker_db.execute(
        "UPDATE runs SET status='failed', error='boom' WHERE id = ?", (run_id,)
    )

    app.state.worker._fail_job(run["session_id"], "boom", run_id)

    assert _state(app, job_id, "only")["status"] == "failed"
    job_status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert job_status == "review"


def test_artifact_ref_contract_requires_existing_contained_paths(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(app, _single_node_graph(output_kind="artifact-ref"))
    root = (
        tmp_path / "ws" / "scratch" / "workflow-runs" / f"job-{job_id}"
    )
    artifact = root / "artifacts" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("report", encoding="utf-8")
    node = _single_node_graph(output_kind="artifact-ref")["nodes"][0]
    job = {"job_id": job_id, "project_id": None}

    value = validate_node_output(
        app,
        job,
        node,
        '[{"path":"artifacts/report.md","type":"document"}]',
    )
    assert value == [{"path": "artifacts/report.md", "type": "document"}]

    try:
        validate_node_output(app, job, node, '{"path":"../secret"}')
    except NodeOutputError as exc:
        assert "inside the job workspace" in str(exc)
    else:
        raise AssertionError("path traversal artifact reference was accepted")


def test_linear_orphan_reaper_ignores_running_graph_jobs(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(app, _single_node_graph())
    app.state.worker.graph_executor.dispatch_ready(job_id)

    failed = app.state.worker.reap_orphaned_jobs()

    assert failed == 0
    status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert status == "running"

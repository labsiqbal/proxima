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
    GraphExecutionError,
    GraphExecutor,
)
from proxima_api.main import create_app


def _app(tmp_path, *, enabled: bool, concurrency: int | None = None):
    config: dict[str, Any] = {
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "seed_users": [
            {"username": "bob", "role": "member", "os_user": "bob"}
        ],
        "feature_workflow_graph": enabled,
        "start_worker": False,
    }
    if concurrency is not None:
        config["graph_node_concurrency"] = concurrency
    return create_app(config)


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


def _nodes_for_runs(app, run_ids: list[int]) -> set[str]:
    return {
        app.state.worker_db.execute(
            "SELECT node_id FROM node_states WHERE run_id = ?", (run_id,)
        ).fetchone()["node_id"]
        for run_id in run_ids
    }


def test_diamond_branches_dispatch_in_parallel_with_fresh_sessions(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    job_id = _create_graph_job(app, _diamond_graph())
    executor: GraphExecutor = app.state.worker.graph_executor
    session_ids: list[int] = []

    def sessions_for(run_ids: list[int]) -> None:
        for run_id in run_ids:
            session_ids.append(
                app.state.worker_db.execute(
                    "SELECT session_id FROM runs WHERE id = ?", (run_id,)
                ).fetchone()["session_id"]
            )

    collect_runs = executor.dispatch_ready(job_id)
    assert _nodes_for_runs(app, collect_runs) == {"collect"}
    sessions_for(collect_runs)
    _complete_current_node(app, job_id, "collect", "facts")

    # The whole point of the graph engine: both branches of the diamond run at
    # once rather than one after the other.
    branch_runs = executor.dispatch_ready(job_id)
    assert _nodes_for_runs(app, branch_runs) == {"draft-a", "draft-b"}
    sessions_for(branch_runs)
    assert executor.dispatch_ready(job_id) == []
    _complete_current_node(app, job_id, "draft-a", "alpha")
    _complete_current_node(app, job_id, "draft-b", "beta")

    merge_runs = executor.dispatch_ready(job_id)
    assert _nodes_for_runs(app, merge_runs) == {"merge"}
    sessions_for(merge_runs)
    merge_prompt = app.state.worker_db.execute(
        "SELECT prompt FROM runs WHERE id = ?", (merge_runs[0],)
    ).fetchone()["prompt"]
    assert "alpha" in merge_prompt
    assert "beta" in merge_prompt
    assert len(set(session_ids)) == len(session_ids) == 4


def test_node_concurrency_budget_caps_a_fan_out(tmp_path):
    app = _app(tmp_path, enabled=True, concurrency=1)
    _client(app)
    job_id = _create_graph_job(app, _diamond_graph())
    executor: GraphExecutor = app.state.worker.graph_executor

    executor.dispatch_ready(job_id)
    _complete_current_node(app, job_id, "collect", "facts")
    branch_runs = executor.dispatch_ready(job_id)

    assert len(branch_runs) == 1
    assert executor.dispatch_ready(job_id) == []


def test_manual_trigger_resolves_to_job_input_without_a_run(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "start", "type": "trigger", "name": "When I run it"},
                {"id": "work", "name": "Work", "instruction": "Do it", "depends_on": ["start"]},
            ]
        }
    )
    job_id = _create_graph_job(app, graph)

    run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)

    trigger = _state(app, job_id, "start")
    assert trigger["status"] == "done"
    assert trigger["run_id"] is None
    assert _decode_json(trigger["output"]) == {"brief": "Launch plan"}
    # No runner is spawned for the trigger; only the node behind it runs.
    assert _nodes_for_runs(app, run_ids) == {"work"}
    work_prompt = app.state.worker_db.execute(
        "SELECT prompt FROM runs WHERE id = ?", (run_ids[0],)
    ).fetchone()["prompt"]
    # The trigger reaches the next node as ordinary typed upstream data.
    assert "Launch plan" in work_prompt


def _second_profile(app) -> dict[str, Any]:
    db = app.state.worker_db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    base = db.execute(
        "SELECT * FROM profiles WHERE user_id = ? ORDER BY id LIMIT 1", (owner,)
    ).fetchone()
    profile_id = db.execute(
        """
        INSERT INTO profiles(user_id, slug, name, runner_id, default_model, hermes_home)
        VALUES (?, 'specialist', 'Specialist', ?, 'specialist-model', ?)
        """,
        (owner, base["runner_id"], base["hermes_home"]),
    ).lastrowid
    return {"id": profile_id, "owner": owner}


def test_node_runs_against_its_own_agent(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    specialist = _second_profile(app)
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "only", "name": "Only", "profile_id": specialist["id"]},
            ]
        }
    )
    job_id = _create_graph_job(app, graph)

    run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]

    run = app.state.worker_db.execute(
        "SELECT * FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert run["profile_id"] == specialist["id"]
    assert run["model"] == "specialist-model"
    node_session = app.state.worker_db.execute(
        "SELECT * FROM sessions WHERE id = ?", (run["session_id"],)
    ).fetchone()
    assert node_session["profile_id"] == specialist["id"]


def test_node_naming_an_unavailable_agent_fails_loudly(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    graph = normalize_graph({"nodes": [{"id": "only", "name": "Only", "profile_id": 9999}]})
    job_id = _create_graph_job(app, graph)

    try:
        app.state.worker.graph_executor.dispatch_ready(job_id)
    except GraphExecutionError as exc:
        assert "no longer available" in str(exc)
    else:
        raise AssertionError("a node naming a missing agent was dispatched anyway")
    # The transaction is rolled back whole, so nothing is left half-dispatched.
    for table in ("runs", "node_states"):
        count = app.state.worker_db.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - fixed table names above
        ).fetchone()["c"]
        assert count == 0


def test_sibling_result_survives_a_branch_pausing_the_job(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "gated", "name": "Gated", "review_required": True},
                {"id": "sibling", "name": "Sibling"},
            ]
        }
    )
    job_id = _create_graph_job(app, graph)
    run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)
    assert len(run_ids) == 2
    by_node = {
        app.state.worker_db.execute(
            "SELECT node_id FROM node_states WHERE run_id = ?", (run_id,)
        ).fetchone()["node_id"]: run_id
        for run_id in run_ids
    }

    # The gated branch pauses the whole job while its sibling is still running.
    app.state.worker._advance_job(_finish_run_row(app, by_node["gated"]), "needs review")
    assert _state(app, job_id, "gated")["status"] == "review"
    job_status = app.state.worker_db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    assert job_status == "review"

    app.state.worker._advance_job(_finish_run_row(app, by_node["sibling"]), "sibling done")

    # The in-flight sibling's work is kept, not dropped on the floor.
    sibling = _state(app, job_id, "sibling")
    assert sibling["status"] == "done"
    assert _decode_json(sibling["output"]) == "sibling done"


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
    assert _state(app, job_id, "draft-b")["status"] == "running"
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


def test_prompt_to_artifact_graph_flow_reaches_final_review(tmp_path):
    app = _app(tmp_path, enabled=True)
    _client(app)
    graph = {
        "nodes": [
            {
                "id": "research",
                "name": "Research",
                "instruction": "Collect verified facts",
                "output_kind": "text",
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "instruction": "Write the final report",
                "output_kind": "artifact-ref",
            },
        ],
        "edges": [{"from": "research", "to": "deliver"}],
    }
    job_id = _create_graph_job(app, graph)
    research_run_id = app.state.worker.graph_executor.dispatch_ready(job_id)[0]
    research_run = _finish_run_row(app, research_run_id)

    app.state.worker._advance_job(research_run, "verified facts")

    deliver_state = _state(app, job_id, "deliver")
    assert deliver_state["status"] == "running"
    deliver_run = _finish_run_row(app, deliver_state["run_id"])
    assert "verified facts" in deliver_run["prompt"]
    root = tmp_path / "ws" / "scratch" / "workflow-runs" / f"job-{job_id}"
    artifact = root / "artifacts" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("final report", encoding="utf-8")

    app.state.worker._advance_job(
        deliver_run,
        '[{"path":"artifacts/report.md","type":"document"}]',
    )

    delivered = _state(app, job_id, "deliver")
    assert delivered["status"] == "done"
    assert _decode_json(delivered["output"]) == [
        {"path": "artifacts/report.md", "type": "document"}
    ]
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

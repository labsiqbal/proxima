from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

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


def _chain_graph(*, gate_first: bool = False) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "a",
                "name": "A",
                "instruction": "produce A",
                "review_required": gate_first,
            },
            {"id": "b", "name": "B", "instruction": "produce B", "depends_on": ["a"]},
            {"id": "c", "name": "C", "instruction": "produce C", "depends_on": ["b"]},
        ]
    }


def _create(client: TestClient, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/graph/jobs",
        json={
            "title": "Graph plan",
            "graph": graph or _chain_graph(),
            "input": {"brief": "launch"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _complete_running_node(app, job_id: int, answer: str) -> str:
    row = app.state.worker_db.execute(
        """
        SELECT ns.node_id, r.* FROM node_states ns
        JOIN runs r ON r.id = ns.run_id
        WHERE ns.job_id = ? AND ns.status = 'running'
        ORDER BY ns.id LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if not row:
        raise AssertionError("graph job has no running node")
    app.state.worker_db.execute(
        "UPDATE runs SET status='completed', finished_at=CURRENT_TIMESTAMP WHERE id=?",
        (row["id"],),
    )
    app.state.worker._advance_job(dict(row), answer)
    return row["node_id"]


def _finish_chain(app, job_id: int) -> None:
    for answer in ("A output", "B output", "C output"):
        _complete_running_node(app, job_id, answer)


def _states(client: TestClient, job_id: int) -> dict[str, dict[str, Any]]:
    payload = client.get(f"/api/graph/jobs/{job_id}").json()
    return {node["node_id"]: node for node in payload["node_states"]}


def test_create_edit_plan_start_and_inspect_graph_job(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = _create(client)
    job_id = job["id"]

    assert job["status"] == "queued"
    assert [node["status"] for node in job["node_states"]] == [
        "pending",
        "pending",
        "pending",
    ]
    revised = {
        "nodes": [
            {"id": "research", "name": "Research"},
            {"id": "write", "name": "Write", "depends_on": ["research"]},
        ]
    }
    updated = client.patch(
        f"/api/graph/jobs/{job_id}/graph", json={"graph": revised}
    )
    assert updated.status_code == 200
    assert [node["node_id"] for node in updated.json()["node_states"]] == [
        "research",
        "write",
    ]

    started = client.post(f"/api/graph/jobs/{job_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    states = _states(client, job_id)
    assert states["research"]["status"] == "running"
    assert states["write"]["status"] == "pending"

    graph_ids = {
        item["id"] for item in client.get("/api/graph/jobs").json()["items"]
    }
    linear_ids = {item["id"] for item in client.get("/api/jobs").json()["items"]}
    assert job_id in graph_ids
    assert job_id not in linear_ids


def test_edit_upstream_output_marks_descendants_stale_and_reruns(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = _create(client)
    job_id = job["id"]
    client.post(f"/api/graph/jobs/{job_id}/start")
    _finish_chain(app, job_id)
    assert client.get(f"/api/graph/jobs/{job_id}").json()["status"] == "review"

    corrected = client.patch(
        f"/api/graph/jobs/{job_id}/nodes/a/output",
        json={"value": "Corrected A"},
    )

    assert corrected.status_code == 200, corrected.text
    payload = corrected.json()
    assert payload["status"] == "running"
    states = {node["node_id"]: node for node in payload["node_states"]}
    assert states["a"]["status"] == "done"
    assert states["a"]["output"] == "Corrected A"
    assert states["b"]["status"] == "running"
    assert states["c"]["status"] == "stale"
    rerun = app.state.worker_db.execute(
        "SELECT prompt FROM runs WHERE id = ?", (states["b"]["run_id"],)
    ).fetchone()
    assert "Corrected A" in rerun["prompt"]


def test_rerun_node_invalidates_downstream_and_uses_new_attempt(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job_id = _create(client)["id"]
    client.post(f"/api/graph/jobs/{job_id}/start")
    _finish_chain(app, job_id)
    before = _states(client, job_id)
    old_run_id = before["b"]["run_id"]

    rerun = client.post(f"/api/graph/jobs/{job_id}/nodes/b/rerun")

    assert rerun.status_code == 200, rerun.text
    states = {node["node_id"]: node for node in rerun.json()["node_states"]}
    assert states["b"]["status"] == "running"
    assert states["b"]["run_id"] != old_run_id
    assert states["c"]["status"] == "stale"

    old_run = dict(
        app.state.worker_db.execute(
            "SELECT * FROM runs WHERE id = ?", (old_run_id,)
        ).fetchone()
    )
    changed = app.state.worker.graph_advancers.advance_run(
        old_run, "late old output", app.state.worker.add_event
    )
    assert not changed
    assert _states(client, job_id)["b"]["run_id"] == states["b"]["run_id"]


def test_gate_approval_then_final_job_approval(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {"nodes": [{"id": "gate", "name": "Gate", "review_required": True}]}
    job_id = _create(client, graph)["id"]
    client.post(f"/api/graph/jobs/{job_id}/start")
    _complete_running_node(app, job_id, "review me")

    states = _states(client, job_id)
    assert states["gate"]["status"] == "review"
    approved_node = client.post(
        f"/api/graph/jobs/{job_id}/nodes/gate/approve"
    )
    assert approved_node.status_code == 200
    assert approved_node.json()["status"] == "review"
    assert approved_node.json()["node_states"][0]["status"] == "done"

    approved_job = client.post(f"/api/graph/jobs/{job_id}/approve")
    assert approved_job.status_code == 200
    assert approved_job.json()["status"] == "done"


def test_save_reviewed_graph_as_reusable_template(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    linear = client.post(
        "/api/workflows",
        json={"name": "Linear only", "steps": [{"name": "One", "instruction": "Do it"}]},
    ).json()
    rejected = client.post(
        "/api/graph/jobs",
        json={"title": "Wrong engine", "workflow_id": linear["id"], "graph": _chain_graph()},
    )
    assert rejected.status_code == 404
    job = _create(client)

    declared = [{"id": "brief", "label": "Brief", "kind": "text", "required": True}]
    saved = client.post(
        f"/api/graph/jobs/{job['id']}/save-template",
        json={
            "name": "Research and publish",
            "description": "Reusable reviewed DAG",
            "category": "research",
            "inputs": declared,
        },
    )

    assert saved.status_code == 201, saved.text
    template = saved.json()
    assert template["name"] == "Research and publish"
    assert template["steps"] == []
    assert template["graph"] == job["graph"]
    # Declared inputs survive: a schedule renders its form from these, so a template
    # that could not carry them could never be scheduled.
    assert template["inputs"] == declared
    assert client.get("/api/graph/templates").json()["items"][0]["inputs"] == declared
    stored = app.state.db.execute(
        "SELECT graph FROM workflows WHERE id = ?", (template["id"],)
    ).fetchone()
    assert stored is not None
    linked = app.state.db.execute(
        "SELECT workflow_id FROM jobs WHERE id = ?", (job["id"],)
    ).fetchone()
    assert linked["workflow_id"] == template["id"]

    graph_templates = client.get("/api/graph/templates").json()["items"]
    assert [item["id"] for item in graph_templates] == [template["id"]]
    assert all(item["id"] != template["id"] for item in client.get("/api/workflows").json())
    assert all(
        item["id"] != template["id"]
        for item in client.get("/api/dashboard").json()["workflows"]
    )
    classic_job = client.post("/api/jobs", json={"workflow_id": template["id"]})
    assert classic_job.status_code == 404

    reused = client.post(
        "/api/graph/jobs",
        json={
            "title": "Second research run",
            "workflow_id": template["id"],
            "graph": template["graph"],
        },
    )
    assert reused.status_code == 201, reused.text
    assert reused.json()["workflow_id"] == template["id"]


def test_graph_routes_are_inert_while_feature_is_off(tmp_path):
    app = _app(tmp_path, enabled=False)
    client = _client(app)
    before = app.state.db.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE engine='graph'"
    ).fetchone()["c"]

    response = client.post(
        "/api/graph/jobs",
        json={"title": "blocked", "graph": {"nodes": [{"id": "x"}]}},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "feature_disabled"
    after = app.state.db.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE engine='graph'"
    ).fetchone()["c"]
    assert after == before

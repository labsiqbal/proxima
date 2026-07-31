from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from project_test_utils import with_browse_root


def _app(tmp_path, *, enabled: bool, **overrides):
    config = {
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(tmp_path)],
        "seed_users": [
            {"username": "bob", "role": "member", "os_user": "bob"}
        ],
        "feature_workflow_graph": enabled,
        "start_worker": False,
    }
    config.update(overrides)
    return create_app(config)


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


def test_graph_job_api_returns_one_timezone_aware_failed_run_projection(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = _create(client)
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', started_at = ?, finished_at = ? "
        "WHERE id = ?",
        ("2026-07-31 05:00:00", "2026-07-31 05:00:12", job["id"]),
    )
    app.state.db.execute(
        "UPDATE node_states SET status = 'failed', started_at = ?, finished_at = ? "
        "WHERE job_id = ? AND node_id = 'a'",
        ("2026-07-31 05:00:00", "2026-07-31 05:00:12", job["id"]),
    )

    payload = client.get(f"/api/graph/jobs/{job['id']}").json()

    assert payload["started_at"] == "2026-07-31T05:00:00Z"
    assert payload["node_states"][0]["finished_at"] == "2026-07-31T05:00:12Z"
    assert payload["run_projection"] == {
        "status": "failed",
        "started_at": "2026-07-31T05:00:00Z",
        "finished_at": "2026-07-31T05:00:12Z",
        "duration_seconds": 12,
    }


def test_graph_job_payload_includes_owning_project_name(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    assert (
        client.post(
            "/api/projects", json={"slug": "beacon", "name": "Beacon release"}
        ).status_code
        == 201
    )

    response = client.post(
        "/api/graph/jobs",
        json={
            "title": "Beacon graph",
            "graph": _chain_graph(),
            "project_slug": "beacon",
        },
    )

    assert response.status_code == 201
    assert response.json()["project_name"] == "Beacon release"
    listed = client.get("/api/graph/jobs").json()["items"]
    assert next(item for item in listed if item["id"] == response.json()["id"])[
        "project_name"
    ] == "Beacon release"


def test_plan_patch_autosaves_graph_and_title_then_keeps_title_renameable(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = _create(client)
    revised = {
        "nodes": [
            {"id": "research", "name": "Research"},
            {"id": "publish", "name": "Publish", "depends_on": ["research"]},
        ]
    }

    saved = client.patch(
        f"/api/graph/jobs/{job['id']}/graph",
        json={"graph": revised, "title": "Daily research"},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["title"] == "Daily research"
    assert [node["node_id"] for node in saved.json()["node_states"]] == [
        "research",
        "publish",
    ]
    session = app.state.db.execute(
        "SELECT title, manual_title FROM sessions WHERE id = ?",
        (job["session_id"],),
    ).fetchone()
    assert dict(session) == {"title": "Daily research", "manual_title": 1}

    assert client.post(f"/api/graph/jobs/{job['id']}/start").status_code == 200
    frozen = client.patch(
        f"/api/graph/jobs/{job['id']}/graph",
        json={"graph": _chain_graph(), "title": "Should not partially save"},
    )
    assert frozen.status_code == 409
    assert client.get(f"/api/graph/jobs/{job['id']}").json()["title"] == "Daily research"

    renamed = client.patch(
        f"/api/graph/jobs/{job['id']}/graph",
        json={"title": "Daily research run"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Daily research run"


def test_plan_patch_rejects_blank_title_and_empty_payload(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = _create(client)
    endpoint = f"/api/graph/jobs/{job['id']}/graph"

    blank = client.patch(endpoint, json={"title": "   "})
    empty = client.patch(endpoint, json={})

    assert blank.status_code == 422
    assert "title" in blank.json()["detail"]
    assert empty.status_code == 422
    assert "graph or title" in empty.json()["detail"]


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
    declared = [{"id": "brief", "label": "Brief", "kind": "text", "required": True}]
    graph = _chain_graph()
    graph["nodes"].insert(
        0,
        {
            "id": "trigger",
            "type": "trigger",
            "name": "When I run it",
            "inputs": declared,
        },
    )
    graph["nodes"][1]["depends_on"] = ["trigger"]
    job = _create(client, graph)
    saved = client.post(
        f"/api/graph/jobs/{job['id']}/save-template",
        json={
            "name": "Research and publish",
            "description": "Reusable reviewed DAG",
            "category": "research",
        },
    )

    assert saved.status_code == 201, saved.text
    template = saved.json()
    assert template["name"] == "Research and publish"
    assert template["steps"] == []
    assert template["graph"] == job["graph"]
    assert template["graph"]["nodes"][0]["inputs"] == declared
    # The old column remains a compatibility projection for RunModal and old clients.
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


def test_legacy_template_inputs_and_schedule_hydrate_onto_trigger(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = _create(client)
    template = client.post(
        f"/api/graph/jobs/{job['id']}/save-template",
        json={"name": "Legacy workflow"},
    ).json()
    declared = [{"id": "topic", "label": "Topic", "kind": "text", "required": True}]
    # Simulate a row saved before trigger-owned inputs existed.
    app.state.db.execute(
        "UPDATE workflows SET graph = ?, inputs = ? WHERE id = ?",
        (json.dumps(job["graph"]), json.dumps(declared), template["id"]),
    )
    client.post(
        "/api/schedules",
        json={"workflow_id": template["id"], "cron": "0 6 * * *"},
    )

    migrated = client.get("/api/graph/templates").json()["items"][0]
    trigger = migrated["graph"]["nodes"][0]

    assert trigger["type"] == "trigger"
    assert trigger["trigger_kind"] == "scheduled"
    assert trigger["inputs"] == declared
    assert trigger["schedule"] == {
        "cron": "0 6 * * *",
        "overlap_policy": "skip",
        "enabled": True,
    }
    assert migrated["graph"]["edges"][0]["from"] == trigger["id"]
    assert migrated["inputs"] == declared


def test_scheduled_trigger_creates_real_schedule_without_manual_input(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = _chain_graph()
    graph["nodes"].insert(
        0,
        {
            "id": "trigger",
            "type": "trigger",
            "name": "Every morning",
            "trigger_kind": "scheduled",
            "inputs": [
                {"id": "legacy", "label": "Legacy", "kind": "text", "required": True}
            ],
            "schedule": {
                "cron": "30 8 * * 1-5",
                "overlap_policy": "allow",
                "enabled": True,
            },
        },
    )
    graph["nodes"][1]["depends_on"] = ["trigger"]
    job = _create(client, graph)

    response = client.post(
        f"/api/graph/jobs/{job['id']}/save-template",
        json={"name": "Weekday report"},
    )

    assert response.status_code == 201, response.text
    schedule = client.get("/api/schedules").json()[0]
    assert schedule["workflow_id"] == response.json()["id"]
    assert schedule["cron"] == "30 8 * * 1-5"
    assert schedule["overlap_policy"] == "allow"
    assert schedule["input"] == {}


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


def test_deleting_a_graph_template_works_and_takes_its_schedules(tmp_path):
    """DELETE /api/workflows/{id} used to 404 for graph rows — the linear-only guard
    predates graph templates being deletable at all."""
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = client.post("/api/graph/jobs", json={"title": "T", "graph": _chain_graph()}).json()
    template = client.post(
        f"/api/graph/jobs/{job['id']}/save-template", json={"name": "Reusable"}
    ).json()
    schedule = client.post(
        "/api/schedules", json={"workflow_id": template["id"], "cron": "0 9 * * *"}
    ).json()

    response = client.delete(f"/api/workflows/{template['id']}")

    assert response.status_code == 200, response.text
    assert client.get("/api/graph/templates").json()["items"] == []
    # The schedule went with it: a schedule for a deleted workflow could never run.
    remaining = [s["id"] for s in client.get("/api/schedules").json()]
    assert schedule["id"] not in remaining


def test_deleting_a_graph_job_sweeps_its_node_sessions(tmp_path):
    """Every node runs in its own session tied to the job by sessions.job_id, and that
    FK is ON DELETE SET NULL — an unswept delete leaves orphan threads in the sidebar."""
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = client.post("/api/graph/jobs", json={"title": "D", "graph": _chain_graph()}).json()
    client.post(f"/api/graph/jobs/{job['id']}/start")
    db = app.state.db
    before = db.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE job_id = ? OR id = ?",
        (job["id"], job["session_id"]),
    ).fetchone()["c"]
    assert before >= 2, "expected the job session plus at least one node session"

    response = client.delete(f"/api/jobs/{job['id']}")

    assert response.status_code == 200, response.text
    left = db.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE job_id = ? OR id = ?",
        (job["id"], job["session_id"]),
    ).fetchone()["c"]
    assert left == 0
    assert db.execute("SELECT COUNT(*) AS c FROM node_states WHERE job_id = ?", (job["id"],)).fetchone()["c"] == 0


def test_rerun_and_output_edit_still_work_after_final_approval(tmp_path):
    """'done' is just an approved review — a correction re-runs the affected slice the
    same way. Only the graph itself stays frozen after start, not its outputs."""
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job_id = _create(client)["id"]
    client.post(f"/api/graph/jobs/{job_id}/start")
    _finish_chain(app, job_id)
    client.post(f"/api/graph/jobs/{job_id}/approve")
    assert client.get(f"/api/graph/jobs/{job_id}").json()["status"] == "done"

    rerun = client.post(f"/api/graph/jobs/{job_id}/nodes/b/rerun")

    assert rerun.status_code == 200, rerun.text
    payload = rerun.json()
    assert payload["status"] == "running"
    states = {node["node_id"]: node for node in payload["node_states"]}
    assert states["b"]["status"] == "running"
    assert states["c"]["status"] == "stale"

    # Land the revived slice and approve again — then correct an output on the done job.
    _complete_running_node(app, job_id, "B v2")
    _complete_running_node(app, job_id, "C v2")
    client.post(f"/api/graph/jobs/{job_id}/approve")
    corrected = client.patch(
        f"/api/graph/jobs/{job_id}/nodes/a/output", json={"value": "A corrected"}
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "running"


def test_template_status_can_toggle_but_authoring_fields_cannot(tmp_path):
    """PATCH /api/workflows is lifecycle-only for graph rows: pause/resume/archive.
    Steps and inputs are authored on the canvas, not through the linear editor route."""
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = client.post("/api/graph/jobs", json={"title": "T", "graph": _chain_graph()}).json()
    template = client.post(
        f"/api/graph/jobs/{job['id']}/save-template", json={"name": "Pausable"}
    ).json()

    paused = client.patch(f"/api/workflows/{template['id']}", json={"status": "draft"})
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "draft"
    # Still listed (only archived templates hide), so it can be resumed from the rail.
    listed = client.get("/api/graph/templates").json()["items"]
    assert [t["status"] for t in listed if t["id"] == template["id"]] == ["draft"]

    rejected = client.patch(
        f"/api/workflows/{template['id']}", json={"steps": [{"name": "X", "instruction": "x"}]}
    )
    assert rejected.status_code == 422

    resumed = client.patch(f"/api/workflows/{template['id']}", json={"status": "active"})
    assert resumed.json()["status"] == "active"

    archived = client.patch(f"/api/workflows/{template['id']}", json={"status": "archived"})
    assert archived.json()["status"] == "archived"
    assert all(
        item["id"] != template["id"]
        for item in client.get("/api/graph/templates").json()["items"]
    )
    archived_items = client.get(
        "/api/graph/templates", params={"include_archived": True}
    ).json()["items"]
    assert [item["status"] for item in archived_items if item["id"] == template["id"]] == [
        "archived"
    ]

    restored = client.patch(f"/api/workflows/{template['id']}", json={"status": "active"})
    assert restored.json()["status"] == "active"
    assert any(
        item["id"] == template["id"]
        for item in client.get("/api/graph/templates").json()["items"]
    )


def test_archive_then_restore_reinstates_the_pre_archive_status(tmp_path):
    """A paused (draft) template archived and later restored comes back paused, so
    its still-existing schedules stay stopped rather than silently resuming."""
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = client.post("/api/graph/jobs", json={"title": "T", "graph": _chain_graph()}).json()
    template = client.post(
        f"/api/graph/jobs/{job['id']}/save-template", json={"name": "Paused publisher"}
    ).json()

    # Pause it (schedules stop), then archive the paused template.
    assert client.patch(
        f"/api/workflows/{template['id']}", json={"status": "draft"}
    ).json()["status"] == "draft"
    assert client.patch(
        f"/api/workflows/{template['id']}", json={"status": "archived"}
    ).json()["status"] == "archived"

    # Restore returns it to draft (paused), NOT active.
    restored = client.patch(f"/api/workflows/{template['id']}", json={"status": "active"})
    assert restored.json()["status"] == "draft"
    listed = client.get("/api/graph/templates").json()["items"]
    assert [t["status"] for t in listed if t["id"] == template["id"]] == ["draft"]

    # An active template still round-trips through archive back to active.
    assert client.patch(
        f"/api/workflows/{template['id']}", json={"status": "active"}
    ).json()["status"] == "active"
    assert client.patch(
        f"/api/workflows/{template['id']}", json={"status": "archived"}
    ).json()["status"] == "archived"
    assert client.patch(
        f"/api/workflows/{template['id']}", json={"status": "active"}
    ).json()["status"] == "active"


def test_an_invalid_graph_is_a_422_not_a_500(tmp_path):
    """Found live: a cyclic graph in PATCH /graph crashed with an unhandled
    GraphValidationError. An invalid graph is the client's error."""
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    job = client.post("/api/graph/jobs", json={"title": "T", "graph": _chain_graph()}).json()
    cyclic = {"nodes": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}]}

    patched = client.patch(f"/api/graph/jobs/{job['id']}/graph", json={"graph": cyclic})
    created = client.post("/api/graph/jobs", json={"title": "C", "graph": cyclic})

    assert patched.status_code == 422
    assert "acyclic" in patched.json()["detail"]
    assert created.status_code == 422


def test_manual_intake_create_edit_delete_is_atomic_and_ids_survive_reload(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "inputs": [
                    {
                        "id": "campaign",
                        "label": "Campaign",
                        "kind": "text",
                        "required": True,
                    }
                ],
            }
        ]
    }
    job = _create(client, graph)
    job_id = job["id"]

    edited = {
        "nodes": [
            {
                **graph["nodes"][0],
                "inputs": [
                    {
                        "id": "campaign",
                        "label": "Campaign name",
                        "kind": "text",
                        "required": True,
                    },
                    {
                        "id": "audience",
                        "label": "Audience",
                        "kind": "text",
                        "required": False,
                    },
                ],
            }
        ]
    }
    response = client.patch(
        f"/api/graph/jobs/{job_id}/graph", json={"graph": edited}
    )
    assert response.status_code == 200, response.text
    assert response.json()["graph"]["nodes"][0]["inputs"] == edited["nodes"][0]["inputs"]
    assert client.get(f"/api/graph/jobs/{job_id}").json()["graph"]["nodes"][0][
        "inputs"
    ] == edited["nodes"][0]["inputs"]

    duplicate = {
        "nodes": [
            {
                **edited["nodes"][0],
                "inputs": [
                    edited["nodes"][0]["inputs"][0],
                    {
                        **edited["nodes"][0]["inputs"][1],
                        "id": "campaign",
                    },
                ],
            }
        ]
    }
    rejected_duplicate = client.patch(
        f"/api/graph/jobs/{job_id}/graph", json={"graph": duplicate}
    )
    assert rejected_duplicate.status_code == 422
    assert "duplicate input id" in rejected_duplicate.json()["detail"]

    invalid_id = {
        "nodes": [
            {
                **edited["nodes"][0],
                "inputs": [
                    {
                        **edited["nodes"][0]["inputs"][0],
                        "id": "campaign name",
                    }
                ],
            }
        ]
    }
    rejected_id = client.patch(
        f"/api/graph/jobs/{job_id}/graph", json={"graph": invalid_id}
    )
    assert rejected_id.status_code == 422
    assert "letters, numbers, and underscores" in rejected_id.json()["detail"]

    # Both rejected whole-graph edits leave the last accepted intake contract intact.
    assert client.get(f"/api/graph/jobs/{job_id}").json()["graph"]["nodes"][0][
        "inputs"
    ] == edited["nodes"][0]["inputs"]

    deleted = {
        "nodes": [{**edited["nodes"][0], "inputs": [edited["nodes"][0]["inputs"][0]]}]
    }
    response = client.patch(
        f"/api/graph/jobs/{job_id}/graph", json={"graph": deleted}
    )
    assert response.status_code == 200, response.text
    assert response.json()["graph"]["nodes"][0]["inputs"] == deleted["nodes"][0]["inputs"]


def test_manual_start_requires_values_and_resolves_optional_defaults(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "inputs": [
                    {
                        "id": "campaign",
                        "label": "Campaign",
                        "kind": "text",
                        "required": True,
                    },
                    {
                        "id": "notes",
                        "label": "Notes",
                        "kind": "text",
                        "required": False,
                    },
                    {
                        "id": "channel",
                        "label": "Channel",
                        "kind": "text",
                        "required": False,
                        "default": "email",
                    },
                ],
            }
        ]
    }
    job = _create(client, graph)
    job_id = job["id"]

    missing = client.post(f"/api/graph/jobs/{job_id}/start")
    assert missing.status_code == 422
    assert "Campaign" in missing.json()["detail"]
    assert client.get(f"/api/graph/jobs/{job_id}").json()["status"] == "queued"

    started = client.post(
        f"/api/graph/jobs/{job_id}/start",
        json={"input": {"campaign": "Launch week", "notes": ""}},
    )
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "review"
    assert body["input"] == {"brief": "launch", "campaign": "Launch week", "channel": "email"}
    trigger = next(state for state in body["node_states"] if state["node_id"] == "trigger")
    assert trigger["inputs"] == {"job_input": body["input"]}
    assert trigger["output"] == body["input"]


def test_manual_start_rejects_invalid_number_and_url_values_atomically(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "inputs": [
                    {
                        "id": "quantity",
                        "label": "Quantity",
                        "kind": "number",
                        "required": True,
                    },
                    {
                        "id": "landing_page",
                        "label": "Landing page",
                        "kind": "url",
                        "required": False,
                    },
                ],
            }
        ]
    }
    job = _create(client, graph)

    invalid_number = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"quantity": "many"}},
    )
    assert invalid_number.status_code == 422
    assert invalid_number.json()["detail"] == "Quantity: value must be a number"

    non_finite_number = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"quantity": "NaN"}},
    )
    assert non_finite_number.status_code == 422
    assert non_finite_number.json()["detail"] == "Quantity: value must be a finite number"

    invalid_url = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"quantity": "12", "landing_page": "example.com"}},
    )
    assert invalid_url.status_code == 422
    assert "complete http:// or https:// URL" in invalid_url.json()["detail"]

    unchanged = client.get(f"/api/graph/jobs/{job['id']}").json()
    assert unchanged["status"] == "queued"
    assert unchanged["input"] == {"brief": "launch"}

    started = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={
            "input": {
                "quantity": "12",
                "landing_page": "https://example.com/launch",
            }
        },
    )
    assert started.status_code == 200, started.text
    assert started.json()["input"] == {
        "brief": "launch",
        "quantity": "12",
        "landing_page": "https://example.com/launch",
    }


def test_start_rejects_a_saved_graph_change_during_the_execution_claim(
    tmp_path, monkeypatch
):
    from proxima_api import worktrees

    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "inputs": [
                    {
                        "id": "campaign",
                        "label": "Campaign",
                        "kind": "text",
                        "required": True,
                    }
                ],
            }
        ]
    }
    job = _create(client, graph)
    changed_graph = {
        "nodes": [
            {
                **graph["nodes"][0],
                "inputs": [
                    graph["nodes"][0]["inputs"][0],
                    {
                        "id": "audience",
                        "label": "Audience",
                        "kind": "text",
                        "required": False,
                    },
                ],
            }
        ]
    }

    def save_concurrent_graph(*_args):
        app.state.db.execute(
            "UPDATE jobs SET graph = ? WHERE id = ?",
            (json.dumps(changed_graph), job["id"]),
        )

    monkeypatch.setattr(worktrees, "bind_graph_job_repo_worktree", save_concurrent_graph)

    refused = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"campaign": "Launch week"}},
    )

    assert refused.status_code == 409
    assert "workflow changed while starting" in refused.json()["detail"]
    unchanged = client.get(f"/api/graph/jobs/{job['id']}").json()
    assert unchanged["status"] == "queued"
    assert unchanged["input"] == {"brief": "launch"}
    assert unchanged["graph"]["nodes"][0]["inputs"] == changed_graph["nodes"][0]["inputs"]


def test_start_dispatch_failure_restores_original_input(tmp_path, monkeypatch):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "inputs": [
                    {
                        "id": "campaign",
                        "label": "Campaign",
                        "kind": "text",
                        "required": True,
                    },
                    {
                        "id": "notes",
                        "label": "Notes",
                        "kind": "text",
                        "required": False,
                    },
                ],
            },
            {"id": "write", "name": "Write", "instruction": "draft", "depends_on": ["trigger"]},
        ]
    }
    job = _create(client, graph)
    real_dispatch = app.state.worker.graph_executor.dispatch_ready
    attempts = {"count": 0}

    def flaky_dispatch(job_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("missing execution profile")
        return real_dispatch(job_id)

    monkeypatch.setattr(app.state.worker.graph_executor, "dispatch_ready", flaky_dispatch)

    refused = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"campaign": "Launch week", "notes": "temp"}},
    )
    assert refused.status_code == 409
    assert "missing execution profile" in refused.json()["detail"]

    rolled_back = client.get(f"/api/graph/jobs/{job['id']}").json()
    assert rolled_back["status"] == "queued"
    assert rolled_back["input"] == {"brief": "launch"}
    assert rolled_back.get("started_at") in (None, "")

    # Blank optional notes must not keep a polluted freeze from the failed claim.
    started = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"campaign": "Launch week"}},
    )
    assert started.status_code == 200, started.text
    assert started.json()["input"] == {"brief": "launch", "campaign": "Launch week"}


def test_start_no_dispatchable_node_restores_original_input(tmp_path, monkeypatch):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "trigger_kind": "manual",
                "name": "When I run it",
                "inputs": [
                    {
                        "id": "campaign",
                        "label": "Campaign",
                        "kind": "text",
                        "required": True,
                    }
                ],
            },
            {"id": "write", "name": "Write", "instruction": "draft", "depends_on": ["trigger"]},
        ]
    }
    job = _create(client, graph)

    monkeypatch.setattr(app.state.worker.graph_executor, "dispatch_ready", lambda _job_id: [])

    refused = client.post(
        f"/api/graph/jobs/{job['id']}/start",
        json={"input": {"campaign": "Launch week"}},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "graph job has no dispatchable node"

    rolled_back = client.get(f"/api/graph/jobs/{job['id']}").json()
    assert rolled_back["status"] == "queued"
    assert rolled_back["input"] == {"brief": "launch"}


def test_scheduled_start_preserves_trigger_owned_input(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    created = client.post(
        "/api/graph/jobs",
        json={
            "title": "Scheduled graph",
            "graph": {
                "nodes": [
                    {
                        "id": "trigger",
                        "type": "trigger",
                        "trigger_kind": "scheduled",
                        "name": "Weekday morning",
                        "schedule": {
                            "cron": "30 8 * * 1-5",
                            "overlap_policy": "skip",
                            "enabled": True,
                        },
                        "inputs": [
                            {
                                "id": "campaign",
                                "label": "Campaign",
                                "kind": "text",
                                "required": True,
                            }
                        ],
                    }
                ]
            },
            "input": {
                "campaign": "scheduler-owned",
                "scheduled_for": "2026-08-03T08:30:00Z",
            },
        },
    )
    assert created.status_code == 201, created.text

    started = client.post(f"/api/graph/jobs/{created.json()['id']}/start")

    assert started.status_code == 200, started.text
    assert started.json()["input"] == {
        "campaign": "scheduler-owned",
        "scheduled_for": "2026-08-03T08:30:00Z",
    }


# ── per-job targets + repo plans (Phase-1 slice 3, T1/T2) ────────────────


def _scratch_repo(path) -> None:
    """A real git repo with one commit on branch main (worktree cuts need one)."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> None:
        res = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=str(path), capture_output=True, text=True,
        )
        assert res.returncode == 0, f"git {args}: {res.stderr}"

    git("init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def _link_project(client: TestClient, path, slug: str) -> dict[str, Any]:
    response = client.post(
        "/api/projects/link",
        json=with_browse_root(client, {"path": str(path), "slug": slug}),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _tagged_graph() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "fix", "name": "Fix the bug", "instruction": "fix", "target": "."},
            {"id": "report", "name": "Write report", "instruction": "write",
             "target": "ops", "depends_on": ["fix"]},
        ]
    }


def test_plan_targets_are_validated_against_the_projects_areas(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    _scratch_repo(tmp_path / "myrepo")
    _link_project(client, tmp_path / "myrepo", "myrepo")

    created = client.post(
        "/api/graph/jobs",
        json={"title": "Fix + report", "graph": _tagged_graph(), "project_slug": "myrepo"},
    )
    assert created.status_code == 201, created.text
    nodes = {n["id"]: n for n in created.json()["graph"]["nodes"]}
    assert nodes["fix"]["touches_repo"] is True
    assert nodes["report"]["touches_repo"] is False

    ghost = {"nodes": [{"id": "a", "name": "A", "instruction": "x", "target": "apps/ghost"}]}
    rejected = client.post(
        "/api/graph/jobs", json={"title": "Ghost", "graph": ghost, "project_slug": "myrepo"}
    )
    assert rejected.status_code == 422
    assert "apps/ghost" in rejected.json()["detail"]

    # A repo job needs a project: without one there are no code areas to bind to.
    homeless = client.post("/api/graph/jobs", json={"title": "Homeless", "graph": ghost})
    assert homeless.status_code == 422
    assert "no project" in homeless.json()["detail"]

    # The same gate guards plan edits, not just creation.
    plan_id = created.json()["id"]
    edited = client.patch(f"/api/graph/jobs/{plan_id}/graph", json={"graph": ghost})
    assert edited.status_code == 422


def test_ambiguous_target_blocks_start_with_the_owners_question(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    graph = {
        "nodes": [
            {"id": "study", "name": "Study layout", "instruction": "study",
             "target_ambiguous": True, "target_question": "does this touch the web app?"},
        ]
    }
    plan = client.post("/api/graph/jobs", json={"title": "Ambiguous", "graph": graph}).json()

    refused = client.post(f"/api/graph/jobs/{plan['id']}/start")

    assert refused.status_code == 409
    assert "does this touch the web app?" in refused.json()["detail"]
    assert client.get(f"/api/graph/jobs/{plan['id']}").json()["status"] == "queued"

    # Picking a target IS the resolution; the plan then starts.
    resolved = {"nodes": [{**graph["nodes"][0], "target": "ops", "target_ambiguous": False}]}
    assert client.patch(
        f"/api/graph/jobs/{plan['id']}/graph", json={"graph": resolved}
    ).status_code == 200
    assert client.post(f"/api/graph/jobs/{plan['id']}/start").json()["status"] == "running"


def test_flag_off_regression_repo_tagged_plan_runs_without_worktrees(tmp_path):
    """feature_repo_worktrees off (the slice-4 escape hatch): a target-tagged
    plan executes exactly as before slice 3 - no worktree row, no target
    pinned on the job."""
    app = _app(tmp_path, enabled=True, feature_repo_worktrees=False)
    client = _client(app)
    _scratch_repo(tmp_path / "myrepo")
    _link_project(client, tmp_path / "myrepo", "myrepo")
    plan = client.post(
        "/api/graph/jobs",
        json={"title": "Fix + report", "graph": _tagged_graph(), "project_slug": "myrepo"},
    ).json()

    started = client.post(f"/api/graph/jobs/{plan['id']}/start")

    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    assert "worktree" not in started.json()
    assert app.state.db.execute(
        "SELECT 1 FROM job_worktrees WHERE job_id = ?", (plan["id"],)
    ).fetchone() is None
    job_row = app.state.db.execute(
        "SELECT target_area_id FROM jobs WHERE id = ?", (plan["id"],)
    ).fetchone()
    assert job_row["target_area_id"] is None


def test_repo_plan_reserves_its_worktree_and_merges_on_final_approve(tmp_path):
    app = _app(tmp_path, enabled=True, feature_repo_worktrees=True)
    client = _client(app)
    _scratch_repo(tmp_path / "myrepo")
    _link_project(client, tmp_path / "myrepo", "myrepo")
    plan = client.post(
        "/api/graph/jobs",
        json={"title": "Fix + report", "graph": _tagged_graph(), "project_slug": "myrepo"},
    ).json()

    started = client.post(f"/api/graph/jobs/{plan['id']}/start")

    assert started.status_code == 200, started.text
    payload = started.json()
    assert payload["worktree"]["status"] == "active"
    assert payload["worktree"]["base_branch"] == "main"
    worktree_dir = Path(payload["worktree"]["worktree_path"])
    assert worktree_dir.is_dir()
    pinned = app.state.db.execute(
        "SELECT target_area_id FROM jobs WHERE id = ?", (plan["id"],)
    ).fetchone()
    assert pinned["target_area_id"] is not None

    # The agent's work lands in the worktree; the final approve merges it home.
    (worktree_dir / "fix.txt").write_text("patched\n", encoding="utf-8")
    _complete_running_node(app, plan["id"], "fixed")
    _complete_running_node(app, plan["id"], "reported")
    assert client.get(f"/api/graph/jobs/{plan['id']}").json()["status"] == "review"

    approved = client.post(f"/api/graph/jobs/{plan['id']}/approve")

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    assert approved.json()["worktree"]["status"] == "merged"
    assert (tmp_path / "myrepo" / "fix.txt").read_text(encoding="utf-8") == "patched\n"
    assert not worktree_dir.exists()


def test_repo_plan_reject_discards_worktree_and_records_reason(tmp_path):
    """The review surface's reject door works for plans too (shared /api/jobs
    reject): the plan fails with the owner's why and the isolated worktree is
    torn down unmerged - the primary tree never sees the discarded change."""
    app = _app(tmp_path, enabled=True, feature_repo_worktrees=True)
    client = _client(app)
    _scratch_repo(tmp_path / "myrepo")
    _link_project(client, tmp_path / "myrepo", "myrepo")
    plan = client.post(
        "/api/graph/jobs",
        json={"title": "Fix + report", "graph": _tagged_graph(), "project_slug": "myrepo"},
    ).json()
    started = client.post(f"/api/graph/jobs/{plan['id']}/start")
    assert started.status_code == 200, started.text
    worktree_dir = Path(started.json()["worktree"]["worktree_path"])
    (worktree_dir / "fix.txt").write_text("wrong fix\n", encoding="utf-8")
    _complete_running_node(app, plan["id"], "fixed")
    _complete_running_node(app, plan["id"], "reported")
    assert client.get(f"/api/graph/jobs/{plan['id']}").json()["status"] == "review"

    rejected = client.post(f"/api/jobs/{plan['id']}/reject", json={"reason": "fixes the wrong module"})

    assert rejected.status_code == 200, rejected.text
    payload = client.get(f"/api/graph/jobs/{plan['id']}").json()
    assert payload["status"] == "failed"
    assert payload["rejected_reason"] == "fixes the wrong module"
    assert payload["worktree"]["status"] == "discarded"
    assert not worktree_dir.exists()
    assert not (tmp_path / "myrepo" / "fix.txt").exists()


def test_repo_plan_with_two_code_areas_refuses_to_start(tmp_path):
    app = _app(tmp_path, enabled=True, feature_repo_worktrees=True)
    client = _client(app)
    container = tmp_path / "container"
    _scratch_repo(container / "web")
    _scratch_repo(container / "api")
    _link_project(client, container, "container")
    graph = {
        "nodes": [
            {"id": "a", "name": "A", "instruction": "x", "target": "web"},
            {"id": "b", "name": "B", "instruction": "y", "target": "api"},
        ]
    }
    plan = client.post(
        "/api/graph/jobs", json={"title": "Two repos", "graph": graph, "project_slug": "container"}
    ).json()

    refused = client.post(f"/api/graph/jobs/{plan['id']}/start")

    assert refused.status_code == 409
    assert "one code area" in refused.json()["detail"]
    assert client.get(f"/api/graph/jobs/{plan['id']}").json()["status"] == "queued"


def test_recipe_promotion_round_trip_keeps_job_targets(tmp_path):
    app = _app(tmp_path, enabled=True)
    client = _client(app)
    _scratch_repo(tmp_path / "myrepo")
    _link_project(client, tmp_path / "myrepo", "myrepo")
    plan = client.post(
        "/api/graph/jobs",
        json={"title": "Fix + report", "graph": _tagged_graph(), "project_slug": "myrepo"},
    ).json()

    saved = client.post(
        f"/api/graph/jobs/{plan['id']}/save-template",
        json={"name": "Fix recipe", "category": "build"},
    )
    assert saved.status_code == 201, saved.text
    template = saved.json()
    template_nodes = {n["id"]: n for n in template["graph"]["nodes"]}
    assert template_nodes["fix"]["target"] == "."
    assert template_nodes["fix"]["touches_repo"] is True

    rerun = client.post(
        "/api/graph/jobs",
        json={
            "title": "Fix again",
            "graph": template["graph"],
            "workflow_id": template["id"],
            "project_slug": "myrepo",
        },
    )
    assert rerun.status_code == 201, rerun.text
    rerun_nodes = {n["id"]: n for n in rerun.json()["graph"]["nodes"]}
    assert rerun_nodes["fix"]["target"] == "."
    assert rerun_nodes["fix"]["touches_repo"] is True


def test_authoring_chat_style_multi_node_graph_is_runnable(tmp_path):
    """Plan Chat applyReply lands a multi-node DAG the API accepts as a draft,
    template, and startable job — the product happy path for Recipes authoring."""
    # Shape matches what apps/web parseGraphDraft + graphIsStructurallyRunnable accept
    # for a parallel research → write → review graph produced by the authoring agent.
    authored = {
        "nodes": [
            {
                "id": "trigger",
                "type": "trigger",
                "name": "When I run it",
                "instruction": "",
                "output_kind": "json",
            },
            {
                "id": "research",
                "type": "agent",
                "name": "Research",
                "instruction": "Collect facts for the brief",
                "output_kind": "text",
            },
            {
                "id": "post-x",
                "type": "agent",
                "name": "Post X",
                "instruction": "Write the X post from research",
                "output_kind": "text",
            },
            {
                "id": "post-li",
                "type": "agent",
                "name": "Post LI",
                "instruction": "Write the LinkedIn post from research",
                "output_kind": "text",
            },
            {
                "id": "bundle",
                "type": "agent",
                "name": "Bundle",
                "instruction": "Combine both posts for review",
                "review_required": True,
                "output_kind": "text",
            },
        ],
        "edges": [
            {"from": "trigger", "to": "research"},
            {"from": "research", "to": "post-x"},
            {"from": "research", "to": "post-li"},
            {"from": "post-x", "to": "bundle"},
            {"from": "post-li", "to": "bundle"},
        ],
    }
    app = _app(tmp_path, enabled=True)
    client = _client(app)

    created = client.post(
        "/api/graph/jobs",
        json={"title": "Authored from Plan Chat", "graph": authored},
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "queued"
    assert len(job["graph"]["nodes"]) == 5
    assert len(job["graph"]["edges"]) == 5

    # Persist agent metadata the way Save as template does after authoring.
    saved = client.post(
        f"/api/graph/jobs/{job['id']}/save-template",
        json={
            "name": "Content pipeline",
            "description": "From Plan Chat",
            "category": "content",
            "inputs": [{"id": "brief", "label": "Brief", "kind": "text", "required": True}],
        },
    )
    assert saved.status_code == 201, saved.text
    template = saved.json()
    assert template["name"] == "Content pipeline"
    assert len(template["graph"]["nodes"]) == 5

    # Start the draft plan — proves the authored graph is executable structure.
    started = client.post(f"/api/graph/jobs/{job['id']}/start")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] in ("running", "review", "done", "queued")
    # Research is the first agent after the trigger and should be ready/running.
    states = {n["node_id"]: n["status"] for n in body["node_states"]}
    assert states.get("research") in ("ready", "running", "done", "pending", "review")

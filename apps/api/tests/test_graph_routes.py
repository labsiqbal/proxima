from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from proxima_api.main import create_app


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
    response = client.post("/api/projects/link", json={"path": str(path), "slug": slug})
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
    """feature_repo_worktrees off (the default): a target-tagged plan executes
    exactly as before slice 3 - no worktree row, no target pinned on the job."""
    app = _app(tmp_path, enabled=True)
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

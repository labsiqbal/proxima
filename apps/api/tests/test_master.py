from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.master_runtime import master_capacity, execute_tool, handle_master_response
from proxima_api.job_checkpoints import create_checkpoint, restore_checkpoint
from proxima_api.main import create_app
from proxima_api import app_settings, turn_restore


def _client(tmp_path: Path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_master_orchestrator": True,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    created = client.post("/api/projects", json={"slug": "master-project", "name": "Master project"})
    assert created.status_code == 201
    return app, client


def test_master_desk_creates_hidden_system_identity(
    tmp_path: Path, monkeypatch
):
    app, client = _client(tmp_path)

    desk = client.get("/api/master/desk")

    assert desk.status_code == 200
    assert desk.json()["session"]["mode"] == "master"
    assert desk.json()["capacity"] == {"running": 0, "max": 3, "free": 3, "queued": 0}
    assert client.get("/api/sessions").json()["sessions"] == []
    assert [profile["name"] for profile in client.get("/api/profiles").json()["profiles"]] == ["Default"]
    master_profile = app.state.db.execute(
        "SELECT id, name, system_kind FROM profiles WHERE system_kind = 'master'"
    ).fetchone()
    assert {key: master_profile[key] for key in ("name", "system_kind")} == {"name": "Master", "system_kind": "master"}
    assert client.post("/api/sessions", json={"title": "Imposter", "profile_id": master_profile["id"]}).status_code == 404
    origin_master_session_id = desk.json()["session"]["id"]
    assert client.patch(f"/api/sessions/{origin_master_session_id}", json={"title": "Imposter"}).status_code == 409
    assert client.delete(f"/api/sessions/{origin_master_session_id}").status_code == 409
    master_run = client.post(
        "/api/master/messages", json={"content": "List current work"}
    )
    assert master_run.status_code == 409
    assert (
        master_run.json()["detail"]["code"]
        == "master_runner_not_conforming"
    )
    assert client.put("/api/settings/master", json={"runner_id": "not-a-runner"}).status_code == 422
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda runner_id: (runner_id == "codex", ""),
    )
    switched = client.put("/api/settings/master", json={"runner_id": "codex"})
    assert switched.status_code == 200
    assert switched.json()["runner_id"] == "codex"
    assert app.state.db.execute(
        "SELECT COUNT(*) AS c FROM profiles WHERE system_kind='master'"
    ).fetchone()["c"] == 1


def test_alpha_route_alias_reads_the_same_master_records(tmp_path: Path):
    app, client = _client(tmp_path)
    master = client.get("/api/master/desk")
    legacy = client.get("/api/alpha/desk")

    assert master.status_code == legacy.status_code == 200
    assert master.json()["session"]["id"] == legacy.json()["session"]["id"]
    assert master.json()["session"]["mode"] == "master"
    assert legacy.json()["session"]["mode"] == "alpha"
    assert master.json()["jobs"] == legacy.json()["jobs"]
    assert "master_run" in master.json()
    assert "alpha_run" in legacy.json()
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM sessions WHERE mode = 'master'"
    ).fetchone()[0] == 1


def test_master_message_acceptance_returns_canonical_durable_message(
    tmp_path: Path, monkeypatch
):
    app, client = _client(tmp_path)
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda _runner_id: (True, ""),
    )

    response = client.post(
        "/api/master/messages",
        json={"content": "Keep this turn in durable order"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["message"] == {
        "id": body["message"]["id"],
        "role": "user",
        "content": "Keep this turn in durable order",
        "author": "owner",
        "run_id": body["run_id"],
        "created_at": body["message"]["created_at"],
    }
    stored = app.state.db.execute(
        "SELECT id, run_id FROM messages WHERE id = ?",
        (body["message"]["id"],),
    ).fetchone()
    assert dict(stored) == {
        "id": body["message"]["id"],
        "run_id": body["run_id"],
    }
    assert client.get("/api/master/desk").json()["event_cursor"] > 0


def test_master_desk_cursor_never_leads_the_snapshot(tmp_path: Path):
    app, client = _client(tmp_path)
    session_id = client.get("/api/master/desk").json()["session"]["id"]
    app.state.db.execute(
        "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) "
        "VALUES (NULL, ?, NULL, 1, 'note', '{}')",
        (session_id,),
    )
    app.state.db.commit()
    latest = app.state.db.execute(
        "SELECT MAX(id) AS id FROM events WHERE session_id = ?",
        (session_id,),
    ).fetchone()["id"]

    cursor = client.get("/api/master/desk").json()["event_cursor"]

    assert cursor == latest


def test_multi_dispatch_rolls_back_every_job_when_one_task_is_invalid(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]

    result = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "tasks": [
                {"title": "Valid first task", "brief": "Do valid work", "project_slug": project["slug"]},
                {"title": "Missing brief", "project_slug": project["slug"]},
            ],
        },
    )

    assert result["ok"] is False
    assert app.state.db.execute("SELECT COUNT(*) AS c FROM jobs WHERE origin_master_session_id IS NOT NULL").fetchone()["c"] == 0


def test_master_batch_dispatch_uses_durable_idempotent_dependency_dag(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    args = {
        "idempotency_key": "master-dag-timeout",
        "tasks": [
            {
                "key": "research",
                "title": "Research",
                "brief": "Collect evidence",
                "project_slug": project["slug"],
            },
            {
                "key": "report",
                "title": "Report",
                "brief": "Write the report",
                "project_slug": project["slug"],
                "depends_on": ["research"],
            },
        ],
    }

    first = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        args,
    )
    repeated = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        args,
    )

    assert first["ok"] is repeated["ok"] is True
    assert [job["id"] for job in first["result"]["jobs"]] == [
        job["id"] for job in repeated["result"]["jobs"]
    ]
    assert [job["status"] for job in first["result"]["jobs"]] == [
        "running",
        "queued",
    ]
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM task_delegations"
    ).fetchone()[0] == 2
    dependency = app.state.db.execute(
        "SELECT * FROM task_dependencies"
    ).fetchone()
    assert dependency["task_id"] == first["result"]["jobs"][1]["id"]
    assert dependency["depends_on_task_id"] == first["result"]["jobs"][0]["id"]
    blocked = app.state.db.execute(
        "SELECT blocked_reason FROM jobs WHERE id = ?",
        (dependency["task_id"],),
    ).fetchone()["blocked_reason"]
    assert "currently running" in blocked
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_duplicate_dispatch_envelopes_reject_the_round_before_mutation(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (project["slug"],)
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', ?) "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
                "Delegate once",
            ),
        ).fetchone()
    )
    envelope = json.dumps(
        {
            "name": "delegate_tasks",
            "arguments": {
                "start": False,
                "tasks": [
                    {
                        "title": "One durable Task",
                        "brief": "Create only one",
                        "container_id": container_id,
                        "area_id": area_id,
                        "profile_id": profile_id,
                    }
                ],
            },
        }
    )

    calls = handle_master_response(
        app,
        app.state.db,
        run,
        f"<proxima-tool>{envelope}</proxima-tool>\n"
        f"<proxima-tool>{envelope}</proxima-tool>",
    )

    assert calls[0]["error"]["code"] == "duplicate_tool_call"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM jobs WHERE origin_master_session_id = ?",
        (desk["session"]["id"],),
    ).fetchone()[0] == 0


def test_master_in_process_multi_dispatch_is_autonomous_checkpointed_and_scoped_to_three(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    tasks = [
        {"title": f"Slice {index}", "brief": f"Do independent slice {index}", "project_slug": project["slug"]}
        for index in range(4)
    ]

    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {"tasks": tasks},
    )

    assert result["ok"] is True
    assert len(result["result"]["jobs"]) == 4
    rows = app.state.db.execute(
        "SELECT id, input, origin_master_session_id FROM jobs ORDER BY id"
    ).fetchall()
    assert {json.loads(row["input"])["execution_policy"] for row in rows} == {"autonomous"}
    assert {row["origin_master_session_id"] for row in rows} == {desk["session"]["id"]}
    assert app.state.db.execute("SELECT COUNT(*) FROM job_checkpoints").fetchone()[0] == 4
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'master.job.create'"
    ).fetchone()[0] == 4
    run_ids = [row["id"] for row in app.state.db.execute("SELECT id FROM runs ORDER BY id").fetchall()]
    assert all(app.state.worker._auto_approve_on(run_id) for run_id in run_ids)

    claimed = [app.state.worker.claim_run() for _ in range(3)]
    assert all(claimed)
    assert app.state.worker.claim_run() is None
    assert master_capacity(app.state.db, desk["session"]["id"])["running"] == 3


def test_master_capacity_counts_each_queued_worker_run(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [{"title": "Parallel plan", "brief": "Run branches", "project_slug": project["slug"]}],
        },
    )["result"]["jobs"][0]
    session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?", (job["id"],)
    ).fetchone()["session_id"]
    first_run = app.state.db.execute(
        "SELECT * FROM runs WHERE session_id = ?", (session_id,)
    ).fetchone()
    for _ in range(2):
        app.state.db.execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt) "
            "VALUES (?, ?, ?, ?, ?, 'queued', 'branch')",
            (
                session_id,
                first_run["project_id"],
                first_run["user_id"],
                first_run["profile_id"],
                first_run["runner_id"],
            ),
        )

    assert master_capacity(app.state.db, desk["session"]["id"])["queued"] == 3


def test_master_starts_saved_graph_plan_through_in_process_engine(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    project_id = app.state.db.execute("SELECT id FROM projects WHERE slug='master-project'").fetchone()["id"]
    workflow_id = app.state.db.execute(
        "INSERT INTO workflows(project_id, name, graph, steps, created_by) VALUES (?, 'Saved plan', ?, '[]', ?)",
        (
            project_id,
            json.dumps({"nodes": [{"id": "one", "name": "One", "instruction": "Do one", "output_kind": "text"}], "edges": []}),
            owner_id,
        ),
    ).lastrowid

    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "start_plan",
        {"workflow_id": workflow_id, "start": False},
    )

    assert result["ok"] is True
    job = app.state.db.execute("SELECT * FROM jobs WHERE id = ?", (result["result"]["job"]["id"],)).fetchone()
    assert job["engine"] == "graph"
    assert job["origin_master_session_id"] == desk["session"]["id"]
    assert app.state.db.execute("SELECT COUNT(*) FROM job_checkpoints WHERE job_id = ?", (job["id"],)).fetchone()[0] == 1


def test_checkpoint_restore_never_resets_the_shared_project_checkout(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "owner@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Owner"], check=True)
    (root / "state.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "before"], check=True)
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {"start": False, "tasks": [{"title": "Safe restore", "brief": "Work", "project_slug": project["slug"]}]},
    )["result"]["jobs"][0]
    checkpoint = create_checkpoint(app.state.db, job["id"])
    assert checkpoint["git_refs"][0]["restore_strategy"] == "reference_only"

    (root / "state.txt").write_text("later\n")
    subprocess.run(["git", "-C", str(root), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "later"], check=True)
    later_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
    ).stdout.strip()
    app.state.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))

    restored = restore_checkpoint(app.state.db, checkpoint["id"], confirmed=True)

    assert restored["git_restored"] == []
    assert subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
    ).stdout.strip() == later_head
    assert (root / "state.txt").read_text() == "later\n"


def test_master_repo_checkpoint_captures_and_restores_the_job_worktree(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "owner@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Owner"], check=True)
    (root / "state.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "before"], check=True)
    areas = client.post(f"/api/projects/{project['slug']}/areas/detect").json()
    area_id = areas["code_areas"][0]["id"]

    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [{
                "title": "Restorable repo work",
                "brief": "Change the repo",
                "project_slug": project["slug"],
                "target_area_id": area_id,
            }],
        },
    )["result"]["jobs"][0]
    checkpoint = dict(app.state.db.execute(
        "SELECT * FROM job_checkpoints WHERE job_id = ? ORDER BY id LIMIT 1", (job["id"],)
    ).fetchone())
    checkpoint["git_refs"] = json.loads(checkpoint["git_refs_json"])
    assert checkpoint["git_refs"][0]["restore_strategy"] == "worktree_reset"
    worktree = Path(checkpoint["git_refs"][0]["worktree_path"])
    (worktree / "state.txt").write_text("after\n")
    subprocess.run(["git", "-C", str(worktree), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "after"], check=True)
    app.state.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))

    restored = restore_checkpoint(app.state.db, checkpoint["id"], confirmed=True)

    assert restored["git_restored"] == [str(worktree)]
    assert (worktree / "state.txt").read_text() == "before\n"


def test_checkpoint_fifo_keeps_thirty_unpinned(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {"tasks": [{"title": "One", "brief": "Do one", "project_slug": project["slug"]}], "start": False},
    )
    job_id = result["result"]["jobs"][0]["id"]

    for _ in range(31):
        create_checkpoint(app.state.db, job_id)

    rows = app.state.db.execute(
        "SELECT id FROM job_checkpoints ORDER BY created_at, id"
    ).fetchall()
    assert len(rows) == 30
    assert rows[0]["id"] == 2


def test_turn_restore_previews_paths_and_restores_pre_turn_content(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    target = root / "notes.txt"
    target.write_text("before")
    session = client.post(
        "/api/sessions", json={"title": "Hands on", "project_slug": project["slug"]}
    ).json()
    before = turn_restore.capture_snapshot(root)
    target.write_text("after")
    message_id = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, 'assistant', 'Changed it')",
        (session["id"],),
    ).lastrowid
    turn_restore.record_journal(
        app.state.db,
        message_id=message_id,
        session_id=session["id"],
        root=root,
        before=before,
    )

    preview = client.get(f"/api/chat/messages/{message_id}/restore-turn")
    restored = client.post(
        f"/api/chat/messages/{message_id}/restore-turn",
        json={"confirm": True},
    )

    assert preview.status_code == 200
    assert preview.json()["paths"] == ["notes.txt"]
    assert restored.status_code == 200
    assert target.read_text() == "before"
    assert client.get(f"/api/chat/messages/{message_id}/restore-turn").status_code == 404


def test_unattended_supervisor_enforces_turn_budget_and_surfaces_clean_stop(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {"title": "Queued A", "brief": "Do A", "project_slug": project["slug"]},
                {"title": "Queued B", "brief": "Do B", "project_slug": project["slug"]},
            ],
            "start": False,
        },
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True, budget_turns=1)

    first = app.state.master_supervisor.tick()
    second = app.state.master_supervisor.tick()

    assert len(first["started"]) == 1
    assert second["stopped"] == "turn budget exhausted"
    assert app_settings.get_master_settings(app.state.worker_db)["unattended"] is False
    attention = client.get("/api/attention").json()["items"]
    assert any(item["kind"] == "master_budget" for item in attention)


def test_script_trust_attention_shows_hash_and_uses_in_process_approval(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    project_root = Path(project["path"]) / "ops"
    (project_root / "scripts").mkdir(exist_ok=True)
    script_bytes = b"print('ok')\n"
    (project_root / "scripts" / "hello.py").write_bytes(script_bytes)
    job = client.post(
        "/api/graph/jobs",
        json={
            "title": "Script plan",
            "project_slug": project["slug"],
            "graph": {
                "nodes": [{"id": "script", "name": "Script", "type": "script", "command": "hello.py", "output_kind": "text"}],
                "edges": [],
            },
        },
    ).json()
    import hashlib

    digest = hashlib.sha256(script_bytes).hexdigest()
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))
    app.state.db.execute(
        "UPDATE node_states SET status='failed', error=? WHERE job_id=? AND node_id='script'",
        (f"script_approval_required: scripts/hello.py (sha256 {digest})", job["id"]),
    )

    attention = client.get("/api/attention").json()["items"]
    item = next(item for item in attention if item["kind"] == "script_trust")
    assert digest in item["title"]
    assert item["inline_ok"] is True
    approved = client.post(f"/api/attention/{item['id']}/act", json={"action": "approve"})
    assert approved.status_code == 200
    assert app.state.db.execute(
        "SELECT content_hash FROM script_trust WHERE project_id = (SELECT id FROM projects WHERE slug=?)",
        (project["slug"],),
    ).fetchone()["content_hash"] == digest


def test_permission_attention_closes_when_choice_is_delivered(tmp_path: Path):
    app, client = _client(tmp_path)
    session = client.post("/api/sessions", json={"title": "Permission"}).json()
    user = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    profile = app.state.db.execute("SELECT * FROM profiles WHERE is_default = 1").fetchone()
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id, user_id, profile_id, runner_id, status, prompt) "
        "VALUES (?, ?, ?, ?, 'running', 'test')",
        (session["id"], user["id"], profile["id"], profile["runner_id"]),
    ).lastrowid
    app.state.db.execute(
        "INSERT INTO attention_items(kind, title, target_json, inline_ok, actions_json, source_key) "
        "VALUES ('permission_job', 'Allow write', '{}', 1, '[\"approve\"]', ?)",
        (f"permission:{run_id}:request-1",),
    )

    class Proc:
        def resolve_permission(self, request_id, option_id):
            return request_id == "request-1" and option_id == "allow"

    app.state.worker.active_runs[run_id] = (Proc(), "session")
    assert app.state.worker.resolve_permission(run_id, "request-1", "allow") is True
    assert app.state.db.execute(
        "SELECT status FROM attention_items WHERE source_key = ?",
        (f"permission:{run_id}:request-1",),
    ).fetchone()["status"] == "resolved"


def test_disallowed_master_tool_returns_structured_error(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]

    result = execute_tool(app.state.db, app, {"id": owner_id}, desk["session"]["id"], "wipe_database", {})

    assert result == {
        "ok": False,
        "tool": "wipe_database",
        "error": {"code": "tool_not_allowed", "message": "Master tool 'wipe_database' is not allowed"},
    }

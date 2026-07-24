from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.alpha_runtime import alpha_capacity, execute_tool, handle_alpha_response
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
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    created = client.post("/api/projects", json={"slug": "alpha-project", "name": "Alpha project"})
    assert created.status_code == 201
    return app, client


def test_alpha_desk_creates_hidden_system_identity(tmp_path: Path):
    app, client = _client(tmp_path)

    desk = client.get("/api/alpha/desk")

    assert desk.status_code == 200
    assert desk.json()["session"]["mode"] == "alpha"
    assert desk.json()["capacity"] == {"running": 0, "max": 3, "free": 3, "queued": 0}
    assert client.get("/api/sessions").json()["sessions"] == []
    assert [profile["name"] for profile in client.get("/api/profiles").json()["profiles"]] == ["Default"]
    alpha_profile = app.state.db.execute(
        "SELECT id, name, system_kind FROM profiles WHERE system_kind = 'alpha'"
    ).fetchone()
    assert {key: alpha_profile[key] for key in ("name", "system_kind")} == {"name": "Alpha", "system_kind": "alpha"}
    assert client.post("/api/sessions", json={"title": "Imposter", "profile_id": alpha_profile["id"]}).status_code == 404
    alpha_session_id = desk.json()["session"]["id"]
    assert client.patch(f"/api/sessions/{alpha_session_id}", json={"title": "Imposter"}).status_code == 409
    assert client.delete(f"/api/sessions/{alpha_session_id}").status_code == 409
    alpha_run = client.post("/api/alpha/messages", json={"content": "List current work"}).json()
    assert app.state.worker._auto_approve_on(alpha_run["run_id"]) is True
    run_row = dict(app.state.db.execute("SELECT * FROM runs WHERE id=?", (alpha_run["run_id"],)).fetchone())
    results = handle_alpha_response(
        app,
        app.state.db,
        run_row,
        '<proxima-tool>{"name":"list_projects","arguments":{}}</proxima-tool>',
    )
    assert results[0]["ok"] is True
    continuation = app.state.db.execute(
        "SELECT kind, prompt FROM runs WHERE session_id=? ORDER BY id DESC LIMIT 1",
        (alpha_session_id,),
    ).fetchone()
    assert continuation["kind"] == "alpha_tool_1"
    assert "alpha-project" in continuation["prompt"]
    assert client.put("/api/settings/alpha", json={"runner_id": "not-a-runner"}).status_code == 422
    switched = client.put("/api/settings/alpha", json={"runner_id": "codex"})
    assert switched.status_code == 200
    assert switched.json()["runner_id"] == "codex"
    assert app.state.db.execute(
        "SELECT COUNT(*) AS c FROM profiles WHERE system_kind='alpha'"
    ).fetchone()["c"] == 1


def test_multi_dispatch_rolls_back_every_job_when_one_task_is_invalid(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/alpha/desk").json()
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
    assert app.state.db.execute("SELECT COUNT(*) AS c FROM jobs WHERE alpha_session_id IS NOT NULL").fetchone()["c"] == 0


def test_alpha_in_process_multi_dispatch_is_autonomous_checkpointed_and_scoped_to_three(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/alpha/desk").json()
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
        "SELECT id, input, alpha_session_id FROM jobs ORDER BY id"
    ).fetchall()
    assert {json.loads(row["input"])["execution_policy"] for row in rows} == {"autonomous"}
    assert {row["alpha_session_id"] for row in rows} == {desk["session"]["id"]}
    assert app.state.db.execute("SELECT COUNT(*) FROM job_checkpoints").fetchone()[0] == 4
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'alpha.job.create'"
    ).fetchone()[0] == 4
    run_ids = [row["id"] for row in app.state.db.execute("SELECT id FROM runs ORDER BY id").fetchall()]
    assert all(app.state.worker._auto_approve_on(run_id) for run_id in run_ids)

    claimed = [app.state.worker.claim_run() for _ in range(3)]
    assert all(claimed)
    assert app.state.worker.claim_run() is None
    assert alpha_capacity(app.state.db, desk["session"]["id"])["running"] == 3


def test_alpha_starts_saved_graph_plan_through_in_process_engine(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/alpha/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    project_id = app.state.db.execute("SELECT id FROM projects WHERE slug='alpha-project'").fetchone()["id"]
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
    assert job["alpha_session_id"] == desk["session"]["id"]
    assert app.state.db.execute("SELECT COUNT(*) FROM job_checkpoints WHERE job_id = ?", (job["id"],)).fetchone()[0] == 1


def test_checkpoint_restore_never_resets_the_shared_project_checkout(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/alpha/desk").json()
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


def test_checkpoint_fifo_keeps_thirty_unpinned(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/alpha/desk").json()
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
    desk = client.get("/api/alpha/desk").json()
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
    app_settings.set_alpha_settings(app.state.worker_db, unattended=True, budget_turns=1)

    first = app.state.alpha_supervisor.tick()
    second = app.state.alpha_supervisor.tick()

    assert len(first["started"]) == 1
    assert second["stopped"] == "turn budget exhausted"
    assert app_settings.get_alpha_settings(app.state.worker_db)["unattended"] is False
    attention = client.get("/api/attention").json()["items"]
    assert any(item["kind"] == "alpha_budget" for item in attention)


def test_script_trust_attention_shows_hash_and_uses_in_process_approval(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    project_root = Path(project["path"])
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


def test_disallowed_alpha_tool_returns_structured_error(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/alpha/desk").json()
    owner_id = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]

    result = execute_tool(app.state.db, app, {"id": owner_id}, desk["session"]["id"], "wipe_database", {})

    assert result == {
        "ok": False,
        "tool": "wipe_database",
        "error": {"code": "tool_not_allowed", "message": "Alpha tool 'wipe_database' is not allowed"},
    }

from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _app(tmp_path):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )


def _client(app):
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_link_completed_media_run_turns_queued_task_into_review(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    assert client.post("/api/projects", json={"slug": "alpha", "name": "Alpha"}).status_code == 201
    job = client.post("/api/jobs", json={"project_slug": "alpha", "input": {"brief": "/image launch poster", "task_kind": "image"}}).json()
    user_id = job["created_by"]
    profile = app.state.db.execute("SELECT * FROM profiles WHERE user_id=? AND is_default=1", (user_id,)).fetchone()
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id,project_id,user_id,profile_id,runner_id,status,prompt,kind,started_at,finished_at) VALUES (?,?,?,?,?,'completed',?,'media_image',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        (job["session_id"], job["project_id"], user_id, profile["id"], profile["runner_id"], "/image launch poster"),
    ).lastrowid
    app.state.db.execute(
        "INSERT INTO messages(session_id,role,content,author,run_id) VALUES (?,'assistant',?,?,?)",
        (job["session_id"], "Generated image artifact: `artifacts/media/images/task.png`.", profile["name"], run_id),
    )

    linked = client.post(f"/api/jobs/{job['id']}/link-run", json={"run_id": run_id})
    assert linked.status_code == 200
    body = linked.json()
    assert body["status"] == "review"
    assert body["steps_state"][0]["status"] == "done"
    assert body["steps_state"][0]["run_id"] == run_id
    assert "Generated image artifact" in body["steps_state"][0]["output_summary"]
    repeated = client.post(f"/api/jobs/{job['id']}/link-run", json={"run_id": run_id})
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "review"


def test_link_run_rejects_non_media_execution(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    assert client.post("/api/projects", json={"slug": "alpha", "name": "Alpha"}).status_code == 201
    job = client.post("/api/jobs", json={"project_slug": "alpha", "input": {"brief": "ordinary task"}}).json()
    profile = app.state.db.execute("SELECT * FROM profiles WHERE user_id=? AND is_default=1", (job["created_by"],)).fetchone()
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id,project_id,user_id,profile_id,runner_id,status,prompt,kind) VALUES (?,?,?,?,?,'queued',?,'chat')",
        (job["session_id"], job["project_id"], job["created_by"], profile["id"], profile["runner_id"], "ordinary task"),
    ).lastrowid
    response = client.post(f"/api/jobs/{job['id']}/link-run", json={"run_id": run_id})
    assert response.status_code == 422
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "queued"


def test_adhoc_task_uses_selected_agent_profile(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    profile = client.post("/api/profiles", json={"name": "Task Agent", "runner_id": "codex"}).json()
    job = client.post("/api/jobs", json={"profile_id": profile["id"], "input": {"brief": "selected agent"}}).json()
    session = app.state.db.execute("SELECT profile_id FROM sessions WHERE id=?", (job["session_id"],)).fetchone()
    assert session["profile_id"] == profile["id"]
    assert client.post(f"/api/jobs/{job['id']}/start").status_code == 200
    run = app.state.db.execute("SELECT profile_id FROM runs WHERE session_id=? ORDER BY id DESC LIMIT 1", (job["session_id"],)).fetchone()
    assert run["profile_id"] == profile["id"]


def test_link_failed_media_run_marks_task_failed(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    assert client.post("/api/projects", json={"slug": "alpha", "name": "Alpha"}).status_code == 201
    job = client.post("/api/jobs", json={"project_slug": "alpha", "input": {"brief": "/image launch poster", "task_kind": "image"}}).json()
    profile = app.state.db.execute("SELECT * FROM profiles WHERE user_id=? AND is_default=1", (job["created_by"],)).fetchone()
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id,project_id,user_id,profile_id,runner_id,status,prompt,kind,started_at,finished_at) VALUES (?,?,?,?,?,'failed',?,'media_image',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        (job["session_id"], job["project_id"], job["created_by"], profile["id"], profile["runner_id"], "/image launch poster"),
    ).lastrowid
    app.state.db.execute(
        "INSERT INTO messages(session_id,role,content,author,run_id) VALUES (?,'assistant',?,?,?)",
        (job["session_id"], "Media generation failed: provider unavailable", profile["name"], run_id),
    )
    linked = client.post(f"/api/jobs/{job['id']}/link-run", json={"run_id": run_id})
    assert linked.status_code == 200
    assert linked.json()["status"] == "failed"
    assert "provider unavailable" in linked.json()["steps_state"][0]["error"]


def test_autonomous_task_finishes_without_final_review(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    job = client.post("/api/jobs", json={"input": {"brief": "complete independently", "execution_policy": "autonomous"}}).json()
    assert client.post(f"/api/jobs/{job['id']}/start").status_code == 200
    run = app.state.db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY id DESC LIMIT 1", (job["session_id"],)).fetchone()
    app.state.worker._advance_job(dict(run), "Finished independently")
    completed = client.get(f"/api/jobs/{job['id']}").json()
    assert completed["status"] == "done"
    assert completed["steps_state"][0]["output_summary"] == "Finished independently"

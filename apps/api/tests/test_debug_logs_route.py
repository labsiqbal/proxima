from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def test_debug_logs_returns_journal_and_run_state(tmp_path, monkeypatch):
    class Result:
        returncode = 0
        stdout = "2026-06-27T10:00:00 proxima test log line\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        assert "journalctl" in args[0]
        assert "proxima.service" in args[0]
        assert kwargs["timeout"] == 5
        return Result()

    monkeypatch.setattr("proxima_api.routes.admin.subprocess.run", fake_run)
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})

    r = c.get("/api/debug/logs?limit=120")

    assert r.status_code == 200, r.text
    data = r.json()
    assert "test log line" in data["logs"]
    assert data["logError"] == ""
    assert data["logHint"] == ""
    assert data["serviceUnit"] == "proxima.service"
    assert data["runs"] == []
    assert data["rawActiveSessionIds"] == []
    assert data["activeRuns"] == []
    assert data["staleRuns"] == []


def test_debug_logs_uses_configured_service_name(tmp_path, monkeypatch):
    class Result:
        returncode = 0
        stdout = "2026-06-27T10:00:00 preview-proxima ready\n"
        stderr = ""

    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        return Result()

    monkeypatch.setattr("proxima_api.routes.admin.subprocess.run", fake_run)
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "service_name": "preview-proxima",
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})

    data = c.get("/api/debug/logs").json()

    assert seen and "preview-proxima.service" in seen[0]
    assert data["serviceUnit"] == "preview-proxima.service"
    assert "preview-proxima ready" in data["logs"]
    assert data["logHint"] == ""


def test_debug_logs_empty_journal_explains_service_unit(tmp_path, monkeypatch):
    class Result:
        returncode = 0
        stdout = "-- No entries --\n"
        stderr = ""

    monkeypatch.setattr("proxima_api.routes.admin.subprocess.run", lambda *args, **kwargs: Result())
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "service_name": "proxima-staging.service",
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})

    data = c.get("/api/debug/logs").json()

    assert data["serviceUnit"] == "proxima-staging.service"
    assert "proxima-staging.service" in data["logHint"]
    assert "PROXIMA_SERVICE_NAME" in data["logHint"]


def test_debug_logs_separates_stale_runs_from_active_sessions(tmp_path, monkeypatch):
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("proxima_api.routes.admin.subprocess.run", lambda *args, **kwargs: Result())
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "run_stale_seconds": 60,
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    slug = c.get("/api/projects").json()["projects"][0]["slug"]
    fresh_sid = c.post("/api/sessions", json={"title": "fresh", "project_slug": slug}).json()["id"]
    stale_sid = c.post("/api/sessions", json={"title": "stale", "project_slug": slug}).json()["id"]
    db = app.state.db
    fresh_run_id = db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, created_at, heartbeat_at) "
        "SELECT ?, s.project_id, s.owner_user_id, s.profile_id, s.runner_id, 'running', 'fresh', datetime('now'), datetime('now') FROM sessions s WHERE s.id = ?",
        (fresh_sid, fresh_sid),
    ).lastrowid
    stale_run_id = db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, created_at, heartbeat_at) "
        "SELECT ?, s.project_id, s.owner_user_id, s.profile_id, s.runner_id, 'running', 'stale', datetime('now','-10 minutes'), datetime('now','-10 minutes') FROM sessions s WHERE s.id = ?",
        (stale_sid, stale_sid),
    ).lastrowid

    data = c.get("/api/debug/logs").json()

    assert data["rawActiveSessionIds"] == [fresh_sid]
    assert [r["id"] for r in data["activeRuns"]] == [fresh_run_id]
    assert [r["id"] for r in data["staleRuns"]] == [stale_run_id]


def test_debug_logs_does_not_mark_queued_run_stale_when_session_is_active(tmp_path, monkeypatch):
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("proxima_api.routes.admin.subprocess.run", lambda *args, **kwargs: Result())
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "run_stale_seconds": 60,
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    slug = c.get("/api/projects").json()["projects"][0]["slug"]
    sid = c.post("/api/sessions", json={"title": "active", "project_slug": slug}).json()["id"]
    db = app.state.db
    running_id = db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, created_at, heartbeat_at) "
        "SELECT ?, s.project_id, s.owner_user_id, s.profile_id, s.runner_id, 'running', 'active', datetime('now'), datetime('now') FROM sessions s WHERE s.id = ?",
        (sid, sid),
    ).lastrowid
    queued_id = db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, created_at) "
        "SELECT ?, s.project_id, s.owner_user_id, s.profile_id, s.runner_id, 'queued', 'waiting', datetime('now','-10 minutes') FROM sessions s WHERE s.id = ?",
        (sid, sid),
    ).lastrowid

    data = c.get("/api/debug/logs").json()

    assert [r["id"] for r in data["activeRuns"]] == [running_id]
    assert queued_id not in [r["id"] for r in data["staleRuns"]]


def test_debug_logs_reports_orphaned_running_jobs(tmp_path, monkeypatch):
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("proxima_api.routes.admin.subprocess.run", lambda *args, **kwargs: Result())
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    job = c.post("/api/jobs", json={"input": {"brief": "orphaned job"}}).json()
    app.state.db.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job["id"],))

    data = c.get("/api/debug/logs").json()

    assert [j["id"] for j in data["orphanedJobs"]] == [job["id"]]


def test_reap_orphaned_jobs_marks_running_jobs_failed(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    job = c.post("/api/jobs", json={"input": {"brief": "orphaned job"}}).json()
    app.state.db.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job["id"],))

    r = c.post("/api/debug/reap-orphaned-jobs")

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 1}
    updated = c.get(f"/api/jobs/{job['id']}").json()
    assert updated["status"] == "failed"
    assert updated["steps_state"][0]["status"] == "failed"
    assert updated["steps_state"][0]["error"] == "Job stalled (no active run)"

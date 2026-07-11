from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def test_user_can_create_multiple_hermes_profiles(tmp_path):
    app = create_app({"database_path": str(tmp_path / "proxima.db"), "workspace_root": str(tmp_path / "workspace"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    client = TestClient(app)
    auto = client.post("/auth/auto").json()
    owner = auto["user"]["username"]
    headers = {"Authorization": f"Bearer {auto['token']}"}

    created = client.post("/api/profiles", headers=headers, json={"slug": "acme", "name": "Acme", "default_model": "test/model"})
    assert created.status_code == 201
    assert created.json()["slug"] == "acme"
    body = client.get("/api/profiles", headers=headers).json()
    assert [p["slug"] for p in body["profiles"]] == ["default", "acme"]
    assert (tmp_path / "workspace" / "hermes-profiles" / owner / "acme").exists()


def test_run_creation_is_async_and_persists_events(tmp_path):
    app = create_app({"database_path": str(tmp_path / "proxima.db"), "workspace_root": str(tmp_path / "workspace"), "projectctl_path": "/usr/bin/true", "start_worker": False})
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {client.post('/auth/auto').json()['token']}"}
    session = client.post("/api/sessions", headers=headers, json={"title": "Async"}).json()

    run = client.post(f"/api/sessions/{session['id']}/runs", headers=headers, json={"message": "hello"})
    assert run.status_code == 202
    assert run.json()["status"] == "queued"
    events = client.get(f"/api/sessions/{session['id']}/events", headers=headers).json()["events"]
    assert events[0]["type"] == "run.queued"

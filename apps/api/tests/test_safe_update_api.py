from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _client(tmp_path, **config):
    app = create_app({"database_path": str(tmp_path / "db.sqlite"), "workspace_root": str(tmp_path / "work"), "start_worker": False, **config})
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {client.post('/auth/auto').json()['token']}"
    return client


def test_safe_update_routes_are_disabled_by_default(tmp_path):
    response = _client(tmp_path).get("/api/self-updates/capability")
    assert response.status_code == 503
    assert response.json()["detail"]["feature"] == "safe_self_update"


def test_enabled_safe_update_refuses_unmanaged_before_creating_run(tmp_path):
    client = _client(tmp_path, feature_safe_self_update=True)
    response = client.post("/api/self-updates", json={"base_commit": "a" * 40, "candidate_commit": "b" * 40})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "safe_update_unmanaged"
    assert client.app.state.db.execute("SELECT COUNT(*) FROM self_update_runs").fetchone()[0] == 0


def test_maintenance_status_reads_no_application_truth(tmp_path):
    assert _client(tmp_path).get("/api/maintenance").json() == {"active": False, "phase": None}

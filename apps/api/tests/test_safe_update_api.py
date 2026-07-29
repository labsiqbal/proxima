from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from proxima_api.main import _config_from_env, create_app
from proxima_api.settings import safe_update_config_from_env


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


def test_maintenance_status_reads_only_configured_external_fence(tmp_path):
    fence = tmp_path / "root-owned" / "fence.json"
    fence.parent.mkdir()
    fence.write_text(
        '{"phase":"write_fenced","run_id":"' + "a" * 32 + '"}',
        encoding="utf-8",
    )
    response = _client(tmp_path, safe_update_fence_path=str(fence)).get(
        "/api/maintenance"
    )
    assert response.json() == {
        "active": True,
        "phase": "write_fenced",
        "run_id": "a" * 32,
    }


def test_maintenance_status_fails_closed_for_invalid_utf8_fence(tmp_path):
    fence = tmp_path / "root-owned" / "fence.json"
    fence.parent.mkdir()
    fence.write_bytes(b"\xff\xfe")
    response = _client(tmp_path, safe_update_fence_path=str(fence)).get(
        "/api/maintenance"
    )
    assert response.status_code == 200
    assert response.json() == {
        "active": True,
        "phase": "unknown",
        "reason": "maintenance_state_unreadable",
    }


def test_production_env_loaders_share_disabled_enrollment_contract(
    tmp_path, monkeypatch
):
    fence = tmp_path / "root-owned" / "fence.json"
    monkeypatch.setenv("PROXIMA_FEATURE_SAFE_SELF_UPDATE", "1")
    monkeypatch.setenv("PROXIMA_SAFE_UPDATE_FENCE_PATH", str(fence))
    expected = {
        "feature_safe_self_update": True,
        "safe_update_fence_path": str(fence),
    }
    assert safe_update_config_from_env() == expected
    config = _config_from_env()
    assert {key: config[key] for key in expected} == expected


def test_safe_update_fence_path_must_be_absolute(tmp_path):
    with pytest.raises(ValueError, match="must be absolute"):
        create_app(
            {
                "database_path": str(tmp_path / "db.sqlite"),
                "workspace_root": str(tmp_path / "work"),
                "start_worker": False,
                "safe_update_fence_path": "relative/fence.json",
            }
        )

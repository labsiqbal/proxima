from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_external_submission_reconciles_stale_projection_rows(tmp_path):
    client = _client(tmp_path, feature_safe_self_update=True)

    class External:
        def __init__(self):
            self.responses = [
                {"accepted": True, "run_id": "a" * 32, "reason": None},
                {"accepted": True, "run_id": "b" * 32, "reason": None},
                {
                    "accepted": False,
                    "run_id": "b" * 32,
                    "reason": "safe_update_in_progress",
                },
            ]

        def submit(self, request):
            return self.responses.pop(0)

        def capability(self):
            return {"managed": True}

        def recovery_status(self, run_id):
            return {"safe": False, "run_id": run_id}

    client.app.state.safe_updates.external = External()
    request = {"base_commit": "a" * 40, "candidate_commit": "b" * 40}
    first = client.post("/api/self-updates", json=request)
    second = client.post("/api/self-updates", json=request)
    blocked = client.post("/api/self-updates", json=request)

    assert first.status_code == 202
    assert second.status_code == 202
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        "code": "safe_update_in_progress",
        "run_id": "b" * 32,
    }
    rows = client.app.state.db.execute(
        "SELECT id, phase, status FROM self_update_runs ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("a" * 32, "external_reconciled", "superseded"),
        ("b" * 32, "preflight", "requested"),
    ]


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


def test_plain_api_entrypoint_reads_fence_without_repository_imports(tmp_path):
    api_root = Path(__file__).resolve().parents[1]
    fence = tmp_path / "root-owned" / "fence.json"
    fence.parent.mkdir()
    fence.write_text(
        '{"phase":"write_fenced","run_id":"' + "a" * 32 + '"}',
        encoding="utf-8",
    )
    script = """
import json
import sys
from fastapi.testclient import TestClient
from proxima_api.main import create_app

try:
    import apps.safe_updater
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("repository package unexpectedly importable")
app = create_app({
    "database_path": sys.argv[1],
    "workspace_root": sys.argv[2],
    "start_worker": False,
    "safe_update_fence_path": sys.argv[3],
})
client = TestClient(app)
client.headers["Authorization"] = "Bearer " + client.post("/auth/auto").json()["token"]
print(json.dumps(client.get("/api/maintenance").json(), sort_keys=True))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(api_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "plain.sqlite"),
            str(tmp_path / "workspace"),
            str(fence),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "active": True,
        "phase": "write_fenced",
        "run_id": "a" * 32,
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

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.runner_specs import default_runner


def _app(tmp_path):
    return create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"), "projectctl_path": "/usr/bin/true", "start_worker": False})


def test_profile_defaults_to_ready_runner(tmp_path):
    # The auto-created default profile's runner must match what default_runner()
    # resolves to on this host (env override → first ready runner → fallback).
    # We assert the contract, not a hard-coded vendor, so the test is deterministic
    # regardless of which agent CLIs happen to be installed/logged-in here.
    c = TestClient(_app(tmp_path))
    tok = c.post("/auth/auto").json()["token"]
    profs = c.get("/api/profiles", headers={"Authorization": f"Bearer {tok}"}).json()["profiles"]
    assert profs[0]["runner_id"] == default_runner()
    assert profs[0]["is_default"] is True


def test_create_profile_with_claude_code_runner(tmp_path):
    c = TestClient(_app(tmp_path))
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = c.post("/api/profiles", headers=h, json={"name": "CC", "runner_id": "claude-code"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["runner_id"] == "claude-code"


def test_unknown_runner_rejected(tmp_path):
    c = TestClient(_app(tmp_path))
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.post("/api/profiles", headers=h, json={"name": "X", "runner_id": "bogus"}).status_code == 400


def test_change_profile_runner(tmp_path):
    c = TestClient(_app(tmp_path))
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid = c.get("/api/profiles", headers=h).json()["profiles"][0]["id"]
    r = c.patch(f"/api/profiles/{pid}", headers=h, json={"runner_id": "codex"})
    assert r.status_code == 200 and r.json()["runner_id"] == "codex"
    assert c.patch(f"/api/profiles/{pid}", headers=h, json={"runner_id": "nope"}).status_code == 400

from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.updates import is_newer, parse_version, read_local_version


def make_app(tmp_path, **overrides):
    cfg = {
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    }
    cfg.update(overrides)
    return create_app(cfg)


def auth_client(app) -> TestClient:
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# ── version identity ────────────────────────────────────────────────


def test_read_local_version_reads_version_file():
    v = read_local_version()
    assert v != "0.0.0"
    assert parse_version(v) > (0, 0, 0)


def test_read_local_version_falls_back_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("proxima_api.updates.repo_root", lambda: tmp_path)
    assert read_local_version() == "0.0.0"


def test_parse_version_variants():
    assert parse_version("0.2.0") == (0, 2, 0)
    assert parse_version("v1.10.3") == (1, 10, 3)
    assert parse_version("2.0") == (2, 0, 0)
    assert parse_version("0.3.0-rc1") == (0, 3, 0)
    assert parse_version("abc") == (0, 0, 0)
    assert parse_version("") == (0, 0, 0)


def test_is_newer():
    assert is_newer("0.3.0", "0.2.0")
    assert is_newer("v0.2.1", "0.2.0")
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.1.9", "0.2.0")
    assert not is_newer("", "0.2.0")


def test_app_version_comes_from_version_file(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app)
    body = client.get("/api/health").json()
    assert body["version"] == read_local_version()


# ── UpdateManager (Task 2) ──────────────────────────────────────────

import asyncio

from proxima_api.updates import UpdateManager, _parse_release


def make_manager(tmp_path, repo="acme/widget", token=None) -> UpdateManager:
    return UpdateManager({
        "database_path": str(tmp_path / "proxima.db"),
        "update_repo": repo,
        "update_token": token,
    })


def test_parse_release_extracts_fields():
    parsed = _parse_release({
        "tag_name": "v0.3.0",
        "body": "## What's new\n- things",
        "html_url": "https://github.com/acme/widget/releases/tag/v0.3.0",
        "published_at": "2026-07-10T00:00:00Z",
    })
    assert parsed == {
        "version": "0.3.0",
        "notes": "## What's new\n- things",
        "url": "https://github.com/acme/widget/releases/tag/v0.3.0",
        "published_at": "2026-07-10T00:00:00Z",
    }


def test_parse_release_tolerates_missing_fields():
    parsed = _parse_release({})
    assert parsed["version"] == ""
    assert parsed["notes"] == ""
    assert parsed["url"] == ""
    assert parsed["published_at"] is None


def test_private_release_check_sends_configured_token(tmp_path, monkeypatch):
    calls = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "v1.0.1"}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, headers):
            calls.update({"url": url, "headers": headers})
            return Response()

    monkeypatch.setattr("proxima_api.updates.httpx.AsyncClient", Client)
    manager = make_manager(tmp_path, repo="labsiqbal/proxima", token="private-token")

    release = asyncio.run(manager._fetch_latest_release())

    assert release["version"] == "1.0.1"
    assert calls["url"].endswith("/repos/labsiqbal/proxima/releases/latest")
    assert calls["headers"]["Authorization"] == "Bearer private-token"


def test_check_now_records_newer_release(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.current = "0.2.0"

    async def fake_fetch():
        return {"version": "0.3.0", "notes": "notes", "url": "u", "published_at": None}

    monkeypatch.setattr(m, "_fetch_latest_release", fake_fetch)
    asyncio.run(m.check_now())

    st = m.status()
    assert st["update_available"] is True
    assert st["latest"]["version"] == "0.3.0"
    assert st["checked_at"] is not None
    assert st["last_error"] is None


def test_check_now_same_version_not_available(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.current = "0.3.0"

    async def fake_fetch():
        return {"version": "0.3.0", "notes": "", "url": "", "published_at": None}

    monkeypatch.setattr(m, "_fetch_latest_release", fake_fetch)
    asyncio.run(m.check_now())

    assert m.status()["update_available"] is False


def test_check_now_failure_is_silent_and_preserves_state(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.current = "0.2.0"

    async def good_fetch():
        return {"version": "0.3.0", "notes": "", "url": "", "published_at": None}

    monkeypatch.setattr(m, "_fetch_latest_release", good_fetch)
    asyncio.run(m.check_now())

    async def bad_fetch():
        raise RuntimeError("github is down")

    monkeypatch.setattr(m, "_fetch_latest_release", bad_fetch)
    asyncio.run(m.check_now())  # must not raise

    st = m.status()
    assert st["update_available"] is True          # previous result kept
    assert st["latest"]["version"] == "0.3.0"
    assert "github is down" in st["last_error"]


def test_status_shape(tmp_path):
    st = make_manager(tmp_path).status()
    assert set(st.keys()) == {
        "current_version", "latest", "update_available", "state", "checked_at",
        "last_error", "log_tail", "apply_supported", "manual_command",
    }
    assert st["state"] == "idle"
    assert st["latest"] is None
    assert st["update_available"] is False


# ── UpdateManager (Task 3) ──────────────────────────────────────────

import json
import os
import subprocess
import time

import pytest

import proxima_api.updates as updates_mod
from proxima_api.updates import (
    NoUpdateAvailable,
    UpdateInProgress,
    UpdateUnsupported,
)


def seed_latest(m, version="0.9.0"):
    m._latest = {"version": version, "notes": "", "url": "", "published_at": None}


class FakeProc:
    pid = 4242


def test_apply_spawns_detached_updater_and_writes_marker(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.current = "0.2.0"
    seed_latest(m, "0.9.0")
    calls = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(updates_mod.subprocess, "Popen", fake_popen)

    result = m.apply()

    assert result == {"started": True, "target": "0.9.0"}
    assert calls["argv"][0] == "bash"
    assert calls["argv"][1].endswith("scripts/proxima")
    assert calls["argv"][2] == "update"
    assert calls["kwargs"]["start_new_session"] is True
    marker = json.loads(m.marker_path.read_text())
    assert marker["state"] == "running"
    assert marker["target"] == "0.9.0"
    assert marker["pid"] == 4242
    # pid 4242 is a fake and almost certainly dead in the test env, so status()
    # may already self-heal running → failed; both prove the marker is consumed.
    assert m.status()["state"] in ("running", "failed")


def test_apply_rejects_when_no_update_known(tmp_path):
    m = make_manager(tmp_path)
    m.current = "0.2.0"
    with pytest.raises(NoUpdateAvailable):
        m.apply()
    seed_latest(m, "0.2.0")  # same version is not an update
    with pytest.raises(NoUpdateAvailable):
        m.apply()


def test_apply_rejects_while_running(tmp_path):
    m = make_manager(tmp_path)
    m.current = "0.2.0"
    seed_latest(m)
    m.marker_path.parent.mkdir(parents=True, exist_ok=True)
    m.marker_path.write_text(json.dumps({
        "state": "running", "target": "0.9.0", "started_at": "x", "pid": os.getpid(),
    }))
    with pytest.raises(UpdateInProgress):
        m.apply()


def test_apply_rejects_on_windows(tmp_path, monkeypatch):
    m = make_manager(tmp_path)
    m.current = "0.2.0"
    seed_latest(m)
    monkeypatch.setattr(updates_mod.sys, "platform", "win32")
    with pytest.raises(UpdateUnsupported):
        m.apply()


def test_marker_running_with_live_pid_stays_running(tmp_path):
    m = make_manager(tmp_path)
    m.current = "0.2.0"
    m.marker_path.parent.mkdir(parents=True, exist_ok=True)
    m.marker_path.write_text(json.dumps({
        "state": "running", "target": "0.9.0", "started_at": "x", "pid": os.getpid(),
    }))
    assert m.status()["state"] == "running"


def test_marker_running_reaching_target_becomes_done_then_idle(tmp_path):
    m = make_manager(tmp_path)
    m.current = "0.9.0"  # the "new server" is running the target version
    m.marker_path.parent.mkdir(parents=True, exist_ok=True)
    m.marker_path.write_text(json.dumps({
        "state": "running", "target": "0.9.0", "started_at": "x", "pid": 1,
    }))
    m.reconcile_marker()
    assert m.status()["state"] == "idle"
    assert json.loads(m.marker_path.read_text())["state"] == "done"


def test_marker_running_dead_pid_wrong_version_becomes_failed(tmp_path):
    m = make_manager(tmp_path)
    m.current = "0.2.0"
    m.marker_path.parent.mkdir(parents=True, exist_ok=True)
    m.log_path.write_text("line1\nline2\nboom\n")
    m.marker_path.write_text(json.dumps({
        "state": "running", "target": "0.9.0", "started_at": "x", "pid": 999_999_999,
    }))
    m.reconcile_marker()
    st = m.status()
    assert st["state"] == "failed"
    assert "boom" in st["log_tail"]
    assert json.loads(m.marker_path.read_text())["state"] == "failed"


def test_marker_absent_or_corrupt_is_idle(tmp_path):
    m = make_manager(tmp_path)
    assert m.status()["state"] == "idle"
    m.marker_path.parent.mkdir(parents=True, exist_ok=True)
    m.marker_path.write_text("{not json")
    assert m.status()["state"] == "idle"


def test_pid_alive_reaps_zombie_children():
    # A child that exits without being wait()ed becomes a zombie; os.kill(pid, 0)
    # succeeds on zombies, so _pid_alive must reap before checking.
    proc = subprocess.Popen(["true"])
    deadline = time.time() + 5
    alive = True
    while time.time() < deadline:
        alive = updates_mod._pid_alive(proc.pid)
        if not alive:
            break
        time.sleep(0.05)
    assert alive is False


# ── routes (Task 4) ─────────────────────────────────────────────────


def test_update_status_route(tmp_path):
    app = make_app(tmp_path)
    c = auth_client(app)
    r = c.get("/api/update/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"] == read_local_version()
    assert body["state"] == "idle"
    assert body["update_available"] is False


def test_update_check_route_refreshes(tmp_path, monkeypatch):
    app = make_app(tmp_path)

    async def fake_fetch():
        return {"version": "99.0.0", "notes": "big", "url": "u", "published_at": None}

    monkeypatch.setattr(app.state.updates, "_fetch_latest_release", fake_fetch)
    c = auth_client(app)
    r = c.post("/api/update/check")
    assert r.status_code == 200, r.text
    assert r.json()["update_available"] is True
    assert r.json()["latest"]["version"] == "99.0.0"


def test_update_apply_route_guards(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    c = auth_client(app)

    r = c.post("/api/update/apply")           # nothing known yet
    assert r.status_code == 400

    app.state.updates._latest = {"version": "99.0.0", "notes": "", "url": "", "published_at": None}
    monkeypatch.setattr(updates_mod.subprocess, "Popen", lambda *a, **k: FakeProc())
    r = c.post("/api/update/apply")
    assert r.status_code == 200, r.text
    assert r.json() == {"started": True, "target": "99.0.0"}

    # marker now says running (pid 4242 is dead → may demote to failed; force running)
    app.state.updates.marker_path.write_text(json.dumps({
        "state": "running", "target": "99.0.0", "started_at": "x", "pid": os.getpid(),
    }))
    r = c.post("/api/update/apply")
    assert r.status_code == 409


def test_update_routes_match_peer_admin_route_behavior(tmp_path, monkeypatch):
    # Behavioral parity with peer admin_user routes (e.g. /api/audit): requests
    # with no bearer token resolve to the owner (single-user; the network layer
    # is the access boundary — CLAUDE.md, docs/security-boundaries.md) instead
    # of 401/403. This guards against an accidental hard-auth regression on
    # these routes; the admin_user wiring itself is visible in routes/update.py.
    app = make_app(tmp_path)

    async def fake_fetch():
        return {"version": "0.0.1", "notes": "", "url": "", "published_at": None}

    monkeypatch.setattr(app.state.updates, "_fetch_latest_release", fake_fetch)
    c = TestClient(app)  # no token
    assert c.get("/api/update/status").status_code == 200
    assert c.post("/api/update/check").status_code == 200
    assert c.post("/api/update/apply").status_code == 400  # 0.0.1 is not newer than current


# ── ASGI entrypoint config parity (main.py _config_from_env) ────────


def test_config_from_env_reads_update_vars(monkeypatch):
    from proxima_api.main import _config_from_env

    monkeypatch.setenv("PROXIMA_UPDATE_CHECK", "0")
    monkeypatch.setenv("PROXIMA_UPDATE_REPO", "acme/fork")
    monkeypatch.setenv("PROXIMA_UPDATE_TOKEN", "private-token")
    cfg = _config_from_env()
    assert cfg["update_check"] is False
    assert cfg["update_repo"] == "acme/fork"
    assert cfg["update_token"] == "private-token"

    monkeypatch.delenv("PROXIMA_UPDATE_CHECK")
    monkeypatch.delenv("PROXIMA_UPDATE_REPO")
    monkeypatch.delenv("PROXIMA_UPDATE_TOKEN")
    cfg = _config_from_env()
    assert cfg["update_check"] is True
    assert cfg["update_repo"] == "labsiqbal/proxima"

"""Auth/readiness health checks behind the Home dashboard banner."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from proxima_api import app_settings, auth_health
from proxima_api.main import create_app


@pytest.fixture(autouse=True)
def _fresh_cache():
    auth_health.reset()
    yield
    auth_health.reset()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, runner_id TEXT)")
    return conn


# ── media checks ─────────────────────────────────────────────────────────────

def test_media_checks_only_selected_provider(monkeypatch):
    conn = _conn()
    app_settings.set_json(conn, app_settings.IMAGE_GEN_KEY, {"provider": "xai-oauth", "apiKey": None, "baseUrl": None})
    image_calls = []
    monkeypatch.setattr("proxima_api.image_providers.test_connection",
                        lambda pid, key, base_url=None: (image_calls.append(pid), {"ok": False, "detail": "token expired"})[1])
    checks = auth_health._media_checks(conn)

    assert image_calls == ["xai-oauth"]
    by_id = {c["id"]: c for c in checks}
    assert by_id["image:xai-oauth"]["ok"] is False
    assert "token expired" in by_id["image:xai-oauth"]["detail"]


def test_media_checks_default_providers_when_unset(monkeypatch):
    conn = _conn()
    seen = []
    monkeypatch.setattr("proxima_api.image_providers.test_connection",
                        lambda pid, key, base_url=None: (seen.append(pid), {"ok": True})[1])
    auth_health._media_checks(conn)
    assert seen == ["codex"]


def test_media_check_exception_becomes_failed_check(monkeypatch):
    conn = _conn()
    monkeypatch.setattr("proxima_api.image_providers.test_connection",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    checks = auth_health._media_checks(conn)
    image = next(c for c in checks if c["area"] == "image")
    assert image["ok"] is False and "boom" in image["detail"]


# ── runner checks ────────────────────────────────────────────────────────────

def test_runner_checks_cover_runners_used_by_profiles(monkeypatch):
    conn = _conn()
    conn.executemany("INSERT INTO profiles(runner_id) VALUES (?)", [("hermes",), ("hermes",), ("codex",), ("claude-code",)])
    monkeypatch.setattr("proxima_api.auth_health.runner_readiness", lambda: {
        "hermes": {"id": "hermes", "displayName": "Hermes", "installed": True, "ready": True, "authHint": ""},
        "codex": {"id": "codex", "displayName": "Codex", "installed": True, "ready": True, "authHint": ""},
        "claude-code": {"id": "claude-code", "displayName": "Claude Code", "installed": False, "ready": False, "authHint": "Install the claude CLI."},
    })
    monkeypatch.setattr("proxima_api.auth_health.hermes_status", lambda: {"ready": False, "guidance": "hermes home has no auth.json"})
    monkeypatch.setattr("proxima_api.image_providers.codex_ready", lambda: {"ready": True, "detail": "Codex is logged in."})

    checks = {c["id"]: c for c in auth_health._runner_checks(conn)}

    assert set(checks) == {"runner:hermes", "runner:codex", "runner:claude-code"}  # deduped
    assert checks["runner:hermes"]["ok"] is False and "auth.json" in checks["runner:hermes"]["detail"]
    assert checks["runner:codex"]["ok"] is True
    assert checks["runner:claude-code"]["ok"] is False and "Install" in checks["runner:claude-code"]["detail"]


# ── snapshot cache ───────────────────────────────────────────────────────────

def test_snapshot_before_first_refresh_reports_checking():
    assert auth_health.snapshot("/nonexistent.db", enabled=False) == {"status": "checking", "checks": []}


def test_refresh_populates_snapshot_and_ttl_prevents_rerun(tmp_path, monkeypatch):
    db = tmp_path / "h.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, runner_id TEXT);")
    conn.close()
    monkeypatch.setattr("proxima_api.image_providers.test_connection", lambda *a, **k: {"ok": True, "detail": "ok"})
    auth_health._refresh(str(db))
    snap = auth_health.snapshot(str(db), enabled=True)

    assert snap["status"] == "ok" and snap["checkedAt"]
    assert [c["ok"] for c in snap["checks"]] == [True]

    spawned = []
    monkeypatch.setattr(auth_health.threading, "Thread",
                        lambda *a, **k: spawned.append(1) or (_ for _ in ()).throw(AssertionError("thread spawned inside TTL")))
    assert auth_health.snapshot(str(db), enabled=True) == snap  # cached, no refresh thread
    assert not spawned


def test_stale_snapshot_kicks_background_refresh(tmp_path, monkeypatch):
    class FakeThread:
        def __init__(self, *a, **k):
            self.target, self.args = k.get("target"), k.get("args", ())
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(auth_health.threading, "Thread", FakeThread)
    db = tmp_path / "h.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, runner_id TEXT);")
    conn.close()
    monkeypatch.setattr("proxima_api.image_providers.test_connection", lambda *a, **k: {"ok": True})

    first = auth_health.snapshot(str(db), enabled=True)  # stale → refresh runs (synchronously via FakeThread)
    assert first == {"status": "checking", "checks": []}  # returned the pre-refresh cache
    assert auth_health.snapshot(str(db), enabled=True)["status"] == "ok"


def test_invalidate_marks_stale_but_keeps_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "h.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, runner_id TEXT);")
    conn.close()
    monkeypatch.setattr("proxima_api.image_providers.test_connection", lambda *a, **k: {"ok": True})
    auth_health._refresh(str(db))
    old = auth_health.snapshot(str(db), enabled=True)
    assert old["status"] == "ok"

    auth_health.invalidate()  # e.g. a provider-settings PUT

    kicked = []
    class FakeThread:
        def __init__(self, *a, **k):
            kicked.append(1)
        def start(self):
            pass
    monkeypatch.setattr(auth_health.threading, "Thread", FakeThread)
    assert auth_health.snapshot(str(db), enabled=True) == old  # old data stays visible (no "checking" flash)
    assert kicked  # ...but a refresh was kicked despite the TTL


def test_failed_refresh_bumps_timestamp_no_hot_loop(monkeypatch):
    auth_health._refresh("/nonexistent/dir/that/cannot/exist\0bad")
    assert auth_health._snapshot is None
    assert time.time() - auth_health._refreshed_at < 5  # timestamp bumped → next poll won't re-kick immediately


# ── dashboard wiring ─────────────────────────────────────────────────────────

def test_dashboard_includes_auth_health_and_test_gate_spawns_no_thread(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(auth_health.threading, "Thread", lambda *a, **k: spawned.append(1) or (_ for _ in ()).throw(AssertionError("spawned")))
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"),
                      "projectctl_path": "/usr/bin/true", "start_worker": False})
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    res = c.get("/api/dashboard")
    assert res.status_code == 200
    assert res.json()["authHealth"] == {"status": "checking", "checks": []}
    assert not spawned  # start_worker=False gates the background check off

"""Password login gate: first-run set-password, then login required."""
from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })


def test_first_run_is_passwordless_then_gates_after_set(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)

    # First run: no password, open (network-only), auto-login works.
    assert c.get("/api/setup/status").json()["password_set"] is False
    assert c.post("/auth/auto").status_code == 200
    assert c.get("/api/sessions").status_code == 200

    # Too-short password rejected.
    assert c.post("/auth/set-password", json={"password": "short"}).status_code == 400

    # Set a real password -> logged in, and now flagged as set.
    r = c.post("/auth/set-password", json={"password": "correct horse battery"})
    assert r.status_code == 200 and r.json()["token"]
    assert c.get("/api/setup/status").json()["password_set"] is True
    # Can't set twice.
    assert c.post("/auth/set-password", json={"password": "another one entirely"}).status_code == 409

    # A fresh client (no session) is now locked out; auto-login is disabled.
    fresh = TestClient(app)
    assert fresh.get("/api/sessions").status_code == 401
    assert fresh.post("/auth/auto").status_code == 401
    assert fresh.post("/auth/login", json={"password": "wrong guess here"}).status_code == 401

    # Correct password -> session token, and requests work (bearer AND cookie).
    lr = fresh.post("/auth/login", json={"password": "correct horse battery"})
    assert lr.status_code == 200
    tok = lr.json()["token"]
    assert fresh.get("/api/sessions", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    # cookie-based auth is exercised in test_session_cookie (Secure cookie needs https,
    # which the http TestClient won't send; the app's API path uses the bearer token).

    # Logout revokes the session.
    assert fresh.post("/auth/logout", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    locked = TestClient(app)
    assert locked.get("/api/sessions", headers={"Authorization": f"Bearer {tok}"}).status_code == 401

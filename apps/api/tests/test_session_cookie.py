"""C1 foundation: HttpOnly session cookie.

/auth/auto mints an HttpOnly proxima_session cookie; SSE/WS handlers accept it as a
fallback to ?token=. Additive (?token= still works). These tests cover the
cookie-set + WS/SSE auth-acceptance without consuming the infinite SSE stream
(which would hang a synchronous TestClient).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })


def test_auth_auto_sets_httponly_session_cookie(tmp_path):
    c = TestClient(_app(tmp_path))
    r = c.post("/auth/auto")
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "")
    assert "proxima_session=" in sc
    assert "httponly" in sc.lower()
    assert "samesite=lax" in sc.lower()


def test_ws_events_auths_via_cookie_without_token_query(tmp_path):
    c = TestClient(_app(tmp_path))
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    sid = c.post("/api/sessions", headers=h, json={"title": "x"}).json()["id"]

    # WS connect with the cookie set, NO ?token= → must authenticate + accept.
    c.cookies.set("proxima_session", token)
    with c.websocket_connect(f"/api/ws/sessions/{sid}"):
        pass  # accepted = authed via cookie

    # Without cookie and without ?token= → server rejects (4401) before accept.
    c.cookies.clear()
    with pytest.raises(Exception):
        with c.websocket_connect(f"/api/ws/sessions/{sid}"):
            pass


def test_terminal_ws_requires_session_even_passwordless(tmp_path):
    """No owner-fallback bypass: the terminal WS rejects without a valid session — even
    in passwordless mode — mirroring ws_events + SSE. Guards the closed cfg[single_user]
    hole that could have opened a shell without the password."""
    c = TestClient(_app(tmp_path))
    c.post("/auth/auto")   # a passwordless owner now exists
    c.cookies.clear()      # no cookie, no ?token=
    with pytest.raises(Exception):
        with c.websocket_connect("/api/ws/terminal"):
            pass


def test_sse_rejects_without_any_token(tmp_path):
    """Fast 401 path (no infinite stream): no cookie, no ?token= → 401 immediately,
    proving auth is enforced before the generator starts."""
    c = TestClient(_app(tmp_path))
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    sid = c.post("/api/sessions", headers=h, json={"title": "x"}).json()["id"]
    # TestClient persists the proxima_session cookie /auth/auto set — clear it, else the
    # GET below auths via that cookie, starts the infinite stream, and hangs the client.
    c.cookies.clear()
    # No cookie, no ?token= → 401 immediately (auth enforced before the generator).
    r = c.get(f"/api/sessions/{sid}/events/stream")
    assert r.status_code == 401
    # (A successful auth starts an infinite stream, so the positive case is
    # covered by the WebSocket test above rather than consuming the stream here.)

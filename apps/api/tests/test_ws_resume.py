"""Regression: the WS event stream must resume from the client's cursor.

Before the fix ``ws_events`` hardcoded ``last_id = 0`` and took no resume param,
so every reconnect replayed the entire session transcript (duplicate delivery).
It now accepts ``after_id`` and resumes from it, mirroring the SSE sibling.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })


def test_ws_resumes_from_after_id_and_skips_backlog(tmp_path):
    c = TestClient(_app(tmp_path))
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    sid = c.post("/api/sessions", headers=h, json={"title": "x"}).json()["id"]

    # Two events already in the log before the client (re)connects.
    with c.app.state.db_lock:
        c.app.state.db.execute("INSERT INTO events(session_id, seq, type, payload) VALUES (?, 1, 'test.a', '{}')", (sid,))
        c.app.state.db.execute("INSERT INTO events(session_id, seq, type, payload) VALUES (?, 2, 'test.b', '{}')", (sid,))
        rows = c.app.state.db.execute("SELECT id, type FROM events WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
    first_id, second_id = rows[0]["id"], rows[1]["id"]
    assert rows[0]["type"] == "test.a" and rows[1]["type"] == "test.b"

    c.cookies.set("proxima_session", token)
    # Resume after the first event: the very first frame must be the SECOND event,
    # never a replay of the first.
    with c.websocket_connect(f"/api/ws/sessions/{sid}?after_id={first_id}") as ws:
        frame = ws.receive_json()
    assert frame["id"] == second_id
    assert frame["type"] == "test.b"

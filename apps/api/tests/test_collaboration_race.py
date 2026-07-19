"""Regression: a cancelled collaboration must not be resurrected.

Before the guarded-transition refactor, ``_finish_collaboration`` did a blind
``UPDATE prompt_collaborations SET status='done'`` — so a synthesis run that
completed AFTER the user cancelled (the request thread and the worker race on
separate connections) would silently flip a 'cancelled' collaboration back to
'done'. The guard makes the worker's write conditional on a non-terminal status,
so the cancel wins.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
        "start_worker": False,
    })


def test_cancelled_collaboration_is_not_flipped_back_to_done(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "brainstorm"}).json()["id"]
    parent_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "brainstorm"},
    ).json()["run_id"]
    collab = app.state.db.execute(
        "SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)
    ).fetchone()

    # The user cancels — mirror what chat.py cancel_run persists: the collaboration
    # and its parent run go terminal.
    app.state.db.execute("UPDATE prompt_collaborations SET status = 'cancelled' WHERE id = ?", (collab["id"],))
    app.state.db.execute("UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (parent_id,))

    # The worker races in and tries to finish the (already cancelled) collaboration.
    collab_row = app.state.worker_db.execute(
        "SELECT * FROM prompt_collaborations WHERE id = ?", (collab["id"],)
    ).fetchone()
    app.state.worker._finish_collaboration(collab_row, [], "Synthesized answer", "stop")

    # The guard keeps the cancel: status stays 'cancelled', not 'done', and the
    # cancelled parent run is not resurrected to 'completed'.
    assert app.state.db.execute(
        "SELECT status FROM prompt_collaborations WHERE id = ?", (collab["id"],)
    ).fetchone()["status"] == "cancelled"
    assert client.get(f"/api/runs/{parent_id}", headers=headers).json()["status"] == "cancelled"
    stored = app.state.db.execute(
        "SELECT final_message_id FROM prompt_collaborations WHERE id = ?", (collab["id"],)
    ).fetchone()
    assert stored["final_message_id"] is None
    assert app.state.db.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE run_id = ? AND role = 'assistant'",
        (parent_id,),
    ).fetchone()["n"] == 0
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert not any(
        e["run_id"] == parent_id and e["type"] in {"message.complete", "run.completed"}
        for e in events
    )

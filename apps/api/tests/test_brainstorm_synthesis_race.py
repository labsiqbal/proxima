"""Regression: brainstorm synthesis must wait for ALL children.

The premature-synthesis race: the request thread queues the children, then
persists child_run_ids. If the first child completed before that list was
written, the worker saw an empty child_run_ids, concluded "0 remaining", and
synthesized after a single lane. The worker now counts completeness from the
live runs table + profile_ids (the expected count, persisted at collab creation),
so an empty/stale child_run_ids can't trigger an early synthesis.
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


def _complete(app, run_id, text):
    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
    run = dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    app.state.worker._complete_collaboration_run(run, text, "stop")


def test_synthesis_waits_for_all_children_even_with_empty_child_run_ids(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    uid = app.state.db.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()["id"]
    app.state.db.execute("INSERT INTO profiles(user_id, slug, name, runner_id, hermes_home) VALUES (?, 'p2', 'Two', 'codex', ?)", (uid, str(tmp_path / "h2")))
    client.put("/api/settings/collaboration", headers=headers, json={"brainstorm_agents": 2, "debate_rounds": 2})
    sid = client.post("/api/sessions", headers=headers, json={"title": "bs"}).json()["id"]
    parent_id = client.post(
        f"/api/sessions/{sid}/runs", headers=headers,
        json={"message": "pick", "prompt_mode": "brainstorm"},
    ).json()["run_id"]

    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)).fetchone()
    children = app.state.db.execute(
        "SELECT id FROM runs WHERE collaboration_id = ? AND kind = 'collab_brainstorm_child' ORDER BY id", (collab["id"],)
    ).fetchall()
    assert len(children) == 2

    # Simulate the race: child_run_ids not yet persisted when the first child finishes.
    app.state.db.execute("UPDATE prompt_collaborations SET child_run_ids = '[]' WHERE id = ?", (collab["id"],))
    _complete(app, children[0]["id"], "Lane one idea.")

    # No premature synthesis: one lane still pending.
    row = app.state.db.execute("SELECT synthesis_run_id FROM prompt_collaborations WHERE id = ?", (collab["id"],)).fetchone()
    assert row["synthesis_run_id"] is None
    assert app.state.db.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE collaboration_id = ? AND kind = 'collab_brainstorm_synthesis'", (collab["id"],)
    ).fetchone()["c"] == 0

    # Second lane completes -> now synthesis fires.
    _complete(app, children[1]["id"], "Lane two idea.")
    row = app.state.db.execute("SELECT synthesis_run_id FROM prompt_collaborations WHERE id = ?", (collab["id"],)).fetchone()
    assert row["synthesis_run_id"] is not None

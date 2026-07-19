from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.run_drafts import RunDrafts


def _draft_run(tmp_path, status: str):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    session_id = client.post(
        "/api/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "draft"},
    ).json()["id"]
    user_id = app.state.db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id, user_id, status, prompt, kind) VALUES (?, ?, ?, 'draft', 'wiki_draft')",
        (session_id, user_id, status),
    ).lastrowid
    run = dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    return app, run


def test_cancelled_draft_run_is_not_resurrected(tmp_path):
    app, run = _draft_run(tmp_path, "cancelled")
    events: list[str] = []

    handled = RunDrafts(app).handle_draft_run(
        run,
        "# Draft\nBody",
        None,
        lambda _run, _session, _project, event_type, _payload: events.append(event_type),
    )

    saved = app.state.db.execute("SELECT status FROM runs WHERE id = ?", (run["id"],)).fetchone()
    assert handled is True
    assert saved["status"] == "cancelled"
    assert events == []


def test_running_draft_run_completes_once(tmp_path):
    app, run = _draft_run(tmp_path, "running")
    events: list[str] = []

    handled = RunDrafts(app).handle_draft_run(
        run,
        "# Draft\nBody",
        "end_turn",
        lambda _run, _session, _project, event_type, _payload: events.append(event_type),
    )

    saved = app.state.db.execute("SELECT status FROM runs WHERE id = ?", (run["id"],)).fetchone()
    assert handled is True
    assert saved["status"] == "completed"
    assert events == ["wiki.draft", "run.completed"]

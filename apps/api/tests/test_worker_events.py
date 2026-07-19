from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def test_concurrent_events_get_unique_monotonic_sequences(tmp_path):
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
        json={"title": "events"},
    ).json()["id"]
    user_id = app.state.db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id, user_id, status, prompt) VALUES (?, ?, 'running', 'events')",
        (session_id, user_id),
    ).lastrowid

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(app.state.worker.add_event, run_id, session_id, None, "test.event", {"n": index})
            for index in range(40)
        ]
        for future in futures:
            future.result()

    rows = app.state.db.execute(
        "SELECT seq FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
    ).fetchall()
    assert [row["seq"] for row in rows] == list(range(1, 41))

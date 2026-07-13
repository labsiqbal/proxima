"""Regression: the stale-run reaper must not kill a run this worker is still
executing. A busy event loop can stall the heartbeat past the threshold even
though the ACP subprocess is alive — reaping it there loses live work. Only runs
with no live task (orphaned by a crash/restart) are genuine reap targets.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


class _FakeTask:
    """Stands in for an in-flight asyncio.Task (run_tasks value)."""
    def done(self) -> bool:
        return False


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
        "start_worker": False,
    })


def test_reaper_skips_actively_running_run(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    sid = client.post("/api/sessions", headers={"Authorization": f"Bearer {token}"}, json={"title": "s"}).json()["id"]
    uid = app.state.db.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()["id"]

    # Two 'running' runs, both with a stale (2-minute-old) heartbeat.
    def _stale_run() -> int:
        cur = app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, prompt, status, started_at, heartbeat_at) "
            "VALUES (?, ?, 'p', 'running', datetime('now','-300 seconds'), datetime('now','-120 seconds'))",
            (sid, uid),
        )
        return int(cur.lastrowid)

    alive_run = _stale_run()   # this worker is still executing it
    orphan_run = _stale_run()  # no live task (crashed/restarted)
    app.state.worker.run_tasks[alive_run] = _FakeTask()

    app.state.worker.reaper.reap_stale_runs(60)

    def _status(rid: int) -> str:
        return app.state.db.execute("SELECT status FROM runs WHERE id = ?", (rid,)).fetchone()["status"]

    assert _status(alive_run) == "running", "a live run was false-positive reaped"
    assert _status(orphan_run) == "failed", "an orphaned stale run should be reaped"

"""Regression: sessions.produced_artifacts is written from the worker AND from
request handlers (add on run finish, prune on artifact delete) on different
connections. A naive read-modify-write loses one side under concurrency. The
compare-and-swap helper re-reads + retries so nothing is silently lost.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from proxima_api.artifacts import update_produced_artifacts
from proxima_api.db import connect
from proxima_api.main import create_app


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })


def test_cas_retries_and_preserves_concurrent_write(tmp_path):
    db_path = tmp_path / "proxima.db"
    app = _app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    sid = client.post("/api/sessions", headers={"Authorization": f"Bearer {token}"}, json={"title": "s"}).json()["id"]
    app.state.db.execute(
        "UPDATE sessions SET produced_artifacts = ? WHERE id = ?",
        (json.dumps([{"type": "doc", "path": "a"}]), sid),
    )

    other = connect(db_path)  # a second, independent connection (like the request thread vs worker)
    calls = {"n": 0}

    def mutate(current):
        calls["n"] += 1
        if calls["n"] == 1:
            # A concurrent writer lands between our read and our write: it appends "b".
            other.execute(
                "UPDATE sessions SET produced_artifacts = ? WHERE id = ?",
                (json.dumps(current + [{"type": "doc", "path": "b"}]), sid),
            )
        return current + [{"type": "doc", "path": "c"}]

    update_produced_artifacts(app.state.db, sid, mutate)

    result = json.loads(app.state.db.execute("SELECT produced_artifacts FROM sessions WHERE id = ?", (sid,)).fetchone()["produced_artifacts"])
    paths = sorted(a["path"] for a in result)
    # Without CAS, "b" (the concurrent write) would be clobbered by our stale write.
    assert paths == ["a", "b", "c"], paths
    assert calls["n"] == 2  # first attempt lost the CAS and retried once


def test_cas_noop_when_unchanged(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    sid = client.post("/api/sessions", headers={"Authorization": f"Bearer {token}"}, json={"title": "s"}).json()["id"]
    # identity mutate -> no write, no error, no infinite loop
    update_produced_artifacts(app.state.db, sid, lambda current: current)
    assert app.state.db.execute("SELECT produced_artifacts FROM sessions WHERE id = ?", (sid,)).fetchone()["produced_artifacts"] == "[]"

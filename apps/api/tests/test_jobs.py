from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.routes import work as work_routes


def _app(tmp_path):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )


def _client(app, **kwargs):
    c = TestClient(app, **kwargs)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _make_workflow(c, steps):
    return c.post("/api/workflows", json={"name": "W", "steps": steps}).json()["id"]


def _latest_run(app, session_id):
    row = app.state.db.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def test_create_job_from_workflow_snapshots_steps(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _make_workflow(c, [{"name": "A", "instruction": "do a"}, {"name": "B", "instruction": "do b"}])
    job = c.post("/api/jobs", json={"workflow_id": wid, "input": {"brief": "make X"}}).json()
    assert job["status"] == "queued"
    assert [s["name"] for s in job["steps_state"]] == ["A", "B"]
    assert all(s["status"] == "queued" for s in job["steps_state"])
    assert job["session_id"]


def test_create_job_substitutes_inputs_in_step_snapshot(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _make_workflow(c, [{"name": "A", "instruction": "do {{topic}}", "expected_output": "summary for {{topic}}", "rules": "mention {{topic}}"}])
    job = c.post("/api/jobs", json={"workflow_id": wid, "input": {"topic": "launch plan"}}).json()
    step = job["steps_state"][0]
    assert step["instruction"] == "do launch plan"
    assert step["expected_output"] == "summary for launch plan"
    assert step["rules"] == "mention launch plan"


def test_create_adhoc_job_single_step(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    job = c.post("/api/jobs", json={"input": {"brief": "just do this"}}).json()
    assert job["workflow_id"] is None
    assert len(job["steps_state"]) == 1
    assert job["steps_state"][0]["instruction"] == "just do this"


def test_executor_advances_steps_then_review(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _make_workflow(c, [{"name": "A", "instruction": "do a"}, {"name": "B", "instruction": "do b"}])
    job = c.post("/api/jobs", json={"workflow_id": wid}).json()
    jid, sid = job["id"], job["session_id"]

    assert c.post(f"/api/jobs/{jid}/start").status_code == 200
    j = c.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "running"
    assert j["steps_state"][0]["status"] == "running"

    # simulate the worker completing step 0
    app.state.worker._advance_job(_latest_run(app, sid), "keywords found")
    j = c.get(f"/api/jobs/{jid}").json()
    assert j["steps_state"][0]["status"] == "done"
    assert j["steps_state"][0]["output_summary"] == "keywords found"
    assert j["current_step_idx"] == 1
    assert j["steps_state"][1]["status"] == "running"

    # complete step 1 -> last step -> review
    app.state.worker._advance_job(_latest_run(app, sid), "draft done")
    j = c.get(f"/api/jobs/{jid}").json()
    assert j["steps_state"][1]["status"] == "done"
    assert j["status"] == "review"


def test_jobs_list_filter_and_approve(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    job = c.post("/api/jobs", json={"input": {"brief": "x"}}).json()
    res = c.get("/api/jobs").json()
    assert "items" in res and "total" in res and res["total"] >= 1
    assert any(it["id"] == job["id"] for it in c.get("/api/jobs?status=queued").json()["items"])

    # force to review, then approve -> done
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))
    approved = c.post(f"/api/jobs/{job['id']}/approve").json()
    assert approved["status"] == "done"


def test_jobs_list_filters_by_effective_status(tmp_path):
    app = _app(tmp_path)
    client = _client(app)
    job = client.post(
        "/api/jobs",
        json={"input": {"brief": "surface the failed step"}},
    ).json()
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', "
        "steps_state = json_set(steps_state, '$[0].status', 'failed') "
        "WHERE id = ?",
        (job["id"],),
    )

    failed = client.get("/api/jobs?status=failed").json()
    review = client.get("/api/jobs?status=review").json()

    assert [item["id"] for item in failed["items"]] == [job["id"]]
    assert failed["items"][0]["run_projection"]["status"] == "failed"
    assert failed["total"] == 1
    assert review["items"] == []
    assert review["total"] == 0


def test_final_approval_rolls_back_when_task_invalidation_cannot_commit(
    tmp_path, monkeypatch
):
    app = _app(tmp_path)
    client = _client(app)
    job = client.post("/api/jobs", json={"input": {"brief": "x"}}).json()
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', "
        "steps_state = json_set(steps_state, '$[0].status', 'done') "
        "WHERE id = ?",
        (job["id"],),
    )

    def fail_invalidation(*_args, **_kwargs):
        raise RuntimeError("invalidation unavailable")

    monkeypatch.setattr(work_routes, "append_task_update", fail_invalidation)

    with pytest.raises(RuntimeError, match="invalidation unavailable"):
        client.post(f"/api/jobs/{job['id']}/approve")

    row = app.state.db.execute(
        "SELECT status, finished_at FROM jobs WHERE id = ?", (job["id"],)
    ).fetchone()
    assert dict(row) == {"status": "review", "finished_at": None}


def test_new_job_defaults_to_linear_engine(tmp_path):
    # ADR-0001 coexistence: every classic job is engine='linear'; the graph
    # engine is opt-in and never the default.
    app = _app(tmp_path)
    c = _client(app)
    job = c.post("/api/jobs", json={"input": {"brief": "x"}}).json()
    row = app.state.db.execute("SELECT engine, graph FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert row["engine"] == "linear"
    assert row["graph"] is None


def test_graph_jobs_are_hidden_from_the_linear_activity_list(tmp_path):
    # S2 hardening: a engine='graph' job (no steps_state) must not surface in the
    # classic Activity list, which hard-binds steps_state/current_step_idx.
    app = _app(tmp_path)
    c = _client(app)
    linear = c.post("/api/jobs", json={"input": {"brief": "linear one"}}).json()
    app.state.db.execute(
        "INSERT INTO jobs(title, status, engine, graph, created_by) VALUES ('graph one','queued','graph','{\"nodes\":[],\"edges\":[]}',?)",
        (linear["created_by"],),
    )
    ids = {it["id"] for it in c.get("/api/jobs").json()["items"]}
    titles = {it["title"] for it in c.get("/api/jobs").json()["items"]}
    assert linear["id"] in ids
    assert "graph one" not in titles


def test_jobs_list_filters_by_project_slug(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    assert c.post("/api/projects", json={"slug": "alpha", "name": "Alpha"}).status_code == 201
    assert c.post("/api/projects", json={"slug": "beta", "name": "Beta"}).status_code == 201
    ja = c.post("/api/jobs", json={"project_slug": "alpha", "input": {"brief": "alpha job"}}).json()
    jb = c.post("/api/jobs", json={"project_slug": "beta", "input": {"brief": "beta job"}}).json()

    alpha = c.get("/api/jobs?project_slug=alpha").json()["items"]
    beta = c.get("/api/jobs?project_slug=beta").json()["items"]

    assert [j["id"] for j in alpha] == [ja["id"]]
    assert [j["id"] for j in beta] == [jb["id"]]
    assert [j["project_name"] for j in alpha] == ["Alpha"]
    assert [j["project_name"] for j in beta] == ["Beta"]


def test_delete_started_job_cancels_run_before_session_cleanup(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _make_workflow(c, [{"name": "A", "instruction": "do a"}])
    job = c.post("/api/jobs", json={"workflow_id": wid}).json()
    started = c.post(f"/api/jobs/{job['id']}/start").json()
    run_id = started["steps_state"][0]["run_id"]
    session_id = job["session_id"]

    assert c.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert app.state.db.execute("SELECT 1 FROM jobs WHERE id = ?", (job["id"],)).fetchone() is None
    assert app.state.db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None
    assert app.state.db.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() is None
    # A worker callback that arrives after the session has been deleted should no-op,
    # not raise a FK error that keeps the worker noisy/stuck.
    app.state.worker.add_event(run_id, session_id, None, "message.delta", {"text": "late"})


def test_delete_job_preserves_recovery_source_identity(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    job = c.post(
        "/api/jobs",
        json={"input": {"brief": "recoverable"}},
    ).json()
    job_id = int(job["id"])
    task_session_id = int(job["session_id"])
    owner_id = int(
        app.state.db.execute(
            "SELECT owner_user_id FROM sessions WHERE id = ?",
            (task_session_id,),
        ).fetchone()[0]
    )
    # One Master session per owner: startup already provisions it while the
    # feature is on, so adopt that row instead of inserting a second one.
    existing_master = app.state.db.execute(
        "SELECT id FROM sessions WHERE owner_user_id = ? AND mode = 'master'",
        (owner_id,),
    ).fetchone()
    master_session_id = int(
        existing_master[0]
        if existing_master
        else app.state.db.execute(
            "INSERT INTO sessions(title, owner_user_id, mode) "
            "VALUES ('Master', ?, 'master')",
            (owner_id,),
        ).lastrowid
    )
    app.state.db.execute(
        "UPDATE jobs SET origin_master_session_id = ? WHERE id = ?",
        (master_session_id, job_id),
    )
    source_ids: list[tuple[int, int]] = []
    for seq in (1, 2):
        task_event_id = int(
            app.state.db.execute(
                "INSERT INTO events(session_id, seq, type, payload) "
                "VALUES (?, ?, 'job.update', '{}')",
                (task_session_id, seq),
            ).lastrowid
        )
        outbox_id = int(
            app.state.db.execute(
                "INSERT INTO task_recovery_outbox("
                "job_id, task_event_id, recovery_json, master_session_id"
                ") VALUES (?, ?, ?, ?)",
                (
                    job_id,
                    task_event_id,
                    json.dumps({"checkpoint_id": seq}),
                    master_session_id,
                ),
            ).lastrowid
        )
        source_ids.append((outbox_id, task_event_id))

    assert c.delete(f"/api/jobs/{job_id}").status_code == 200
    assert dict(
        app.state.db.execute(
            "SELECT job_id, task_session_id, master_session_id, "
            "first_task_event_id, last_task_event_id, "
            "first_recovery_outbox_id, last_recovery_outbox_id, "
            "capture_source, deletion_source "
            "FROM task_recovery_history_tombstones WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    ) == {
        "job_id": job_id,
        "task_session_id": task_session_id,
        "master_session_id": master_session_id,
        "first_task_event_id": min(pair[1] for pair in source_ids),
        "last_task_event_id": max(pair[1] for pair in source_ids),
        "first_recovery_outbox_id": min(pair[0] for pair in source_ids),
        "last_recovery_outbox_id": max(pair[0] for pair in source_ids),
        "capture_source": "session",
        "deletion_source": "task_event",
    }
    assert [
        tuple(row)
        for row in app.state.db.execute(
            "SELECT recovery_outbox_id, task_event_id, "
            "task_session_id, master_session_id "
            "FROM task_recovery_source_history "
            "WHERE job_id = ? ORDER BY recovery_outbox_id",
            (job_id,),
        ).fetchall()
    ] == [
        (
            outbox_id,
            task_event_id,
            task_session_id,
            master_session_id,
        )
        for outbox_id, task_event_id in source_ids
    ]
    assert app.state.db.execute(
        "SELECT 1 FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone() is None
    assert app.state.db.execute(
        "SELECT 1 FROM sessions WHERE id = ?",
        (task_session_id,),
    ).fetchone() is None
    assert app.state.db.execute(
        "SELECT 1 FROM sessions WHERE id = ?",
        (master_session_id,),
    ).fetchone() is not None
    assert app.state.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_start_job_rollback_keeps_job_queued_when_run_enqueue_fails(tmp_path):
    app = _app(tmp_path)
    c = _client(app, raise_server_exceptions=False)
    job = c.post("/api/jobs", json={"input": {"brief": "fragile"}}).json()
    # Simulate a stale/broken job row whose session disappeared before Start.
    app.state.db.execute("UPDATE jobs SET session_id = NULL WHERE id = ?", (job["id"],))

    res = c.post(f"/api/jobs/{job['id']}/start")
    assert res.status_code == 409
    assert res.json()["detail"] == "job session missing"
    refreshed = c.get(f"/api/jobs/{job['id']}").json()
    assert refreshed["status"] == "queued"
    assert refreshed["steps_state"][0]["status"] == "queued"
    assert app.state.db.execute("SELECT 1 FROM runs WHERE prompt = 'fragile'").fetchone() is None


def test_approve_job_rollback_keeps_job_in_review_when_run_enqueue_fails(tmp_path):
    app = _app(tmp_path)
    c = _client(app, raise_server_exceptions=False)
    wid = _make_workflow(c, [{"name": "A", "instruction": "do a", "review_required": True}, {"name": "B", "instruction": "do b"}])
    job = c.post("/api/jobs", json={"workflow_id": wid}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    app.state.worker._advance_job(_latest_run(app, job["session_id"]), "out a")
    assert c.get(f"/api/jobs/{job['id']}").json()["status"] == "review"
    # Simulate session cleanup racing before Approve resumes the next step.
    app.state.db.execute("UPDATE jobs SET session_id = NULL WHERE id = ?", (job["id"],))

    res = c.post(f"/api/jobs/{job['id']}/approve")
    assert res.status_code == 409
    assert res.json()["detail"] == "job session missing"
    refreshed = c.get(f"/api/jobs/{job['id']}").json()
    assert refreshed["status"] == "review"
    assert refreshed["current_step_idx"] == 0
    assert refreshed["steps_state"][1]["status"] == "queued"


def test_worker_reaps_running_job_without_active_run(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    job = c.post("/api/jobs", json={"input": {"brief": "orphaned"}}).json()
    steps = job["steps_state"]
    steps[0]["status"] = "running"
    app.state.db.execute(
        "UPDATE jobs SET status = 'running', steps_state = ? WHERE id = ?",
        (json.dumps(steps), job["id"]),
    )

    app.state.worker.reap_orphaned_jobs()

    refreshed = c.get(f"/api/jobs/{job['id']}").json()
    assert refreshed["status"] == "failed"
    assert refreshed["steps_state"][0]["status"] == "failed"
    assert refreshed["steps_state"][0]["error"] == "Job stalled (no active run)"


def test_worker_does_not_reap_running_job_with_queued_run(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    job = c.post("/api/jobs", json={"input": {"brief": "queued run"}}).json()
    c.post(f"/api/jobs/{job['id']}/start")

    app.state.worker.reap_orphaned_jobs()

    refreshed = c.get(f"/api/jobs/{job['id']}").json()
    assert refreshed["status"] == "running"
    assert refreshed["steps_state"][0]["status"] == "running"


def test_startup_reaps_orphaned_running_job(tmp_path):
    db_path = tmp_path / "proxima.db"
    app = _app(tmp_path)
    c = _client(app)
    job = c.post("/api/jobs", json={"input": {"brief": "restart orphan"}}).json()
    steps = job["steps_state"]
    steps[0]["status"] = "running"
    app.state.db.execute(
        "UPDATE jobs SET status = 'running', steps_state = ? WHERE id = ?",
        (json.dumps(steps), job["id"]),
    )
    app.state.db.close()
    app.state.worker_db.close()

    restarted = create_app(
        {
            "database_path": str(db_path),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    with TestClient(restarted) as rc:
        tok = rc.post("/auth/auto").json()["token"]
        rc.headers.update({"Authorization": f"Bearer {tok}"})
        refreshed = rc.get(f"/api/jobs/{job['id']}").json()

    assert refreshed["status"] == "failed"
    assert refreshed["steps_state"][0]["error"] == "Job stalled (no active run)"


def test_archive_old_jobs_helper(tmp_path):
    import sqlite3
    from proxima_api import db as dbmod
    from proxima_api.main import archive_old_jobs

    conn = sqlite3.connect(tmp_path / "h.db")
    conn.row_factory = sqlite3.Row
    dbmod.init_db(conn)
    conn.execute(
        "INSERT INTO jobs(title, status, created_at) VALUES ('old', 'done', datetime('now','-40 days'))"
    )
    conn.execute("INSERT INTO jobs(title, status) VALUES ('new', 'done')")
    conn.commit()
    n = archive_old_jobs(conn, days=30)
    assert n == 1
    archived = {r["title"] for r in conn.execute("SELECT title FROM jobs WHERE archived_at IS NOT NULL")}
    assert archived == {"old"}

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from proxima_api.main import create_app


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

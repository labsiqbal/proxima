from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from proxima_api import main, workflows as wf
from proxima_api.scheduler import _spawn_scheduled_job
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


def _client(app):
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _wf(c):
    return c.post("/api/workflows", json={"name": "W", "steps": [{"name": "A", "instruction": "a"}]}).json()["id"]


def test_cron_matches():
    dt = datetime(2026, 6, 22, 9, 30)
    assert wf.cron_matches("* * * * *", dt)
    assert wf.cron_matches("30 9 * * *", dt)
    assert not wf.cron_matches("31 9 * * *", dt)
    assert wf.cron_matches("*/15 * * * *", dt)            # 30 % 15 == 0
    assert not wf.cron_matches("*/7 * * * *", datetime(2026, 6, 22, 9, 31))
    assert wf.cron_matches("0,30 9 * * *", dt)
    assert wf.cron_matches("0-45 9 22 6 *", dt)           # minute-range + day 22 + month 6
    assert not wf.cron_matches("0-45 9 6 6 *", dt)        # day-of-month 6 != 22
    assert not wf.cron_matches("bad cron", dt)            # wrong field count


def test_schedule_crud_and_validation(tmp_path):
    c = _client(_app(tmp_path))
    wid = _wf(c)
    assert c.post("/api/schedules", json={"workflow_id": wid, "cron": "nope"}).status_code == 422
    s = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *"}).json()
    assert s["enabled"] is True and s["overlap_policy"] == "skip"
    assert c.patch(f"/api/schedules/{s['id']}", json={"enabled": False}).json()["enabled"] is False
    assert any(x["id"] == s["id"] for x in c.get(f"/api/schedules?workflow_id={wid}").json())
    c.delete(f"/api/schedules/{s['id']}")
    assert all(x["id"] != s["id"] for x in c.get("/api/schedules").json())


def test_cron_valid_and_defensive_matcher():
    # well-formed crons pass
    for ok in ("* * * * *", "*/15 * * * *", "0 9 * * 1-5", "0,30 9 22 6 *", "0 0 1 1 0"):
        assert wf.cron_valid(ok), ok
    # malformed-but-5-field crons that used to raise at match time must be rejected
    for bad in ("*/0 * * * *", "x * * * *", "* * * * MON", "0 99 * * *", "60 * * * *", "5-2 * * * *"):
        assert not wf.cron_valid(bad), bad
    # defensive: matcher/next never raise on a bad cron — they just don't match
    dt = datetime(2026, 6, 22, 9, 30)
    for bad in ("*/0 * * * *", "x * * * *", "* * * * MON"):
        assert wf.cron_matches(bad, dt) is False
        assert wf.next_cron_after(bad, dt) is None


def test_create_schedule_rejects_malformed_5field_cron(tmp_path):
    c = _client(_app(tmp_path))
    wid = _wf(c)
    for bad in ("*/0 * * * *", "0 9 * * MON", "abc * * * *"):
        assert c.post("/api/schedules", json={"workflow_id": wid, "cron": bad}).status_code == 422, bad


def test_legacy_bad_cron_row_does_not_break_tick_or_dashboard(tmp_path):
    # A malformed cron that predates validation (injected straight into the DB)
    # must NOT abort the scheduler pass for sibling schedules, nor 500 the dashboard.
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    good = c.post("/api/schedules", json={"workflow_id": wid, "cron": "* * * * *"}).json()
    bad = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *"}).json()
    with app.state.db_lock:
        app.state.db.execute("UPDATE schedules SET cron = '*/0 * * * *' WHERE id = ?", (bad["id"],))
        app.state.db.commit()
    # the good (every-minute) schedule still spawns despite the poisoned sibling row
    spawned = main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 30))
    assert len(spawned) == 1
    assert c.get(f"/api/jobs/{spawned[0]}").json()["schedule_id"] == good["id"]
    # dashboard renders instead of 500
    assert c.get("/api/dashboard").status_code == 200


def test_dashboard_active_runs_ignores_stale_runs(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
            "run_stale_seconds": 60,
        }
    )
    c = _client(app)
    c.post("/api/projects", json={"slug": "alpha", "name": "Alpha"})
    slug = "alpha"
    fresh_sid = c.post("/api/sessions", json={"title": "fresh", "project_slug": slug}).json()["id"]
    stale_sid = c.post("/api/sessions", json={"title": "stale", "project_slug": slug}).json()["id"]
    db = app.state.db
    db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, created_at, heartbeat_at) "
        "SELECT ?, s.project_id, s.owner_user_id, s.profile_id, s.runner_id, 'running', 'fresh', datetime('now'), datetime('now') FROM sessions s WHERE s.id = ?",
        (fresh_sid, fresh_sid),
    )
    db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, created_at, heartbeat_at) "
        "SELECT ?, s.project_id, s.owner_user_id, s.profile_id, s.runner_id, 'running', 'stale', datetime('now','-10 minutes'), datetime('now','-10 minutes') FROM sessions s WHERE s.id = ?",
        (stale_sid, stale_sid),
    )

    dashboard = c.get("/api/dashboard").json()
    assert dashboard["counts"]["activeRuns"] == 1
    assert [s["id"] for s in dashboard["activeSessions"]] == [fresh_sid]


def test_scheduler_spawns_then_guards_and_skips_overlap(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "* * * * *"}).json()
    now = datetime(2026, 6, 22, 9, 30)

    spawned = main._scheduler_tick(app, now=now)
    assert len(spawned) == 1
    job = c.get(f"/api/jobs/{spawned[0]}").json()
    assert job["schedule_id"] == sch["id"]
    assert job["status"] == "running"

    # same minute -> no double-fire
    assert main._scheduler_tick(app, now=now) == []
    # next minute, prior job still active -> overlap policy 'skip'
    assert main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 31)) == []


def test_scheduler_substitutes_inputs_in_step_snapshot(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "W", "steps": [{"name": "A", "instruction": "write {{topic}}", "expected_output": "doc about {{topic}}"}]}).json()["id"]
    c.post("/api/schedules", json={"workflow_id": wid, "cron": "* * * * *", "input": {"topic": "weekly memo"}})

    spawned = main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 30))
    step = c.get(f"/api/jobs/{spawned[0]}").json()["steps_state"][0]
    assert step["instruction"] == "write weekly memo"
    assert step["expected_output"] == "doc about weekly memo"


def test_scheduler_skips_empty_workflow_without_stuck_job(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "Empty", "steps": []}).json()["id"]
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "* * * * *"}).json()

    assert main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 30)) == []
    jobs = c.get(f"/api/jobs?workflow_id={wid}").json()["items"]
    assert jobs == []
    refreshed = c.get("/api/schedules").json()[0]
    assert refreshed["id"] == sch["id"]
    assert refreshed["last_run_minute"] == "2026-06-22T09:30"


def test_disabled_and_nonmatching_schedules_do_not_spawn(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    c.post("/api/schedules", json={"workflow_id": wid, "cron": "* * * * *", "enabled": False})
    c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 3 * * *"})  # 3am only
    assert main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 30)) == []


def test_run_now_fires_the_schedule_through_the_scheduler_path(tmp_path):
    # "Run now" must prove the stored cron target, so it goes through the same spawn
    # the tick uses: same workflow, same project, same substituted input.
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "W", "steps": [{"name": "A", "instruction": "write {{topic}}"}]}).json()["id"]
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *", "input": {"topic": "weekly memo"}}).json()

    job = c.post(f"/api/schedules/{sch['id']}/run").json()
    assert job["schedule_id"] == sch["id"]
    assert job["workflow_id"] == wid
    assert job["status"] == "running"
    assert job["steps_state"][0]["instruction"] == "write weekly memo"


def test_run_now_does_not_swallow_the_real_tick_for_that_minute(tmp_path):
    # The guard that matters: a manual run at 09:00 must not claim the 09:00 minute
    # and leave the owner thinking the schedule fired on its own.
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *", "overlap_policy": "allow"}).json()

    manual = c.post(f"/api/schedules/{sch['id']}/run").json()
    assert c.get("/api/schedules").json()[0]["last_run_minute"] is None

    spawned = main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 0))
    assert len(spawned) == 1 and spawned[0] != manual["id"]
    assert c.get("/api/schedules").json()[0]["last_run_minute"] == "2026-06-22T09:00"


def test_run_now_works_on_a_disabled_schedule(tmp_path):
    # 'enabled' governs the tick. Trying a schedule before trusting it to fire on its
    # own is exactly when it is still switched off.
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *", "enabled": False}).json()

    assert c.post(f"/api/schedules/{sch['id']}/run").json()["schedule_id"] == sch["id"]
    assert main._scheduler_tick(app, now=datetime(2026, 6, 22, 9, 0)) == []


def test_run_now_reports_an_overlap_skip_instead_of_doing_nothing(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *", "overlap_policy": "skip"}).json()

    assert c.post(f"/api/schedules/{sch['id']}/run").status_code == 200
    blocked = c.post(f"/api/schedules/{sch['id']}/run")
    assert blocked.status_code == 409
    assert "overlap" in blocked.json()["detail"]


def test_run_now_allows_a_second_run_when_overlap_is_allow(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = _wf(c)
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *", "overlap_policy": "allow"}).json()

    first = c.post(f"/api/schedules/{sch['id']}/run").json()
    second = c.post(f"/api/schedules/{sch['id']}/run").json()
    assert first["id"] != second["id"]


def test_run_now_on_an_unrunnable_workflow_409s(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "Empty", "steps": []}).json()["id"]
    sch = c.post("/api/schedules", json={"workflow_id": wid, "cron": "0 9 * * *"}).json()

    assert c.post(f"/api/schedules/{sch['id']}/run").status_code == 409


def test_run_now_is_scoped_to_the_owner(tmp_path):
    c = _client(_app(tmp_path))
    assert c.post("/api/schedules/9999/run").status_code == 404


def _graph_app(tmp_path, *, graph_enabled: bool = True):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "feature_workflow_graph": graph_enabled,
            "start_worker": False,
        }
    )


def _graph_workflow(app, name: str = "Graph W") -> int:
    """A saved graph template: a workflows row whose steps are '[]' and graph is a DAG."""
    import json as _json

    from proxima_api.graph import normalize_graph

    db = app.state.worker_db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    graph = normalize_graph({
        "nodes": [{"id": "only", "name": "Only", "instruction": "Do {{brief}}"}],
    })
    cur = db.execute(
        "INSERT INTO workflows(name, description, category, status, steps, graph, inputs, created_by) "
        "VALUES (?, '', 'other', 'active', '[]', ?, '[]', ?)",
        (name, _json.dumps(graph), owner),
    )
    return int(cur.lastrowid)


def _schedule_for(app, workflow_id: int, inp: str = '{"brief": "the launch"}') -> dict:
    db = app.state.worker_db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    cur = db.execute(
        "INSERT INTO schedules(workflow_id, cron, input, enabled, overlap_policy, created_by) "
        "VALUES (?, '* * * * *', ?, 1, 'skip', ?)",
        (workflow_id, inp, owner),
    )
    row = db.execute("SELECT * FROM schedules WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def test_scheduling_a_graph_workflow_spawns_a_graph_job(tmp_path):
    """It used to build steps_state from a graph template's '[]' steps and return None —
    a scheduled graph did nothing at all, with no error."""
    app = _graph_app(tmp_path)
    _client(app)
    workflow_id = _graph_workflow(app)
    sched = _schedule_for(app, workflow_id)

    job_id = _spawn_scheduled_job(app, sched, "2026-07-17T10:00")

    assert job_id is not None
    job = dict(app.state.worker_db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    assert job["engine"] == "graph"
    assert job["schedule_id"] == sched["id"]
    assert job["status"] == "running"
    # The node ran, and the schedule's stored input reached its {{brief}}.
    run = app.state.worker_db.execute(
        "SELECT r.prompt FROM runs r JOIN node_states n ON n.run_id = r.id WHERE n.job_id = ?",
        (job_id,),
    ).fetchone()
    assert run is not None, "the graph job spawned but no node was dispatched"
    assert "Do the launch" in run["prompt"]


def test_scheduling_a_graph_is_skipped_when_the_feature_is_off(tmp_path):
    """The master switch means the executor would never dispatch it — better to skip than
    to leave a 'running' job nothing will advance."""
    app = _graph_app(tmp_path, graph_enabled=False)
    _client(app)
    sched = _schedule_for(app, _graph_workflow(app))

    job_id = _spawn_scheduled_job(app, sched, "2026-07-17T10:00")

    assert job_id is None
    assert app.state.worker_db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0
    # The minute is still claimed, so a skipped graph does not retry every tick.
    claimed = app.state.worker_db.execute(
        "SELECT last_run_minute FROM schedules WHERE id = ?", (sched["id"],)
    ).fetchone()["last_run_minute"]
    assert claimed == "2026-07-17T10:00"


def test_scheduling_a_linear_workflow_is_unchanged(tmp_path):
    app = _graph_app(tmp_path)
    c = _client(app)
    sched = _schedule_for(app, _wf(c))

    job_id = _spawn_scheduled_job(app, sched, "2026-07-17T10:00")

    job = dict(app.state.worker_db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    assert job["engine"] == "linear"
    assert job["steps_state"] != "[]"


def test_a_paused_workflow_does_not_fire_its_schedule(tmp_path):
    """The owner's rule: only active workflows run on a schedule. Pausing (draft) takes
    the template out of rotation; the minute is still claimed so it does not retry."""
    app = _graph_app(tmp_path)
    _client(app)
    workflow_id = _graph_workflow(app)
    sched = _schedule_for(app, workflow_id)
    app.state.worker_db.execute("UPDATE workflows SET status='draft' WHERE id=?", (workflow_id,))

    job_id = _spawn_scheduled_job(app, sched, "2026-07-17T11:00")

    assert job_id is None
    assert app.state.worker_db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0
    claimed = app.state.worker_db.execute(
        "SELECT last_run_minute FROM schedules WHERE id = ?", (sched["id"],)
    ).fetchone()["last_run_minute"]
    assert claimed == "2026-07-17T11:00"


def _scratch_repo(path):
    import subprocess
    from pathlib import Path

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> None:
        res = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=str(path), capture_output=True, text=True,
        )
        assert res.returncode == 0, f"git {args}: {res.stderr}"

    git("init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


def test_scheduled_repo_graph_binds_worktree_like_manual_start(tmp_path):
    """Cron / Run-now used to spawn graph jobs without target_area_id or a worktree,
    so a repo recipe could write into the live code area. Isolation must match
    POST /api/graph/jobs/{id}/start."""
    import json as _json
    from pathlib import Path

    from proxima_api.graph import normalize_graph

    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "feature_workflow_graph": True,
            "feature_repo_worktrees": True,
            "start_worker": False,
        }
    )
    c = _client(app)
    repo = tmp_path / "myrepo"
    _scratch_repo(repo)
    linked = c.post("/api/projects/link", json={"path": str(repo), "slug": "myrepo"})
    assert linked.status_code == 201, linked.text
    db = app.state.worker_db
    project_id = db.execute("SELECT id FROM projects WHERE slug = 'myrepo'").fetchone()["id"]
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    graph = normalize_graph({
        "nodes": [{
            "id": "fix",
            "name": "Fix",
            "instruction": "edit README",
            "target": ".",
        }],
    })
    wcur = db.execute(
        "INSERT INTO workflows(name, description, category, status, steps, graph, inputs, project_id, created_by) "
        "VALUES ('Repo recipe', '', 'other', 'active', '[]', ?, '[]', ?, ?)",
        (_json.dumps(graph), project_id, owner),
    )
    workflow_id = int(wcur.lastrowid)
    scur = db.execute(
        "INSERT INTO schedules(workflow_id, project_id, cron, input, enabled, overlap_policy, created_by) "
        "VALUES (?, ?, '* * * * *', '{}', 1, 'skip', ?)",
        (workflow_id, project_id, owner),
    )
    sched = dict(db.execute("SELECT * FROM schedules WHERE id = ?", (scur.lastrowid,)).fetchone())

    job_id = _spawn_scheduled_job(app, sched, "2026-07-17T12:00")

    assert job_id is not None
    job = dict(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    assert job["status"] == "running"
    assert job["target_area_id"] is not None
    wt = db.execute("SELECT * FROM job_worktrees WHERE job_id = ?", (job_id,)).fetchone()
    assert wt is not None, "scheduled repo graph must cut an isolated worktree"
    assert wt["status"] == "active"
    assert Path(wt["worktree_path"]).is_dir()
    # Live project tree stays clean — work happens only in the worktree.
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_scheduled_repo_graph_fails_visibly_when_worktree_cut_refused(tmp_path):
    """A dirty live repo must not spawn an unisolated running plan — fail the job."""
    import json as _json

    from proxima_api.graph import normalize_graph

    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "feature_workflow_graph": True,
            "feature_repo_worktrees": True,
            "start_worker": False,
        }
    )
    c = _client(app)
    repo = tmp_path / "dirty"
    _scratch_repo(repo)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    linked = c.post("/api/projects/link", json={"path": str(repo), "slug": "dirty"})
    assert linked.status_code == 201, linked.text
    db = app.state.worker_db
    project_id = db.execute("SELECT id FROM projects WHERE slug = 'dirty'").fetchone()["id"]
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    graph = normalize_graph({
        "nodes": [{"id": "fix", "name": "Fix", "instruction": "x", "target": "."}],
    })
    wcur = db.execute(
        "INSERT INTO workflows(name, description, category, status, steps, graph, inputs, project_id, created_by) "
        "VALUES ('Dirty recipe', '', 'other', 'active', '[]', ?, '[]', ?, ?)",
        (_json.dumps(graph), project_id, owner),
    )
    scur = db.execute(
        "INSERT INTO schedules(workflow_id, project_id, cron, input, enabled, overlap_policy, created_by) "
        "VALUES (?, ?, '* * * * *', '{}', 1, 'skip', ?)",
        (int(wcur.lastrowid), project_id, owner),
    )
    sched = dict(db.execute("SELECT * FROM schedules WHERE id = ?", (scur.lastrowid,)).fetchone())

    job_id = _spawn_scheduled_job(app, sched, "2026-07-17T13:00")

    assert job_id is not None
    job = dict(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    assert job["status"] == "failed"
    assert job["rejected_reason"] and "cannot start repo plan" in job["rejected_reason"]
    assert db.execute("SELECT COUNT(*) AS c FROM job_worktrees WHERE job_id = ?", (job_id,)).fetchone()["c"] == 0
    # No node run was dispatched against the live tree.
    assert db.execute(
        "SELECT COUNT(*) AS c FROM runs r JOIN node_states n ON n.run_id = r.id WHERE n.job_id = ?",
        (job_id,),
    ).fetchone()["c"] == 0

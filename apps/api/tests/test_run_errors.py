from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from proxima_api.container_activity import (
    ContainerBoundaryError,
    acquire_container_activity_lease,
)
from proxima_api.main import create_app


class FakeAcpProcess:
    def __init__(self, behavior: str):
        self.behavior = behavior
        self.cancelled = False

    async def load_session(self, session_id, cwd):
        raise Exception("not loadable")  # force a fresh new_session

    async def new_session(self, cwd):
        return "acp-test-1"

    async def prompt(
        self, session_id, text, on_update, on_permission=None, timeout=600, images=None
    ):
        if self.behavior == "fail":
            raise Exception("boom from runner")
        if self.behavior == "timeout":
            # Simulate a wedged agent turn: streams a little, then never returns,
            # so the worker's asyncio.wait_for fires a TimeoutError.
            on_update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "partial..."},
                }
            )
            raise asyncio.TimeoutError()
        if self.behavior == "stream":
            on_update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello world"},
                }
            )
        return "end_turn"

    def cancel(self, session_id):
        self.cancelled = True


class FakeAcpManager:
    def __init__(self, behavior: str):
        self.behavior = behavior
        self.recycled: list[tuple] = []

    async def get(self, spec=None, home=None, cwd=None, **_kwargs):
        return FakeAcpProcess(self.behavior)

    async def recycle(self, spec=None, home=None, cwd=None, **_kwargs):
        self.recycled.append((home, cwd))

    async def shutdown(self):
        pass


class RecoverableHistoryProcess(FakeAcpProcess):
    def __init__(self, first: bool):
        super().__init__("stream")
        self.first = first

    async def load_session(self, session_id, cwd):
        return None

    async def new_session(self, cwd):
        return "acp-recovered"

    async def prompt(
        self, session_id, text, on_update, on_permission=None, timeout=600, images=None
    ):
        if self.first:
            raise Exception(
                "[property_name_above_max_length] Invalid property name in 'input[48].arguments': '{{d...(' is too long"
            )
        on_update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "recovered ok"},
            }
        )
        return "end_turn"


class RecoverableHistoryManager(FakeAcpManager):
    def __init__(self):
        super().__init__("stream")
        self.calls = 0

    async def get(self, spec=None, home=None, cwd=None):
        self.calls += 1
        return RecoverableHistoryProcess(self.calls == 1)


class CancelBeforeFailProcess(FakeAcpProcess):
    def __init__(self, app):
        super().__init__("fail")
        self.app = app

    async def prompt(
        self, session_id, text, on_update, on_permission=None, timeout=600, images=None
    ):
        with self.app.state.db_lock:
            row = self.app.state.worker_db.execute(
                "SELECT id FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row is not None
            self.app.state.worker_db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
        raise Exception("boom after cancel")


class CancelBeforeFailManager(FakeAcpManager):
    def __init__(self, app):
        super().__init__("fail")
        self.app = app

    async def get(self, spec=None, home=None, cwd=None):
        return CancelBeforeFailProcess(self.app)


def _setup(tmp_path, behavior):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = FakeAcpManager(behavior)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/api/sessions", headers=headers, json={"title": "x"}).json()
    client.post(
        f"/api/sessions/{session['id']}/runs", headers=headers, json={"message": "hi"}
    )

    async def run_once():
        run = app.state.worker.claim_run()
        assert run is not None
        await app.state.worker.execute_run(run)

    asyncio.run(run_once())
    return client, headers, session


def test_failed_run_stores_error_message(tmp_path):
    client, headers, session = _setup(tmp_path, "fail")
    msgs = client.get(
        f"/api/sessions/{session['id']}/messages", headers=headers
    ).json()["messages"]
    err = [m for m in msgs if m["role"] == "error"]
    assert err and err[-1]["content"].startswith("Run failed")
    events = client.get(
        f"/api/sessions/{session['id']}/events", headers=headers
    ).json()["events"]
    assert any(e["type"] == "run.failed" for e in events)


def test_setup_failure_immediately_fails_claimed_run(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = c.post(
        "/api/projects",
        headers=headers,
        json={"slug": "broken", "name": "Broken"},
    )
    assert created.status_code == 201, created.text
    project_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'broken'"
    ).fetchone()["id"]
    not_a_directory = tmp_path / "project-is-a-file"
    not_a_directory.write_text("not a directory", encoding="utf-8")
    app.state.db.execute(
        "UPDATE projects SET path = ? WHERE id = ?",
        (str(not_a_directory), project_id),
    )
    session = c.post(
        "/api/sessions",
        headers=headers,
        json={"title": "setup failure", "project_slug": "broken"},
    ).json()
    run_id = c.post(
        f"/api/sessions/{session['id']}/runs",
        headers=headers,
        json={"message": "hi"},
    ).json()["run_id"]
    run = app.state.worker.claim_run()
    assert run and run["id"] == run_id

    asyncio.run(app.state.worker.execute_run(run))

    persisted = c.get(f"/api/runs/{run_id}", headers=headers).json()
    assert persisted["status"] == "failed"
    assert persisted["error"]
    events = c.get(f"/api/sessions/{session['id']}/events", headers=headers).json()[
        "events"
    ]
    assert any(e["type"] == "run.failed" and e["run_id"] == run_id for e in events)


def _assert_exclusive_quiescence_available(app, project_id: int) -> None:
    """Setup failures must not leave a stuck shared activity lease."""
    lease = acquire_container_activity_lease(
        app.state.db,
        int(project_id),
        shared=False,
        timeout=0.5,
    )
    try:
        assert lease is not None
    finally:
        lease.release()


def _project_session_run(
    tmp_path,
    *,
    slug: str = "lease-setup",
    message: str = "hi",
):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = FakeAcpManager("stream")
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/projects",
        headers=headers,
        json={"slug": slug, "name": slug.title()},
    )
    assert created.status_code == 201, created.text
    project_id = int(
        app.state.db.execute(
            "SELECT id FROM projects WHERE slug = ?",
            (slug,),
        ).fetchone()["id"]
    )
    session = client.post(
        "/api/sessions",
        headers=headers,
        json={"title": slug, "project_slug": slug},
    ).json()
    run_id = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=headers,
        json={"message": message},
    ).json()["run_id"]
    run = app.state.worker.claim_run()
    assert run and run["id"] == run_id
    return app, client, headers, project_id, run


def test_setup_failure_releases_project_activity_lease(tmp_path):
    """Broken project path after lease acquire must not block quiescence."""
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = c.post(
        "/api/projects",
        headers=headers,
        json={"slug": "lease-broken", "name": "Lease Broken"},
    )
    assert created.status_code == 201, created.text
    project_id = int(
        app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'lease-broken'"
        ).fetchone()["id"]
    )
    not_a_directory = tmp_path / "project-is-a-file"
    not_a_directory.write_text("not a directory", encoding="utf-8")
    app.state.db.execute(
        "UPDATE projects SET path = ? WHERE id = ?",
        (str(not_a_directory), project_id),
    )
    session = c.post(
        "/api/sessions",
        headers=headers,
        json={"title": "lease setup", "project_slug": "lease-broken"},
    ).json()
    run_id = c.post(
        f"/api/sessions/{session['id']}/runs",
        headers=headers,
        json={"message": "hi"},
    ).json()["run_id"]
    run = app.state.worker.claim_run()
    assert run and run["id"] == run_id

    asyncio.run(app.state.worker.execute_run(run))

    persisted = c.get(f"/api/runs/{run_id}", headers=headers).json()
    assert persisted["status"] == "failed"
    _assert_exclusive_quiescence_available(app, project_id)


def test_missing_worktree_setup_releases_activity_lease(tmp_path):
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="missing-wt",
    )
    db = app.state.db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    job_id = db.execute(
        "INSERT INTO jobs(project_id, session_id, title, status, input, steps_state, "
        "engine, created_by) VALUES (?, ?, 'wt job', 'running', '{}', '[]', 'linear', ?)",
        (project_id, run["session_id"], owner),
    ).lastrowid
    db.execute(
        "UPDATE sessions SET job_id = ? WHERE id = ?",
        (job_id, run["session_id"]),
    )
    missing = tmp_path / "gone-worktree"
    db.execute(
        "INSERT INTO job_worktrees(job_id, repo_path, worktree_path, branch, "
        "base_branch, base_commit, status) VALUES (?, ?, ?, 'b', 'main', 'abc', 'active')",
        (job_id, str(tmp_path / "repo"), str(missing)),
    )

    asyncio.run(app.state.worker.execute_run(run))

    persisted = client.get(f"/api/runs/{run['id']}", headers=headers).json()
    assert persisted["status"] == "failed"
    assert "worktree missing" in (persisted.get("error") or "")
    _assert_exclusive_quiescence_available(app, project_id)


def test_ops_root_setup_failure_releases_activity_lease(tmp_path, monkeypatch):
    import proxima_api.worker as worker_mod

    def boom_ops_root(*_args, **_kwargs):
        raise RuntimeError("ops_root exploded")

    monkeypatch.setattr(worker_mod, "ops_root", boom_ops_root)
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="ops-boom",
    )

    asyncio.run(app.state.worker.execute_run(run))

    persisted = client.get(f"/api/runs/{run['id']}", headers=headers).json()
    assert persisted["status"] == "failed"
    assert "ops_root exploded" in (persisted.get("error") or "")
    _assert_exclusive_quiescence_available(app, project_id)


def test_mkdir_setup_failure_releases_activity_lease(tmp_path, monkeypatch):
    import proxima_api.worker as worker_mod

    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="mkdir-boom",
    )
    original_mkdir = worker_mod.Path.mkdir

    def boom_mkdir(self, *args, **kwargs):
        raise OSError("mkdir refused")

    monkeypatch.setattr(worker_mod.Path, "mkdir", boom_mkdir)
    try:
        asyncio.run(app.state.worker.execute_run(run))
    finally:
        monkeypatch.setattr(worker_mod.Path, "mkdir", original_mkdir)

    persisted = client.get(f"/api/runs/{run['id']}", headers=headers).json()
    assert persisted["status"] == "failed"
    assert "mkdir refused" in (persisted.get("error") or "")
    _assert_exclusive_quiescence_available(app, project_id)


def test_snapshot_setup_failure_releases_activity_lease(tmp_path, monkeypatch):
    import proxima_api.worker as worker_mod

    def boom_snapshot(*_args, **_kwargs):
        raise RuntimeError("snapshot refused")

    monkeypatch.setattr(
        worker_mod.turn_restore,
        "capture_snapshot",
        boom_snapshot,
    )
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="snap-boom",
    )

    asyncio.run(app.state.worker.execute_run(run))

    persisted = client.get(f"/api/runs/{run['id']}", headers=headers).json()
    assert persisted["status"] == "failed"
    assert "snapshot refused" in (persisted.get("error") or "")
    _assert_exclusive_quiescence_available(app, project_id)


def test_pre_start_exception_releases_activity_lease(tmp_path, monkeypatch):
    """Exceptions after setup but before writer start still release the lease."""
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="pre-start",
    )

    async def boom_refresh(*_args, **_kwargs):
        raise RuntimeError("credentials refresh failed")

    monkeypatch.setattr(
        app.state.worker.prompting,
        "refresh_credentials_if_needed",
        boom_refresh,
    )

    asyncio.run(app.state.worker.execute_run(run))

    persisted = client.get(f"/api/runs/{run['id']}", headers=headers).json()
    assert persisted["status"] == "failed"
    assert "credentials refresh failed" in (persisted.get("error") or "")
    _assert_exclusive_quiescence_available(app, project_id)


def test_post_start_exception_releases_when_not_retained(tmp_path):
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="post-start",
    )
    app.state.acp_manager = FakeAcpManager("fail")

    asyncio.run(app.state.worker.execute_run(run))

    persisted = client.get(f"/api/runs/{run['id']}", headers=headers).json()
    assert persisted["status"] == "failed"
    _assert_exclusive_quiescence_available(app, project_id)


def test_ownership_transfer_keeps_activity_lease_blocker(tmp_path, monkeypatch):
    """When the lease is transferred to a writer-tree monitor, do not release."""
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="transfer",
    )

    class RetainingManager(FakeAcpManager):
        def __init__(self):
            super().__init__("fail")
            self.lease = None
            self.supports_activity_guardian = True

        async def get(self, spec=None, home=None, cwd=None, **kwargs):
            lease = kwargs.get("activity_lease")
            self.lease = lease
            if lease is not None:
                lease._retained_for_writer_tree = True
            raise RuntimeError("start failed after transfer")

    mgr = RetainingManager()
    app.state.acp_manager = mgr

    asyncio.run(app.state.worker.execute_run(run))

    assert mgr.lease is not None
    assert getattr(mgr.lease, "_retained_for_writer_tree", False) is True
    assert getattr(mgr.lease, "_released", False) is False

    blocked = False
    try:
        exclusive = acquire_container_activity_lease(
            app.state.db,
            int(project_id),
            shared=False,
            timeout=0.2,
        )
    except ContainerBoundaryError:
        blocked = True
    else:
        exclusive.release()
    assert blocked, "transferred lease must keep exclusive quiescence blocked"


def test_activity_lease_cleanup_is_idempotent_after_setup_failure(
    tmp_path,
    monkeypatch,
):
    import proxima_api.worker as worker_mod

    releases: list[str] = []
    real_acquire = worker_mod.acquire_container_activity_lease

    def tracking_acquire(*args, **kwargs):
        lease = real_acquire(*args, **kwargs)
        original_release = lease.release

        def tracked_release():
            releases.append("release")
            return original_release()

        lease.release = tracked_release  # type: ignore[method-assign]
        return lease

    monkeypatch.setattr(
        worker_mod,
        "acquire_container_activity_lease",
        tracking_acquire,
    )
    monkeypatch.setattr(
        worker_mod.turn_restore,
        "capture_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("snap")),
    )
    app, client, headers, project_id, run = _project_session_run(
        tmp_path,
        slug="idempotent",
    )

    asyncio.run(app.state.worker.execute_run(run))
    # A second execute_run on a fresh claim must not find a stuck lease.
    _assert_exclusive_quiescence_available(app, project_id)
    assert releases == ["release"]


def test_timed_out_run_recycles_agent_process(tmp_path):
    # A run that times out must recycle the cached agent process, otherwise the
    # next message in the project gets "Queued for the next turn" forever against
    # the still-wedged Hermes session.
    client, headers, session = _setup(tmp_path, "timeout")
    events = client.get(
        f"/api/sessions/{session['id']}/events", headers=headers
    ).json()["events"]
    failed = [e for e in events if e["type"] == "run.failed"]
    assert failed and "timed out" in str(failed[-1]["payload"]).lower()
    # The whole point of the fix: the wedged process is evicted from the cache.
    assert client.app.state.acp_manager.recycled, (
        "timed-out run did not recycle the agent process"
    )


def test_recoverable_agent_history_error_resets_acp_session_and_retries(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = RecoverableHistoryManager()
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sess = c.post("/api/sessions", headers=h, json={"title": "corrupt"}).json()
    profile = c.get("/api/profiles", headers=h).json()["profiles"][0]
    app.state.worker_db.execute(
        "INSERT OR REPLACE INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (?, ?, ?)",
        (sess["id"], profile["hermes_home"], "acp-corrupt"),
    )
    c.post(f"/api/sessions/{sess['id']}/runs", headers=h, json={"message": "hi"})
    run = app.state.worker.claim_run()
    asyncio.run(app.state.worker.execute_run(run))

    msgs = c.get(f"/api/sessions/{sess['id']}/messages", headers=h).json()["messages"]
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "recovered ok"
    row = app.state.worker_db.execute(
        "SELECT acp_session_id FROM agent_sessions WHERE session_id = ? AND hermes_home = ?",
        (sess["id"], profile["hermes_home"]),
    ).fetchone()
    assert row["acp_session_id"] == "acp-recovered"
    assert app.state.acp_manager.recycled


def test_claim_run_reaps_stale_run_blocking_queued_turn(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
            "run_stale_seconds": 60,
        }
    )
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sess = c.post("/api/sessions", headers=h, json={"title": "blocked"}).json()
    old_run = c.post(
        f"/api/sessions/{sess['id']}/runs", headers=h, json={"message": "old"}
    ).json()["run_id"]
    app.state.worker_db.execute(
        "UPDATE runs SET status = 'running', started_at = datetime('now','-10 minutes'), heartbeat_at = datetime('now','-10 minutes') WHERE id = ?",
        (old_run,),
    )
    new_run = c.post(
        f"/api/sessions/{sess['id']}/runs", headers=h, json={"message": "new"}
    ).json()["run_id"]

    claimed = app.state.worker.claim_run()

    assert claimed and claimed["id"] == new_run
    assert c.get(f"/api/runs/{old_run}", headers=h).json()["status"] == "failed"
    assert c.get(f"/api/runs/{new_run}", headers=h).json()["status"] == "running"
    events = c.get(f"/api/sessions/{sess['id']}/events", headers=h).json()["events"]
    old_events = [e["type"] for e in events if e["run_id"] == old_run]
    new_events = [e["type"] for e in events if e["run_id"] == new_run]
    assert "run.failed" in old_events
    assert new_events[-1] == "run.started"


def test_timeout_finalizer_does_not_overwrite_concurrent_cancel(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = FakeAcpManager("timeout")
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sess = c.post("/api/sessions", headers=h, json={"title": "x"}).json()
    c.post(f"/api/sessions/{sess['id']}/runs", headers=h, json={"message": "hi"})
    run = app.state.worker.claim_run()
    rid = run["id"]

    async def recycle_and_cancel(spec=None, home=None, cwd=None):
        app.state.acp_manager.recycled.append((home, cwd))
        with app.state.db_lock:
            app.state.worker_db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (rid,),
            )

    app.state.acp_manager.recycle = recycle_and_cancel
    asyncio.run(app.state.worker.execute_run(run))

    assert c.get(f"/api/runs/{rid}", headers=h).json()["status"] == "cancelled"
    events = c.get(f"/api/sessions/{sess['id']}/events", headers=h).json()["events"]
    assert not any(e["type"] == "run.failed" for e in events)


def test_exception_finalizer_does_not_overwrite_concurrent_cancel(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = CancelBeforeFailManager(app)
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sess = c.post("/api/sessions", headers=h, json={"title": "x"}).json()
    c.post(f"/api/sessions/{sess['id']}/runs", headers=h, json={"message": "hi"})
    run = app.state.worker.claim_run()
    rid = run["id"]

    asyncio.run(app.state.worker.execute_run(run))

    assert c.get(f"/api/runs/{rid}", headers=h).json()["status"] == "cancelled"
    msgs = c.get(f"/api/sessions/{sess['id']}/messages", headers=h).json()["messages"]
    assert not any(m["role"] == "error" for m in msgs)


def _goal_app(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = FakeAcpManager("stream")
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sess = c.post("/api/sessions", headers=h, json={"title": "g"}).json()
    pid = c.get("/api/profiles", headers=h).json()["profiles"][0]["id"]
    c.post(
        f"/api/sessions/{sess['id']}/goal",
        headers=h,
        json={"objective": "do x", "max_iter": 5, "profile_id": pid},
    )
    return app, c, h, sess["id"]


def test_cancel_during_post_prompt_window_is_not_resurrected_and_stops_goal(tmp_path):
    # The user cancels during the post-prompt auto-title await (a ~30s window).
    # The run must stay 'cancelled' (not resurrected to 'completed'), emit no
    # run.completed, and — critically — the goal loop must NOT enqueue another turn.
    app, c, h, sid = _goal_app(tmp_path)
    run = app.state.worker.claim_run()
    rid = run["id"]

    async def cancel_then_title(*a, **k):
        with app.state.db_lock:
            app.state.worker_db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (rid,),
            )
        return "Some Title"

    app.state.worker._generate_title = cancel_then_title
    asyncio.run(app.state.worker.execute_run(run))

    assert c.get(f"/api/runs/{rid}", headers=h).json()["status"] == "cancelled"
    events = c.get(f"/api/sessions/{sid}/events", headers=h).json()["events"]
    assert not any(e["type"] == "run.completed" for e in events)
    # no continuation turn was enqueued
    n = app.state.worker_db.execute(
        "SELECT COUNT(*) AS c FROM runs WHERE session_id = ?", (sid,)
    ).fetchone()["c"]
    assert n == 1


def test_cancel_goal_marks_active_run_cancelled(tmp_path):
    # cancel_goal must set the queued/running run to 'cancelled' so claim_run
    # won't execute one more goal turn after the user cancelled.
    app, c, h, sid = _goal_app(tmp_path)
    c.post(f"/api/sessions/{sid}/goal/cancel", headers=h)
    last = app.state.worker_db.execute(
        "SELECT status FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()
    assert last["status"] == "cancelled"
    assert app.state.worker.claim_run() is None


def test_cancel_goal_cancels_all_queued_session_runs(tmp_path):
    # If multiple goal/chat turns were queued before the user hit Cancel, none of
    # them should survive and execute after the goal is cancelled.
    app, c, h, sid = _goal_app(tmp_path)
    pid = c.get("/api/profiles", headers=h).json()["profiles"][0]["id"]
    c.post(
        f"/api/sessions/{sid}/goal",
        headers=h,
        json={"objective": "do y", "max_iter": 5, "profile_id": pid},
    )

    c.post(f"/api/sessions/{sid}/goal/cancel", headers=h)

    statuses = [
        r["status"]
        for r in app.state.worker_db.execute(
            "SELECT status FROM runs WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
    ]
    assert statuses == ["cancelled", "cancelled"]
    assert app.state.worker.claim_run() is None


def test_late_stream_events_after_cancel_are_ignored(tmp_path):
    app, c, h, sid = _goal_app(tmp_path)
    run = app.state.worker.claim_run()
    rid = run["id"]
    app.state.worker_db.execute(
        "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (rid,),
    )

    app.state.worker.add_event(rid, sid, None, "message.delta", {"text": "late"})
    app.state.worker.add_event(
        rid, sid, None, "approval.request", {"request_id": "late"}
    )
    app.state.worker.add_event(rid, sid, None, "run.cancelled", {})

    events = c.get(f"/api/sessions/{sid}/events", headers=h).json()["events"]
    assert [e["type"] for e in events if e["run_id"] == rid] == [
        "goal.update",
        "run.queued",
        "run.started",
        "run.cancelled",
    ]


def test_streamed_run_persists_assistant_message_and_acp_session(tmp_path):
    client, headers, session = _setup(tmp_path, "stream")
    msgs = client.get(
        f"/api/sessions/{session['id']}/messages", headers=headers
    ).json()["messages"]
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst and asst[-1]["content"] == "hello world"
    events = client.get(
        f"/api/sessions/{session['id']}/events", headers=headers
    ).json()["events"]
    assert any(e["type"] == "message.delta" for e in events)
    assert any(e["type"] == "run.completed" for e in events)


def test_auto_title_does_not_overwrite_manual_rename_during_first_run(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    app.state.acp_manager = FakeAcpManager("stream")
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    sess = c.post("/api/sessions", headers=h, json={"title": "Original"}).json()
    c.post(f"/api/sessions/{sess['id']}/runs", headers=h, json={"message": "hi"})
    run = app.state.worker.claim_run()
    assert run is not None

    async def rename_then_title(*a, **k):
        c.patch(f"/api/sessions/{sess['id']}", headers=h, json={"title": "Manual Name"})
        return "Auto Title"

    app.state.worker._generate_title = rename_then_title
    asyncio.run(app.state.worker.execute_run(run))

    assert (
        c.get("/api/sessions", headers=h).json()["sessions"][0]["title"]
        == "Manual Name"
    )

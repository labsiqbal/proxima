"""Timeout auto-continuation (Phase-1 slice 5, T5).

A job turn that hits the per-turn quota is resumed, not abandoned: a continuation
run is enqueued in the SAME session (context carries) and - for repo jobs - the
same worktree (files persist). The chain is capped (config `run_continuation_limit`,
default 5); at the cap the job fails loudly / the plan pauses for review with a
plain-language reason. Chat and goal-mode timeout behavior is unchanged.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from proxima_api import app_settings
from proxima_api.graph import normalize_graph
from proxima_api.main import _config_from_env, create_app


class FakeAcpProcess:
    def __init__(self, manager: "FakeAcpManager"):
        self.manager = manager

    async def load_session(self, session_id, cwd):
        raise Exception("not loadable")  # force a fresh new_session

    async def new_session(self, cwd):
        self.manager.sessions += 1
        return f"acp-test-{self.manager.sessions}"

    async def prompt(self, session_id, text, on_update, on_permission=None, timeout=600, images=None):
        self.manager.prompts.append({"text": text, "timeout": timeout})
        if self.manager.behavior == "timeout":
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial..."}})
            raise asyncio.TimeoutError()
        on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}})
        return "end_turn"

    def cancel(self, session_id):
        pass


class FakeAcpManager:
    """Records every cwd/prompt so tests can assert worktree binding and quota."""

    def __init__(self, behavior: str):
        self.behavior = behavior
        self.cwds: list[str] = []
        self.prompts: list[dict[str, Any]] = []
        self.recycled: list[tuple] = []
        self.sessions = 0

    async def get(self, spec=None, home=None, cwd=None):
        self.cwds.append(cwd)
        return FakeAcpProcess(self)

    async def recycle(self, spec=None, home=None, cwd=None):
        self.recycled.append((home, cwd))

    async def shutdown(self):
        pass


def _app(tmp_path, **extra_config):
    config: dict[str, Any] = {
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        **extra_config,
    }
    app = create_app(config)
    app.state.acp_manager = FakeAcpManager("timeout")
    return app


def _client(app) -> TestClient:
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _execute_next(app) -> dict[str, Any]:
    run = app.state.worker.claim_run()
    assert run is not None
    asyncio.run(app.state.worker.execute_run(run))
    return run


def _run_row(app, run_id: int) -> dict[str, Any]:
    return dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def _session_runs(app, session_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in app.state.db.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()]


def _events(app, session_id: int) -> list[dict[str, Any]]:
    return [
        {**dict(r), "payload": json.loads(r["payload"])}
        for r in app.state.db.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    ]


def _start_adhoc_job(c, brief: str = "long refactor") -> tuple[int, int]:
    job = c.post("/api/jobs", json={"input": {"brief": brief}}).json()
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    return job["id"], job["session_id"]


# ── linear jobs ──────────────────────────────────────────────────────────────

def test_timed_out_linear_job_run_enqueues_continuation_in_same_session(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    jid, sid = _start_adhoc_job(c)

    original = _execute_next(app)

    runs = _session_runs(app, sid)
    assert len(runs) == 2, "timeout must enqueue exactly one continuation run"
    failed, continuation = runs
    assert failed["id"] == original["id"]
    assert failed["status"] == "failed"
    assert "auto-continuing (1/5)" in failed["error"]
    assert continuation["status"] == "queued"
    assert continuation["session_id"] == sid  # same session -> context carries
    assert continuation["continued_from_run_id"] == failed["id"]
    assert continuation["continuation_count"] == 1
    assert "CONTINUE from where it stopped" in continuation["prompt"]
    assert "continuation 1 of 5" in continuation["prompt"]

    job = c.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "running", "a continued job must not fail or stall"
    assert job["steps_state"][0]["status"] == "running"
    assert job["steps_state"][0]["run_id"] == continuation["id"]

    events = _events(app, sid)
    failed_events = [e for e in events if e["type"] == "run.failed"]
    assert failed_events and failed_events[-1]["payload"]["continued_by_run_id"] == continuation["id"]
    queued = [e for e in events if e["type"] == "run.queued" and e["run_id"] == continuation["id"]]
    assert queued and queued[0]["payload"]["continuation"] == 1
    assert queued[0]["payload"]["continuation_limit"] == 5

    # The salvage behavior is unchanged: streamed partial text lands in the chat.
    msgs = app.state.db.execute(
        "SELECT content FROM messages WHERE session_id = ? AND role = 'assistant'", (sid,)
    ).fetchall()
    assert any("partial..." in m["content"] for m in msgs)


def test_continuation_chain_counts_up_and_reuses_the_same_worktree(tmp_path):
    # Repo jobs: the continuation run executes in the SAME isolated worktree,
    # because cwd binds to the job's active worktree row, not to the run.
    app = _app(tmp_path)
    c = _client(app)
    jid, sid = _start_adhoc_job(c)
    worktree = tmp_path / "wt" / f"job-{jid}"
    worktree.mkdir(parents=True)
    app.state.db.execute(
        "INSERT INTO job_worktrees(job_id, repo_path, worktree_path, branch, base_branch, base_commit, status) "
        "VALUES (?, ?, ?, 'proxima/job', 'main', 'deadbeef', 'active')",
        (jid, str(tmp_path / "repo"), str(worktree)),
    )

    _execute_next(app)   # original turn times out -> continuation 1
    _execute_next(app)   # continuation 1 times out -> continuation 2

    runs = _session_runs(app, sid)
    assert [r["continuation_count"] for r in runs] == [0, 1, 2]
    assert runs[1]["continued_from_run_id"] == runs[0]["id"]
    assert runs[2]["continued_from_run_id"] == runs[1]["id"]
    assert "auto-continuing (2/5)" in runs[1]["error"]
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "running"
    # Both the original turn and its continuation ran in the job's worktree.
    assert app.state.acp_manager.cwds == [str(worktree), str(worktree)]


def test_continuation_cap_fails_linear_job_loudly(tmp_path):
    app = _app(tmp_path, run_continuation_limit=2)
    c = _client(app)
    jid, sid = _start_adhoc_job(c)
    # The queued run is already the chain's last allowed continuation.
    app.state.db.execute(
        "UPDATE runs SET continuation_count = 2 WHERE session_id = ?", (sid,)
    )

    original = _execute_next(app)

    runs = _session_runs(app, sid)
    assert len(runs) == 1, "at the cap no further continuation may be enqueued"
    failed = _run_row(app, original["id"])
    assert failed["status"] == "failed"
    # Honest stop: a plain-language reason, not a bare timeout string.
    assert "2 automatic continuations" in failed["error"]
    assert "split it into smaller jobs" in failed["error"]
    job = c.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "failed", "a capped job must fail loudly, never sit in limbo"
    assert "automatic continuations" in job["steps_state"][0]["error"]


# ── graph (plan) jobs ────────────────────────────────────────────────────────

def _create_graph_job(app, graph: dict[str, Any]) -> int:
    db = app.state.worker_db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    profile = db.execute(
        "SELECT * FROM profiles WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1",
        (owner,),
    ).fetchone()
    session_id = db.execute(
        "INSERT INTO sessions(title, owner_user_id, profile_id, runner_id, visibility, mode) "
        "VALUES ('Graph parent', ?, ?, ?, 'private', 'chat')",
        (owner, profile["id"], profile["runner_id"]),
    ).lastrowid
    job_id = db.execute(
        "INSERT INTO jobs(session_id, title, status, input, steps_state, engine, graph, created_by) "
        "VALUES (?, 'Graph job', 'running', ?, '[]', 'graph', ?, ?)",
        (session_id, json.dumps({"brief": "Plan"}), json.dumps(graph), owner),
    ).lastrowid
    db.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
    assert job_id is not None
    return job_id


def _single_node_graph() -> dict[str, Any]:
    return normalize_graph({"nodes": [{"id": "build", "name": "Build", "instruction": "Do it"}]})


def _node_state(app, job_id: int, node_id: str) -> dict[str, Any]:
    return dict(app.state.worker_db.execute(
        "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?", (job_id, node_id)
    ).fetchone())


def test_timed_out_graph_node_run_reattaches_node_to_continuation(tmp_path):
    app = _app(tmp_path)
    _client(app)
    job_id = _create_graph_job(app, _single_node_graph())
    run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)
    assert len(run_ids) == 1

    original = _execute_next(app)

    node = _node_state(app, job_id, "build")
    assert node["status"] == "running", "the node stays live across a continuation"
    assert node["run_id"] != original["id"], "the node must follow the continuation run"
    continuation = _run_row(app, node["run_id"])
    assert continuation["status"] == "queued"
    assert continuation["kind"] == "wf_node"
    assert continuation["session_id"] == original["session_id"]
    assert continuation["continued_from_run_id"] == original["id"]
    assert continuation["continuation_count"] == 1
    assert "CONTINUE from where it stopped" in continuation["prompt"]
    job = app.state.db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["status"] == "running"


def test_graph_node_continuation_cap_pauses_plan_for_review(tmp_path):
    app = _app(tmp_path, run_continuation_limit=3)
    _client(app)
    job_id = _create_graph_job(app, _single_node_graph())
    run_ids = app.state.worker.graph_executor.dispatch_ready(job_id)
    app.state.db.execute("UPDATE runs SET continuation_count = 3 WHERE id = ?", (run_ids[0],))

    _execute_next(app)

    node = _node_state(app, job_id, "build")
    assert node["status"] == "failed"
    assert "3 automatic continuations" in node["error"]
    job = app.state.db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["status"] == "review", "at the cap the plan pauses for review"
    runs = _session_runs(app, _run_row(app, run_ids[0])["session_id"])
    assert len(runs) == 1, "no continuation past the cap"


# ── unchanged flows ──────────────────────────────────────────────────────────

def test_chat_timeout_does_not_enqueue_a_continuation(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    sess = c.post("/api/sessions", json={"title": "chat"}).json()
    c.post(f"/api/sessions/{sess['id']}/runs", json={"message": "hi"})

    original = _execute_next(app)

    runs = _session_runs(app, sess["id"])
    assert len(runs) == 1
    assert _run_row(app, original["id"])["error"] == "Hermes runner timed out"
    assert app.state.acp_manager.recycled, "timeout must still recycle the wedged agent"


def test_goal_mode_timeout_behavior_is_unchanged(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    sess = c.post("/api/sessions", json={"title": "goal"}).json()
    pid = c.get("/api/profiles").json()["profiles"][0]["id"]
    c.post(f"/api/sessions/{sess['id']}/goal", json={"objective": "do x", "max_iter": 5, "profile_id": pid})

    _execute_next(app)

    runs = _session_runs(app, sess["id"])
    assert len(runs) == 1, "goal mode gets no timeout continuation (T5: superseded, untouched)"
    goal = app.state.db.execute(
        "SELECT goal_status FROM sessions WHERE id = ?", (sess["id"],)
    ).fetchone()
    assert goal["goal_status"] == "running"  # pre-slice-5 behavior, unchanged


# ── turn quota setting ───────────────────────────────────────────────────────

def test_run_timeout_setting_precedence():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    # Unset -> config fallback -> hard default.
    assert app_settings.get_run_timeout_seconds(conn, {"run_timeout_seconds": 1234}) == 1234
    assert app_settings.get_run_timeout_seconds(conn, {}) == 900
    # The in-app setting wins over config.
    app_settings.set_run_timeout_seconds(conn, 1800)
    assert app_settings.get_run_timeout_seconds(conn, {"run_timeout_seconds": 1234}) == 1800
    # Garbage or out-of-range stored values fall back instead of breaking runs.
    conn.execute("UPDATE app_settings SET value = 'abc' WHERE key = 'run_timeout_seconds'")
    assert app_settings.get_run_timeout_seconds(conn, {"run_timeout_seconds": 1234}) == 1234
    conn.execute("UPDATE app_settings SET value = '5' WHERE key = 'run_timeout_seconds'")
    assert app_settings.get_run_timeout_seconds(conn, {"run_timeout_seconds": 1234}) == 1234
    for bad in (59, 7201, "x"):
        try:
            app_settings.set_run_timeout_seconds(conn, bad)  # type: ignore[arg-type]
            raise AssertionError(f"accepted invalid timeout {bad!r}")
        except ValueError:
            pass


def test_worker_uses_the_in_app_turn_quota_setting(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    saved = c.put("/api/settings/runs", json={"run_timeout_seconds": 1200})
    assert saved.status_code == 200
    sess = c.post("/api/sessions", json={"title": "quota"}).json()
    c.post(f"/api/sessions/{sess['id']}/runs", json={"message": "hi"})

    _execute_next(app)

    assert app.state.acp_manager.prompts[-1]["timeout"] == 1200


def test_run_settings_routes_roundtrip_and_validation(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    initial = c.get("/api/settings/runs").json()
    assert initial["run_timeout_seconds"] == 900
    assert initial["default_run_timeout_seconds"] == 900
    assert initial["continuation_limit"] == 5

    assert c.put("/api/settings/runs", json={"run_timeout_seconds": 1800}).status_code == 200
    assert c.get("/api/settings/runs").json()["run_timeout_seconds"] == 1800

    assert c.put("/api/settings/runs", json={"run_timeout_seconds": 10}).status_code == 400
    assert c.put("/api/settings/runs", json={"run_timeout_seconds": "abc"}).status_code == 400
    assert c.put("/api/settings/runs", json={}).status_code == 400
    assert c.get("/api/settings/runs").json()["run_timeout_seconds"] == 1800


def test_asgi_entrypoint_env_config_carries_run_quota_keys(monkeypatch):
    # The plain `uvicorn proxima_api.main:app` path must honor the same env
    # overrides as scripts/serve.py (T5 closed this gap).
    monkeypatch.setenv("PROXIMA_RUN_TIMEOUT_SECONDS", "1500")
    monkeypatch.setenv("PROXIMA_RUN_CONTINUATION_LIMIT", "7")
    cfg = _config_from_env()
    assert cfg["run_timeout_seconds"] == 1500
    assert cfg["run_continuation_limit"] == 7

    monkeypatch.delenv("PROXIMA_RUN_TIMEOUT_SECONDS")
    monkeypatch.delenv("PROXIMA_RUN_CONTINUATION_LIMIT")
    cfg = _config_from_env()
    assert cfg["run_timeout_seconds"] == 900
    assert cfg["run_continuation_limit"] == 5

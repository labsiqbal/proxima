"""Satpam supervision loop (Phase-1 slice 12, T10).

Covers every detection rung against fixture states (stalled via worktree
signatures, looping via salvaged-output hashes, confused via the continuation
cap and repeated contract failures), the action-ladder automation boundaries -
steer is automatic, restart-clean is automatic ONLY for non-repo work while a
repo restart parks as a pending approval card (the CRITICAL boundary) -
decision-hold (dependents hold, independent branches keep dispatching, the
owner's answer re-runs the node), timeline events, the Settings thresholds,
fail-quiet behavior, and the regression that a normally-progressing job is
never touched.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from proxima_api import satpam as satpam_mod
from proxima_api.graph import normalize_graph
from proxima_api.main import create_app


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert res.returncode == 0, f"git {args}: {res.stderr}"
    return res.stdout.strip()


def _scratch_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _app(tmp_path: Path, **config):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(tmp_path)],
        "start_worker": False,
        "feature_repo_worktrees": True,
        "feature_workflow_graph": True,
        **config,
    })


def _client(app) -> TestClient:
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _repo_job(c: TestClient, slug: str, folder: Path, brief: str = "change the code") -> dict:
    p = c.post("/api/projects/link", json={"path": str(folder), "slug": slug})
    assert p.status_code == 201, p.text
    area_id = p.json()["code_areas"][0]["id"]
    job = c.post("/api/jobs", json={"project_slug": slug, "input": {"brief": brief}, "target_area_id": area_id})
    assert job.status_code == 200, job.text
    return job.json()


def _ops_job(c: TestClient, brief: str = "write a report") -> dict:
    job = c.post("/api/jobs", json={"input": {"brief": brief}})
    assert job.status_code == 200, job.text
    return job.json()


def _advance_chain(app, session_id: int, *, salvage: str | None = None) -> int:
    """End the chain's current turn and enqueue its continuation with the same
    rows the worker's timeout path writes: the previous run failed, its salvaged
    text as an assistant message, a queued continuation run one ordinal deeper,
    and (for graph chains) the node re-attached to the new run."""
    db = app.state.db
    prev = db.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
    ).fetchone()
    assert prev is not None, "chain has no run to continue"
    db.execute(
        "UPDATE runs SET status='failed', error='Hermes runner timed out', finished_at=CURRENT_TIMESTAMP WHERE id=?",
        (prev["id"],),
    )
    if salvage:
        db.execute(
            "INSERT INTO messages(session_id, role, content, run_id) VALUES (?, 'assistant', ?, ?)",
            (session_id, salvage, prev["id"]),
        )
    cur = db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, "
        "model, hermes_home, kind, continued_from_run_id, continuation_count) "
        "VALUES (?, ?, ?, ?, ?, 'queued', 'continue where you stopped', ?, ?, ?, ?, ?)",
        (
            prev["session_id"], prev["project_id"], prev["user_id"], prev["profile_id"],
            prev["runner_id"], prev["model"], prev["hermes_home"], prev["kind"] or "chat",
            prev["id"], (prev["continuation_count"] or 0) + 1,
        ),
    )
    new_id = int(cur.lastrowid)
    db.execute(
        "UPDATE node_states SET run_id = ?, version = version + 1 WHERE run_id = ?",
        (new_id, prev["id"]),
    )
    return new_id


def _interventions(app, job_id: int) -> list[dict[str, Any]]:
    return [
        dict(r) for r in app.state.db.execute(
            "SELECT * FROM satpam_interventions WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
    ]


def _events(app, session_id: int, prefix: str = "satpam.") -> list[dict[str, Any]]:
    rows = app.state.db.execute(
        "SELECT type, payload FROM events WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()
    return [
        {"type": r["type"], **json.loads(r["payload"])}
        for r in rows if r["type"].startswith(prefix)
    ]


def _run(app, run_id: int) -> dict[str, Any]:
    return dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def _job(app, job_id: int) -> dict[str, Any]:
    return dict(app.state.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


# ── graph fixtures ────────────────────────────────────────────────────────


def _create_graph_job(app, graph: dict[str, Any]) -> int:
    db = app.state.db
    owner = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    profile = db.execute(
        "SELECT * FROM profiles WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1",
        (owner,),
    ).fetchone()
    session_id = db.execute(
        "INSERT INTO sessions(title, owner_user_id, profile_id, runner_id, visibility, mode) "
        "VALUES ('Plan parent', ?, ?, ?, 'private', 'chat')",
        (owner, profile["id"], profile["runner_id"]),
    ).lastrowid
    job_id = db.execute(
        "INSERT INTO jobs(session_id, title, status, input, steps_state, engine, graph, created_by) "
        "VALUES (?, 'Plan', 'running', ?, '[]', 'graph', ?, ?)",
        (session_id, json.dumps({"brief": "go"}), json.dumps(graph), owner),
    ).lastrowid
    db.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
    return int(job_id)


def _node_state(app, job_id: int, node_id: str) -> dict[str, Any]:
    return dict(app.state.db.execute(
        "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?", (job_id, node_id)
    ).fetchone())


def _node_run(app, job_id: int, node_id: str) -> dict[str, Any]:
    return _run(app, int(_node_state(app, job_id, node_id)["run_id"]))


def _finish_node_run(app, job_id: int, node_id: str, answer: str) -> bool:
    """Complete a node's current run and push the answer through the real
    advancer, exactly as the worker does after a successful turn."""
    run = _node_run(app, job_id, node_id)
    app.state.db.execute(
        "UPDATE runs SET status='completed', finished_at=CURRENT_TIMESTAMP WHERE id=?",
        (run["id"],),
    )
    worker = app.state.worker
    return worker.graph_advancers.advance_run(run, answer, worker.add_event)


# ── detection: stalled (repo chain, worktree signature) ───────────────────


def test_stalled_repo_chain_steers_once_automatically(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    session_id = _job(app, job["id"])["session_id"]
    sat = app.state.worker.satpam

    # Turn 1 ends with an untouched worktree: one strike, below N=2 - no action.
    _advance_chain(app, session_id, salvage="working on it")
    sat.tick()
    assert _interventions(app, job["id"]) == []

    # Turn 2 also ends with no repo change: stalled -> automatic steer.
    cont = _advance_chain(app, session_id, salvage="still working")
    sat.tick()
    ivs = _interventions(app, job["id"])
    assert [(i["action"], i["detection"], i["status"]) for i in ivs] == [("steer", "stalled", "applied")]
    # The queued continuation's prompt was amended in place - the steer rides
    # into the very next turn, and it is visibly supervisor text.
    prompt = _run(app, cont)["prompt"]
    assert "SUPERVISOR NOTE" in prompt and "no new" in prompt.lower()
    # Visible intervention: a satpam.steered timeline event exists.
    assert [e["type"] for e in _events(app, session_id)] == ["satpam.steered"]
    # Steering never touches the job itself.
    assert _job(app, job["id"])["status"] == "running"


def test_healthy_progressing_repo_job_is_never_touched(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    session_id = _job(app, job["id"])["session_id"]
    wt_path = Path(c.get(f"/api/jobs/{job['id']}").json()["worktree"]["worktree_path"])
    sat = app.state.worker.satpam

    for turn, text in enumerate(["progress one", "progress two", "progress three"], start=1):
        (wt_path / "work.txt").write_text(f"turn {turn}\n", encoding="utf-8")
        _advance_chain(app, session_id, salvage=text)
        sat.tick()

    assert _interventions(app, job["id"]) == []
    assert _events(app, session_id) == []
    watch = app.state.db.execute(
        "SELECT * FROM satpam_watch WHERE session_id = ?", (session_id,)
    ).fetchone()
    assert watch["stall_turns"] == 0 and watch["loop_turns"] == 0 and watch["steer_count"] == 0
    # And the payload carries no satpam section - untouched jobs look exactly
    # as they did before this slice.
    assert "satpam" not in c.get(f"/api/jobs/{job['id']}").json()


def test_job_without_continuations_is_ignored_entirely(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    job = _ops_job(c)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    app.state.worker.satpam.tick()
    assert app.state.db.execute("SELECT COUNT(*) AS c FROM satpam_watch").fetchone()["c"] == 0
    assert _interventions(app, job["id"]) == []


# ── detection: looping (salvaged output hash) ─────────────────────────────


def test_looping_ops_chain_detected_by_identical_salvage(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    job = _ops_job(c)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    session_id = _job(app, job["id"])["session_id"]
    sat = app.state.worker.satpam

    # Turn 1 stores the baseline; turns 2 and 3 repeat it -> looping at N=2.
    _advance_chain(app, session_id, salvage="I will now analyze the data.")
    sat.tick()
    _advance_chain(app, session_id, salvage="I will now analyze the data.")
    sat.tick()
    assert _interventions(app, job["id"]) == []
    _advance_chain(app, session_id, salvage="I will now analyze  the data.")  # whitespace-insensitive
    sat.tick()
    ivs = _interventions(app, job["id"])
    assert [(i["action"], i["detection"]) for i in ivs] == [("steer", "looping")]


# ── action ladder: restart automation boundary ────────────────────────────


def test_nonrepo_graph_node_restarts_automatically_after_failed_steer(tmp_path: Path):
    app = _app(tmp_path)
    _client(app)
    graph = normalize_graph({"nodes": [{"id": "scout", "name": "Scout", "instruction": "Research"}]})
    job_id = _create_graph_job(app, graph)
    app.state.worker.graph_executor.dispatch_ready(job_id)
    first_session = int(_node_run(app, job_id, "scout")["session_id"])
    sat = app.state.worker.satpam

    # Loop to a steer (baseline + N identical turns), then keep looping.
    for _ in range(3):
        _advance_chain(app, first_session, salvage="same thing again")
        sat.tick()
    assert [(i["action"], i["status"]) for i in _interventions(app, job_id)] == [("steer", "applied")]
    for _ in range(2):
        _advance_chain(app, first_session, salvage="same thing again")
        sat.tick()

    ivs = _interventions(app, job_id)
    assert [(i["action"], i["status"]) for i in ivs] == [("steer", "applied"), ("restart", "applied")]
    # The stuck attempt was cancelled and the node re-dispatched in a FRESH session.
    node = _node_state(app, job_id, "scout")
    assert node["status"] == "running"
    new_run = _run(app, int(node["run_id"]))
    assert int(new_run["session_id"]) != first_session
    assert new_run["continuation_count"] == 0
    old_runs = app.state.db.execute(
        "SELECT status FROM runs WHERE session_id = ?", (first_session,)
    ).fetchall()
    assert {r["status"] for r in old_runs} <= {"failed", "cancelled"}
    # The job itself kept running throughout - restart is not a plan pause.
    assert _job(app, job_id)["status"] == "running"
    assert any(e["type"] == "satpam.restarted" for e in _events(app, first_session))


def test_nonrepo_linear_job_restarts_from_step_one_with_fresh_context(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    job = _ops_job(c, brief="summarize the findings")
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    session_id = _job(app, job["id"])["session_id"]
    app.state.db.execute(
        "INSERT INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (?, 'home', 'acp-1')",
        (session_id,),
    )
    sat = app.state.worker.satpam
    for _ in range(5):
        _advance_chain(app, session_id, salvage="round and round")
        sat.tick()

    assert [i["action"] for i in _interventions(app, job["id"])] == ["steer", "restart"]
    fresh = _job(app, job["id"])
    assert fresh["status"] == "running" and fresh["current_step_idx"] == 0
    steps = json.loads(fresh["steps_state"])
    assert steps[0]["status"] == "running" and steps[0].get("run_id")
    new_run = _run(app, steps[0]["run_id"])
    assert new_run["status"] == "queued" and new_run["continuation_count"] == 0
    # Fresh context: the ACP session mapping is dropped so the re-run starts clean.
    acp = app.state.db.execute(
        "SELECT COUNT(*) AS c FROM agent_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()["c"]
    assert acp == 0


def test_repo_restart_requires_owner_approval_and_never_fires_alone(tmp_path: Path):
    """CRITICAL boundary (T10, captain-ratified): a repo job's restart-clean
    discards agent work, so the satpam may only QUEUE it - the job, its runs,
    and its worktree must be untouched until the owner approves in-app."""
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    job_id = job["id"]
    session_id = _job(app, job_id)["session_id"]
    wt_before = c.get(f"/api/jobs/{job_id}").json()["worktree"]
    sat = app.state.worker.satpam

    # Leave one uncommitted edit, then stall long enough to pass the steer rung.
    # The WIP file makes turn 1 read as progress (it IS a repo change vs the
    # fresh cut), so the stall strikes start at turn 2 and steer fires at 3.
    Path(wt_before["worktree_path"], "half-done.txt").write_text("wip\n", encoding="utf-8")
    for _ in range(3):
        _advance_chain(app, session_id, salvage="stuck")
        sat.tick()
    assert [(i["action"], i["status"]) for i in _interventions(app, job_id)] == [("steer", "applied")]
    for _ in range(2):
        _advance_chain(app, session_id, salvage="stuck")
        sat.tick()

    ivs = _interventions(app, job_id)
    assert [(i["action"], i["status"]) for i in ivs] == [("steer", "applied"), ("restart", "pending")]
    pending = ivs[-1]
    assert "DISCARD" in pending["reason"]
    # NOTHING was executed: job still running, chain alive, worktree + WIP intact.
    assert _job(app, job_id)["status"] == "running"
    latest = app.state.db.execute(
        "SELECT status FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
    ).fetchone()
    assert latest["status"] == "queued"
    assert Path(wt_before["worktree_path"], "half-done.txt").is_file()
    assert any(e["type"] == "satpam.restart.queued" for e in _events(app, session_id))
    # More stalled turns do NOT pile on more cards while one is pending.
    _advance_chain(app, session_id, salvage="stuck")
    sat.tick()
    assert len(_interventions(app, job_id)) == 2
    # The job payload carries the pending card for the Tasks UI.
    payload = c.get(f"/api/jobs/{job_id}").json()
    assert any(i["status"] == "pending" and i["action"] == "restart" for i in payload["satpam"])

    # Owner approves: worktree re-cut fresh from HEAD, WIP gone, job re-running.
    approved = c.post(f"/api/jobs/{job_id}/satpam/{pending['id']}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert any(i["status"] == "approved" for i in body["satpam"])
    wt_after = body["worktree"]
    assert wt_after["status"] == "active"
    assert wt_after["base_commit"] == _git(repo, "rev-parse", "HEAD")
    assert not Path(wt_after["worktree_path"], "half-done.txt").exists()
    assert body["status"] == "running"
    steps = body["steps_state"]
    assert steps[0]["status"] == "running" and steps[0].get("run_id")
    fresh_run = _run(app, steps[0]["run_id"])
    assert fresh_run["status"] == "queued" and fresh_run["continuation_count"] == 0


def test_pending_repo_restart_can_be_dismissed(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    job_id = job["id"]
    session_id = _job(app, job_id)["session_id"]
    sat = app.state.worker.satpam
    for _ in range(4):
        _advance_chain(app, session_id, salvage="stuck")
        sat.tick()
    pending = _interventions(app, job_id)[-1]
    assert pending["status"] == "pending"

    dismissed = c.post(f"/api/jobs/{job_id}/satpam/{pending['id']}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    row = app.state.db.execute(
        "SELECT status, resolved_at FROM satpam_interventions WHERE id = ?", (pending["id"],)
    ).fetchone()
    assert row["status"] == "dismissed" and row["resolved_at"]
    assert _job(app, job_id)["status"] == "running"
    # Dismissing twice is a 409, not a silent success.
    assert c.post(f"/api/jobs/{job_id}/satpam/{pending['id']}/dismiss").status_code == 409


def test_escalates_after_a_restart_did_not_help(tmp_path: Path):
    app = _app(tmp_path)
    _client(app)
    graph = normalize_graph({"nodes": [{"id": "scout", "name": "Scout", "instruction": "Research"}]})
    job_id = _create_graph_job(app, graph)
    app.state.worker.graph_executor.dispatch_ready(job_id)
    sat = app.state.worker.satpam

    # First stuck episode: steer, then automatic (non-repo) restart.
    s1 = int(_node_run(app, job_id, "scout")["session_id"])
    for _ in range(5):
        _advance_chain(app, s1, salvage="same thing again")
        sat.tick()
    assert [(i["action"]) for i in _interventions(app, job_id)] == ["steer", "restart"]

    # Second episode in the fresh session loops again: steer, then ESCALATE
    # (a second restart would just thrash).
    s2 = int(_node_run(app, job_id, "scout")["session_id"])
    assert s2 != s1
    for _ in range(8):
        _advance_chain(app, s2, salvage="new attempt, same rut")
        sat.tick()
        if _job(app, job_id)["status"] != "running":
            break
    actions = [i["action"] for i in _interventions(app, job_id)]
    assert actions == ["steer", "restart", "steer", "escalate"]
    job = _job(app, job_id)
    assert job["status"] == "review"
    node = _node_state(app, job_id, "scout")
    assert node["status"] == "failed"
    assert "unstuck" in (node["error"] or "")
    assert any(e["type"] == "satpam.escalated" for e in _events(app, s2))


# ── detection: confused ───────────────────────────────────────────────────


def test_continuation_cap_records_confused_escalation(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    job = _ops_job(c)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    job_id = job["id"]
    session_id = _job(app, job_id)["session_id"]
    run = dict(app.state.db.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
    ).fetchone())

    app.state.worker.satpam.record_cap_escalation(run, 5, 900)
    ivs = _interventions(app, job_id)
    assert [(i["action"], i["detection"]) for i in ivs] == [("escalate", "confused")]
    assert "5 automatic continuations" in ivs[0]["reason"]
    assert any(e["type"] == "satpam.escalated" for e in _events(app, session_id))


def test_repeated_contract_failure_escalates_on_second_strike(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    graph = normalize_graph({"nodes": [
        {"id": "data", "name": "Data", "instruction": "Emit JSON", "output_kind": "json"},
    ]})
    job_id = _create_graph_job(app, graph)
    app.state.worker.graph_executor.dispatch_ready(job_id)

    # First contract failure: node fails + plan pauses (existing behavior), and
    # the strike is counted - but no escalation record yet.
    assert _finish_node_run(app, job_id, "data", "this is not json")
    node = _node_state(app, job_id, "data")
    assert node["status"] == "failed" and node["contract_failures"] == 1
    assert _job(app, job_id)["status"] == "review"
    assert _interventions(app, job_id) == []

    # The owner reruns it; the agent produces invalid output AGAIN: confused.
    rerun = c.post(f"/api/graph/jobs/{job_id}/nodes/data/rerun")
    assert rerun.status_code == 200, rerun.text
    assert _finish_node_run(app, job_id, "data", "still not json")
    node = _node_state(app, job_id, "data")
    assert node["contract_failures"] == 2
    ivs = _interventions(app, job_id)
    assert [(i["action"], i["detection"]) for i in ivs] == [("escalate", "confused")]
    assert "failed its declared contract 2" in ivs[0]["reason"]


# ── decision-hold (T10 #4) ────────────────────────────────────────────────


def test_decision_hold_parks_node_holds_dependents_continues_independents(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    graph = normalize_graph({"nodes": [
        {"id": "ask", "name": "Ask", "instruction": "Pick a direction"},
        {"id": "other", "name": "Other", "instruction": "Independent work"},
        {"id": "after", "name": "After", "instruction": "Build on ask", "depends_on": ["ask"]},
    ]})
    job_id = _create_graph_job(app, graph)
    app.state.worker.graph_executor.dispatch_ready(job_id)
    assert _node_state(app, job_id, "ask")["status"] == "running"
    assert _node_state(app, job_id, "other")["status"] == "running"

    # 'ask' surfaces a genuine open decision: it parks in review with the
    # question, the JOB STAYS RUNNING, and the dependent holds.
    assert _finish_node_run(app, job_id, "ask", "DECISION_NEEDED: Ship as a CLI or a web app?")
    node = _node_state(app, job_id, "ask")
    assert node["status"] == "review"
    assert node["question"] == "Ship as a CLI or a web app?"
    assert node["output"] is None
    assert _job(app, job_id)["status"] == "running"
    assert _node_state(app, job_id, "after")["status"] == "pending"
    # The independent branch is still live and can complete normally.
    assert _node_state(app, job_id, "other")["status"] == "running"
    assert _finish_node_run(app, job_id, "other", "independent result")
    assert _node_state(app, job_id, "other")["status"] == "done"

    # With the independents drained, the plan parks for the owner.
    assert _job(app, job_id)["status"] == "review"

    # The owner answers: the node re-runs with the decision in its prompt;
    # the plan resumes.
    answered = c.post(
        f"/api/graph/jobs/{job_id}/nodes/ask/answer", json={"answer": "A CLI first."}
    )
    assert answered.status_code == 200, answered.text
    assert _job(app, job_id)["status"] == "running"
    node = _node_state(app, job_id, "ask")
    assert node["status"] == "running" and node["answer"] == "A CLI first."
    prompt = _node_run(app, job_id, "ask")["prompt"]
    assert "OWNER DECISION" in prompt
    assert "Ship as a CLI or a web app?" in prompt
    assert "A CLI first." in prompt

    # The re-run completes with real output -> the dependent finally dispatches.
    assert _finish_node_run(app, job_id, "ask", "CLI scaffold plan")
    assert _node_state(app, job_id, "ask")["status"] == "done"
    assert _node_state(app, job_id, "after")["status"] == "running"


def test_answer_requires_an_open_decision(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    graph = normalize_graph({"nodes": [{"id": "n", "name": "N", "instruction": "Work"}]})
    job_id = _create_graph_job(app, graph)
    app.state.worker.graph_executor.dispatch_ready(job_id)
    res = c.post(f"/api/graph/jobs/{job_id}/nodes/n/answer", json={"answer": "irrelevant"})
    assert res.status_code == 409


# ── steer plumbing ────────────────────────────────────────────────────────


def test_pending_steer_is_consumed_by_next_continuation(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    job = _ops_job(c)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    session_id = _job(app, job["id"])["session_id"]
    db = app.state.db
    db.execute(
        "INSERT INTO satpam_watch(session_id, job_id, last_turn, steer_pending) VALUES (?, ?, 0, ?)",
        (session_id, job["id"], "Course-correct now."),
    )
    run = dict(db.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
    ).fetchone())
    db.execute("UPDATE runs SET status='failed' WHERE id = ?", (run["id"],))

    worker = app.state.worker
    with app.state.db_lock:
        outcome = worker._continue_after_timeout(run)
    assert outcome.get("run_id")
    prompt = _run(app, outcome["run_id"])["prompt"]
    assert "SUPERVISOR NOTE" in prompt and "Course-correct now." in prompt
    watch = db.execute(
        "SELECT steer_pending FROM satpam_watch WHERE session_id = ?", (session_id,)
    ).fetchone()
    assert watch["steer_pending"] is None


# ── settings ──────────────────────────────────────────────────────────────


def test_satpam_settings_routes_and_bounds(tmp_path: Path):
    app = _app(tmp_path)
    c = _client(app)
    body = c.get("/api/settings/satpam").json()
    assert body["stall_turns"] == 2 and body["check_seconds"] == 60
    assert body["min_stall_turns"] == 1 and body["max_check_seconds"] == 3600

    ok = c.put("/api/settings/satpam", json={"stall_turns": 3, "check_seconds": 120})
    assert ok.status_code == 200
    assert c.get("/api/settings/satpam").json()["stall_turns"] == 3

    assert c.put("/api/settings/satpam", json={"stall_turns": 0, "check_seconds": 120}).status_code == 400
    assert c.put("/api/settings/satpam", json={"stall_turns": 2, "check_seconds": 5}).status_code == 400
    assert c.put("/api/settings/satpam", json={"stall_turns": "x", "check_seconds": 60}).status_code == 400


def test_threshold_setting_is_honored(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)
    c = _client(app)
    assert c.put("/api/settings/satpam", json={"stall_turns": 3, "check_seconds": 60}).status_code == 200
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    session_id = _job(app, job["id"])["session_id"]
    sat = app.state.worker.satpam

    # Two stalled turns: below the raised N=3 - no action.
    for _ in range(2):
        _advance_chain(app, session_id, salvage="stuck")
        sat.tick()
    assert _interventions(app, job["id"]) == []
    # The third one crosses it.
    _advance_chain(app, session_id, salvage="stuck")
    sat.tick()
    assert [(i["action"], i["detection"]) for i in _interventions(app, job["id"])] == [("steer", "stalled")]


# ── fail-quiet ────────────────────────────────────────────────────────────


def test_satpam_is_fail_quiet_and_keeps_supervising_other_chains(tmp_path: Path, monkeypatch):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)
    c = _client(app)
    broken = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{broken['id']}/start").status_code == 200
    broken_session = _job(app, broken["id"])["session_id"]
    healthy = _ops_job(c)
    assert c.post(f"/api/jobs/{healthy['id']}/start").status_code == 200
    healthy_session = _job(app, healthy["id"])["session_id"]

    _advance_chain(app, broken_session, salvage="loop loop")
    for _ in range(3):
        _advance_chain(app, healthy_session, salvage="loop loop")

    def _boom(_path):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(satpam_mod.worktrees, "work_signature", _boom)
    sat = app.state.worker.satpam
    for _ in range(2):
        sat.tick()  # must not raise
        _advance_chain(app, broken_session, salvage="loop loop")
        _advance_chain(app, healthy_session, salvage="loop loop")
    sat.tick()

    # The broken chain produced no intervention (its evaluation kept failing
    # quietly) but the healthy ops chain was still supervised and steered.
    assert _interventions(app, broken["id"]) == []
    assert [(i["action"], i["detection"]) for i in _interventions(app, healthy["id"])] == [("steer", "looping")]


def test_maybe_tick_survives_a_broken_tick(tmp_path: Path, monkeypatch):
    app = _app(tmp_path)
    _client(app)
    sat = app.state.worker.satpam
    monkeypatch.setattr(sat, "tick", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sat.maybe_tick(10_000.0)  # must not raise

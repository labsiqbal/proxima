from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import app_settings, satpam, worktrees
from proxima_api.main import create_app
from proxima_api.master_runtime import execute_tool
from proxima_api.master_tool_broker import MasterToolBroker
from proxima_api.routes.chat import _stream_session_events


def _app_and_client(
    tmp_path: Path,
    *,
    database_path: Path | None = None,
    feature_enabled: bool = True,
    max_parallel: int = 3,
):
    app = create_app(
        {
            "database_path": str(database_path or tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_master_orchestrator": feature_enabled,
            "master_max_parallel": max_parallel,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    project = client.post(
        "/api/projects",
        json={"slug": "projection", "name": "Projection"},
    ).json()
    return app, client, project


def _delegate(
    app,
    client: TestClient,
    project: dict,
    *,
    key: str,
    tasks: list[dict],
) -> tuple[dict, list[dict]]:
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users WHERE username = 'owner'"
    ).fetchone()["id"]
    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "idempotency_key": key,
            "tasks": [
                {"project_slug": project["slug"], **task}
                for task in tasks
            ],
        },
    )
    assert result["ok"] is True
    return desk, result["result"]["jobs"]


def _projection_events(
    client: TestClient, master_session_id: int
) -> list[dict]:
    return [
        event
        for event in client.get(
            f"/api/sessions/{master_session_id}/events"
        ).json()["events"]
        if event["type"].startswith("master.")
    ]


async def _next_sse_event(app, session_id: int, after_id: int) -> str:
    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    stream = _stream_session_events(
        app,
        ConnectedRequest(),
        session_id,
        after_id,
        lambda: app.state.db,
    )
    try:
        return await anext(stream)
    finally:
        await stream.aclose()


def _advance_chain(app, session_id: int, *, salvage: str) -> None:
    previous = app.state.db.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    assert previous is not None
    app.state.db.execute(
        "UPDATE runs SET status = 'failed', error = 'runner timed out', "
        "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (previous["id"],),
    )
    app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, run_id) "
        "VALUES (?, 'assistant', ?, ?)",
        (session_id, salvage, previous["id"]),
    )
    app.state.db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, "
        "status, prompt, model, hermes_home, kind, continued_from_run_id, "
        "continuation_count) VALUES (?, ?, ?, ?, ?, 'queued', "
        "'continue where you stopped', ?, ?, ?, ?, ?)",
        (
            previous["session_id"],
            previous["project_id"],
            previous["user_id"],
            previous["profile_id"],
            previous["runner_id"],
            previous["model"],
            previous["hermes_home"],
            previous["kind"] or "chat",
            previous["id"],
            (previous["continuation_count"] or 0) + 1,
        ),
    )


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Projection Test",
            "-c",
            "user.email=projection@example.com",
            *args,
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_master_dependency_supervision_projects_satpam_to_thread_and_stream(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users WHERE username = 'owner'"
    ).fetchone()["id"]
    delegated = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "idempotency_key": "dependent-repro",
            "tasks": [
                {
                    "key": "research",
                    "title": "Research",
                    "brief": "Collect evidence",
                    "project_slug": project["slug"],
                },
                {
                    "key": "report",
                    "title": "Report",
                    "brief": "Write the report",
                    "project_slug": project["slug"],
                    "depends_on": ["research"],
                },
            ],
        },
    )
    assert delegated["ok"] is True
    research, report = delegated["result"]["jobs"]

    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    tick = app.state.master_supervisor.tick()

    assert tick["started"] == [research["id"]]
    blocked = app.state.db.execute(
        "SELECT status, blocked_reason FROM jobs WHERE id = ?",
        (report["id"],),
    ).fetchone()
    assert blocked["status"] == "queued"
    assert "currently running" in blocked["blocked_reason"]

    worker_session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?",
        (research["id"],),
    ).fetchone()["session_id"]
    for _ in range(3):
        _advance_chain(
            app,
            worker_session_id,
            salvage="I will now analyze the data.",
        )
        app.state.worker.satpam.tick()

    interventions = app.state.db.execute(
        "SELECT id, action FROM satpam_interventions WHERE job_id = ?",
        (research["id"],),
    ).fetchall()
    assert [(row["action"]) for row in interventions] == ["steer"]

    messages = client.get(
        f"/api/sessions/{desk['session']['id']}/messages"
    ).json()["messages"]
    assert any(
        "Satpam" in message["content"] and "steer" in message["content"].lower()
        for message in messages
    )
    events = client.get(
        f"/api/sessions/{desk['session']['id']}/events"
    ).json()["events"]
    assert [
        event["type"]
        for event in events
        if event["type"] == "master.satpam.steered"
    ] == ["master.satpam.steered"]


def test_task_lifecycle_review_checkpoint_and_sse_replay_are_exactly_once(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="lifecycle",
        tasks=[
            {
                "key": "guarded",
                "title": "Guarded report",
                "brief": "Write a report",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET input = json_set(input, '$.execution_policy', 'guarded'), "
        "steps_state = json_set(steps_state, '$[0].review_required', json('true')) "
        "WHERE id = ?",
        (job_id,),
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    assert app.state.master_supervisor.tick()["started"] == [job_id]
    run = dict(
        app.state.db.execute(
            "SELECT r.* FROM runs r JOIN sessions s ON s.id = r.session_id "
            "WHERE s.job_id = ? ORDER BY r.id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    )

    app.state.worker._advance_job(run, "Report ready")
    review = app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert review["status"] == "review"
    review_projection = app.state.db.execute(
        "SELECT payload_json FROM master_projections "
        "WHERE task_id = ? AND projection_type = 'master.task.review_ready'",
        (job_id,),
    ).fetchone()
    review_payload = json.loads(review_projection["payload_json"])
    assert review_payload["checkpoint_id"] is not None
    assert review_payload["attention_required"] is True

    approved = client.post(f"/api/jobs/{job_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"
    for _ in range(3):
        app.state.master_projection.reconcile()
        app.state.master_projection.project_task(job_id)

    types = [
        row["projection_type"]
        for row in app.state.db.execute(
            "SELECT projection_type FROM master_projections "
            "WHERE task_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    ]
    assert types == [
        "master.task.started",
        "master.task.review_ready",
        "master.task.completed",
    ]
    master_session_id = desk["session"]["id"]
    events = _projection_events(client, master_session_id)
    assert [event["type"] for event in events] == types
    assert [event["id"] for event in events] == sorted(
        event["id"] for event in events
    )
    message_ids = [
        row["message_id"]
        for row in app.state.db.execute(
            "SELECT message_id FROM master_projections "
            "WHERE task_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    ]
    assert message_ids == sorted(message_ids)

    sse = asyncio.run(
        _next_sse_event(
            app,
            master_session_id,
            events[0]["id"],
        )
    )
    assert f"id: {events[1]['id']}\n" in sse
    assert "event: master.task.review_ready\n" in sse
    assert (
        client.get(
            f"/api/sessions/{master_session_id}/events"
            f"?after_id={events[-1]['id']}"
        ).json()["events"]
        == []
    )


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_terminal_prerequisite_projects_stable_downstream_blocker(
    tmp_path: Path,
    terminal_status: str,
):
    app, client, project = _app_and_client(tmp_path, max_parallel=1)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key=f"terminal-{terminal_status}",
        tasks=[
            {
                "key": "upstream",
                "title": "Upstream",
                "brief": "Do upstream",
            },
            {
                "key": "downstream",
                "title": "Downstream",
                "brief": "Do downstream",
                "depends_on": ["upstream"],
            },
            {
                "key": "independent",
                "title": "Independent",
                "brief": "Do independent",
            },
        ],
    )
    upstream, downstream, independent = jobs
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    assert app.state.master_supervisor.tick()["started"] == [upstream["id"]]
    app.state.db.execute(
        "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP "
        "WHERE session_id = (SELECT session_id FROM jobs WHERE id = ?) "
        "AND status IN ('queued', 'running')",
        (upstream["id"],),
    )
    app.state.db.execute(
        "UPDATE jobs SET status = ?, finished_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (terminal_status, upstream["id"]),
    )
    app.state.task_delegation.prerequisite_changed(
        upstream["id"], connection=app.state.db
    )

    tick = app.state.master_supervisor.tick()
    assert tick["started"] == [independent["id"]]
    blocked = app.state.db.execute(
        "SELECT status, blocked_reason FROM jobs WHERE id = ?",
        (downstream["id"],),
    ).fetchone()
    assert blocked["status"] == "queued"
    assert f"which {terminal_status}" in blocked["blocked_reason"]
    projections = _projection_events(client, desk["session"]["id"])
    terminal_event = (
        "master.task.failed"
        if terminal_status == "failed"
        else "master.task.cancelled"
    )
    assert len(
        [
            event
            for event in projections
            if event["type"] == terminal_event
            and event["payload"]["task_id"] == upstream["id"]
        ]
    ) == 1
    assert len(
        [
            event
            for event in projections
            if event["type"] == "master.task.blocked"
            and event["payload"]["task_id"] == downstream["id"]
        ]
    ) == 1


def test_duplicate_and_concurrent_supervisor_ticks_claim_each_task_once(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path, max_parallel=2)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="concurrent-ticks",
        tasks=[
            {"key": "one", "title": "One", "brief": "Do one"},
            {"key": "two", "title": "Two", "brief": "Do two"},
        ],
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    barrier = threading.Barrier(3)
    results: list[dict] = []

    def tick() -> None:
        barrier.wait()
        results.append(app.state.master_supervisor.tick())

    threads = [threading.Thread(target=tick) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    app.state.master_supervisor.tick()

    assert app.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE session_id IN "
        "(SELECT session_id FROM jobs WHERE id IN (?, ?))",
        (jobs[0]["id"], jobs[1]["id"]),
    ).fetchone()[0] == 2
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE projection_type = 'master.task.started'"
    ).fetchone()[0] == 2
    assert any(result.get("busy") for result in results) or sorted(
        job_id
        for result in results
        for job_id in result.get("started", [])
    ) == sorted(job["id"] for job in jobs)


def test_restart_reconciliation_preserves_one_message_and_event(
    tmp_path: Path,
):
    database_path = tmp_path / "restart.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
    )
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="restart-projection",
        tasks=[
            {"key": "task", "title": "Restart safe", "brief": "Do work"}
        ],
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    app.state.master_supervisor.tick()
    before = app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections"
    ).fetchone()[0]

    restarted = create_app(
        {
            "database_path": str(database_path),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_master_orchestrator": True,
        }
    )
    for _ in range(3):
        restarted.state.master_projection.reconcile()

    assert restarted.state.db.execute(
        "SELECT COUNT(*) FROM master_projections"
    ).fetchone()[0] == before
    assert restarted.state.db.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND author = 'Master'",
        (desk["session"]["id"],),
    ).fetchone()[0] == before
    assert restarted.state.db.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? "
        "AND type LIKE 'master.%'",
        (desk["session"]["id"],),
    ).fetchone()[0] == before
    assert restarted.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (jobs[0]["id"],)
    ).fetchone()["status"] == "running"


def test_feature_off_does_not_instantiate_or_project_master_services(
    tmp_path: Path,
):
    app, client, _project = _app_and_client(
        tmp_path,
        feature_enabled=False,
    )

    assert app.state.master_supervisor is None
    assert app.state.master_projection is None
    assert client.get("/api/master/desk").status_code == 503
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections"
    ).fetchone()[0] == 0


def test_projection_refuses_mismatched_master_owner(tmp_path: Path):
    app, client, _project = _app_and_client(tmp_path)
    desk = client.get("/api/master/desk").json()
    other_id = app.state.db.execute(
        "INSERT INTO users(username, os_user) VALUES ('other', 'other')"
    ).lastrowid
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE user_id = 1 AND is_default = 1"
    ).fetchone()["id"]
    session_id = app.state.db.execute(
        "INSERT INTO sessions(title, owner_user_id, profile_id, runner_id) "
        "VALUES ('forged worker', ?, ?, 'codex')",
        (other_id, profile_id),
    ).lastrowid
    job_id = app.state.db.execute(
        "INSERT INTO jobs(session_id, title, status, input, steps_state, "
        "created_by, origin_master_session_id) "
        "VALUES (?, 'Forged', 'running', '{}', '[]', ?, ?)",
        (session_id, other_id, desk["session"]["id"]),
    ).lastrowid

    assert app.state.master_projection.project_task(job_id) is None
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 0


def test_retried_attention_tool_projects_one_action_required_message(
    tmp_path: Path,
):
    app, client, _project = _app_and_client(tmp_path)
    desk = client.get("/api/master/desk").json()
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
    )
    args = {
        "title": "Choose a direction",
        "message": "Pick option A or B.",
        "idempotency_key": "attention-choice",
    }

    first = broker.execute("create_attention", args)
    second = broker.execute("create_attention", args)

    assert first["result"]["attention_id"] == second["result"]["attention_id"]
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE projection_type = 'master.attention.required'"
    ).fetchone()[0] == 1
    events = _projection_events(client, desk["session"]["id"])
    attention_events = [
        event
        for event in events
        if event["type"] == "master.attention.required"
    ]
    assert len(attention_events) == 1
    assert attention_events[0]["payload"]["attention_required"] is True


def test_master_supervisor_never_invokes_satpam_recovery_methods(
    tmp_path: Path,
    monkeypatch,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, _jobs = _delegate(
        app,
        client,
        project,
        key="satpam-only",
        tasks=[
            {"key": "task", "title": "Running", "brief": "Do work"}
        ],
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    app.state.master_supervisor.tick()
    called: list[str] = []
    monkeypatch.setattr(
        app.state.worker.satpam,
        "_restart_clean",
        lambda *_args, **_kwargs: called.append("restart"),
    )
    monkeypatch.setattr(
        app.state.worker.satpam,
        "_escalate_stuck",
        lambda *_args, **_kwargs: called.append("escalate"),
    )

    for _ in range(3):
        app.state.master_supervisor.tick()

    assert called == []


def test_satpam_restart_and_escalation_each_project_once(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="satpam-ladder",
        tasks=[
            {"key": "task", "title": "Looping Task", "brief": "Do work"}
        ],
    )
    job_id = jobs[0]["id"]
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    app.state.master_supervisor.tick()
    worker_session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["session_id"]

    for _ in range(20):
        _advance_chain(
            app,
            worker_session_id,
            salvage="I will repeat the same analysis.",
        )
        app.state.worker.satpam.tick()
        actions = [
            row["action"]
            for row in app.state.db.execute(
                "SELECT action FROM satpam_interventions "
                "WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        ]
        if actions and actions[-1] == "escalate":
            break

    interventions = app.state.db.execute(
        "SELECT id, action FROM satpam_interventions "
        "WHERE job_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()
    assert [row["action"] for row in interventions] == [
        "steer",
        "restart",
        "steer",
        "escalate",
    ]
    for row in interventions:
        app.state.master_projection.project_satpam(row["id"])
        app.state.master_projection.project_satpam(row["id"])

    projections = app.state.db.execute(
        "SELECT source_id, projection_type FROM master_projections "
        "WHERE source_table = 'satpam_interventions' ORDER BY id"
    ).fetchall()
    assert [(row["source_id"], row["projection_type"]) for row in projections] == [
        (interventions[0]["id"], "master.satpam.steered"),
        (interventions[1]["id"], "master.satpam.restarted"),
        (interventions[2]["id"], "master.satpam.steered"),
        (interventions[3]["id"], "master.satpam.escalated"),
    ]
    events = _projection_events(client, desk["session"]["id"])
    assert len(
        [event for event in events if event["type"].startswith("master.satpam.")]
    ) == 4


def test_retried_repo_recovery_failure_projects_one_attention_and_event(
    tmp_path: Path,
    monkeypatch,
):
    app, client, _project = _app_and_client(tmp_path)
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("start\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    linked = client.post(
        "/api/projects/link",
        json={"path": str(repo), "slug": "repo-projection"},
    )
    assert linked.status_code == 201, linked.text
    project = linked.json()
    area_id = project["code_areas"][0]["id"]
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="repo-recovery",
        tasks=[
            {
                "key": "repo",
                "title": "Repo Task",
                "brief": "Change code",
                "target_area_id": area_id,
            }
        ],
    )
    job_id = jobs[0]["id"]
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    app.state.master_supervisor.tick()
    intervention_id = satpam.record_intervention(
        app.state.worker_db,
        job_id,
        None,
        satpam.ACTION_RESTART,
        satpam.DETECTION_STALLED,
        satpam.STATUS_PENDING,
        "Restart needs approval",
    )
    app.state.master_projection.project_satpam(intervention_id)
    monkeypatch.setattr(
        worktrees,
        "recut_job_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            worktrees.WorktreeError("repository is dirty")
        ),
    )

    for _ in range(2):
        with pytest.raises(satpam.SatpamRestartError, match="dirty"):
            app.state.worker.satpam.execute_restart(
                job_id, intervention_id
            )

    assert app.state.db.execute(
        "SELECT COUNT(*) FROM attention_items "
        "WHERE source_key = ?",
        (f"satpam-recovery-failed:{intervention_id}",),
    ).fetchone()[0] == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE projection_type = 'master.satpam.recovery_failed' "
        "AND task_id = ?",
        (job_id,),
    ).fetchone()[0] == 1
    events = _projection_events(client, desk["session"]["id"])
    assert len(
        [
            event
            for event in events
            if event["type"] == "master.satpam.recovery_failed"
        ]
    ) == 1

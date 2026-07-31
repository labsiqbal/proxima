from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import (
    app_settings,
    master_decisions,
    master_focus,
    satpam,
    worktrees,
)
from proxima_api.db import connect
from proxima_api.graph import normalize_graph
from proxima_api.job_checkpoints import create_checkpoint
from proxima_api.main import create_app
from proxima_api.master_runtime import execute_tool
from proxima_api.master_tool_broker import MasterToolBroker
from proxima_api.migrations import MIGRATIONS
from proxima_api.routes.chat import _sse_resume_cursor, _stream_session_events
from proxima_api.task_delegation import TaskDelegationRequest
from proxima_api.task_state_events import append_task_update
from project_test_utils import with_browse_root


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


def _make_focus_origin_uncaptured(app, job_id: int) -> None:
    app.state.db.execute(
        "DROP TRIGGER task_delegations_focus_immutable"
    )
    app.state.db.execute(
        "UPDATE task_delegations SET origin_focus_epoch_id = NULL, "
        "origin_focus_captured = 0 WHERE job_id = ?",
        (job_id,),
    )
    next(migration[2] for migration in MIGRATIONS if migration[0] == 40)(
        app.state.db
    )


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


def test_idle_sse_flushes_a_connection_comment_without_waiting_for_keepalive(
    tmp_path: Path,
):
    app, client, _project = _app_and_client(tmp_path)
    desk = client.get("/api/master/desk").json()

    comment = asyncio.run(
        _next_sse_event(
            app,
            desk["session"]["id"],
            desk["event_cursor"],
        )
    )

    assert comment == ": connected\n\n"


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
    task_session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["session_id"]
    task_events = client.get(
        f"/api/sessions/{task_session_id}/events"
    ).json()["events"]
    assert [
        (event["type"], event["payload"])
        for event in task_events
        if event["type"] == "job.update"
    ][-1] == (
        "job.update",
        {
            "job_id": job_id,
            "status": "done",
            "mutation": "review_approved",
        },
    )
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


def test_checkpoint_restore_starts_a_new_projection_lifecycle(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="projection-lifecycle-epoch",
        tasks=[{
            "key": "task",
            "title": "Repeat lifecycle",
            "brief": "Project every restored run",
        }],
    )
    job_id = jobs[0]["id"]
    checkpoint = create_checkpoint(app.state.db, job_id)
    revisions = []
    for status in ("running", "review", "done"):
        app.state.db.execute(
            "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (status, job_id),
        )
        app.state.master_projection.project_task(job_id)
        revisions.append(
            app.state.db.execute(
                "SELECT projection_revision FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()["projection_revision"]
        )

    restored = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )

    assert restored.status_code == 200
    assert restored.json()["restored_status"] == "queued"
    for status in ("running", "review", "done"):
        app.state.db.execute(
            "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (status, job_id),
        )
        app.state.master_projection.project_task(job_id)
        revisions.append(
            app.state.db.execute(
                "SELECT projection_revision FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()["projection_revision"]
        )

    rows = app.state.db.execute(
        "SELECT projection_key, projection_type "
        "FROM master_projections WHERE task_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()
    assert [row["projection_type"] for row in rows] == [
        "master.task.started",
        "master.task.review_ready",
        "master.task.completed",
        "master.task.started",
        "master.task.review_ready",
        "master.task.completed",
    ]
    assert [row["projection_key"] for row in rows] == [
        f"task:{job_id}:revision:{revisions[0]}:started",
        f"task:{job_id}:revision:{revisions[1]}:review",
        f"task:{job_id}:revision:{revisions[2]}:completed",
        f"task:{job_id}:revision:{revisions[3]}:started",
        f"task:{job_id}:revision:{revisions[4]}:review",
        f"task:{job_id}:revision:{revisions[5]}:completed",
    ]
    for _ in range(3):
        app.state.master_projection.project_task(job_id)
        assert app.state.master_projection.reconcile()["created"] == 0
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 6


def test_running_review_running_uses_distinct_transition_revisions(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="running-review-running",
        tasks=[{
            "key": "task",
            "title": "Resume after gate",
            "brief": "Project each gate transition",
        }],
    )
    job_id = jobs[0]["id"]
    transitions = []
    for status, mutation in (
        ("running", "worker_started"),
        ("review", "gate_review"),
        ("running", "gate_approved"),
    ):
        app.state.db.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        task_event = append_task_update(
            app.state.db,
            job_id=job_id,
            mutation=mutation,
        )
        transitions.append(
            (
                task_event["projection_outbox_id"],
                app.state.db.execute(
                    "SELECT projection_revision FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()["projection_revision"],
            )
        )
        app.state.master_projection.process_task_outbox(
            task_event["projection_outbox_id"]
        )

    rows = app.state.db.execute(
        "SELECT projection_key, projection_type "
        "FROM master_projections WHERE task_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()
    assert [row["projection_type"] for row in rows] == [
        "master.task.started",
        "master.task.review_ready",
        "master.task.started",
    ]
    assert [row["projection_key"] for row in rows] == [
        f"task:{job_id}:revision:{transitions[0][1]}:started",
        f"task:{job_id}:revision:{transitions[1][1]}:review",
        f"task:{job_id}:revision:{transitions[2][1]}:started",
    ]
    for outbox_id, _revision in transitions:
        app.state.master_projection.process_task_outbox(outbox_id)
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 3


def test_same_status_linear_progress_reuses_one_projection_generation(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="same-status-linear-progress",
        tasks=[{
            "key": "task",
            "title": "Linear progress",
            "brief": "Advance without duplicate lifecycle events",
        }],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'running' WHERE id = ?",
        (job_id,),
    )
    app.state.master_projection.project_task(job_id)
    initial = dict(
        app.state.db.execute(
            "SELECT projection_revision, projection_state FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    )

    for index, step_status in enumerate(("running", "done", "running")):
        app.state.db.execute(
            "UPDATE jobs SET status = 'running', "
            "steps_state = json_set(steps_state, '$[0].status', ?) "
            "WHERE id = ?",
            (step_status, job_id),
        )
        task_event = append_task_update(
            app.state.db,
            job_id=job_id,
            mutation=f"same_status_progress_{index}",
        )
        assert "projection_outbox_id" not in task_event
        app.state.master_projection.project_task(job_id)

    assert dict(
        app.state.db.execute(
            "SELECT projection_revision, projection_state FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    ) == initial
    assert initial["projection_state"] == "started"
    assert [
        row["projection_type"]
        for row in app.state.db.execute(
            "SELECT projection_type FROM master_projections "
            "WHERE task_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    ] == ["master.task.started"]


def test_same_status_graph_progress_reuses_one_projection_generation(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="same-status-graph-progress",
        tasks=[{
            "key": "task",
            "title": "Graph progress",
            "brief": "Advance nodes without duplicate lifecycle events",
        }],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'running', engine = 'graph', "
        "graph = '{\"nodes\": [], \"edges\": []}', steps_state = '[]' "
        "WHERE id = ?",
        (job_id,),
    )
    app.state.master_projection.project_task(job_id)
    initial_revision = app.state.db.execute(
        "SELECT projection_revision FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["projection_revision"]

    app.state.db.execute(
        "INSERT INTO node_states(job_id, node_id, status) "
        "VALUES (?, 'work', 'queued')",
        (job_id,),
    )
    app.state.master_projection.project_task(job_id)
    app.state.db.execute(
        "UPDATE node_states SET status = 'running' "
        "WHERE job_id = ? AND node_id = 'work'",
        (job_id,),
    )
    app.state.master_projection.project_task(job_id)
    app.state.db.execute(
        "UPDATE node_states SET status = 'done' "
        "WHERE job_id = ? AND node_id = 'work'",
        (job_id,),
    )
    app.state.master_projection.project_task(job_id)

    assert app.state.db.execute(
        "SELECT projection_revision FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["projection_revision"] == initial_revision
    assert [
        row["projection_type"]
        for row in app.state.db.execute(
            "SELECT projection_type FROM master_projections "
            "WHERE task_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    ] == ["master.task.started"]


def test_task_outbox_retries_in_event_order_without_duplicate_delivery(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="ordered-outbox",
        tasks=[{
            "key": "task",
            "title": "Ordered transitions",
            "brief": "Replay in causal order",
        }],
    )
    job_id = jobs[0]["id"]
    outbox_ids = []
    for status, mutation in (
        ("running", "worker_started"),
        ("review", "worker_review"),
    ):
        app.state.db.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        event = append_task_update(
            app.state.db,
            job_id=job_id,
            mutation=mutation,
        )
        outbox_ids.append(event["projection_outbox_id"])
    app.state.db.execute(
        "CREATE TRIGGER reject_ordered_start "
        "BEFORE INSERT ON events "
        "WHEN NEW.type = 'master.task.started' "
        "BEGIN SELECT RAISE(ABORT, 'started delivery interrupted'); END"
    )

    assert (
        app.state.master_projection.process_task_outbox(outbox_ids[1])
        is None
    )
    assert (
        app.state.master_projection.safe_process_task_outbox(outbox_ids[0])
        is None
    )
    assert (
        app.state.master_projection.process_task_outbox(outbox_ids[1])
        is None
    )
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 0
    assert [
        tuple(row)
        for row in app.state.db.execute(
            "SELECT state, attempt_count FROM task_projection_outbox "
            "WHERE id IN (?, ?) ORDER BY task_event_id",
            tuple(outbox_ids),
        ).fetchall()
    ] == [("pending", 1), ("pending", 0)]

    app.state.db.execute("DROP TRIGGER reject_ordered_start")
    app.state.master_projection.reconcile()

    assert [
        row["projection_type"]
        for row in app.state.db.execute(
            "SELECT projection_type FROM master_projections "
            "WHERE task_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    ] == ["master.task.started", "master.task.review_ready"]
    for outbox_id in reversed(outbox_ids):
        app.state.master_projection.process_task_outbox(outbox_id)
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 2


def test_checkpoint_recovery_supersedes_only_unpublished_task_transitions(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="recovery-supersession",
        tasks=[{
            "key": "task",
            "title": "Supersede stale transitions",
            "brief": "Restore queued authoritatively",
        }],
    )
    job_id = jobs[0]["id"]
    checkpoint = create_checkpoint(app.state.db, job_id)
    stale_outbox_ids = []
    for status, mutation in (
        ("failed", "worker_failed"),
        ("done", "owner_completed"),
    ):
        app.state.db.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        task_event = append_task_update(
            app.state.db,
            job_id=job_id,
            mutation=mutation,
        )
        stale_outbox_ids.append(task_event["projection_outbox_id"])

    restored = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )

    assert restored.status_code == 200
    assert restored.json()["restored_status"] == "queued"
    assert restored.json()["projection_repair"]["state"] == "projected"
    recovery = app.state.db.execute(
        "SELECT * FROM task_recovery_outbox WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert [
        tuple(row)
        for row in app.state.db.execute(
            "SELECT state, superseded_by_event_id "
            "FROM task_projection_outbox WHERE id IN (?, ?) "
            "ORDER BY task_event_id",
            tuple(stale_outbox_ids),
        ).fetchall()
    ] == [
        ("superseded", recovery["task_event_id"]),
        ("superseded", recovery["task_event_id"]),
    ]
    assert [
        event["type"]
        for event in _projection_events(client, desk["session"]["id"])
    ] == ["master.task.recovered"]
    for outbox_id in stale_outbox_ids:
        assert (
            app.state.master_projection.process_task_outbox(outbox_id)
            is None
        )
    app.state.master_projection.process_recovery_outbox(recovery["id"])
    assert [
        event["type"]
        for event in _projection_events(client, desk["session"]["id"])
    ] == ["master.task.recovered"]


def test_multiple_pending_checkpoint_recoveries_publish_once_in_event_order(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="ordered-recovery-audits",
        tasks=[{
            "key": "task",
            "title": "Preserve recovery audit",
            "brief": "Publish every restore once",
        }],
    )
    job_id = jobs[0]["id"]
    checkpoint = create_checkpoint(app.state.db, job_id)
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (job_id,),
    )
    app.state.db.execute(
        "CREATE TRIGGER reject_ordered_recovery "
        "BEFORE INSERT ON events "
        "WHEN NEW.type = 'master.task.recovered' "
        "BEGIN SELECT RAISE(ABORT, 'recovery delivery interrupted'); END"
    )

    first = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )
    second = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    pending = app.state.db.execute(
        "SELECT id, task_event_id, state, attempt_count "
        "FROM task_recovery_outbox WHERE job_id = ? ORDER BY task_event_id",
        (job_id,),
    ).fetchall()
    assert [row["state"] for row in pending] == ["pending", "pending"]
    assert [row["attempt_count"] for row in pending] == [1, 0]
    assert [
        event["type"]
        for event in _projection_events(client, desk["session"]["id"])
    ] == []

    app.state.db.execute("DROP TRIGGER reject_ordered_recovery")
    app.state.master_projection.reconcile()
    app.state.master_projection.reconcile()

    projected = app.state.db.execute(
        "SELECT task_event_id, state, attempt_count "
        "FROM task_recovery_outbox WHERE job_id = ? ORDER BY task_event_id",
        (job_id,),
    ).fetchall()
    assert [row["state"] for row in projected] == ["projected", "projected"]
    assert [row["attempt_count"] for row in projected] == [2, 1]
    recovery_events = [
        event
        for event in _projection_events(client, desk["session"]["id"])
        if event["type"] == "master.task.recovered"
    ]
    assert len(recovery_events) == 2
    assert [event["payload"]["prior_status"] for event in recovery_events] == [
        "failed",
        "queued",
    ]


def test_legacy_recovery_gap_corrects_after_current_task_projection(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="legacy-recovery-ordering-gap",
        tasks=[{
            "key": "task",
            "title": "Repair legacy recovery order",
            "brief": "Keep current Task status authoritative",
        }],
    )
    job_id = jobs[0]["id"]
    checkpoint = create_checkpoint(app.state.db, job_id)
    task_session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["session_id"]
    predecessor_seq = app.state.db.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM events "
        "WHERE session_id = ?",
        (task_session_id,),
    ).fetchone()["seq"]
    predecessor_event_id = app.state.db.execute(
        "INSERT INTO events(session_id, seq, type, payload) "
        "VALUES (?, ?, 'job.update', ?)",
        (
            task_session_id,
            predecessor_seq,
            json.dumps(
                {
                    "job_id": job_id,
                    "status": "failed",
                    "mutation": "checkpoint_restored",
                }
            ),
        ),
    ).lastrowid
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (job_id,),
    )
    restored = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )
    assert restored.status_code == 200
    successor = app.state.db.execute(
        "SELECT * FROM task_recovery_outbox WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    gap_id = app.state.db.execute(
        "INSERT INTO task_recovery_outbox("
        "job_id, task_event_id, recovery_json, state, master_session_id, "
        "ordering_successor_id"
        ") VALUES (?, ?, ?, 'legacy_ordering_gap', ?, ?)",
        (
            job_id,
            predecessor_event_id,
            json.dumps(
                {
                    "job_id": job_id,
                    "checkpoint_id": checkpoint["id"],
                    "actor": {"id": 1, "username": "owner"},
                    "prior_status": "failed",
                    "restored_status": "queued",
                    "discarded_progress": [],
                    "conflicting_progress": [],
                }
            ),
            desk["session"]["id"],
            successor["id"],
        ),
    ).lastrowid
    gap_audit_id = app.state.db.execute(
        "INSERT INTO task_recovery_ordering_gaps("
        "job_id, predecessor_outbox_id, successor_outbox_id, kind, "
        "predecessor_task_event_id, successor_task_event_id, "
        "successor_publication_event_id"
        ") VALUES (?, ?, ?, 'unpublished_predecessor', ?, ?, ?)",
        (
            job_id,
            gap_id,
            successor["id"],
            predecessor_event_id,
            successor["task_event_id"],
            successor["event_id"],
        ),
    ).lastrowid
    correction_id = app.state.db.execute(
        "INSERT INTO task_recovery_corrections("
        "job_id, successor_outbox_id, gap_count, first_task_event_id, "
        "last_task_event_id, first_successor_task_event_id, "
        "last_successor_task_event_id, master_session_id"
        ") VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
        (
            job_id,
            successor["id"],
            predecessor_event_id,
            predecessor_event_id,
            successor["task_event_id"],
            successor["task_event_id"],
            desk["session"]["id"],
        ),
    ).lastrowid
    app.state.db.execute(
        "INSERT INTO task_recovery_correction_gaps("
        "correction_id, gap_id"
        ") VALUES (?, ?)",
        (correction_id, gap_audit_id),
    )
    assert client.get(f"/api/jobs/{job_id}").json()["projection_repair"] == {
        "kind": "recovery_history",
        "state": "pending",
        "failure_code": None,
        "task_event_id": successor["task_event_id"],
    }

    app.state.db.execute(
        "UPDATE jobs SET status = 'done', finished_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (job_id,),
    )
    append_task_update(
        app.state.db,
        job_id=job_id,
        mutation="owner_completed",
    )
    app.state.db.execute(
        "CREATE TRIGGER reject_recovery_correction_event "
        "BEFORE INSERT ON events "
        "WHEN NEW.type = 'master.task.recovery_history_corrected' "
        "BEGIN SELECT RAISE(ABORT, 'injected correction failure'); END"
    )
    app.state.master_projection.reconcile()
    assert dict(
        app.state.db.execute(
            "SELECT state, failure_code, attempt_count "
            "FROM task_recovery_corrections WHERE id = ?",
            (correction_id,),
        ).fetchone()
    ) == {
        "state": "pending",
        "failure_code": "projection_failed",
        "attempt_count": 1,
    }
    app.state.db.execute("DROP TRIGGER reject_recovery_correction_event")
    app.state.master_projection.reconcile()
    app.state.master_projection.reconcile()

    assert app.state.db.execute(
        "SELECT state FROM task_recovery_outbox WHERE id = ?",
        (gap_id,),
    ).fetchone()["state"] == "legacy_ordering_gap"
    assert dict(
        app.state.db.execute(
            "SELECT state, failure_code, attempt_count "
            "FROM task_recovery_corrections WHERE id = ?",
            (correction_id,),
        ).fetchone()
    ) == {
        "state": "projected",
        "failure_code": None,
        "attempt_count": 2,
    }
    events = _projection_events(client, desk["session"]["id"])
    assert [event["type"] for event in events] == [
        "master.task.recovered",
        "master.task.completed",
        "master.task.recovery_history_corrected",
    ]
    correction_event = events[-1]
    container_id = app.state.db.execute(
        "SELECT project_id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["project_id"]
    assert correction_event["payload"] == {
        "message_id": correction_event["payload"]["message_id"],
        "task_id": job_id,
        "gap_count": 1,
        "first_task_event_id": predecessor_event_id,
        "last_task_event_id": predecessor_event_id,
        "successor_task_event_id": successor["task_event_id"],
        "first_successor_task_event_id": successor["task_event_id"],
        "last_successor_task_event_id": successor["task_event_id"],
        "focus_epoch_id": correction_event["payload"]["focus_epoch_id"],
        "focus_container_id": correction_event["payload"][
            "focus_container_id"
        ],
        "subject_container_id": container_id,
    }
    assert len(json.dumps(correction_event["payload"]).encode("utf-8")) < 2048
    assert sum(
        event["type"] == "master.task.recovery_history_corrected"
        for event in events
    ) == 1
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"
    assert client.get(
        f"/api/jobs/{job_id}"
    ).json()["projection_repair"] is None


def test_final_approval_commits_outbox_and_replays_projection_failure(
    tmp_path: Path,
    monkeypatch,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="approval-rollback",
        tasks=[
            {
                "key": "task",
                "title": "Atomic approval",
                "brief": "Complete atomically",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', "
        "steps_state = json_set(steps_state, '$[0].status', 'done') "
        "WHERE id = ?",
        (job_id,),
    )
    app.state.db.execute(
        "CREATE TRIGGER reject_atomic_completion "
        "BEFORE INSERT ON events "
        "WHEN NEW.type = 'master.task.completed' "
        "BEGIN SELECT RAISE(ABORT, 'completion projection rejected'); END"
    )
    notifications: list[int] = []
    monkeypatch.setattr(app.state.hub, "notify", notifications.append)

    approved = client.post(f"/api/jobs/{job_id}/approve")

    assert approved.status_code == 200
    assert approved.json()["status"] == "done"
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["status"] == "done"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'job.update' "
        "AND json_extract(payload, '$.job_id') = ? "
        "AND json_extract(payload, '$.mutation') = 'review_approved'",
        (job_id,),
    ).fetchone()[0] == 1
    outbox = app.state.db.execute(
        "SELECT id, state, failure_code, attempt_count "
        "FROM task_projection_outbox WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert dict(outbox) == {
        "id": outbox["id"],
        "state": "pending",
        "failure_code": "projection_failed",
        "attempt_count": 1,
    }
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE task_id = ? AND projection_type = 'master.task.completed'",
        (job_id,),
    ).fetchone()[0] == 0
    task_session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["session_id"]
    assert notifications == [task_session_id]

    app.state.db.execute("DROP TRIGGER reject_atomic_completion")
    replay = app.state.master_projection.reconcile()

    assert replay["created"] == 1
    assert dict(
        app.state.db.execute(
            "SELECT state, failure_code, attempt_count "
            "FROM task_projection_outbox WHERE id = ?",
            (outbox["id"],),
        ).fetchone()
    ) == {
        "state": "projected",
        "failure_code": None,
        "attempt_count": 2,
    }
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE task_id = ? AND projection_type = 'master.task.completed'",
        (job_id,),
    ).fetchone()[0] == 1
    assert app.state.master_projection.reconcile()["created"] == 0


def test_checkpoint_restore_atomically_reconciles_task_fleet_and_history(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="checkpoint-reconciliation",
        tasks=[
            {
                "key": "recover",
                "title": "Recover durable Task",
                "brief": "Restore this Task coherently",
            }
        ],
    )
    job_id = jobs[0]["id"]
    checkpoint = create_checkpoint(app.state.db, job_id)
    job = app.state.db.execute(
        "SELECT session_id, project_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    profile = app.state.db.execute(
        "SELECT id, runner_id FROM profiles WHERE is_default = 1"
    ).fetchone()
    app.state.db.execute(
        "INSERT INTO runs("
        "session_id, project_id, user_id, profile_id, runner_id, status, prompt, kind"
        ") VALUES (?, ?, 1, ?, ?, 'failed', 'discarded recovery progress', 'job')",
        (
            job["session_id"],
            job["project_id"],
            profile["id"],
            profile["runner_id"],
        ),
    ).lastrowid
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed', rejected_reason = 'Bad continuation', "
        "finished_at = CURRENT_TIMESTAMP, "
        "steps_state = json_set(steps_state, '$[0].status', 'failed') "
        "WHERE id = ?",
        (job_id,),
    )
    oversized_node_id = "untrusted-node-" + ("x" * 20_000)
    app.state.db.execute(
        "INSERT INTO node_states(job_id, node_id, status) "
        "VALUES (?, ?, 'failed')",
        (job_id, oversized_node_id),
    )
    app.state.master_projection.project_task(job_id)

    response = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )

    assert response.status_code == 200
    recovery = response.json()["recovery"]
    assert recovery["actor"] == {"id": 1, "username": "owner"}
    assert recovery["checkpoint_id"] == checkpoint["id"]
    assert recovery["prior_status"] == "failed"
    assert recovery["restored_status"] == "queued"
    assert any(
        item.startswith("1 run created")
        for item in recovery["discarded_progress"]
    )
    assert any(
        item.startswith("1 Recipe node progress record changed")
        for item in recovery["discarded_progress"]
    )
    assert recovery["conflicting_progress"] == []
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    fleet_job = next(
        item
        for item in client.get("/api/master/desk").json()["jobs"]
        if item["id"] == job_id
    )
    assert fleet_job["status"] == "queued"

    task_events = client.get(
        f"/api/sessions/{job['session_id']}/events"
    ).json()["events"]
    task_event = next(
        event
        for event in reversed(task_events)
        if event["type"] == "job.update"
    )
    assert task_event["payload"] == {
        "job_id": job_id,
        "status": "queued",
        "mutation": "checkpoint_restored",
        "checkpoint_id": checkpoint["id"],
    }

    master_events = _projection_events(client, desk["session"]["id"])
    recovery_event = next(
        event
        for event in master_events
        if event["type"] == "master.task.recovered"
    )
    assert recovery_event["payload"]["actor"] == {
        "id": 1,
        "username": "owner",
    }
    assert recovery_event["payload"]["discarded_progress"] == recovery[
        "discarded_progress"
    ]
    assert oversized_node_id not in json.dumps(recovery_event)
    assert len(json.dumps(recovery_event["payload"]).encode("utf-8")) < 16 * 1024
    messages = client.get(
        f"/api/sessions/{desk['session']['id']}/messages"
    ).json()["messages"]
    recovery_message = next(
        message
        for message in messages
        if message["id"] == recovery_event["payload"]["message_id"]
    )
    assert "owner restored Task" in recovery_message["content"]
    assert f"checkpoint #{checkpoint['id']}" in recovery_message["content"]
    assert "Failed to Queued" in recovery_message["content"]
    assert "discarded" in recovery_message["content"].lower()
    audit = app.state.db.execute(
        "SELECT metadata FROM audit_log "
        "WHERE action = 'master.checkpoint.restore' AND target_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (str(job_id),),
    ).fetchone()
    assert json.loads(audit["metadata"])["discarded_progress"] == recovery[
        "discarded_progress"
    ]


def test_sse_reconnect_honors_last_event_id_header(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="last-event-id",
        tasks=[
            {
                "key": "task",
                "title": "Reconnect safe",
                "brief": "Project two states",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,)
    )
    app.state.master_projection.project_task(job_id)
    app.state.db.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,)
    )
    app.state.master_projection.project_task(job_id)
    events = _projection_events(client, desk["session"]["id"])

    resume_after = _sse_resume_cursor(0, str(events[0]["id"]))
    sse = asyncio.run(
        _next_sse_event(
            app,
            desk["session"]["id"],
            resume_after,
        )
    )
    assert f"id: {events[1]['id']}\n" in sse
    assert "event: master.task.completed\n" in sse


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


def test_api_run_cancel_cancels_master_task_and_projects_once(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path, max_parallel=1)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="api-cancel",
        tasks=[
            {"key": "upstream", "title": "Cancel me", "brief": "Run"},
            {
                "key": "downstream",
                "title": "Wait",
                "brief": "Wait",
                "depends_on": ["upstream"],
            },
        ],
    )
    upstream, downstream = jobs
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    assert app.state.master_supervisor.tick()["started"] == [upstream["id"]]
    run_id = app.state.db.execute(
        "SELECT r.id FROM runs r JOIN sessions s ON s.id = r.session_id "
        "WHERE s.job_id = ?",
        (upstream["id"],),
    ).fetchone()["id"]

    first = client.post(f"/api/runs/{run_id}/cancel")
    repeated = client.post(f"/api/runs/{run_id}/cancel")

    assert first.status_code == repeated.status_code == 200
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (upstream["id"],)
    ).fetchone()["status"] == "cancelled"
    blocker = app.state.db.execute(
        "SELECT status, blocked_reason FROM jobs WHERE id = ?",
        (downstream["id"],),
    ).fetchone()
    assert blocker["status"] == "queued"
    assert "which cancelled" in blocker["blocked_reason"]
    cancelled = [
        event
        for event in _projection_events(client, desk["session"]["id"])
        if event["type"] == "master.task.cancelled"
        and event["payload"]["task_id"] == upstream["id"]
    ]
    assert len(cancelled) == 1


def test_idle_reconcile_ticks_do_not_reinsert_projected_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app, client, project = _app_and_client(tmp_path, max_parallel=1)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="idle-reconcile",
        tasks=[{"key": "solo", "title": "Solo", "brief": "Run"}],
    )
    job = jobs[0]
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    assert app.state.master_supervisor.tick()["started"] == [job["id"]]
    app.state.worker_db.execute(
        "UPDATE jobs SET status = 'done', finished_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job["id"],),
    )

    first = app.state.master_projection.reconcile()
    assert first["created"] >= 1

    service = app.state.master_projection
    original_insert = service._insert
    insert_calls: list[str] = []

    def spy_insert(*args, **kwargs):
        insert_calls.append(str(kwargs.get("projection_type", "")))
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(service, "_insert", spy_insert)

    for _ in range(3):
        assert app.state.master_projection.reconcile()["created"] == 0
    for _ in range(3):
        app.state.master_supervisor.tick()

    assert insert_calls == []
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE source_table = 'jobs' AND source_id = ? "
        "AND projection_type = 'master.task.completed'",
        (job["id"],),
    ).fetchone()[0] == 1


def test_uncaptured_legacy_master_task_remains_startable_and_approvable(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="legacy-uncaptured-start",
        tasks=[
            {
                "key": "legacy",
                "title": "Legacy Task",
                "brief": "Continue after migration",
            }
        ],
    )
    job_id = jobs[0]["id"]
    _make_focus_origin_uncaptured(app, job_id)

    result = app.state.task_delegation.start(job_id, {"id": 1})

    assert result.started is True
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["status"] == "running"
    assert app.state.master_projection.safe_project_task(job_id) is None
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE source_table = 'jobs' "
        "AND source_id = ?",
        (job_id,),
    ).fetchone()[0] == 0
    app.state.db.execute(
        "UPDATE jobs SET status = 'review' WHERE id = ?",
        (job_id,),
    )

    approved = client.post(f"/api/jobs/{job_id}/approve")

    assert approved.status_code == 200
    assert approved.json()["status"] == "done"
    assert dict(
        app.state.db.execute(
            "SELECT state, failure_code, attempt_count "
            "FROM task_projection_outbox WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    ) == {
        "state": "failed_attribution",
        "failure_code": "focus_attribution_unavailable",
        "attempt_count": 1,
    }
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE source_table = 'jobs' "
        "AND source_id = ?",
        (job_id,),
    ).fetchone()[0] == 0


def test_uncaptured_legacy_checkpoint_restore_commits_repair_intent(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="legacy-recovery-intent",
        tasks=[{
            "key": "legacy",
            "title": "Legacy recovery",
            "brief": "Restore without unsafe attribution",
        }],
    )
    job_id = jobs[0]["id"]
    checkpoint = create_checkpoint(app.state.db, job_id)
    _make_focus_origin_uncaptured(app, job_id)
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed', "
        "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job_id,),
    )

    restored = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )
    repeated = client.post(
        f"/api/jobs/{job_id}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )

    assert restored.status_code == 200
    assert repeated.status_code == 200
    assert restored.json()["restored_status"] == "queued"
    assert restored.json()["projection_repair"] == {
        "outbox_id": restored.json()["projection_repair"]["outbox_id"],
        "state": "failed_attribution",
        "failure_code": "focus_attribution_unavailable",
    }
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()["status"] == "queued"
    outboxes = app.state.db.execute(
        "SELECT state, failure_code, attempt_count, recovery_json "
        "FROM task_recovery_outbox WHERE job_id = ? ORDER BY task_event_id",
        (job_id,),
    ).fetchall()
    assert len(outboxes) == 2
    assert [outbox["state"] for outbox in outboxes] == [
        "failed_attribution",
        "failed_attribution",
    ]
    assert [outbox["failure_code"] for outbox in outboxes] == [
        "focus_attribution_unavailable",
        "focus_attribution_unavailable",
    ]
    assert [outbox["attempt_count"] for outbox in outboxes] == [1, 0]
    assert all(
        len(outbox["recovery_json"].encode("utf-8")) < 16 * 1024
        for outbox in outboxes
    )
    latest_task_event_id = app.state.db.execute(
        "SELECT task_event_id FROM task_recovery_outbox WHERE job_id = ? "
        "ORDER BY task_event_id DESC LIMIT 1",
        (job_id,),
    ).fetchone()["task_event_id"]
    expected_repair = {
        "kind": "recovery",
        "state": "failed_attribution",
        "failure_code": "focus_attribution_unavailable",
        "task_event_id": latest_task_event_id,
    }
    assert client.get(
        f"/api/jobs/{job_id}"
    ).json()["projection_repair"] == expected_repair
    assert next(
        job
        for job in client.get("/api/master/desk").json()["jobs"]
        if job["id"] == job_id
    )["projection_repair"] == expected_repair
    assert "master.task.recovered" not in [
        event["type"]
        for event in _projection_events(client, desk["session"]["id"])
    ]

    app.state.db.execute("DROP TRIGGER task_delegations_focus_immutable")
    app.state.db.execute(
        "UPDATE task_delegations SET origin_focus_captured = 1 "
        "WHERE job_id = ?",
        (job_id,),
    )
    next(migration[2] for migration in MIGRATIONS if migration[0] == 40)(
        app.state.db
    )
    app.state.master_projection.reconcile()

    repaired = app.state.db.execute(
        "SELECT state, failure_code, attempt_count "
        "FROM task_recovery_outbox WHERE job_id = ? ORDER BY task_event_id",
        (job_id,),
    ).fetchall()
    assert [dict(row) for row in repaired] == [
        {
            "state": "projected",
            "failure_code": None,
            "attempt_count": 2,
        },
        {
            "state": "projected",
            "failure_code": None,
            "attempt_count": 1,
        },
    ]
    assert client.get(
        f"/api/jobs/{job_id}"
    ).json()["projection_repair"] is None
    assert [
        event["type"]
        for event in _projection_events(client, desk["session"]["id"])
    ] == ["master.task.recovered", "master.task.recovered"]


def test_reconcile_continues_after_uncaptured_legacy_task(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="legacy-uncaptured-reconcile",
        tasks=[
            {
                "key": "legacy",
                "title": "Legacy Task",
                "brief": "Remain unprojected",
            },
            {
                "key": "current",
                "title": "Current Task",
                "brief": "Project after the legacy Task",
            },
        ],
    )
    legacy_id, current_id = (job["id"] for job in jobs)
    _make_focus_origin_uncaptured(app, legacy_id)
    app.state.db.execute(
        "UPDATE jobs SET status = 'done', updated_at = CURRENT_TIMESTAMP "
        "WHERE id IN (?, ?)",
        (legacy_id, current_id),
    )

    result = app.state.master_projection.reconcile()

    assert result == {"observed": 2, "created": 1}
    projections = app.state.db.execute(
        "SELECT source_id, projection_type FROM master_projections "
        "WHERE source_table = 'jobs' ORDER BY source_id",
    ).fetchall()
    assert [
        (row["source_id"], row["projection_type"]) for row in projections
    ] == [(current_id, "master.task.completed")]


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


def test_master_graph_branch_never_queues_beyond_parallel_capacity(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path, max_parallel=1)
    desk = client.get("/api/master/desk").json()
    owner = dict(
        app.state.db.execute(
            "SELECT * FROM users WHERE username = 'owner'"
        ).fetchone()
    )
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE user_id = ? AND is_default = 1",
        (owner["id"],),
    ).fetchone()["id"]
    project_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (project["slug"],)
    ).fetchone()["id"]
    area_id = project["ops_area"]["id"]
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "left", "name": "Left", "instruction": "Do left"},
                {"id": "right", "name": "Right", "instruction": "Do right"},
            ]
        }
    )
    recipe_id = app.state.db.execute(
        "INSERT INTO workflows(project_id, name, graph, created_by) "
        "VALUES (?, 'Parallel recipe', ?, ?)",
        (project_id, json.dumps(graph), owner["id"]),
    ).lastrowid
    delegated = app.state.task_delegation.create_and_start(
        owner,
        TaskDelegationRequest(
            title="Parallel Task",
            brief="Run both graph branches",
            container_id=project_id,
            area_id=area_id,
            profile_id=profile_id,
            execution_policy="guarded",
            idempotency_key="parallel-capacity",
            recipe_id=recipe_id,
            origin_session_id=desk["session"]["id"],
            routing_reason="Capacity integration test",
        ),
        start=False,
        connection=app.state.db,
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)

    app.state.master_supervisor.tick()

    active_runs = app.state.db.execute(
        "SELECT COUNT(*) FROM runs r "
        "JOIN sessions s ON s.id = r.session_id "
        "WHERE s.job_id = ? AND r.status IN ('queued', 'running')",
        (delegated.job["id"],),
    ).fetchone()[0]
    assert active_runs <= 1


def test_separate_worker_connections_claim_one_queued_run_once(
    tmp_path: Path,
):
    database_path = tmp_path / "cross-process-claim.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
        max_parallel=1,
    )
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="cross-process-claim",
        tasks=[{"key": "one", "title": "One claim", "brief": "Run once"}],
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)
    assert app.state.master_supervisor.tick()["started"] == [jobs[0]["id"]]
    second_app = create_app(dict(app.state.config))

    update_seen = [threading.Event(), threading.Event()]
    for index, candidate in enumerate((app, second_app)):
        candidate.state.worker_db.set_trace_callback(
            lambda statement, signal=update_seen[index]: (
                signal.set()
                if "UPDATE runs SET status = 'running'" in statement
                else None
            )
        )
    blocker = connect(database_path)
    blocker.execute("BEGIN IMMEDIATE")
    barrier = threading.Barrier(3)
    claims: list[dict | None] = []

    def claim(candidate) -> None:
        barrier.wait()
        claims.append(candidate.state.worker.claim_run())

    threads = [
        threading.Thread(target=claim, args=(candidate,))
        for candidate in (app, second_app)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for signal in update_seen:
        signal.wait(timeout=0.5)
    blocker.execute("COMMIT")
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len([claim for claim in claims if claim is not None]) == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM events WHERE run_id = ("
        "SELECT r.id FROM runs r JOIN sessions s ON s.id = r.session_id "
        "WHERE s.job_id = ?) AND type = 'run.started'",
        (jobs[0]["id"],),
    ).fetchone()[0] == 1
    assert desk["session"]["id"] > 0


def test_separate_start_connections_reserve_master_capacity_atomically(
    tmp_path: Path,
):
    database_path = tmp_path / "cross-process-start.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
        max_parallel=1,
    )
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="cross-process-start",
        tasks=[
            {"key": "one", "title": "One", "brief": "Run one"},
            {"key": "two", "title": "Two", "brief": "Run two"},
        ],
    )
    second_app = create_app(dict(app.state.config))
    barrier = threading.Barrier(3)
    results: list[object] = []

    def start(candidate, job_id: int) -> None:
        barrier.wait()
        try:
            results.append(
                candidate.state.task_delegation.start(
                    job_id,
                    {"id": 1},
                    connection=candidate.state.worker_db,
                )
            )
        except Exception as exc:
            results.append(exc)

    threads = [
        threading.Thread(
            target=start,
            args=(candidate, job["id"]),
        )
        for candidate, job in zip((app, second_app), jobs, strict=True)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert app.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')"
    ).fetchone()[0] == 1
    assert sorted(
        row["status"]
        for row in app.state.db.execute(
            "SELECT status FROM jobs WHERE id IN (?, ?)",
            (jobs[0]["id"], jobs[1]["id"]),
        ).fetchall()
    ) == ["queued", "running"]
    assert len(results) == 2


def test_separate_start_connections_reserve_supervisor_budget_atomically(
    tmp_path: Path,
):
    database_path = tmp_path / "cross-process-budget.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
        max_parallel=2,
    )
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="cross-process-budget",
        tasks=[
            {"key": "one", "title": "One", "brief": "Run one"},
            {"key": "two", "title": "Two", "brief": "Run two"},
        ],
    )
    app_settings.set_master_settings(
        app.state.worker_db,
        unattended=True,
        budget_turns=1,
    )
    second_app = create_app(dict(app.state.config))
    barrier = threading.Barrier(3)
    results: list[object] = []

    def start(candidate, job_id: int) -> None:
        barrier.wait()
        try:
            results.append(
                candidate.state.task_delegation.start(
                    job_id,
                    {"id": 1},
                    connection=candidate.state.worker_db,
                    supervisor_budget_turns=1,
                )
            )
        except Exception as exc:
            results.append(exc)

    threads = [
        threading.Thread(
            target=start,
            args=(candidate, job["id"]),
        )
        for candidate, job in zip((app, second_app), jobs, strict=True)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert app.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')"
    ).fetchone()[0] == 1
    assert app_settings.get_setting(
        app.state.db,
        "master.budget.turns_used",
    ) == "1"
    assert sum(
        getattr(result, "code", None) == "master_budget_exhausted"
        for result in results
    ) == 1


def test_supervisor_skips_forged_routing_and_starts_later_valid_task(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path, max_parallel=1)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users WHERE username = 'owner'"
    ).fetchone()["id"]
    profile = app.state.db.execute(
        "SELECT * FROM profiles WHERE user_id = ? AND is_default = 1",
        (owner_id,),
    ).fetchone()
    other_id = app.state.db.execute(
        "INSERT INTO users(username, os_user) VALUES ('other-route', 'other-route')"
    ).lastrowid
    other_root = tmp_path / "other-route"
    other_root.mkdir()
    other_project_id = app.state.db.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('other-route', 'Other route', ?, ?)",
        (str(other_root), other_id),
    ).lastrowid
    other_area_id = app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', 'ops', 'auto')",
        (other_project_id,),
    ).lastrowid
    forged_session_id = app.state.db.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, "
        "runner_id, mode) VALUES ('Forged worker', ?, ?, ?, ?, 'chat')",
        (
            other_project_id,
            owner_id,
            profile["id"],
            profile["runner_id"],
        ),
    ).lastrowid
    forged_job_id = app.state.db.execute(
        "INSERT INTO jobs(project_id, session_id, title, input, steps_state, "
        "target_area_id, created_by, origin_master_session_id) "
        "VALUES (?, ?, 'Forged route', '{}', ?, ?, ?, ?)",
        (
            other_project_id,
            forged_session_id,
            json.dumps(
                [
                    {
                        "name": "Task",
                        "instruction": "Must not run",
                        "status": "pending",
                    }
                ]
            ),
            other_area_id,
            owner_id,
            desk["session"]["id"],
        ),
    ).lastrowid
    app.state.db.execute(
        "UPDATE sessions SET job_id = ? WHERE id = ?",
        (forged_job_id, forged_session_id),
    )
    _desk, valid_jobs = _delegate(
        app,
        client,
        project,
        key="valid-after-forged",
        tasks=[{"key": "valid", "title": "Valid", "brief": "Run valid"}],
    )
    app_settings.set_master_settings(app.state.worker_db, unattended=True)

    tick = app.state.master_supervisor.tick()

    assert tick["started"] == [valid_jobs[0]["id"]]
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (forged_job_id,)
    ).fetchone()["status"] == "queued"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE session_id = ?", (forged_session_id,)
    ).fetchone()[0] == 0
    app.state.db.execute(
        "UPDATE runs SET status = 'cancelled' WHERE session_id = ("
        "SELECT session_id FROM jobs WHERE id = ?)",
        (valid_jobs[0]["id"],),
    )
    app.state.db.execute(
        "INSERT INTO runs(session_id, project_id, user_id, profile_id, "
        "runner_id, status, prompt) VALUES (?, ?, ?, ?, ?, 'queued', 'forged')",
        (
            forged_session_id,
            other_project_id,
            owner_id,
            profile["id"],
            profile["runner_id"],
        ),
    )
    assert app.state.worker.claim_run() is None


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


def test_startup_rejects_matching_but_unsafe_legacy_projection_payload(
    tmp_path: Path,
):
    database_path = tmp_path / "unsafe-legacy-projection.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
    )
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="unsafe-legacy-projection",
        tasks=[{"key": "task", "title": "Project", "brief": "Finish"}],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?",
        (job_id,),
    )
    projection = app.state.master_projection.project_task(job_id)
    assert projection is not None
    payload = json.loads(projection["payload_json"])
    payload["raw_path"] = "/private/worktree"
    unsafe = json.dumps(payload)
    app.state.db.execute(
        "UPDATE master_projections SET payload_json = ? WHERE id = ?",
        (unsafe, projection["id"]),
    )
    app.state.db.execute(
        "UPDATE events SET payload = ? WHERE id = ?",
        (unsafe, projection["event_id"]),
    )

    with pytest.raises(RuntimeError, match="payload links"):
        create_app(dict(app.state.config))


def test_concurrent_projection_connections_create_one_message_and_event(
    tmp_path: Path,
):
    database_path = tmp_path / "concurrent-projection.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
    )
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="concurrent-projection",
        tasks=[{"key": "task", "title": "Project once", "brief": "Finish"}],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,)
    )
    second_app = create_app(dict(app.state.config))
    barrier = threading.Barrier(3)
    results: list[dict | None] = []

    def project(candidate) -> None:
        barrier.wait()
        results.append(candidate.state.master_projection.project_task(job_id))

    threads = [
        threading.Thread(target=project, args=(candidate,))
        for candidate in (app, second_app)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len(results) == 2
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? "
        "AND author = 'Master'",
        (desk["session"]["id"],),
    ).fetchone()[0] == 1
    assert len(_projection_events(client, desk["session"]["id"])) == 1


def test_projection_transaction_rolls_back_all_three_rows(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="projection-rollback",
        tasks=[{"key": "task", "title": "Rollback", "brief": "Do work"}],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,)
    )
    app.state.db.execute(
        "CREATE TRIGGER reject_master_projection_event "
        "BEFORE INSERT ON events "
        "WHEN NEW.type = 'master.task.completed' "
        "BEGIN SELECT RAISE(ABORT, 'projection event rejected'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="rejected"):
        app.state.master_projection.project_task(job_id)

    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE task_id = ?",
        (job_id,),
    ).fetchone()[0] == 0
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? "
        "AND author = 'Master'",
        (desk["session"]["id"],),
    ).fetchone()[0] == 0
    assert _projection_events(client, desk["session"]["id"]) == []


def test_projection_links_survive_task_delete_and_restrict_delivery_delete(
    tmp_path: Path,
):
    database_path = tmp_path / "projection-delete.db"
    app, client, project = _app_and_client(
        tmp_path,
        database_path=database_path,
    )
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="projection-delete",
        tasks=[{"key": "task", "title": "Delete source", "brief": "Finish"}],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,)
    )
    projection = app.state.master_projection.project_task(job_id)
    assert projection is not None

    with pytest.raises(sqlite3.IntegrityError):
        app.state.db.execute(
            "DELETE FROM messages WHERE id = ?", (projection["message_id"],)
        )
    with pytest.raises(sqlite3.IntegrityError):
        app.state.db.execute(
            "DELETE FROM events WHERE id = ?", (projection["event_id"],)
        )
    app.state.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    stored = app.state.db.execute(
        "SELECT task_id, source_id, message_id, event_id "
        "FROM master_projections WHERE id = ?",
        (projection["id"],),
    ).fetchone()
    assert stored["task_id"] is None
    assert stored["source_id"] == job_id
    assert stored["message_id"] == projection["message_id"]
    assert stored["event_id"] == projection["event_id"]
    create_app(dict(app.state.config))


def test_task_projection_focus_survives_origin_run_deletion(
    tmp_path: Path,
    monkeypatch,
):
    app, client, project = _app_and_client(tmp_path)
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda _runner_id: (True, ""),
    )
    desk = client.get("/api/master/desk").json()
    project_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?",
        (project["slug"],),
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops'",
        (project_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    focus = client.put(
        "/api/master/focus",
        json={"container_id": project_id, "version": 0},
    ).json()["focus"]
    turn = client.post(
        "/api/master/messages",
        json={"content": "Delegate a durable focused Task"},
    ).json()
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        origin_message_id=turn["message"]["id"],
    )
    delegated = broker.execute(
        "delegate_tasks",
        {
            "idempotency_key": "durable-focus-origin",
            "start": False,
            "tasks": [
                {
                    "title": "Keep Focus",
                    "brief": "Finish after the origin run is deleted",
                    "container_id": project_id,
                    "area_id": area_id,
                    "profile_id": profile_id,
                }
            ],
        },
    )
    job_id = delegated["result"]["tasks"][0]["id"]
    captured = app.state.db.execute(
        "SELECT origin_message_id, origin_focus_epoch_id, "
        "origin_focus_captured FROM task_delegations WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert dict(captured) == {
        "origin_message_id": turn["message"]["id"],
        "origin_focus_epoch_id": focus["current_epoch_id"],
        "origin_focus_captured": 1,
    }

    app.state.db.execute(
        "UPDATE runs SET status = 'completed', "
        "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (turn["run_id"],),
    )
    deleted = client.delete(f"/api/runs/{turn['run_id']}")
    assert deleted.status_code == 200
    durable = app.state.db.execute(
        "SELECT origin_message_id, origin_focus_epoch_id, "
        "origin_focus_captured FROM task_delegations WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert dict(durable) == {
        "origin_message_id": None,
        "origin_focus_epoch_id": focus["current_epoch_id"],
        "origin_focus_captured": 1,
    }

    app.state.db.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ?",
        (job_id,),
    )
    projection = app.state.master_projection.project_task(job_id)
    attribution = app.state.db.execute(
        "SELECT focus_epoch_id, focus_container_id "
        "FROM message_focus WHERE message_id = ?",
        (projection["message_id"],),
    ).fetchone()
    assert dict(attribution) == {
        "focus_epoch_id": focus["current_epoch_id"],
        "focus_container_id": project_id,
    }


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


def test_projection_refuses_forged_taskless_attention_owner(tmp_path: Path):
    app, client, _project = _app_and_client(tmp_path)
    desk = client.get("/api/master/desk").json()
    attention_id = app.state.db.execute(
        "INSERT INTO attention_items(kind, title, target_json, source_key) "
        "VALUES ('master_decision', 'Forged attention', ?, 'forged-source')",
        (
            json.dumps(
                {
                    "origin_master_session_id": desk["session"]["id"],
                    "message": "This row has no owner-scoped source",
                }
            ),
        ),
    ).lastrowid

    assert app.state.master_projection.project_attention(attention_id) is None
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections WHERE source_id = ? "
        "AND source_table = 'attention_items'",
        (attention_id,),
    ).fetchone()[0] == 0


def test_retried_attention_tool_projects_one_action_required_message(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="decision-task",
        tasks=[
            {
                "title": "Prepare rollout",
                "brief": "Prepare a rollout plan",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', current_step_idx = 0, "
        "steps_state = ? WHERE id = ?",
        (
            json.dumps(
                [
                    {
                        "title": "Prepare rollout",
                        "status": "done",
                        "output_summary": "A rollout window is still needed.",
                    }
                ]
            ),
            job_id,
        ),
    )
    project_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?",
        (project["slug"],),
    ).fetchone()["id"]
    current_focus = master_focus.change_focus(
        app.state.db,
        master_session_id=desk["session"]["id"],
        container_id=project_id,
        expected_version=0,
    )
    origin = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) "
        "VALUES (?, 'user', 'Ask for a decision')",
        (desk["session"]["id"],),
    )
    master_focus.stamp_message(
        app.state.db,
        message_id=origin.lastrowid,
        focus_epoch_id=current_focus["current_epoch_id"],
    )
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        origin_message_id=origin.lastrowid,
    )
    args = {
        "title": "Choose a direction",
        "prompt": "Pick option A or B.",
        "context": "The rollout is ready once the owner chooses.",
        "response": {
            "type": "choice",
            "choices": [
                {"id": "a", "label": "Option A"},
                {"id": "b", "label": "Option B"},
            ],
        },
        "task_id": job_id,
        "idempotency_key": "attention-choice",
    }

    first = broker.execute("create_attention", args)
    second = broker.execute("create_attention", args)

    assert first["result"]["attention_id"] == second["result"]["attention_id"]
    assert first["result"]["decision_id"] == second["result"]["decision_id"]
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
    assert {
        key: attention_events[0]["payload"][key]
        for key in (
            "focus_epoch_id",
            "focus_container_id",
            "subject_container_id",
        )
    } == {
        "focus_epoch_id": None,
        "focus_container_id": None,
        "subject_container_id": project_id,
    }
    projection_message_id = attention_events[0]["payload"]["message_id"]
    attribution = app.state.db.execute(
        "SELECT focus_epoch_id, focus_container_id "
        "FROM message_focus WHERE message_id = ?",
        (projection_message_id,),
    ).fetchone()
    assert dict(attribution) == {
        "focus_epoch_id": None,
        "focus_container_id": None,
    }


def test_master_decision_defer_resolve_and_stale_response_are_exactly_once(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="decision-resolution-task",
        tasks=[
            {
                "title": "Prepare production rollout",
                "brief": "Prepare the release and wait for a rollout window",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', current_step_idx = 0, "
        "steps_state = ? WHERE id = ?",
        (
            json.dumps(
                [
                    {
                        "title": "Prepare release",
                        "status": "done",
                        "output_summary": "The rollout window needs a decision.",
                    }
                ]
            ),
            job_id,
        ),
    )
    origin = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'user', 'Prepare the rollout', 'owner')",
        (desk["session"]["id"],),
    )
    master_focus.stamp_message(
        app.state.db,
        message_id=origin.lastrowid,
        focus_epoch_id=None,
    )
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1, "username": "owner"},
        desk["session"]["id"],
        origin_message_id=origin.lastrowid,
    )
    created = broker.execute(
        "create_attention",
        {
            "title": "Choose rollout window",
            "prompt": "Which rollout window should the release use?",
            "context": "Both windows include two hours of planned downtime.",
            "response": {
                "type": "choice",
                "choices": [
                    {
                        "id": "saturday",
                        "label": "Saturday 02:00 UTC",
                    },
                    {
                        "id": "sunday",
                        "label": "Sunday 02:00 UTC",
                    },
                ],
            },
            "task_id": job_id,
            "idempotency_key": "rollout-window",
        },
    )
    assert created["ok"] is True
    decision_id = created["result"]["decision_id"]

    attention = client.get("/api/attention").json()["items"]
    decision_item = next(
        item for item in attention if item["kind"] == "master_decision"
    )
    assert decision_item["decision"]["prompt"] == (
        "Which rollout window should the release use?"
    )
    assert decision_item["decision"]["context"].startswith("Both windows")
    assert decision_item["decision"]["task"]["id"] == job_id
    assert not any(item["id"] == f"job:{job_id}" for item in attention)
    assert client.get("/api/master/desk").json()["decisions"][0][
        "origin_message_id"
    ] == origin.lastrowid
    task_payload = client.get(f"/api/jobs/{job_id}").json()
    assert task_payload["master_decision"]["id"] == decision_id
    assert task_payload["master_decision"]["prompt"] == (
        "Which rollout window should the release use?"
    )
    generic_approval = client.post(f"/api/jobs/{job_id}/approve")
    assert generic_approval.status_code == 409
    assert generic_approval.json()["detail"]["code"] == (
        "master_decision_pending"
    )
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"

    invalid = client.post(
        f"/api/master/decisions/{decision_id}/resolve",
        json={"expected_version": 1, "response": "weekday"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_decision_response"
    assert app.state.db.execute(
        "SELECT state FROM master_decisions WHERE id = ?", (decision_id,)
    ).fetchone()["state"] == "pending"

    stale_defer = client.post(
        f"/api/master/decisions/{decision_id}/defer",
        json={"expected_version": 99},
    )
    assert stale_defer.status_code == 409
    assert stale_defer.json()["detail"]["code"] == "decision_stale"

    deferred = client.post(
        f"/api/master/decisions/{decision_id}/defer",
        json={"expected_version": 1},
    )
    assert deferred.status_code == 200
    assert deferred.json()["state"] == "deferred"
    assert deferred.json()["version"] == 2
    assert not any(
        item["kind"] == "master_decision"
        for item in client.get("/api/attention").json()["items"]
    )
    reloaded = client.get("/api/master/desk").json()["decisions"]
    assert reloaded[0]["state"] == "deferred"
    assert reloaded[0]["version"] == 2

    resolved = client.post(
        f"/api/master/decisions/{decision_id}/resolve",
        json={"expected_version": 2, "response": "sunday"},
    )
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["state"] == "resolved"
    assert payload["response"] == {
        "value": "sunday",
        "label": "Sunday 02:00 UTC",
    }
    assert payload["resolved_by_user_id"] == 1
    assert payload["resolved_at"]

    job = app.state.db.execute(
        "SELECT session_id, status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "running"
    run_rows = app.state.db.execute(
        "SELECT id, prompt FROM runs WHERE session_id = ? ORDER BY id",
        (job["session_id"],),
    ).fetchall()
    assert len(run_rows) == 1
    assert "Sunday 02:00 UTC" in run_rows[0]["prompt"]
    task_messages = app.state.db.execute(
        "SELECT content FROM messages WHERE session_id = ? AND role = 'user'",
        (job["session_id"],),
    ).fetchall()
    assert len(task_messages) == 1
    assert "Sunday 02:00 UTC" in task_messages[0]["content"]
    task_events = app.state.db.execute(
        "SELECT type, payload FROM events WHERE run_id = ? ORDER BY seq",
        (run_rows[0]["id"],),
    ).fetchall()
    assert [event["type"] for event in task_events] == [
        "master.decision.resolved",
        "run.queued",
    ]
    assert json.loads(task_events[0]["payload"])["actor_user_id"] == 1
    assert json.loads(task_events[1]["payload"]) == {
        "runner": json.loads(task_events[1]["payload"])["runner"],
        "job": job_id,
        "decision_id": decision_id,
    }
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'job.update' "
        "AND session_id = ? "
        "AND json_extract(payload, '$.job_id') = ? "
        "AND json_extract(payload, '$.mutation') = 'review_approved' "
        "AND json_extract(payload, '$.status') = 'running'",
        (job["session_id"], job_id),
    ).fetchone()[0] == 1
    outbox = app.state.db.execute(
        "SELECT state, mutation, task_status FROM task_projection_outbox "
        "WHERE job_id = ? ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    assert dict(outbox) == {
        "state": "projected",
        "mutation": "review_approved",
        "task_status": "running",
    }
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'master.decision.resolve' "
        "AND target_id = ?",
        (str(decision_id),),
    ).fetchone()[0] == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE projection_type IN ("
        "'master.decision.deferred', 'master.decision.resolved'"
        ") AND source_id = ?",
        (created["result"]["attention_id"],),
    ).fetchone()[0] == 2
    projection_messages = [
        row["content"]
        for row in app.state.db.execute(
            "SELECT message.content FROM master_projections projection "
            "JOIN messages message ON message.id = projection.message_id "
            "WHERE projection.source_id = ? "
            "AND projection.projection_type LIKE 'master.decision.%' "
            "ORDER BY projection.id",
            (created["result"]["attention_id"],),
        ).fetchall()
    ]
    assert projection_messages == [
        f"Owner deferred decision #{decision_id} for Task #{job_id}.",
        (
            f"Owner resolved decision #{decision_id} for Task #{job_id}. "
            "The Task is continuing."
        ),
    ]

    repeated = client.post(
        f"/api/master/decisions/{decision_id}/resolve",
        json={"expected_version": 2, "response": "sunday"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "decision_stale"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE session_id = ?", (job["session_id"],)
    ).fetchone()[0] == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE session_id = ? AND role = 'user'",
        (job["session_id"],),
    ).fetchone()[0] == 1
    assert client.get(
        f"/api/master/decisions/{decision_id}"
    ).json()["response"]["label"] == "Sunday 02:00 UTC"



def _create_review_decision(
    app,
    client: TestClient,
    project: dict,
    *,
    key: str,
    engine: str = "linear",
    steps_state: list[dict] | None = None,
):
    desk, jobs = _delegate(
        app,
        client,
        project,
        key=key,
        tasks=[
            {
                "title": "Prepare production rollout",
                "brief": "Prepare the release and wait for a rollout window",
            }
        ],
    )
    job_id = jobs[0]["id"]
    if engine == "graph":
        graph = normalize_graph(
            {
                "nodes": [
                    {
                        "id": "n1",
                        "name": "Prepare",
                        "type": "agent",
                        "instruction": "Prepare the release",
                    }
                ],
                "edges": [],
            }
        )
        app.state.db.execute(
            "UPDATE jobs SET status = 'review', engine = 'graph', graph = ?, "
            "steps_state = '[]', current_step_idx = 0 WHERE id = ?",
            (json.dumps(graph), job_id),
        )
    else:
        steps = steps_state or [
            {
                "title": "Prepare release",
                "status": "done",
                "output_summary": "The rollout window needs a decision.",
            }
        ]
        app.state.db.execute(
            "UPDATE jobs SET status = 'review', current_step_idx = 0, "
            "steps_state = ? WHERE id = ?",
            (json.dumps(steps), job_id),
        )
    origin = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'user', 'Prepare the rollout', 'owner')",
        (desk["session"]["id"],),
    )
    master_focus.stamp_message(
        app.state.db,
        message_id=origin.lastrowid,
        focus_epoch_id=None,
    )
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1, "username": "owner"},
        desk["session"]["id"],
        origin_message_id=origin.lastrowid,
    )
    created = broker.execute(
        "create_attention",
        {
            "title": "Choose rollout window",
            "prompt": "Which rollout window should the release use?",
            "context": "Both windows include two hours of planned downtime.",
            "response": {
                "type": "choice",
                "choices": [
                    {"id": "saturday", "label": "Saturday 02:00 UTC"},
                    {"id": "sunday", "label": "Sunday 02:00 UTC"},
                ],
            },
            "task_id": job_id,
            "idempotency_key": f"{key}-decision",
        },
    )
    return desk, job_id, created, origin.lastrowid




def test_reject_settles_unresolved_master_decision(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    _desk, job_id, created, _origin = _create_review_decision(
        app, client, project, key="reject-settles-decision"
    )
    assert created["ok"] is True
    decision_id = created["result"]["decision_id"]

    rejected = client.post(
        f"/api/jobs/{job_id}/reject",
        json={"reason": "Rollout is cancelled"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "failed"

    decision = client.get(f"/api/master/decisions/{decision_id}").json()
    assert decision["state"] == "resolved"
    assert decision["response"]["value"] == (
        master_decisions.TASK_LEFT_REVIEW_RESPONSE_VALUE
    )
    assert "left review" in decision["response"]["label"].lower()
    assert app.state.db.execute(
        "SELECT status FROM attention_items WHERE id = ?",
        (decision["attention_item_id"],),
    ).fetchone()["status"] == "resolved"
    assert not any(
        item["kind"] == "master_decision"
        for item in client.get("/api/attention").json()["items"]
    )
    assert client.get("/api/master/desk").json()["decisions"] == []
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'master.decision.settle' AND target_id = ?",
        (str(decision_id),),
    ).fetchone()[0] == 1
    # Settled decisions cannot continue the failed Task.
    repeated = client.post(
        f"/api/master/decisions/{decision_id}/resolve",
        json={"expected_version": decision["version"], "response": "sunday"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "decision_not_pending"


def test_delete_job_settles_and_projects_master_decision(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    desk, job_id, created, _origin = _create_review_decision(
        app, client, project, key="delete-settles-decision"
    )
    assert created["ok"] is True
    decision_id = created["result"]["decision_id"]
    master_session_id = desk["session"]["id"]

    deleted = client.delete(f"/api/jobs/{job_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "id": job_id}

    decision = app.state.db.execute(
        "SELECT state, response_json, requesting_job_id FROM master_decisions "
        "WHERE id = ?",
        (decision_id,),
    ).fetchone()
    assert decision["state"] == "resolved"
    assert decision["requesting_job_id"] is None
    response = json.loads(decision["response_json"])
    assert response["value"] == master_decisions.TASK_LEFT_REVIEW_RESPONSE_VALUE
    assert response["task_id"] == job_id
    assert app.state.db.execute(
        "SELECT status FROM attention_items WHERE id = ?",
        (created["result"]["attention_id"],),
    ).fetchone()["status"] == "resolved"

    projection = app.state.db.execute(
        "SELECT projection_type, task_id, payload_json, message_id "
        "FROM master_projections "
        "WHERE projection_key = ? AND master_session_id = ?",
        (f"decision:{decision_id}:resolved", master_session_id),
    ).fetchone()
    assert projection is not None
    assert projection["projection_type"] == "master.decision.resolved"
    # FK is nulled after job delete; immutable identity lives in the payload.
    assert projection["task_id"] is None
    payload = json.loads(projection["payload_json"])
    assert payload["task_id"] == job_id
    assert payload["decision_id"] == decision_id
    assert payload["closed_without_owner_response"] is True
    message = app.state.db.execute(
        "SELECT content FROM messages WHERE id = ?",
        (projection["message_id"],),
    ).fetchone()
    assert message["content"] == (
        f"Decision #{decision_id} for Task #{job_id} "
        "was closed because the Task left review."
    )
    # After delete the live FK is gone, so catch-up cannot double-insert;
    # the durable outbox row already carries immutable task identity.
    assert app.state.master_projection.project_decision(decision_id) is None
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE projection_key = ?",
        (f"decision:{decision_id}:resolved",),
    ).fetchone()[0] == 1


def test_delete_job_rolls_back_settle_when_projection_fails(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    _desk, job_id, created, _origin = _create_review_decision(
        app, client, project, key="delete-rollback-decision"
    )
    assert created["ok"] is True
    decision_id = created["result"]["decision_id"]
    attention_id = created["result"]["attention_id"]

    original = app.state.master_projection.project_decision

    def _boom(decision_id_arg, **kwargs):
        raise RuntimeError("projection exploded")

    app.state.master_projection.project_decision = _boom  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="projection exploded"):
            client.delete(f"/api/jobs/{job_id}")
    finally:
        app.state.master_projection.project_decision = original  # type: ignore[method-assign]

    # Whole delete transaction rolled back: job, decision, and Attention remain.
    assert app.state.db.execute(
        "SELECT id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone() is not None
    decision = app.state.db.execute(
        "SELECT state, requesting_job_id FROM master_decisions WHERE id = ?",
        (decision_id,),
    ).fetchone()
    assert decision["state"] == "pending"
    assert decision["requesting_job_id"] == job_id
    assert app.state.db.execute(
        "SELECT status FROM attention_items WHERE id = ?", (attention_id,)
    ).fetchone()["status"] == "open"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_projections "
        "WHERE projection_key = ?",
        (f"decision:{decision_id}:resolved",),
    ).fetchone()[0] == 0


def test_project_delete_settles_open_decisions_and_projects(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    desk, job_id, created, _origin = _create_review_decision(
        app, client, project, key="project-delete-settles"
    )
    assert created["ok"] is True
    decision_id = created["result"]["decision_id"]
    master_session_id = desk["session"]["id"]

    deleted = client.delete(f"/api/projects/{project['slug']}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True

    decision = app.state.db.execute(
        "SELECT state, response_json, requesting_job_id FROM master_decisions "
        "WHERE id = ?",
        (decision_id,),
    ).fetchone()
    assert decision is not None
    assert decision["state"] == "resolved"
    assert decision["requesting_job_id"] is None
    assert json.loads(decision["response_json"])["task_id"] == job_id
    assert app.state.db.execute(
        "SELECT status FROM attention_items WHERE id = ?",
        (created["result"]["attention_id"],),
    ).fetchone()["status"] == "resolved"
    projection = app.state.db.execute(
        "SELECT payload_json, message_id FROM master_projections "
        "WHERE projection_key = ? AND master_session_id = ?",
        (f"decision:{decision_id}:resolved", master_session_id),
    ).fetchone()
    assert projection is not None
    payload = json.loads(projection["payload_json"])
    assert payload["task_id"] == job_id
    assert payload["closed_without_owner_response"] is True
    assert app.state.db.execute(
        "SELECT content FROM messages WHERE id = ?",
        (projection["message_id"],),
    ).fetchone()["content"] == (
        f"Decision #{decision_id} for Task #{job_id} "
        "was closed because the Task left review."
    )
    assert app.state.db.execute(
        "SELECT id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone() is None


def test_graph_task_rejects_master_decision_creation_and_guards_approve(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, job_id, created, origin_id = _create_review_decision(
        app, client, project, key="graph-decision-denied", engine="graph"
    )
    assert created["ok"] is False
    assert created["error"]["code"] == "decision_task_invalid"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_decisions WHERE requesting_job_id = ?",
        (job_id,),
    ).fetchone()[0] == 0

    # Defense in depth: even a forged open decision blocks graph approval.
    attention = app.state.db.execute(
        "INSERT INTO attention_items("
        "kind, title, target_json, inline_ok, actions_json, status, source_key"
        ") VALUES ('master_decision', 'Forged', '{}', 0, '[]', 'open', "
        "'forged-graph-decision')"
    )
    app.state.db.execute(
        "INSERT INTO master_decisions("
        "attention_item_id, owner_user_id, master_session_id, "
        "origin_message_id, requesting_job_id, title, prompt, context, "
        "response_shape_json, request_fingerprint, state"
        ") VALUES (?, 1, ?, ?, ?, 'Forged', 'Choose?', 'Context', ?, 'fp', "
        "'pending')",
        (
            attention.lastrowid,
            desk["session"]["id"],
            origin_id,
            job_id,
            json.dumps(
                {
                    "type": "choice",
                    "choices": [
                        {"id": "a", "label": "A"},
                        {"id": "b", "label": "B"},
                    ],
                }
            ),
        ),
    )
    approved = client.post(f"/api/graph/jobs/{job_id}/approve")
    assert approved.status_code == 409
    detail = approved.json()["detail"]
    assert detail["code"] == "master_decision_pending"
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"


def test_master_decision_free_text_shape_is_bounded_and_owner_readable():
    assert master_decisions.normalize_response_shape(
        {
            "type": "text",
            "max_length": 320,
            "placeholder": "Describe the rollout constraint",
        }
    ) == {
        "type": "text",
        "max_length": 320,
        "placeholder": "Describe the rollout constraint",
    }
    with pytest.raises(
        master_decisions.MasterDecisionError,
        match="1 to 4000 characters",
    ):
        master_decisions.normalize_response_shape(
            {"type": "text", "max_length": 5000}
        )


def test_projection_summaries_exclude_untrusted_paths_commands_and_secrets(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="safe-projection-summary",
        tasks=[
            {
                "key": "task",
                "title": "run /private/worktree/secret.sh",
                "brief": "Do work",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed', "
        "rejected_reason = 'token=TOP_SECRET /private/worktree' "
        "WHERE id = ?",
        (job_id,),
    )
    app.state.master_projection.project_task(job_id)
    permission_id = app.state.db.execute(
        "INSERT INTO attention_items(kind, title, target_json, source_key) "
        "VALUES ('permission_job', ?, ?, 'permission:safe-summary')",
        (
            "cd /private/worktree && publish TOP_SECRET",
            json.dumps(
                {
                    "job_id": job_id,
                    "message": "token=TOP_SECRET",
                }
            ),
        ),
    ).lastrowid
    app.state.master_projection.project_attention(permission_id)
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
    )
    broker.execute(
        "create_attention",
        {
            "title": "Read /private/worktree",
            "prompt": "Choose whether to read /private/worktree",
            "context": "token=TOP_SECRET",
            "response": {
                "type": "choice",
                "choices": [
                    {"id": "read", "label": "Read it"},
                    {"id": "skip", "label": "Skip it"},
                ],
            },
            "task_id": job_id,
            "idempotency_key": "safe-summary-attention",
        },
    )
    intervention_id = satpam.record_intervention(
        app.state.db,
        job_id,
        None,
        satpam.ACTION_STEER,
        satpam.DETECTION_STALLED,
        satpam.STATUS_APPLIED,
        "Inspect /private/worktree with token=TOP_SECRET",
    )
    app.state.master_projection.project_satpam(intervention_id)

    messages = app.state.db.execute(
        "SELECT content FROM messages WHERE session_id = ? "
        "AND author = 'Master'",
        (desk["session"]["id"],),
    ).fetchall()
    events = _projection_events(client, desk["session"]["id"])
    projected_text = json.dumps(
        {
            "messages": [row["content"] for row in messages],
            "events": [event["payload"] for event in events],
        }
    )
    assert "/private/worktree" not in projected_text
    assert "TOP_SECRET" not in projected_text
    assert "token=" not in projected_text
    assert "publish" not in projected_text
    assert {
        event["type"]
        for event in events
    } == {
        "master.attention.required",
        "master.satpam.steered",
        "master.task.failed",
    }


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
        json=with_browse_root(
            client,
            {"path": str(repo), "slug": "repo-projection"},
        ),
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



def test_bare_supervisor_start_failure_attention_stays_listed(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="bare-start-failure",
        tasks=[{"title": "Start me", "brief": "Fail to start"}],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "INSERT INTO attention_items("
        "kind, title, target_json, inline_ok, actions_json, status, source_key"
        ") VALUES ("
        "'master_decision', 'Master could not start queued work', ?, 0, '[]', "
        "'open', ?"
        ")",
        (
            json.dumps(
                {
                    "view": "master",
                    "job_id": job_id,
                    "error": "runner missing",
                    "origin_master_session_id": desk["session"]["id"],
                }
            ),
            f"master-start:{job_id}",
        ),
    )
    attention_id = app.state.db.execute(
        "SELECT id FROM attention_items WHERE source_key = ?",
        (f"master-start:{job_id}",),
    ).fetchone()["id"]

    items = client.get("/api/attention").json()["items"]
    bare = next(item for item in items if item["id"] == f"attention:{attention_id}")
    assert bare["kind"] == "master_decision"
    assert bare["title"] == "Master could not start queued work"
    assert "decision" not in bare or bare.get("decision") is None
    assert bare["target"]["job_id"] == job_id

    desk_payload = client.get("/api/master/desk").json()
    desk_bare = next(
        item
        for item in desk_payload["attention"]
        if item["id"] == f"attention:{attention_id}"
    )
    assert desk_bare["title"] == "Master could not start queued work"
    assert "decision" not in desk_bare or desk_bare.get("decision") is None


def test_approve_and_create_decision_race_cannot_orphan_pending(
    tmp_path: Path,
):
    database_path = tmp_path / "approve-decision-race.db"
    app, client, project = _app_and_client(
        tmp_path, database_path=database_path
    )
    desk, jobs = _delegate(
        app,
        client,
        project,
        key="approve-decision-race",
        tasks=[
            {
                "title": "Race window",
                "brief": "Stay in review for the race",
            }
        ],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', current_step_idx = 0, "
        "steps_state = ? WHERE id = ?",
        (
            json.dumps(
                [
                    {
                        "title": "Prepare release",
                        "status": "done",
                        "output_summary": "Ready for a decision race.",
                    }
                ]
            ),
            job_id,
        ),
    )
    origin = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'user', 'Prepare the rollout', 'owner')",
        (desk["session"]["id"],),
    )
    master_focus.stamp_message(
        app.state.db,
        message_id=origin.lastrowid,
        focus_epoch_id=None,
    )
    second_app = create_app(dict(app.state.config))
    second_client = TestClient(second_app)
    token = second_client.post("/auth/auto").json()["token"]
    second_client.headers.update({"Authorization": f"Bearer {token}"})

    barrier = threading.Barrier(3)
    results: dict[str, object] = {}

    def approve() -> None:
        barrier.wait()
        results["approve"] = client.post(f"/api/jobs/{job_id}/approve")

    def create() -> None:
        barrier.wait()
        broker = MasterToolBroker(
            second_app.state.db,
            second_app,
            {"id": 1, "username": "owner"},
            desk["session"]["id"],
            origin_message_id=origin.lastrowid,
        )
        results["create"] = broker.execute(
            "create_attention",
            {
                "title": "Choose rollout window",
                "prompt": "Which rollout window should the release use?",
                "context": "Both windows include two hours of planned downtime.",
                "response": {
                    "type": "choice",
                    "choices": [
                        {"id": "saturday", "label": "Saturday 02:00 UTC"},
                        {"id": "sunday", "label": "Sunday 02:00 UTC"},
                    ],
                },
                "task_id": job_id,
                "idempotency_key": "approve-decision-race",
            },
        )

    threads = [
        threading.Thread(target=approve),
        threading.Thread(target=create),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    job_status = app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"]
    pending_count = app.state.db.execute(
        "SELECT COUNT(*) FROM master_decisions "
        "WHERE requesting_job_id = ? AND state IN ('pending', 'deferred')",
        (job_id,),
    ).fetchone()[0]
    if job_status != "review":
        assert pending_count == 0
    if pending_count > 0:
        assert job_status == "review"
        approve_response = results["approve"]
        assert approve_response.status_code == 409
        assert approve_response.json()["detail"]["code"] == (
            "master_decision_pending"
        )
    create_result = results["create"]
    assert isinstance(create_result, dict)
    if create_result.get("ok"):
        assert pending_count == 1
        assert job_status == "review"
    else:
        assert job_status == "done"
        assert pending_count == 0


def test_graph_approve_claim_checks_pending_decision_atomically(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    desk, job_id, created, origin_id = _create_review_decision(
        app, client, project, key="graph-atomic-pending", engine="graph"
    )
    assert created["ok"] is False
    attention = app.state.db.execute(
        "INSERT INTO attention_items("
        "kind, title, target_json, inline_ok, actions_json, status, source_key"
        ") VALUES ('master_decision', 'Forged', '{}', 0, '[]', 'open', "
        "'forged-graph-atomic')"
    )
    app.state.db.execute(
        "INSERT INTO master_decisions("
        "attention_item_id, owner_user_id, master_session_id, "
        "origin_message_id, requesting_job_id, title, prompt, context, "
        "response_shape_json, request_fingerprint, state"
        ") VALUES (?, 1, ?, ?, ?, 'Forged', 'Choose?', 'Context', ?, 'fp', "
        "'pending')",
        (
            attention.lastrowid,
            desk["session"]["id"],
            origin_id,
            job_id,
            json.dumps(
                {
                    "type": "choice",
                    "choices": [
                        {"id": "a", "label": "A"},
                        {"id": "b", "label": "B"},
                    ],
                }
            ),
        ),
    )
    # Force the outer pre-check to miss so only the in-transaction check can win.
    original = master_decisions.pending_decision_for_job
    calls = {"n": 0}

    def flaky(conn, job_id_arg):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(conn, job_id_arg)

    master_decisions.pending_decision_for_job = flaky
    try:
        approved = client.post(f"/api/graph/jobs/{job_id}/approve")
    finally:
        master_decisions.pending_decision_for_job = original
    assert approved.status_code == 409
    assert approved.json()["detail"]["code"] == "master_decision_pending"
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM master_decisions "
        "WHERE requesting_job_id = ? AND state = 'pending'",
        (job_id,),
    ).fetchone()[0] == 1


def test_linear_approve_claim_checks_pending_decision_atomically(
    tmp_path: Path,
):
    app, client, project = _app_and_client(tmp_path)
    _desk, job_id, created, _origin = _create_review_decision(
        app, client, project, key="linear-atomic-pending"
    )
    assert created["ok"] is True
    original = master_decisions.pending_decision_for_job
    calls = {"n": 0}

    def flaky(conn, job_id_arg):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(conn, job_id_arg)

    master_decisions.pending_decision_for_job = flaky
    try:
        approved = client.post(f"/api/jobs/{job_id}/approve")
    finally:
        master_decisions.pending_decision_for_job = original
    assert approved.status_code == 409
    assert approved.json()["detail"]["code"] == "master_decision_pending"
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"
    assert calls["n"] >= 2


def _repo_review_job(tmp_path: Path, *, key: str):
    repo = tmp_path / f"repo-{key}"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "add", "README.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "readme"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    app = create_app(
        {
            "database_path": str(tmp_path / f"{key}.db"),
            "workspace_root": str(tmp_path / f"{key}-ws"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_master_orchestrator": True,
            "feature_repo_worktrees": True,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    linked = client.post(
        "/api/projects/link",
        json=with_browse_root(client, {"path": str(repo), "slug": key}),
    )
    assert linked.status_code == 201, linked.text
    area_id = linked.json()["code_areas"][0]["id"]
    job = client.post(
        "/api/jobs",
        json={
            "project_slug": key,
            "target_area_id": area_id,
            "input": {"brief": "change code"},
        },
    ).json()
    started = client.post(f"/api/jobs/{job['id']}/start")
    assert started.status_code == 200, started.text
    wt_path = Path(started.json()["worktree"]["worktree_path"])
    (wt_path / "feature.py").write_text("x = 1\n", encoding="utf-8")
    desk = client.get("/api/master/desk").json()
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', current_step_idx = 0, "
        "origin_master_session_id = ?, steps_state = ? WHERE id = ?",
        (
            desk["session"]["id"],
            json.dumps(
                [
                    {
                        "title": "Change code",
                        "status": "done",
                        "output_summary": "Ready to merge.",
                    }
                ]
            ),
            job["id"],
        ),
    )
    origin = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'user', 'Ship it', 'owner')",
        (desk["session"]["id"],),
    )
    master_focus.stamp_message(
        app.state.db,
        message_id=origin.lastrowid,
        focus_epoch_id=None,
    )
    return app, client, desk, job["id"], origin.lastrowid, repo


def test_create_decision_refuses_live_final_approval_intent(tmp_path: Path):
    app, client, desk, job_id, origin_id, _repo = _repo_review_job(
        tmp_path, key="intent-blocks-decision"
    )
    with app.state.db_lock:
        app.state.db.execute("BEGIN IMMEDIATE")
        intent, resumed = master_decisions.claim_final_approval_intent(
            app.state.db,
            job_id=job_id,
            actor_user_id=1,
        )
        app.state.db.execute("COMMIT")
    assert resumed is False
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1, "username": "owner"},
        desk["session"]["id"],
        origin_message_id=origin_id,
    )
    created = broker.execute(
        "create_attention",
        {
            "title": "Blocked by approve",
            "prompt": "Should not land",
            "context": "Approve intent is live",
            "response": {
                "type": "choice",
                "choices": [
                    {"id": "a", "label": "A"},
                    {"id": "b", "label": "B"},
                ],
            },
            "task_id": job_id,
            "idempotency_key": "blocked-by-intent",
        },
    )
    assert created["ok"] is False
    assert created["error"]["code"] == "final_approval_in_flight"
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM master_decisions "
            "WHERE requesting_job_id = ? AND state IN ('pending', 'deferred')",
            (job_id,),
        ).fetchone()[0]
        == 0
    )
    assert int(intent["generation"]) == 1


def test_final_approve_merge_race_blocks_decision_both_orders(tmp_path: Path):
    app, client, desk, job_id, origin_id, repo = _repo_review_job(
        tmp_path, key="merge-race"
    )
    original_merge = worktrees.merge_job_worktree
    barrier = threading.Barrier(2)
    merge_calls = {"n": 0}

    def slow_merge(conn, job, wt):
        merge_calls["n"] += 1
        barrier.wait(timeout=5)
        return original_merge(conn, job, wt)

    worktrees.merge_job_worktree = slow_merge
    results: dict[str, object] = {}

    def approve() -> None:
        results["approve"] = client.post(f"/api/jobs/{job_id}/approve")

    def create() -> None:
        barrier.wait(timeout=5)
        broker = MasterToolBroker(
            app.state.db,
            app,
            {"id": 1, "username": "owner"},
            desk["session"]["id"],
            origin_message_id=origin_id,
        )
        results["create"] = broker.execute(
            "create_attention",
            {
                "title": "During merge",
                "prompt": "Pick a window",
                "context": "Merge is in flight",
                "response": {
                    "type": "choice",
                    "choices": [
                        {"id": "a", "label": "A"},
                        {"id": "b", "label": "B"},
                    ],
                },
                "task_id": job_id,
                "idempotency_key": "during-merge",
            },
        )

    try:
        threads = [threading.Thread(target=approve), threading.Thread(target=create)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    finally:
        worktrees.merge_job_worktree = original_merge

    approve_response = results["approve"]
    create_result = results["create"]
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "done"
    assert isinstance(create_result, dict)
    assert create_result.get("ok") is False
    assert create_result["error"]["code"] == "final_approval_in_flight"
    assert merge_calls["n"] == 1
    assert (repo / "feature.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM master_decisions "
            "WHERE requesting_job_id = ? AND state IN ('pending', 'deferred')",
            (job_id,),
        ).fetchone()[0]
        == 0
    )
    intent_state = app.state.db.execute(
        "SELECT state, generation FROM job_final_approval_intents "
        "WHERE job_id = ? ORDER BY generation DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    assert dict(intent_state) == {"state": "finalized", "generation": 1}


def test_final_approve_decision_first_blocks_merge_claim(tmp_path: Path):
    app, client, desk, job_id, origin_id, _repo = _repo_review_job(
        tmp_path, key="decision-first"
    )
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1, "username": "owner"},
        desk["session"]["id"],
        origin_message_id=origin_id,
    )
    created = broker.execute(
        "create_attention",
        {
            "title": "Before approve",
            "prompt": "Pick a window",
            "context": "Decision lands first",
            "response": {
                "type": "choice",
                "choices": [
                    {"id": "a", "label": "A"},
                    {"id": "b", "label": "B"},
                ],
            },
            "task_id": job_id,
            "idempotency_key": "before-approve",
        },
    )
    assert created["ok"] is True
    original_merge = worktrees.merge_job_worktree
    merge_calls = {"n": 0}

    def guarded_merge(conn, job, wt):
        merge_calls["n"] += 1
        return original_merge(conn, job, wt)

    worktrees.merge_job_worktree = guarded_merge
    try:
        approved = client.post(f"/api/jobs/{job_id}/approve")
    finally:
        worktrees.merge_job_worktree = original_merge
    assert approved.status_code == 409
    assert approved.json()["detail"]["code"] == "master_decision_pending"
    assert merge_calls["n"] == 0
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"
    assert app.state.db.execute(
        "SELECT status FROM job_worktrees WHERE job_id = ?", (job_id,)
    ).fetchone()["status"] == "active"
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM job_final_approval_intents "
            "WHERE job_id = ? AND state = 'live'",
            (job_id,),
        ).fetchone()[0]
        == 0
    )


def test_merge_failure_releases_intent_and_stays_in_review(tmp_path: Path):
    app, client, _desk, job_id, _origin_id, _repo = _repo_review_job(
        tmp_path, key="merge-fail"
    )

    def boom(conn, job, wt):
        raise worktrees.WorktreeError("simulated merge conflict")

    original = worktrees.merge_job_worktree
    worktrees.merge_job_worktree = boom
    try:
        approved = client.post(f"/api/jobs/{job_id}/approve")
    finally:
        worktrees.merge_job_worktree = original
    assert approved.status_code == 409
    assert "merge blocked" in approved.json()["detail"]
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"
    intent = app.state.db.execute(
        "SELECT state, error FROM job_final_approval_intents "
        "WHERE job_id = ? ORDER BY generation DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    assert intent["state"] == "released"
    assert "simulated merge conflict" in (intent["error"] or "")


def test_final_approval_restart_finalizes_merged_live_intent(tmp_path: Path):
    app, client, _desk, job_id, _origin_id, repo = _repo_review_job(
        tmp_path, key="restart-finalize"
    )
    with app.state.db_lock:
        app.state.db.execute("BEGIN IMMEDIATE")
        intent, _resumed = master_decisions.claim_final_approval_intent(
            app.state.db,
            job_id=job_id,
            actor_user_id=1,
        )
        app.state.db.execute("COMMIT")
    job = app.state.db.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    wt = worktrees.job_worktree_row(app.state.db, job_id)
    merged = worktrees.merge_job_worktree(app.state.db, job, wt)
    assert merged["status"] == "merged"
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"

    events = master_decisions.reconcile_final_approval_intents(app)
    assert events
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "done"
    assert app.state.db.execute(
        "SELECT state FROM job_final_approval_intents "
        "WHERE job_id = ? AND generation = ?",
        (job_id, int(intent["generation"])),
    ).fetchone()["state"] == "finalized"
    assert (repo / "feature.py").read_text(encoding="utf-8") == "x = 1\n"

    # Restart again is a no-op: no second merge, no second done transition.
    original = worktrees.merge_job_worktree
    calls = {"n": 0}

    def counted(conn, job_row, wt_row):
        calls["n"] += 1
        return original(conn, job_row, wt_row)

    worktrees.merge_job_worktree = counted
    try:
        again = master_decisions.reconcile_final_approval_intents(app)
    finally:
        worktrees.merge_job_worktree = original
    assert again == []
    assert calls["n"] == 0


def test_final_approval_restart_releases_incomplete_merge(tmp_path: Path):
    app, _client, _desk, job_id, _origin_id, _repo = _repo_review_job(
        tmp_path, key="restart-release"
    )
    with app.state.db_lock:
        app.state.db.execute("BEGIN IMMEDIATE")
        intent, _ = master_decisions.claim_final_approval_intent(
            app.state.db,
            job_id=job_id,
            actor_user_id=1,
        )
        app.state.db.execute("COMMIT")
    app.state.db.execute(
        "UPDATE job_worktrees SET status = 'merging' WHERE job_id = ?",
        (job_id,),
    )
    events = master_decisions.reconcile_final_approval_intents(app)
    assert events == []
    assert app.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "review"
    assert app.state.db.execute(
        "SELECT status FROM job_worktrees WHERE job_id = ?", (job_id,)
    ).fetchone()["status"] == "conflict"
    assert app.state.db.execute(
        "SELECT state FROM job_final_approval_intents "
        "WHERE job_id = ? AND generation = ?",
        (job_id, int(intent["generation"])),
    ).fetchone()["state"] == "released"


def test_generation_mismatch_refuses_stale_finalize(tmp_path: Path):
    app, _client, _desk, job_id, _origin_id, _repo = _repo_review_job(
        tmp_path, key="gen-mismatch"
    )
    with app.state.db_lock:
        app.state.db.execute("BEGIN IMMEDIATE")
        first, _ = master_decisions.claim_final_approval_intent(
            app.state.db,
            job_id=job_id,
            actor_user_id=1,
        )
        app.state.db.execute("COMMIT")
    master_decisions.release_final_approval_intent(
        app.state.db,
        job_id=job_id,
        generation=int(first["generation"]),
        error="owner cancelled",
    )
    with app.state.db_lock:
        app.state.db.execute("BEGIN IMMEDIATE")
        second, _ = master_decisions.claim_final_approval_intent(
            app.state.db,
            job_id=job_id,
            actor_user_id=1,
        )
        app.state.db.execute("COMMIT")
    assert int(second["generation"]) == int(first["generation"]) + 1
    assert not master_decisions.finalize_final_approval_intent(
        app.state.db,
        job_id=job_id,
        generation=int(first["generation"]),
    )
    assert master_decisions.finalize_final_approval_intent(
        app.state.db,
        job_id=job_id,
        generation=int(second["generation"]),
    )


def test_ordinary_no_decision_final_approve_still_works(tmp_path: Path):
    app, client, project = _app_and_client(tmp_path)
    _desk, jobs = _delegate(
        app,
        client,
        project,
        key="ordinary-approve",
        tasks=[{"title": "Ship docs", "brief": "No decision needed"}],
    )
    job_id = jobs[0]["id"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', current_step_idx = 0, "
        "steps_state = ? WHERE id = ?",
        (
            json.dumps(
                [
                    {
                        "title": "Write docs",
                        "status": "done",
                        "output_summary": "Docs ready.",
                    }
                ]
            ),
            job_id,
        ),
    )
    approved = client.post(f"/api/jobs/{job_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM job_final_approval_intents WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        == 0
    )


def test_resume_merged_live_intent_without_second_merge(tmp_path: Path):
    app, client, _desk, job_id, _origin_id, repo = _repo_review_job(
        tmp_path, key="resume-merged"
    )
    with app.state.db_lock:
        app.state.db.execute("BEGIN IMMEDIATE")
        intent, _ = master_decisions.claim_final_approval_intent(
            app.state.db,
            job_id=job_id,
            actor_user_id=1,
        )
        app.state.db.execute("COMMIT")
    job = app.state.db.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    wt = worktrees.job_worktree_row(app.state.db, job_id)
    worktrees.merge_job_worktree(app.state.db, job, wt)

    original = worktrees.merge_job_worktree
    calls = {"n": 0}

    def counted(conn, job_row, wt_row):
        calls["n"] += 1
        return original(conn, job_row, wt_row)

    worktrees.merge_job_worktree = counted
    try:
        approved = client.post(f"/api/jobs/{job_id}/approve")
    finally:
        worktrees.merge_job_worktree = original
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    assert calls["n"] == 0
    assert app.state.db.execute(
        "SELECT state FROM job_final_approval_intents "
        "WHERE job_id = ? AND generation = ?",
        (job_id, int(intent["generation"])),
    ).fetchone()["state"] == "finalized"
    assert (repo / "feature.py").read_text(encoding="utf-8") == "x = 1\n"

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api.master_runtime import (
    master_capacity,
    execute_tool,
    handle_master_response,
)
from proxima_api.run_prompting import RunPrompting
from proxima_api.job_checkpoints import (
    CheckpointError,
    create_checkpoint,
    restore_checkpoint,
)
from proxima_api.db import connect
from proxima_api.main import create_app
from proxima_api import app_settings, turn_restore
from proxima_api import master_focus


def _client(tmp_path: Path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    created = client.post(
        "/api/projects", json={"slug": "master-project", "name": "Master project"}
    )
    assert created.status_code == 201
    return app, client


def test_master_desk_creates_hidden_system_identity(tmp_path: Path, monkeypatch):
    app, client = _client(tmp_path)

    desk = client.get("/api/master/desk")

    assert desk.status_code == 200
    assert desk.json()["session"]["mode"] == "master"
    assert desk.json()["capacity"] == {"running": 0, "max": 3, "free": 3, "queued": 0}
    assert client.get("/api/sessions").json()["sessions"] == []
    assert [
        profile["name"] for profile in client.get("/api/profiles").json()["profiles"]
    ] == ["Default"]
    master_profile = app.state.db.execute(
        "SELECT id, name, system_kind FROM profiles WHERE system_kind = 'master'"
    ).fetchone()
    assert {key: master_profile[key] for key in ("name", "system_kind")} == {
        "name": "Master",
        "system_kind": "master",
    }
    assert (
        client.post(
            "/api/sessions",
            json={"title": "Imposter", "profile_id": master_profile["id"]},
        ).status_code
        == 404
    )
    origin_master_session_id = desk.json()["session"]["id"]
    assert (
        client.patch(
            f"/api/sessions/{origin_master_session_id}", json={"title": "Imposter"}
        ).status_code
        == 409
    )
    assert client.delete(f"/api/sessions/{origin_master_session_id}").status_code == 409
    master_run = client.post(
        "/api/master/messages", json={"content": "List current work"}
    )
    assert master_run.status_code == 409
    assert master_run.json()["detail"]["code"] == "master_runner_not_conforming"
    assert (
        client.put(
            "/api/settings/master", json={"runner_id": "not-a-runner"}
        ).status_code
        == 422
    )
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda runner_id: (runner_id == "codex", ""),
    )
    switched = client.put("/api/settings/master", json={"runner_id": "codex"})
    assert switched.status_code == 200
    assert switched.json()["runner_id"] == "codex"
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) AS c FROM profiles WHERE system_kind='master'"
        ).fetchone()["c"]
        == 1
    )


def test_alpha_route_alias_reads_the_same_master_records(tmp_path: Path):
    app, client = _client(tmp_path)
    master = client.get("/api/master/desk")
    legacy = client.get("/api/alpha/desk")

    assert master.status_code == legacy.status_code == 200
    assert master.json()["session"]["id"] == legacy.json()["session"]["id"]
    assert master.json()["session"]["mode"] == "master"
    assert legacy.json()["session"]["mode"] == "alpha"
    assert master.json()["jobs"] == legacy.json()["jobs"]
    assert "master_run" in master.json()
    assert "alpha_run" in legacy.json()
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM sessions WHERE mode = 'master'"
        ).fetchone()[0]
        == 1
    )


def test_master_message_acceptance_returns_canonical_durable_message(
    tmp_path: Path, monkeypatch
):
    app, client = _client(tmp_path)
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda _runner_id: (True, ""),
    )

    response = client.post(
        "/api/master/messages",
        json={"content": "Keep this turn in durable order"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["message"] == {
        "id": body["message"]["id"],
        "role": "user",
        "content": "Keep this turn in durable order",
        "author": "owner",
        "run_id": body["run_id"],
        "created_at": body["message"]["created_at"],
        "master_target": {
            "focus_mode": "fleet",
            "focus_container_id": None,
            "target_mode": "auto",
            "target_container_id": None,
            "target_area_id": None,
        },
    }
    stored = app.state.db.execute(
        "SELECT id, run_id FROM messages WHERE id = ?",
        (body["message"]["id"],),
    ).fetchone()
    assert dict(stored) == {
        "id": body["message"]["id"],
        "run_id": body["run_id"],
    }
    context = app.state.db.execute(
        "SELECT focus_mode, focus_container_id, target_mode, "
        "target_container_id, target_area_id "
        "FROM master_message_context WHERE message_id = ?",
        (body["message"]["id"],),
    ).fetchone()
    assert dict(context) == body["message"]["master_target"]
    listed = client.get(f"/api/sessions/{body['session_id']}/messages").json()[
        "messages"
    ]
    assert listed[0]["master_target"] == body["message"]["master_target"]
    assert client.get("/api/master/desk").json()["event_cursor"] > 0


def test_generic_run_producers_refuse_the_master_session(tmp_path: Path):
    app, client = _client(tmp_path)
    session_id = client.get("/api/master/desk").json()["session"]["id"]
    assistant = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author) "
        "VALUES (?, 'assistant', 'Master answer', 'Master')",
        (session_id,),
    )
    master_focus.stamp_message(
        app.state.db,
        message_id=assistant.lastrowid,
        focus_epoch_id=None,
    )
    before = {
        "messages": app.state.db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0],
        "runs": app.state.db.execute(
            "SELECT COUNT(*) FROM runs WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0],
    }

    responses = [
        client.post(
            f"/api/sessions/{session_id}/messages",
            json={"role": "user", "content": "Bypass the Master boundary"},
        ),
        client.post(
            f"/api/sessions/{session_id}/runs",
            json={"message": "Bypass the Master boundary"},
        ),
        client.post(
            f"/api/sessions/{session_id}/goal",
            json={"objective": "Bypass the Master boundary"},
        ),
        client.post(
            f"/api/sessions/{session_id}/wiki-note/draft",
            json={},
        ),
        client.post(
            f"/api/sessions/{session_id}/promote-workflow",
            json={},
        ),
        client.post(
            f"/api/messages/{assistant.lastrowid}/reviews",
            json={},
        ),
    ]

    assert [response.status_code for response in responses] == [409] * 6
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        == before["messages"]
    )
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM runs WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        == before["runs"]
    )
    goal = app.state.db.execute(
        "SELECT goal_text, goal_status FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    assert dict(goal) == {"goal_text": None, "goal_status": None}


def test_master_focus_is_versioned_durable_and_pending_until_turn_closes(
    tmp_path: Path, monkeypatch
):
    app, client = _client(tmp_path)
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda _runner_id: (True, ""),
    )
    first = client.post(
        "/api/projects", json={"slug": "focus-one", "name": "Focus one"}
    ).json()
    second = client.post(
        "/api/projects", json={"slug": "focus-two", "name": "Focus two"}
    ).json()
    first_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (first["slug"],)
    ).fetchone()["id"]
    second_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (second["slug"],)
    ).fetchone()["id"]

    desk = client.get("/api/master/desk").json()
    assert desk["focus"] == {
        "current_epoch_id": None,
        "current_container_id": None,
        "pending_container_id": None,
        "pending": False,
        "version": 0,
    }
    changed = client.put(
        "/api/master/focus", json={"container_id": first_id, "version": 0}
    )
    assert changed.status_code == 200
    focus = changed.json()["focus"]
    assert focus["current_container_id"] == first_id
    assert focus["current_epoch_id"] is not None
    assert focus["version"] == 1
    assert (
        client.put(
            "/api/master/focus", json={"container_id": second_id, "version": 0}
        ).status_code
        == 409
    )

    queued = client.post(
        "/api/master/messages", json={"content": "Stay in the first Container"}
    )
    assert queued.status_code == 202
    run_id = queued.json()["run_id"]
    assert (
        app.state.db.execute(
            "SELECT focus_epoch_id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()["focus_epoch_id"]
        == focus["current_epoch_id"]
    )
    pending = client.put(
        "/api/master/focus", json={"container_id": second_id, "version": 1}
    )
    assert pending.status_code == 200
    assert pending.json()["pending"] is True
    assert pending.json()["focus"]["current_container_id"] == first_id
    assert pending.json()["focus"]["pending_container_id"] == second_id
    assert pending.json()["focus"]["pending"] is True
    assert (
        client.post(
            "/api/master/messages", json={"content": "Must not queue"}
        ).status_code
        == 409
    )

    app.state.db.execute("UPDATE runs SET status = 'completed' WHERE id = ?", (run_id,))
    applied = master_focus.apply_pending_if_idle(
        app.state.db, master_session_id=desk["session"]["id"]
    )
    assert applied and applied["current_container_id"] == second_id
    assert applied["pending_container_id"] is None
    assert applied["pending"] is False
    assert applied["version"] == 3

    fleet_turn = client.post(
        "/api/master/messages",
        json={"content": "Finish in Fleet mode"},
    )
    assert fleet_turn.status_code == 202
    fleet_pending = client.put(
        "/api/master/focus",
        json={"container_id": None, "version": 3},
    )
    assert fleet_pending.status_code == 200
    assert fleet_pending.json()["focus"] == {
        "current_epoch_id": applied["current_epoch_id"],
        "current_container_id": second_id,
        "pending_container_id": None,
        "pending": True,
        "version": 4,
    }
    cancelled = client.post(f"/api/runs/{fleet_turn.json()['run_id']}/cancel")
    assert cancelled.status_code == 200
    after_cancel = client.get("/api/master/desk").json()["focus"]
    assert after_cancel == {
        "current_epoch_id": None,
        "current_container_id": None,
        "pending_container_id": None,
        "pending": False,
        "version": 5,
    }
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND content LIKE 'Master Focus changed%'",
            (desk["session"]["id"],),
        ).fetchone()[0]
        == 3
    )


def test_master_prompt_history_never_splices_prior_focus_epoch(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    session_id = desk["session"]["id"]
    first = client.post(
        "/api/projects", json={"slug": "history-one", "name": "History one"}
    ).json()
    second = client.post(
        "/api/projects", json={"slug": "history-two", "name": "History two"}
    ).json()
    first_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (first["slug"],)
    ).fetchone()["id"]
    second_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (second["slug"],)
    ).fetchone()["id"]
    epoch_one = master_focus.change_focus(
        app.state.db,
        master_session_id=session_id,
        container_id=first_id,
        expected_version=0,
    )["current_epoch_id"]
    old = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, 'assistant', 'HOSTILE-A-ONLY')",
        (session_id,),
    )
    master_focus.stamp_message(
        app.state.db, message_id=old.lastrowid, focus_epoch_id=epoch_one
    )
    epoch_two = master_focus.change_focus(
        app.state.db,
        master_session_id=session_id,
        container_id=second_id,
        expected_version=1,
    )["current_epoch_id"]
    current = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, 'user', 'Container B request')",
        (session_id,),
    )
    master_focus.stamp_message(
        app.state.db, message_id=current.lastrowid, focus_epoch_id=epoch_two
    )

    history = RunPrompting._master_history(
        app.state.db,
        session_id,
        current_prompt="Container B request",
        focus_epoch_id=epoch_two,
    )
    assert "HOSTILE-A-ONLY" not in history
    assert f"Container {second_id}" in history


def test_explicit_master_target_is_validated_and_focuses_before_enqueue(
    tmp_path: Path, monkeypatch
):
    app, client = _client(tmp_path)
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda _runner_id: (True, ""),
    )
    target = client.post(
        "/api/projects",
        json={"slug": "explicit-target", "name": "Explicit target"},
    ).json()
    target_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'explicit-target'"
    ).fetchone()["id"]
    target_area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (target_id,),
    ).fetchone()["id"]

    response = client.post(
        "/api/master/messages",
        json={
            "content": "Route this exact work",
            "focus": {"mode": "fleet"},
            "target": {
                "mode": "explicit",
                "container_id": target_id,
                "area_id": target_area_id,
            },
        },
    )

    assert target["slug"] == "explicit-target"
    assert response.status_code == 202
    assert response.json()["message"]["master_target"] == {
        "focus_mode": "container",
        "focus_container_id": target_id,
        "target_mode": "explicit",
        "target_container_id": target_id,
        "target_area_id": target_area_id,
    }
    prompt = app.state.db.execute(
        "SELECT prompt FROM runs WHERE id = ?",
        (response.json()["run_id"],),
    ).fetchone()["prompt"]
    assert prompt == "Route this exact work"
    routing = RunPrompting._master_routing_context(
        app.state.db, response.json()["run_id"]
    )
    assert "explicitly targeted" in routing
    assert f"Area id {target_area_id}" in routing

    wrong_area = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id != ? LIMIT 1",
        (target_id,),
    ).fetchone()["id"]
    rejected = client.post(
        "/api/master/messages",
        json={
            "content": "Must reject mismatched Area",
            "target": {
                "mode": "explicit",
                "container_id": target_id,
                "area_id": wrong_area,
            },
        },
    )
    assert rejected.status_code == 422
    assert "not in the selected Container" in rejected.json()["detail"]

    blocked_delete = client.delete("/api/projects/explicit-target")
    assert blocked_delete.status_code == 409
    assert Path(target["path"]).exists()
    app.state.db.execute(
        "UPDATE runs SET status = 'completed' WHERE id = ?",
        (response.json()["run_id"],),
    )
    epoch_id = response.json()["focus"]["current_epoch_id"]
    deleted = client.delete("/api/projects/explicit-target")
    assert deleted.status_code == 200
    historical_context = app.state.db.execute(
        "SELECT focus_mode, focus_container_id, target_mode, "
        "target_container_id, target_area_id "
        "FROM master_message_context WHERE message_id = ?",
        (response.json()["message"]["id"],),
    ).fetchone()
    assert dict(historical_context) == {
        "focus_mode": "container",
        "focus_container_id": target_id,
        "target_mode": "explicit",
        "target_container_id": target_id,
        "target_area_id": target_area_id,
    }
    listed = client.get(
        f"/api/sessions/{response.json()['session_id']}/messages"
    ).json()["messages"]
    historical_message = next(
        message
        for message in listed
        if message["id"] == response.json()["message"]["id"]
    )
    assert historical_message["message_focus"] == {
        "focus_epoch_id": epoch_id,
        "focus_container_id": target_id,
        "subject_container_id": None,
    }
    assert historical_message["master_target"] == dict(historical_context)
    epoch = app.state.db.execute(
        "SELECT container_id, ended_at FROM master_focus_epochs WHERE id = ?",
        (epoch_id,),
    ).fetchone()
    assert epoch["container_id"] == target_id
    assert epoch["ended_at"] is not None
    assert (
        client.get("/api/master/desk").json()["focus"]["current_container_id"] is None
    )


def test_master_run_messages_are_attributed_at_persistence_boundary(
    tmp_path: Path,
    monkeypatch,
):
    app, client = _client(tmp_path)
    monkeypatch.setattr(
        "proxima_api.routes.master.master_runner_conformance",
        lambda _runner_id: (True, ""),
    )
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'master-project'"
    ).fetchone()["id"]
    desk = client.get("/api/master/desk").json()
    focused = client.put(
        "/api/master/focus",
        json={"container_id": container_id, "version": 0},
    ).json()["focus"]
    turn = client.post(
        "/api/master/messages",
        json={"content": "Keep failures in this Focus"},
    ).json()

    message = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, run_id) "
        "VALUES (?, 'error', 'Run failed safely', ?)",
        (desk["session"]["id"], turn["run_id"]),
    )
    attribution = app.state.db.execute(
        "SELECT focus_epoch_id, focus_container_id "
        "FROM message_focus WHERE message_id = ?",
        (message.lastrowid,),
    ).fetchone()

    assert dict(attribution) == {
        "focus_epoch_id": focused["current_epoch_id"],
        "focus_container_id": container_id,
    }
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Message Focus epoch attribution is immutable",
    ):
        app.state.db.execute(
            "UPDATE message_focus SET focus_epoch_id = NULL WHERE message_id = ?",
            (message.lastrowid,),
        )
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Run Focus epoch attribution is immutable",
    ):
        app.state.db.execute(
            "UPDATE runs SET focus_epoch_id = NULL WHERE id = ?",
            (turn["run_id"],),
        )


def test_master_desk_cursor_never_leads_the_snapshot(tmp_path: Path):
    app, client = _client(tmp_path)
    session_id = client.get("/api/master/desk").json()["session"]["id"]
    app.state.db.execute(
        "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) "
        "VALUES (NULL, ?, NULL, 1, 'note', '{}')",
        (session_id,),
    )
    app.state.db.commit()
    latest = app.state.db.execute(
        "SELECT MAX(id) AS id FROM events WHERE session_id = ?",
        (session_id,),
    ).fetchone()["id"]

    cursor = client.get("/api/master/desk").json()["event_cursor"]

    assert cursor == latest


def test_multi_dispatch_rolls_back_every_job_when_one_task_is_invalid(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]

    result = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "tasks": [
                {
                    "title": "Valid first task",
                    "brief": "Do valid work",
                    "project_slug": project["slug"],
                },
                {"title": "Missing brief", "project_slug": project["slug"]},
            ],
        },
    )

    assert result["ok"] is False
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE origin_master_session_id IS NOT NULL"
        ).fetchone()["c"]
        == 0
    )


def test_master_batch_dispatch_uses_durable_idempotent_dependency_dag(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    args = {
        "idempotency_key": "master-dag-timeout",
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
    }

    first = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        args,
    )
    repeated = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        args,
    )

    assert first["ok"] is repeated["ok"] is True
    assert [job["id"] for job in first["result"]["jobs"]] == [
        job["id"] for job in repeated["result"]["jobs"]
    ]
    assert [job["status"] for job in first["result"]["jobs"]] == [
        "running",
        "queued",
    ]
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM task_delegations").fetchone()[0] == 2
    )
    dependency = app.state.db.execute("SELECT * FROM task_dependencies").fetchone()
    assert dependency["task_id"] == first["result"]["jobs"][1]["id"]
    assert dependency["depends_on_task_id"] == first["result"]["jobs"][0]["id"]
    blocked = app.state.db.execute(
        "SELECT blocked_reason FROM jobs WHERE id = ?",
        (dependency["task_id"],),
    ).fetchone()["blocked_reason"]
    assert "currently running" in blocked
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_duplicate_dispatch_envelopes_reject_the_round_before_mutation(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?", (project["slug"],)
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', ?) "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
                "Delegate once",
            ),
        ).fetchone()
    )
    envelope = json.dumps(
        {
            "name": "delegate_tasks",
            "arguments": {
                "start": False,
                "tasks": [
                    {
                        "title": "One durable Task",
                        "brief": "Create only one",
                        "container_id": container_id,
                        "area_id": area_id,
                        "profile_id": profile_id,
                    }
                ],
            },
        }
    )

    calls = handle_master_response(
        app,
        app.state.db,
        run,
        f"<proxima-tool>{envelope}</proxima-tool>\n"
        f"<proxima-tool>{envelope}</proxima-tool>",
    )

    assert calls[0]["error"]["code"] == "duplicate_tool_call"
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE origin_master_session_id = ?",
            (desk["session"]["id"],),
        ).fetchone()[0]
        == 0
    )


def test_master_in_process_multi_dispatch_is_autonomous_checkpointed_and_scoped_to_three(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    tasks = [
        {
            "title": f"Slice {index}",
            "brief": f"Do independent slice {index}",
            "project_slug": project["slug"],
        }
        for index in range(4)
    ]

    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {"tasks": tasks},
    )

    assert result["ok"] is True
    assert len(result["result"]["jobs"]) == 4
    rows = app.state.db.execute(
        "SELECT id, input, origin_master_session_id FROM jobs ORDER BY id"
    ).fetchall()
    assert {json.loads(row["input"])["execution_policy"] for row in rows} == {
        "autonomous"
    }
    assert {row["origin_master_session_id"] for row in rows} == {desk["session"]["id"]}
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM job_checkpoints").fetchone()[0] == 4
    )
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'master.job.create'"
        ).fetchone()[0]
        == 4
    )
    run_ids = [
        row["id"]
        for row in app.state.db.execute("SELECT id FROM runs ORDER BY id").fetchall()
    ]
    assert all(app.state.worker._auto_approve_on(run_id) for run_id in run_ids)

    claimed = [app.state.worker.claim_run() for _ in range(3)]
    assert all(claimed)
    assert app.state.worker.claim_run() is None
    assert master_capacity(app.state.db, desk["session"]["id"])["running"] == 3


def test_master_capacity_counts_each_queued_worker_run(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {
                    "title": "Parallel plan",
                    "brief": "Run branches",
                    "project_slug": project["slug"],
                }
            ],
        },
    )["result"]["jobs"][0]
    session_id = app.state.db.execute(
        "SELECT session_id FROM jobs WHERE id = ?", (job["id"],)
    ).fetchone()["session_id"]
    first_run = app.state.db.execute(
        "SELECT * FROM runs WHERE session_id = ?", (session_id,)
    ).fetchone()
    for _ in range(2):
        app.state.db.execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt) "
            "VALUES (?, ?, ?, ?, ?, 'queued', 'branch')",
            (
                session_id,
                first_run["project_id"],
                first_run["user_id"],
                first_run["profile_id"],
                first_run["runner_id"],
            ),
        )

    assert master_capacity(app.state.db, desk["session"]["id"])["queued"] == 3


def test_master_starts_saved_graph_plan_through_in_process_engine(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    project_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug='master-project'"
    ).fetchone()["id"]
    workflow_id = app.state.db.execute(
        "INSERT INTO workflows(project_id, name, graph, steps, created_by) VALUES (?, 'Saved plan', ?, '[]', ?)",
        (
            project_id,
            json.dumps(
                {
                    "nodes": [
                        {
                            "id": "one",
                            "name": "One",
                            "instruction": "Do one",
                            "output_kind": "text",
                        }
                    ],
                    "edges": [],
                }
            ),
            owner_id,
        ),
    ).lastrowid

    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "start_plan",
        {"workflow_id": workflow_id, "start": False},
    )

    assert result["ok"] is True
    job = app.state.db.execute(
        "SELECT * FROM jobs WHERE id = ?", (result["result"]["job"]["id"],)
    ).fetchone()
    assert job["engine"] == "graph"
    assert job["origin_master_session_id"] == desk["session"]["id"]
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM job_checkpoints WHERE job_id = ?", (job["id"],)
        ).fetchone()[0]
        == 1
    )
    app.state.db.execute(
        "UPDATE jobs SET status = 'review' WHERE id = ?",
        (job["id"],),
    )
    app.state.db.execute(
        "UPDATE node_states SET status = 'failed' WHERE job_id = ?",
        (job["id"],),
    )
    fleet_job = next(
        item
        for item in client.get("/api/master/desk").json()["jobs"]
        if item["id"] == job["id"]
    )
    assert fleet_job["run_projection"]["status"] == "failed"
    assert fleet_job["desk_status"] == "failed"
    projection = app.state.master_projection.project_task(job["id"])
    assert projection["projection_type"] == "master.task.failed"


def test_checkpoint_restore_never_resets_the_shared_project_checkout(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "owner@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Owner"], check=True)
    (root / "state.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "before"], check=True)
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "tasks": [
                {
                    "title": "Safe restore",
                    "brief": "Work",
                    "project_slug": project["slug"],
                }
            ],
        },
    )["result"]["jobs"][0]
    checkpoint = create_checkpoint(app.state.db, job["id"])
    assert checkpoint["git_refs"][0]["restore_strategy"] == "reference_only"

    (root / "state.txt").write_text("later\n")
    subprocess.run(["git", "-C", str(root), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "later"], check=True)
    later_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    app.state.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))

    restored = restore_checkpoint(app.state.db, checkpoint["id"], confirmed=True)

    assert restored["git_restored"] == []
    assert (
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        == later_head
    )
    assert (root / "state.txt").read_text() == "later\n"


def test_master_repo_checkpoint_captures_and_restores_the_job_worktree(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "owner@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Owner"], check=True)
    (root / "state.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "before"], check=True)
    areas = client.post(f"/api/projects/{project['slug']}/areas/detect").json()
    area_id = areas["code_areas"][0]["id"]

    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {
                    "title": "Restorable repo work",
                    "brief": "Change the repo",
                    "project_slug": project["slug"],
                    "target_area_id": area_id,
                }
            ],
        },
    )["result"]["jobs"][0]
    checkpoint = dict(
        app.state.db.execute(
            "SELECT * FROM job_checkpoints WHERE job_id = ? ORDER BY id LIMIT 1",
            (job["id"],),
        ).fetchone()
    )
    checkpoint["git_refs"] = json.loads(checkpoint["git_refs_json"])
    assert checkpoint["git_refs"][0]["restore_strategy"] == "worktree_reset"
    worktree = Path(checkpoint["git_refs"][0]["worktree_path"])
    (worktree / "state.txt").write_text("after\n")
    subprocess.run(["git", "-C", str(worktree), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "after"], check=True)
    app.state.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))

    restored = restore_checkpoint(app.state.db, checkpoint["id"], confirmed=True)

    assert restored["git_restored"] == [str(worktree)]
    assert (worktree / "state.txt").read_text() == "before\n"


def test_checkpoint_restore_compensates_after_post_reset_failure(
    tmp_path: Path,
    monkeypatch,
):
    from proxima_api import job_checkpoints

    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "owner@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Owner"],
        check=True,
    )
    (root / "state.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "before"], check=True)
    areas = client.post(f"/api/projects/{project['slug']}/areas/detect").json()
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {
                    "title": "Compensated repo work",
                    "brief": "Change the repo",
                    "project_slug": project["slug"],
                    "target_area_id": areas["code_areas"][0]["id"],
                }
            ],
        },
    )["result"]["jobs"][0]
    checkpoint = app.state.db.execute(
        "SELECT id, git_refs_json FROM job_checkpoints WHERE job_id = ?",
        (job["id"],),
    ).fetchone()
    worktree = Path(json.loads(checkpoint["git_refs_json"])[0]["worktree_path"])
    (worktree / "state.txt").write_text("after\n")
    subprocess.run(["git", "-C", str(worktree), "add", "state.txt"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "after"], check=True)
    after_head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed' WHERE id = ?",
        (job["id"],),
    )
    reset = job_checkpoints._reset_git

    def fail_after_reset(plan):
        reset(plan)
        raise CheckpointError("injected post-reset failure")

    monkeypatch.setattr(job_checkpoints, "_reset_git", fail_after_reset)

    with pytest.raises(CheckpointError, match="injected post-reset failure"):
        restore_checkpoint(
            app.state.db,
            checkpoint["id"],
            confirmed=True,
        )

    assert (
        subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        == after_head
    )
    assert (worktree / "state.txt").read_text() == "after\n"
    assert (
        app.state.db.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (job["id"],),
        ).fetchone()["status"]
        == "failed"
    )


def test_checkpoint_restore_callback_failure_prevents_destructive_reset(
    tmp_path: Path,
    monkeypatch,
):
    from proxima_api import job_checkpoints

    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "tasks": [
                {
                    "title": "Validate before reset",
                    "brief": "Keep destructive restore safe",
                    "project_slug": project["slug"],
                }
            ],
        },
    )["result"]["jobs"][0]
    checkpoint = create_checkpoint(app.state.db, job["id"])
    fake_worktree = tmp_path / "fake-worktree"
    fake_worktree.mkdir()
    app.state.db.execute(
        "UPDATE job_checkpoints SET git_refs_json = ? WHERE id = ?",
        (
            json.dumps(
                [
                    {
                        "restore_strategy": "worktree_reset",
                        "worktree_path": str(fake_worktree),
                        "sha": "checkpoint",
                    }
                ]
            ),
            checkpoint["id"],
        ),
    )
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed' WHERE id = ?",
        (job["id"],),
    )
    reset_called = False
    monkeypatch.setattr(
        job_checkpoints,
        "_preflight_git",
        lambda _ref: (fake_worktree, "checkpoint", "current"),
    )

    def reset(_plan):
        nonlocal reset_called
        reset_called = True

    monkeypatch.setattr(job_checkpoints, "_reset_git", reset)

    def reject_recovery(_conn, _recovery):
        raise RuntimeError("recovery projection rejected")

    with pytest.raises(RuntimeError, match="projection rejected"):
        restore_checkpoint(
            app.state.db,
            checkpoint["id"],
            confirmed=True,
            on_restore=reject_recovery,
        )

    assert reset_called is False
    assert (
        app.state.db.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (job["id"],),
        ).fetchone()["status"]
        == "failed"
    )


def test_checkpoint_restore_rereads_concurrent_progress_after_preflight(
    tmp_path: Path,
    monkeypatch,
):
    from proxima_api import job_checkpoints

    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    project = client.get("/api/projects").json()["projects"][0]
    job = execute_tool(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "tasks": [
                {
                    "title": "Concurrent restore",
                    "brief": "Preserve newly started work",
                    "project_slug": project["slug"],
                }
            ],
        },
    )["result"]["jobs"][0]
    checkpoint = create_checkpoint(app.state.db, job["id"])
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed' WHERE id = ?",
        (job["id"],),
    )
    task = app.state.db.execute(
        "SELECT session_id, project_id FROM jobs WHERE id = ?",
        (job["id"],),
    ).fetchone()
    profile = app.state.db.execute(
        "SELECT id, runner_id FROM profiles WHERE is_default = 1"
    ).fetchone()
    racer = connect(tmp_path / "proxima.db")
    raced_run_id: int | None = None

    def race_after_preflight(_checkpoint):
        nonlocal raced_run_id
        racer.execute("BEGIN IMMEDIATE")
        racer.execute(
            "UPDATE jobs SET status = 'running', "
            "started_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job["id"],),
        )
        raced_run_id = racer.execute(
            "INSERT INTO runs("
            "session_id, project_id, user_id, profile_id, runner_id, "
            "status, prompt, kind"
            ") VALUES (?, ?, 1, ?, ?, 'running', 'new progress', 'job')",
            (
                task["session_id"],
                task["project_id"],
                profile["id"],
                profile["runner_id"],
            ),
        ).lastrowid
        racer.execute("COMMIT")
        return []

    monkeypatch.setattr(
        job_checkpoints,
        "_preflight_checkpoint_git",
        race_after_preflight,
    )

    response = client.post(
        f"/api/jobs/{job['id']}/checkpoint/restore",
        json={"checkpoint_id": checkpoint["id"], "confirm": True},
    )
    racer.close()

    assert response.status_code == 409
    assert response.json()["detail"] == ("conflicting jobs are running in this project")
    assert (
        app.state.db.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (job["id"],),
        ).fetchone()["status"]
        == "running"
    )
    assert (
        app.state.db.execute(
            "SELECT status FROM runs WHERE id = ?",
            (raced_run_id,),
        ).fetchone()["status"]
        == "running"
    )
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'job.update' "
            "AND json_extract(payload, '$.mutation') = 'checkpoint_restored' "
            "AND json_extract(payload, '$.job_id') = ?",
            (job["id"],),
        ).fetchone()[0]
        == 0
    )


def test_checkpoint_fifo_keeps_thirty_unpinned(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    result = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {"title": "One", "brief": "Do one", "project_slug": project["slug"]}
            ],
            "start": False,
        },
    )
    job_id = result["result"]["jobs"][0]["id"]

    for _ in range(31):
        create_checkpoint(app.state.db, job_id)

    rows = app.state.db.execute(
        "SELECT id FROM job_checkpoints ORDER BY created_at, id"
    ).fetchall()
    assert len(rows) == 30
    assert rows[0]["id"] == 2


def test_turn_restore_previews_paths_and_restores_pre_turn_content(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    target = root / "notes.txt"
    target.write_text("before")
    session = client.post(
        "/api/sessions", json={"title": "Hands on", "project_slug": project["slug"]}
    ).json()
    before = turn_restore.capture_snapshot(root)
    target.write_text("after")
    message_id = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, 'assistant', 'Changed it')",
        (session["id"],),
    ).lastrowid
    turn_restore.record_journal(
        app.state.db,
        message_id=message_id,
        session_id=session["id"],
        root=root,
        before=before,
    )

    preview = client.get(f"/api/chat/messages/{message_id}/restore-turn")
    restored = client.post(
        f"/api/chat/messages/{message_id}/restore-turn",
        json={"confirm": True},
    )

    assert preview.status_code == 200
    assert preview.json()["paths"] == ["notes.txt"]
    assert restored.status_code == 200
    assert target.read_text() == "before"
    assert (
        client.get(f"/api/chat/messages/{message_id}/restore-turn").status_code == 404
    )


def test_pre_migration_turn_journal_restores_through_active_ops_root(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    root = Path(project["path"])
    target = root / "ops" / "wiki" / "migrated.md"
    target.write_text("after", encoding="utf-8")
    session = client.post(
        "/api/sessions",
        json={"title": "Legacy journal", "project_slug": project["slug"]},
    ).json()
    message_id = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) "
        "VALUES (?, 'assistant', 'Changed it')",
        (session["id"],),
    ).lastrowid
    entries = [
        {
            "path": "wiki/migrated.md",
            "before_hash": None,
            "before_content_b64": base64.b64encode(b"before").decode("ascii"),
            "after_hash": "unused",
        }
    ]
    app.state.db.execute(
        "INSERT INTO turn_file_journals("
        "message_id, session_id, entries_json"
        ") VALUES (?, ?, ?)",
        (message_id, session["id"], json.dumps(entries)),
    )

    restored = client.post(
        f"/api/chat/messages/{message_id}/restore-turn",
        json={"confirm": True},
    )

    assert restored.status_code == 200, restored.text
    assert target.read_text(encoding="utf-8") == "before"
    assert not (root / "wiki").exists()


def test_unattended_supervisor_enforces_turn_budget_and_surfaces_clean_stop(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {"title": "Queued A", "brief": "Do A", "project_slug": project["slug"]},
                {"title": "Queued B", "brief": "Do B", "project_slug": project["slug"]},
            ],
            "start": False,
        },
    )
    app_settings.set_master_settings(
        app.state.worker_db, unattended=True, budget_turns=1
    )

    first = app.state.master_supervisor.tick()
    second = app.state.master_supervisor.tick()

    assert len(first["started"]) == 1
    assert second["stopped"] == "turn budget exhausted"
    assert app_settings.get_master_settings(app.state.worker_db)["unattended"] is False
    attention = client.get("/api/attention").json()["items"]
    assert any(item["kind"] == "master_budget" for item in attention)


def test_script_trust_attention_shows_hash_and_uses_in_process_approval(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    project_root = Path(project["path"]) / "ops"
    (project_root / "scripts").mkdir(exist_ok=True)
    script_bytes = b"print('ok')\n"
    (project_root / "scripts" / "hello.py").write_bytes(script_bytes)
    job = client.post(
        "/api/graph/jobs",
        json={
            "title": "Script plan",
            "project_slug": project["slug"],
            "graph": {
                "nodes": [
                    {
                        "id": "script",
                        "name": "Script",
                        "type": "script",
                        "command": "hello.py",
                        "output_kind": "text",
                    }
                ],
                "edges": [],
            },
        },
    ).json()
    import hashlib

    digest = hashlib.sha256(script_bytes).hexdigest()
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))
    app.state.db.execute(
        "UPDATE node_states SET status='failed', error=? WHERE job_id=? AND node_id='script'",
        (f"script_approval_required: scripts/hello.py (sha256 {digest})", job["id"]),
    )

    attention = client.get("/api/attention").json()["items"]
    item = next(item for item in attention if item["kind"] == "script_trust")
    assert digest in item["title"]
    assert item["inline_ok"] is True
    assert item["run_projection"]["status"] == "failed"
    assert item["created_at"].endswith("Z")
    approved = client.post(
        f"/api/attention/{item['id']}/act", json={"action": "approve"}
    )
    assert approved.status_code == 200
    assert (
        app.state.db.execute(
            "SELECT content_hash FROM script_trust WHERE project_id = (SELECT id FROM projects WHERE slug=?)",
            (project["slug"],),
        ).fetchone()["content_hash"]
        == digest
    )


def test_permission_attention_closes_when_choice_is_delivered(tmp_path: Path):
    app, client = _client(tmp_path)
    session = client.post("/api/sessions", json={"title": "Permission"}).json()
    user = app.state.db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    profile = app.state.db.execute(
        "SELECT * FROM profiles WHERE is_default = 1"
    ).fetchone()
    run_id = app.state.db.execute(
        "INSERT INTO runs(session_id, user_id, profile_id, runner_id, status, prompt) "
        "VALUES (?, ?, ?, ?, 'running', 'test')",
        (session["id"], user["id"], profile["id"], profile["runner_id"]),
    ).lastrowid
    app.state.db.execute(
        "INSERT INTO attention_items(kind, title, target_json, inline_ok, actions_json, source_key) "
        "VALUES ('permission_job', 'Allow write', '{}', 1, '[\"approve\"]', ?)",
        (f"permission:{run_id}:request-1",),
    )

    class Proc:
        def resolve_permission(self, request_id, option_id):
            return request_id == "request-1" and option_id == "allow"

    app.state.worker.active_runs[run_id] = (Proc(), "session")
    assert app.state.worker.resolve_permission(run_id, "request-1", "allow") is True
    assert (
        app.state.db.execute(
            "SELECT status FROM attention_items WHERE source_key = ?",
            (f"permission:{run_id}:request-1",),
        ).fetchone()["status"]
        == "resolved"
    )


def test_disallowed_master_tool_returns_structured_error(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()["id"]

    result = execute_tool(
        app.state.db, app, {"id": owner_id}, desk["session"]["id"], "wipe_database", {}
    )

    assert result == {
        "ok": False,
        "tool": "wipe_database",
        "error": {
            "code": "tool_not_allowed",
            "message": "Master tool 'wipe_database' is not allowed",
        },
    }

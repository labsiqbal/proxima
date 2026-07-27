from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import app_settings
from proxima_api.main import create_app
from proxima_api.master_runtime import (
    MASTER_MAX_TURN_OUTPUT_BYTES,
    handle_master_response,
)
from proxima_api.master_tool_broker import MasterToolBroker
from proxima_api.runner_specs import RUNNER_SPECS, RunnerSpec
from proxima_api.run_prompting import MASTER_HISTORY_BYTES, RunPrompting


def _client(tmp_path: Path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_master_orchestrator": True,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return app, client


def test_fresh_master_thread_receives_bounded_durable_history():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE messages("
        "id INTEGER PRIMARY KEY, session_id INTEGER, role TEXT, content TEXT)"
    )
    db.executemany(
        "INSERT INTO messages(session_id, role, content) VALUES (1, ?, ?)",
        [
            ("user", "first request"),
            ("assistant", "first response"),
            ("system", "Master tool result: safe product record"),
            ("user", "current request"),
        ],
    )

    history = RunPrompting._master_history(
        db, 1, current_prompt="current request"
    )
    decoded = json.loads(history)

    assert decoded == [
        {"role": "user", "content": "first request"},
        {"role": "assistant", "content": "first response"},
        {
            "role": "system",
            "content": "Master tool result: safe product record",
        },
    ]
    assert len(history.encode()) <= MASTER_HISTORY_BYTES


def test_nonconforming_runner_is_rejected_before_master_turn_starts(tmp_path: Path):
    app, client = _client(tmp_path)
    app_settings.set_setting(app.state.db, "master.runner_id", "claude-code")

    before_messages = app.state.db.execute(
        "SELECT COUNT(*) FROM messages"
    ).fetchone()[0]
    before_runs = app.state.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    response = client.post(
        "/api/master/messages",
        json={"content": "Read the repository and tell me what you find"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "master_runner_not_conforming",
        "message": (
            "Claude Code cannot run Master because its adapter does not prove "
            "the chat-only boundary"
        ),
    }
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        == before_messages
    )
    assert app.state.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before_runs


def test_feature_off_startup_does_not_provision_master_runner_home(tmp_path: Path):
    profiles_root = tmp_path / "managed-profiles"
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "hermes_profiles_root": str(profiles_root),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_master_orchestrator": False,
        }
    )
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM profiles WHERE system_kind = 'master'"
        ).fetchone()[0]
        == 0
    )
    assert not any(
        entry.name.startswith("master-system")
        for entry in profiles_root.rglob("*")
    )


def test_feature_off_first_authenticated_route_stays_master_inert(tmp_path: Path):
    profiles_root = tmp_path / "managed-profiles"
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "hermes_profiles_root": str(profiles_root),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
            "feature_master_orchestrator": False,
        }
    )
    client = TestClient(app)

    assert client.get("/api/profiles").status_code == 200
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM profiles WHERE system_kind = 'master'"
        ).fetchone()[0]
        == 0
    )
    assert not any(
        entry.name.startswith("master-system")
        for entry in profiles_root.rglob("*")
    )


def test_master_runner_switch_persists_explicit_empty_capabilities(
    tmp_path: Path, monkeypatch
):
    for runner_id in ("master-fixture-a", "master-fixture-b"):
        monkeypatch.setitem(
            RUNNER_SPECS,
            runner_id,
            RunnerSpec(
                id=runner_id,
                spawn_argv=["/usr/bin/true"],
                home_env="MASTER_FIXTURE_HOME",
                binary="/usr/bin/true",
                display_name=runner_id,
                master_chat_only=True,
            ),
        )
    app, client = _client(tmp_path)

    first = client.put(
        "/api/settings/master", json={"runner_id": "master-fixture-a"}
    )
    switched = client.put(
        "/api/settings/master", json={"runner_id": "master-fixture-b"}
    )

    assert first.status_code == switched.status_code == 200
    profile = app.state.db.execute(
        "SELECT runner_id, capabilities FROM profiles WHERE system_kind = 'master'"
    ).fetchone()
    assert profile["runner_id"] == "master-fixture-b"
    assert profile["capabilities"] == '{"skills":[],"mcp":[]}'


def test_malformed_nested_tool_envelope_is_a_visible_stable_error(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', 'test') "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
            ),
        ).fetchone()
    )

    results = handle_master_response(
        app,
        app.state.db,
        run,
        (
            '<proxima-tool>{"name":"list_containers","arguments":'
            '<proxima-tool>{}</proxima-tool>}</proxima-tool>'
        ),
    )

    assert results == [
        {
            "ok": False,
            "tool": None,
            "error": {
                "code": "malformed_tool_call",
                "message": "nested Master tool envelope",
            },
        }
    ]
    message = app.state.db.execute(
        "SELECT content FROM messages WHERE run_id = ? ORDER BY id DESC LIMIT 1",
        (run["id"],),
    ).fetchone()
    assert "malformed_tool_call" in message["content"]


@pytest.mark.parametrize(
    "answer",
    [
        "<proxima-too",
        "</proxima-too",
        "<",
    ],
)
def test_partial_tool_markers_are_visible_errors(tmp_path: Path, answer: str):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', 'partial') "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
            ),
        ).fetchone()
    )

    results = handle_master_response(app, app.state.db, run, answer)

    assert results[0]["error"]["code"] == "malformed_tool_call"


@pytest.mark.parametrize(
    "invalid",
    [
        "<proxima-tool>{",
        (
            '<proxima-tool>{"name":"not_allowed",'
            '"arguments":{}}</proxima-tool>'
        ),
    ],
)
def test_invalid_envelope_rejects_valid_mutation_in_same_round(
    tmp_path: Path,
    invalid: str,
):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', 'mixed') "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
            ),
        ).fetchone()
    )
    valid = (
        '<proxima-tool>{"name":"create_attention","arguments":'
        '{"title":"Must not exist","message":"No partial action"}}'
        "</proxima-tool>"
    )

    results = handle_master_response(
        app,
        app.state.db,
        run,
        valid + invalid,
    )

    assert results[0]["ok"] is False
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM attention_items "
        "WHERE title = 'Must not exist'"
    ).fetchone()[0] == 0


def test_master_tool_broker_validates_schema_and_never_returns_paths(tmp_path: Path):
    app, client = _client(tmp_path)
    client.post(
        "/api/projects",
        json={"slug": "broker-container", "name": "Broker container"},
    )
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'broker-container'"
    ).fetchone()["id"]
    desk = client.get("/api/master/desk").json()
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
    )

    listed = broker.execute("list_containers", {})
    detailed = broker.execute(
        "get_container", {"container_id": container_id}
    )
    rejected = broker.execute(
        "get_container",
        {"container_id": container_id, "path": "/etc/passwd"},
    )
    unavailable = broker.execute(
        "query_context",
        {"container_id": container_id, "query": "What do we know?"},
    )

    assert listed["ok"] is detailed["ok"] is unavailable["ok"] is True
    assert "path" not in json.dumps(listed).lower()
    assert "path" not in json.dumps(detailed).lower()
    assert detailed["result"]["container"]["target_areas"]
    assert rejected["error"]["code"] == "invalid_tool_arguments"
    assert unavailable["result"]["code"] == "feature_unavailable"

    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    unsafe_input = broker.execute(
        "delegate_tasks",
        {
            "idempotency_key": "unsafe-path",
            "tasks": [
                {
                    "title": "Unsafe",
                    "brief": "Read /etc/passwd",
                    "container_id": container_id,
                    "area_id": area_id,
                    "profile_id": profile_id,
                }
            ],
        },
    )
    assert unsafe_input["error"]["code"] == "unsafe_tool_text"
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

    app.state.db.execute(
        "UPDATE projects SET name = '/protected/container/root' WHERE id = ?",
        (container_id,),
    )
    unsafe_output = broker.execute("list_containers", {})
    assert unsafe_output["error"]["code"] == "unsafe_tool_result"
    assert "/protected/container/root" not in json.dumps(unsafe_output)


def test_broker_does_not_list_recipe_bound_to_another_owner(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    other_user_id = app.state.db.execute(
        "INSERT INTO users(username, os_user) VALUES ('other', 'other')"
    ).lastrowid
    other_container_id = app.state.db.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('other-container', 'Other', ?, ?)",
        (str(tmp_path / "other"), other_user_id),
    ).lastrowid
    app.state.db.execute(
        "INSERT INTO workflows(project_id, name, created_by) "
        "VALUES (?, 'Cross-owner recipe', 1)",
        (other_container_id,),
    )
    global_recipe_id = app.state.db.execute(
        "INSERT INTO workflows(project_id, name, created_by) "
        "VALUES (NULL, 'Owner global recipe', 1)"
    ).lastrowid
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
    )

    result = broker.execute("list_recipes", {})

    assert result["ok"] is True
    assert [recipe["id"] for recipe in result["result"]["recipes"]] == [
        global_recipe_id
    ]


def test_duplicate_delegate_envelope_rejects_before_atomic_task_dag(tmp_path: Path):
    app, client = _client(tmp_path)
    client.post(
        "/api/projects",
        json={"slug": "delegation-container", "name": "Delegation container"},
    )
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'delegation-container'"
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    desk = client.get("/api/master/desk").json()
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', 'delegate') "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
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
                        "key": "research",
                        "title": "Research",
                        "brief": "Gather evidence",
                        "container_id": container_id,
                        "area_id": area_id,
                        "profile_id": profile_id,
                    },
                    {
                        "key": "report",
                        "title": "Report",
                        "brief": "Write the report",
                        "container_id": container_id,
                        "area_id": area_id,
                        "profile_id": profile_id,
                        "depends_on": ["research"],
                    },
                ],
            },
        }
    )

    results = handle_master_response(
        app,
        app.state.db,
        run,
        (
            f"<proxima-tool>{envelope}</proxima-tool>"
            f"<proxima-tool>{envelope}</proxima-tool>"
        ),
    )

    assert results[0]["error"]["code"] == "duplicate_tool_call"
    assert app.state.db.execute("SELECT COUNT(*) FROM task_delegations").fetchone()[0] == 0
    assert app.state.db.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0] == 0
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_replayed_delegate_envelope_across_tool_rounds_is_rejected(tmp_path: Path):
    app, client = _client(tmp_path)
    client.post(
        "/api/projects",
        json={"slug": "replay-container", "name": "Replay container"},
    )
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'replay-container'"
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    desk = client.get("/api/master/desk").json()
    first_run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', 'delegate') "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
            ),
        ).fetchone()
    )
    origin_message_id = int(
        app.state.db.execute(
            "INSERT INTO messages(session_id, role, content, author, run_id) "
            "VALUES (?, 'user', 'Delegate once', 'owner', ?)",
            (desk["session"]["id"], first_run["id"]),
        ).lastrowid
    )
    envelope = json.dumps(
        {
            "name": "delegate_tasks",
            "arguments": {
                "start": False,
                "tasks": [
                    {
                        "title": "Only once",
                        "brief": "Create one Task",
                        "container_id": container_id,
                        "area_id": area_id,
                        "profile_id": profile_id,
                    }
                ],
            },
        }
    )
    first = handle_master_response(
        app,
        app.state.db,
        first_run,
        f"<proxima-tool>{envelope}</proxima-tool>",
    )
    continuation = dict(
        app.state.db.execute(
            "SELECT * FROM runs WHERE continued_from_run_id = ? ORDER BY id DESC LIMIT 1",
            (first_run["id"],),
        ).fetchone()
    )

    replay = handle_master_response(
        app,
        app.state.db,
        continuation,
        f"<proxima-tool>{envelope}</proxima-tool>",
    )

    assert first[0]["ok"] is True
    assert replay[0]["error"]["code"] == "duplicate_tool_call"
    assert app.state.db.execute("SELECT COUNT(*) FROM task_delegations").fetchone()[0] == 1
    delegation = app.state.db.execute(
        "SELECT origin_message_id, routing_mode, routing_reason "
        "FROM task_delegations"
    ).fetchone()
    assert delegation["origin_message_id"] == origin_message_id
    assert delegation["routing_mode"] == "auto"
    assert delegation["routing_reason"]


def test_delegate_start_intent_survives_failure_between_commit_and_start(
    tmp_path: Path, monkeypatch
):
    app, client = _client(tmp_path)
    client.post(
        "/api/projects",
        json={"slug": "restart-container", "name": "Restart container"},
    )
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'restart-container'"
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    desk = client.get("/api/master/desk").json()
    broker = MasterToolBroker(
        app.state.db, app, {"id": 1}, desk["session"]["id"]
    )
    monkeypatch.setattr(
        broker,
        "_start_task_ids",
        lambda _task_ids: (_ for _ in ()).throw(RuntimeError("exit")),
    )

    result = broker.execute(
        "delegate_tasks",
        {
            "idempotency_key": "restart-gap",
            "start": True,
            "tasks": [
                {
                    "title": "Resume me",
                    "brief": "Start after recovery",
                    "container_id": container_id,
                    "area_id": area_id,
                    "profile_id": profile_id,
                }
            ],
        },
    )

    assert result["error"]["code"] == "tool_failed"
    durable = app.state.db.execute(
        "SELECT start_requested, start_state FROM task_delegations"
    ).fetchone()
    assert dict(durable) == {
        "start_requested": 1,
        "start_state": "pending",
    }


def test_master_permissions_are_deny_only_but_task_policy_is_preserved(tmp_path: Path):
    app, client = _client(tmp_path)
    client.post(
        "/api/projects",
        json={"slug": "policy-container", "name": "Policy container"},
    )
    container_id = app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'policy-container'"
    ).fetchone()["id"]
    area_id = app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["id"]
    profile_id = app.state.db.execute(
        "SELECT id FROM profiles WHERE is_default = 1"
    ).fetchone()["id"]
    desk = client.get("/api/master/desk").json()
    master_run_id = app.state.db.execute(
        "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
        "status, prompt) VALUES (?, 1, ?, ?, 'master', 'queued', 'chat')",
        (
            desk["session"]["id"],
            desk["session"]["profile_id"],
            desk["session"]["runner_id"],
        ),
    ).lastrowid
    broker = MasterToolBroker(
        app.state.db, app, {"id": 1}, desk["session"]["id"]
    )
    guarded = broker.execute(
        "delegate_tasks",
        {
            "idempotency_key": "guarded",
            "start": True,
            "tasks": [
                {
                    "title": "Guarded",
                    "brief": "Ask before privileged actions",
                    "container_id": container_id,
                    "area_id": area_id,
                    "profile_id": profile_id,
                    "execution_policy": "guarded",
                }
            ],
        },
    )
    autonomous = broker.execute(
        "delegate_tasks",
        {
            "idempotency_key": "autonomous",
            "start": True,
            "tasks": [
                {
                    "title": "Autonomous",
                    "brief": "Run autonomously",
                    "container_id": container_id,
                    "area_id": area_id,
                    "profile_id": profile_id,
                    "execution_policy": "autonomous",
                }
            ],
        },
    )
    run_ids = {
        row["title"]: row["run_id"]
        for row in app.state.db.execute(
            "SELECT j.title, r.id AS run_id FROM jobs j "
            "JOIN sessions s ON s.job_id = j.id "
            "JOIN runs r ON r.session_id = s.id"
        ).fetchall()
    }

    assert guarded["ok"] is autonomous["ok"] is True
    assert app.state.worker._auto_approve_on(master_run_id) is False
    assert app.state.worker._auto_approve_on(run_ids["Guarded"]) is False
    assert app.state.worker._auto_approve_on(run_ids["Autonomous"]) is True


def test_tool_round_cap_fails_visibly_without_executing_action(tmp_path: Path):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()
    run = dict(
        app.state.db.execute(
            "INSERT INTO runs(session_id, user_id, profile_id, runner_id, kind, "
            "status, prompt) VALUES (?, 1, ?, ?, 'master_tool_6', 'running', 'cap') "
            "RETURNING *",
            (
                desk["session"]["id"],
                desk["session"]["profile_id"],
                desk["session"]["runner_id"],
            ),
        ).fetchone()
    )
    envelope = json.dumps(
        {
            "name": "create_attention",
            "arguments": {"title": "Hidden action", "message": "Must not happen"},
        }
    )

    results = handle_master_response(
        app,
        app.state.db,
        run,
        f"<proxima-tool>{envelope}</proxima-tool>",
    )

    assert results[0]["error"]["code"] == "tool_round_limit"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM attention_items WHERE title = 'Hidden action'"
    ).fetchone()[0] == 0
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE continued_from_run_id = ?", (run["id"],)
    ).fetchone()[0] == 0


def test_request_and_call_caps_reject_the_whole_round_before_actions(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    desk = client.get("/api/master/desk").json()

    def run_for(answer: str, prompt: str):
        run = dict(
            app.state.db.execute(
                "INSERT INTO runs(session_id, user_id, profile_id, runner_id, "
                "kind, status, prompt) VALUES (?, 1, ?, ?, 'master', 'running', ?) "
                "RETURNING *",
                (
                    desk["session"]["id"],
                    desk["session"]["profile_id"],
                    desk["session"]["runner_id"],
                    prompt,
                ),
            ).fetchone()
        )
        return handle_master_response(app, app.state.db, run, answer)

    oversized = run_for(
        (
            '<proxima-tool>{"name":"create_attention","arguments":'
            '{"title":"Oversized","message":"'
            + ("x" * 20_000)
            + '"}}</proxima-tool>'
        ),
        "oversized",
    )
    envelopes = "".join(
        (
            '<proxima-tool>{"name":"create_attention","arguments":'
            f'{{"title":"Call {index}","message":"no"}}}}</proxima-tool>'
        )
        for index in range(9)
    )
    too_many = run_for(envelopes, "too many")

    assert oversized[0]["error"]["code"] == "tool_request_too_large"
    assert too_many[0]["error"]["code"] == "too_many_tool_calls"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM attention_items WHERE kind = 'master_decision'"
    ).fetchone()[0] == 0
def test_tool_result_cap_returns_only_a_deterministic_error(tmp_path: Path):
    app, client = _client(tmp_path)
    client.post(
        "/api/projects",
        json={"slug": "large-result", "name": "Large result container"},
    )
    desk = client.get("/api/master/desk").json()
    broker = MasterToolBroker(
        app.state.db,
        app,
        {"id": 1},
        desk["session"]["id"],
        result_bytes=80,
    )

    result = broker.execute("list_containers", {})

    assert result == {
        "ok": False,
        "tool": "list_containers",
        "error": {
            "code": "tool_result_too_large",
            "message": "list_containers result exceeds the 80-byte limit",
        },
    }
    assert "result" not in result


def test_aggregate_master_output_cap_fails_without_saving_truncated_action(
    tmp_path: Path, monkeypatch
):
    runner_id = "master-output-fixture"
    monkeypatch.setitem(
        RUNNER_SPECS,
        runner_id,
        RunnerSpec(
            id=runner_id,
            spawn_argv=["/usr/bin/true"],
            home_env="MASTER_FIXTURE_HOME",
            binary="/usr/bin/true",
            display_name="Master output fixture",
            master_chat_only=True,
        ),
    )
    app, client = _client(tmp_path)
    assert client.put(
        "/api/settings/master", json={"runner_id": runner_id}
    ).status_code == 200

    class OversizedProcess:
        async def new_session(self, _cwd):
            return "oversized"

        async def load_session(self, _session_id, _cwd):
            return None

        async def prompt(
            self,
            session_id,
            _text,
            on_update,
            on_permission=None,
            timeout=600,
            images=None,
        ):
            on_update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": (
                            "x" * MASTER_MAX_TURN_OUTPUT_BYTES
                            + '<proxima-tool>{"name":"create_attention"'
                        ),
                    },
                }
            )
            return "end_turn"

        def cancel(self, _session_id):
            return None

    class OversizedManager:
        async def get(self, _spec=None, _home=None, _cwd=None):
            return OversizedProcess()

        async def recycle(self, _spec=None, _home=None, _cwd=None):
            return None

    app.state.acp_manager = OversizedManager()
    response = client.post(
        "/api/master/messages", json={"content": "Produce too much output"}
    )
    assert response.status_code == 202
    run = app.state.worker.claim_run()
    assert run is not None

    asyncio.run(app.state.worker.execute_run(run))

    saved = client.get(f"/api/runs/{run['id']}").json()
    assert saved["status"] == "failed"
    assert "master_output_too_large" in saved["error"]
    desk = client.get("/api/master/desk").json()
    messages = client.get(
        f"/api/sessions/{desk['session']['id']}/messages"
    ).json()["messages"]
    assert not any(
        "create_attention" in message["content"]
        for message in messages
        if message["role"] != "user"
    )

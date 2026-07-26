from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.task_delegation import (
    DependencyRequest,
    TaskDelegationError,
    TaskDelegationRequest,
)


def _app(tmp_path: Path, *, database_path: Path | None = None):
    return create_app(
        {
            "database_path": str(database_path or tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "feature_repo_worktrees": True,
        }
    )


def _client(app) -> TestClient:
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _container(client: TestClient, app, slug: str) -> tuple[dict, dict]:
    response = client.post(
        "/api/projects", json={"slug": slug, "name": slug.title()}
    )
    assert response.status_code == 201
    container = dict(
        app.state.db.execute(
            "SELECT * FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
    )
    area = app.state.db.execute(
        "SELECT * FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container["id"],),
    ).fetchone()
    assert area is not None
    return container, dict(area)


def _user_profile(app) -> tuple[dict, dict]:
    user = dict(app.state.db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone())
    profile = dict(
        app.state.db.execute(
            "SELECT * FROM profiles WHERE user_id = ? AND is_default = 1",
            (user["id"],),
        ).fetchone()
    )
    return user, profile


def _request(
    *,
    container_id: int,
    area_id: int,
    profile_id: int,
    key: str,
    client_key: str = "task",
    dependencies: tuple[DependencyRequest, ...] = (),
    policy: str = "guarded",
) -> TaskDelegationRequest:
    return TaskDelegationRequest(
        title=f"Task {client_key}",
        brief=f"Do {client_key}",
        container_id=container_id,
        area_id=area_id,
        profile_id=profile_id,
        execution_policy=policy,
        idempotency_key=key,
        client_key=client_key,
        dependencies=dependencies,
        routing_mode="explicit",
        routing_reason="Integration test target",
    )


def test_work_route_idempotency_replays_one_task_and_delegation(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "idempotent")
    payload = {
        "project_id": container["id"],
        "target_area_id": area["id"],
        "title": "One durable Task",
        "input": {"brief": "Create it once"},
    }

    first = client.post(
        "/api/jobs", json=payload, headers={"Idempotency-Key": "route-timeout-1"}
    )
    repeated = client.post(
        "/api/jobs", json=payload, headers={"Idempotency-Key": "route-timeout-1"}
    )

    assert first.status_code == repeated.status_code == 200
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["input"] == payload["input"]
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM task_delegations").fetchone()[0]
        == 1
    )
    assert repeated.json()["delegation"]["idempotency_key"] == "route-timeout-1"


def test_idempotency_key_rejects_changed_request(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "conflict")
    headers = {"Idempotency-Key": "same-key"}
    first = client.post(
        "/api/jobs",
        json={
            "project_id": container["id"],
            "target_area_id": area["id"],
            "input": {"brief": "First"},
        },
        headers=headers,
    )
    conflict = client.post(
        "/api/jobs",
        json={
            "project_id": container["id"],
            "target_area_id": area["id"],
            "input": {"brief": "Different"},
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_committed_start_intent_resumes_once_after_restart(tmp_path: Path):
    database_path = tmp_path / "durable.db"
    app = _app(tmp_path, database_path=database_path)
    client = _client(app)
    container, area = _container(client, app, "restart")
    user, profile = _user_profile(app)
    created = app.state.task_delegation.create_and_start(
        user,
        _request(
            container_id=container["id"],
            area_id=area["id"],
            profile_id=profile["id"],
            key="crash-before-start",
        ),
        start=False,
        connection=app.state.db,
    )
    job_id = created.job["id"]
    app.state.db.execute(
        "UPDATE task_delegations SET start_requested = 1, start_state = 'pending' "
        "WHERE job_id = ?",
        (job_id,),
    )

    restarted = _app(tmp_path, database_path=database_path)

    job = restarted.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "running"
    assert (
        restarted.state.db.execute(
            "SELECT COUNT(*) FROM runs WHERE session_id = "
            "(SELECT session_id FROM jobs WHERE id = ?)",
            (job_id,),
        ).fetchone()[0]
        == 1
    )
    restarted.state.task_delegation.resume_committed(
        connection=restarted.state.db
    )
    assert (
        restarted.state.db.execute(
            "SELECT COUNT(*) FROM runs WHERE session_id = "
            "(SELECT session_id FROM jobs WHERE id = ?)",
            (job_id,),
        ).fetchone()[0]
        == 1
    )
    assert (
        restarted.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    )
    assert (
        restarted.state.db.execute(
            "SELECT COUNT(*) FROM task_delegations"
        ).fetchone()[0]
        == 1
    )


def test_restart_reconciles_a_committed_run_with_a_starting_audit(tmp_path: Path):
    database_path = tmp_path / "starting-audit.db"
    app = _app(tmp_path, database_path=database_path)
    client = _client(app)
    container, area = _container(client, app, "starting-audit")
    user, profile = _user_profile(app)
    result = app.state.task_delegation.create_and_start(
        user,
        _request(
            container_id=container["id"],
            area_id=area["id"],
            profile_id=profile["id"],
            key="starting-audit-task",
        ),
        connection=app.state.db,
    )
    app.state.db.execute(
        "UPDATE task_delegations SET start_state = 'starting', started_at = NULL "
        "WHERE job_id = ?",
        (result.job["id"],),
    )

    restarted = _app(tmp_path, database_path=database_path)

    audit = restarted.state.db.execute(
        "SELECT start_state, started_at FROM task_delegations WHERE job_id = ?",
        (result.job["id"],),
    ).fetchone()
    assert audit["start_state"] == "started"
    assert audit["started_at"] is not None
    assert restarted.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE session_id = "
        "(SELECT session_id FROM jobs WHERE id = ?)",
        (result.job["id"],),
    ).fetchone()[0] == 1


def test_batch_cycle_rolls_back_without_partial_rows(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "cycle")
    user, profile = _user_profile(app)
    before_sessions = app.state.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    with pytest.raises(TaskDelegationError, match="cycle") as error:
        app.state.task_delegation.create_batch(
            user,
            [
                _request(
                    container_id=container["id"],
                    area_id=area["id"],
                    profile_id=profile["id"],
                    key="cycle-a",
                    client_key="a",
                    dependencies=(DependencyRequest("b"),),
                ),
                _request(
                    container_id=container["id"],
                    area_id=area["id"],
                    profile_id=profile["id"],
                    key="cycle-b",
                    client_key="b",
                    dependencies=(DependencyRequest("a"),),
                ),
            ],
            start=False,
            connection=app.state.db,
        )

    assert error.value.code == "dependency_cycle"
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM task_delegations").fetchone()[0]
        == 0
    )
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0]
        == 0
    )
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        == before_sessions
    )


@pytest.mark.parametrize(
    ("dependencies", "code"),
    [
        ((DependencyRequest("self"),), "self_dependency"),
        (
            (DependencyRequest("prerequisite"), DependencyRequest("prerequisite")),
            "duplicate_dependency",
        ),
    ],
)
def test_batch_rejects_self_and_duplicate_dependencies_atomically(
    tmp_path: Path,
    dependencies: tuple[DependencyRequest, ...],
    code: str,
):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(
        client, app, f"invalid-{code.replace('_', '-')}"
    )
    user, profile = _user_profile(app)
    requests = [
        _request(
            container_id=container["id"],
            area_id=area["id"],
            profile_id=profile["id"],
            key=f"{code}-self",
            client_key="self",
            dependencies=dependencies,
        )
    ]
    if code == "duplicate_dependency":
        requests.insert(
            0,
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key=f"{code}-prerequisite",
                client_key="prerequisite",
            ),
        )

    with pytest.raises(TaskDelegationError) as error:
        app.state.task_delegation.create_batch(
            user, requests, start=False, connection=app.state.db
        )

    assert error.value.code == code
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert (
        app.state.db.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0]
        == 0
    )


def test_downstream_task_is_visibly_blocked_then_starts_exactly_once(
    tmp_path: Path,
):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "dag")
    user, profile = _user_profile(app)

    upstream, downstream = app.state.task_delegation.create_batch(
        user,
        [
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="dag-upstream",
                client_key="upstream",
            ),
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="dag-downstream",
                client_key="downstream",
                dependencies=(DependencyRequest("upstream"),),
            ),
        ],
        start=True,
        connection=app.state.db,
    )

    assert upstream.job["status"] == "running"
    assert downstream.job["status"] == "queued"
    assert downstream.blocked_reason
    assert "currently running" in downstream.blocked_reason
    downstream_id = downstream.job["id"]
    upstream_id = upstream.job["id"]
    assert client.get(f"/api/jobs/{downstream_id}").json()["blocked_reason"]
    app.state.db.execute(
        "UPDATE jobs SET status = 'done', finished_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (upstream_id,),
    )

    assert app.state.task_delegation.prerequisite_changed(
        upstream_id, connection=app.state.db
    ) == [downstream_id]
    assert (
        app.state.db.execute(
            "SELECT status, blocked_reason FROM jobs WHERE id = ?",
            (downstream_id,),
        ).fetchone()["status"]
        == "running"
    )
    app.state.task_delegation.prerequisite_changed(
        upstream_id, connection=app.state.db
    )
    assert (
        app.state.db.execute(
            "SELECT COUNT(*) FROM runs WHERE session_id = "
            "(SELECT session_id FROM jobs WHERE id = ?)",
            (downstream_id,),
        ).fetchone()[0]
        == 1
    )


def test_failed_prerequisite_updates_durable_blocked_reason(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "failed-dependency")
    user, profile = _user_profile(app)
    upstream, downstream = app.state.task_delegation.create_batch(
        user,
        [
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="failed-up",
                client_key="up",
            ),
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="failed-down",
                client_key="down",
                dependencies=(DependencyRequest("up"),),
            ),
        ],
        start=True,
        connection=app.state.db,
    )
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed' WHERE id = ?", (upstream.job["id"],)
    )

    app.state.task_delegation.prerequisite_changed(
        upstream.job["id"], connection=app.state.db
    )

    blocked = app.state.db.execute(
        "SELECT jobs.status, jobs.blocked_reason, delegation.start_state "
        "FROM jobs JOIN task_delegations AS delegation "
        "ON delegation.job_id = jobs.id WHERE jobs.id = ?",
        (downstream.job["id"],),
    ).fetchone()
    assert blocked["status"] == "queued"
    assert blocked["start_state"] == "blocked"
    assert "which failed" in blocked["blocked_reason"]


def test_failed_graph_step_blocks_even_a_review_dependency(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "failed-graph-dependency")
    user, profile = _user_profile(app)
    upstream, downstream = app.state.task_delegation.create_batch(
        user,
        [
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="failed-graph-up",
                client_key="up",
            ),
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="failed-graph-down",
                client_key="down",
                dependencies=(
                    DependencyRequest("up", required_status="review"),
                ),
            ),
        ],
        start=False,
        connection=app.state.db,
    )
    app.state.db.execute(
        "UPDATE jobs SET engine = 'graph', status = 'review' WHERE id = ?",
        (upstream.job["id"],),
    )
    app.state.db.execute(
        "INSERT INTO node_states(job_id, node_id, status, output_kind) "
        "VALUES (?, 'failed-step', 'failed', 'text')",
        (upstream.job["id"],),
    )

    result = app.state.task_delegation.start(
        downstream.job["id"], user, connection=app.state.db
    )

    assert not result.started
    assert "failed Recipe step" in str(result.blocked_reason)
    assert result.job["status"] == "queued"


def test_deferred_batch_start_intent_survives_restart(tmp_path: Path):
    database_path = tmp_path / "deferred-batch.db"
    app = _app(tmp_path, database_path=database_path)
    client = _client(app)
    container, area = _container(client, app, "deferred-batch")
    user, profile = _user_profile(app)
    results = app.state.task_delegation.create_batch(
        user,
        [
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="deferred-batch-task",
            ),
        ],
        start=True,
        defer_start=True,
        connection=app.state.db,
    )
    job_id = results[0].job["id"]
    intent = app.state.db.execute(
        "SELECT start_requested, start_state FROM task_delegations "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert intent["start_requested"] == 1
    assert intent["start_state"] == "pending"
    assert results[0].job["status"] == "queued"

    restarted = _app(tmp_path, database_path=database_path)

    assert restarted.state.db.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["status"] == "running"
    assert restarted.state.db.execute(
        "SELECT COUNT(*) FROM runs WHERE session_id = "
        "(SELECT session_id FROM jobs WHERE id = ?)",
        (job_id,),
    ).fetchone()[0] == 1


def test_impossible_terminal_prerequisite_rolls_back_new_task(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "impossible")
    user, profile = _user_profile(app)
    prerequisite = app.state.task_delegation.create_and_start(
        user,
        _request(
            container_id=container["id"],
            area_id=area["id"],
            profile_id=profile["id"],
            key="terminal-prerequisite",
        ),
        start=False,
        connection=app.state.db,
    )
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed' WHERE id = ?",
        (prerequisite.job["id"],),
    )
    before = app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    with pytest.raises(TaskDelegationError) as error:
        app.state.task_delegation.create_and_start(
            user,
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="impossible-downstream",
                dependencies=(DependencyRequest(prerequisite.job["id"]),),
            ),
            start=False,
            connection=app.state.db,
        )

    assert error.value.code == "impossible_prerequisite"
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before


def test_exact_container_area_and_owner_validation(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    first, first_area = _container(client, app, "first")
    second, second_area = _container(client, app, "second")
    user, profile = _user_profile(app)

    with pytest.raises(TaskDelegationError) as cross_area:
        app.state.task_delegation.create_and_start(
            user,
            _request(
                container_id=first["id"],
                area_id=second_area["id"],
                profile_id=profile["id"],
                key="cross-area",
            ),
            start=False,
            connection=app.state.db,
        )
    assert cross_area.value.code == "area_not_in_container"

    other_user_id = app.state.db.execute(
        "INSERT INTO users(username, os_user, role) VALUES "
        "('other-owner', 'other-owner', 'member')"
    ).lastrowid
    other_container_id = app.state.db.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        ("other", "Other", str(tmp_path / "other"), other_user_id),
    ).lastrowid
    other_area_id = app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', 'ops', 'auto')",
        (other_container_id,),
    ).lastrowid
    with pytest.raises(TaskDelegationError) as cross_owner:
        app.state.task_delegation.create_and_start(
            user,
            _request(
                container_id=other_container_id,
                area_id=other_area_id,
                profile_id=profile["id"],
                key="cross-owner",
            ),
            start=False,
            connection=app.state.db,
        )
    assert cross_owner.value.code == "container_not_found"

    with pytest.raises(TaskDelegationError) as missing_area:
        app.state.task_delegation.create_and_start(
            user,
            _request(
                container_id=second["id"],
                area_id=0,
                profile_id=profile["id"],
                key="missing-area",
            ),
            start=False,
            connection=app.state.db,
        )
    assert missing_area.value.code == "invalid_area_id"
    assert app.state.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_database_trigger_rejects_cycle_from_non_service_writer(tmp_path: Path):
    app = _app(tmp_path)
    client = _client(app)
    container, area = _container(client, app, "trigger")
    user, profile = _user_profile(app)
    first, second = app.state.task_delegation.create_batch(
        user,
        [
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="trigger-first",
                client_key="first",
            ),
            _request(
                container_id=container["id"],
                area_id=area["id"],
                profile_id=profile["id"],
                key="trigger-second",
                client_key="second",
            ),
        ],
        start=False,
        connection=app.state.db,
    )
    app.state.db.execute(
        "INSERT INTO task_dependencies(task_id, depends_on_task_id) VALUES (?, ?)",
        (first.job["id"], second.job["id"]),
    )

    with pytest.raises(sqlite3.IntegrityError, match="cycle"):
        app.state.db.execute(
            "INSERT INTO task_dependencies(task_id, depends_on_task_id) VALUES (?, ?)",
            (second.job["id"], first.job["id"]),
        )

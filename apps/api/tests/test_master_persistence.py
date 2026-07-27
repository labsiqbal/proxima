from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api.db import connect, init_db
from proxima_api.main import create_app
from proxima_api.master_persistence import (
    MasterPersistenceError,
    migrate_master_persistence,
)
from proxima_api.migrations import current_version, run_migrations


def _alpha_v30_database(path: Path, workspace: Path) -> dict[str, int]:
    conn = connect(path)
    init_db(conn)
    run_migrations(conn, str(path))
    conn.execute("DELETE FROM schema_migrations WHERE version = 31")
    conn.execute("DROP INDEX IF EXISTS idx_jobs_origin_master")
    conn.execute("DROP INDEX IF EXISTS idx_profiles_one_master")
    conn.execute("DROP INDEX IF EXISTS idx_sessions_one_master")
    conn.execute("DROP TRIGGER IF EXISTS master_session_must_be_unbound_insert")
    conn.execute("DROP TRIGGER IF EXISTS master_session_must_be_unbound_update")
    conn.execute(
        "ALTER TABLE jobs RENAME COLUMN origin_master_session_id TO alpha_session_id"
    )
    conn.execute(
        "CREATE INDEX idx_jobs_alpha "
        "ON jobs(alpha_session_id, status, created_at)"
    )

    owner_id = int(
        conn.execute(
            "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
        ).lastrowid
    )
    default_profile_id = int(
        conn.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, runner_id, is_default"
            ") VALUES (?, 'default', 'Default', ?, 'hermes', 1)",
            (owner_id, str(workspace / "profiles" / "default")),
        ).lastrowid
    )
    alpha_profile_id = int(
        conn.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, runner_id, system_kind"
            ") VALUES (?, 'alpha-system', 'Alpha', ?, 'hermes', 'alpha')",
            (owner_id, str(workspace / "profiles" / "alpha-system")),
        ).lastrowid
    )
    alpha_session_id = int(
        conn.execute(
            "INSERT INTO sessions("
            "title, owner_user_id, profile_id, runner_id, mode, manual_title"
            ") VALUES ('Alpha', ?, ?, 'hermes', 'alpha', 1)",
            (owner_id, alpha_profile_id),
        ).lastrowid
    )
    message_id = int(
        conn.execute(
            "INSERT INTO messages(session_id, role, content, author) "
            "VALUES (?, 'user', 'preserve this history', 'owner')",
            (alpha_session_id,),
        ).lastrowid
    )
    run_id = int(
        conn.execute(
            "INSERT INTO runs("
            "session_id, user_id, profile_id, runner_id, kind, status, prompt"
            ") VALUES (?, ?, ?, 'hermes', 'alpha', 'done', 'preserve this run')",
            (alpha_session_id, owner_id, alpha_profile_id),
        ).lastrowid
    )
    conn.execute(
        "INSERT INTO events(run_id, session_id, seq, type, payload) "
        "VALUES (?, ?, 1, 'run.completed', ?)",
        (
            run_id,
            alpha_session_id,
            json.dumps({"alpha": True, "view": "alpha"}),
        ),
    )
    container_root = workspace / "container"
    container_root.mkdir(parents=True)
    container_id = int(
        conn.execute(
            "INSERT INTO projects(slug, name, path, owner_user_id) "
            "VALUES ('fixture', 'Fixture', ?, ?)",
            (str(container_root), owner_id),
        ).lastrowid
    )
    area_id = int(
        conn.execute(
            "INSERT INTO project_areas(project_id, kind, rel_path, source) "
            "VALUES (?, 'ops', 'ops', 'manual')",
            (container_id,),
        ).lastrowid
    )
    worker_session_id = int(
        conn.execute(
            "INSERT INTO sessions("
            "title, project_id, owner_user_id, profile_id, runner_id, mode"
            ") VALUES ('Task worker', ?, ?, ?, 'hermes', 'chat')",
            (container_id, owner_id, default_profile_id),
        ).lastrowid
    )
    job_id = int(
        conn.execute(
            "INSERT INTO jobs("
            "project_id, session_id, title, status, input, steps_state, "
            "target_area_id, alpha_session_id, created_by"
            ") VALUES (?, ?, 'Preserved Task', 'review', ?, '[]', ?, ?, ?)",
            (
                container_id,
                worker_session_id,
                json.dumps(
                    {
                        "execution_policy": "autonomous",
                        "alpha_dispatched": True,
                    }
                ),
                area_id,
                alpha_session_id,
                owner_id,
            ),
        ).lastrowid
    )
    conn.execute(
        "UPDATE sessions SET job_id = ? WHERE id = ?",
        (job_id, worker_session_id),
    )
    delegation_id = int(
        conn.execute(
            "INSERT INTO task_delegations("
            "origin_session_id, origin_message_id, container_id, target_area_id, "
            "job_id, routing_mode, created_by, idempotency_key, "
            "idempotency_identity, request_fingerprint, start_requested, start_state"
            ") VALUES (?, ?, ?, ?, ?, 'explicit', ?, 'fixture-key', "
            "'fixture-identity', 'fixture-fingerprint', 1, 'started')",
            (
                alpha_session_id,
                message_id,
                container_id,
                area_id,
                job_id,
                owner_id,
            ),
        ).lastrowid
    )
    checkpoint_id = int(
        conn.execute(
        "INSERT INTO job_checkpoints(job_id, payload_json, git_refs_json) "
        "VALUES (?, ?, '[]')",
        (
            job_id,
            json.dumps(
                {
                    "job": {
                        "status": "queued",
                        "alpha_session_id": alpha_session_id,
                        "input": {"alpha_dispatched": True},
                    },
                    "run_ids": [],
                }
            ),
        ),
        ).lastrowid
    )
    attention_id = int(
        conn.execute(
            "INSERT INTO attention_items("
            "kind, title, target_json, source_key"
            ") VALUES ('alpha_budget', 'Alpha budget preserved', ?, 'alpha-budget:fixture')",
            (
                json.dumps(
                    {
                        "view": "alpha",
                        "alpha_session_id": alpha_session_id,
                    }
                ),
            ),
        ).lastrowid
    )
    conn.executemany(
        "INSERT INTO app_settings(key, value) VALUES (?, ?)",
        (
            ("alpha.runner_id", "hermes"),
            ("alpha.unattended", "1"),
            ("alpha.budget.turns", "17"),
            ("alpha.budget.wall_seconds", "7200"),
            ("alpha.budget.tokens_optional", "12345"),
            ("alpha.budget.started_at", "2026-01-02T03:04:05+00:00"),
            ("alpha.budget.turns_used", "4"),
        ),
    )
    conn.execute(
        "INSERT INTO audit_log("
        "actor_user_id, action, target_type, target_id, metadata"
        ") VALUES (?, 'alpha.job.create', 'job', ?, ?)",
        (
            owner_id,
            str(job_id),
            json.dumps({"alpha_session_id": alpha_session_id}),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "owner_id": owner_id,
        "profile_id": alpha_profile_id,
        "session_id": alpha_session_id,
        "message_id": message_id,
        "run_id": run_id,
        "job_id": job_id,
        "delegation_id": delegation_id,
        "checkpoint_id": checkpoint_id,
        "attention_id": attention_id,
    }


def _app(path: Path, workspace: Path, *, enabled: bool = True):
    return create_app(
        {
            "database_path": str(path),
            "workspace_root": str(workspace),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
            "update_check": False,
            "feature_master_orchestrator": enabled,
        }
    )


def test_current_alpha_database_migrates_in_place_through_master_and_alias_api(
    tmp_path: Path,
):
    db_path = tmp_path / "proxima.db"
    workspace = tmp_path / "workspace"
    ids = _alpha_v30_database(db_path, workspace)

    app = _app(db_path, workspace)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})

    assert current_version(app.state.db) == 31
    profile = app.state.db.execute(
        "SELECT id, slug, name, system_kind FROM profiles WHERE system_kind = 'master'"
    ).fetchone()
    session = app.state.db.execute(
        "SELECT id, title, mode, project_id FROM sessions WHERE mode = 'master'"
    ).fetchone()
    job = app.state.db.execute(
        "SELECT id, origin_master_session_id FROM jobs WHERE id = ?",
        (ids["job_id"],),
    ).fetchone()
    assert dict(profile) == {
        "id": ids["profile_id"],
        "slug": "master-system",
        "name": "Master",
        "system_kind": "master",
    }
    assert dict(session) == {
        "id": ids["session_id"],
        "title": "Master",
        "mode": "master",
        "project_id": None,
    }
    assert dict(job) == {
        "id": ids["job_id"],
        "origin_master_session_id": ids["session_id"],
    }
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM profiles WHERE system_kind = 'master'"
    ).fetchone()[0] == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM sessions WHERE mode = 'master'"
    ).fetchone()[0] == 1
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM profiles WHERE system_kind = 'alpha'"
    ).fetchone()[0] == 0
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM sessions WHERE mode = 'alpha'"
    ).fetchone()[0] == 0

    assert app.state.db.execute(
        "SELECT id FROM messages WHERE id = ? AND session_id = ?",
        (ids["message_id"], ids["session_id"]),
    ).fetchone()
    assert app.state.db.execute(
        "SELECT id, kind FROM runs WHERE id = ? AND session_id = ?",
        (ids["run_id"], ids["session_id"]),
    ).fetchone()["kind"] == "master"
    assert app.state.db.execute(
        "SELECT id FROM job_checkpoints WHERE id = ? AND job_id = ?",
        (ids["checkpoint_id"], ids["job_id"]),
    ).fetchone()
    checkpoint_payload = json.loads(
        app.state.db.execute(
            "SELECT payload_json FROM job_checkpoints WHERE id = ?",
            (ids["checkpoint_id"],),
        ).fetchone()["payload_json"]
    )
    assert checkpoint_payload["job"]["origin_master_session_id"] == ids["session_id"]
    assert checkpoint_payload["job"]["input"] == {"master_dispatched": True}
    assert json.loads(
        app.state.db.execute(
            "SELECT input FROM jobs WHERE id = ?", (ids["job_id"],)
        ).fetchone()["input"]
    )["master_dispatched"] is True
    delegation = app.state.db.execute(
        "SELECT id, origin_session_id, origin_message_id, job_id "
        "FROM task_delegations WHERE id = ?",
        (ids["delegation_id"],),
    ).fetchone()
    assert dict(delegation) == {
        "id": ids["delegation_id"],
        "origin_session_id": ids["session_id"],
        "origin_message_id": ids["message_id"],
        "job_id": ids["job_id"],
    }
    attention = app.state.db.execute(
        "SELECT id, kind, title, target_json FROM attention_items WHERE id = ?",
        (ids["attention_id"],),
    ).fetchone()
    assert attention["kind"] == "master_budget"
    assert attention["title"] == "Master budget preserved"
    assert json.loads(attention["target_json"]) == {
        "view": "master",
        "origin_master_session_id": ids["session_id"],
    }
    assert app.state.db.execute(
        "SELECT value FROM app_settings WHERE key = 'master.budget.turns'"
    ).fetchone()["value"] == "17"
    assert app.state.db.execute(
        "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'alpha.%'"
    ).fetchone()[0] == 0
    assert json.loads(
        app.state.db.execute(
            "SELECT payload FROM events WHERE run_id = ?", (ids["run_id"],)
        ).fetchone()["payload"]
    ) == {"master": True, "view": "master"}
    audit = app.state.db.execute(
        "SELECT action, metadata FROM audit_log WHERE target_id = ?",
        (str(ids["job_id"]),),
    ).fetchone()
    assert audit["action"] == "master.job.create"
    assert json.loads(audit["metadata"]) == {
        "origin_master_session_id": ids["session_id"]
    }
    assert app.state.db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert any(
        row[3] == "origin_master_session_id" and row[6] == "SET NULL"
        for row in app.state.db.execute("PRAGMA foreign_key_list(jobs)").fetchall()
    )
    assert [
        row[2]
        for row in app.state.db.execute(
            "PRAGMA index_info(idx_jobs_origin_master)"
        ).fetchall()
    ] == ["origin_master_session_id", "status", "created_at"]

    master = client.get("/api/master/desk")
    legacy = client.get("/api/alpha/desk")
    assert master.status_code == legacy.status_code == 200
    assert master.json()["session"]["id"] == legacy.json()["session"]["id"] == ids[
        "session_id"
    ]
    assert master.json()["session"]["mode"] == "master"
    assert legacy.json()["session"]["mode"] == "alpha"
    assert master.json()["jobs"][0]["id"] == legacy.json()["jobs"][0]["id"] == ids[
        "job_id"
    ]
    assert master.json()["jobs"][0]["origin_master_session_id"] == ids["session_id"]
    assert legacy.json()["jobs"][0]["alpha_session_id"] == ids["session_id"]
    assert master.json()["budgets"]["budget_turns"] == 17
    assert master.json()["checkpoints"][0]["id"] == ids["checkpoint_id"]
    messages = client.get(f"/api/sessions/{ids['session_id']}/messages")
    assert messages.status_code == 200
    assert messages.json()["messages"][0]["content"] == "preserve this history"

    app.state.db.execute("SAVEPOINT master_delete_probe")
    app.state.db.execute(
        "DELETE FROM sessions WHERE id = ?", (ids["session_id"],)
    )
    assert app.state.db.execute(
        "SELECT origin_master_session_id FROM jobs WHERE id = ?", (ids["job_id"],)
    ).fetchone()["origin_master_session_id"] is None
    app.state.db.execute("ROLLBACK TO master_delete_probe")
    app.state.db.execute("RELEASE master_delete_probe")
    assert app.state.db.execute(
        "SELECT origin_master_session_id FROM jobs WHERE id = ?", (ids["job_id"],)
    ).fetchone()["origin_master_session_id"] == ids["session_id"]


def test_restart_runner_switch_and_feature_off_preserve_one_identity(tmp_path: Path):
    db_path = tmp_path / "proxima.db"
    workspace = tmp_path / "workspace"
    ids = _alpha_v30_database(db_path, workspace)
    first = _app(db_path, workspace)
    client = TestClient(first)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    assert client.put(
        "/api/settings/master", json={"runner_id": "codex"}
    ).status_code == 200

    restarted = _app(db_path, workspace)
    assert restarted.state.db.execute(
        "SELECT id FROM profiles WHERE system_kind = 'master'"
    ).fetchone()["id"] == ids["profile_id"]
    assert restarted.state.db.execute(
        "SELECT id FROM sessions WHERE mode = 'master'"
    ).fetchone()["id"] == ids["session_id"]
    assert restarted.state.db.execute(
        "SELECT runner_id FROM sessions WHERE id = ?", (ids["session_id"],)
    ).fetchone()["runner_id"] == "codex"

    disabled = _app(db_path, workspace, enabled=False)
    disabled_client = TestClient(disabled)
    disabled_token = disabled_client.post("/auth/auto").json()["token"]
    disabled_client.headers.update(
        {"Authorization": f"Bearer {disabled_token}"}
    )
    response = disabled_client.get("/api/master/desk")
    assert response.status_code == 503
    assert response.json()["detail"]["feature"] == "master_orchestrator"
    assert disabled.state.db.execute(
        "SELECT COUNT(*) FROM profiles WHERE system_kind = 'master'"
    ).fetchone()[0] == 1
    assert disabled.state.db.execute(
        "SELECT COUNT(*) FROM sessions WHERE mode = 'master'"
    ).fetchone()[0] == 1
    assert disabled.state.db.execute(
        "SELECT origin_master_session_id FROM jobs WHERE id = ?", (ids["job_id"],)
    ).fetchone()["origin_master_session_id"] == ids["session_id"]


def test_partial_column_state_recovers_idempotently_and_conflicts_refuse(
    tmp_path: Path,
):
    db_path = tmp_path / "partial.db"
    conn = connect(db_path)
    init_db(conn)
    run_migrations(conn, str(db_path))
    owner_id = int(
        conn.execute(
            "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
        ).lastrowid
    )
    profile_id = int(
        conn.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, system_kind"
            ") VALUES (?, 'master-system', 'Master', '/tmp/master', 'master')",
            (owner_id,),
        ).lastrowid
    )
    session_id = int(
        conn.execute(
            "INSERT INTO sessions(title, owner_user_id, profile_id, mode) "
            "VALUES ('Master', ?, ?, 'master')",
            (owner_id, profile_id),
        ).lastrowid
    )
    job_id = int(
        conn.execute(
            "INSERT INTO jobs(title, created_by, origin_master_session_id) "
            "VALUES ('partial', ?, ?)",
            (owner_id, session_id),
        ).lastrowid
    )
    conn.execute(
        "ALTER TABLE jobs ADD COLUMN alpha_session_id INTEGER "
        "REFERENCES sessions(id) ON DELETE SET NULL"
    )
    conn.execute(
        "UPDATE jobs SET alpha_session_id = origin_master_session_id WHERE id = ?",
        (job_id,),
    )

    migrate_master_persistence(conn)
    migrate_master_persistence(conn)
    assert "alpha_session_id" not in {
        row[1] for row in conn.execute("PRAGMA table_info(jobs)")
    }
    assert conn.execute(
        "SELECT origin_master_session_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["origin_master_session_id"] == session_id

    other_session_id = int(
        conn.execute(
            "INSERT INTO sessions(title, owner_user_id, profile_id, mode) "
            "VALUES ('ordinary', ?, ?, 'chat')",
            (owner_id, profile_id),
        ).lastrowid
    )
    conn.execute(
        "ALTER TABLE jobs ADD COLUMN alpha_session_id INTEGER "
        "REFERENCES sessions(id) ON DELETE SET NULL"
    )
    conn.execute(
        "UPDATE jobs SET alpha_session_id = ? WHERE id = ?",
        (other_session_id, job_id),
    )
    with pytest.raises(
        MasterPersistenceError, match="conflicting Alpha and Master origins"
    ):
        migrate_master_persistence(conn)


def test_ambiguous_dual_identity_refuses_without_rewriting_rows(tmp_path: Path):
    db_path = tmp_path / "ambiguous.db"
    conn = connect(db_path)
    init_db(conn)
    run_migrations(conn, str(db_path))
    owner_id = int(
        conn.execute(
            "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
        ).lastrowid
    )
    master_id = int(
        conn.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, system_kind"
            ") VALUES (?, 'master-system', 'Master', '/tmp/master', 'master')",
            (owner_id,),
        ).lastrowid
    )
    alpha_id = int(
        conn.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, system_kind"
            ") VALUES (?, 'alpha-system', 'Alpha', '/tmp/alpha', 'alpha')",
            (owner_id,),
        ).lastrowid
    )

    with pytest.raises(MasterPersistenceError, match="multiple Alpha or Master"):
        migrate_master_persistence(conn)
    assert conn.execute(
        "SELECT system_kind FROM profiles WHERE id = ?", (master_id,)
    ).fetchone()["system_kind"] == "master"
    assert conn.execute(
        "SELECT system_kind FROM profiles WHERE id = ?", (alpha_id,)
    ).fetchone()["system_kind"] == "alpha"


@pytest.mark.parametrize(
    ("profile_kind", "session_mode"),
    [("alpha", "master"), ("master", "alpha")],
)
def test_partially_renamed_identity_recovers_in_place(
    tmp_path: Path,
    profile_kind: str,
    session_mode: str,
):
    conn = connect(tmp_path / "partial-identity.db")
    init_db(conn)
    run_migrations(conn)
    owner_id = int(
        conn.execute(
            "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
        ).lastrowid
    )
    profile_id = int(
        conn.execute(
            "INSERT INTO profiles("
            "user_id, slug, name, hermes_home, system_kind"
            ") VALUES (?, ?, 'orchestrator', '/tmp/orchestrator', ?)",
            (
                owner_id,
                "alpha-system" if profile_kind == "alpha" else "master-system",
                profile_kind,
            ),
        ).lastrowid
    )
    session_id = int(
        conn.execute(
            "INSERT INTO sessions(title, owner_user_id, profile_id, mode) "
            "VALUES ('orchestrator', ?, ?, ?)",
            (owner_id, profile_id, session_mode),
        ).lastrowid
    )

    migrate_master_persistence(conn)

    assert conn.execute(
        "SELECT system_kind FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()["system_kind"] == "master"
    assert conn.execute(
        "SELECT mode FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()["mode"] == "master"


def test_transactional_migration_refusal_rolls_back_identity_and_version(
    tmp_path: Path,
):
    db_path = tmp_path / "rollback.db"
    workspace = tmp_path / "workspace"
    ids = _alpha_v30_database(db_path, workspace)
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES ('master.runner_id', 'codex')"
    )
    conn.commit()

    with pytest.raises(
        MasterPersistenceError, match="alpha.runner_id and master.runner_id conflict"
    ):
        run_migrations(conn, str(db_path))

    assert current_version(conn) == 30
    assert conn.execute(
        "SELECT system_kind FROM profiles WHERE id = ?", (ids["profile_id"],)
    ).fetchone()["system_kind"] == "alpha"
    assert conn.execute(
        "SELECT mode FROM sessions WHERE id = ?", (ids["session_id"],)
    ).fetchone()["mode"] == "alpha"
    assert "alpha_session_id" in {
        row[1] for row in conn.execute("PRAGMA table_info(jobs)")
    }

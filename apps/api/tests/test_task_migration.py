from __future__ import annotations

import json
import sqlite3

from proxima_api import db


def _seed_task(conn, status="doing"):
    conn.execute("INSERT INTO users(id, username, os_user) VALUES (1, 'u', 'u')")
    conn.execute("INSERT INTO projects(id, slug, name, path, owner_user_id) VALUES (1, 'p', 'P', '/tmp/p', 1)")
    conn.execute("INSERT INTO sessions(id, title, owner_user_id) VALUES (1, 'T', 1)")
    conn.execute(
        "INSERT INTO tasks(project_id, session_id, title, description, status, created_by) VALUES (1, 1, 'T', 'desc', ?, 1)",
        (status,),
    )
    conn.commit()


def test_tasks_migrate_to_jobs(tmp_path):
    conn = sqlite3.connect(tmp_path / "proxima.db")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)  # no tasks yet -> no-op
    _seed_task(conn, status="doing")

    n = db.migrate_existing(conn) if False else db._migrate_tasks_to_jobs(conn)
    assert n == 1
    job = conn.execute("SELECT * FROM jobs WHERE title = 'T'").fetchone()
    assert job is not None
    assert job["workflow_id"] is None
    assert job["status"] == "running"  # doing -> running
    assert json.loads(job["steps_state"])[0]["instruction"] == "desc"
    # session is now linked to the job
    assert conn.execute("SELECT job_id FROM sessions WHERE id = 1").fetchone()["job_id"] == job["id"]


def test_migration_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "proxima.db")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    _seed_task(conn, status="done")

    assert db._migrate_tasks_to_jobs(conn) == 1
    assert db._migrate_tasks_to_jobs(conn) == 0  # already migrated -> skipped
    assert conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 1


def test_migrate_existing_cleans_orphan_agent_sessions(tmp_path):
    conn = sqlite3.connect(tmp_path / "proxima.db")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (999, '/tmp/home', 'stale')"
    )
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is not None

    db.migrate_existing(conn)

    assert conn.execute("SELECT COUNT(*) AS c FROM agent_sessions").fetchone()["c"] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchone() is None

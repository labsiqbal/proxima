from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from proxima_api.db import SCHEMA, connect, init_db
from proxima_api.master_persistence import MasterPersistenceError
from proxima_api.migrations import MIGRATIONS, current_version, run_migrations


def _add_foo(conn):
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY, note TEXT)")


def _add_users_nickname(conn):
    conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")


def test_migrations_upgrade_pre_alpha_jobs_to_neutral_master_origin(tmp_path: Path):
    conn = connect(tmp_path / "pre-alpha.db")
    legacy_schema = SCHEMA.replace(
        "  origin_master_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,\n", ""
    )
    conn.executescript(legacy_schema)

    init_db(conn)
    run_migrations(conn, str(tmp_path / "pre-alpha.db"))

    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
    assert "origin_master_session_id" in columns
    assert "alpha_session_id" not in columns
    assert "idx_jobs_origin_master" in indexes


def test_no_pending_is_noop_but_creates_tracking_table(tmp_path: Path):
    conn = connect(tmp_path / "h.db")
    assert run_migrations(conn, str(tmp_path / "h.db"), migrations=[]) == []
    # tracking table exists, version 0
    assert current_version(conn) == 0


def test_init_db_adds_project_path_identity_to_existing_schema(tmp_path: Path):
    conn = connect(tmp_path / "legacy-projects.db")
    legacy_schema = SCHEMA.replace("  path_identity TEXT,\n", "")
    conn.executescript(legacy_schema)
    project_path = tmp_path / "legacy-project"
    project_path.mkdir()
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
    ).lastrowid
    project_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('legacy', 'Legacy', ?, ?)",
        (str(project_path), user_id),
    ).lastrowid

    init_db(conn)

    assert "path_identity" in {
        row[1] for row in conn.execute("PRAGMA table_info(projects)")
    }
    identity = conn.execute(
        "SELECT path_identity FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()["path_identity"]
    assert identity.startswith(("posix:", "windows:"))


def test_init_db_marks_unreachable_legacy_project_identity_unavailable(
    tmp_path: Path,
):
    conn = connect(tmp_path / "missing-legacy-project.db")
    legacy_schema = SCHEMA.replace("  path_identity TEXT,\n", "")
    conn.executescript(legacy_schema)
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
    ).lastrowid
    project_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('missing', 'Missing', ?, ?)",
        (str(tmp_path / "missing"), user_id),
    ).lastrowid

    init_db(conn)

    identity = conn.execute(
        "SELECT path_identity FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()["path_identity"]
    assert identity.startswith("unavailable:")


def test_v43_adds_safe_update_projection_once_to_pre_v43_schema(tmp_path: Path):
    db_path = tmp_path / "schema-42.db"
    conn = connect(db_path)
    init_db(conn)
    through_v43 = [migration for migration in MIGRATIONS if migration[0] <= 43]
    run_migrations(conn, str(db_path), migrations=through_v43)
    conn.execute("DROP TABLE self_update_runs")
    conn.execute("DELETE FROM schema_migrations WHERE version = 43")

    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v43,
    ) == [43]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v43,
    ) == []
    assert current_version(conn) == 43
    assert {
        row[1] for row in conn.execute("PRAGMA table_info(self_update_runs)")
    } == {
        "id",
        "origin_job_id",
        "base_commit",
        "candidate_commit",
        "previous_release_id",
        "candidate_release_id",
        "previous_schema_version",
        "candidate_schema_version",
        "phase",
        "status",
        "journal_digest",
        "journal_ref",
        "evidence_summary",
        "failure_class",
        "rollback_status",
        "created_at",
        "updated_at",
    }
    assert {
        row[1]
        for row in conn.execute("PRAGMA index_list(self_update_runs)")
    } == {"idx_self_update_runs_status", "sqlite_autoindex_self_update_runs_1"}
    assert [
        row[2]
        for row in conn.execute(
            "PRAGMA index_info(idx_self_update_runs_status)"
        )
    ] == ["status", "created_at"]


def test_v45_adds_task_projection_outbox_and_lifecycle_history(
    tmp_path: Path,
):
    db_path = tmp_path / "schema-43.db"
    conn = connect(db_path)
    init_db(conn)
    through_v45 = [migration for migration in MIGRATIONS if migration[0] <= 45]
    run_migrations(conn, str(db_path), migrations=through_v45)
    conn.execute("DROP TABLE task_projection_outbox")
    conn.execute(
        "CREATE UNIQUE INDEX uq_master_projections_source_type "
        "ON master_projections("
        "owner_user_id, source_table, source_id, projection_type)"
    )
    conn.execute("DELETE FROM schema_migrations WHERE version = 45")

    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v45,
    ) == [45]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v45,
    ) == []
    assert current_version(conn) == 45
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(task_projection_outbox)"
        )
    } == {
        "id",
        "job_id",
        "task_event_id",
        "projection_epoch",
        "task_status",
        "mutation",
        "state",
        "projection_id",
        "failure_code",
        "attempt_count",
        "created_at",
        "updated_at",
    }
    projection_indexes = {
        row[1]
        for row in conn.execute("PRAGMA index_list(master_projections)")
    }
    assert "uq_master_projections_source_type" not in projection_indexes


def _v46_recovery_database(
    tmp_path: Path,
    name: str,
) -> tuple[sqlite3.Connection, Path, dict[str, int]]:
    db_path = tmp_path / name
    conn = connect(db_path)
    init_db(conn)
    through_v45 = [migration for migration in MIGRATIONS if migration[0] <= 45]
    run_migrations(conn, str(db_path), migrations=through_v45)
    conn.execute("DROP TABLE task_recovery_outbox")
    conn.execute("DROP TABLE task_projection_outbox")
    conn.execute("DELETE FROM schema_migrations WHERE version = 45")
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v45,
    ) == [45]

    through_v46 = [migration for migration in MIGRATIONS if migration[0] <= 46]
    assert run_migrations(conn, str(db_path), migrations=through_v46) == [46]
    owner_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('audit-owner', 'owner')"
    ).lastrowid
    master_session_id = conn.execute(
        "INSERT INTO sessions(title, owner_user_id, mode) "
        "VALUES ('Master', ?, 'master')",
        (owner_id,),
    ).lastrowid
    task_session_id = conn.execute(
        "INSERT INTO sessions(title, owner_user_id) VALUES ('Task', ?)",
        (owner_id,),
    ).lastrowid
    job_id = conn.execute(
        "INSERT INTO jobs(session_id, title, status, created_by, "
        "origin_master_session_id) VALUES (?, 'Recovery', 'queued', ?, ?)",
        (task_session_id, owner_id, master_session_id),
    ).lastrowid
    return conn, db_path, {
        "master_session_id": int(master_session_id),
        "task_session_id": int(task_session_id),
        "job_id": int(job_id),
    }


def _recovery_task_event(
    conn: sqlite3.Connection,
    *,
    task_session_id: int,
    seq: int,
) -> int:
    return int(
        conn.execute(
            "INSERT INTO events(session_id, seq, type, payload) "
            "VALUES (?, ?, 'job.update', '{}')",
            (task_session_id, seq),
        ).lastrowid
    )


def _recovery_payload(job_id: int, checkpoint_id: int) -> str:
    return json.dumps(
        {
            "job_id": job_id,
            "checkpoint_id": checkpoint_id,
            "actor": {"id": 1, "username": "audit-owner"},
            "prior_status": "failed",
            "restored_status": "queued",
            "discarded_progress": [],
            "conflicting_progress": [],
        }
    )


def _projected_v46_recovery(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    task_event_id: int,
    master_session_id: int,
    recovery_json: str,
) -> int:
    message_id = int(
        conn.execute(
            "INSERT INTO messages(session_id, role, content, author) "
            "VALUES (?, 'assistant', 'Recovered Task.', 'Master')",
            (master_session_id,),
        ).lastrowid
    )
    event_seq = int(
        conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events "
            "WHERE session_id = ?",
            (master_session_id,),
        ).fetchone()[0]
    )
    event_id = int(
        conn.execute(
            "INSERT INTO events(session_id, seq, type, payload) "
            "VALUES (?, ?, 'master.task.recovered', '{}')",
            (master_session_id, event_seq),
        ).lastrowid
    )
    return int(
        conn.execute(
            "INSERT INTO task_recovery_outbox("
            "job_id, task_event_id, projection_revision, recovery_json, "
            "state, master_session_id, message_id, event_id"
            ") VALUES (?, ?, 2, ?, 'projected', ?, ?, ?)",
            (
                job_id,
                task_event_id,
                recovery_json,
                master_session_id,
                message_id,
                event_id,
            ),
        ).lastrowid
    )


def _v48_correction_database(
    tmp_path: Path,
    name: str,
    *,
    pair_count: int = 2,
) -> tuple[sqlite3.Connection, Path, dict[str, int], list[int]]:
    conn, db_path, ids = _v46_recovery_database(tmp_path, name)
    for index in range(pair_count):
        predecessor_event_id = _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=index * 2 + 1,
        )
        successor_event_id = _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=index * 2 + 2,
        )
        conn.execute(
            "INSERT INTO task_recovery_outbox("
            "job_id, task_event_id, projection_revision, recovery_json, "
            "state, master_session_id, superseded_by_event_id"
            ") VALUES (?, ?, ?, ?, 'superseded', ?, ?)",
            (
                ids["job_id"],
                predecessor_event_id,
                index + 1,
                _recovery_payload(ids["job_id"], index + 1),
                ids["master_session_id"],
                successor_event_id,
            ),
        )
        _projected_v46_recovery(
            conn,
            job_id=ids["job_id"],
            task_event_id=successor_event_id,
            master_session_id=ids["master_session_id"],
            recovery_json=_recovery_payload(
                ids["job_id"],
                pair_count + index + 1,
            ),
        )
    through_v48 = [
        migration for migration in MIGRATIONS if migration[0] <= 48
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v48,
    ) == [47, 48]
    correction_ids = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM task_recovery_corrections "
            "ORDER BY first_task_event_id"
        ).fetchall()
    ]
    return conn, db_path, ids, correction_ids


def _deliver_v47_correction(
    conn: sqlite3.Connection,
    *,
    correction_id: int,
    label: str,
) -> tuple[int, int]:
    correction = conn.execute(
        "SELECT correction.*, successor.task_event_id "
        "AS successor_task_event_id "
        "FROM task_recovery_corrections AS correction "
        "JOIN task_recovery_outbox AS successor "
        "ON successor.id = correction.successor_outbox_id "
        "WHERE correction.id = ?",
        (correction_id,),
    ).fetchone()
    message_id = int(
        conn.execute(
            "INSERT INTO messages(session_id, role, content, author) "
            "VALUES (?, 'assistant', ?, 'Master')",
            (
                correction["master_session_id"],
                f"Legacy correction {label}",
            ),
        ).lastrowid
    )
    event_seq = int(
        conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events "
            "WHERE session_id = ?",
            (correction["master_session_id"],),
        ).fetchone()[0]
    )
    event_id = int(
        conn.execute(
            "INSERT INTO events(session_id, seq, type, payload) "
            "VALUES (?, ?, "
            "'master.task.recovery_history_corrected', ?)",
            (
                correction["master_session_id"],
                event_seq,
                json.dumps(
                    {
                        "message_id": message_id,
                        "task_id": correction["job_id"],
                        "gap_count": correction["gap_count"],
                        "first_task_event_id": correction[
                            "first_task_event_id"
                        ],
                        "last_task_event_id": correction[
                            "last_task_event_id"
                        ],
                        "successor_task_event_id": correction[
                            "successor_task_event_id"
                        ],
                    }
                ),
            ),
        ).lastrowid
    )
    conn.execute(
        "UPDATE task_recovery_corrections "
        "SET state = 'projected', message_id = ?, event_id = ?, "
        "failure_code = NULL, attempt_count = attempt_count + 1 "
        "WHERE id = ?",
        (message_id, event_id, correction_id),
    )
    return message_id, event_id


def test_v48_retains_published_successor_ordering_gap(tmp_path: Path):
    conn, db_path, ids = _v46_recovery_database(
        tmp_path,
        "schema-46-published-successor.db",
    )
    first_event_id = _recovery_task_event(
        conn,
        task_session_id=ids["task_session_id"],
        seq=1,
    )
    successor_event_id = _recovery_task_event(
        conn,
        task_session_id=ids["task_session_id"],
        seq=2,
    )
    first_payload = _recovery_payload(ids["job_id"], 1)
    first_outbox_id = int(
        conn.execute(
            "INSERT INTO task_recovery_outbox("
            "job_id, task_event_id, projection_revision, recovery_json, "
            "state, master_session_id, superseded_by_event_id, "
            "failure_code, attempt_count, created_at, updated_at"
            ") VALUES (?, ?, 1, ?, 'superseded', ?, ?, "
            "'projection_failed', 3, "
            "'2026-07-20 01:02:03', '2026-07-20 04:05:06')",
            (
                ids["job_id"],
                first_event_id,
                first_payload,
                ids["master_session_id"],
                successor_event_id,
            ),
        ).lastrowid
    )
    successor_outbox_id = _projected_v46_recovery(
        conn,
        job_id=ids["job_id"],
        task_event_id=successor_event_id,
        master_session_id=ids["master_session_id"],
        recovery_json=_recovery_payload(ids["job_id"], 2),
    )

    assert run_migrations(conn, str(db_path)) == [47, 48, 49, 50, 51, 52, 53]
    assert run_migrations(conn, str(db_path)) == []
    assert current_version(conn) == 53
    gap = dict(
        conn.execute(
            "SELECT id, task_event_id, recovery_json, state, "
            "ordering_successor_id, failure_code, attempt_count, created_at "
            "FROM task_recovery_outbox WHERE id = ?",
            (first_outbox_id,),
        ).fetchone()
    )
    assert gap == {
        "id": first_outbox_id,
        "task_event_id": first_event_id,
        "recovery_json": first_payload,
        "state": "legacy_ordering_gap",
        "ordering_successor_id": successor_outbox_id,
        "failure_code": None,
        "attempt_count": 3,
        "created_at": "2026-07-20 01:02:03",
    }
    assert dict(
        conn.execute(
            "SELECT job_id, successor_outbox_id, gap_count, "
            "first_task_event_id, last_task_event_id, "
            "first_successor_task_event_id, "
            "last_successor_task_event_id, state "
            "FROM task_recovery_corrections"
        ).fetchone()
    ) == {
        "job_id": ids["job_id"],
        "successor_outbox_id": successor_outbox_id,
        "gap_count": 1,
        "first_task_event_id": first_event_id,
        "last_task_event_id": first_event_id,
        "first_successor_task_event_id": successor_event_id,
        "last_successor_task_event_id": successor_event_id,
        "state": "pending",
    }
    assert dict(
        conn.execute(
            "SELECT job_id, predecessor_outbox_id, successor_outbox_id, "
            "kind, predecessor_task_event_id, successor_task_event_id, "
            "predecessor_publication_event_id, "
            "successor_publication_event_id "
            "FROM task_recovery_ordering_gaps"
        ).fetchone()
    ) == {
        "job_id": ids["job_id"],
        "predecessor_outbox_id": first_outbox_id,
        "successor_outbox_id": successor_outbox_id,
        "kind": "unpublished_predecessor",
        "predecessor_task_event_id": first_event_id,
        "successor_task_event_id": successor_event_id,
        "predecessor_publication_event_id": None,
        "successor_publication_event_id": conn.execute(
            "SELECT event_id FROM task_recovery_outbox WHERE id = ?",
            (successor_outbox_id,),
        ).fetchone()["event_id"],
    }
    with pytest.raises(
        sqlite3.IntegrityError,
        match="legacy recovery ordering gap is immutable",
    ):
        conn.execute(
            "UPDATE task_recovery_outbox SET recovery_json = '{}' "
            "WHERE id = ?",
            (first_outbox_id,),
        )
    conn.execute("DELETE FROM jobs WHERE id = ?", (ids["job_id"],))
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_outbox"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gaps"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_history_tombstones"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_correction_history"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gap_history"
    ).fetchone()[0] == 1


def test_v48_keeps_unpublished_recoveries_strictly_orderable(
    tmp_path: Path,
):
    conn, db_path, ids = _v46_recovery_database(
        tmp_path,
        "schema-46-unpublished-recoveries.db",
    )
    event_ids = [
        _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=seq,
        )
        for seq in range(1, 5)
    ]
    for index in (0, 2):
        conn.execute(
            "INSERT INTO task_recovery_outbox("
            "job_id, task_event_id, projection_revision, recovery_json, "
            "state, master_session_id, superseded_by_event_id, failure_code"
            ") VALUES (?, ?, ?, ?, 'superseded', ?, ?, ?)",
            (
                ids["job_id"],
                event_ids[index],
                index + 1,
                _recovery_payload(ids["job_id"], index + 1),
                ids["master_session_id"],
                event_ids[index + 1],
                (
                    "focus_attribution_unavailable"
                    if index == 0
                    else None
                ),
            ),
        )
        conn.execute(
            "INSERT INTO task_recovery_outbox("
            "job_id, task_event_id, projection_revision, recovery_json, "
            "state, master_session_id"
            ") VALUES (?, ?, ?, ?, 'pending', ?)",
            (
                ids["job_id"],
                event_ids[index + 1],
                index + 2,
                _recovery_payload(ids["job_id"], index + 2),
                ids["master_session_id"],
            ),
        )

    assert run_migrations(conn, str(db_path)) == [47, 48, 49, 50, 51, 52, 53]
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT task_event_id, state, ordering_successor_id "
            "FROM task_recovery_outbox ORDER BY task_event_id"
        ).fetchall()
    ] == [
        (event_ids[0], "failed_attribution", None),
        (event_ids[1], "pending", None),
        (event_ids[2], "pending", None),
        (event_ids[3], "pending", None),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 0


def test_v48_migration_is_atomic_and_idempotent(tmp_path: Path):
    conn, db_path, ids = _v46_recovery_database(
        tmp_path,
        "schema-46-interrupted-ordering-gap.db",
    )
    first_event_id = _recovery_task_event(
        conn,
        task_session_id=ids["task_session_id"],
        seq=1,
    )
    successor_event_id = _recovery_task_event(
        conn,
        task_session_id=ids["task_session_id"],
        seq=2,
    )
    conn.execute(
        "INSERT INTO task_recovery_outbox("
        "job_id, task_event_id, projection_revision, recovery_json, "
        "state, master_session_id, superseded_by_event_id"
        ") VALUES (?, ?, 1, ?, 'superseded', ?, ?)",
        (
            ids["job_id"],
            first_event_id,
            _recovery_payload(ids["job_id"], 1),
            ids["master_session_id"],
            successor_event_id,
        ),
    )
    _projected_v46_recovery(
        conn,
        job_id=ids["job_id"],
        task_event_id=successor_event_id,
        master_session_id=ids["master_session_id"],
        recovery_json=_recovery_payload(ids["job_id"], 2),
    )
    through_v47 = [migration for migration in MIGRATIONS if migration[0] <= 47]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v47,
    ) == [47]
    migration48 = next(entry[2] for entry in MIGRATIONS if entry[0] == 48)

    conn.execute("BEGIN")
    migration48(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    conn.execute("ROLLBACK")
    assert "ordering_successor_id" not in {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_outbox)"
        ).fetchall()
    }

    conn.execute("BEGIN")
    migration48(conn)
    conn.execute("COMMIT")
    conn.execute("BEGIN")
    migration48(conn)
    conn.execute("COMMIT")
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_outbox "
        "WHERE state = 'legacy_ordering_gap'"
    ).fetchone()[0] == 1


def test_v50_schema_separates_markers_and_recovery_coverage(
    tmp_path: Path,
):
    conn, db_path, _ = _v46_recovery_database(
        tmp_path,
        "schema-46-final-contract.db",
    )

    assert run_migrations(conn, str(db_path)) == [47, 48, 49, 50, 51, 52, 53]
    assert run_migrations(conn, str(db_path)) == []
    assert current_version(conn) == 53
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_outbox)"
        )
    } == {
        "id",
        "job_id",
        "task_event_id",
        "recovery_json",
        "state",
        "master_session_id",
        "message_id",
        "event_id",
        "ordering_successor_id",
        "failure_code",
        "attempt_count",
        "created_at",
        "updated_at",
    }
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_corrections)"
        )
    } >= {
        "job_id",
        "marker_kind",
        "successor_outbox_id",
        "gap_count",
        "first_task_event_id",
        "last_task_event_id",
        "first_successor_task_event_id",
        "last_successor_task_event_id",
        "state",
        "message_id",
        "event_id",
    }
    defaults = {
        row[1]: row[4]
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_corrections)"
        )
    }
    assert defaults["first_successor_task_event_id"] == "1"
    assert defaults["last_successor_task_event_id"] == "1"
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_ordering_gaps)"
        )
    } >= {
        "job_id",
        "predecessor_outbox_id",
        "successor_outbox_id",
        "kind",
        "predecessor_task_event_id",
        "successor_task_event_id",
        "predecessor_publication_event_id",
        "successor_publication_event_id",
    }
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(task_recovery_correction_gaps)"
        )
    } >= {"correction_id", "gap_id", "created_at"}
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA index_list(task_recovery_corrections)"
        )
        if row[2]
    } == {"uq_task_recovery_corrections_active_job"}
    assert {
        row[1] for row in conn.execute("PRAGMA table_info(jobs)")
    } >= {"projection_revision", "projection_state"}
    assert {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND name LIKE '%projection_revision%'"
        ).fetchall()
    } == set()


def test_v49_detects_reversals_and_aggregates_corrections_per_task(
    tmp_path: Path,
):
    conn, db_path, ids = _v46_recovery_database(
        tmp_path,
        "schema-46-projected-reversals.db",
    )
    owner_id = int(
        conn.execute(
            "SELECT owner_user_id FROM sessions WHERE id = ?",
            (ids["master_session_id"],),
        ).fetchone()[0]
    )
    second_session_id = int(
        conn.execute(
            "INSERT INTO sessions(title, owner_user_id) "
            "VALUES ('Second Task', ?)",
            (owner_id,),
        ).lastrowid
    )
    second_job_id = int(
        conn.execute(
            "INSERT INTO jobs(session_id, title, status, created_by, "
            "origin_master_session_id) "
            "VALUES (?, 'Ordered recovery', 'queued', ?, ?)",
            (
                second_session_id,
                owner_id,
                ids["master_session_id"],
            ),
        ).lastrowid
    )
    first_events = [
        _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=1,
        ),
        _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=2,
        ),
        _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=3,
        ),
        _recovery_task_event(
            conn,
            task_session_id=ids["task_session_id"],
            seq=4,
        ),
    ]
    second_events = [
        _recovery_task_event(
            conn,
            task_session_id=second_session_id,
            seq=1,
        ),
        _recovery_task_event(
            conn,
            task_session_id=second_session_id,
            seq=2,
        ),
    ]
    for index in (1, 0, 3, 2):
        _projected_v46_recovery(
            conn,
            job_id=ids["job_id"],
            task_event_id=first_events[index],
            master_session_id=ids["master_session_id"],
            recovery_json=_recovery_payload(ids["job_id"], index + 1),
        )
    for index in (0, 1):
        _projected_v46_recovery(
            conn,
            job_id=second_job_id,
            task_event_id=second_events[index],
            master_session_id=ids["master_session_id"],
            recovery_json=_recovery_payload(second_job_id, index + 1),
        )
    preserved = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, job_id, task_event_id, recovery_json, state, "
            "master_session_id, message_id, event_id, attempt_count, "
            "created_at, updated_at "
            "FROM task_recovery_outbox ORDER BY id"
        ).fetchall()
    ]

    assert run_migrations(conn, str(db_path)) == [47, 48, 49, 50, 51, 52, 53]
    assert run_migrations(conn, str(db_path)) == []
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT predecessor_task_event_id, successor_task_event_id, "
            "kind FROM task_recovery_ordering_gaps "
            "WHERE job_id = ? ORDER BY predecessor_task_event_id",
            (ids["job_id"],),
        ).fetchall()
    ] == [
        (first_events[0], first_events[1], "projected_reversal"),
        (first_events[2], first_events[3], "projected_reversal"),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gaps "
        "WHERE job_id = ?",
        (second_job_id,),
    ).fetchone()[0] == 0
    assert dict(
        conn.execute(
            "SELECT job_id, marker_kind, gap_count, first_task_event_id, "
            "last_task_event_id, first_successor_task_event_id, "
            "last_successor_task_event_id, state "
            "FROM task_recovery_corrections"
        ).fetchone()
    ) == {
        "job_id": ids["job_id"],
        "marker_kind": "aggregate",
        "gap_count": 2,
        "first_task_event_id": first_events[0],
        "last_task_event_id": first_events[2],
        "first_successor_task_event_id": first_events[1],
        "last_successor_task_event_id": first_events[3],
        "state": "pending",
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_correction_gaps"
    ).fetchone()[0] == 2
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT id, job_id, task_event_id, recovery_json, state, "
            "master_session_id, message_id, event_id, attempt_count, "
            "created_at, updated_at "
            "FROM task_recovery_outbox ORDER BY id"
        ).fetchall()
    ] == preserved
    with pytest.raises(
        sqlite3.IntegrityError,
        match="legacy recovery ordering gap record is immutable",
    ):
        conn.execute(
            "UPDATE task_recovery_ordering_gaps "
            "SET kind = 'unpublished_predecessor'"
        )


def test_v49_migration_is_atomic_and_idempotent(tmp_path: Path):
    conn, db_path, ids = _v46_recovery_database(
        tmp_path,
        "schema-46-reversal-interrupted.db",
    )
    predecessor_event_id = _recovery_task_event(
        conn,
        task_session_id=ids["task_session_id"],
        seq=1,
    )
    successor_event_id = _recovery_task_event(
        conn,
        task_session_id=ids["task_session_id"],
        seq=2,
    )
    _projected_v46_recovery(
        conn,
        job_id=ids["job_id"],
        task_event_id=successor_event_id,
        master_session_id=ids["master_session_id"],
        recovery_json=_recovery_payload(ids["job_id"], 2),
    )
    _projected_v46_recovery(
        conn,
        job_id=ids["job_id"],
        task_event_id=predecessor_event_id,
        master_session_id=ids["master_session_id"],
        recovery_json=_recovery_payload(ids["job_id"], 1),
    )
    through_v48 = [migration for migration in MIGRATIONS if migration[0] <= 48]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v48,
    ) == [47, 48]
    migration49 = next(entry[2] for entry in MIGRATIONS if entry[0] == 49)

    conn.execute("BEGIN")
    migration49(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gaps"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    conn.execute("ROLLBACK")
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gaps"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 0

    conn.execute("BEGIN")
    migration49(conn)
    conn.execute("COMMIT")
    conn.execute("BEGIN")
    migration49(conn)
    conn.execute("COMMIT")
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gaps"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1


def test_v50_preserves_multiple_delivered_v47_markers_before_v48(
    tmp_path: Path,
):
    conn, db_path, _, correction_ids = _v48_correction_database(
        tmp_path,
        "schema-48-delivered-corrections.db",
    )
    for index, correction_id in enumerate(correction_ids, start=1):
        _deliver_v47_correction(
            conn,
            correction_id=correction_id,
            label=str(index),
        )
    before_corrections = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, job_id, successor_outbox_id, gap_count, "
            "first_task_event_id, last_task_event_id, state, "
            "master_session_id, message_id, event_id, failure_code, "
            "attempt_count, created_at, updated_at "
            "FROM task_recovery_corrections ORDER BY id"
        ).fetchall()
    ]
    before_history = [
        tuple(row)
        for row in conn.execute(
            "SELECT event.id, event.payload, message.id, message.content "
            "FROM events AS event "
            "JOIN messages AS message "
            "ON message.id = json_extract(event.payload, '$.message_id') "
            "WHERE event.type = "
            "'master.task.recovery_history_corrected' "
            "ORDER BY event.id"
        ).fetchall()
    ]
    conn.commit()

    init_db(conn)
    assert run_migrations(conn, str(db_path)) == [49, 50, 51, 52, 53]
    assert run_migrations(conn, str(db_path)) == []
    after_corrections = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, job_id, successor_outbox_id, gap_count, "
            "first_task_event_id, last_task_event_id, state, "
            "master_session_id, message_id, event_id, failure_code, "
            "attempt_count, created_at, updated_at "
            "FROM task_recovery_corrections ORDER BY id"
        ).fetchall()
    ]
    assert after_corrections == before_corrections
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT marker_kind, state FROM task_recovery_corrections "
            "ORDER BY id"
        ).fetchall()
    ] == [
        ("legacy_partial", "projected"),
        ("legacy_partial", "projected"),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_correction_gaps"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections "
        "WHERE state != 'projected'"
    ).fetchone()[0] == 0
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT event.id, event.payload, message.id, message.content "
            "FROM events AS event "
            "JOIN messages AS message "
            "ON message.id = json_extract(event.payload, '$.message_id') "
            "WHERE event.type = "
            "'master.task.recovery_history_corrected' "
            "ORDER BY event.id"
        ).fetchall()
    ] == before_history
    with pytest.raises(
        sqlite3.IntegrityError,
        match="delivered recovery correction is immutable",
    ):
        conn.execute(
            "UPDATE task_recovery_corrections SET gap_count = 9 "
            "WHERE id = ?",
            (correction_ids[0],),
        )


def test_v50_recovers_multiple_delivered_markers_from_v48_history(
    tmp_path: Path,
):
    conn, db_path, _, correction_ids = _v48_correction_database(
        tmp_path,
        "schema-49-delivered-corrections.db",
    )
    replacement_id = correction_ids[1] + 37
    conn.execute(
        "UPDATE task_recovery_corrections SET id = ? WHERE id = ?",
        (replacement_id, correction_ids[1]),
    )
    correction_ids[1] = replacement_id
    for index, correction_id in enumerate(correction_ids, start=1):
        _deliver_v47_correction(
            conn,
            correction_id=correction_id,
            label=str(index),
        )
    before_history = [
        tuple(row)
        for row in conn.execute(
            "SELECT event.id, event.payload, message.id, message.content "
            "FROM events AS event "
            "JOIN messages AS message "
            "ON message.id = json_extract(event.payload, '$.message_id') "
            "WHERE event.type = "
            "'master.task.recovery_history_corrected' "
            "ORDER BY event.id"
        ).fetchall()
    ]
    conn.commit()
    through_v49 = [
        migration for migration in MIGRATIONS if migration[0] <= 49
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v49,
    ) == [49]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    assert [
        int(row["correction_id"])
        for row in conn.execute(
            "SELECT correction_id FROM "
            "task_recovery_delivered_marker_staging "
            "ORDER BY correction_id"
        ).fetchall()
    ] == sorted(correction_ids)

    assert run_migrations(conn, str(db_path)) == [50, 51, 52, 53]
    assert [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM task_recovery_corrections ORDER BY id"
        ).fetchall()
    ] == sorted(correction_ids)
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT marker_kind, state, gap_count "
            "FROM task_recovery_corrections ORDER BY event_id"
        ).fetchall()
    ] == [
        ("legacy_partial", "projected", 1),
        ("legacy_partial", "projected", 1),
    ]
    assert conn.execute(
        "SELECT COUNT(DISTINCT correction_id) "
        "FROM task_recovery_correction_gaps"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(DISTINCT gap_id) "
        "FROM task_recovery_correction_gaps"
    ).fetchone()[0] == 2
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT event.id, event.payload, message.id, message.content "
            "FROM events AS event "
            "JOIN messages AS message "
            "ON message.id = json_extract(event.payload, '$.message_id') "
            "WHERE event.type = "
            "'master.task.recovery_history_corrected' "
            "ORDER BY event.id"
        ).fetchall()
    ] == before_history


def test_v50_records_pre_staging_identity_loss_without_inventing_markers(
    tmp_path: Path,
):
    conn, db_path, _, correction_ids = _v48_correction_database(
        tmp_path,
        "schema-49-unstaged-delivered-corrections.db",
    )
    delivered_links = [
        _deliver_v47_correction(
            conn,
            correction_id=correction_id,
            label=str(index),
        )
        for index, correction_id in enumerate(correction_ids, start=1)
    ]
    through_v49 = [
        migration for migration in MIGRATIONS if migration[0] <= 49
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v49,
    ) == [49]
    conn.execute(
        "DROP TRIGGER task_recovery_delivered_marker_staging_immutable"
    )
    conn.execute(
        "DROP TRIGGER "
        "task_recovery_delivered_marker_staging_delete_immutable"
    )
    conn.execute(
        "DROP TABLE task_recovery_delivered_marker_staging_gaps"
    )
    conn.execute("DROP TABLE task_recovery_delivered_marker_staging")

    assert run_migrations(conn, str(db_path)) == [50, 51, 52, 53]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 0
    losses = [
        dict(row)
        for row in conn.execute(
            "SELECT observed_correction_id, message_id, event_id, reason "
            "FROM task_recovery_legacy_losses ORDER BY event_id"
        ).fetchall()
    ]
    assert len(losses) == 2
    assert [row["reason"] for row in losses] == [
        "v48_identity_unavailable",
        "v48_identity_unavailable",
    ]
    assert [
        (int(row["message_id"]), int(row["event_id"]))
        for row in losses
    ] == delivered_links
    assert conn.execute(
        "SELECT COUNT(DISTINCT gap_id) "
        "FROM task_recovery_legacy_loss_gaps"
    ).fetchone()[0] == 2


@pytest.mark.parametrize("delete_kind", ["job", "task_session"])
def test_recovery_audit_identity_survives_task_source_deletion(
    tmp_path: Path,
    delete_kind: str,
):
    conn, db_path, ids, correction_ids = _v48_correction_database(
        tmp_path,
        f"recovery-history-{delete_kind}.db",
    )
    delivered_links = [
        _deliver_v47_correction(
            conn,
            correction_id=correction_id,
            label=str(index),
        )
        for index, correction_id in enumerate(correction_ids, start=1)
    ]
    init_db(conn)
    assert run_migrations(conn, str(db_path)) == [49, 50, 51, 52, 53]
    source_before = [
        (int(row["id"]), int(row["task_event_id"]))
        for row in conn.execute(
            "SELECT id, task_event_id FROM task_recovery_outbox "
            "WHERE job_id = ? ORDER BY id",
            (ids["job_id"],),
        ).fetchall()
    ]
    coverage_before = [
        tuple(row)
        for row in conn.execute(
            "SELECT correction_id, gap_id, created_at "
            "FROM task_recovery_correction_gaps "
            "ORDER BY correction_id, gap_id"
        ).fetchall()
    ]
    if delete_kind == "job":
        conn.execute("DELETE FROM jobs WHERE id = ?", (ids["job_id"],))
    else:
        conn.execute(
            "DELETE FROM sessions WHERE id = ?",
            (ids["task_session_id"],),
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gaps"
    ).fetchone()[0] == 0
    assert [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM task_recovery_correction_history ORDER BY id"
        ).fetchall()
    ] == sorted(correction_ids)
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT correction_id, gap_id, created_at "
            "FROM task_recovery_correction_gap_history "
            "ORDER BY correction_id, gap_id"
        ).fetchall()
    ] == coverage_before
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_ordering_gap_history"
    ).fetchone()[0] == 2
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT message_id, event_id "
            "FROM task_recovery_correction_history ORDER BY event_id"
        ).fetchall()
    ] == delivered_links
    assert all(
        conn.execute(
            "SELECT 1 FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        and conn.execute(
            "SELECT 1 FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        for message_id, event_id in delivered_links
    )
    assert [
        int(row["correction_id"])
        for row in conn.execute(
            "SELECT correction_id FROM "
            "task_recovery_delivered_marker_staging "
            "ORDER BY correction_id"
        ).fetchall()
    ] == sorted(correction_ids)
    assert dict(
        conn.execute(
            "SELECT job_id, task_session_id, master_session_id, "
            "first_task_event_id, last_task_event_id, "
            "first_recovery_outbox_id, last_recovery_outbox_id, "
            "capture_source, deletion_source "
            "FROM task_recovery_history_tombstones WHERE job_id = ?",
            (ids["job_id"],),
        ).fetchone()
    ) == {
        "job_id": ids["job_id"],
        "task_session_id": ids["task_session_id"],
        "master_session_id": ids["master_session_id"],
        "first_task_event_id": min(pair[1] for pair in source_before),
        "last_task_event_id": max(pair[1] for pair in source_before),
        "first_recovery_outbox_id": min(pair[0] for pair in source_before),
        "last_recovery_outbox_id": max(pair[0] for pair in source_before),
        "capture_source": (
            "job" if delete_kind == "job" else "session"
        ),
        "deletion_source": (
            "job" if delete_kind == "job" else "task_event"
        ),
    }
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT recovery_outbox_id, task_event_id, "
            "task_session_id, master_session_id "
            "FROM task_recovery_source_history "
            "WHERE job_id = ? ORDER BY recovery_outbox_id",
            (ids["job_id"],),
        ).fetchall()
    ] == [
        (
            outbox_id,
            task_event_id,
            ids["task_session_id"],
            ids["master_session_id"],
        )
        for outbox_id, task_event_id in source_before
    ]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(
        sqlite3.IntegrityError,
        match="archived recovery correction is immutable",
    ):
        conn.execute(
            "UPDATE task_recovery_correction_history "
            "SET gap_count = gap_count + 1 WHERE id = ?",
            (correction_ids[0],),
        )


def test_recovery_source_identity_survives_event_and_later_cascades(
    tmp_path: Path,
):
    conn, db_path, ids, _ = _v48_correction_database(
        tmp_path,
        "recovery-source-event-cascade.db",
    )
    init_db(conn)
    assert run_migrations(conn, str(db_path)) == [49, 50, 51, 52, 53]
    source_before = [
        (int(row["id"]), int(row["task_event_id"]))
        for row in conn.execute(
            "SELECT id, task_event_id FROM task_recovery_outbox "
            "WHERE job_id = ? ORDER BY id",
            (ids["job_id"],),
        ).fetchall()
    ]

    conn.execute(
        "DELETE FROM events WHERE id = ?",
        (source_before[0][1],),
    )
    conn.execute(
        "DELETE FROM events WHERE id = ?",
        (source_before[0][1],),
    )
    tombstone = dict(
        conn.execute(
            "SELECT * FROM task_recovery_history_tombstones "
            "WHERE job_id = ?",
            (ids["job_id"],),
        ).fetchone()
    )
    source_history = [
        tuple(row)
        for row in conn.execute(
            "SELECT recovery_outbox_id, task_event_id, "
            "task_session_id, master_session_id "
            "FROM task_recovery_source_history "
            "WHERE job_id = ? ORDER BY recovery_outbox_id",
            (ids["job_id"],),
        ).fetchall()
    ]
    assert tombstone["task_session_id"] == ids["task_session_id"]
    assert tombstone["master_session_id"] == ids["master_session_id"]
    assert tombstone["capture_source"] == "event"
    assert tombstone["deletion_source"] == "task_event"
    assert source_history == [
        (
            outbox_id,
            task_event_id,
            ids["task_session_id"],
            ids["master_session_id"],
        )
        for outbox_id, task_event_id in source_before
    ]

    conn.execute(
        "DELETE FROM sessions WHERE id = ?",
        (ids["task_session_id"],),
    )
    conn.execute("DELETE FROM jobs WHERE id = ?", (ids["job_id"],))
    assert dict(
        conn.execute(
            "SELECT * FROM task_recovery_history_tombstones "
            "WHERE job_id = ?",
            (ids["job_id"],),
        ).fetchone()
    ) == tombstone
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT recovery_outbox_id, task_event_id, "
            "task_session_id, master_session_id "
            "FROM task_recovery_source_history "
            "WHERE job_id = ? ORDER BY recovery_outbox_id",
            (ids["job_id"],),
        ).fetchall()
    ] == source_history
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v52_completes_partial_recovery_tombstone_atomically(
    tmp_path: Path,
):
    conn, db_path, ids, _ = _v48_correction_database(
        tmp_path,
        "recovery-source-partial-tombstone.db",
    )
    init_db(conn)
    through_v51 = [
        migration for migration in MIGRATIONS if migration[0] <= 51
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v51,
    ) == [49, 50, 51]
    source_before = [
        (int(row["id"]), int(row["task_event_id"]))
        for row in conn.execute(
            "SELECT id, task_event_id FROM task_recovery_outbox "
            "WHERE job_id = ? ORDER BY id",
            (ids["job_id"],),
        ).fetchall()
    ]
    conn.execute(
        "INSERT INTO task_recovery_history_tombstones("
        "job_id, task_session_id, master_session_id, deletion_source"
        ") VALUES (?, NULL, NULL, 'task_event')",
        (ids["job_id"],),
    )
    migration52 = next(entry[2] for entry in MIGRATIONS if entry[0] == 52)

    conn.execute("BEGIN")
    migration52(conn)
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (ids["job_id"],),
    ).fetchone()[0] == ids["task_session_id"]
    conn.execute("ROLLBACK")
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (ids["job_id"],),
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_source_history "
        "WHERE job_id = ?",
        (ids["job_id"],),
    ).fetchone()[0] == 0

    assert run_migrations(conn, str(db_path)) == [52, 53]
    assert run_migrations(conn, str(db_path)) == []
    tombstone = dict(
        conn.execute(
            "SELECT job_id, task_session_id, master_session_id, "
            "first_task_event_id, last_task_event_id, "
            "first_recovery_outbox_id, last_recovery_outbox_id, "
            "capture_source, deletion_source "
            "FROM task_recovery_history_tombstones WHERE job_id = ?",
            (ids["job_id"],),
        ).fetchone()
    )
    assert tombstone == {
        "job_id": ids["job_id"],
        "task_session_id": ids["task_session_id"],
        "master_session_id": ids["master_session_id"],
        "first_task_event_id": min(pair[1] for pair in source_before),
        "last_task_event_id": max(pair[1] for pair in source_before),
        "first_recovery_outbox_id": min(pair[0] for pair in source_before),
        "last_recovery_outbox_id": max(pair[0] for pair in source_before),
        "capture_source": "outbox",
        "deletion_source": "task_event",
    }
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT recovery_outbox_id, task_event_id, "
            "task_session_id, master_session_id "
            "FROM task_recovery_source_history "
            "WHERE job_id = ? ORDER BY recovery_outbox_id",
            (ids["job_id"],),
        ).fetchall()
    ] == [
        (
            outbox_id,
            task_event_id,
            ids["task_session_id"],
            ids["master_session_id"],
        )
        for outbox_id, task_event_id in source_before
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_session_identity_losses "
        "WHERE job_id = ?",
        (ids["job_id"],),
    ).fetchone()[0] == 0
    with pytest.raises(
        sqlite3.IntegrityError,
        match="recovery history tombstone identity is immutable",
    ):
        conn.execute(
            "UPDATE task_recovery_history_tombstones "
            "SET task_session_id = task_session_id + 1 "
            "WHERE job_id = ?",
            (ids["job_id"],),
        )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v53_repairs_v51_node_session_guess_from_event_provenance(
    tmp_path: Path,
):
    conn, db_path, ids, _ = _v48_correction_database(
        tmp_path,
        "recovery-source-v51-node-guess.db",
    )
    init_db(conn)
    through_v52 = [
        migration for migration in MIGRATIONS if migration[0] <= 52
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v52,
    ) == [49, 50, 51, 52]
    owner_id = int(
        conn.execute(
            "SELECT owner_user_id FROM sessions WHERE id = ?",
            (ids["task_session_id"],),
        ).fetchone()[0]
    )
    node_session_id = int(
        conn.execute(
            "INSERT INTO sessions(title, owner_user_id, job_id) "
            "VALUES ('Graph node', ?, ?)",
            (owner_id, ids["job_id"]),
        ).lastrowid
    )
    conn.execute(
        "UPDATE jobs SET session_id = NULL WHERE id = ?",
        (ids["job_id"],),
    )
    conn.execute(
        "DELETE FROM sessions WHERE id = ?",
        (node_session_id,),
    )
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (ids["job_id"],),
    ).fetchone()[0] == node_session_id

    assert run_migrations(conn, str(db_path)) == [53]
    assert run_migrations(conn, str(db_path)) == []
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (ids["job_id"],),
    ).fetchone()[0] == ids["task_session_id"]
    assert dict(
        conn.execute(
            "SELECT job_id, reason, observed_task_session_id "
            "FROM task_recovery_session_identity_losses "
            "WHERE job_id = ?",
            (ids["job_id"],),
        ).fetchone()
    ) == {
        "job_id": ids["job_id"],
        "reason": "unverified_v51_session_discarded",
        "observed_task_session_id": node_session_id,
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v53_never_promotes_mixed_graph_sessions_to_task_identity(
    tmp_path: Path,
):
    db_path = tmp_path / "recovery-source-v52-mixed-nodes.db"
    conn = connect(db_path)
    init_db(conn)
    through_v52 = [
        migration for migration in MIGRATIONS if migration[0] <= 52
    ]
    run_migrations(conn, str(db_path), migrations=through_v52)
    owner_id = int(
        conn.execute(
            "INSERT INTO users(username, os_user) "
            "VALUES ('mixed-owner', 'mixed-owner')"
        ).lastrowid
    )
    job_id = int(
        conn.execute(
            "INSERT INTO jobs(title, status, created_by) "
            "VALUES ('Mixed graph recovery', 'queued', ?)",
            (owner_id,),
        ).lastrowid
    )
    node_session_ids = [
        int(
            conn.execute(
                "INSERT INTO sessions(title, owner_user_id, job_id) "
                "VALUES (?, ?, ?)",
                (f"Node {index}", owner_id, job_id),
            ).lastrowid
        )
        for index in (1, 2)
    ]
    source_ids: list[tuple[int, int, int]] = []
    for seq, session_id in enumerate(node_session_ids, start=1):
        task_event_id = int(
            conn.execute(
                "INSERT INTO events(session_id, seq, type, payload) "
                "VALUES (?, ?, 'job.update', '{}')",
                (session_id, seq),
            ).lastrowid
        )
        outbox_id = int(
            conn.execute(
                "INSERT INTO task_recovery_outbox("
                "job_id, task_event_id, recovery_json"
                ") VALUES (?, ?, '{}')",
                (job_id, task_event_id),
            ).lastrowid
        )
        source_ids.append((outbox_id, task_event_id, session_id))

    conn.execute(
        "DELETE FROM sessions WHERE id = ?",
        (node_session_ids[0],),
    )
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0] == node_session_ids[0]

    assert run_migrations(conn, str(db_path)) == [53]
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0] is None
    assert dict(
        conn.execute(
            "SELECT reason, observed_task_session_id "
            "FROM task_recovery_session_identity_losses "
            "WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    ) == {
        "reason": "unverified_v51_session_discarded",
        "observed_task_session_id": node_session_ids[0],
    }
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT recovery_outbox_id, task_event_id, task_session_id "
            "FROM task_recovery_source_history "
            "WHERE job_id = ? ORDER BY recovery_outbox_id",
            (job_id,),
        ).fetchall()
    ] == source_ids

    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    assert conn.execute(
        "SELECT task_session_id FROM task_recovery_history_tombstones "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_session_identity_losses "
        "WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0] == 1
    assert run_migrations(conn, str(db_path)) == []
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v51_recovery_history_detachment_is_atomic_and_idempotent(
    tmp_path: Path,
):
    conn, db_path, _, correction_ids = _v48_correction_database(
        tmp_path,
        "recovery-history-v50-atomic.db",
    )
    _deliver_v47_correction(
        conn,
        correction_id=correction_ids[0],
        label="atomic",
    )
    through_v50 = [
        migration for migration in MIGRATIONS if migration[0] <= 50
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v50,
    ) == [49, 50]
    conn.execute("DROP TRIGGER IF EXISTS jobs_archive_recovery_history")
    conn.execute(
        "DROP TRIGGER IF EXISTS task_recovery_outbox_archive_history"
    )
    conn.commit()
    migration51 = next(entry[2] for entry in MIGRATIONS if entry[0] == 51)

    conn.execute("BEGIN")
    migration51(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
        "AND name IN ("
        "'jobs_archive_recovery_history', "
        "'task_recovery_outbox_archive_history'"
        ")"
    ).fetchone()[0] == 2
    conn.execute("ROLLBACK")
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
        "AND name IN ("
        "'jobs_archive_recovery_history', "
        "'task_recovery_outbox_archive_history'"
        ")"
    ).fetchone()[0] == 0

    for _ in range(2):
        conn.execute("BEGIN")
        migration51(conn)
        conn.execute("COMMIT")
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
        "AND name IN ("
        "'jobs_archive_recovery_history', "
        "'task_recovery_outbox_archive_history'"
        ")"
    ).fetchone()[0] == 2


def test_v50_aggregates_only_uncovered_gaps_atomically(
    tmp_path: Path,
):
    conn, db_path, _, correction_ids = _v48_correction_database(
        tmp_path,
        "schema-48-delivered-plus-pending.db",
    )
    message_id, event_id = _deliver_v47_correction(
        conn,
        correction_id=correction_ids[1],
        label="delivered",
    )
    message_before = tuple(
        conn.execute(
            "SELECT content, author FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    )
    event_before = str(
        conn.execute(
            "SELECT payload FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()[0]
    )
    conn.commit()
    init_db(conn)
    through_v49 = [
        migration for migration in MIGRATIONS if migration[0] <= 49
    ]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=through_v49,
    ) == [49]
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    migration50 = next(entry[2] for entry in MIGRATIONS if entry[0] == 50)

    conn.execute("BEGIN")
    migration50(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections "
        "WHERE marker_kind = 'aggregate' AND state = 'pending'"
    ).fetchone()[0] == 1
    conn.execute("ROLLBACK")
    assert conn.execute(
        "SELECT COUNT(*) FROM task_recovery_corrections"
    ).fetchone()[0] == 1
    assert "marker_kind = 'legacy_partial'" not in " ".join(
        str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'task_recovery_corrections'"
            ).fetchone()[0]
        ).lower().split()
    )

    conn.execute("BEGIN")
    migration50(conn)
    conn.execute("COMMIT")
    conn.execute("BEGIN")
    migration50(conn)
    conn.execute("COMMIT")
    markers = [
        dict(row)
        for row in conn.execute(
            "SELECT id, marker_kind, state, gap_count, "
            "first_task_event_id, last_task_event_id "
            "FROM task_recovery_corrections ORDER BY id"
        ).fetchall()
    ]
    assert [marker["marker_kind"] for marker in markers] == [
        "legacy_partial",
        "aggregate",
    ]
    assert [marker["state"] for marker in markers] == [
        "projected",
        "pending",
    ]
    assert [marker["gap_count"] for marker in markers] == [1, 1]
    assert conn.execute(
        "SELECT COUNT(DISTINCT gap_id) "
        "FROM task_recovery_correction_gaps"
    ).fetchone()[0] == 2
    assert tuple(
        conn.execute(
            "SELECT content, author FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    ) == message_before
    assert conn.execute(
        "SELECT payload FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()[0] == event_before


def test_schema_31_to_35_is_idempotent_and_preserves_replay_contract(
    tmp_path: Path,
):
    db_path = tmp_path / "schema-31.db"
    conn = connect(db_path)
    init_db(conn)
    run_migrations(conn, str(db_path))
    conn.execute("DELETE FROM schema_migrations WHERE version >= 32")
    conn.execute("DROP TABLE master_tool_calls")
    conn.execute("DROP TABLE master_projections")
    conn.execute("DROP TABLE graph_states")
    conn.execute("DROP TRIGGER jobs_ops_done_knowledge_rebuild")
    conn.execute("DROP TABLE knowledge_rebuild_intents")

    assert run_migrations(conn, str(db_path)) == [
        32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48,
        49, 50, 51, 52, 53,
    ]
    assert run_migrations(conn, str(db_path)) == []
    assert current_version(conn) == 53
    assert {
        row[1] for row in conn.execute("PRAGMA table_info(master_tool_calls)")
    } == {
        "id",
        "master_session_id",
        "turn_root_run_id",
        "envelope_hash",
        "tool_name",
        "status",
        "result_json",
        "created_at",
        "completed_at",
    }
    assert {
        row[1] for row in conn.execute("PRAGMA table_info(master_projections)")
    } >= {
        "owner_user_id",
        "master_session_id",
        "projection_key",
        "projection_type",
        "source_table",
        "source_id",
        "message_id",
        "event_id",
    }
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(master_message_context)"
        )
    } >= {
        "message_id",
        "focus_mode",
        "focus_container_id",
        "target_mode",
        "target_container_id",
        "target_area_id",
    }


def test_schema_32_drift_fails_and_rolls_back_version_record(tmp_path: Path):
    db_path = tmp_path / "drifted-schema-31.db"
    conn = connect(db_path)
    init_db(conn)
    run_migrations(conn, str(db_path))
    conn.execute("DELETE FROM schema_migrations WHERE version >= 32")
    conn.execute("DROP TABLE master_tool_calls")
    conn.execute(
        "CREATE TABLE master_tool_calls("
        "id INTEGER PRIMARY KEY, master_session_id INTEGER NOT NULL)"
    )

    with pytest.raises(
        MasterPersistenceError,
        match="ledger schema is incomplete",
    ):
        run_migrations(conn, str(db_path))

    assert current_version(conn) == 31
    assert {
        row[1] for row in conn.execute("PRAGMA table_info(master_tool_calls)")
    } == {"id", "master_session_id"}


def test_schema_33_rejects_incomplete_projection_state(tmp_path: Path):
    db_path = tmp_path / "incomplete-projection.db"
    conn = connect(db_path)
    init_db(conn)
    run_migrations(conn, str(db_path))
    owner_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('projection-owner', 'owner')"
    ).lastrowid
    profile_id = conn.execute(
        "INSERT INTO profiles(user_id, slug, name, hermes_home, system_kind) "
        "VALUES (?, 'master', 'Master', '/tmp/master', 'master')",
        (owner_id,),
    ).lastrowid
    session_id = conn.execute(
        "INSERT INTO sessions(title, owner_user_id, profile_id, mode) "
        "VALUES ('Master', ?, ?, 'master')",
        (owner_id, profile_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO master_projections("
        "owner_user_id, master_session_id, projection_key, projection_type, "
        "source_table, source_id, payload_json"
        ") VALUES (?, ?, 'partial', 'master.attention.required', "
        "'attention_items', 999, 'not-json')",
        (owner_id, session_id),
    )
    conn.execute("DELETE FROM schema_migrations WHERE version >= 33")

    with pytest.raises(RuntimeError, match="projection ledger"):
        run_migrations(conn, str(db_path))

    assert current_version(conn) == 32


def test_applies_pending_once_then_idempotent(tmp_path: Path):
    db = tmp_path / "h.db"
    conn = connect(db)
    migs = [(1, "add foo", _add_foo)]
    assert run_migrations(conn, str(db), migrations=migs) == [1]
    assert current_version(conn) == 1
    # foo table now exists
    assert conn.execute("SELECT COUNT(*) FROM foo").fetchone()[0] == 0
    # second run does nothing (no re-apply)
    assert run_migrations(conn, str(db), migrations=migs) == []


def test_backup_created_and_existing_data_preserved(tmp_path: Path):
    db = tmp_path / "h.db"
    conn = connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    conn.execute("INSERT INTO users(username) VALUES ('alice')")

    migs = [(1, "add nickname", _add_users_nickname)]
    run_migrations(conn, str(db), migrations=migs)

    # a backup snapshot was written before migrating
    backups = list((tmp_path / "backups").glob("*.pre-migration-*.db"))
    assert len(backups) == 1
    # original row survived and the new column exists
    row = conn.execute("SELECT username, nickname FROM users").fetchone()
    assert row["username"] == "alice"
    assert row["nickname"] is None
    # the backup still has the pre-migration shape (no nickname column)
    bconn = connect(backups[0])
    bcols = {r[1] for r in bconn.execute("PRAGMA table_info(users)").fetchall()}
    assert "nickname" not in bcols
    assert bconn.execute("SELECT username FROM users").fetchone()["username"] == "alice"


def test_failed_migration_rolls_back_and_does_not_record(tmp_path: Path):
    db = tmp_path / "h.db"
    conn = connect(db)

    def _boom(c):
        c.execute("CREATE TABLE half (id INTEGER)")
        raise RuntimeError("kaboom")

    try:
        run_migrations(conn, str(db), migrations=[(1, "boom", _boom)])
        assert False, "should have raised"
    except RuntimeError:
        pass
    # version unchanged, partial table rolled back
    assert current_version(conn) == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='half'").fetchone() is None


def test_v4_adds_runs_kind(tmp_path: Path):
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(tmp_path / "m.db")
    conn.row_factory = _sqlite3.Row
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY)")  # pre-kind shape
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")   # for earlier migrations
    conn.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")    # later migrations ALTER sessions
    applied = run_migrations(conn, str(tmp_path / "m.db"))
    assert 4 in applied
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    assert "kind" in cols
    conn.execute("INSERT INTO runs DEFAULT VALUES")
    assert conn.execute("SELECT kind FROM runs").fetchone()["kind"] == "chat"


def test_v5_relabels_private_projects(tmp_path: Path):
    conn = connect(tmp_path / "h.db")
    conn.executescript("""
      CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, visibility TEXT);
      INSERT INTO projects(name, visibility) VALUES ('carol (private)', 'private');
      INSERT INTO projects(name, visibility) VALUES ('Team Roadmap', 'shared');
    """)
    # earlier migrations need these tables to exist
    conn.executescript("CREATE TABLE messages(id INTEGER PRIMARY KEY); CREATE TABLE profiles(id INTEGER PRIMARY KEY); CREATE TABLE runs(id INTEGER PRIMARY KEY); CREATE TABLE sessions(id INTEGER PRIMARY KEY);")
    applied = run_migrations(conn, str(tmp_path / "h.db"))
    assert 5 in applied
    names = {r[0] for r in conn.execute("SELECT name FROM projects").fetchall()}
    assert "carol (personal)" in names      # relabelled
    assert "Team Roadmap" in names          # untouched
    assert not any("(private)" in n for n in names)


def test_v8_adds_sessions_manual_title(tmp_path: Path):
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(tmp_path / "m.db")
    conn.row_factory = _sqlite3.Row
    conn.executescript("""
      CREATE TABLE messages(id INTEGER PRIMARY KEY);
      CREATE TABLE profiles(id INTEGER PRIMARY KEY);
      CREATE TABLE runs(id INTEGER PRIMARY KEY);
      CREATE TABLE sessions(id INTEGER PRIMARY KEY, title TEXT NOT NULL);
    """)
    applied = run_migrations(conn, str(tmp_path / "m.db"))
    assert 8 in applied
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "manual_title" in cols
    conn.execute("INSERT INTO sessions(title) VALUES ('s')")
    assert conn.execute("SELECT manual_title FROM sessions").fetchone()["manual_title"] == 0


def test_v10_drops_project_members(tmp_path: Path):
    conn = connect(tmp_path / "m.db")
    conn.executescript("""
      CREATE TABLE messages(id INTEGER PRIMARY KEY);
      CREATE TABLE profiles(id INTEGER PRIMARY KEY);
      CREATE TABLE runs(id INTEGER PRIMARY KEY);
      CREATE TABLE sessions(id INTEGER PRIMARY KEY);
      CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT, visibility TEXT);
      CREATE TABLE project_members(project_id INTEGER, user_id INTEGER, role TEXT);
    """)
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_members'").fetchone()
    applied = run_migrations(conn, str(tmp_path / "m.db"))
    assert 10 in applied
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_members'").fetchone() is None


def test_migration_14_drops_dead_sessions_acp_session_id(tmp_path):
    """The legacy sessions.acp_session_id column is dropped; agent_sessions is the
    authoritative ACP-session store."""
    from proxima_api.db import connect, init_db
    from proxima_api.migrations import run_migrations

    # Simulate an old install that still has the dead column.
    db_path = tmp_path / "old.db"
    conn = connect(db_path)
    init_db(conn, [])
    conn.execute("ALTER TABLE sessions ADD COLUMN acp_session_id TEXT")

    # No migrations are recorded yet after init_db, so this runs 1..14 fresh;
    # migration 14 drops the column we just simulated an old install having.
    run_migrations(conn, str(db_path))

    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "acp_session_id" not in cols
    # agent_sessions (the real store) is untouched.
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_sessions'").fetchone()


def test_migration_15_adds_messages_run_id_fk_preserving_data(tmp_path):
    """messages.run_id becomes a real FK (ON DELETE SET NULL) via table rebuild,
    without losing rows or the inbound message_reviews reference."""
    from proxima_api.db import connect, init_db
    from proxima_api.migrations import run_migrations

    db_path = tmp_path / "m.db"
    conn = connect(db_path)
    init_db(conn, [])
    conn.execute("INSERT INTO users(username, os_user) VALUES ('u','u')")
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.execute("INSERT INTO sessions(title, owner_user_id) VALUES ('s', ?)", (uid,))
    sid = conn.execute("SELECT id FROM sessions").fetchone()["id"]
    conn.execute("INSERT INTO runs(session_id, user_id, prompt) VALUES (?,?,'p')", (sid, uid))
    rid = conn.execute("SELECT id FROM runs").fetchone()["id"]
    conn.execute("INSERT INTO messages(session_id, role, content, run_id) VALUES (?,'assistant','hi',?)", (sid, rid))
    mid = conn.execute("SELECT id FROM messages").fetchone()["id"]
    conn.execute("INSERT INTO message_reviews(source_message_id, session_id, mode) VALUES (?,?,'validate')", (mid, sid))

    run_migrations(conn, str(db_path))

    # FK exists, no integrity violations, rows + inbound reference preserved.
    assert any(r[3] == "run_id" and r[6] == "SET NULL" for r in conn.execute("PRAGMA foreign_key_list(messages)").fetchall())
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT 1 FROM messages WHERE id=?", (mid,)).fetchone() is not None
    assert conn.execute("SELECT 1 FROM message_reviews WHERE source_message_id=?", (mid,)).fetchone() is not None
    # the FK behaves: deleting the run nulls the pointer instead of dangling it.
    conn.execute("DELETE FROM runs WHERE id=?", (rid,))
    assert conn.execute("SELECT run_id FROM messages WHERE id=?", (mid,)).fetchone()["run_id"] is None


def test_sessions_pointer_fks_enforced_after_migration(tmp_path):
    """After the full migration chain, sessions.job_id/workflow_id are real FKs
    (ON DELETE SET NULL), task_id + the tasks table are gone, and integrity holds."""
    from proxima_api.db import connect, init_db
    from proxima_api.migrations import run_migrations

    db_path = tmp_path / "m.db"
    conn = connect(db_path)
    init_db(conn, [])
    run_migrations(conn, str(db_path))

    fks = {(r[3], r[2]) for r in conn.execute("PRAGMA foreign_key_list(sessions)").fetchall()}
    assert ("job_id", "jobs") in fks and ("workflow_id", "workflows") in fks
    assert "task_id" not in {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone() is None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    # FK behaves: deleting a workflow nulls a session's pointer instead of dangling it.
    conn.execute("INSERT INTO users(username, os_user) VALUES ('u','u')")
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    wid = conn.execute("INSERT INTO workflows(name) VALUES ('w')").lastrowid
    conn.execute("INSERT INTO sessions(title, owner_user_id, workflow_id) VALUES ('s',?,?)", (uid, wid))
    sid = conn.execute("SELECT id FROM sessions").fetchone()["id"]
    conn.execute("DELETE FROM workflows WHERE id=?", (wid,))
    assert conn.execute("SELECT workflow_id FROM sessions WHERE id=?", (sid,)).fetchone()["workflow_id"] is None


def test_v18_wraps_existing_projects_as_containers(tmp_path: Path):
    """Migration note (spec, binding): each existing flat project wraps in place —
    a root that is itself a repo registers as the sole code area '.', a repo-less
    project gets zero code areas, and every project gets its single ops area.
    No files are moved; a path missing on this machine is fine."""
    repo_root = tmp_path / "repoproj"
    (repo_root / ".git").mkdir(parents=True)
    flat_root = tmp_path / "flatproj"
    (flat_root / "artifacts").mkdir(parents=True)

    conn = connect(tmp_path / "m.db")
    conn.executescript("""
      CREATE TABLE messages(id INTEGER PRIMARY KEY);
      CREATE TABLE profiles(id INTEGER PRIMARY KEY);
      CREATE TABLE runs(id INTEGER PRIMARY KEY);
      CREATE TABLE sessions(id INTEGER PRIMARY KEY);
      CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT, visibility TEXT, path TEXT);
    """)
    conn.execute("INSERT INTO projects(name, visibility, path) VALUES ('r', 'private', ?)", (str(repo_root),))
    conn.execute("INSERT INTO projects(name, visibility, path) VALUES ('f', 'private', ?)", (str(flat_root),))
    conn.execute("INSERT INTO projects(name, visibility, path) VALUES ('gone', 'private', ?)", (str(tmp_path / "missing"),))

    applied = run_migrations(conn, str(tmp_path / "m.db"))
    assert 18 in applied

    def areas(pid: int) -> set[tuple[str, str, str]]:
        return {(r["kind"], r["rel_path"], r["source"]) for r in conn.execute(
            "SELECT kind, rel_path, source FROM project_areas WHERE project_id = ?", (pid,)).fetchall()}

    assert areas(1) == {("ops", ".", "auto"), ("code", ".", "auto")}
    assert areas(2) == {("ops", ".", "auto")}          # zero code areas is valid
    assert areas(3) == {("ops", ".", "auto")}          # missing path: nothing breaks


def test_v18_full_chain_enforces_single_ops_area(tmp_path: Path):
    import sqlite3 as _sqlite3
    conn = connect(tmp_path / "m.db")
    from proxima_api.db import init_db
    init_db(conn, [])
    run_migrations(conn, str(tmp_path / "m.db"))
    conn.execute("INSERT INTO users(username, os_user) VALUES ('u','u')")
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    pid = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES ('p','P','/tmp/nope',?)", (uid,)
    ).lastrowid
    conn.execute("INSERT INTO project_areas(project_id, kind, rel_path) VALUES (?, 'ops', '.')", (pid,))
    try:
        conn.execute("INSERT INTO project_areas(project_id, kind, rel_path) VALUES (?, 'ops', 'other')", (pid,))
        assert False, "second ops area must violate the partial unique index"
    except _sqlite3.IntegrityError:
        pass
    # Areas die with their project (ON DELETE CASCADE).
    conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
    assert conn.execute("SELECT COUNT(*) FROM project_areas WHERE project_id = ?", (pid,)).fetchone()[0] == 0


def test_v19_adds_job_target_binding_and_worktrees(tmp_path: Path):
    """Slice 2 (T1): existing jobs gain a NULL target_area_id (today's
    behavior - no repo binding) and the job_worktrees lifecycle table appears;
    a repo job's worktree row dies with its job (ON DELETE CASCADE)."""
    conn = connect(tmp_path / "m.db")
    conn.executescript("""
      CREATE TABLE messages(id INTEGER PRIMARY KEY);
      CREATE TABLE profiles(id INTEGER PRIMARY KEY);
      CREATE TABLE runs(id INTEGER PRIMARY KEY);
      CREATE TABLE sessions(id INTEGER PRIMARY KEY);
      CREATE TABLE jobs(id INTEGER PRIMARY KEY, title TEXT, status TEXT);
      INSERT INTO jobs(title, status) VALUES ('pre-existing', 'done');
    """)

    applied = run_migrations(conn, str(tmp_path / "m.db"))
    assert 19 in applied

    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "target_area_id" in cols
    assert conn.execute("SELECT target_area_id FROM jobs").fetchone()["target_area_id"] is None

    job_id = conn.execute("INSERT INTO jobs(title, status) VALUES ('repo job', 'running')").lastrowid
    conn.execute(
        "INSERT INTO job_worktrees(job_id, repo_path, worktree_path, branch, base_branch, base_commit) "
        "VALUES (?, '/tmp/repo', '/tmp/wt', 'proxima/job-2', 'main', 'abc')",
        (job_id,),
    )
    assert conn.execute("SELECT status FROM job_worktrees WHERE job_id = ?", (job_id,)).fetchone()["status"] == "active"
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    assert conn.execute("SELECT COUNT(*) FROM job_worktrees").fetchone()[0] == 0


def test_v27_moves_workflow_inputs_onto_trigger_node(tmp_path: Path):
    db_path = tmp_path / "m.db"
    conn = connect(db_path)
    init_db(conn, [])
    conn.execute("INSERT INTO users(username, os_user) VALUES ('owner', 'owner')")
    owner_id = conn.execute("SELECT id FROM users").fetchone()["id"]
    graph = {
        "nodes": [
            {
                "id": "work",
                "type": "task",
                "name": "Draft",
                "instruction": "Draft {{topic}}",
            }
        ],
        "edges": [],
    }
    declared_inputs = [
        {
            "id": "topic",
            "label": "Topic",
            "type": "text",
            "required": True,
        }
    ]
    workflow_id = conn.execute(
        "INSERT INTO workflows(name, graph, inputs, created_by) VALUES (?, ?, ?, ?)",
        ("Legacy graph workflow", json.dumps(graph), json.dumps(declared_inputs), owner_id),
    ).lastrowid

    applied = run_migrations(conn, str(db_path))

    assert 27 in applied
    stored = conn.execute(
        "SELECT graph, inputs FROM workflows WHERE id = ?", (workflow_id,)
    ).fetchone()
    migrated_graph = json.loads(stored["graph"])
    trigger = migrated_graph["nodes"][0]
    assert trigger["type"] == "trigger"
    assert trigger["trigger_kind"] == "manual"
    assert trigger["inputs"] == declared_inputs
    assert migrated_graph["edges"] == [{"from": trigger["id"], "to": "work"}]
    assert json.loads(stored["inputs"]) == declared_inputs


def test_v28_migrates_schema_27_alpha_data_without_rewriting_backbone_rows(
    tmp_path: Path,
):
    from proxima_api.container_registry import migrate_legacy_ops_containers

    db_path = tmp_path / "schema-27.db"
    conn = connect(db_path)
    init_db(conn, [])
    conn.execute("DROP TABLE container_ops_migrations")
    conn.execute("DROP TABLE container_registry")
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 28)],
    )

    root = tmp_path / "alpha-container"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "alpha.md").write_bytes(b"alpha history bytes")
    owner_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('alpha-container', 'Alpha Container', ?, ?)",
        (str(root), owner_id),
    ).lastrowid
    ops_area_id = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', '.', 'auto')",
        (container_id,),
    ).lastrowid
    profile_id = conn.execute(
        "INSERT INTO profiles(user_id, slug, name, hermes_home, system_kind) "
        "VALUES (?, 'alpha', 'Alpha', '/tmp/alpha-home', 'alpha')",
        (owner_id,),
    ).lastrowid
    session_id = conn.execute(
        "INSERT INTO sessions(title, owner_user_id, profile_id, mode) "
        "VALUES ('Alpha', ?, ?, 'alpha')",
        (owner_id, profile_id),
    ).lastrowid
    conn.execute("DROP INDEX IF EXISTS idx_jobs_origin_master")
    conn.execute(
        "ALTER TABLE jobs RENAME COLUMN origin_master_session_id TO alpha_session_id"
    )
    conn.execute(
        "CREATE INDEX idx_jobs_alpha "
        "ON jobs(alpha_session_id, status, created_at)"
    )
    job_id = conn.execute(
        "INSERT INTO jobs(title, project_id, created_by, alpha_session_id, target_area_id) "
        "VALUES ('Alpha task', ?, ?, ?, ?)",
        (container_id, owner_id, session_id, ops_area_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO attention_items(kind, title, source_key) "
        "VALUES ('alpha', 'Existing attention', 'alpha-existing')"
    )

    assert run_migrations(conn, str(db_path)) == [
        28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43,
        44,
        45, 46, 47, 48, 49, 50, 51, 52, 53
    ]
    assert current_version(conn) == 53
    assert migrate_legacy_ops_containers(conn) == {
        "complete": 1,
        "attention": 0,
    }

    assert conn.execute(
        "SELECT slug, path FROM projects WHERE id = ?", (container_id,)
    ).fetchone()["slug"] == "alpha-container"
    assert conn.execute(
        "SELECT origin_master_session_id, target_area_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["origin_master_session_id"] == session_id
    assert conn.execute(
        "SELECT mode FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()["mode"] == "master"
    assert conn.execute(
        "SELECT status FROM attention_items WHERE source_key = 'alpha-existing'"
    ).fetchone()["status"] == "open"
    assert conn.execute(
        "SELECT rel_path FROM project_areas WHERE id = ?", (ops_area_id,)
    ).fetchone()["rel_path"] == "ops"
    assert (root / "ops" / "wiki" / "alpha.md").read_bytes() == b"alpha history bytes"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v29_and_v30_add_safe_task_dependency_contracts_to_schema_28(
    tmp_path: Path,
):
    db_path = tmp_path / "schema-28.db"
    conn = connect(db_path)
    init_db(conn, [])
    conn.execute("DROP TABLE task_dependencies")
    conn.execute("DROP TABLE task_delegations")
    conn.execute("ALTER TABLE jobs DROP COLUMN blocked_reason")
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 29)],
    )

    assert run_migrations(conn, str(db_path)) == [
        29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
        46, 47, 48, 49, 50, 51, 52, 53
    ]
    assert current_version(conn) == 53
    assert "blocked_reason" in {
        row[1] for row in conn.execute("PRAGMA table_info(jobs)")
    }
    assert {
        "task_delegations",
        "task_dependencies",
    }.issubset(
        {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    )
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'task_dependencies_no_cycle'"
    ).fetchone()
    prerequisite_fk = next(
        row
        for row in conn.execute(
            "PRAGMA foreign_key_list(task_dependencies)"
        ).fetchall()
        if row[3] == "depends_on_task_id"
    )
    assert prerequisite_fk[6] == "RESTRICT"
    delegation_container_fk = next(
        row
        for row in conn.execute(
            "PRAGMA foreign_key_list(task_delegations)"
        ).fetchall()
        if row[3] == "container_id"
    )
    assert delegation_container_fk[6] == "RESTRICT"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _prepare_schema_34_graph_fixture(tmp_path: Path):
    db_path = tmp_path / "schema-34.db"
    conn = connect(db_path)
    init_db(conn, [])
    conn.execute("DROP TABLE graph_states")
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 35)],
    )
    return db_path, conn


def test_v35_graph_states_upgrade_rerun_and_scope_constraints(tmp_path: Path):
    db_path, conn = _prepare_schema_34_graph_fixture(tmp_path)
    apply_graph_states = next(m[2] for m in MIGRATIONS if m[0] == 35)

    assert run_migrations(
        conn,
        str(db_path),
        migrations=[m for m in MIGRATIONS if m[0] == 35],
    ) == [35]
    assert run_migrations(
        conn,
        str(db_path),
        migrations=[m for m in MIGRATIONS if m[0] == 35],
    ) == []
    assert current_version(conn) == 35

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(graph_states)")
    }
    assert columns == {
        "id",
        "container_id",
        "area_id",
        "kind",
        "root_path",
        "graph_path",
        "source_fingerprint",
        "graph_sha256",
        "tool_version",
        "semantic_backend",
        "state",
        "generation",
        "last_success_at",
        "last_attempt_at",
        "last_error",
        "created_at",
        "updated_at",
    }
    indexes = {
        row[1]: bool(row[2])
        for row in conn.execute("PRAGMA index_list(graph_states)")
    }
    assert indexes["uq_graph_states_knowledge"]
    assert indexes["uq_graph_states_code"]
    assert not indexes["idx_graph_states_container"]

    owner_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('graph-owner', 'owner')"
    ).lastrowid
    first_container = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('first-graph', 'First', '/tmp/first-graph', ?)",
        (owner_id,),
    ).lastrowid
    second_container = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) "
        "VALUES ('second-graph', 'Second', '/tmp/second-graph', ?)",
        (owner_id,),
    ).lastrowid
    wrong_area = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', '.', 'manual')",
        (second_container,),
    ).lastrowid

    with pytest.raises(
        sqlite3.IntegrityError,
        match="graph state Area is not an active code Area",
    ):
        conn.execute(
            "INSERT INTO graph_states("
            "container_id, area_id, kind, root_path, graph_path"
            ") VALUES (?, ?, 'code', '/tmp', '/tmp/graph.json')",
            (first_container, wrong_area),
        )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # Keep apply_graph_states referenced so static analysis sees the binding.
    assert callable(apply_graph_states)


def test_v35_graph_states_migration_rolls_back_as_one_transaction(
    tmp_path: Path,
):
    db_path, conn = _prepare_schema_34_graph_fixture(tmp_path)
    apply_graph_states = next(m[2] for m in MIGRATIONS if m[0] == 35)

    def apply_then_fail(connection):
        apply_graph_states(connection)
        raise RuntimeError("forced graph migration rollback")

    with pytest.raises(RuntimeError, match="forced graph migration rollback"):
        run_migrations(
            conn,
            str(db_path),
            migrations=[
                (
                    35,
                    "forced graph migration rollback",
                    apply_then_fail,
                )
            ],
        )

    assert current_version(conn) == 34
    assert conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'graph_states'"
    ).fetchone() is None


def test_fresh_install_graph_states_matches_idempotent_migration(
    tmp_path: Path,
):
    db_path = tmp_path / "fresh.db"
    conn = connect(db_path)
    init_db(conn, [])
    apply_graph_states = next(m[2] for m in MIGRATIONS if m[0] == 35)
    apply_lifecycle = next(m[2] for m in MIGRATIONS if m[0] == 36)
    apply_knowledge_outbox = next(m[2] for m in MIGRATIONS if m[0] == 37)

    before = [
        tuple(row)
        for row in conn.execute("PRAGMA table_info(graph_states)").fetchall()
    ]
    apply_graph_states(conn)
    apply_lifecycle(conn)
    apply_knowledge_outbox(conn)
    after = [
        tuple(row)
        for row in conn.execute("PRAGMA table_info(graph_states)").fetchall()
    ]

    assert before == after
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'graph_states_area_scope_insert'"
    ).fetchone()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _prepare_schema_35_graph_fixture(tmp_path: Path):
    db_path = tmp_path / "schema-35.db"
    conn = connect(db_path)
    init_db(conn, [])
    # Drop lifecycle columns so migration 36 has work to do.
    conn.execute("DROP TABLE graph_states")
    conn.execute("DROP TRIGGER jobs_ops_done_knowledge_rebuild")
    conn.execute("DROP TABLE knowledge_rebuild_intents")
    next(m[2] for m in MIGRATIONS if m[0] == 35)(conn)
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 36)],
    )
    return db_path, conn


def test_v36_and_v37_graph_lifecycle_upgrade_and_idempotent(tmp_path: Path):
    db_path, conn = _prepare_schema_35_graph_fixture(tmp_path)

    assert run_migrations(conn, str(db_path)) == [
        36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53
    ]
    assert run_migrations(conn, str(db_path)) == []
    assert current_version(conn) == 53

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(graph_states)")
    }
    assert {
        "repo_head",
        "pending_base_commit",
        "pending_head_commit",
        "rebuild_reason",
    }.issubset(columns)
    assert {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(knowledge_rebuild_intents)"
        )
    } == {
        "container_id",
        "reason",
        "intent_version",
        "created_at",
        "updated_at",
    }
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'jobs_ops_done_knowledge_rebuild'"
    ).fetchone()


def test_v39_preserves_epoch_identity_and_recovers_pending_fleet(
    tmp_path: Path,
):
    db_path = tmp_path / "schema-38-focus.db"
    conn = connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects(id INTEGER PRIMARY KEY);
        CREATE TABLE sessions(
          id INTEGER PRIMARY KEY,
          mode TEXT NOT NULL
        );
        CREATE TABLE master_focus_epochs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          master_session_id INTEGER NOT NULL
            REFERENCES sessions(id) ON DELETE CASCADE,
          container_id INTEGER NOT NULL
            REFERENCES projects(id) ON DELETE RESTRICT,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          ended_at TEXT,
          version INTEGER NOT NULL
        );
        CREATE TABLE master_focus_state(
          master_session_id INTEGER PRIMARY KEY
            REFERENCES sessions(id) ON DELETE CASCADE,
          current_epoch_id INTEGER
            REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
          pending_container_id INTEGER
            REFERENCES projects(id) ON DELETE SET NULL,
          version INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE runs(
          id INTEGER PRIMARY KEY,
          session_id INTEGER NOT NULL,
          focus_epoch_id INTEGER
        );
        CREATE TABLE messages(
          id INTEGER PRIMARY KEY,
          session_id INTEGER NOT NULL,
          run_id INTEGER
        );
        CREATE TABLE message_focus(
          message_id INTEGER PRIMARY KEY,
          focus_epoch_id INTEGER,
          focus_container_id INTEGER,
          subject_container_id INTEGER
        );
        INSERT INTO projects(id) VALUES (7);
        INSERT INTO sessions(id, mode) VALUES (3, 'master');
        INSERT INTO master_focus_epochs(
          id, master_session_id, container_id, version
        ) VALUES (11, 3, 7, 1);
        INSERT INTO master_focus_state(
          master_session_id, current_epoch_id, pending_container_id, version
        ) VALUES (3, 11, NULL, 2);
        INSERT INTO runs(id, session_id, focus_epoch_id)
        VALUES (13, 3, 11);
        INSERT INTO messages(id, session_id, run_id)
        VALUES (17, 3, 13);
        """
    )
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 39)],
    )

    assert run_migrations(conn, str(db_path)) == [
        39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53
    ]
    assert current_version(conn) == 53
    state = conn.execute(
        "SELECT pending_focus, pending_container_id "
        "FROM master_focus_state WHERE master_session_id = 3"
    ).fetchone()
    assert dict(state) == {
        "pending_focus": 1,
        "pending_container_id": None,
    }
    assert all(
        row[3] != "container_id"
        for row in conn.execute(
            "PRAGMA foreign_key_list(master_focus_epochs)"
        ).fetchall()
    )
    attribution = conn.execute(
        "SELECT focus_epoch_id, focus_container_id "
        "FROM message_focus WHERE message_id = 17"
    ).fetchone()
    assert dict(attribution) == {
        "focus_epoch_id": 11,
        "focus_container_id": 7,
    }
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Message Focus epoch attribution is immutable",
    ):
        conn.execute(
            "UPDATE message_focus SET focus_epoch_id = NULL "
            "WHERE message_id = 17"
        )
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Run Focus epoch attribution is immutable",
    ):
        conn.execute(
            "UPDATE runs SET focus_epoch_id = NULL WHERE id = 13"
        )
    conn.execute("DELETE FROM projects WHERE id = 7")
    assert conn.execute(
        "SELECT container_id FROM master_focus_epochs WHERE id = 11"
    ).fetchone()["container_id"] == 7


def test_v40_persists_task_focus_after_origin_message_deletion(
    tmp_path: Path,
):
    db_path = tmp_path / "schema-39-task-focus.db"
    conn = connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions(
          id INTEGER PRIMARY KEY,
          mode TEXT NOT NULL
        );
        CREATE TABLE master_focus_epochs(
          id INTEGER PRIMARY KEY,
          master_session_id INTEGER NOT NULL
            REFERENCES sessions(id) ON DELETE CASCADE,
          container_id INTEGER NOT NULL
        );
        CREATE TABLE messages(
          id INTEGER PRIMARY KEY,
          session_id INTEGER NOT NULL
            REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE message_focus(
          message_id INTEGER PRIMARY KEY
            REFERENCES messages(id) ON DELETE CASCADE,
          focus_epoch_id INTEGER
        );
        CREATE TABLE task_delegations(
          id INTEGER PRIMARY KEY,
          origin_session_id INTEGER
            REFERENCES sessions(id) ON DELETE SET NULL,
          origin_message_id INTEGER
            REFERENCES messages(id) ON DELETE SET NULL
        );
        INSERT INTO sessions(id, mode) VALUES (3, 'master');
        INSERT INTO master_focus_epochs(
          id, master_session_id, container_id
        ) VALUES (11, 3, 7);
        INSERT INTO messages(id, session_id) VALUES (17, 3);
        INSERT INTO message_focus(message_id, focus_epoch_id)
        VALUES (17, 11);
        INSERT INTO task_delegations(
          id, origin_session_id, origin_message_id
        ) VALUES (19, 3, 17);
        """
    )
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 40)],
    )

    assert run_migrations(conn, str(db_path)) == [
        40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53
    ]
    captured = conn.execute(
        "SELECT origin_focus_epoch_id, origin_focus_captured "
        "FROM task_delegations WHERE id = 19"
    ).fetchone()
    assert dict(captured) == {
        "origin_focus_epoch_id": 11,
        "origin_focus_captured": 1,
    }

    conn.execute("DELETE FROM messages WHERE id = 17")
    durable = conn.execute(
        "SELECT origin_message_id, origin_focus_epoch_id, "
        "origin_focus_captured FROM task_delegations WHERE id = 19"
    ).fetchone()
    assert dict(durable) == {
        "origin_message_id": None,
        "origin_focus_epoch_id": 11,
        "origin_focus_captured": 1,
    }
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Focus attribution is immutable",
    ):
        conn.execute(
            "UPDATE task_delegations SET origin_focus_epoch_id = NULL "
            "WHERE id = 19"
        )


def test_v42_preserves_historical_master_scope_after_container_deletion(
    tmp_path: Path,
):
    db_path = tmp_path / "schema-41-master-scope.db"
    conn = connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects(id INTEGER PRIMARY KEY);
        CREATE TABLE project_areas(
          id INTEGER PRIMARY KEY,
          project_id INTEGER NOT NULL
            REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE sessions(id INTEGER PRIMARY KEY);
        CREATE TABLE messages(
          id INTEGER PRIMARY KEY,
          session_id INTEGER NOT NULL
            REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE master_focus_epochs(
          id INTEGER PRIMARY KEY,
          master_session_id INTEGER NOT NULL
            REFERENCES sessions(id) ON DELETE CASCADE,
          container_id INTEGER NOT NULL
        );
        CREATE TABLE master_message_context(
          message_id INTEGER PRIMARY KEY
            REFERENCES messages(id) ON DELETE CASCADE,
          focus_mode TEXT NOT NULL,
          focus_container_id INTEGER
            REFERENCES projects(id) ON DELETE SET NULL,
          target_mode TEXT NOT NULL,
          target_container_id INTEGER
            REFERENCES projects(id) ON DELETE SET NULL,
          target_area_id INTEGER
            REFERENCES project_areas(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE message_focus(
          message_id INTEGER PRIMARY KEY
            REFERENCES messages(id) ON DELETE CASCADE,
          focus_epoch_id INTEGER
            REFERENCES master_focus_epochs(id) ON DELETE SET NULL,
          focus_container_id INTEGER
            REFERENCES projects(id) ON DELETE SET NULL,
          subject_container_id INTEGER
            REFERENCES projects(id) ON DELETE SET NULL
        );
        CREATE TABLE events(
          id INTEGER PRIMARY KEY,
          payload TEXT NOT NULL
        );
        CREATE TABLE master_projections(
          message_id INTEGER,
          event_id INTEGER,
          payload_json TEXT NOT NULL
        );
        CREATE TRIGGER message_focus_epoch_immutable
        BEFORE UPDATE OF focus_epoch_id ON message_focus
        WHEN NEW.focus_epoch_id IS NOT OLD.focus_epoch_id
        BEGIN
          SELECT RAISE(
            ABORT,
            'Message Focus epoch attribution is immutable'
          );
        END;
        INSERT INTO projects(id) VALUES (7);
        INSERT INTO project_areas(id, project_id) VALUES (8, 7);
        INSERT INTO sessions(id) VALUES (3);
        INSERT INTO messages(id, session_id) VALUES (17, 3);
        INSERT INTO master_focus_epochs(
          id, master_session_id, container_id
        ) VALUES (11, 3, 7);
        INSERT INTO master_message_context(
          message_id, focus_mode, focus_container_id, target_mode,
          target_container_id, target_area_id
        ) VALUES (17, 'container', 7, 'explicit', 7, 8);
        INSERT INTO message_focus(
          message_id, focus_epoch_id, focus_container_id,
          subject_container_id
        ) VALUES (17, 11, 7, 7);
        INSERT INTO events(id, payload)
        VALUES (23, '{"message_id":17,"container_id":7}');
        INSERT INTO master_projections(
          message_id, event_id, payload_json
        ) VALUES (
          17, 23, '{"message_id":17,"container_id":7}'
        );
        """
    )
    current_version(conn)
    conn.executemany(
        "INSERT INTO schema_migrations(version, description, applied_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP)",
        [(version, f"schema {version}") for version in range(1, 42)],
    )

    assert run_migrations(conn, str(db_path)) == [
        42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53
    ]
    conn.execute("DELETE FROM projects WHERE id = 7")
    context = conn.execute(
        "SELECT focus_container_id, target_container_id, target_area_id "
        "FROM master_message_context WHERE message_id = 17"
    ).fetchone()
    assert dict(context) == {
        "focus_container_id": 7,
        "target_container_id": 7,
        "target_area_id": 8,
    }
    attribution = conn.execute(
        "SELECT focus_epoch_id, focus_container_id, subject_container_id "
        "FROM message_focus WHERE message_id = 17"
    ).fetchone()
    assert dict(attribution) == {
        "focus_epoch_id": 11,
        "focus_container_id": 7,
        "subject_container_id": 7,
    }
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM master_projections"
        ).fetchone()["payload_json"]
    )
    assert payload["focus_epoch_id"] == 11
    assert payload["focus_container_id"] == 7
    assert payload["subject_container_id"] == 7
    assert json.loads(
        conn.execute("SELECT payload FROM events").fetchone()["payload"]
    ) == payload
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Message Focus epoch attribution is immutable",
    ):
        conn.execute(
            "UPDATE message_focus SET focus_epoch_id = NULL "
            "WHERE message_id = 17"
        )

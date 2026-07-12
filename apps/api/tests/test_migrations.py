from __future__ import annotations

from pathlib import Path

from proxima_api.db import connect
from proxima_api.migrations import current_version, run_migrations


def _add_foo(conn):
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY, note TEXT)")


def _add_users_nickname(conn):
    conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")


def test_no_pending_is_noop_but_creates_tracking_table(tmp_path: Path):
    conn = connect(tmp_path / "h.db")
    assert run_migrations(conn, str(tmp_path / "h.db"), migrations=[]) == []
    # tracking table exists, version 0
    assert current_version(conn) == 0


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
    import sqlite3
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


def test_migration_16_adds_sessions_pointer_fks_nulling_dangling(tmp_path):
    """sessions.task_id/job_id/workflow_id become real FKs (ON DELETE SET NULL);
    pre-existing dangling values are nulled so enforcement can't reject real data."""
    from proxima_api import migrations as M
    from proxima_api.db import connect, init_db
    from proxima_api.migrations import run_migrations

    db_path = tmp_path / "m.db"
    conn = connect(db_path)
    init_db(conn, [])
    run_migrations(conn, str(db_path), migrations=[m for m in M.MIGRATIONS if m[0] <= 15])
    conn.execute("INSERT INTO users(username, os_user) VALUES ('u','u')")
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    wid = conn.execute("INSERT INTO workflows(name) VALUES ('w')").lastrowid
    # a dangling job_id (99) + a valid workflow_id
    conn.execute("INSERT INTO sessions(title, owner_user_id, job_id, workflow_id) VALUES ('s',?,99,?)", (uid, wid))
    sid = conn.execute("SELECT id FROM sessions").fetchone()["id"]

    run_migrations(conn, str(db_path))

    assert any(r[3] == "task_id" for r in conn.execute("PRAGMA foreign_key_list(sessions)").fetchall())
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    row = conn.execute("SELECT job_id, workflow_id FROM sessions WHERE id=?", (sid,)).fetchone()
    assert row["job_id"] is None          # dangling nulled
    assert row["workflow_id"] == wid      # valid kept
    # FK behaves: deleting the workflow nulls the pointer.
    conn.execute("DELETE FROM workflows WHERE id=?", (wid,))
    assert conn.execute("SELECT workflow_id FROM sessions WHERE id=?", (sid,)).fetchone()["workflow_id"] is None

"""Master identity and durable Alpha compatibility migration.

The Alpha identity is the migration seed for the product-native Master. This
module owns only durable naming and invariants. Runtime restriction and the
typed Master tool broker land separately.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .run_projection import canonicalize_api_timestamps, project_job_run

MASTER_PROFILE_KIND = "master"
MASTER_SESSION_MODE = "master"
LEGACY_PROFILE_KIND = "alpha"
LEGACY_SESSION_MODE = "alpha"
ORIGIN_MASTER_COLUMN = "origin_master_session_id"
LEGACY_ORIGIN_COLUMN = "alpha_session_id"


class MasterPersistenceError(RuntimeError):
    """The durable Master identity cannot be reconciled without guessing."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _decode_object(raw: Any, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise MasterPersistenceError(f"{label} contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise MasterPersistenceError(f"{label} must contain a JSON object")
    return value


def canonicalize_master_payload(value: Any) -> Any:
    """Normalize legacy ownership keys for canonical API responses."""
    if isinstance(value, list):
        return [canonicalize_master_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = {
            "alpha_session_id": ORIGIN_MASTER_COLUMN,
            "alpha_dispatched": "master_dispatched",
        }.get(str(raw_key), str(raw_key))
        item = canonicalize_master_payload(raw_value)
        if key in normalized and normalized[key] != item:
            raise MasterPersistenceError(
                f"legacy and canonical payload keys conflict at {key}"
            )
        normalized[key] = item
    if normalized.get("view") == "alpha":
        normalized["view"] = "master"
    return normalized


def legacy_alpha_payload(value: Any) -> Any:
    """Project canonical ownership keys for the one-release Alpha API alias."""
    if isinstance(value, list):
        return [legacy_alpha_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    projected: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = {
            ORIGIN_MASTER_COLUMN: "alpha_session_id",
            "master_dispatched": "alpha_dispatched",
            "master_run": "alpha_run",
        }.get(str(raw_key), str(raw_key))
        projected[key] = legacy_alpha_payload(raw_value)
    if projected.get("view") == "master":
        projected["view"] = "alpha"
    if projected.get("mode") == "master":
        projected["mode"] = "alpha"
    return projected


def _migrate_identity_rows(conn: sqlite3.Connection) -> None:
    if not {"profiles", "sessions"}.issubset(
        {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    ):
        return
    profile_columns = _columns(conn, "profiles")
    session_columns = _columns(conn, "sessions")
    if "system_kind" not in profile_columns or "mode" not in session_columns:
        return

    profiles = conn.execute(
        "SELECT id, user_id, slug, name, system_kind FROM profiles "
        "WHERE system_kind IN (?, ?) ORDER BY user_id, id",
        (LEGACY_PROFILE_KIND, MASTER_PROFILE_KIND),
    ).fetchall()
    sessions = conn.execute(
        "SELECT id, owner_user_id, profile_id, project_id, mode FROM sessions "
        "WHERE mode IN (?, ?) ORDER BY owner_user_id, id",
        (LEGACY_SESSION_MODE, MASTER_SESSION_MODE),
    ).fetchall()

    profiles_by_owner: dict[int, list[sqlite3.Row]] = {}
    for row in profiles:
        profiles_by_owner.setdefault(int(row["user_id"]), []).append(row)
    sessions_by_owner: dict[int, list[sqlite3.Row]] = {}
    for row in sessions:
        sessions_by_owner.setdefault(int(row["owner_user_id"]), []).append(row)

    for owner_id in sorted(set(profiles_by_owner) | set(sessions_by_owner)):
        owner_profiles = profiles_by_owner.get(owner_id, [])
        owner_sessions = sessions_by_owner.get(owner_id, [])
        if len(owner_profiles) > 1:
            raise MasterPersistenceError(
                f"owner {owner_id} has multiple Alpha or Master profiles"
            )
        if len(owner_sessions) > 1:
            raise MasterPersistenceError(
                f"owner {owner_id} has multiple Alpha or Master sessions"
            )
        profile = owner_profiles[0] if owner_profiles else None
        session = owner_sessions[0] if owner_sessions else None
        if session is not None:
            if session["project_id"] is not None:
                raise MasterPersistenceError(
                    f"Master session {session['id']} is project-bound"
                )
            if profile is None or session["profile_id"] != profile["id"]:
                raise MasterPersistenceError(
                    f"Master session {session['id']} is not linked to its one system profile"
                )

    for profile in profiles:
        slug = str(profile["slug"])
        next_slug = (
            f"master-system{slug.removeprefix('alpha-system')}"
            if slug.startswith("alpha-system")
            else slug
        )
        conflict = conn.execute(
            "SELECT id FROM profiles WHERE user_id = ? AND slug = ? AND id != ?",
            (profile["user_id"], next_slug, profile["id"]),
        ).fetchone()
        if conflict:
            raise MasterPersistenceError(
                f"cannot rename profile {profile['id']} to occupied slug {next_slug}"
            )
        if (
            profile["system_kind"] != MASTER_PROFILE_KIND
            or next_slug != profile["slug"]
            or profile["name"] != "Master"
        ):
            conn.execute(
                "UPDATE profiles SET system_kind = ?, slug = ?, name = 'Master', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (MASTER_PROFILE_KIND, next_slug, profile["id"]),
            )
    conn.execute(
        "UPDATE sessions SET mode = ?, title = 'Master', "
        "updated_at = CURRENT_TIMESTAMP WHERE mode = ?",
        (MASTER_SESSION_MODE, LEGACY_SESSION_MODE),
    )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_one_master "
        "ON profiles(user_id) WHERE system_kind = 'master'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_one_master "
        "ON sessions(owner_user_id) WHERE mode = 'master'"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS master_session_must_be_unbound_insert
        BEFORE INSERT ON sessions
        WHEN NEW.mode = 'master' AND NEW.project_id IS NOT NULL
        BEGIN
          SELECT RAISE(ABORT, 'Master session must be project-unbound');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS master_session_must_be_unbound_update
        BEFORE UPDATE OF mode, project_id ON sessions
        WHEN NEW.mode = 'master' AND NEW.project_id IS NOT NULL
        BEGIN
          SELECT RAISE(ABORT, 'Master session must be project-unbound');
        END
        """
    )


def _migrate_job_origin(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "jobs")
    if not columns:
        return
    has_legacy = LEGACY_ORIGIN_COLUMN in columns
    has_master = ORIGIN_MASTER_COLUMN in columns
    if has_legacy and has_master:
        conflict = conn.execute(
            f"SELECT id FROM jobs WHERE {LEGACY_ORIGIN_COLUMN} IS NOT NULL "
            f"AND {ORIGIN_MASTER_COLUMN} IS NOT NULL "
            f"AND {LEGACY_ORIGIN_COLUMN} != {ORIGIN_MASTER_COLUMN} LIMIT 1"
        ).fetchone()
        if conflict:
            raise MasterPersistenceError(
                f"job {conflict['id']} has conflicting Alpha and Master origins"
            )
        conn.execute(
            f"UPDATE jobs SET {ORIGIN_MASTER_COLUMN} = "
            f"COALESCE({ORIGIN_MASTER_COLUMN}, {LEGACY_ORIGIN_COLUMN})"
        )
        conn.execute("DROP INDEX IF EXISTS idx_jobs_alpha")
        conn.execute(f"ALTER TABLE jobs DROP COLUMN {LEGACY_ORIGIN_COLUMN}")
    elif has_legacy:
        conn.execute(
            f"ALTER TABLE jobs RENAME COLUMN {LEGACY_ORIGIN_COLUMN} "
            f"TO {ORIGIN_MASTER_COLUMN}"
        )
        conn.execute("DROP INDEX IF EXISTS idx_jobs_alpha")
    elif not has_master:
        conn.execute(
            f"ALTER TABLE jobs ADD COLUMN {ORIGIN_MASTER_COLUMN} INTEGER "
            "REFERENCES sessions(id) ON DELETE SET NULL"
        )

    columns = _columns(conn, "jobs")
    if {"status", "created_at", ORIGIN_MASTER_COLUMN}.issubset(columns):
        conn.execute("DROP INDEX IF EXISTS idx_jobs_origin_master")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_origin_master "
            f"ON jobs({ORIGIN_MASTER_COLUMN}, status, created_at)"
        )
    _assert_job_origin_integrity(conn)


def _assert_job_origin_integrity(conn: sqlite3.Connection) -> None:
    if ORIGIN_MASTER_COLUMN not in _columns(conn, "jobs"):
        return
    origin_fks = [
        row
        for row in conn.execute("PRAGMA foreign_key_list(jobs)").fetchall()
        if row["from"] == ORIGIN_MASTER_COLUMN
    ]
    if len(origin_fks) != 1 or (
        origin_fks[0]["table"] != "sessions"
        or origin_fks[0]["to"] != "id"
        or str(origin_fks[0]["on_delete"]).upper() != "SET NULL"
    ):
        raise MasterPersistenceError(
            "jobs Master origin foreign key is missing or inconsistent"
        )
    if {"status", "created_at"}.issubset(_columns(conn, "jobs")):
        index_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA index_info(idx_jobs_origin_master)"
            ).fetchall()
        ]
        if index_columns != [ORIGIN_MASTER_COLUMN, "status", "created_at"]:
            raise MasterPersistenceError(
                "jobs Master origin index is missing or inconsistent"
            )
    if "mode" in _columns(conn, "sessions"):
        invalid = conn.execute(
            f"SELECT j.id FROM jobs j JOIN sessions s ON s.id = j.{ORIGIN_MASTER_COLUMN} "
            f"WHERE j.{ORIGIN_MASTER_COLUMN} IS NOT NULL AND s.mode != ? LIMIT 1",
            (MASTER_SESSION_MODE,),
        ).fetchone()
        if invalid:
            raise MasterPersistenceError(
                f"job {invalid['id']} points at a non-Master origin session"
            )


def _migrate_settings(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "app_settings"):
        return
    mappings = {
        "alpha.runner_id": "master.runner_id",
        "alpha.unattended": "master.unattended",
        "alpha.budget.turns": "master.budget.turns",
        "alpha.budget.wall_seconds": "master.budget.wall_seconds",
        "alpha.budget.tokens_optional": "master.budget.tokens_optional",
        "alpha.tour.core_done": "master.tour.core_done",
        "alpha.budget.started_at": "master.budget.started_at",
        "alpha.budget.turns_used": "master.budget.turns_used",
    }
    for legacy_key, master_key in mappings.items():
        legacy = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (legacy_key,)
        ).fetchone()
        if legacy is None:
            continue
        master = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (master_key,)
        ).fetchone()
        if master is not None and master["value"] != legacy["value"]:
            raise MasterPersistenceError(
                f"settings {legacy_key} and {master_key} conflict"
            )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings(key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (master_key, legacy["value"]),
        )
        conn.execute("DELETE FROM app_settings WHERE key = ?", (legacy_key,))


def _migrate_attention(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "attention_items"):
        return
    rows = conn.execute(
        "SELECT id, kind, title, target_json, source_key FROM attention_items"
    ).fetchall()
    for row in rows:
        kind_value = str(row["kind"])
        source_key = row["source_key"]
        source_value = source_key if isinstance(source_key, str) else ""
        if kind_value not in {"alpha", "alpha_budget", "alpha_decision"} and not (
            source_value.startswith("alpha-budget:")
            or source_value.startswith("alpha-start:")
            or source_value.startswith("alpha:")
        ):
            continue
        target = _decode_object(
            row["target_json"], label=f"attention item {row['id']} target"
        )
        normalized = canonicalize_master_payload(target)
        kind = {
            "alpha": "master",
            "alpha_budget": "master_budget",
            "alpha_decision": "master_decision",
        }.get(kind_value, kind_value)
        if isinstance(source_key, str):
            if source_key.startswith("alpha-budget:"):
                source_key = "master-budget:" + source_key.removeprefix(
                    "alpha-budget:"
                )
            elif source_key.startswith("alpha-start:"):
                source_key = "master-start:" + source_key.removeprefix("alpha-start:")
            elif source_key.startswith("alpha:"):
                source_key = "master:" + source_key.removeprefix("alpha:")
        if source_key != row["source_key"] and source_key is not None:
            conflict = conn.execute(
                "SELECT id FROM attention_items WHERE source_key = ? AND id != ?",
                (source_key, row["id"]),
            ).fetchone()
            if conflict:
                raise MasterPersistenceError(
                    f"attention source key {source_key} is ambiguous"
                )
        title = str(row["title"]).replace("Alpha", "Master")
        conn.execute(
            "UPDATE attention_items SET kind = ?, title = ?, target_json = ?, "
            "source_key = ? WHERE id = ?",
            (
                kind,
                title,
                json.dumps(normalized, ensure_ascii=False),
                source_key,
                row["id"],
            ),
        )


def _migrate_owned_json_payloads(conn: sqlite3.Connection) -> None:
    job_columns = _columns(conn, "jobs")
    if {"input", ORIGIN_MASTER_COLUMN}.issubset(job_columns):
        for row in conn.execute(
            f"SELECT id, input FROM jobs WHERE {ORIGIN_MASTER_COLUMN} IS NOT NULL"
        ).fetchall():
            payload = _decode_object(row["input"], label=f"job {row['id']} input")
            normalized = canonicalize_master_payload(payload)
            if normalized != payload:
                conn.execute(
                    "UPDATE jobs SET input = ? WHERE id = ?",
                    (json.dumps(normalized, ensure_ascii=False), row["id"]),
                )
    if _table_exists(conn, "job_checkpoints") and ORIGIN_MASTER_COLUMN in job_columns:
        for row in conn.execute(
            "SELECT cp.id, cp.payload_json FROM job_checkpoints cp "
            "JOIN jobs j ON j.id = cp.job_id "
            f"WHERE j.{ORIGIN_MASTER_COLUMN} IS NOT NULL"
        ).fetchall():
            payload = _decode_object(
                row["payload_json"], label=f"checkpoint {row['id']} payload"
            )
            normalized = canonicalize_master_payload(payload)
            if normalized != payload:
                conn.execute(
                    "UPDATE job_checkpoints SET payload_json = ? WHERE id = ?",
                    (json.dumps(normalized, ensure_ascii=False), row["id"]),
                )


def _migrate_runs_events_and_audit(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "runs") and "kind" in _columns(conn, "runs"):
        conn.execute(
            "UPDATE runs SET kind = 'master' WHERE kind = 'alpha'"
        )
        conn.execute(
            "UPDATE runs SET kind = 'master_tool_' || substr(kind, 12) "
            "WHERE kind GLOB 'alpha_tool_[0-9]*'"
        )
    event_columns = _columns(conn, "events")
    if {"id", "payload", "session_id", "run_id"}.issubset(event_columns):
        for row in conn.execute(
            "SELECT e.id, e.payload FROM events e WHERE "
            "EXISTS ("
            "  SELECT 1 FROM sessions s LEFT JOIN jobs j ON j.id = s.job_id "
            "  WHERE s.id = e.session_id "
            f"    AND (s.mode = ? OR j.{ORIGIN_MASTER_COLUMN} IS NOT NULL)"
            ") OR EXISTS ("
            "  SELECT 1 FROM runs r JOIN sessions s ON s.id = r.session_id "
            "  LEFT JOIN jobs j ON j.id = s.job_id "
            "  WHERE r.id = e.run_id "
            f"    AND (s.mode = ? OR j.{ORIGIN_MASTER_COLUMN} IS NOT NULL)"
            ")",
            (MASTER_SESSION_MODE, MASTER_SESSION_MODE),
        ).fetchall():
            payload = _decode_object(
                row["payload"], label=f"event {row['id']} payload"
            )
            normalized = canonicalize_master_payload(payload)
            if "alpha" in normalized:
                legacy_value = normalized.pop("alpha")
                if "master" in normalized and normalized["master"] != legacy_value:
                    raise MasterPersistenceError(
                        f"event {row['id']} has conflicting Alpha and Master markers"
                    )
                normalized["master"] = legacy_value
            if normalized != payload:
                conn.execute(
                    "UPDATE events SET payload = ? WHERE id = ?",
                    (json.dumps(normalized, ensure_ascii=False), row["id"]),
                )
    if _table_exists(conn, "audit_log"):
        rows = conn.execute(
            "SELECT id, action, target_id, metadata FROM audit_log"
        ).fetchall()
        for row in rows:
            action = str(row["action"])
            if not action.startswith("alpha."):
                continue
            target_id = str(row["target_id"])
            action = "master." + action.removeprefix("alpha.")
            if target_id == "alpha":
                target_id = "master"
            elif target_id.startswith("alpha."):
                target_id = "master." + target_id.removeprefix("alpha.")
            metadata = _decode_object(
                row["metadata"], label=f"audit row {row['id']} metadata"
            )
            normalized = canonicalize_master_payload(metadata)
            conn.execute(
                "UPDATE audit_log SET action = ?, target_id = ?, metadata = ? "
                "WHERE id = ?",
                (
                    action,
                    target_id,
                    json.dumps(normalized, ensure_ascii=False),
                    row["id"],
                ),
            )


def migrate_master_persistence(conn: sqlite3.Connection) -> None:
    """Migrate Alpha's durable identity and ownership links in place."""
    _migrate_identity_rows(conn)
    _migrate_job_origin(conn)
    _migrate_settings(conn)
    _migrate_attention(conn)
    _migrate_owned_json_payloads(conn)
    _migrate_runs_events_and_audit(conn)
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise MasterPersistenceError(
            f"Master persistence migration found foreign-key violations: "
            f"{[tuple(row) for row in violations]}"
        )


def assert_master_persistence(conn: sqlite3.Connection) -> None:
    """Refuse startup when a supposedly migrated database is ambiguous."""
    job_columns = _columns(conn, "jobs")
    if job_columns and (
        ORIGIN_MASTER_COLUMN not in job_columns or LEGACY_ORIGIN_COLUMN in job_columns
    ):
        raise MasterPersistenceError("jobs Master origin migration is incomplete")
    legacy_profile = (
        conn.execute(
            "SELECT id FROM profiles WHERE system_kind = ? LIMIT 1",
            (LEGACY_PROFILE_KIND,),
        ).fetchone()
        if "system_kind" in _columns(conn, "profiles")
        else None
    )
    legacy_session = (
        conn.execute(
            "SELECT id FROM sessions WHERE mode = ? LIMIT 1",
            (LEGACY_SESSION_MODE,),
        ).fetchone()
        if "mode" in _columns(conn, "sessions")
        else None
    )
    if legacy_profile or legacy_session:
        raise MasterPersistenceError("Alpha identity migration is incomplete")
    _migrate_identity_rows(conn)
    _assert_job_origin_integrity(conn)
    assert_master_tool_ledger(conn)
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise MasterPersistenceError(
            "Master persistence validation found foreign-key violations: "
            f"{[tuple(row) for row in violations]}"
        )


def assert_master_tool_ledger(conn: sqlite3.Connection) -> None:
    """Refuse a partial or drifted durable Master tool ledger."""
    expected_columns = {
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
    columns = _columns(conn, "master_tool_calls")
    if columns != expected_columns:
        raise MasterPersistenceError(
            "Master tool-call ledger schema is incomplete"
        )
    foreign_keys = {
        (str(row[3]), str(row[2]), str(row[6]))
        for row in conn.execute(
            "PRAGMA foreign_key_list(master_tool_calls)"
        ).fetchall()
    }
    if {
        ("master_session_id", "sessions", "CASCADE"),
        ("turn_root_run_id", "runs", "CASCADE"),
    } - foreign_keys:
        raise MasterPersistenceError(
            "Master tool-call ledger ownership is not enforced"
        )
    unique_shapes = set()
    for row in conn.execute(
        "PRAGMA index_list(master_tool_calls)"
    ).fetchall():
        if not row[2]:
            continue
        index_name = str(row[1]).replace('"', '""')
        unique_shapes.add(
            tuple(
                str(column[2])
                for column in conn.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            )
        )
    if ("turn_root_run_id", "envelope_hash") not in unique_shapes:
        raise MasterPersistenceError(
            "Master tool-call replay protection is not unique"
        )
    has_rows = conn.execute(
        "SELECT 1 FROM master_tool_calls LIMIT 1"
    ).fetchone()
    inconsistent = None
    if has_rows is not None:
        if "mode" not in _columns(conn, "sessions") or (
            "session_id" not in _columns(conn, "runs")
        ):
            raise MasterPersistenceError(
                "Master tool-call ledger parent schema is incomplete"
            )
        inconsistent = conn.execute(
            "SELECT mtc.id FROM master_tool_calls mtc "
            "JOIN sessions s ON s.id = mtc.master_session_id "
            "JOIN runs r ON r.id = mtc.turn_root_run_id "
            "WHERE s.mode != 'master' "
            "OR r.session_id != mtc.master_session_id "
            "OR mtc.envelope_hash GLOB '*[^0-9a-f]*' "
            "OR length(mtc.envelope_hash) != 64 "
            "OR trim(mtc.tool_name) = '' "
            "OR (mtc.status = 'pending' AND "
            "(mtc.result_json IS NOT NULL OR mtc.completed_at IS NOT NULL)) "
            "OR (mtc.status = 'complete' AND "
            "(mtc.result_json IS NULL OR mtc.completed_at IS NULL)) "
            "LIMIT 1"
        ).fetchone()
    if inconsistent is not None:
        raise MasterPersistenceError(
            "Master tool-call ledger contains ambiguous durable state"
        )
    for row in conn.execute(
        "SELECT id, result_json FROM master_tool_calls "
        "WHERE result_json IS NOT NULL"
    ).fetchall():
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise MasterPersistenceError(
                "Master tool-call ledger contains invalid result JSON"
            ) from exc
        if not isinstance(result, dict):
            raise MasterPersistenceError(
                "Master tool-call ledger result must be an object"
            )


def master_identity_rows(
    conn: sqlite3.Connection, owner_id: int
) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    profiles = conn.execute(
        "SELECT * FROM profiles WHERE user_id = ? AND system_kind = ? ORDER BY id",
        (owner_id, MASTER_PROFILE_KIND),
    ).fetchall()
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE owner_user_id = ? AND mode = ? ORDER BY id",
        (owner_id, MASTER_SESSION_MODE),
    ).fetchall()
    if len(profiles) > 1 or len(sessions) > 1:
        raise MasterPersistenceError(
            f"owner {owner_id} has a forked Master identity"
        )
    profile = profiles[0] if profiles else None
    session = sessions[0] if sessions else None
    if session is not None and (
        session["project_id"] is not None
        or profile is None
        or session["profile_id"] != profile["id"]
    ):
        raise MasterPersistenceError(
            f"owner {owner_id} has an inconsistent Master identity"
        )
    return profile, session


def canonical_job_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the ownership, timestamp, and run projection of one job payload.

    Job input is user-extensible domain data. Its legacy ownership keys are
    canonicalized only when the job is known to be Master-owned.
    """
    normalized = dict(payload)
    legacy_origin = normalized.pop(LEGACY_ORIGIN_COLUMN, None)
    master_origin = normalized.get(ORIGIN_MASTER_COLUMN)
    if (
        legacy_origin is not None
        and master_origin is not None
        and legacy_origin != master_origin
    ):
        raise MasterPersistenceError(
            "legacy and canonical job origins conflict"
        )
    if master_origin is None and legacy_origin is not None:
        normalized[ORIGIN_MASTER_COLUMN] = legacy_origin
        master_origin = legacy_origin
    if master_origin is not None and isinstance(normalized.get("input"), dict):
        normalized["input"] = canonicalize_master_payload(normalized["input"])
    normalized = canonicalize_api_timestamps(normalized)
    normalized["run_projection"] = project_job_run(normalized)
    return normalized

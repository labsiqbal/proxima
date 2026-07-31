"""Durable Master owner decisions and exactly-once Task continuation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .auth import iso_now
from .task_state_events import append_task_update


class MasterDecisionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# System response value when a Task leaves review without an owner answer.
TASK_LEFT_REVIEW_RESPONSE_VALUE = "__task_left_review__"


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise MasterDecisionError(f"invalid_{name}", f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MasterDecisionError(
            f"invalid_{name}", f"{name} must be an integer"
        ) from exc
    if parsed <= 0:
        raise MasterDecisionError(f"invalid_{name}", f"{name} must be positive")
    return parsed


def _bounded_text(
    value: Any,
    name: str,
    *,
    maximum: int,
    required: bool = True,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise MasterDecisionError(
            f"invalid_{name}", f"Master decision {name} is required"
        )
    if len(text) > maximum:
        raise MasterDecisionError(
            f"invalid_{name}",
            f"Master decision {name} must be at most {maximum} characters",
        )
    return text


def normalize_response_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MasterDecisionError(
            "invalid_response_shape",
            "Master decision response must define choices or free text",
        )
    response_type = value.get("type")
    if response_type == "choice":
        raw_choices = value.get("choices")
        if not isinstance(raw_choices, list) or not 2 <= len(raw_choices) <= 10:
            raise MasterDecisionError(
                "invalid_response_shape",
                "A bounded decision needs 2 to 10 choices",
            )
        choices: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in raw_choices:
            if not isinstance(raw, Mapping):
                raise MasterDecisionError(
                    "invalid_response_shape",
                    "Each decision choice needs an id and label",
                )
            choice_id = _bounded_text(raw.get("id"), "choice id", maximum=80)
            label = _bounded_text(raw.get("label"), "choice label", maximum=200)
            description = _bounded_text(
                raw.get("description"),
                "choice description",
                maximum=500,
                required=False,
            )
            if choice_id in seen:
                raise MasterDecisionError(
                    "invalid_response_shape",
                    "Decision choice ids must be unique",
                )
            seen.add(choice_id)
            choice = {"id": choice_id, "label": label}
            if description:
                choice["description"] = description
            choices.append(choice)
        return {"type": "choice", "choices": choices}
    if response_type == "text":
        max_length = value.get("max_length", 2000)
        if isinstance(max_length, bool):
            max_length = 0
        try:
            max_length = int(max_length)
        except (TypeError, ValueError, OverflowError):
            max_length = 0
        if not 1 <= max_length <= 4000:
            raise MasterDecisionError(
                "invalid_response_shape",
                "Free-text decisions must allow 1 to 4000 characters",
            )
        placeholder = _bounded_text(
            value.get("placeholder") or "Enter your decision",
            "response placeholder",
            maximum=200,
        )
        return {
            "type": "text",
            "max_length": max_length,
            "placeholder": placeholder,
        }
    raise MasterDecisionError(
        "invalid_response_shape",
        "Master decision response type must be choice or text",
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _fingerprint(
    *,
    title: str,
    prompt: str,
    context: str,
    response_shape: Mapping[str, Any],
    job_id: int,
    origin_message_id: int,
) -> str:
    encoded = json.dumps(
        {
            "title": title,
            "prompt": prompt,
            "context": context,
            "response": dict(response_shape),
            "task_id": job_id,
            "origin_message_id": origin_message_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def decision_payload(
    conn: sqlite3.Connection, row: sqlite3.Row | Mapping[str, Any]
) -> dict[str, Any]:
    data = dict(row)
    data["response_shape"] = _json_object(data.pop("response_shape_json"))
    raw_response = data.pop("response_json")
    data["response"] = _json_object(raw_response) if raw_response else None
    data["legacy_without_task"] = data.get("requesting_job_id") is None
    job = None
    if data.get("requesting_job_id") is not None:
        job = conn.execute(
            "SELECT j.id, j.title, j.status, j.engine, j.project_id, "
            "p.name AS project_name, p.slug AS project_slug "
            "FROM jobs j LEFT JOIN projects p ON p.id = j.project_id "
            "WHERE j.id = ?",
            (data["requesting_job_id"],),
        ).fetchone()
    data["task"] = dict(job) if job is not None else None
    return data


def list_decisions(
    conn: sqlite3.Connection,
    *,
    owner_user_id: int,
    master_session_id: int | None = None,
    include_resolved: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["owner_user_id = ?"]
    params: list[Any] = [owner_user_id]
    if master_session_id is not None:
        clauses.append("master_session_id = ?")
        params.append(master_session_id)
    if not include_resolved:
        clauses.append("state IN ('pending', 'deferred')")
    params.append(max(1, min(200, limit)))
    rows = conn.execute(
        "SELECT * FROM master_decisions WHERE "
        + " AND ".join(clauses)
        + " ORDER BY CASE state WHEN 'pending' THEN 0 ELSE 1 END, "
        "updated_at DESC, id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [decision_payload(conn, row) for row in rows]


def get_decision(
    conn: sqlite3.Connection, decision_id: int, owner_user_id: int
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM master_decisions WHERE id = ? AND owner_user_id = ?",
        (decision_id, owner_user_id),
    ).fetchone()
    if row is None:
        raise MasterDecisionError(
            "decision_not_found", "Master decision was not found", 404
        )
    return row


def pending_decision_for_job(
    conn: sqlite3.Connection, job_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id FROM master_decisions WHERE requesting_job_id = ? "
        "AND state IN ('pending', 'deferred') ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()


def pending_decision_conflict(decision_id: int) -> dict[str, str]:
    return {
        "code": "master_decision_pending",
        "message": (
            f"Resolve Master decision #{decision_id} "
            "instead of approving this Task"
        ),
    }


def task_can_continue_after_decision(job: Mapping[str, Any] | sqlite3.Row) -> bool:
    """Linear Tasks with a current step can accept exactly-once continuation."""
    data = dict(job)
    if str(data.get("engine") or "linear") == "graph":
        return False
    try:
        steps = json.loads(data.get("steps_state") or "[]")
    except (TypeError, json.JSONDecodeError):
        return False
    try:
        current_step = int(data.get("current_step_idx") or 0)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(steps, list)
        and 0 <= current_step < len(steps)
        and isinstance(steps[current_step], dict)
    )


def settle_open_decisions_for_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    actor_user_id: int | None,
    reason: str,
) -> list[int]:
    """Close unresolved decisions when their Task leaves review without an answer.

    Runs inside the caller's write transaction. Returns settled decision ids so
    the caller can project them while the Task row still exists.
    """
    rows = conn.execute(
        "SELECT * FROM master_decisions WHERE requesting_job_id = ? "
        "AND state IN ('pending', 'deferred') ORDER BY id",
        (job_id,),
    ).fetchall()
    if not rows:
        return []
    reason_label = (str(reason or "").strip() or "Task left review")[:200]
    response = {
        "value": TASK_LEFT_REVIEW_RESPONSE_VALUE,
        "label": f"Closed because the Task left review ({reason_label})",
        "task_id": int(job_id),
    }
    response_json = json.dumps(
        response, ensure_ascii=False, separators=(",", ":")
    )
    settled: list[int] = []
    for row in rows:
        updated = conn.execute(
            "UPDATE master_decisions SET state = 'resolved', "
            "response_json = ?, resolved_by_user_id = ?, "
            "resolved_at = CURRENT_TIMESTAMP, version = version + 1, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND state IN ('pending', 'deferred')",
            (response_json, actor_user_id, row["id"]),
        )
        if updated.rowcount != 1:
            continue
        conn.execute(
            "UPDATE attention_items SET status = 'resolved', "
            "resolved_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status IN ('open', 'deferred')",
            (row["attention_item_id"],),
        )
        conn.execute(
            "INSERT INTO audit_log("
            "actor_user_id, action, target_type, target_id, metadata"
            ") VALUES (?, 'master.decision.settle', 'master_decision', ?, ?)",
            (
                actor_user_id,
                str(row["id"]),
                json.dumps(
                    {
                        "task_id": job_id,
                        "reason": reason_label,
                        "previous_state": row["state"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )
        settled.append(int(row["id"]))
    return settled


def settle_open_decisions_for_jobs(
    conn: sqlite3.Connection,
    *,
    job_ids: list[int],
    actor_user_id: int | None,
    reason: str,
) -> list[int]:
    """Settle every open decision linked to the given Tasks, in id order."""
    settled: list[int] = []
    for job_id in sorted({int(job_id) for job_id in job_ids if int(job_id) > 0}):
        settled.extend(
            settle_open_decisions_for_job(
                conn,
                job_id=job_id,
                actor_user_id=actor_user_id,
                reason=reason,
            )
        )
    return settled


def project_settled_decisions(
    app: Any,
    decision_ids: list[int],
    *,
    conn: sqlite3.Connection | None = None,
    external_transaction: bool = False,
) -> list[int]:
    """Project settled decisions into Master.

    When ``external_transaction`` is true, writes on ``conn`` inside the caller's
    transaction and returns master session ids the caller must notify after commit.
    """
    projection = getattr(app.state, "master_projection", None)
    if projection is None or not decision_ids:
        return []
    notify_sessions: list[int] = []
    for decision_id in decision_ids:
        if external_transaction:
            if conn is None:
                raise ValueError("external decision projection requires a connection")
            row = projection.project_decision(
                decision_id,
                conn=conn,
                external_transaction=True,
            )
            if row is not None:
                notify_sessions.append(int(row["master_session_id"]))
            continue
        projection.safe_project_decision(decision_id)
    return notify_sessions


def create_decision(
    conn: sqlite3.Connection,
    app: Any,
    user: Mapping[str, Any],
    *,
    master_session_id: int,
    origin_message_id: int,
    source_key: str,
    title: Any,
    prompt: Any,
    context: Any,
    response_shape: Any,
    task_id: Any,
) -> dict[str, Any]:
    owner_user_id = _as_int(user.get("id"), "owner")
    master_session_id = _as_int(master_session_id, "master_session_id")
    origin_message_id = _as_int(origin_message_id, "origin_message_id")
    job_id = _as_int(task_id, "task_id")
    clean_title = _bounded_text(title, "title", maximum=200)
    clean_prompt = _bounded_text(prompt, "prompt", maximum=4000)
    clean_context = _bounded_text(context, "context", maximum=4000)
    shape = normalize_response_shape(response_shape)
    clean_source_key = _bounded_text(source_key, "source key", maximum=500)
    request_fingerprint = _fingerprint(
        title=clean_title,
        prompt=clean_prompt,
        context=clean_context,
        response_shape=shape,
        job_id=job_id,
        origin_message_id=origin_message_id,
    )

    with app.state.db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            replay = conn.execute(
                "SELECT decision.* FROM master_decisions decision "
                "JOIN attention_items attention "
                "ON attention.id = decision.attention_item_id "
                "WHERE attention.source_key = ?",
                (clean_source_key,),
            ).fetchone()
            if replay is not None:
                if replay["request_fingerprint"] != request_fingerprint:
                    raise MasterDecisionError(
                        "decision_idempotency_conflict",
                        "This decision key was already used for a different request",
                        409,
                    )
                conn.execute("COMMIT")
                return decision_payload(conn, replay)

            session = conn.execute(
                "SELECT id FROM sessions WHERE id = ? AND owner_user_id = ? "
                "AND mode = 'master' AND project_id IS NULL",
                (master_session_id, owner_user_id),
            ).fetchone()
            if session is None:
                raise MasterDecisionError(
                    "decision_master_invalid",
                    "Master decision conversation is not available",
                    409,
                )
            if conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND session_id = ?",
                (origin_message_id, master_session_id),
            ).fetchone() is None:
                raise MasterDecisionError(
                    "decision_message_invalid",
                    "Master decision source message is not available",
                    409,
                )
            job = conn.execute(
                "SELECT j.id, j.status, j.engine, j.session_id, "
                "j.steps_state, j.current_step_idx "
                "FROM jobs j WHERE j.id = ? AND j.created_by = ? "
                "AND j.origin_master_session_id = ?",
                (job_id, owner_user_id, master_session_id),
            ).fetchone()
            if job is None:
                raise MasterDecisionError(
                    "decision_task_invalid",
                    "Master decision Task is not owned by this conversation",
                    409,
                )
            if job["status"] != "review":
                raise MasterDecisionError(
                    "decision_task_not_waiting",
                    "Master can request a decision only for a Task waiting in review",
                    409,
                )
            if not task_can_continue_after_decision(job):
                raise MasterDecisionError(
                    "decision_task_invalid",
                    "Master decisions require a Task that can continue after "
                    "the owner responds",
                    409,
                )
            if conn.execute(
                "SELECT 1 FROM master_decisions WHERE requesting_job_id = ? "
                "AND state IN ('pending', 'deferred')",
                (job_id,),
            ).fetchone() is not None:
                raise MasterDecisionError(
                    "decision_already_pending",
                    "This Task already has an unresolved Master decision",
                    409,
                )
            target = {
                "view": "master",
                "job_id": job_id,
                "engine": job["engine"],
                "origin_master_session_id": master_session_id,
                "origin_message_id": origin_message_id,
            }
            attention_cursor = conn.execute(
                "INSERT INTO attention_items("
                "kind, title, target_json, inline_ok, actions_json, status, "
                "source_key"
                ") VALUES ('master_decision', ?, ?, 0, '[]', 'open', ?)",
                (
                    clean_title,
                    json.dumps(
                        target, ensure_ascii=False, separators=(",", ":")
                    ),
                    clean_source_key,
                ),
            )
            attention_id = int(attention_cursor.lastrowid)
            decision_cursor = conn.execute(
                "INSERT INTO master_decisions("
                "attention_item_id, owner_user_id, master_session_id, "
                "origin_message_id, requesting_job_id, title, prompt, context, "
                "response_shape_json, request_fingerprint"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attention_id,
                    owner_user_id,
                    master_session_id,
                    origin_message_id,
                    job_id,
                    clean_title,
                    clean_prompt,
                    clean_context,
                    json.dumps(shape, ensure_ascii=False, separators=(",", ":")),
                    request_fingerprint,
                ),
            )
            decision_id = int(decision_cursor.lastrowid)
            target["decision_id"] = decision_id
            conn.execute(
                "UPDATE attention_items SET target_json = ? WHERE id = ?",
                (
                    json.dumps(
                        target, ensure_ascii=False, separators=(",", ":")
                    ),
                    attention_id,
                ),
            )
            conn.execute(
                "INSERT INTO audit_log("
                "actor_user_id, action, target_type, target_id, metadata"
                ") VALUES (?, 'master.decision.request', 'master_decision', ?, ?)",
                (
                    owner_user_id,
                    str(decision_id),
                    json.dumps(
                        {
                            "task_id": job_id,
                            "master_session_id": master_session_id,
                            "origin_message_id": origin_message_id,
                            "response_type": shape["type"],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    projection = getattr(app.state, "master_projection", None)
    if projection is not None:
        projection.safe_project_attention(attention_id)
    return decision_payload(
        conn,
        conn.execute(
            "SELECT * FROM master_decisions WHERE id = ?", (decision_id,)
        ).fetchone(),
    )


def _validated_response(
    shape: Mapping[str, Any], value: Any
) -> dict[str, str]:
    if shape.get("type") == "choice":
        response_id = _bounded_text(value, "response", maximum=80)
        choice = next(
            (
                item
                for item in shape.get("choices", [])
                if isinstance(item, Mapping) and item.get("id") == response_id
            ),
            None,
        )
        if choice is None:
            raise MasterDecisionError(
                "invalid_decision_response",
                "Choose one of the available decision options",
            )
        return {"value": response_id, "label": str(choice["label"])}
    max_length = int(shape.get("max_length") or 2000)
    response_text = _bounded_text(value, "response", maximum=max_length)
    return {"value": response_text, "label": response_text}


def _continuation_prompt(
    *,
    prompt: str,
    context: str,
    response_label: str,
) -> str:
    return (
        "Continue this Task using the owner's durable Master decision below. "
        "Apply the decision exactly once, do not ask the same question again, "
        "and complete the remaining work.\n\n"
        f"Decision prompt: {prompt}\n"
        f"Owner-readable context: {context}\n"
        f"Owner response: {response_label}"
    )


def defer_decision(
    conn: sqlite3.Connection,
    app: Any,
    user: Mapping[str, Any],
    decision_id: int,
    expected_version: int,
) -> dict[str, Any]:
    owner_user_id = _as_int(user.get("id"), "owner")
    decision_id = _as_int(decision_id, "decision_id")
    expected_version = _as_int(expected_version, "expected_version")
    with app.state.db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = get_decision(conn, decision_id, owner_user_id)
            if row["version"] != expected_version:
                raise MasterDecisionError(
                    "decision_stale",
                    "This decision changed. Refresh it before responding",
                    409,
                )
            if row["state"] != "pending":
                raise MasterDecisionError(
                    "decision_not_pending",
                    "This decision is no longer pending",
                    409,
                )
            updated = conn.execute(
                "UPDATE master_decisions SET state = 'deferred', "
                "deferred_by_user_id = ?, deferred_at = CURRENT_TIMESTAMP, "
                "version = version + 1, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND state = 'pending' AND version = ?",
                (owner_user_id, decision_id, expected_version),
            )
            if updated.rowcount != 1:
                raise MasterDecisionError(
                    "decision_stale",
                    "This decision changed. Refresh it before responding",
                    409,
                )
            conn.execute(
                "UPDATE attention_items SET status = 'deferred' "
                "WHERE id = ? AND status = 'open'",
                (row["attention_item_id"],),
            )
            conn.execute(
                "INSERT INTO audit_log("
                "actor_user_id, action, target_type, target_id, metadata"
                ") VALUES (?, 'master.decision.defer', 'master_decision', ?, ?)",
                (
                    owner_user_id,
                    str(decision_id),
                    json.dumps(
                        {"task_id": row["requesting_job_id"]},
                        separators=(",", ":"),
                    ),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    projection = getattr(app.state, "master_projection", None)
    if projection is not None:
        projection.safe_project_decision(decision_id)
    return decision_payload(
        conn,
        conn.execute(
            "SELECT * FROM master_decisions WHERE id = ?", (decision_id,)
        ).fetchone(),
    )


def resolve_decision(
    conn: sqlite3.Connection,
    app: Any,
    user: Mapping[str, Any],
    decision_id: int,
    expected_version: int,
    response_value: Any,
) -> dict[str, Any]:
    owner_user_id = _as_int(user.get("id"), "owner")
    decision_id = _as_int(decision_id, "decision_id")
    expected_version = _as_int(expected_version, "expected_version")
    task_event: dict[str, int] | None = None
    continuation_run_id: int | None = None
    task_session_id: int | None = None
    continuation_project_id: int | None = None
    continuation_runner_id: str | None = None
    continuation_job_id: int | None = None
    with app.state.db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = get_decision(conn, decision_id, owner_user_id)
            if row["version"] != expected_version:
                raise MasterDecisionError(
                    "decision_stale",
                    "This decision changed. Refresh it before responding",
                    409,
                )
            if row["state"] not in {"pending", "deferred"}:
                raise MasterDecisionError(
                    "decision_not_pending",
                    "This decision has already been resolved",
                    409,
                )
            response = _validated_response(
                _json_object(row["response_shape_json"]), response_value
            )
            task_message_id = None
            continuation_run_id = None
            job_id = row["requesting_job_id"]
            if job_id is not None:
                job = conn.execute(
                    "SELECT j.*, s.profile_id, s.runner_id, "
                    "s.id AS task_session_id, p.default_model, p.hermes_home "
                    "FROM jobs j JOIN sessions s ON s.id = j.session_id "
                    "JOIN profiles p ON p.id = s.profile_id "
                    "WHERE j.id = ? AND j.created_by = ? "
                    "AND j.origin_master_session_id = ?",
                    (job_id, owner_user_id, row["master_session_id"]),
                ).fetchone()
                if job is None or job["status"] != "review":
                    raise MasterDecisionError(
                        "decision_task_stale",
                        "The requesting Task is no longer waiting for this decision",
                        409,
                    )
                task_session_id = int(job["task_session_id"])
                if conn.execute(
                    "SELECT 1 FROM runs WHERE session_id = ? "
                    "AND status IN ('queued', 'running')",
                    (task_session_id,),
                ).fetchone() is not None:
                    raise MasterDecisionError(
                        "decision_task_busy",
                        "The requesting Task is already running",
                        409,
                    )
                if not task_can_continue_after_decision(job):
                    raise MasterDecisionError(
                        "decision_task_invalid",
                        "The requesting Task cannot accept a decision continuation",
                        409,
                    )
                steps = json.loads(job["steps_state"] or "[]")
                current_step = int(job["current_step_idx"] or 0)
                response_message = (
                    f"Owner decision for: {row['prompt']}\n"
                    f"Response: {response['label']}"
                )
                task_message_cursor = conn.execute(
                    "INSERT INTO messages(session_id, role, content, author) "
                    "VALUES (?, 'user', ?, ?)",
                    (task_session_id, response_message[:8000], user.get("username")),
                )
                task_message_id = int(task_message_cursor.lastrowid)
                run_cursor = conn.execute(
                    "INSERT INTO runs("
                    "session_id, project_id, user_id, profile_id, runner_id, "
                    "status, prompt, model, hermes_home, kind"
                    ") VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, 'chat')",
                    (
                        task_session_id,
                        job["project_id"],
                        owner_user_id,
                        job["profile_id"],
                        job["runner_id"],
                        _continuation_prompt(
                            prompt=row["prompt"],
                            context=row["context"],
                            response_label=response["label"],
                        ),
                        job["default_model"],
                        job["hermes_home"],
                    ),
                )
                continuation_run_id = int(run_cursor.lastrowid)
                continuation_project_id = job["project_id"]
                continuation_runner_id = str(job["runner_id"])
                continuation_job_id = int(job_id)
                steps[current_step].update(
                    {
                        "status": "running",
                        "run_id": continuation_run_id,
                        "started_at": iso_now(),
                    }
                )
                for key in ("error", "finished_at"):
                    steps[current_step].pop(key, None)
                updated_job = conn.execute(
                    "UPDATE jobs SET status = 'running', blocked_reason = NULL, "
                    "rejected_reason = NULL, finished_at = NULL, steps_state = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'review'",
                    (
                        json.dumps(
                            steps, ensure_ascii=False, separators=(",", ":")
                        ),
                        job_id,
                    ),
                )
                if updated_job.rowcount != 1:
                    raise MasterDecisionError(
                        "decision_task_stale",
                        "The requesting Task is no longer waiting for this decision",
                        409,
                    )
                event_payload = json.dumps(
                    {
                        "decision_id": decision_id,
                        "task_id": job_id,
                        "prompt": row["prompt"],
                        "response": response["label"],
                        "actor_user_id": owner_user_id,
                        "occurred_at": iso_now(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                conn.execute(
                    "INSERT INTO events("
                    "run_id, session_id, project_id, seq, type, payload"
                    ") VALUES (?, ?, ?, 1, 'master.decision.resolved', ?)",
                    (
                        continuation_run_id,
                        task_session_id,
                        job["project_id"],
                        event_payload,
                    ),
                )
                task_event = append_task_update(
                    conn,
                    job_id=int(job_id),
                    mutation="review_approved",
                )

            updated = conn.execute(
                "UPDATE master_decisions SET state = 'resolved', "
                "response_json = ?, resolved_by_user_id = ?, "
                "resolved_at = CURRENT_TIMESTAMP, task_message_id = ?, "
                "continuation_run_id = ?, version = version + 1, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND state IN ('pending', 'deferred') "
                "AND version = ?",
                (
                    json.dumps(
                        response, ensure_ascii=False, separators=(",", ":")
                    ),
                    owner_user_id,
                    task_message_id,
                    continuation_run_id,
                    decision_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise MasterDecisionError(
                    "decision_stale",
                    "This decision changed. Refresh it before responding",
                    409,
                )
            conn.execute(
                "UPDATE attention_items SET status = 'resolved', "
                "resolved_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status IN ('open', 'deferred')",
                (row["attention_item_id"],),
            )
            conn.execute(
                "INSERT INTO audit_log("
                "actor_user_id, action, target_type, target_id, metadata"
                ") VALUES (?, 'master.decision.resolve', 'master_decision', ?, ?)",
                (
                    owner_user_id,
                    str(decision_id),
                    json.dumps(
                        {
                            "task_id": job_id,
                            "response": response["label"],
                            "task_message_id": task_message_id,
                            "continuation_run_id": continuation_run_id,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    if task_event is not None:
        app.state.hub.notify(task_event["session_id"])
        outbox_id = task_event.get("projection_outbox_id")
        projection = getattr(app.state, "master_projection", None)
        if outbox_id is not None and projection is not None:
            projection.safe_process_task_outbox(outbox_id)
    elif task_session_id is not None:
        app.state.hub.notify(task_session_id)
    if (
        continuation_run_id is not None
        and task_session_id is not None
        and continuation_job_id is not None
    ):
        app.state.worker.add_event(
            continuation_run_id,
            task_session_id,
            continuation_project_id,
            "run.queued",
            {
                "runner": continuation_runner_id,
                "job": continuation_job_id,
                "decision_id": decision_id,
            },
        )
    projection = getattr(app.state, "master_projection", None)
    if projection is not None:
        projection.safe_project_decision(decision_id)
    return decision_payload(
        conn,
        conn.execute(
            "SELECT * FROM master_decisions WHERE id = ?", (decision_id,)
        ).fetchone(),
    )

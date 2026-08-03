"""The Inbox notification ledger (#158) and attention self-clearing (#157).

**One store, two axes.** `attention_items` was already the durable home for work
that needs the owner. The Inbox extends that table instead of forking a second
notification store next to it, because a fork would immediately have to be kept
consistent with attention's own lifecycle (resolve, defer, cascade) - two copies
of the same truth is the bug, not the feature.

The two axes are independent on purpose:

* ``read_at`` - *has the owner seen this?* The header popover is ephemeral like a
  phone notification: it lists unread rows only, and clicking (or dismissing) one
  marks it read so it disappears from the header. Nothing is deleted.
* ``status`` - *does this still need the owner?* Unchanged meaning. An item the
  owner dismissed from the header is still ``open`` and still actionable in the
  Inbox; an item the system settled is ``resolved`` and stays as history.

**One id space.** ``item_key`` is the public identifier. Rows created directly by
a producer get ``attention:{id}``; items the attention route derives from other
tables (job reviews, node-script trust, satpam restarts) are recorded here under
the same synthetic id the route has always exposed (``job:12``), so the header,
the Inbox and ``POST /api/attention/{id}/act`` all address the same thing.

**Informational notifications.** Task outcomes (done / failed / cancelled) are
projected into the ledger with ``requires_action = 0``, carrying the failure
detail in ``body`` so an error is diagnosable from the Inbox without hunting for
the run. They are projected by reading the jobs table rather than by hooking
every producer: a pull projection cannot miss a transition that happened while
the server was down, and ``item_key`` UNIQUE makes it idempotent. A watermark
recorded when the ledger was created keeps pre-Inbox history from being replayed.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from . import app_settings

# Ids the attention route derives from other tables rather than storing.
PROJECTED_PREFIXES = ("job:", "script:", "satpam:")

TASK_OUTCOME_WATERMARK_KEY = "inbox.task_outcome_since"

SEVERITIES = ("info", "success", "warning", "error", "action")

_KIND_SEVERITY = {
    "master_budget": "warning",
    "satpam_recovery_failed": "error",
    "container_ops_migration": "warning",
    "task_outcome": "info",
}

_TASK_OUTCOME_SEVERITY = {
    "done": "success",
    "failed": "error",
    "cancelled": "info",
}

_TERMINAL_TASK_STATUSES = tuple(_TASK_OUTCOME_SEVERITY)

# Kinds whose whole content is a notice: there is no decision waiting behind
# them, so the owner acknowledging one from the header settles it (#157).
# Everything else keeps dismiss meaning *seen*, never *done* - dismissing an Ops
# migration must not claim the migration succeeded.
ACKNOWLEDGEABLE_KINDS = frozenset({"master_budget"})

_DEFAULT_INBOX_LIMIT = 60
_MAX_INBOX_LIMIT = 200
_MAX_BODY = 2000


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback


def severity_for_kind(kind: str) -> str:
    return _KIND_SEVERITY.get(kind, "action")


# ── Body text: the diagnosis, plus the step that clears it (#133) ─────────────


def body_for_item(kind: str, target: Mapping[str, Any]) -> str:
    if kind == "master_budget":
        reason = str(target.get("reason") or "its configured budget was reached")
        return (
            f"Master stopped its unattended queue because {reason}. "
            "Nothing was lost - queued Tasks stay queued. Turn Unattended back on "
            "from the Master desk when you want it to keep going."
        )
    if kind == "container_ops_migration":
        reason = str(target.get("reason") or "")
        return reason or "Open the project's Ops migration to review what changed."
    if kind == "permission_job":
        return "A run is waiting for a permission decision before it can continue."
    return ""


# ── Reading ───────────────────────────────────────────────────────────────────


def _row_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    item_key = str(data.get("item_key") or f"attention:{data['id']}")
    target = _json(data.get("target_json"), {})
    if not isinstance(target, dict):
        target = {}
    kind = str(data.get("kind") or "")
    # Severity and body are derived from the kind unless a producer stored its
    # own, so the six existing raw-SQL producers keep working untouched.
    severity = str(data.get("severity") or "")
    if severity not in SEVERITIES or severity == "action":
        severity = severity_for_kind(kind)
    body = str(data.get("body") or "") or body_for_item(kind, target)
    return {
        "id": item_key,
        "seq": int(data["id"]),
        "kind": kind,
        "title": str(data.get("title") or ""),
        "target": target,
        "inline_ok": bool(data.get("inline_ok")),
        "actions": _json(data.get("actions_json"), []) or [],
        "status": str(data.get("status") or "open"),
        "severity": severity,
        "body": body,
        "detail": _json(data.get("detail_json"), {}) or {},
        "requires_action": bool(
            data.get("requires_action")
            if data.get("requires_action") is not None
            else True
        ),
        "read": data.get("read_at") is not None,
        "read_at": data.get("read_at"),
        "created_at": data.get("created_at"),
        "resolved_at": data.get("resolved_at"),
    }


def unread_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM attention_items WHERE read_at IS NULL"
    ).fetchone()
    return int(row["n"] if row else 0)


def bounded_limit(limit: int | None) -> int:
    """The page size actually served, which is what pagination must compare to.

    A caller asking for 1000 gets 200; comparing the returned count against the
    *requested* 1000 would decide there is no next page and strand the rest.
    """
    return max(1, min(int(limit or _DEFAULT_INBOX_LIMIT), _MAX_INBOX_LIMIT))


def list_items(
    conn,
    *,
    unread_only: bool = False,
    limit: int = _DEFAULT_INBOX_LIMIT,
    before: int | None = None,
) -> list[dict[str, Any]]:
    bounded = bounded_limit(limit)
    clauses: list[str] = []
    params: list[Any] = []
    if unread_only:
        clauses.append("read_at IS NULL")
    if before is not None:
        clauses.append("id < ?")
        params.append(int(before))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    # Ordered by id, not created_at, because the cursor *is* the id: a projected
    # Task outcome carries the moment the work finished, which can be older than
    # a row recorded before it, and mixing the two would let "Load older" skip
    # rows. Insertion order is also the honest reading of "newest first" here -
    # it is the order the owner learned about things.
    rows = conn.execute(
        f"SELECT * FROM attention_items {where} ORDER BY id DESC LIMIT ?",
        (*params, bounded),
    ).fetchall()
    return [_row_payload(row) for row in rows]


def get_row(conn, item_key: str):
    return conn.execute(
        "SELECT * FROM attention_items WHERE item_key = ?", (item_key,)
    ).fetchone()


# ── Read state ────────────────────────────────────────────────────────────────


def set_read(conn, item_key: str, read: bool) -> bool:
    cursor = conn.execute(
        "UPDATE attention_items SET read_at = "
        + ("CURRENT_TIMESTAMP" if read else "NULL")
        + " WHERE item_key = ?",
        (item_key,),
    )
    return cursor.rowcount > 0


def acknowledge(conn, item_key: str) -> bool:
    """Dismiss from the header: always *seen*, and *done* for a pure notice.

    A Master budget notice has no decision behind it, so leaving it ``open``
    after the owner acknowledged it would keep it on the Master desk's work
    panel forever - the exact defect #157 reports, moved one surface across.
    """
    if not set_read(conn, item_key, True):
        return False
    conn.execute(
        "UPDATE attention_items SET status = 'resolved', "
        "resolved_at = CURRENT_TIMESTAMP "
        f"WHERE item_key = ? AND status = 'open' AND kind IN "
        f"({', '.join('?' for _ in ACKNOWLEDGEABLE_KINDS)})",
        (item_key, *sorted(ACKNOWLEDGEABLE_KINDS)),
    )
    return True


def mark_all_read(conn) -> int:
    cursor = conn.execute(
        "UPDATE attention_items SET read_at = CURRENT_TIMESTAMP WHERE read_at IS NULL"
    )
    return int(cursor.rowcount or 0)


# ── Settling stale items (#157) ───────────────────────────────────────────────


def settle_stale(conn) -> int:
    """Close items whose underlying state has moved on.

    ``master_budget`` says "Master stopped its unattended queue". Once the owner
    turns Unattended back on, that sentence describes a state that no longer
    exists, so the item is stale and clears itself - it does not sit in the
    header with a red badge forever (#157). It stays in the Inbox as history.
    """
    if not app_settings.get_master_settings(conn)["unattended"]:
        return 0
    cursor = conn.execute(
        "UPDATE attention_items SET status = 'resolved', "
        "resolved_at = CURRENT_TIMESTAMP "
        "WHERE kind = 'master_budget' AND status = 'open'"
    )
    return int(cursor.rowcount or 0)


def settle_seen(conn) -> int:
    """Retire settled work from the ephemeral header.

    An item that asked for a decision and no longer does - the review was
    approved, the decision resolved, the restart ran, the budget notice went
    stale - has nothing left to say, so it stops counting toward the unread
    badge. Purely informational notifications are untouched: only the owner
    decides they have read those. Either way the row stays in the Inbox.
    """
    cursor = conn.execute(
        "UPDATE attention_items SET read_at = CURRENT_TIMESTAMP "
        "WHERE read_at IS NULL AND requires_action = 1 AND status != 'open'"
    )
    return int(cursor.rowcount or 0)


# ── Recording what the header showed ──────────────────────────────────────────


def _upsert_projected(conn, item: Mapping[str, Any]) -> None:
    item_key = str(item["id"])
    kind = str(item.get("kind") or "")
    target = item.get("target") if isinstance(item.get("target"), dict) else {}
    actions = item.get("actions") or []
    existing = get_row(conn, item_key)
    if existing is None:
        conn.execute(
            "INSERT INTO attention_items(kind, title, target_json, inline_ok, "
            "actions_json, status, item_key, severity, body, requires_action, "
            "created_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, 1, "
            "COALESCE(?, CURRENT_TIMESTAMP))",
            (
                kind,
                str(item.get("title") or ""),
                json.dumps(target),
                1 if item.get("inline_ok") else 0,
                json.dumps(list(actions)),
                item_key,
                severity_for_kind(kind),
                body_for_item(kind, target),
                item.get("created_at"),
            ),
        )
        return
    conn.execute(
        "UPDATE attention_items SET kind = ?, title = ?, target_json = ?, "
        "inline_ok = ?, actions_json = ?, status = 'open', resolved_at = NULL "
        "WHERE item_key = ?",
        (
            kind,
            str(item.get("title") or ""),
            json.dumps(target),
            1 if item.get("inline_ok") else 0,
            json.dumps(list(actions)),
            item_key,
        ),
    )


def record_live_items(conn, live_items: Sequence[Mapping[str, Any]]) -> None:
    """Mirror the derived attention items into the ledger.

    Rows the producers wrote themselves are already in the ledger; only the items
    the attention route computes from other tables need a durable copy, so the
    Inbox is a true superset of everything the header ever showed.
    """
    seen: set[str] = set()
    for item in live_items:
        item_key = str(item.get("id") or "")
        if not item_key.startswith(PROJECTED_PREFIXES):
            continue
        seen.add(item_key)
        _upsert_projected(conn, item)
    stale = [
        str(row["item_key"])
        for row in conn.execute(
            "SELECT item_key FROM attention_items "
            "WHERE status = 'open' AND item_key IS NOT NULL"
        ).fetchall()
        if str(row["item_key"]).startswith(PROJECTED_PREFIXES)
        and str(row["item_key"]) not in seen
    ]
    for item_key in stale:
        # The work left the state that asked for the owner (the review was
        # approved, the restart ran). It stays in the Inbox as history;
        # settle_seen then retires it from the header.
        conn.execute(
            "UPDATE attention_items SET status = 'resolved', "
            "resolved_at = CURRENT_TIMESTAMP WHERE item_key = ?",
            (item_key,),
        )


# ── Informational notifications: how work ended ───────────────────────────────


def _task_failure_detail(conn, row: Any) -> str:
    job = dict(row)
    rejected = job.get("rejected_reason")
    if rejected:
        return str(rejected)
    steps = _json(job.get("steps_state"), [])
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict) and step.get("status") == "failed":
                error = step.get("error")
                if error:
                    return str(error)
    node = conn.execute(
        "SELECT error FROM node_states WHERE job_id = ? AND status = 'failed' "
        "AND error IS NOT NULL AND error != '' ORDER BY id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    if node and node["error"]:
        return str(node["error"])
    run = conn.execute(
        "SELECT r.error FROM runs r JOIN sessions s ON s.id = r.session_id "
        "WHERE s.job_id = ? AND r.error IS NOT NULL AND r.error != '' "
        "ORDER BY r.id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    if run and run["error"]:
        return str(run["error"])
    return ""


def ensure_task_outcome_watermark(conn) -> str:
    """Pin the moment the Inbox started listening.

    Called once at startup (and defensively on every projection) so an upgrade
    with years of finished Tasks in its database does not dump that history into
    a brand-new Inbox. It is deliberately *not* set by the migration: migration
    63 also runs against partial legacy schemas that have no app_settings table.
    """
    since = app_settings.get_setting(conn, TASK_OUTCOME_WATERMARK_KEY)
    if since:
        return since
    row = conn.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()
    now = str(row["now"])
    app_settings.set_setting(conn, TASK_OUTCOME_WATERMARK_KEY, now)
    return now


MAX_CLIENT_ERROR_TITLE = 160
MAX_CLIENT_ERRORS_PER_DAY = 50


def record_client_error(
    conn, *, key: str, title: str, detail: str, target: Mapping[str, Any] | None = None
) -> str | None:
    """Give an error the owner actually saw a home it survives a reload in.

    The global error surface raises API failures, unhandled rejections and
    stale-chunk failures as toasts and then forgets them, which is exactly the
    diagnostic the Inbox is supposed to keep (#158). The browser is not trusted
    to say anything else: the text is bounded, stored as an informational row
    that can never carry an action, and the whole channel is capped per day so a
    render loop cannot fill the ledger. ``item_key`` UNIQUE means the surface's
    own repeat collapsing (one toast, xN) writes one row.
    """
    cleaned = " ".join(str(title or "").split())[:MAX_CLIENT_ERROR_TITLE]
    if not cleaned or not str(key or "").strip():
        return None
    recent = conn.execute(
        "SELECT COUNT(*) AS n FROM attention_items WHERE kind = 'client_error' "
        "AND created_at >= datetime('now', '-1 day')"
    ).fetchone()
    if int(recent["n"] if recent else 0) >= MAX_CLIENT_ERRORS_PER_DAY:
        return None
    item_key = f"client-error:{str(key).strip()[:120]}"
    conn.execute(
        "INSERT OR IGNORE INTO attention_items(kind, title, target_json, inline_ok, "
        "actions_json, status, item_key, severity, body, detail_json, "
        "requires_action) VALUES ('client_error', ?, ?, 0, '[]', 'resolved', ?, "
        "'error', ?, '{}', 0)",
        (
            cleaned,
            json.dumps(dict(target or {})),
            item_key,
            str(detail or "")[:_MAX_BODY],
        ),
    )
    return item_key


def _task_outcome_title(title: str, status: str) -> str:
    label = title or "Task"
    return {
        "done": f"{label} finished",
        "failed": f"{label} failed",
        "cancelled": f"{label} was cancelled",
    }[status]


def _task_outcome_body(conn, job: Any, status: str) -> str:
    if status == "failed":
        detail = _task_failure_detail(conn, job)
        if detail:
            return (
                f"{detail}\n\nOpen the Task to read the full run output and retry "
                "the failed step."
            )
        return (
            "The Task stopped without a recorded reason. Open the Task to read "
            "its run output."
        )
    if status == "cancelled":
        return "The Task was cancelled before it finished."
    return "The Task completed every step."


def record_task_outcomes(conn) -> int:
    """Project terminal Task transitions into the ledger.

    Pull-based on purpose: it cannot miss a transition that happened while the
    server was down, and ``item_key`` UNIQUE makes replays free. The watermark is
    the moment the ledger was created, so a database with years of finished work
    does not dump that history into a brand-new Inbox.
    """
    since = ensure_task_outcome_watermark(conn)
    placeholders = ", ".join("?" for _ in _TERMINAL_TASK_STATUSES)
    rows = conn.execute(
        "SELECT j.* FROM jobs j WHERE j.status IN "
        f"({placeholders}) AND COALESCE(j.finished_at, j.updated_at) >= ? "
        "AND NOT EXISTS (SELECT 1 FROM attention_items a "
        "  WHERE a.item_key = 'task:' || j.id || ':' || j.status) "
        "ORDER BY j.id",
        (*_TERMINAL_TASK_STATUSES, since),
    ).fetchall()
    recorded = 0
    for job in rows:
        status = str(job["status"])
        item_key = f"task:{job['id']}:{status}"
        conn.execute(
            "INSERT OR IGNORE INTO attention_items(kind, title, target_json, "
            "inline_ok, actions_json, status, item_key, severity, body, "
            "detail_json, requires_action, created_at) "
            "VALUES ('task_outcome', ?, ?, 0, '[]', 'resolved', ?, ?, ?, ?, 0, "
            "COALESCE(?, CURRENT_TIMESTAMP))",
            (
                _task_outcome_title(str(job["title"] or ""), status),
                json.dumps(
                    {
                        "view": "task",
                        "job_id": int(job["id"]),
                        "engine": str(job["engine"] or "linear"),
                        "task_status": status,
                    }
                ),
                item_key,
                _TASK_OUTCOME_SEVERITY[status],
                _task_outcome_body(conn, job, status)[:_MAX_BODY],
                json.dumps({"task_status": status}),
                job["finished_at"] or job["updated_at"],
            ),
        )
        conn.execute(
            "UPDATE attention_items SET resolved_at = COALESCE(resolved_at, "
            "CURRENT_TIMESTAMP) WHERE item_key = ?",
            (item_key,),
        )
        recorded += 1
    return recorded


# ── Orchestration ─────────────────────────────────────────────────────────────


def settle(conn) -> None:
    """Run before the live attention list is computed."""
    settle_stale(conn)
    record_task_outcomes(conn)
    settle_seen(conn)


def record(conn, live_items: Iterable[Mapping[str, Any]]) -> None:
    """Run after the live attention list is computed."""
    record_live_items(conn, list(live_items))
    settle_seen(conn)

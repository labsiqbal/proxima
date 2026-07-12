"""Request-side prompt-collaboration dispatch (Brainstorm / Debate).

Extracted from routes/chat.py: starting a multi-agent collaboration (fan a prompt
to N profiles, or a debate) is a self-contained unit of the run-dispatch. The
worker owns execution; this owns setup. Bound to the request-thread ``db`` getter,
the ``app`` (for event emission), and ``profile_for_user`` via a small factory so
the chat gate just calls one function.
"""
from __future__ import annotations

import json
from typing import Any

from . import app_settings
from .prompt_collaborations import (
    build_brainstorm_child_prompt,
    build_debate_stance_prompt,
    collaboration_card_payload,
)
from .schemas import RunCreateRequest


def make_start_collaboration(app, db, profile_for_user):
    """Return the ``_start_prompt_collaboration(session, payload, user,
    active_profile, display_message)`` dispatcher used by create_run."""

    def _collaboration_profiles(payload: RunCreateRequest, active_profile: dict[str, Any], user: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen: set[int] = set()

        def add(profile: dict[str, Any]) -> None:
            pid = int(profile["id"])
            if pid not in seen and len(selected) < limit:
                seen.add(pid)
                selected.append(dict(profile))

        add(active_profile)
        for pid in payload.participant_profile_ids or []:
            add(profile_for_user(pid, user))
        if len(selected) < limit:
            rows = db().execute(
                """
                SELECT * FROM profiles
                WHERE user_id = ?
                ORDER BY CASE WHEN runner_id = ? THEN 1 ELSE 0 END, is_default DESC, id ASC
                """,
                (user["id"], active_profile["runner_id"]),
            ).fetchall()
            for row in rows:
                add(dict(row))
        return selected or [dict(active_profile)]

    def _queue_collaboration_child(collab: dict[str, Any], session: dict[str, Any], user: dict[str, Any], profile: dict[str, Any], prompt: str, kind: str, role: str) -> int:
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, collaboration_id, collaboration_role)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (session["id"], session["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"], kind, collab["id"], role),
        )
        run_id = int(cur.lastrowid)
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": kind, "collaboration_id": collab["id"], "role": role})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "collaboration.child.queued", collaboration_card_payload(collab, run_id, profile, role, "queued"))
        return run_id

    def _start_prompt_collaboration(session: dict[str, Any], payload: RunCreateRequest, user: dict[str, Any], active_profile: dict[str, Any], display_message: str) -> dict[str, Any]:
        mode = payload.prompt_mode
        settings = app_settings.get_collaboration_settings(db())
        limit = settings["brainstorm_agents"] if mode == "brainstorm" else 2
        profiles = _collaboration_profiles(payload, active_profile, user, limit)
        parent = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, collaboration_role, started_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, 'parent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (session["id"], session["project_id"], user["id"], active_profile["id"], active_profile["runner_id"], payload.message, payload.model or active_profile["default_model"], active_profile["hermes_home"], f"collab_{mode}"),
        )
        parent_run_id = int(parent.lastrowid)
        cur = db().execute(
            """
            INSERT INTO prompt_collaborations(session_id, project_id, user_id, parent_run_id, mode, status, prompt, profile_ids)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (session["id"], session["project_id"], user["id"], parent_run_id, mode, payload.message, json.dumps([p["id"] for p in profiles])),
        )
        collab_id = int(cur.lastrowid)
        collab = dict(db().execute("SELECT * FROM prompt_collaborations WHERE id = ?", (collab_id,)).fetchone())
        db().execute("UPDATE runs SET collaboration_id = ? WHERE id = ?", (collab_id, parent_run_id))
        app.state.worker.add_event(parent_run_id, session["id"], session["project_id"], "run.queued", {"runner": active_profile["runner_id"], "kind": f"collab_{mode}", "prompt_mode": mode, "label": display_message})
        app.state.worker.add_event(parent_run_id, session["id"], session["project_id"], "run.started", {"runner": active_profile["runner_id"], "kind": f"collab_{mode}"})
        # No "Starting…" delta: the cards announce the run, and the parent
        # bubble must stay byte-identical to the final message (header +
        # synthesis only) so the stream→saved handoff doesn't snap.
        child_ids: list[int] = []
        if mode == "brainstorm":
            for i, participant in enumerate(profiles):
                child_ids.append(_queue_collaboration_child(collab, session, user, participant, build_brainstorm_child_prompt(payload.message, participant, i), "collab_brainstorm_child", f"idea:{i + 1}"))
        else:
            first = profiles[0]
            child_ids.append(_queue_collaboration_child(collab, session, user, first, build_debate_stance_prompt(payload.message, first, "Opening stance"), "collab_debate_stance", "stance"))
        db().execute("UPDATE prompt_collaborations SET child_run_ids = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(child_ids), collab_id))
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session["id"],))
        return {"run_id": parent_run_id, "session_id": session["id"], "status": "running"}

    return _start_prompt_collaboration

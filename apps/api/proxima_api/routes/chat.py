"""Session / run / goal / message / event-stream / terminal routes for the Proxima API.

The chat runtime: sessions+messages, runs+goal loops, wiki-note draft/commit,
promote-to-workflow, dashboard, SSE event stream, the in-browser terminal WS, the
session event WS, and run get/cancel/permission. Extracted via register() —
handler bodies verbatim. user_from_token_query stays in main.py (shared) and is
passed via deps. No behavior change.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import mimetypes
import re
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from ..artifacts import update_produced_artifacts
from ..auth import hash_token
from ..db import connect
from ..terminal import TerminalSession
from .. import fsapi
from .. import app_settings
from .. import auth_health as auth_health_mod
from .. import design_scenes
from .. import features
from .. import kinds
from .. import state
from .. import image_providers
from .. import video_providers
from .. import wiki_memory
from .. import workflows as wf
from ..prompt_collaborations import collaboration_card_payload
from ..chat_collaboration import make_start_collaboration
from ..run_state import active_run_clause, stale_params
from ..runners import detect_runners
from ..goal_loop import GOAL_INSTRUCTIONS, build_goal_prompt
from ..schemas import (
    ChatSendRequest, GoalRequest, MessageCreateRequest,
    PermissionResponse, PromoteWorkflowRequest, RunCreateRequest,
    SessionCreateRequest, SessionUpdateRequest, WikiCommitRequest, WikiDraftRequest,
)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


def _decode_json(value: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("expected valid JSON") from exc


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    event["payload"] = _decode_json(event["payload"] or "{}")
    return event


async def _stream_session_events(
    app: Any,
    request: Request,
    session_id: int,
    after_id: int,
    db_factory: Callable[[], sqlite3.Connection],
) -> AsyncIterator[str]:
    hub = app.state.hub
    event_signal = hub.subscribe(session_id)
    last_id = after_id
    try:
        while not await request.is_disconnected():
            event_signal.clear()
            rows = db_factory().execute(
                "SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id ASC",
                (session_id, last_id),
            ).fetchall()
            for row in rows:
                last_id = row["id"]
                yield (
                    f"id: {row['id']}\nevent: {row['type']}\n"
                    f"data: {json.dumps(_event_payload(row))}\n\n"
                )
            try:
                await asyncio.wait_for(event_signal.wait(), timeout=15)
            except asyncio.TimeoutError as _exc:
                yield ": keepalive\n\n"
    finally:
        hub.unsubscribe(session_id, event_signal)


def _mode_display_message(mode: str, message: str, display_message: str | None) -> str:
    # No mode prefix on the stored user message — the UI already shows the mode
    # (collaboration cards header + result title).
    return display_message or message


def _mode_prompt(mode: str, message: str) -> str:
    if mode == "chat":
        return message
    if mode == "brainstorm":
        return (
            "You are running Proxima Brainstorm mode for this single user prompt. "
            "This is not Validate mode: do not review a previous answer. Explore possibilities before committing to one output.\n\n"
            "Return a concise brainstorm with:\n"
            "1. 4-6 distinct ideas or approaches.\n"
            "2. Key tradeoffs, risks, and when each approach fits.\n"
            "3. A recommended direction or synthesis.\n"
            "4. Suggested next prompt or action.\n\n"
            "Do not mutate files or implement unless the user explicitly asks for implementation inside this prompt.\n\n"
            f"User prompt:\n{message}"
        )
    return (
        "You are running Proxima Debate mode for this single user prompt. "
        "This is not Validate mode: do not review a previous answer. Debate the prompt from the start.\n\n"
        "Return a structured debate with:\n"
        "1. Two or three strong positions.\n"
        "2. Best argument for each position.\n"
        "3. Objections and rebuttals.\n"
        "4. Final synthesis and recommendation.\n"
        "5. Suggested next prompt or action.\n\n"
        "Do not mutate files or implement unless the user explicitly asks for implementation inside this prompt.\n\n"
        f"User prompt:\n{message}"
    )


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    feature_cfg = cfg
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    session_for_user = deps["session_for_user"]
    profile_for_user = deps["profile_for_user"]
    session_payload = deps["session_payload"]
    _project_root = deps["_project_root"]
    user_from_token_query = deps["user_from_token_query"]
    ensure_single_user_owner = deps["ensure_single_user_owner"]

    def _require_mode_feature(mode: str | None) -> None:
        # Feature-blind gate: the registry owns the mode -> feature-flag mapping,
        # so the chat gate never names "design"/DESIGN_STUDIO itself. A new gated
        # session kind is added by registering it in kinds.py.
        flag = kinds.feature_flag_for(mode)
        if flag:
            features.require(feature_cfg, flag)

    def _require_session_features(session: dict[str, Any]) -> None:
        _require_mode_feature(session.get("mode"))

    @app.get("/api/sessions")
    def list_sessions(user: dict[str, Any] = Depends(current_user)):
        # Main chat shows only the kinds the registry marks shown_in_main_chat
        # (kind is authoritative, never inferred from the title). A new session
        # mode = register it in kinds.py; this query needs no edit.
        modes = kinds.main_chat_modes()
        mode_placeholders = ",".join("?" for _ in modes)
        rows = db().execute(
            f"""
            SELECT s.*, p.slug AS project_slug, p.name AS project_name, pr.slug AS profile_slug, pr.name AS profile_name
            FROM sessions s
            LEFT JOIN projects p ON p.id = s.project_id
            LEFT JOIN profiles pr ON pr.id = s.profile_id
            WHERE s.owner_user_id = ?
              AND s.job_id IS NULL        -- workflow-job threads belong to Activity
              AND s.workflow_id IS NULL   -- workflow iterate/test chats are opened from Workflows
              AND IFNULL(s.mode, 'chat') IN ({mode_placeholders})
              AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
            ORDER BY s.updated_at DESC, s.id DESC
            """,
            (user["id"], *modes),
        ).fetchall()
        return {"sessions": [session_payload(dict(row)) for row in rows]}

    @app.get("/api/search")
    def search(q: str = "", user: dict[str, Any] = Depends(current_user)):
        term = q.strip()
        if len(term) < 2:
            return {"projects": [], "chats": [], "messages": []}
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        uid = user["id"]
        projects = [dict(r) for r in db().execute(
            "SELECT p.slug, p.name FROM projects p "
            "WHERE p.owner_user_id = ? AND (p.name LIKE ? ESCAPE '\\' OR p.slug LIKE ? ESCAPE '\\') ORDER BY p.name LIMIT 10",
            (uid, like, like)).fetchall()]
        chats = [dict(r) for r in db().execute(
            "SELECT id, title FROM sessions WHERE owner_user_id = ? AND title LIKE ? ESCAPE '\\' "
            "ORDER BY updated_at DESC LIMIT 10", (uid, like)).fetchall()]
        msgs = [dict(r) for r in db().execute(
            "SELECT m.session_id, m.role, substr(m.content, 1, 160) AS snippet, s.title AS session_title "
            "FROM messages m JOIN sessions s ON s.id = m.session_id WHERE s.owner_user_id = ? "
            "AND m.content LIKE ? ESCAPE '\\' ORDER BY m.id DESC LIMIT 15", (uid, like)).fetchall()]
        return {"projects": projects, "chats": chats, "messages": msgs}

    @app.post("/api/sessions", status_code=201)
    def create_session(payload: SessionCreateRequest, user: dict[str, Any] = Depends(current_user)):
        _require_mode_feature(payload.mode)
        profile = profile_for_user(payload.profile_id, user)
        project_id = None
        if payload.project_slug:
            project = visible_project(payload.project_slug, user)
            project_id = project["id"]
        title = (payload.title or "New session").strip() or "New session"
        cur = db().execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility, mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, project_id, user["id"], profile["id"], profile["runner_id"], payload.visibility, payload.mode),
        )
        row = db().execute(
            """
            SELECT s.*, p.slug AS project_slug, p.name AS project_name, pr.slug AS profile_slug, pr.name AS profile_name
            FROM sessions s LEFT JOIN projects p ON p.id=s.project_id LEFT JOIN profiles pr ON pr.id=s.profile_id WHERE s.id=?
            """,
            (cur.lastrowid,),
        ).fetchone()
        return session_payload(dict(row))

    @app.patch("/api/sessions/{session_id}")
    def update_session(session_id: int, payload: SessionUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        if session["owner_user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="only the session owner can change it")
        if payload.title is not None and payload.title.strip():
            db().execute(
                "UPDATE sessions SET title = ?, manual_title = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (payload.title.strip(), session_id),
            )
        if "project_slug" in payload.model_fields_set:
            # Adopt the chat into a project (slug, access-checked) or detach (null).
            project_id = visible_project(payload.project_slug, user)["id"] if payload.project_slug else None
            db().execute(
                "UPDATE sessions SET project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project_id, session_id),
            )
        if "profile_id" in payload.model_fields_set:
            # Persist the chat's agent (owned/valid via profile_for_user), keeping
            # runner_id in sync so the choice survives a reload.
            profile = profile_for_user(payload.profile_id, user)
            db().execute(
                "UPDATE sessions SET profile_id = ?, runner_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (profile["id"], profile["runner_id"], session_id),
            )
        row = db().execute(
            """
            SELECT s.*, p.slug AS project_slug, p.name AS project_name, pr.slug AS profile_slug, pr.name AS profile_name
            FROM sessions s LEFT JOIN projects p ON p.id=s.project_id LEFT JOIN profiles pr ON pr.id=s.profile_id WHERE s.id=?
            """,
            (session_id,),
        ).fetchone()
        return session_payload(dict(row))

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: int, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        if session["owner_user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="only the session owner can delete it")
        if session["job_id"]:  # a job's thread — delete the job (keeps it from orphaning)
            raise HTTPException(status_code=400, detail="this thread belongs to a job; delete the job instead")
        db().execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return {"ok": True, "id": session_id}

    @app.get("/api/sessions/{session_id}/messages")
    def list_messages(session_id: int, user: dict[str, Any] = Depends(current_user)):
        session_for_user(session_id, user)
        rows = db().execute("SELECT id, role, content, author, run_id, output_links, created_at FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
        # Batch activity + duration for every assistant run in one pass each
        # (avoids an N+1: previously 2 queries per assistant message).
        run_ids = [r["run_id"] for r in rows if r["role"] == "assistant" and r["run_id"]]
        activity_by_run: dict[int, list[dict[str, Any]]] = {}
        duration_by_run: dict[int, int] = {}
        if run_ids:
            run_id_filter = ",".join(str(_as_int(rid)) for rid in run_ids)
            order: dict[int, list[str]] = {}
            items: dict[int, dict[str, dict[str, Any]]] = {}
            for e in db().execute(
                "SELECT run_id, type, payload FROM events WHERE instr(',' || ? || ',', ',' || run_id || ',') > 0 AND type IN ('tool.start','tool.complete') ORDER BY seq",
                (run_id_filter,),
            ).fetchall():
                rid = e["run_id"]
                p = _decode_json(e["payload"] or "{}")
                tid = str(p.get("id") or "")
                if not tid:
                    continue
                order.setdefault(rid, [])
                items.setdefault(rid, {})
                if e["type"] == "tool.start":
                    if tid not in items[rid]:
                        order[rid].append(tid)
                    title = str(p.get("title") or "tool")
                    items[rid][tid] = {"title": title, "status": "running", "subagent": title.strip().lower() == "task"}
                elif tid in items[rid]:
                    items[rid][tid]["status"] = str(p.get("status") or "completed")
            for rid in order:
                activity_by_run[rid] = [items[rid][t] for t in order[rid]]
            for d in db().execute(
                "SELECT id, (julianday(finished_at) - julianday(started_at)) * 86400 AS d "
                "FROM runs WHERE instr(',' || ? || ',', ',' || id || ',') > 0 AND started_at IS NOT NULL AND finished_at IS NOT NULL",
                (run_id_filter,),
            ).fetchall():
                if d["d"] and d["d"] >= 1:
                    duration_by_run[d["id"]] = round(d["d"])
        out = []
        for row in rows:
            m = dict(row)
            try:
                links = _decode_json(m.pop("output_links", "[]") or "[]")
            except Exception:
                links = []
            if links:
                m["output_links"] = links
            if m.get("role") == "assistant" and m.get("run_id"):
                act = activity_by_run.get(m["run_id"], [])
                # The interactive question-form isn't a tool call, so surface it as
                # a synthetic activity step so the panel reflects card creation too.
                if "<question-form" in (m.get("content") or ""):
                    act = act + [{"title": "Interactive form", "status": "completed", "subagent": False}]
                if act:
                    m["activity"] = act
                if m["run_id"] in duration_by_run:
                    m["duration_s"] = duration_by_run[m["run_id"]]
            out.append(m)
        g = db().execute("SELECT goal_text, goal_status, goal_iteration, goal_max FROM sessions WHERE id = ?", (session_id,)).fetchone()
        goal = None
        if g and g["goal_text"] and g["goal_status"]:
            goal = {"objective": g["goal_text"], "status": g["goal_status"], "iteration": g["goal_iteration"], "max": g["goal_max"]}
        return {"messages": out, "goal": goal}

    @app.post("/api/sessions/{session_id}/messages")
    def create_message(session_id: int, payload: MessageCreateRequest, user: dict[str, Any] = Depends(current_user)):
        features.require_command(feature_cfg, payload.content)
        session = session_for_user(session_id, user)
        _require_session_features(session)
        author = user["username"] if payload.role == "user" else None
        cur = db().execute("INSERT INTO messages(session_id, role, content, author) VALUES (?, ?, ?, ?)", (session_id, payload.role, payload.content, author))
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
        return {"id": cur.lastrowid, "role": payload.role, "content": payload.content}

    _start_prompt_collaboration = make_start_collaboration(app, db, profile_for_user)

    @app.post("/api/sessions/{session_id}/runs", status_code=202)
    def create_run(session_id: int, payload: RunCreateRequest, user: dict[str, Any] = Depends(current_user)):
        # Feature preflight must precede the user-message insert and collaboration
        # dispatch. In particular, prompt modes must not bypass media command guards.
        features.require_command(feature_cfg, payload.message)
        session = session_for_user(session_id, user)
        _require_session_features(session)
        # Each collaborator runs with THEIR OWN profile (not the session creator's),
        # so a shared-project member can work in any task/chat with their own agent.
        profile = profile_for_user(payload.profile_id, user)
        if payload.instant_result is not None and not session.get("workflow_id"):
            raise HTTPException(status_code=400, detail="instant result is only available in workflow iteration sessions")
        if payload.instant_result is not None and payload.prompt_mode != "chat":
            raise HTTPException(status_code=400, detail="prompt modes are only available for normal chat runs")
        display_message = _mode_display_message(payload.prompt_mode, payload.message, payload.display_message)
        db().execute("INSERT INTO messages(session_id, role, content, author) VALUES (?, 'user', ?, ?)", (session_id, display_message, user["username"]))
        if payload.prompt_mode != "chat":
            return _start_prompt_collaboration(session, payload, user, profile, display_message)
        # Media prompts (/image, /video, "buat video …") short-circuit to the selected
        # generation provider — the ACP agent never sees them (left to improvise, it
        # builds a studio draft instead of generating). This is the endpoint the chat
        # UI actually posts to, so the interception must live here, not just in
        # /api/chat/send.
        if payload.instant_result is None:
            media = _maybe_complete_chat_media(session_id, payload, user)
            if media is not None:
                return media
        # Resume a goal that paused waiting for the user: their reply re-enters goal
        # mode (instructions appended so the loop continues from this turn).
        prompt = _mode_prompt(payload.prompt_mode, payload.message)
        goal = db().execute("SELECT goal_text, goal_status FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if goal and goal["goal_status"] == "blocked" and goal["goal_text"]:
            prompt = payload.message + GOAL_INSTRUCTIONS
            db().execute("UPDATE sessions SET goal_status = 'running' WHERE id = ?", (session_id,))
        if payload.instant_result is not None:
            cur = db().execute(
                """
                INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, started_at, heartbeat_at, finished_at)
                VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (session_id, session["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, payload.model or profile["default_model"], profile["hermes_home"], payload.prompt_mode),
            )
            run_id = _as_int(cur.lastrowid)
            msg = db().execute(
                "INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, payload.instant_result.strip(), profile["name"], run_id),
            )
            app.state.worker.add_event(run_id, session_id, session["project_id"], "run.queued", {"runner": profile["runner_id"], "label": display_message, "prompt_mode": payload.prompt_mode})
            app.state.worker.add_event(run_id, session_id, session["project_id"], "run.started", {})
            app.state.worker.add_event(run_id, session_id, session["project_id"], "message.complete", {"message_id": msg.lastrowid, "text": payload.instant_result.strip(), "output_links": []})
            app.state.worker.add_event(run_id, session_id, session["project_id"], "run.completed", {"stop_reason": "instant"})
            db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
            return {"run_id": run_id, "session_id": session_id, "status": "completed"}
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (session_id, session["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, payload.model or profile["default_model"], profile["hermes_home"], payload.prompt_mode),
        )
        run_id = _as_int(cur.lastrowid)
        app.state.worker.add_event(run_id, session_id, session["project_id"], "run.queued", {"runner": profile["runner_id"], "label": display_message, "prompt_mode": payload.prompt_mode})
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
        return {"run_id": run_id, "session_id": session_id, "status": "queued"}

    @app.post("/api/sessions/{session_id}/goal", status_code=202)
    def start_goal(session_id: int, payload: GoalRequest, user: dict[str, Any] = Depends(current_user)):
        features.require_command(feature_cfg, payload.objective)
        session = session_for_user(session_id, user)
        _require_session_features(session)
        profile = profile_for_user(payload.profile_id, user)
        db().execute(
            "UPDATE sessions SET goal_text = ?, goal_status = 'running', goal_iteration = 0, goal_max = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (payload.objective, payload.max_iter, session_id),
        )
        db().execute("INSERT INTO messages(session_id, role, content, author) VALUES (?, 'user', ?, ?)", (session_id, f"🎯 Goal: {payload.objective}", user["username"]))
        cur = db().execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (session_id, session["project_id"], user["id"], profile["id"], profile["runner_id"], build_goal_prompt(payload.objective, True), payload.model or profile["default_model"], profile["hermes_home"]),
        )
        run_id = _as_int(cur.lastrowid)
        app.state.worker.add_event(run_id, session_id, session["project_id"], "goal.update", {"status": "running", "iteration": 0, "max": payload.max_iter, "objective": payload.objective})
        app.state.worker.add_event(run_id, session_id, session["project_id"], "run.queued", {"runner": profile["runner_id"], "goal": True})
        return {"run_id": run_id, "session_id": session_id, "status": "running"}

    @app.post("/api/sessions/{session_id}/goal/cancel")
    def cancel_goal(session_id: int, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        db().execute("UPDATE sessions SET goal_status = 'cancelled' WHERE id = ? AND goal_status = 'running'", (session_id,))
        active = db().execute(
            "SELECT id, project_id FROM runs WHERE session_id = ? AND status IN ('queued','running') ORDER BY id DESC",
            (session_id,),
        ).fetchall()
        if active:
            # Mark the run cancelled BEFORE signalling the agent, so the worker's
            # post-prompt guard sees 'cancelled' and doesn't save the interrupted
            # turn as completed. A still-queued continuation is cancelled too, so
            # claim_run won't execute one more goal turn after the user cancelled.
            db().execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE session_id = ? AND status IN ('queued','running')",
                (session_id,),
            )
            for r in active:
                app.state.worker.add_event(_as_int(r["id"]), session_id, r["project_id"], "run.cancelled", {})
                app.state.worker.cancel(_as_int(r["id"]))
        lr = db().execute("SELECT id FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        if lr:
            app.state.worker.add_event(_as_int(lr["id"]), session_id, session["project_id"], "goal.update", {"status": "cancelled"})
        return {"status": "cancelled"}

    def _session_wiki_root(session: dict[str, Any], user: dict[str, Any]) -> Path | None:
        """The session's project wiki. Wiki is project-scoped — a project-less chat
        has no wiki target."""
        if not session["project_id"]:
            return None
        prow = db().execute("SELECT slug FROM projects WHERE id = ?", (session["project_id"],)).fetchone()
        return _project_root(prow["slug"], user) / "wiki" if prow else None

    @app.post("/api/sessions/{session_id}/wiki-note/draft", status_code=202)
    def wiki_note_draft(session_id: int, payload: WikiDraftRequest, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        _require_session_features(session)
        wiki_root = _session_wiki_root(session, user)
        if wiki_root is None:
            raise HTTPException(status_code=400, detail="This chat has no project, so there is no wiki to save to.")
        profile = profile_for_user(payload.profile_id, user)
        notes = fsapi.walk_files(wiki_root) if Path(wiki_root).is_dir() else []
        prompt = wiki_memory.build_draft_prompt(notes)
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, 'wiki_draft')
            """,
            (session_id, session["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"]),
        )
        run_id = _as_int(cur.lastrowid)
        app.state.worker.add_event(run_id, session_id, session["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": "wiki_draft"})
        return {"run_id": run_id, "session_id": session_id, "status": "queued"}

    @app.post("/api/sessions/{session_id}/promote-workflow", status_code=202)
    def promote_workflow(session_id: int, payload: PromoteWorkflowRequest, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        _require_session_features(session)
        profile = profile_for_user(payload.profile_id, user)
        rows = db().execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 50", (session_id,)
        ).fetchall()
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in reversed(rows))
        if payload.engine == "graph":
            features.require(feature_cfg, features.WORKFLOW_GRAPH)
        graph_planning = payload.engine == "graph" or (
            payload.engine == "auto"
            and features.enabled(feature_cfg, features.WORKFLOW_GRAPH)
        )
        prompt = wf.architect_system(graph=graph_planning) + "\n\nCONVERSATION:\n" + convo
        run_kind = "workflow_graph_draft" if graph_planning else "workflow_draft"
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (session_id, session["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"], run_kind),
        )
        run_id = _as_int(cur.lastrowid)
        app.state.worker.add_event(run_id, session_id, session["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": run_kind})
        return {"run_id": run_id, "session_id": session_id, "status": "queued"}

    @app.post("/api/sessions/{session_id}/wiki-note/commit")
    def wiki_note_commit(session_id: int, payload: WikiCommitRequest, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        _require_session_features(session)
        root = _session_wiki_root(session, user)
        if root is None:
            raise HTTPException(status_code=400, detail="no wiki for this session")
        try:
            if payload.mode == "append":
                try:
                    prior = fsapi.read_file(root, payload.path)
                except fsapi.FsError as _exc:
                    prior = ""
                content = (prior.rstrip() + "\n\n" + payload.content.strip() + "\n") if prior else payload.content
            else:
                content = payload.content
            fsapi.write_file(root, payload.path, content)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, 'wiki.note.commit', 'wiki', ?, ?)",
            (user["id"], payload.path, json.dumps({"mode": payload.mode, "session_id": session_id})),
        )
        try:
            wiki_memory.rebuild_index(root)
        except Exception:
            logging.getLogger("proxima.api").exception("wiki index rebuild failed (non-fatal)")
        return {"ok": True, "path": payload.path}

    def _chat_media_kind(message: str) -> tuple[str, str] | None:
        """Command-only by owner decision: generation costs credits, so only an
        explicit /image, /gambar, /video, or /video-studio triggers it — natural
        language ("buat video …") always goes to the agent."""
        text = (message or "").strip()
        low = text.lower()
        command = low.split(maxsplit=1)[0] if low else ""
        arg = text[len(command):].strip()
        if command in {"/design", "/image-studio", "/design-studio"}:
            return "image-studio", arg or "Create a Design Studio draft."
        if command in {"/image", "/gambar"}:
            return "image", arg or "Generate an image."
        if command == "/video-studio":
            return "video-studio", arg or "Create a Video Studio project."
        if command == "/video":
            return "video", arg or "Generate a video."
        return None

    def _project_slug_for_session(session: sqlite3.Row | dict[str, Any]) -> str | None:
        project_id = session["project_id"] if "project_id" in session.keys() else None
        if not project_id:
            return None
        row = db().execute("SELECT slug FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row["slug"] if row else None

    def _merge_session_artifact(conn: sqlite3.Connection, session_id: int, artifact: dict[str, Any]) -> None:
        def _merge(current: list[Any]) -> list[Any]:
            merged = {(a.get("type"), a.get("path")): a for a in current if isinstance(a, dict)}
            merged[(artifact.get("type"), artifact.get("path"))] = artifact
            return list(merged.values())
        update_produced_artifacts(conn, session_id, _merge)

    def _resolve_chat_image_gen() -> dict[str, Any]:
        cfg = app_settings.get_json(db(), app_settings.IMAGE_GEN_KEY)
        if not isinstance(cfg, dict) or cfg.get("provider") not in image_providers.PROVIDERS:
            return {"provider": image_providers.DEFAULT_PROVIDER, "baseUrl": None, "model": None, "apiKey": None}
        return cfg

    def _resolve_chat_video_gen() -> dict[str, Any]:
        cfg = app_settings.get_json(db(), app_settings.VIDEO_GEN_KEY)
        if not isinstance(cfg, dict) or cfg.get("provider") not in video_providers.VIDEO_PROVIDER_IDS:
            return {"provider": video_providers.DEFAULT_PROVIDER, "model": video_providers.DEFAULT_MODEL}
        return cfg

    def _video_title(prompt: str) -> str:
        words = re.sub(r"[^a-zA-Z0-9 ]+", " ", prompt).strip().split()
        return " ".join(words[:8]) or "Video draft"

    def _slug(text: str, fallback: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
        return base or fallback

    def _write_video_shell(root: Path, prompt: str) -> dict[str, Any]:
        title = _video_title(prompt)
        video_id = f"{_slug(title, 'video')}-{_as_int(time.time())}"
        rel_dir = f"artifacts/video/{video_id}"
        d = fsapi.resolve_in_project(root, rel_dir)
        d.mkdir(parents=True, exist_ok=True)
        escaped_title = html.escape(title)
        escaped_prompt = html.escape(prompt)
        (d / "index.html").write_text(f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>{escaped_title}</title></head>
<body style=\"margin:0;background:#0b0f19;color:#f8fafc;font-family:Inter,system-ui,sans-serif;display:grid;place-items:center;min-height:100vh\">
<main style=\"max-width:760px;padding:48px\"><p style=\"letter-spacing:.16em;text-transform:uppercase;color:#94a3b8\">Proxima video artifact</p><h1>{escaped_title}</h1><p style=\"font-size:20px;line-height:1.5\">{escaped_prompt}</p><p style=\"color:#94a3b8\">Open/Edit in Video Studio to turn this brief into a timeline/render.</p></main>
</body></html>""", encoding="utf-8")
        (d / "brief.json").write_text(json.dumps({"title": title, "prompt": prompt}, indent=2), encoding="utf-8")
        return {"type": "video", "id": video_id, "title": title, "path": rel_dir}

    def _complete_media_run(session: sqlite3.Row | dict[str, Any], payload: ChatSendRequest | RunCreateRequest, user: dict[str, Any], kind: str, artifact: dict[str, Any], text: str) -> dict[str, Any]:
        profile = profile_for_user(payload.profile_id, user)
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, started_at, heartbeat_at, finished_at)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (session["id"], session["project_id"], user["id"], profile["id"], profile["runner_id"], payload.message, payload.model or profile["default_model"], profile["hermes_home"], f"media_{kind}"),
        )
        run_id = _as_int(cur.lastrowid)
        msg = db().execute("INSERT INTO messages(session_id, role, content, author, run_id, output_links) VALUES (?, 'assistant', ?, ?, ?, ?)", (session["id"], text, profile["name"], run_id, json.dumps([artifact])))
        _merge_session_artifact(db(), session["id"], artifact)
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": f"media_{kind}"})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.started", {})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "message.complete", {"message_id": msg.lastrowid, "text": text, "output_links": [artifact]})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.completed", {"stop_reason": "media"})
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session["id"],))
        return {"run_id": run_id, "session_id": session["id"], "status": "completed", "media_action": kind, "artifact": artifact}

    def _complete_media_ask(session: sqlite3.Row | dict[str, Any], payload: ChatSendRequest | RunCreateRequest, user: dict[str, Any], kind: str, text: str) -> dict[str, Any]:
        """Post a form-only assistant turn (a <question-form>, no artifact, nothing
        generated) — used when a /image or /design brief is too thin to act on. The
        form's ``submit-as`` re-issues the command with the answers, so the SAME media
        path fires again with an enriched brief. Mirrors _complete_media_run's events
        so the chat renders it like any finished turn, just without output links."""
        profile = profile_for_user(payload.profile_id, user)
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, started_at, heartbeat_at, finished_at)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (session["id"], session["project_id"], user["id"], profile["id"], profile["runner_id"], payload.message, payload.model or profile["default_model"], profile["hermes_home"], f"media_ask_{kind}"),
        )
        run_id = _as_int(cur.lastrowid)
        msg = db().execute("INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'assistant', ?, ?, ?)", (session["id"], text, profile["name"], run_id))
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": f"media_ask_{kind}"})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.started", {})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "message.complete", {"message_id": msg.lastrowid, "text": text, "output_links": []})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.completed", {"stop_reason": "media"})
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session["id"],))
        return {"run_id": run_id, "session_id": session["id"], "status": "completed", "media_action": f"{kind}_ask"}

    # Compact clarifying forms shown when a /image or /design brief is too thin to act
    # on. `submit-as` makes answering re-issue the command with the answers as the brief.
    _MEDIA_BRIEF_FORMS = {
        "image": (
            "Before I generate — a couple of quick things so it lands right:\n"
            '<question-form id="image-brief" title="What should I make?" submit-as="/image">\n'
            '{"questions":[\n'
            '  {"id":"subject","label":"What should the image show?","type":"text","required":true,"placeholder":"e.g. an orange cat asleep on a sofa"},\n'
            '  {"id":"style","label":"Style / mood","type":"text","placeholder":"e.g. photographic, cinematic, flat illustration"},\n'
            '  {"id":"aspect","label":"Size / aspect","type":"radio","options":[{"value":"square 1:1","label":"Square 1:1"},{"value":"portrait 4:5","label":"Portrait 4:5"},{"value":"story 9:16","label":"Story 9:16"},{"value":"landscape 16:9","label":"Landscape 16:9"}]}\n'
            "]}\n"
            "</question-form>"
        ),
        "image-studio": (
            "Before I draft the design — a few quick things so it's on-brief:\n"
            '<question-form id="design-brief" title="What are we designing?" submit-as="/design">\n'
            '{"questions":[\n'
            '  {"id":"goal","label":"Main message / goal?","type":"text","required":true,"placeholder":"e.g. promo 20% off the new coffee menu"},\n'
            '  {"id":"format","label":"Format","type":"radio","options":[{"value":"IG post 1:1","label":"IG post 1:1"},{"value":"IG story 9:16","label":"IG story 9:16"},{"value":"poster","label":"Poster"},{"value":"web banner","label":"Web / banner"}]},\n'
            '  {"id":"audience","label":"Who is it for?","type":"text","placeholder":"e.g. young adults 18–25"},\n'
            '  {"id":"mood","label":"Visual mood / style","type":"text","placeholder":"e.g. clean minimal, bold energetic"},\n'
            '  {"id":"copy","label":"Specific headline/copy? (optional)","type":"text","placeholder":"leave blank to let me write it"}\n'
            "]}\n"
            "</question-form>"
        ),
    }

    def _media_brief_is_thin(message: str) -> bool:
        """A brief is 'thin' when the user gave (almost) no direction: no attached
        reference image and fewer than 3 words after the command. Answers submitted
        back from the form are long, so they never re-trigger the ask."""
        text = (message or "").strip()
        command = text.lower().split(maxsplit=1)[0] if text else ""
        arg = text[len(command):].strip()
        if re.search(r"!\[[^\]]*\]\([^)]+\)", arg):
            return False  # has an attached image — intent is clear enough
        words = [w for w in re.split(r"\s+", re.sub(r"!\[[^\]]*\]\([^)]+\)", "", arg)) if w]
        return len(words) < 3

    MEDIA_RUN_MAX_SECONDS = 1800.0

    def _finish_media_run(run_id: int, session_id: int, project_id: int | None, profile_name: str, generate_fn, database_path: str) -> None:
        """Background completion of a media run: heartbeats while the provider works
        (keeps the stale-run reaper away), then lands the result — or the error — as
        an assistant message + run events, exactly like an agent run finishing."""
        worker = app.state.worker
        conn = connect(database_path)
        try:
            done = threading.Event()
            box: dict[str, Any] = {}

            def work() -> None:
                try:
                    box["result"] = generate_fn()
                except Exception as exc:  # provider errors surface in-thread, in the chat
                    box["error"] = exc
                finally:
                    done.set()

            threading.Thread(target=work, daemon=True, name=f"media-run-{run_id}-gen").start()
            started = time.monotonic()
            while not done.wait(20.0):
                if time.monotonic() - started > MEDIA_RUN_MAX_SECONDS:
                    box.setdefault("error", TimeoutError(f"Media generation timed out after {_as_int(MEDIA_RUN_MAX_SECONDS)}s."))
                    break
                with app.state.db_lock:
                    conn.execute("UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
            error = box.get("error")
            if error is not None:
                detail = str(error) or error.__class__.__name__
                text = f"⚠️ Media generation failed: {detail}"
                with app.state.db_lock:
                    conn.execute("UPDATE runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
                    msg = conn.execute("INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'assistant', ?, ?, ?)", (session_id, text, profile_name, run_id))
                    conn.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
                worker.add_event(run_id, session_id, project_id, "message.complete", {"message_id": msg.lastrowid, "text": text, "output_links": []})
                worker.add_event(run_id, session_id, project_id, "run.failed", {"error": detail})
                run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                if run_row:
                    worker._advance_job(dict(run_row), f"BLOCKED: {text}")
                return
            artifact, text = box["result"]
            with app.state.db_lock:
                conn.execute("UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
                msg = conn.execute("INSERT INTO messages(session_id, role, content, author, run_id, output_links) VALUES (?, 'assistant', ?, ?, ?, ?)", (session_id, text, profile_name, run_id, json.dumps([artifact])))
                _merge_session_artifact(conn, session_id, artifact)
                conn.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
            worker.add_event(run_id, session_id, project_id, "message.complete", {"message_id": msg.lastrowid, "text": text, "output_links": [artifact]})
            worker.add_event(run_id, session_id, project_id, "run.completed", {"stop_reason": "media"})
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run_row:
                worker._advance_job(dict(run_row), text)
        except Exception:
            logging.getLogger("proxima.api").exception("media run %s finalization failed", run_id)
            with contextlib.suppress(Exception):
                with app.state.db_lock:
                    conn.execute("UPDATE runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
                worker.add_event(run_id, session_id, project_id, "run.failed", {"error": "internal error while saving the media result"})
        finally:
            conn.close()

    def _start_media_run(session: sqlite3.Row | dict[str, Any], payload: ChatSendRequest | RunCreateRequest, user: dict[str, Any], kind: str, generate_fn) -> dict[str, Any]:
        """Media generation can take minutes, so it must be VISIBLE and non-blocking:
        the run row + queued/started events are created immediately (typing indicator
        in chat, live row on Home) and a background thread finishes the work."""
        profile = profile_for_user(payload.profile_id, user)
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, started_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (session["id"], session["project_id"], user["id"], profile["id"], profile["runner_id"], payload.message, payload.model or profile["default_model"], profile["hermes_home"], f"media_{kind}"),
        )
        run_id = _as_int(cur.lastrowid)
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": f"media_{kind}", "label": payload.message})
        app.state.worker.add_event(run_id, session["id"], session["project_id"], "run.started", {})
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session["id"],))
        database_path = str((getattr(app.state, "config", {}) or {}).get("database_path") or "")
        threading.Thread(
            target=_finish_media_run,
            args=(run_id, session["id"], session["project_id"], profile["name"], generate_fn, database_path),
            daemon=True,
            name=f"media-run-{run_id}",
        ).start()
        return {"run_id": run_id, "session_id": session["id"], "status": "queued", "media_action": kind}

    def _maybe_complete_chat_media(session_id: int, payload: ChatSendRequest | RunCreateRequest, user: dict[str, Any]) -> dict[str, Any] | None:
        features.require_command(feature_cfg, payload.message)
        media = _chat_media_kind(payload.message)
        if not media:
            return None
        session = session_for_user(session_id, user)
        slug = payload.project_slug or _project_slug_for_session(session)
        if not slug:
            return None
        root = _project_root(slug, user)
        kind, prompt = media
        # Thin brief → clarify in THIS (main) chat with a compact form instead of
        # generating something generic. Answering re-issues the command (submit-as)
        # with the answers as the brief, so this same path runs again — now with enough
        # to go on. Only for the image + design surfaces; video keeps its own flow.
        if kind in _MEDIA_BRIEF_FORMS and _media_brief_is_thin(payload.message):
            return _complete_media_ask(session, payload, user, kind, _MEDIA_BRIEF_FORMS[kind])
        if kind == "image":
            cfg = _resolve_chat_image_gen()
            provider = image_providers.get_provider(cfg.get("provider"))
            caps = provider.capabilities or {}
            model = payload.model or cfg.get("model")
            # Images the user attached (the composer appends ![name](path) markdown) become
            # source/reference images when the provider can edit — so "/image … with this
            # logo" actually uses the logo instead of ignoring it. Strip the refs from the
            # prompt so the model gets clean instructions + the attached pixels.
            ref_paths = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", prompt)
            clean_prompt = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", prompt).strip()
            sources: list[tuple[bytes, str | None]] = []
            if ref_paths and caps.get("imageEdit"):
                for rel in ref_paths:
                    try:
                        src = fsapi.resolve_in_project(root, rel.strip())
                        if src.is_file():
                            sources.append((src.read_bytes(), mimetypes.guess_type(src.name)[0] or "image/png"))
                    except Exception:
                        logging.getLogger("proxima.api").debug("chat /image reference skipped: %s", rel, exc_info=True)
                if len(sources) > 1 and not caps.get("referenceImages"):
                    sources = sources[:1]
            image_bytes = sources[0][0] if sources else None
            image_mime = sources[0][1] if sources else None
            extra_images = sources[1:] or None
            gen_prompt = clean_prompt or (prompt if not ref_paths else "Compose an image using the attached reference image(s).")
            # Attached images but the selected provider can't use them (text-to-image only).
            refs_ignored = bool(ref_paths) and not sources

            def generate_image() -> tuple[dict[str, Any], str]:
                target = fsapi.resolve_in_project(root, f"artifacts/media/images/chat-{_as_int(time.time())}.png")
                target.parent.mkdir(parents=True, exist_ok=True)
                i = 1
                while target.exists():
                    target = target.parent / f"chat-{_as_int(time.time())}-{i}.png"
                    i += 1
                raw = image_providers.generate(
                    provider.id,
                    cfg.get("apiKey"),
                    prompt=gen_prompt,
                    model=model,
                    image_bytes=image_bytes,
                    image_mime=image_mime,
                    extra_images=extra_images,
                    base_url=cfg.get("baseUrl"),
                )
                target.write_bytes(raw)
                actions = ["use-as-reference"]
                if features.enabled(feature_cfg, features.DESIGN_STUDIO):
                    actions.insert(0, "open-design-studio")
                artifact = {"type": "image", "title": target.name, "path": str(target.relative_to(root)), "project_slug": slug, "actions": actions}
                text = f"Generated image artifact: `{artifact['path']}`. Saved as a reusable project artifact."
                if refs_ignored:
                    text += " Note: the attached image was not used as a reference — the selected image provider is text-to-image only. Pick a provider that supports image editing to compose with attachments."
                elif sources:
                    text += f" Used {len(sources)} attached image(s) as reference."
                if features.enabled(feature_cfg, features.DESIGN_STUDIO):
                    text += " Open/Edit it in Design Studio or use it as a reference."
                return artifact, text

            return _start_media_run(session, payload, user, "image", generate_image)
        if kind == "image-studio":
            # Seed a shell scene, then let the DESIGN AGENT compose it from the brief in
            # a linked design session — the draft arrives designed, not blank. Design
            # Studio applies the finished run when opened (appliedRunId recovery), or
            # streams it live if opened while the agent is still working.
            design_id, scene = design_scenes.scene_shell(prompt)
            design_session = create_session(SessionCreateRequest(title=f"Design: {scene['title']}", project_slug=slug, profile_id=payload.profile_id, mode="design"), user)
            scene["sessionId"] = design_session["id"]
            design_run = create_run(design_session["id"], RunCreateRequest(
                message=design_scenes.design_run_message(scene, prompt),
                display_message=prompt,
                profile_id=payload.profile_id,
                model=payload.model,
            ), user)
            artifact = design_scenes.persist_draft(root, design_id, scene, slug, run_pending_id=design_run["run_id"])
            text = f"Created Design Studio draft: `{artifact['path']}`. The design agent is composing it from your brief — open it in Design Studio to watch it land or edit."
            return _complete_media_run(session, payload, user, "image-studio", artifact, text)
        if kind == "video-studio":
            # Fast local file write — no provider involved, complete synchronously.
            artifact = _write_video_shell(root, prompt)
            artifact["project_slug"] = slug
            artifact["actions"] = ["open-video-studio"]
            text = f"Created Video Studio draft: `{artifact['path']}`. Open/Edit in Video Studio to build the timeline or render."
            return _complete_media_run(session, payload, user, "video-studio", artifact, text)
        cfg = _resolve_chat_video_gen()
        video_model = payload.model or cfg.get("model")

        def generate_video() -> tuple[dict[str, Any], str]:
            result = video_providers.generate(str(cfg.get("provider") or video_providers.DEFAULT_PROVIDER), prompt=prompt, model=video_model)
            if result.content is None and not result.url:
                raise video_providers.VideoProviderError("Video provider returned no video data.")
            ext = Path(result.filename or "video.mp4").suffix or ".mp4"
            target = fsapi.resolve_in_project(root, f"artifacts/media/videos/chat-{_as_int(time.time())}{ext}")
            target.parent.mkdir(parents=True, exist_ok=True)
            i = 1
            while target.exists():
                target = target.parent / f"chat-{_as_int(time.time())}-{i}{ext}"; i += 1
            if result.content is not None:
                target.write_bytes(result.content)
            else:
                target.write_text((result.url or "") + "\n", encoding="utf-8")
            meta = {"provider": cfg.get("provider"), "model": video_model, "prompt": prompt, "sourceUrl": result.url, "contentType": result.content_type}
            (target.with_suffix(target.suffix + ".json")).write_text(json.dumps(meta, indent=2), encoding="utf-8")
            artifact = {"type": "video-file", "title": target.name, "path": str(target.relative_to(root)), "project_slug": slug, "actions": ["send-to-video-studio", "use-as-reference"]}
            text = f"Generated video artifact: `{artifact['path']}`. Saved as a generated media result; send it to Video Studio only if you want to remix or build a timeline from it."
            return artifact, text

        return _start_media_run(session, payload, user, "video", generate_video)

    @app.post("/api/chat/send", status_code=202)
    def chat_send(payload: ChatSendRequest, user: dict[str, Any] = Depends(current_user)):
        # A rejected media command must not create an otherwise-empty chat.
        features.require_command(feature_cfg, payload.message)
        if payload.session_id is None:
            created = create_session(SessionCreateRequest(title=payload.message[:60] or "New session", project_slug=payload.project_slug, profile_id=payload.profile_id), user)
            session_id = created["id"]
        else:
            session_id = payload.session_id
        # Media interception happens inside create_run (shared with the session-runs
        # endpoint the chat UI posts to); project_slug rides along for new sessions.
        return create_run(session_id, RunCreateRequest(message=payload.message, profile_id=payload.profile_id, model=payload.model, project_slug=payload.project_slug), user)

    @app.get("/api/sessions/{session_id}/events")
    def list_events(session_id: int, after_id: int = 0, user: dict[str, Any] = Depends(current_user)):
        # Resume by events.id — the session-monotonic key. seq is per-run (it resets
        # to 1 each run), so it's NOT a valid session-level cursor.
        session_for_user(session_id, user)
        rows = db().execute("SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id ASC", (session_id, after_id)).fetchall()
        return {"events": [_event_payload(row) for row in rows]}

    @app.get("/api/dashboard")
    def dashboard(user: dict[str, Any] = Depends(current_user)):
        """Aggregated real-data summary for the Home dashboard."""
        from datetime import datetime as _dtm, timedelta as _td, timezone as _tz
        d = db()
        stale_seconds = _as_int(getattr(app.state, "config", {}).get("run_stale_seconds") or 60)
        active_runs_count = d.execute(
            "SELECT COUNT(DISTINCT session_id) AS c FROM runs WHERE "
            "((status = 'running' AND COALESCE(heartbeat_at, started_at, created_at) >= datetime('now', ?)) "
            "OR (status = 'queued' AND created_at >= datetime('now', ?)))",
            stale_params(stale_seconds),
        ).fetchone()["c"]
        counts = {
            "projects": d.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"],
            "chats": d.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
            "activeRuns": active_runs_count,
        }
        jbs = {r["status"]: r["c"] for r in d.execute("SELECT status, COUNT(*) AS c FROM jobs WHERE archived_at IS NULL GROUP BY status").fetchall()}
        jobs_by_status = {s: jbs.get(s, 0) for s in ("queued", "running", "review", "done")}
        recent = [dict(r) for r in d.execute(
            "SELECT s.id, s.title, s.workflow_id, s.updated_at, s.goal_status, s.mode, p.slug AS project_slug, "
            "(SELECT r.status FROM runs r WHERE r.session_id = s.id ORDER BY r.id DESC LIMIT 1) AS last_run_status "
            "FROM sessions s LEFT JOIN projects p ON p.id = s.project_id "
            "ORDER BY s.updated_at DESC LIMIT 7").fetchall()]
        active_sessions = [dict(r) for r in d.execute(
            f"SELECT s.id, s.title, s.workflow_id, s.updated_at, p.slug AS project_slug, "
            "MAX(COALESCE(r.heartbeat_at, r.started_at, r.created_at)) AS last_active_at "
            "FROM runs r JOIN sessions s ON s.id = r.session_id "
            "LEFT JOIN projects p ON p.id = s.project_id "
            f"WHERE {active_run_clause('r')} "
            "GROUP BY s.id ORDER BY last_active_at DESC LIMIT 5",
            stale_params(stale_seconds),
        ).fetchall()]
        projects = [dict(r) for r in d.execute(
            "SELECT p.slug, p.name, p.path, p.visibility, "
            "(SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) AS chats, "
            "(SELECT MAX(updated_at) FROM sessions s WHERE s.project_id = p.id) AS last_activity "
            "FROM projects p ORDER BY last_activity DESC").fetchall()]
        workflows_out = [
            {"id": r["id"], "name": r["name"], "category": r["category"], "steps": len(_decode_json(r["steps"] or "[]"))}
            for r in d.execute("SELECT id, name, category, steps FROM workflows WHERE graph IS NULL AND status != 'archived' ORDER BY updated_at DESC, id DESC LIMIT 6").fetchall()
        ]
        now_local = _dtm.now()
        schedules_out = []
        for r in d.execute(
            "SELECT sc.id, sc.cron, sc.enabled, w.name AS wf_name FROM schedules sc "
            "LEFT JOIN workflows w ON w.id = sc.workflow_id ORDER BY sc.enabled DESC, sc.id DESC LIMIT 6"
        ).fetchall():
            nxt = wf.next_cron_after(r["cron"], now_local) if r["enabled"] else None
            schedules_out.append({
                "id": r["id"], "workflow_name": r["wf_name"] or "Workflow", "cron": r["cron"],
                "cadence": wf.cadence_human(r["cron"]), "enabled": bool(r["enabled"]),
                "next_run": nxt.isoformat() if nxt else None,
            })
        review_count = d.execute("SELECT COUNT(*) AS c FROM jobs WHERE status = 'review' AND archived_at IS NULL").fetchone()["c"]
        review_jobs = [dict(r) for r in d.execute(
            "SELECT j.id, j.title, j.updated_at, j.workflow_id, p.slug AS project_slug, w.name AS workflow_name "
            "FROM jobs j LEFT JOIN projects p ON p.id = j.project_id LEFT JOIN workflows w ON w.id = j.workflow_id "
            "WHERE j.status = 'review' AND j.archived_at IS NULL ORDER BY j.updated_at DESC, j.id DESC LIMIT 5"
        ).fetchall()]

        def _artifact_kind(path: Path) -> str:
            ext = path.suffix.lower()
            if path.name == "scene.json" and "artifacts/design" in str(path):
                return "design"
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"):
                return "image"
            if ext in (".mp4", ".webm", ".mov"):
                return "video-file"
            if ext in (".html", ".htm"):
                return "page"
            if ext in (".md", ".txt", ".pdf", ".doc", ".docx"):
                return "doc"
            return "file"

        recent_artifacts: list[dict[str, Any]] = []
        for p in projects[:12]:
            root = Path(p["path"])
            if not root.is_dir():
                continue
            for folder in ("artifacts", "reports", "exports"):
                base = root / folder
                if not base.is_dir():
                    continue
                try:
                    for f in base.rglob("*"):
                        if not f.is_file() or any(part in {"node_modules", ".git", "dist", "build", "renders"} for part in f.parts):
                            continue
                        try:
                            rel = str(f.relative_to(root))
                            recent_artifacts.append({
                                "type": _artifact_kind(f), "title": f.parent.name if f.name == "scene.json" else f.name,
                                "path": rel, "project_slug": p["slug"], "updated_at": _dtm.fromtimestamp(f.stat().st_mtime, _tz.utc).isoformat(),
                            })
                        except OSError:
                            pass
                except OSError:
                    pass
        recent_artifacts.sort(key=lambda a: a["updated_at"], reverse=True)
        recent_artifacts = recent_artifacts[:6]
        failed_runs_24h = d.execute("SELECT COUNT(*) AS c FROM runs WHERE status = 'failed' AND created_at >= datetime('now','-24 hours')").fetchone()["c"]
        stale_runs = d.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE status IN ('queued','running') AND NOT "
            "((status = 'running' AND COALESCE(heartbeat_at, started_at, created_at) >= datetime('now', ?)) "
            "OR (status = 'queued' AND created_at >= datetime('now', ?)))",
            stale_params(stale_seconds),
        ).fetchone()["c"]
        runners = detect_runners()
        system_health = {
            "activeRuns": active_runs_count,
            "failedRuns24h": failed_runs_24h,
            "staleRuns": stale_runs,
            "runnersReady": sum(1 for r in runners if r.get("runnable")),
            "runnersTotal": sum(1 for r in runners if r.get("hasAdapter") and not r.get("detectionOnly")),
        }
        # Runs currently blocked waiting for the user's approval (latest event is an
        # approval.request that hasn't been resolved). Usually empty when auto-approve
        # is on, but surfaces cross-project on Home when it's off.
        pending_approvals = [dict(r) for r in d.execute(
            "SELECT s.id, s.title, p.slug AS project_slug "
            "FROM runs r JOIN sessions s ON s.id = r.session_id "
            "LEFT JOIN projects p ON p.id = s.project_id "
            "WHERE r.status = 'running' "
            "AND (SELECT e.type FROM events e WHERE e.run_id = r.id ORDER BY e.seq DESC LIMIT 1) = 'approval.request' "
            "GROUP BY s.id ORDER BY r.id DESC LIMIT 5"
        ).fetchall()]
        # Auth/readiness of the selected media providers + runners in use, cached and
        # refreshed off the request path (checks shell out to CLIs). Gated off in unit
        # tests via start_worker so no check threads spawn there.
        app_cfg = getattr(app.state, "config", {}) or {}
        auth_checks_enabled = bool(app_cfg.get("auth_health_checks", app_cfg.get("start_worker", True)))
        auth_health = auth_health_mod.snapshot(
            str(app_cfg.get("database_path") or ""),
            enabled=auth_checks_enabled,
            include_video=features.enabled(app_cfg, features.VIDEO),
        )
        return {
            "counts": counts, "jobsByStatus": jobs_by_status,
            "recent": recent, "activeSessions": active_sessions, "projects": projects,
            "workflows": workflows_out, "schedules": schedules_out, "reviewCount": review_count,
            "reviewJobs": review_jobs, "recentArtifacts": recent_artifacts, "systemHealth": system_health,
            "pendingApprovals": pending_approvals, "authHealth": auth_health,
        }

    @app.get("/api/runs/active")
    def active_runs(user: dict[str, Any] = Depends(current_user)):
        """Sessions with an in-flight run, so the sidebar can show a thinking
        indicator that survives navigating away from the chat view."""
        stale_seconds = _as_int(getattr(app.state, "config", {}).get("run_stale_seconds") or 60)
        rows = db().execute(
            "SELECT DISTINCT session_id FROM runs WHERE "
            "((status = 'running' AND COALESCE(heartbeat_at, started_at, created_at) >= datetime('now', ?)) "
            "OR (status = 'queued' AND created_at >= datetime('now', ?)))",
            stale_params(stale_seconds),
        ).fetchall()
        return {"session_ids": [r["session_id"] for r in rows]}

    @app.get("/api/sessions/{session_id}/events/stream")
    async def stream_events(request: Request, session_id: int, after_id: int = 0, token: str = ""):
        user = user_from_token_query(token or request.cookies.get("proxima_session", ""))
        session_for_user(session_id, user)
        events = _stream_session_events(app, request, session_id, after_id, db)
        return StreamingResponse(events, media_type="text/event-stream")

    @app.websocket("/api/ws/terminal")
    async def ws_terminal(websocket: WebSocket, token: str = "", project: str = ""):
        """In-browser PTY shell (like SSH from the cockpit). Auth via ?token= or the
        proxima_session cookie — a valid session is always required. cwd = project path
        or workspace."""
        # Require a valid session (cookie or ?token=) — same stance as ws_events + the
        # SSE stream. The FE always holds a proxima_session cookie (from /auth/auto or
        # login), so no owner fallback is needed. (The old cfg["single_user"] fallback
        # could have opened the terminal without the password once that flag was set.)
        token = token or websocket.cookies.get("proxima_session", "")
        user = None
        with app.state.db_lock:
            row = db().execute("SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL", (hash_token(token),)).fetchone()
        if row:
            user = dict(row)
        if not user:
            await websocket.close(code=4401)
            return
        cwd = str(Path(cfg["workspace_root"]))
        if project:
            try:
                p = visible_project(project, user)
                if p.get("path"):
                    cwd = p["path"]
            except Exception:
                pass
        Path(cwd).mkdir(parents=True, exist_ok=True)
        await websocket.accept()
        term = TerminalSession(cwd)
        term.start()
        loop = asyncio.get_event_loop()

        async def pump_out():
            while True:
                data = await loop.run_in_executor(None, term.read, 65536)
                if not data:
                    break
                try:
                    await websocket.send_bytes(data)
                except Exception:
                    break
            try:
                await websocket.close()
            except Exception:
                pass

        out_task = asyncio.create_task(pump_out())
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    term.write(msg["bytes"])
                elif msg.get("text"):
                    t = msg["text"]
                    if t.startswith("{"):
                        try:
                            j = _decode_json(t)
                            if j.get("type") == "resize":
                                term.resize(_as_int(j.get("rows", 24)), _as_int(j.get("cols", 80)))
                            elif j.get("type") == "input":
                                term.write(str(j.get("data", "")).encode())
                            else:
                                term.write(t.encode())
                        except Exception:
                            term.write(t.encode())
                    else:
                        term.write(t.encode())
        except WebSocketDisconnect:
            pass
        finally:
            term.close()
            out_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await out_task

    @app.websocket("/api/ws/sessions/{session_id}")
    async def ws_events(websocket: WebSocket, session_id: int, token: str = "", after_id: int = 0):
        token = token or websocket.cookies.get("proxima_session", "")
        user = None
        token_hash = hash_token(token)
        with app.state.db_lock:
            row = db().execute("SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL", (token_hash,)).fetchone()
        if row:
            user = dict(row)
        if not user:
            await websocket.close(code=4401)
            return
        session_for_user(session_id, user)
        await websocket.accept()
        hub = app.state.hub
        ev = hub.subscribe(session_id)
        # Resume from the client's cursor (events.id) so a reconnect doesn't replay
        # the whole session transcript. Mirrors the SSE sibling's after_id.
        last_id = after_id
        try:
            while True:
                ev.clear()
                rows = db().execute("SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id ASC", (session_id, last_id)).fetchall()
                for row in rows:
                    last_id = row["id"]
                    await websocket.send_json(_event_payload(row))
                try:
                    await asyncio.wait_for(ev.wait(), timeout=15)
                except asyncio.TimeoutError as _exc:
                    pass
        except WebSocketDisconnect:
            return
        finally:
            hub.unsubscribe(session_id, ev)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int, user: dict[str, Any] = Depends(current_user)):
        row = db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        session_for_user(row["session_id"], user)
        return dict(row)

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: int, user: dict[str, Any] = Depends(current_user)):
        row = db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        session_for_user(row["session_id"], user)
        if row["status"] in ("queued", "running"):
            raise HTTPException(status_code=409, detail="cancel the run before deleting it")
        output_paths: set[str] = set()
        for m in db().execute("SELECT output_links FROM messages WHERE run_id = ?", (run_id,)).fetchall():
            try:
                for a in _decode_json(m["output_links"] or "[]"):
                    if a.get("path"):
                        output_paths.add(str(a["path"]))
            except Exception:
                pass
        first_msg = db().execute("SELECT MIN(id) AS id FROM messages WHERE run_id = ?", (run_id,)).fetchone()["id"]
        if first_msg:
            db().execute(
                "DELETE FROM messages WHERE id = (SELECT MAX(id) FROM messages WHERE session_id = ? AND role = 'user' AND id < ?)",
                (row["session_id"], first_msg),
            )
        db().execute("DELETE FROM messages WHERE run_id = ?", (run_id,))
        db().execute("DELETE FROM events WHERE run_id = ?", (run_id,))
        db().execute("DELETE FROM runs WHERE id = ?", (run_id,))
        if output_paths:
            update_produced_artifacts(db(), row["session_id"], lambda current: [a for a in current if a.get("path") not in output_paths])
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (row["session_id"],))
        return {"ok": True, "run_id": run_id}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: int, user: dict[str, Any] = Depends(current_user)):
        row = db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        session_for_user(row["session_id"], user)
        changed = db().execute(
            "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('queued','running')",
            (run_id,),
        ).rowcount > 0
        queued = []
        collab_cancelled = []
        collab_row = None
        if changed:
            queued = db().execute(
                "SELECT id FROM runs WHERE session_id = ? AND id != ? AND status = 'queued'",
                (row["session_id"], run_id),
            ).fetchall()
            db().execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP "
                "WHERE session_id = ? AND id != ? AND status = 'queued'",
                (row["session_id"], run_id),
            )
            if str(row["kind"]).startswith("collab_"):
                collab_row = db().execute(
                    "SELECT * FROM prompt_collaborations WHERE parent_run_id = ? OR id = ?",
                    (run_id, row["collaboration_id"]),
                ).fetchone()
                if collab_row:
                    collab_cancelled = db().execute(
                        "SELECT * FROM runs WHERE collaboration_id = ? AND id != ? AND (? IS NULL OR id != ?) AND status IN ('queued','running','cancelled')",
                        (collab_row["id"], run_id, collab_row["parent_run_id"], collab_row["parent_run_id"]),
                    ).fetchall()
                    db().execute(
                        "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE collaboration_id = ? AND id != ? AND (? IS NULL OR id != ?) AND status IN ('queued','running')",
                        (collab_row["id"], run_id, collab_row["parent_run_id"], collab_row["parent_run_id"]),
                    )
                    # Guarded (request-thread side of the worker race): only cancel a
                    # still-live collaboration — never flip one the worker just finished.
                    state.guarded_transition(
                        db(), "prompt_collaborations", _as_int(collab_row["id"]), "cancelled",
                        state.non_terminal(state.COLLABORATION),
                        set_extra="updated_at = CURRENT_TIMESTAMP",
                    )
        if changed:
            app.state.worker.add_event(run_id, row["session_id"], row["project_id"], "run.cancelled", {})
        notified: set[int] = set()
        for q in [*collab_cancelled, *queued]:
            qid = _as_int(q["id"])
            if qid in notified:
                continue
            notified.add(qid)
            q_session_id = q["session_id"] if "session_id" in q.keys() else row["session_id"]
            q_project_id = q["project_id"] if "project_id" in q.keys() else row["project_id"]
            app.state.worker.add_event(qid, q_session_id, q_project_id, "run.cancelled", {})
            if collab_row is not None and "collaboration_id" in q.keys() and q["collaboration_id"] == collab_row["id"] and q["kind"] not in ("collab_brainstorm", "collab_debate"):
                profile = db().execute("SELECT * FROM profiles WHERE id = ?", (q["profile_id"],)).fetchone()
                if profile:
                    app.state.worker.add_event(qid, q_session_id, q_project_id, "collaboration.child.cancelled", collaboration_card_payload(dict(collab_row), qid, dict(profile), q["collaboration_role"], "cancelled"))
            app.state.worker.cancel(qid)
        if changed:
            app.state.worker.cancel(run_id)
        fresh = db().execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        return {"ok": True, "run_id": run_id, "status": fresh["status"] if fresh else row["status"]}

    @app.post("/api/runs/{run_id}/permission")
    def respond_permission(run_id: int, payload: PermissionResponse, user: dict[str, Any] = Depends(current_user)):
        """Deliver the user's interactive card choice back to the waiting agent."""
        row = db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        session_for_user(row["session_id"], user)
        if row["status"] != "running":
            raise HTTPException(status_code=409, detail="run is not waiting for permission")
        ok = app.state.worker.resolve_permission(run_id, payload.request_id, payload.option_id)
        return {"ok": ok, "run_id": run_id}

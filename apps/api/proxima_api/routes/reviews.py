"""Message-review (Validate sidecar) routes.

Extracted from routes/chat.py: reviewing an assistant message with a second
runner (verdict / gaps / revised content) is its own feature. It touches core
rows by id and enqueues runs the worker executes, but is otherwise self-contained
— it does not reach into the chat gate's collaboration/run internals.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException

from .. import features
from .. import kinds
from ..message_reviews import build_source_merge_prompt, build_validate_prompt, review_payload
from ..schemas import MessageReviewAskOriginalRequest, MessageReviewCreateRequest


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    profile_for_user = deps["profile_for_user"]

    def _require_mode_feature(mode: str | None) -> None:
        # Feature-blind gate via the registry (mirrors the chat gate).
        flag = kinds.feature_flag_for(mode)
        if flag:
            features.require(cfg, flag)

    def _source_message_for_user(message_id: int, user: dict[str, Any]) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT m.*, s.owner_user_id, s.project_id, s.title AS session_title, s.mode AS session_mode,
                   COALESCE(r.runner_id, s.runner_id) AS source_runner,
                   COALESCE(r.profile_id, s.profile_id) AS source_profile_id,
                   COALESCE(pr.name, m.author) AS source_author
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            LEFT JOIN runs r ON r.id = m.run_id
            LEFT JOIN profiles pr ON pr.id = COALESCE(r.profile_id, s.profile_id)
            WHERE m.id = ?
            """,
            (message_id,),
        ).fetchone()
        if not row or row["owner_user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="message not found")
        if row["role"] != "assistant":
            raise HTTPException(status_code=400, detail="only assistant messages can be validated")
        return dict(row)

    def _review_for_user(review_id: int, user: dict[str, Any]) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT mr.*, m.content AS source_content, m.author AS source_author, s.owner_user_id,
                   s.mode AS session_mode,
                   s.project_id, s.title AS session_title, COALESCE(r.profile_id, s.profile_id) AS source_profile_id,
                   COALESCE(r.runner_id, s.runner_id) AS source_runner
            FROM message_reviews mr
            JOIN messages m ON m.id = mr.source_message_id
            JOIN sessions s ON s.id = mr.session_id
            LEFT JOIN runs r ON r.id = m.run_id
            WHERE mr.id = ?
            """,
            (review_id,),
        ).fetchone()
        if not row or row["owner_user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="review not found")
        return dict(row)

    def _pick_reviewer_profile(source_runner: str | None, requested_id: int | None, user: dict[str, Any]) -> dict[str, Any]:
        if requested_id is not None:
            profile = profile_for_user(requested_id, user)
            if profile["runner_id"] == source_runner:
                raise HTTPException(status_code=400, detail="reviewer must use a different runner than the source")
            return dict(profile)
        first = "codex" if source_runner == "claude-code" else "claude-code"
        second = "claude-code" if first == "codex" else "codex"
        row = db().execute(
            """
            SELECT * FROM profiles
            WHERE user_id = ? AND runner_id != ?
            ORDER BY CASE WHEN runner_id = ? THEN 0 WHEN runner_id = ? THEN 1 ELSE 2 END,
                     is_default DESC, id ASC
            LIMIT 1
            """,
            (user["id"], source_runner or "", first, second),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="No reviewer profile with a different runner is available.")
        return dict(row)

    @app.get("/api/messages/{message_id}/reviews")
    def list_message_reviews(message_id: int, user: dict[str, Any] = Depends(current_user)):
        _source_message_for_user(message_id, user)
        rows = db().execute(
            "SELECT * FROM message_reviews WHERE source_message_id = ? ORDER BY id ASC",
            (message_id,),
        ).fetchall()
        return {"reviews": [review_payload(r) for r in rows]}

    @app.post("/api/messages/{message_id}/reviews", status_code=202)
    def create_message_review(message_id: int, payload: MessageReviewCreateRequest, user: dict[str, Any] = Depends(current_user)):
        source = _source_message_for_user(message_id, user)
        _require_mode_feature(source.get("session_mode"))
        reviewer = _pick_reviewer_profile(source.get("source_runner"), payload.reviewer_profile_id, user)
        reviewer_profiles = [{"id": reviewer["id"], "name": reviewer["name"], "runner_id": reviewer["runner_id"]}]
        prompt = build_validate_prompt(
            source_content=source["content"],
            source_author=source.get("source_author"),
            source_runner=source.get("source_runner"),
            session_title=source.get("session_title") or "Untitled session",
            has_unanswered_qform="<question-form" in (source.get("content") or ""),
        )
        cur = db().execute(
            """
            INSERT INTO message_reviews(source_message_id, session_id, mode, status, source_runner,
                                        source_profile_id, reviewer_profile_id, reviewer_profiles)
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                message_id,
                source["session_id"],
                "validate",
                source.get("source_runner"),
                source.get("source_profile_id"),
                reviewer["id"],
                json.dumps(reviewer_profiles),
            ),
        )
        review_id = int(cur.lastrowid)
        run = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, 'message_review')
            """,
            (
                source["session_id"],
                source["project_id"],
                user["id"],
                reviewer["id"],
                reviewer["runner_id"],
                prompt,
                reviewer["default_model"],
                reviewer["hermes_home"],
            ),
        )
        run_id = int(run.lastrowid)
        db().execute("UPDATE message_reviews SET run_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id, review_id))
        row = db().execute("SELECT * FROM message_reviews WHERE id = ?", (review_id,)).fetchone()
        app.state.worker.add_event(run_id, source["session_id"], source["project_id"], "run.queued", {"runner": reviewer["runner_id"], "kind": "message_review", "review_id": review_id})
        app.state.worker.add_event(run_id, source["session_id"], source["project_id"], "message_review.queued", {"review": review_payload(row)})
        return {"review": review_payload(row)}

    @app.post("/api/message-reviews/{review_id}/use-revised")
    def use_revised_review(review_id: int, user: dict[str, Any] = Depends(current_user)):
        review = _review_for_user(review_id, user)
        if not review.get("revised_content"):
            raise HTTPException(status_code=400, detail="review has no revised content yet")
        return {"content": review["revised_content"]}

    @app.post("/api/message-reviews/{review_id}/replace-answer")
    def replace_answer_with_review(review_id: int, user: dict[str, Any] = Depends(current_user)):
        review = _review_for_user(review_id, user)
        _require_mode_feature(review.get("session_mode"))
        if review.get("status") != "done" or not review.get("revised_content"):
            raise HTTPException(status_code=400, detail="review has no revised content yet")
        original = review.get("source_original_content") or review.get("source_content") or ""
        db().execute("UPDATE messages SET content = ? WHERE id = ?", (review["revised_content"], review["source_message_id"]))
        db().execute(
            "UPDATE message_reviews SET source_original_content = ?, applied_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (original, review_id),
        )
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (review["session_id"],))
        row = db().execute("SELECT * FROM message_reviews WHERE id = ?", (review_id,)).fetchone()
        if review.get("run_id"):
            app.state.worker.add_event(int(review["run_id"]), review["session_id"], review["project_id"], "message_review.applied", {"review": review_payload(row)})
        return {"review": review_payload(row), "message": {"id": review["source_message_id"], "content": review["revised_content"]}}

    @app.post("/api/message-reviews/{review_id}/restore-original")
    def restore_original_answer(review_id: int, user: dict[str, Any] = Depends(current_user)):
        review = _review_for_user(review_id, user)
        _require_mode_feature(review.get("session_mode"))
        original = review.get("source_original_content")
        if not original:
            raise HTTPException(status_code=400, detail="review has no stored original content")
        db().execute("UPDATE messages SET content = ? WHERE id = ?", (original, review["source_message_id"]))
        db().execute("UPDATE message_reviews SET applied_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (review_id,))
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (review["session_id"],))
        row = db().execute("SELECT * FROM message_reviews WHERE id = ?", (review_id,)).fetchone()
        if review.get("run_id"):
            app.state.worker.add_event(int(review["run_id"]), review["session_id"], review["project_id"], "message_review.restored", {"review": review_payload(row)})
        return {"review": review_payload(row), "message": {"id": review["source_message_id"], "content": original}}

    @app.post("/api/message-reviews/{review_id}/ask-original", status_code=202)
    def ask_original_to_revise(review_id: int, payload: MessageReviewAskOriginalRequest, user: dict[str, Any] = Depends(current_user)):
        review = _review_for_user(review_id, user)
        _require_mode_feature(review.get("session_mode"))
        profile = profile_for_user(review.get("source_profile_id"), user)
        prompt = build_source_merge_prompt(
            source_content=review.get("source_original_content") or review.get("source_content") or "",
            validation_feedback=review.get("raw_transcript") or "",
            reviewer_revision=review.get("revised_content"),
            note=payload.note,
        )
        cur = db().execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, 'message_review_merge')
            """,
            (review["session_id"], review["project_id"], user["id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"]),
        )
        run_id = int(cur.lastrowid)
        db().execute("UPDATE message_reviews SET status = 'queued', run_id = ?, error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id, review_id))
        row = db().execute("SELECT * FROM message_reviews WHERE id = ?", (review_id,)).fetchone()
        app.state.worker.add_event(run_id, review["session_id"], review["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": "message_review_merge", "review_id": review_id})
        app.state.worker.add_event(run_id, review["session_id"], review["project_id"], "message_review.queued", {"review": review_payload(row)})
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (review["session_id"],))
        return {"run_id": run_id, "session_id": review["session_id"], "status": "queued", "review": review_payload(row)}

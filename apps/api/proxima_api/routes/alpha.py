"""Alpha desk, settings, checkpoints, turn restore, and global attention routes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .. import app_settings, satpam, turn_restore
from ..alpha_runtime import (
    ALPHA_MAX_PARALLEL,
    AlphaToolError,
    alpha_capacity,
    ensure_alpha_identity,
)
from ..job_checkpoints import (
    CheckpointError,
    checkpoint_payload,
    list_checkpoints,
    restore_checkpoint,
    restore_impact,
)
from ..runner_specs import runner_is_selectable
from ..schemas import GraphScriptApproveRequest, JobRejectRequest


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail="expected an integer, got a boolean")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail="expected an integer") from exc


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    create_profile_for = deps["create_profile_for"]
    session_payload = deps["session_payload"]

    def _identity(user: dict[str, Any]):
        try:
            return ensure_alpha_identity(db(), user, create_profile_for=create_profile_for)
        except AlphaToolError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc

    def _alpha_job_payload(row) -> dict[str, Any]:
        data = dict(row)
        data["input"] = _json(data.get("input"), {})
        data["steps_state"] = _json(data.get("steps_state"), [])
        run = db().execute(
            "SELECT status FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (data.get("session_id"),),
        ).fetchone()
        data["run_status"] = run["status"] if run else None
        if data.get("status") == "running" and data["run_status"] == "queued":
            data["desk_status"] = "queued"
        else:
            data["desk_status"] = data.get("status")
        project = db().execute("SELECT slug, name FROM projects WHERE id = ?", (data.get("project_id"),)).fetchone()
        data["project_slug"] = project["slug"] if project else None
        data["project_name"] = project["name"] if project else None
        return data

    @app.get("/api/alpha/desk")
    def get_alpha_desk(user: dict[str, Any] = Depends(current_user)):
        profile, session = _identity(user)
        rows = db().execute(
            "SELECT * FROM jobs WHERE alpha_session_id = ? AND archived_at IS NULL "
            "ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'review' THEN 2 ELSE 3 END, id DESC LIMIT 100",
            (session["id"],),
        ).fetchall()
        jobs = [_alpha_job_payload(row) for row in rows]
        attention = [item for item in _attention_items(user) if item["kind"].startswith("alpha_") or item.get("target", {}).get("alpha_session_id") == session["id"]]
        alpha_run = db().execute(
            "SELECT id, status FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session["id"],),
        ).fetchone()
        return {
            "session": session_payload(session),
            "alpha_run": dict(alpha_run) if alpha_run else None,
            "backing_runner": profile["runner_id"],
            "jobs": jobs,
            "unattended": app_settings.get_alpha_settings(db())["unattended"],
            "budgets": app_settings.get_alpha_settings(db()),
            "capacity": alpha_capacity(db(), session["id"]),
            "attention": attention,
            "checkpoints": list_checkpoints(db(), alpha_session_id=session["id"]),
        }

    @app.post("/api/alpha/messages", status_code=202)
    def create_alpha_message(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        profile, session = _identity(user)
        content = str(payload.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=422, detail="content is required")
        if len(content) > 50_000:
            raise HTTPException(status_code=422, detail="content is too long")
        active = db().execute(
            "SELECT id FROM runs WHERE session_id = ? AND status IN ('queued','running') ORDER BY id LIMIT 1",
            (session["id"],),
        ).fetchone()
        if active:
            raise HTTPException(status_code=409, detail="Alpha is already working on a turn")
        db().execute(
            "INSERT INTO messages(session_id, role, content, author) VALUES (?, 'user', ?, ?)",
            (session["id"], content, user["username"]),
        )
        cur = db().execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, kind, status, prompt, model, hermes_home) "
            "VALUES (?, NULL, ?, ?, ?, 'alpha', 'queued', ?, ?, ?)",
            (
                session["id"], user["id"], profile["id"], profile["runner_id"], content,
                profile["default_model"], profile["hermes_home"],
            ),
        )
        run_id = _as_int(cur.lastrowid)
        db().execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session["id"],))
        app.state.worker.add_event(run_id, session["id"], None, "run.queued", {"runner": profile["runner_id"], "alpha": True})
        return {"run_id": run_id, "session_id": session["id"], "status": "queued"}

    @app.get("/api/settings/alpha")
    def get_alpha_settings(user: dict[str, Any] = Depends(current_user)):
        profile, _session = _identity(user)
        return {**app_settings.get_alpha_settings(db()), "runner_id": profile["runner_id"], "max_parallel": ALPHA_MAX_PARALLEL}

    @app.put("/api/settings/alpha")
    def put_alpha_settings(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        runner_id = payload.get("runner_id")
        if runner_id is not None:
            if not isinstance(runner_id, str) or not runner_is_selectable(runner_id):
                raise HTTPException(status_code=422, detail="unknown Alpha backing runner")
            app_settings.set_setting(db(), "alpha.runner_id", runner_id)
        for boolean_key in ("unattended", "tour_core_done"):
            if boolean_key in payload and not isinstance(payload[boolean_key], bool):
                raise HTTPException(status_code=422, detail=f"{boolean_key} must be true or false")
        token_value: int | None | object = ...
        if "budget_tokens" in payload:
            token_value = None if payload["budget_tokens"] in (None, "") else _as_int(payload["budget_tokens"])
        try:
            settings = app_settings.set_alpha_settings(
                db(),
                unattended=bool(payload["unattended"]) if "unattended" in payload else None,
                budget_turns=_as_int(payload["budget_turns"]) if "budget_turns" in payload else None,
                budget_wall_seconds=_as_int(payload["budget_wall_seconds"]) if "budget_wall_seconds" in payload else None,
                budget_tokens=token_value,
                tour_core_done=bool(payload["tour_core_done"]) if "tour_core_done" in payload else None,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        profile, _session = _identity(user)
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
            "VALUES (?, 'alpha.settings.change', 'settings', 'alpha', ?)",
            (user["id"], json.dumps({key: value for key, value in payload.items() if key != "budget_tokens" or value is not None})),
        )
        return {**settings, "runner_id": profile["runner_id"], "max_parallel": ALPHA_MAX_PARALLEL}

    def _checkpoint_owned(checkpoint_id: int, user: dict[str, Any]):
        row = db().execute(
            "SELECT cp.* FROM job_checkpoints cp JOIN jobs j ON j.id = cp.job_id "
            "WHERE cp.id = ? AND (j.created_by = ? OR j.project_id IN (SELECT id FROM projects WHERE owner_user_id = ?))",
            (checkpoint_id, user["id"], user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="checkpoint not found")
        return row

    @app.get("/api/jobs/{job_id}/checkpoints")
    def get_job_checkpoints(job_id: int, user: dict[str, Any] = Depends(current_user)):
        job = db().execute(
            "SELECT id FROM jobs WHERE id = ? AND (created_by = ? OR project_id IN (SELECT id FROM projects WHERE owner_user_id = ?))",
            (job_id, user["id"], user["id"]),
        ).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"checkpoints": list_checkpoints(db(), job_id=job_id)}

    @app.get("/api/jobs/{job_id}/checkpoint/{checkpoint_id}/restore")
    def preview_checkpoint_restore(job_id: int, checkpoint_id: int, user: dict[str, Any] = Depends(current_user)):
        row = _checkpoint_owned(checkpoint_id, user)
        if row["job_id"] != job_id:
            raise HTTPException(status_code=404, detail="checkpoint not found for job")
        try:
            return restore_impact(db(), checkpoint_id)
        except CheckpointError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/checkpoint/restore")
    def restore_job_checkpoint(job_id: int, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        checkpoint_id = _as_int(payload.get("checkpoint_id"))
        row = _checkpoint_owned(checkpoint_id, user)
        if row["job_id"] != job_id:
            raise HTTPException(status_code=404, detail="checkpoint not found for job")
        try:
            result = restore_checkpoint(db(), checkpoint_id, confirmed=payload.get("confirm") is True)
        except CheckpointError as exc:
            detail = str(exc)
            raise HTTPException(status_code=409 if "running" in detail else 422, detail=detail) from exc
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
            "VALUES (?, 'alpha.checkpoint.restore', 'job', ?, ?)",
            (user["id"], str(job_id), json.dumps({"checkpoint_id": checkpoint_id})),
        )
        return result

    @app.put("/api/jobs/{job_id}/checkpoint/{checkpoint_id}/pin")
    def pin_job_checkpoint(job_id: int, checkpoint_id: int, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        row = _checkpoint_owned(checkpoint_id, user)
        if row["job_id"] != job_id:
            raise HTTPException(status_code=404, detail="checkpoint not found for job")
        db().execute("UPDATE job_checkpoints SET pinned = ? WHERE id = ?", (1 if payload.get("pinned", True) else 0, checkpoint_id))
        return checkpoint_payload(db().execute("SELECT * FROM job_checkpoints WHERE id = ?", (checkpoint_id,)).fetchone())

    def _message_journal(message_id: int, user: dict[str, Any]):
        row = db().execute(
            "SELECT m.id, s.id AS session_id, s.owner_user_id, s.mode, p.path AS project_path "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "LEFT JOIN projects p ON p.id = s.project_id WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        if not row or row["owner_user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="message not found")
        if row["mode"] != "chat" or not row["project_path"]:
            raise HTTPException(status_code=409, detail="turn restore is available only for project Chat sessions")
        return row

    @app.get("/api/chat/messages/{message_id}/restore-turn")
    def preview_turn_restore(message_id: int, user: dict[str, Any] = Depends(current_user)):
        _message_journal(message_id, user)
        try:
            return turn_restore.preview(db(), message_id)
        except turn_restore.TurnRestoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/chat/messages/{message_id}/restore-turn")
    def restore_chat_turn(message_id: int, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        row = _message_journal(message_id, user)
        try:
            result = turn_restore.restore(
                db(), message_id, root=Path(row["project_path"]),
                confirmed=payload.get("confirm") is True,
                accept_active_alpha=payload.get("accept_active_alpha") is True,
            )
        except turn_restore.TurnRestoreError as exc:
            detail = str(exc)
            raise HTTPException(status_code=409 if "Alpha" in detail or "active" in detail else 422, detail=detail) from exc
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
            "VALUES (?, 'chat.turn.restore', 'message', ?, ?)",
            (user["id"], str(message_id), json.dumps({"paths": result["paths"]})),
        )
        return result

    def _attention_items(user: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in db().execute(
            "SELECT * FROM attention_items WHERE status = 'open' ORDER BY created_at DESC, id DESC"
        ).fetchall():
            data = dict(row)
            data["inline_ok"] = bool(data["inline_ok"])
            data["target"] = _json(data.pop("target_json"), {})
            data["actions"] = _json(data.pop("actions_json"), [])
            data["id"] = f"attention:{data['id']}"
            items.append(data)
        for row in db().execute(
            "SELECT j.*, EXISTS(SELECT 1 FROM job_worktrees wt WHERE wt.job_id = j.id) AS has_worktree "
            "FROM jobs j WHERE j.status = 'review' AND (j.created_by = ? OR j.project_id IN "
            "(SELECT id FROM projects WHERE owner_user_id = ?)) ORDER BY j.updated_at DESC",
            (user["id"], user["id"]),
        ).fetchall():
            steps = _json(row["steps_state"], [])
            final_simple = bool(steps) and all(step.get("status") == "done" for step in steps) and not row["has_worktree"] and row["engine"] != "graph"
            kind = "job_review" if final_simple else "job_diff"
            items.append({
                "id": f"job:{row['id']}", "kind": kind, "title": f"{row['title']} needs review",
                "target": {"view": "task", "job_id": row["id"], "engine": row["engine"], "alpha_session_id": row["alpha_session_id"]},
                "inline_ok": final_simple,
                "actions": ["approve", "reject"] if final_simple else [],
                "status": "open", "created_at": row["updated_at"],
            })
        read_script = getattr(app.state, "alpha_read_node_script", None)
        if read_script:
            for row in db().execute(
                "SELECT ns.node_id, j.id AS job_id, j.title, j.updated_at, j.alpha_session_id FROM node_states ns "
                "JOIN jobs j ON j.id = ns.job_id WHERE ns.status = 'failed' "
                "AND ns.error LIKE 'script_approval_required:%' ORDER BY j.updated_at DESC"
            ).fetchall():
                try:
                    script = read_script(row["job_id"], row["node_id"], user)
                except HTTPException:
                    continue
                items.append({
                    "id": f"script:{row['job_id']}:{row['node_id']}", "kind": "script_trust",
                    "title": f"Approve {script['script']} · sha256 {script['sha256']}",
                    "target": {"view": "workflows", "job_id": row["job_id"], "engine": "graph", "node_id": row["node_id"], "sha256": script["sha256"], "alpha_session_id": row["alpha_session_id"]},
                    "inline_ok": True, "actions": ["approve"], "status": "open", "created_at": row["updated_at"],
                })
        for row in db().execute(
            "SELECT si.*, j.title, j.engine, j.alpha_session_id FROM satpam_interventions si JOIN jobs j ON j.id = si.job_id "
            "WHERE si.action = 'restart' AND si.status = 'pending' ORDER BY si.id DESC"
        ).fetchall():
            items.append({
                "id": f"satpam:{row['id']}", "kind": "satpam_restart",
                "title": f"Restart stuck work: {row['title']}",
                "target": {"view": "task", "job_id": row["job_id"], "engine": row["engine"], "alpha_session_id": row["alpha_session_id"]},
                "inline_ok": True, "actions": ["approve", "dismiss"], "status": "open", "created_at": row["created_at"],
            })
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items

    @app.get("/api/attention")
    def get_attention(user: dict[str, Any] = Depends(current_user)):
        items = _attention_items(user)
        return {"items": items, "count": len(items)}

    @app.post("/api/attention/{item_id:path}/act")
    def act_attention(item_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        action = str(payload.get("action") or "")
        if item_id.startswith("attention:"):
            attention_id = _as_int(item_id.split(":", 1)[1])
            row = db().execute("SELECT * FROM attention_items WHERE id = ? AND status = 'open'", (attention_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="attention item not found")
            if not row["inline_ok"]:
                raise HTTPException(status_code=400, detail="this item must be handled on its linked surface")
            target = _json(row["target_json"], {})
            if row["kind"] == "permission_job":
                options = target.get("options") or []
                wanted = "allow" if action == "approve" else "reject"
                option = next((o for o in options if str(o.get("kind", "")).startswith(wanted)), None)
                if not option or not app.state.worker.resolve_permission(_as_int(target.get("run_id")), str(target.get("request_id")), str(option.get("optionId"))):
                    raise HTTPException(status_code=409, detail="permission request is no longer active")
            else:
                # Durable item kinds are navigation-only until an explicit,
                # server-owned mutation handler is mapped here.
                raise HTTPException(status_code=400, detail="this item must be handled on its linked surface")
            db().execute("UPDATE attention_items SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (attention_id,))
            return {"ok": True, "id": item_id, "action": action}
        if item_id.startswith("job:"):
            job_id = _as_int(item_id.split(":", 1)[1])
            current = next((item for item in _attention_items(user) if item["id"] == item_id), None)
            if not current or not current["inline_ok"]:
                raise HTTPException(status_code=400, detail="this review must be handled in Tasks")
            approve_job = getattr(app.state, "alpha_approve_job", None)
            reject_job = getattr(app.state, "alpha_reject_job", None)
            if action == "approve" and approve_job:
                approve_job(job_id, None, user)
            elif action == "reject" and reject_job:
                reject_job(job_id, JobRejectRequest(reason="Rejected from Attention"), user)
            elif action not in {"approve", "reject"}:
                raise HTTPException(status_code=400, detail="action is not available")
            else:
                raise HTTPException(status_code=409, detail="job review service is unavailable")
            return {"ok": True, "id": item_id, "action": action}
        if item_id.startswith("script:"):
            parts = item_id.split(":", 2)
            if len(parts) != 3 or action != "approve":
                raise HTTPException(status_code=400, detail="action is not available")
            job_id, node_id = _as_int(parts[1]), parts[2]
            current = next((item for item in _attention_items(user) if item["id"] == item_id), None)
            if not current or not current["inline_ok"]:
                raise HTTPException(status_code=409, detail="script approval is no longer active")
            approve_script = getattr(app.state, "alpha_approve_node_script", None)
            if not approve_script:
                raise HTTPException(status_code=409, detail="script approval service is unavailable")
            approve_script(job_id, node_id, GraphScriptApproveRequest(expected_sha256=str(current["target"]["sha256"])), user)
            return {"ok": True, "id": item_id, "action": action}
        if item_id.startswith("satpam:"):
            intervention_id = _as_int(item_id.split(":", 1)[1])
            row = db().execute("SELECT * FROM satpam_interventions WHERE id=? AND status='pending'", (intervention_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="satpam item not found")
            if action == "approve":
                try:
                    app.state.worker.satpam.execute_restart(row["job_id"], intervention_id)
                except satpam.SatpamRestartError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            elif action == "dismiss":
                db().execute("UPDATE satpam_interventions SET status='dismissed', resolved_at=CURRENT_TIMESTAMP WHERE id=?", (intervention_id,))
            else:
                raise HTTPException(status_code=400, detail="action is not available")
            return {"ok": True, "id": item_id, "action": action}
        raise HTTPException(status_code=404, detail="attention item not found")

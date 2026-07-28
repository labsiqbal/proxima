"""Master desk, settings, checkpoints, turn restore, and global attention routes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .. import app_settings, features, master_focus, satpam, turn_restore
from ..master_runtime import (
    MasterToolError,
    master_capacity,
    master_parallel_limit,
    ensure_master_identity,
)
from ..job_checkpoints import (
    CheckpointError,
    checkpoint_payload,
    list_checkpoints,
    restore_checkpoint,
    restore_impact,
)
from ..master_persistence import (
    MasterPersistenceError,
    canonical_job_payload,
    legacy_alpha_payload,
)
from ..runner_specs import (
    RUNNER_SPECS,
    master_runner_conformance,
    runner_is_selectable,
)
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

    def _require_master() -> None:
        features.require(app.state.config, features.MASTER_ORCHESTRATOR)

    def _identity(user: dict[str, Any]):
        try:
            return ensure_master_identity(
                db(),
                user,
                create_profile_for=create_profile_for,
                managed_profiles_root=app.state.config["hermes_profiles_root"],
            )
        except (MasterToolError, MasterPersistenceError) as exc:
            code = exc.code if isinstance(exc, MasterToolError) else "master_identity_inconsistent"
            raise HTTPException(
                status_code=409,
                detail={"code": code, "message": str(exc)},
            ) from exc

    def _require_conforming_runner(runner_id: str) -> None:
        conforming, reason = master_runner_conformance(runner_id)
        if conforming:
            return
        spec = RUNNER_SPECS.get(runner_id)
        display_name = spec.display_name if spec else runner_id
        raise HTTPException(
            status_code=409,
            detail={
                "code": "master_runner_not_conforming",
                "message": f"{display_name} cannot run Master because its {reason}",
            },
        )

    def _owned_container(container_id: Any, user: dict[str, Any]):
        resolved = _as_int(container_id)
        row = db().execute(
            "SELECT id FROM projects "
            "WHERE id = ? AND owner_user_id = ? AND archived_at IS NULL",
            (resolved, user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=422,
                detail="Master target Container is not available",
            )
        return row

    def _message_context(
        payload: dict[str, Any],
        user: dict[str, Any],
        focus_container_id: int | None,
    ) -> dict[str, Any]:
        focus = payload.get("focus")
        target = payload.get("target")
        if focus is None:
            focus = {"mode": "fleet"}
        if target is None:
            target = {"mode": "auto"}
        if not isinstance(focus, dict) or not isinstance(target, dict):
            raise HTTPException(
                status_code=422,
                detail="Master Focus and target must be objects",
            )

        focus_mode = focus.get("mode")
        if focus_mode not in {"fleet", "container"}:
            raise HTTPException(status_code=422, detail="unknown Master Focus mode")
        focus_container = None
        if focus_mode == "container":
            focus_container = _owned_container(focus.get("container_id"), user)
        elif focus.get("container_id") is not None:
            raise HTTPException(
                status_code=422,
                detail="Fleet Focus cannot include a Container",
            )

        target_mode = target.get("mode")
        if target_mode not in {"auto", "explicit"}:
            raise HTTPException(status_code=422, detail="unknown Master target mode")
        target_container = None
        target_area_id = None
        if target_mode == "explicit":
            target_container = _owned_container(target.get("container_id"), user)
            if target.get("area_id") is not None:
                target_area_id = _as_int(target["area_id"])
                area = db().execute(
                    "SELECT id FROM project_areas "
                    "WHERE id = ? AND project_id = ? AND source != 'excluded'",
                    (target_area_id, target_container["id"]),
                ).fetchone()
                if area is None:
                    raise HTTPException(
                        status_code=422,
                        detail="Master target Area is not in the selected Container",
                    )
        elif (
            target.get("container_id") is not None
            or target.get("area_id") is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="Automatic Master routing cannot include an explicit target",
            )

        # Focus is server-owned.  The legacy per-message focus field remains
        # validated for compatibility, but cannot overwrite durable Focus.
        if focus_container_id is not None:
            focus_mode = "container"
            focus_container = {"id": focus_container_id}
        else:
            focus_mode = "fleet"
            focus_container = None

        context = {
            "focus_mode": focus_mode,
            "focus_container_id": (
                _as_int(focus_container["id"]) if focus_container is not None else None
            ),
            "target_mode": target_mode,
            "target_container_id": (
                _as_int(target_container["id"]) if target_container is not None else None
            ),
            "target_area_id": target_area_id,
        }
        return context

    def _focus_http_error(exc: master_focus.MasterFocusError) -> HTTPException:
        return HTTPException(
            status_code=409 if exc.code == "focus_version_conflict" else 422,
            detail={"code": exc.code, "message": str(exc)},
        )

    def _master_job_payload(row) -> dict[str, Any]:
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
        return canonical_job_payload(data)

    @app.get("/api/master/desk")
    def get_master_desk(user: dict[str, Any] = Depends(current_user)):
        _require_master()
        profile, session = _identity(user)
        event_cursor = db().execute(
            "SELECT COALESCE(MAX(id), 0) AS id FROM events WHERE session_id = ?",
            (session["id"],),
        ).fetchone()["id"]
        rows = db().execute(
            "SELECT * FROM jobs WHERE origin_master_session_id = ? AND archived_at IS NULL "
            "ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'review' THEN 2 ELSE 3 END, id DESC LIMIT 100",
            (session["id"],),
        ).fetchall()
        jobs = [_master_job_payload(row) for row in rows]
        attention = [item for item in _attention_items(user) if item["kind"].startswith("master_") or item.get("target", {}).get("origin_master_session_id") == session["id"]]
        master_run = db().execute(
            "SELECT id, status FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session["id"],),
        ).fetchone()
        return {
            "session": session_payload(session),
            "master_run": dict(master_run) if master_run else None,
            "event_cursor": event_cursor,
            "backing_runner": profile["runner_id"],
            "jobs": jobs,
            "unattended": app_settings.get_master_settings(db())["unattended"],
            "budgets": app_settings.get_master_settings(db()),
            "capacity": master_capacity(
                db(),
                session["id"],
                max_parallel=master_parallel_limit(app.state.config),
            ),
            "attention": attention,
            "checkpoints": list_checkpoints(db(), origin_master_session_id=session["id"]),
            "focus": master_focus.state_payload(db(), session["id"]),
        }

    @app.get("/api/alpha/desk", deprecated=True)
    def get_alpha_desk(user: dict[str, Any] = Depends(current_user)):
        return legacy_alpha_payload(get_master_desk(user))

    @app.post("/api/alpha/messages", status_code=202, deprecated=True)
    @app.post("/api/master/messages", status_code=202)
    def create_master_message(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        _require_master()
        profile, session = _identity(user)
        _require_conforming_runner(str(profile["runner_id"]))
        content = str(payload.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=422, detail="content is required")
        if len(content) > 50_000:
            raise HTTPException(status_code=422, detail="content is too long")
        # Preserve validation precedence: malformed explicit scopes are rejected
        # even if a previous turn is currently active.
        _message_context(payload, user, None)
        conn = db()
        with app.state.db_lock:
            conn.execute("SAVEPOINT create_master_turn")
            try:
                active = conn.execute(
                    "SELECT id FROM runs WHERE session_id = ? AND status IN ('queued','running') ORDER BY id LIMIT 1",
                    (session["id"],),
                ).fetchone()
                if active:
                    raise HTTPException(status_code=409, detail="Master is already working on a turn")
                focus = master_focus.state_payload(conn, session["id"])
                # An explicit target is a Focus transition and enqueue in one
                # transaction.  It never builds a prompt from the old epoch.
                requested_target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
                target_container_id = requested_target.get("container_id") if requested_target.get("mode") == "explicit" else None
                if target_container_id is not None:
                    target_container = _owned_container(target_container_id, user)
                    target_id = _as_int(target_container["id"])
                    if focus["current_container_id"] != target_id:
                        focus = master_focus.change_focus(
                            conn,
                            master_session_id=session["id"],
                            container_id=target_id,
                            expected_version=focus["version"],
                        )
                context = _message_context(payload, user, focus["current_container_id"])
                message_cur = conn.execute(
                    "INSERT INTO messages(session_id, role, content, author) "
                    "VALUES (?, 'user', ?, ?)",
                    (session["id"], content, user["username"]),
                )
                message_id = _as_int(message_cur.lastrowid)
                master_focus.stamp_message(
                    conn,
                    message_id=message_id,
                    focus_epoch_id=focus["current_epoch_id"],
                )
                conn.execute(
                    "INSERT INTO master_message_context("
                    "message_id, focus_mode, focus_container_id, target_mode, "
                    "target_container_id, target_area_id"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        message_id,
                        context["focus_mode"],
                        context["focus_container_id"],
                        context["target_mode"],
                        context["target_container_id"],
                        context["target_area_id"],
                    ),
                )
                cur = conn.execute(
                    "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, kind, status, prompt, model, hermes_home, focus_epoch_id) "
                    "VALUES (?, NULL, ?, ?, ?, 'master', 'queued', ?, ?, ?, ?)",
                    (
                        session["id"], user["id"], profile["id"], profile["runner_id"],
                        content, profile["default_model"], profile["hermes_home"], focus["current_epoch_id"],
                    ),
                )
                run_id = _as_int(cur.lastrowid)
                conn.execute(
                    "UPDATE messages SET run_id = ? WHERE id = ?",
                    (run_id, message_id),
                )
                conn.execute(
                    "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session["id"],),
                )
                conn.execute("RELEASE SAVEPOINT create_master_turn")
            except master_focus.MasterFocusError as exc:
                conn.execute("ROLLBACK TO SAVEPOINT create_master_turn")
                conn.execute("RELEASE SAVEPOINT create_master_turn")
                raise _focus_http_error(exc) from exc
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT create_master_turn")
                conn.execute("RELEASE SAVEPOINT create_master_turn")
                raise
        message = db().execute(
            "SELECT id, role, content, author, run_id, created_at "
            "FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        app.state.worker.add_event(run_id, session["id"], None, "run.queued", {"runner": profile["runner_id"], "master": True})
        return {
            "run_id": run_id,
            "session_id": session["id"],
            "status": "queued",
            "message": {**dict(message), "master_target": context},
        }

    @app.put("/api/master/focus")
    def put_master_focus(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        _require_master()
        _profile, session = _identity(user)
        if "version" not in payload:
            raise HTTPException(status_code=422, detail="Focus version is required")
        container_id = payload.get("container_id")
        container = _owned_container(container_id, user) if container_id is not None else None
        resolved_container_id = _as_int(container["id"]) if container is not None else None
        conn = db()
        with app.state.db_lock:
            conn.execute("SAVEPOINT update_master_focus")
            try:
                active = conn.execute(
                    "SELECT 1 FROM runs WHERE session_id = ? AND status IN ('queued','running') LIMIT 1",
                    (session["id"],),
                ).fetchone()
                if active:
                    result = master_focus.request_pending_focus(
                        conn,
                        master_session_id=session["id"],
                        container_id=resolved_container_id,
                        expected_version=payload["version"],
                    )
                else:
                    result = master_focus.change_focus(
                        conn,
                        master_session_id=session["id"],
                        container_id=resolved_container_id,
                        expected_version=payload["version"],
                    )
                conn.execute("RELEASE SAVEPOINT update_master_focus")
            except master_focus.MasterFocusError as exc:
                conn.execute("ROLLBACK TO SAVEPOINT update_master_focus")
                conn.execute("RELEASE SAVEPOINT update_master_focus")
                raise _focus_http_error(exc) from exc
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT update_master_focus")
                conn.execute("RELEASE SAVEPOINT update_master_focus")
                raise
        if result.get("changed"):
            app.state.hub.notify(session["id"])
        return {"focus": {key: result[key] for key in ("current_epoch_id", "current_container_id", "pending_container_id", "version")}, "pending": bool(result.get("pending")), "changed": bool(result.get("changed"))}

    def _graph_policy() -> dict[str, Any]:
        """Install-visible local-only Knowledge/Code extraction policy (Group 11)."""
        from ..graph_context import SEMANTIC_BACKEND_LOCAL

        egress = bool(app.state.config.get("graph_semantic_egress_enabled"))
        return {
            "semantic_egress_enabled": egress,
            "local_only": not egress,
            "semantic_backend_default": (
                "disabled" if egress else SEMANTIC_BACKEND_LOCAL
            ),
            "description": (
                "Knowledge and Code graphs use local structural extraction only. "
                "Cloud model egress stays off unless an explicit future captain "
                "policy enables it; configured cloud credentials never unlock it."
            ),
        }

    @app.get("/api/settings/alpha", deprecated=True)
    @app.get("/api/settings/master")
    def get_master_settings(user: dict[str, Any] = Depends(current_user)):
        _require_master()
        profile, _session = _identity(user)
        return {
            **app_settings.get_master_settings(db()),
            "runner_id": profile["runner_id"],
            "max_parallel": master_parallel_limit(app.state.config),
            "graph_policy": _graph_policy(),
        }

    @app.put("/api/settings/alpha", deprecated=True)
    @app.put("/api/settings/master")
    def put_master_settings(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        _require_master()
        runner_id = payload.get("runner_id")
        if runner_id is not None:
            if not isinstance(runner_id, str) or not runner_is_selectable(runner_id):
                raise HTTPException(status_code=422, detail="unknown Master backing runner")
            _require_conforming_runner(runner_id)
            app_settings.set_setting(db(), "master.runner_id", runner_id)
        for boolean_key in ("unattended", "tour_core_done"):
            if boolean_key in payload and not isinstance(payload[boolean_key], bool):
                raise HTTPException(status_code=422, detail=f"{boolean_key} must be true or false")
        token_value: int | None | object = ...
        if "budget_tokens" in payload:
            token_value = None if payload["budget_tokens"] in (None, "") else _as_int(payload["budget_tokens"])
        try:
            settings = app_settings.set_master_settings(
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
            "VALUES (?, 'master.settings.change', 'settings', 'master', ?)",
            (user["id"], json.dumps({key: value for key, value in payload.items() if key != "budget_tokens" or value is not None})),
        )
        return {
            **settings,
            "runner_id": profile["runner_id"],
            "max_parallel": master_parallel_limit(app.state.config),
            "graph_policy": _graph_policy(),
        }

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
            "VALUES (?, 'master.checkpoint.restore', 'job', ?, ?)",
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
                accept_active_master=payload.get("accept_active_master") is True,
                accept_active_alpha=(
                    payload.get("accept_active_alpha")
                    if isinstance(payload.get("accept_active_alpha"), bool)
                    else None
                ),
            )
        except turn_restore.TurnRestoreError as exc:
            detail = str(exc)
            raise HTTPException(status_code=409 if "Master" in detail or "active" in detail else 422, detail=detail) from exc
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
                "target": {"view": "task", "job_id": row["id"], "engine": row["engine"], "origin_master_session_id": row["origin_master_session_id"]},
                "inline_ok": final_simple,
                "actions": ["approve", "reject"] if final_simple else [],
                "status": "open", "created_at": row["updated_at"],
            })
        read_script = getattr(app.state, "master_read_node_script", None)
        if read_script:
            for row in db().execute(
                "SELECT ns.node_id, j.id AS job_id, j.title, j.updated_at, j.origin_master_session_id FROM node_states ns "
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
                    "target": {"view": "workflows", "job_id": row["job_id"], "engine": "graph", "node_id": row["node_id"], "sha256": script["sha256"], "origin_master_session_id": row["origin_master_session_id"]},
                    "inline_ok": True, "actions": ["approve"], "status": "open", "created_at": row["updated_at"],
                })
        for row in db().execute(
            "SELECT si.*, j.title, j.engine, j.origin_master_session_id FROM satpam_interventions si JOIN jobs j ON j.id = si.job_id "
            "WHERE si.action = 'restart' AND si.status = 'pending' ORDER BY si.id DESC"
        ).fetchall():
            items.append({
                "id": f"satpam:{row['id']}", "kind": "satpam_restart",
                "title": f"Restart stuck work: {row['title']}",
                "target": {"view": "task", "job_id": row["job_id"], "engine": row["engine"], "origin_master_session_id": row["origin_master_session_id"]},
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
            approve_job = getattr(app.state, "master_approve_job", None)
            reject_job = getattr(app.state, "master_reject_job", None)
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
            approve_script = getattr(app.state, "master_approve_node_script", None)
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

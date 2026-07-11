"""Task (kanban) routes for the Proxima API.

Extracted from main.py with the register() pattern: route bodies are moved
VERBATIM; register() rebinds the shared create_app closures as locals so nothing
inside the handlers changes. No behavior change.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from ..schemas import TaskCreateRequest, TaskUpdateRequest


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    profile_for_user = deps["profile_for_user"]

    def task_payload(row: dict[str, Any]) -> dict[str, Any]:
        creator = None
        if row.get("created_by"):
            crow = db().execute("SELECT username FROM users WHERE id = ?", (row["created_by"],)).fetchone()
            creator = crow["username"] if crow else None
        return {"id": row["id"], "project_slug": row.get("project_slug"), "session_id": row["session_id"], "title": row["title"], "description": row["description"], "status": row["status"], "assignee": row["assignee"], "created_by": creator, "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def _task_for_user(task_id: int, user: dict[str, Any]) -> dict[str, Any]:
        row = db().execute("SELECT t.*, p.slug AS project_slug FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="task not found")
        visible_project(row["project_slug"], user)  # ACL: must be a project member
        return dict(row)

    def _task_audit(user: dict[str, Any], action: str, task_id: int) -> None:
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, ?, 'task', ?)", (user["id"], action, str(task_id)))

    @app.get("/api/projects/{slug}/tasks")
    def list_tasks(slug: str, user: dict[str, Any] = Depends(current_user)):
        project = visible_project(slug, user)
        rows = db().execute("SELECT t.*, ? AS project_slug FROM tasks t WHERE t.project_id = ? ORDER BY t.updated_at DESC, t.id DESC", (slug, project["id"])).fetchall()
        return {"tasks": [task_payload(dict(r)) for r in rows]}

    @app.post("/api/projects/{slug}/tasks", status_code=201)
    def create_task(slug: str, payload: TaskCreateRequest, user: dict[str, Any] = Depends(current_user)):
        project = visible_project(slug, user)
        profile = profile_for_user(None, user)
        title = payload.title.strip()
        cur = db().execute(
            "INSERT INTO tasks(project_id, title, description, assignee, created_by) VALUES (?, ?, ?, ?, ?)",
            (project["id"], title, payload.description or "", payload.assignee, user["id"]),
        )
        task_id = int(cur.lastrowid)
        scur = db().execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility, task_id) VALUES (?, ?, ?, ?, ?, 'project', ?)",
            (title[:80], project["id"], user["id"], profile["id"], profile["runner_id"], task_id),
        )
        db().execute("UPDATE tasks SET session_id = ? WHERE id = ?", (scur.lastrowid, task_id))
        _task_audit(user, "task.create", task_id)
        row = db().execute("SELECT t.*, ? AS project_slug FROM tasks t WHERE t.id = ?", (slug, task_id)).fetchone()
        return task_payload(dict(row))

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int, user: dict[str, Any] = Depends(current_user)):
        return task_payload(_task_for_user(task_id, user))

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: int, payload: TaskUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        _task_for_user(task_id, user)
        fields: list[str] = []
        vals: list[Any] = []
        if payload.title is not None and payload.title.strip():
            fields.append("title = ?"); vals.append(payload.title.strip())
        if payload.description is not None:
            fields.append("description = ?"); vals.append(payload.description)
        if payload.status is not None:
            fields.append("status = ?"); vals.append(payload.status)
        if payload.assignee is not None:
            fields.append("assignee = ?"); vals.append(payload.assignee or None)
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            db().execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", (*vals, task_id))
            _task_audit(user, "task.update", task_id)
        row = db().execute("SELECT t.*, p.slug AS project_slug FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.id = ?", (task_id,)).fetchone()
        return task_payload(dict(row))

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: int, user: dict[str, Any] = Depends(current_user)):
        task = _task_for_user(task_id, user)
        db().execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if task["session_id"]:
            db().execute("DELETE FROM sessions WHERE id = ?", (task["session_id"],))
        _task_audit(user, "task.delete", task_id)
        return {"ok": True, "id": task_id}

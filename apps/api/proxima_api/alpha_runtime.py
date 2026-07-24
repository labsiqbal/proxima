"""Alpha system identity and in-process product-tool runtime."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable

from fastapi import HTTPException

from . import app_settings, features, workflows as wf, worktrees
from .auth import iso_now
from .job_checkpoints import create_checkpoint
from .schemas import GraphJobCreateRequest

ALPHA_MAX_PARALLEL = 3
ALPHA_MAX_TOOL_ROUNDS = 6
ALPHA_PROFILE_KIND = "alpha"
ALPHA_TOOL_RE = re.compile(r"<proxima-tool>\s*(\{.*?\})\s*</proxima-tool>", re.DOTALL)
ALPHA_INSTRUCTIONS = """You are Alpha, Proxima's built-in orchestrator. You delegate outcomes to worker agents and report progress plainly. You are not a coding worker profile.

Your Proxima product tools are server-owned in-process handlers. To call one, emit exactly:
<proxima-tool>{\"name\":\"tool_name\",\"arguments\":{...}}</proxima-tool>
You may emit several calls. Never use curl, browser requests, localhost, shell commands, or project files to control Proxima.

Allowed tools:
- list_projects {}
- list_jobs {\"status\": optional}
- list_worker_agents {}
- list_plans {\"project_slug\": optional}
- get_alpha_settings {}
- capacity {}
- dispatch_jobs {\"tasks\":[{\"title\":str,\"brief\":str,\"project_slug\":str,\"profile_id\":optional int,\"target_area_id\":optional int}],\"start\":optional bool}
- start_jobs {\"job_ids\":[int,...]}
- start_plan {\"workflow_id\":int,\"project_slug\":optional str,\"profile_id\":optional int,\"input\":optional object,\"start\":optional bool}
- set_unattended {\"enabled\":bool}
- set_budgets {\"turns\":int,\"wall_seconds\":int,\"tokens\":optional int or null}
- create_attention {\"title\":str,\"message\":str}

Default Alpha worker policy is Autonomous. Dispatch independent work together; Proxima runs at most three Alpha workers concurrently and queues the rest. Commit, push, and PR work is allowed when requested and available through the owner's existing git/gh environment. Do not restart stuck work; satpam owns stuck-run recovery. Destructive product administration is not in your allowlist.
When a tool fails, explain the structured error and offer a safe next step. Do not claim a job exists until dispatch_jobs returns its id.
"""


class AlphaToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise AlphaToolError("invalid_integer", "expected an integer, got a boolean")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AlphaToolError("invalid_integer", f"expected an integer, got {value!r}") from exc


def _system_profile_slug(runner_id: str) -> str:
    suffix = re.sub(r"[^a-z0-9]+", "-", runner_id.lower()).strip("-") or "runner"
    return f"alpha-system-{suffix}"[:63].rstrip("-")


def ensure_alpha_identity(
    conn,
    user: dict[str, Any],
    *,
    create_profile_for: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_runner = app_settings.get_setting(conn, "alpha.runner_id")
    if not selected_runner:
        default = conn.execute(
            "SELECT runner_id FROM profiles WHERE user_id = ? AND is_default = 1 "
            "AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
            (user["id"],),
        ).fetchone()
        if not default:
            default = conn.execute(
                "SELECT runner_id FROM profiles WHERE user_id = ? AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
                (user["id"],),
            ).fetchone()
        selected_runner = default["runner_id"] if default else None
    profile = conn.execute(
        "SELECT * FROM profiles WHERE user_id = ? AND system_kind = ? ORDER BY id LIMIT 1",
        (user["id"], ALPHA_PROFILE_KIND),
    ).fetchone()
    if not selected_runner:
        raise AlphaToolError("runner_unavailable", "Set up a runnable agent before opening Alpha")
    if profile and profile["runner_id"] != selected_runner:
        # Keep one durable Alpha identity. Stage the selected runner once so the
        # existing profile receives the correct managed home/credentials, then
        # remove the temporary row without exposing either in Agents.
        staged_slug = _system_profile_slug(str(selected_runner))
        if conn.execute(
            "SELECT 1 FROM profiles WHERE user_id = ? AND slug = ?", (user["id"], staged_slug)
        ).fetchone():
            staged_slug = f"{staged_slug[:52]}-switch-{user['id']}"
        staged = create_profile_for(
            user, staged_slug, "Alpha runner switch", runner_id=str(selected_runner),
            instructions=ALPHA_INSTRUCTIONS,
        )
        conn.execute(
            "UPDATE profiles SET runner_id = ?, hermes_home = ?, default_model = ?, "
            "capabilities = ?, instructions = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                staged["runner_id"], staged["hermes_home"], staged["default_model"],
                staged["capabilities"], ALPHA_INSTRUCTIONS, profile["id"],
            ),
        )
        conn.execute("DELETE FROM profiles WHERE id = ?", (staged["id"],))
        profile = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile["id"],)).fetchone()
    if not profile:
        slug = "alpha-system"
        if conn.execute(
            "SELECT 1 FROM profiles WHERE user_id = ? AND slug = ?", (user["id"], slug)
        ).fetchone():
            slug = f"alpha-system-{user['id']}"
        created = create_profile_for(
            user, slug, "Alpha", runner_id=str(selected_runner), instructions=ALPHA_INSTRUCTIONS,
        )
        conn.execute(
            "UPDATE profiles SET system_kind = ?, is_default = 0 WHERE id = ?",
            (ALPHA_PROFILE_KIND, created["id"]),
        )
        profile = conn.execute("SELECT * FROM profiles WHERE id = ?", (created["id"],)).fetchone()
    profile_dict = dict(profile)
    # Keep the orchestration contract current across upgrades without exposing a
    # fake editable Alpha coding persona in the Agents screen.
    if profile_dict.get("instructions") != ALPHA_INSTRUCTIONS:
        conn.execute(
            "UPDATE profiles SET instructions = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ALPHA_INSTRUCTIONS, profile_dict["id"]),
        )
        profile_dict["instructions"] = ALPHA_INSTRUCTIONS
    session = conn.execute(
        "SELECT * FROM sessions WHERE owner_user_id = ? AND mode = 'alpha' ORDER BY id LIMIT 1",
        (user["id"],),
    ).fetchone()
    if not session:
        cur = conn.execute(
            "INSERT INTO sessions(title, owner_user_id, profile_id, runner_id, visibility, mode, manual_title) "
            "VALUES ('Alpha', ?, ?, ?, 'private', 'alpha', 1)",
            (user["id"], profile_dict["id"], profile_dict["runner_id"]),
        )
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (cur.lastrowid,)).fetchone()
    elif session["profile_id"] != profile_dict["id"] or session["runner_id"] != profile_dict["runner_id"]:
        conn.execute(
            "UPDATE sessions SET profile_id = ?, runner_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (profile_dict["id"], profile_dict["runner_id"], session["id"]),
        )
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session["id"],)).fetchone()
    return profile_dict, dict(session)


def alpha_capacity(conn, alpha_session_id: int) -> dict[str, int]:
    running = conn.execute(
        "SELECT COUNT(DISTINCT r.id) AS c FROM runs r "
        "JOIN sessions s ON s.id = r.session_id JOIN jobs j ON j.id = s.job_id "
        "WHERE j.alpha_session_id = ? AND r.status = 'running'",
        (alpha_session_id,),
    ).fetchone()["c"]
    queued = conn.execute(
        "SELECT ("
        "  SELECT COUNT(*) FROM runs r JOIN sessions s ON s.id = r.session_id "
        "  JOIN jobs j ON j.id = s.job_id WHERE j.alpha_session_id = ? AND r.status = 'queued'"
        ") + ("
        "  SELECT COUNT(*) FROM jobs j WHERE j.alpha_session_id = ? AND j.status = 'queued' "
        "  AND NOT EXISTS (SELECT 1 FROM sessions s JOIN runs r ON r.session_id = s.id "
        "                  WHERE s.job_id = j.id AND r.status = 'queued')"
        ") AS c",
        (alpha_session_id, alpha_session_id),
    ).fetchone()["c"]
    running_int = _as_int(running)
    return {
        "running": running_int,
        "max": ALPHA_MAX_PARALLEL,
        "free": max(0, ALPHA_MAX_PARALLEL - running_int),
        "queued": _as_int(queued),
    }


def _profile_for_worker(conn, user_id: int, profile_id: Any) -> dict[str, Any]:
    if profile_id is None:
        row = conn.execute(
            "SELECT * FROM profiles WHERE user_id = ? AND is_default = 1 "
            "AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
            (user_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ? AND user_id = ? AND COALESCE(system_kind, '') = ''",
            (_as_int(profile_id), user_id),
        ).fetchone()
    if not row:
        raise AlphaToolError("worker_profile_not_found", "Worker agent is not available")
    return dict(row)


def _project_for_slug(conn, user_id: int, slug: Any) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM projects WHERE slug = ? AND owner_user_id = ? AND archived_at IS NULL",
        (str(slug or ""), user_id),
    ).fetchone()
    if not row:
        raise AlphaToolError("project_not_found", f"Project {slug!r} was not found")
    return dict(row)


def _job_payload(conn, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise AlphaToolError("job_not_found", f"Job {job_id} was not found")
    data = dict(row)
    for key, fallback in (("input", {}), ("steps_state", [])):
        try:
            data[key] = json.loads(data.get(key) or json.dumps(fallback))
        except (TypeError, ValueError):
            data[key] = fallback
    return data


def create_alpha_job(conn, app, user: dict[str, Any], alpha_session_id: int, task: dict[str, Any]) -> dict[str, Any]:
    title = str(task.get("title") or "").strip()
    brief = str(task.get("brief") or "").strip()
    if not title or not brief:
        raise AlphaToolError("invalid_task", "Each task needs a title and brief")
    if len(title) > 200 or len(brief) > 50_000:
        raise AlphaToolError("task_too_large", "Task title or brief is too long")
    project = _project_for_slug(conn, user["id"], task.get("project_slug"))
    profile = _profile_for_worker(conn, user["id"], task.get("profile_id"))
    target_area_id = task.get("target_area_id")
    if target_area_id is not None:
        area = conn.execute(
            "SELECT id FROM project_areas WHERE id = ? AND project_id = ? AND source != 'excluded'",
            (_as_int(target_area_id), project["id"]),
        ).fetchone()
        if not area:
            raise AlphaToolError("target_area_not_found", "Target area is not in this project")
        target_area_id = area["id"]
    step = wf.normalize_steps([{"name": "Task", "instruction": brief}])[0]
    input_data = {
        "brief": brief,
        "task_kind": "agent",
        "execution_policy": "autonomous",
        "alpha_dispatched": True,
    }
    steps_state = [wf.step_state_from(step, input_data)]
    scur = conn.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility) "
        "VALUES (?, ?, ?, ?, ?, 'project')",
        (title[:80], project["id"], user["id"], profile["id"], profile["runner_id"]),
    )
    session_id = _as_int(scur.lastrowid)
    jcur = conn.execute(
        "INSERT INTO jobs(project_id, session_id, title, input, steps_state, target_area_id, created_by, alpha_session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project["id"], session_id, title, json.dumps(input_data), json.dumps(steps_state),
            target_area_id, user["id"], alpha_session_id,
        ),
    )
    job_id = _as_int(jcur.lastrowid)
    conn.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, session_id))
    conn.execute(
        "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
        "VALUES (?, 'alpha.job.create', 'job', ?, ?)",
        (user["id"], str(job_id), json.dumps({"alpha_session_id": alpha_session_id, "project": project["slug"]})),
    )
    return _job_payload(conn, job_id)


def create_alpha_plan(conn, app, user: dict[str, Any], alpha_session_id: int, args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _as_int(args.get("workflow_id"))
    workflow = conn.execute(
        "SELECT * FROM workflows WHERE id = ? AND status = 'active' AND (created_by = ? OR project_id IN "
        "(SELECT id FROM projects WHERE owner_user_id = ?))",
        (workflow_id, user["id"], user["id"]),
    ).fetchone()
    if not workflow:
        raise AlphaToolError("plan_not_found", f"Plan {workflow_id} was not found")
    profile = _profile_for_worker(conn, user["id"], args.get("profile_id"))
    project_slug = args.get("project_slug")
    project = _project_for_slug(conn, user["id"], project_slug) if project_slug else None
    project_id = project["id"] if project else workflow["project_id"]
    if workflow["graph"] is not None:
        create_graph = getattr(app.state, "alpha_create_graph_job", None)
        start_graph = getattr(app.state, "alpha_start_graph_job", None)
        if not create_graph or not start_graph:
            raise AlphaToolError("plan_engine_unavailable", "Graph plan engine is unavailable")
        created = create_graph(
            GraphJobCreateRequest(
                title=str(workflow["name"]),
                graph=json.loads(workflow["graph"]),
                input=args.get("input") if isinstance(args.get("input"), dict) else {},
                project_id=project_id,
                profile_id=profile["id"],
                workflow_id=workflow_id,
            ),
            user,
        )
        job_id = _as_int(created["id"])
        conn.execute("UPDATE jobs SET alpha_session_id = ? WHERE id = ?", (alpha_session_id, job_id))
        conn.execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
            "VALUES (?, 'alpha.job.create', 'job', ?, ?)",
            (user["id"], str(job_id), json.dumps({"alpha_session_id": alpha_session_id, "workflow_id": workflow_id})),
        )
        create_checkpoint(conn, job_id)
        start_error = None
        if args.get("start", True):
            try:
                start_graph(job_id, user)
            except HTTPException as exc:
                start_error = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
        result = _job_payload(conn, job_id)
        if start_error:
            result["_start_error"] = start_error
        return result
    steps = json.loads(workflow["steps"] or "[]")
    inputs = args.get("input") if isinstance(args.get("input"), dict) else {}
    states = [wf.step_state_from(step, inputs) for step in steps]
    visibility = "project" if project_id else "private"
    scur = conn.execute(
        "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility) VALUES (?, ?, ?, ?, ?, ?)",
        (str(workflow["name"])[:80], project_id, user["id"], profile["id"], profile["runner_id"], visibility),
    )
    jcur = conn.execute(
        "INSERT INTO jobs(project_id, workflow_id, session_id, title, input, steps_state, created_by, alpha_session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, workflow_id, scur.lastrowid, workflow["name"], json.dumps(inputs), json.dumps(states), user["id"], alpha_session_id),
    )
    job_id = _as_int(jcur.lastrowid)
    conn.execute("UPDATE sessions SET job_id = ? WHERE id = ?", (job_id, scur.lastrowid))
    conn.execute(
        "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
        "VALUES (?, 'alpha.job.create', 'job', ?, ?)",
        (user["id"], str(job_id), json.dumps({"alpha_session_id": alpha_session_id, "workflow_id": workflow_id})),
    )
    if args.get("start", True):
        try:
            return start_alpha_job(conn, app, user, job_id)
        except AlphaToolError as exc:
            result = _job_payload(conn, job_id)
            result["_start_error"] = str(exc)
            return result
    return _job_payload(conn, job_id)


def start_alpha_job(conn, app, user: dict[str, Any], job_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ? AND created_by = ? AND alpha_session_id IS NOT NULL",
        (job_id, user["id"]),
    ).fetchone()
    if not row:
        raise AlphaToolError("job_not_found", f"Alpha job {job_id} was not found")
    job = dict(row)
    if job["status"] != "queued":
        return _job_payload(conn, job_id)
    steps = json.loads(job["steps_state"] or "[]")
    if not steps or not job.get("session_id"):
        raise AlphaToolError("job_not_startable", "Job has no runnable session or step")
    if features.enabled(app.state.config, features.REPO_WORKTREES):
        try:
            worktrees.ensure_job_worktree(conn, app.state.config, job)
        except worktrees.WorktreeError as exc:
            raise AlphaToolError("worktree_failed", f"Cannot start repo job: {exc}") from exc
    # Capture the queued job after any isolated worktree exists, but before a
    # run is enqueued. This makes a repo checkpoint genuinely restorable while
    # the primary checkout remains reference-only.
    create_checkpoint(conn, job_id)
    session = conn.execute("SELECT * FROM sessions WHERE id = ?", (job["session_id"],)).fetchone()
    profile = conn.execute("SELECT * FROM profiles WHERE id = ?", (session["profile_id"],)).fetchone() if session else None
    if not profile:
        raise AlphaToolError("worker_profile_not_found", "Worker agent is no longer available")
    inputs = json.loads(job["input"] or "{}")
    prompt = wf.build_step_prompt(steps[0], 0, len(steps), inputs)
    with app.state.db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            claimed = conn.execute(
                "UPDATE jobs SET status='running', started_at=CURRENT_TIMESTAMP, current_step_idx=0, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='queued'",
                (job_id,),
            )
            if claimed.rowcount == 0:
                conn.execute("ROLLBACK")
                return _job_payload(conn, job_id)
            cur = conn.execute(
                "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (
                    job["session_id"], job["project_id"], user["id"], profile["id"],
                    profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"],
                ),
            )
            run_id = _as_int(cur.lastrowid)
            steps[0].update({"status": "running", "run_id": run_id, "started_at": iso_now()})
            conn.execute(
                "UPDATE jobs SET steps_state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(steps), job_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    app.state.worker.add_event(
        run_id, job["session_id"], job["project_id"], "run.queued",
        {"runner": profile["runner_id"], "job": job_id, "alpha": True},
    )
    return _job_payload(conn, job_id)


def _tool_list_projects(conn, user: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT slug, name FROM projects WHERE owner_user_id = ? AND archived_at IS NULL ORDER BY name",
        (user["id"],),
    ).fetchall()
    return {"projects": [dict(row) for row in rows]}


def _tool_list_jobs(conn, user: dict[str, Any], alpha_session_id: int, args: dict[str, Any]) -> dict[str, Any]:
    status = args.get("status")
    if status and status not in {"queued", "running", "review", "done", "failed", "cancelled"}:
        raise AlphaToolError("invalid_status", f"Unknown job status {status!r}")
    rows = conn.execute(
        "SELECT id, title, status, project_id, created_at, updated_at FROM jobs "
        "WHERE alpha_session_id = ? AND (? IS NULL OR status = ?) ORDER BY id DESC LIMIT 100",
        (alpha_session_id, status, status),
    ).fetchall()
    return {"jobs": [dict(row) for row in rows]}


def execute_tool(conn, app, user: dict[str, Any], alpha_session_id: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "list_projects":
            data = _tool_list_projects(conn, user)
        elif name == "list_jobs":
            data = _tool_list_jobs(conn, user, alpha_session_id, args)
        elif name == "list_worker_agents":
            rows = conn.execute(
                "SELECT id, name, runner_id, is_default FROM profiles WHERE user_id = ? "
                "AND COALESCE(system_kind, '') = '' ORDER BY is_default DESC, name",
                (user["id"],),
            ).fetchall()
            data = {"agents": [dict(row) for row in rows]}
        elif name == "list_plans":
            project_slug = args.get("project_slug")
            project_id = _project_for_slug(conn, user["id"], project_slug)["id"] if project_slug else None
            rows = conn.execute(
                "SELECT id, name, description, category, CASE WHEN graph IS NULL THEN 'linear' ELSE 'graph' END AS engine "
                "FROM workflows WHERE status = 'active' AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?)) AND (? IS NULL OR project_id = ?) "
                "ORDER BY updated_at DESC LIMIT 100",
                (user["id"], user["id"], project_id, project_id),
            ).fetchall()
            data = {"plans": [dict(row) for row in rows]}
        elif name == "get_alpha_settings":
            data = app_settings.get_alpha_settings(conn)
        elif name == "capacity":
            data = {"capacity": alpha_capacity(conn, alpha_session_id)}
        elif name == "dispatch_jobs":
            tasks = args.get("tasks")
            if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
                raise AlphaToolError("invalid_tasks", "dispatch_jobs needs 1 to 20 tasks")
            if not all(isinstance(task, dict) for task in tasks):
                raise AlphaToolError("invalid_task", "Every task must be an object")
            # Validate/create the batch atomically so one malformed task cannot
            # leave an unreported partial dispatch behind.
            conn.execute("SAVEPOINT alpha_dispatch")
            try:
                jobs = [create_alpha_job(conn, app, user, alpha_session_id, task) for task in tasks]
                conn.execute("RELEASE SAVEPOINT alpha_dispatch")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT alpha_dispatch")
                conn.execute("RELEASE SAVEPOINT alpha_dispatch")
                raise
            start_errors: list[dict[str, Any]] = []
            if args.get("start", True):
                started_jobs: list[dict[str, Any]] = []
                for job in jobs:
                    try:
                        started_jobs.append(start_alpha_job(conn, app, user, job["id"]))
                    except AlphaToolError as exc:
                        queued = _job_payload(conn, job["id"])
                        start_errors.append({"job_id": job["id"], "code": exc.code, "message": str(exc)})
                        started_jobs.append(queued)
                jobs = started_jobs
            data = {
                "jobs": [{"id": job["id"], "title": job["title"], "status": job["status"]} for job in jobs],
                "capacity": alpha_capacity(conn, alpha_session_id),
            }
            if start_errors:
                data["start_errors"] = start_errors
                return {
                    "ok": False,
                    "tool": name,
                    "result": data,
                    "error": {
                        "code": "job_start_failed",
                        "message": "The jobs were created but some remained queued; inspect the returned job cards.",
                    },
                }
        elif name == "start_jobs":
            ids = args.get("job_ids")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise AlphaToolError("invalid_job_ids", "start_jobs needs 1 to 20 job ids")
            job_ids = [_as_int(job_id) for job_id in ids]
            owned = conn.execute(
                f"SELECT id FROM jobs WHERE created_by = ? AND alpha_session_id = ? AND id IN ({','.join('?' for _ in job_ids)})",
                (user["id"], alpha_session_id, *job_ids),
            ).fetchall()
            if {row["id"] for row in owned} != set(job_ids):
                raise AlphaToolError("job_not_found", "One or more Alpha jobs were not found")
            jobs: list[dict[str, Any]] = []
            start_errors: list[dict[str, Any]] = []
            for job_id in job_ids:
                try:
                    jobs.append(start_alpha_job(conn, app, user, job_id))
                except AlphaToolError as exc:
                    jobs.append(_job_payload(conn, job_id))
                    start_errors.append({"job_id": job_id, "code": exc.code, "message": str(exc)})
            data = {
                "jobs": [{"id": job["id"], "title": job["title"], "status": job["status"]} for job in jobs],
                "capacity": alpha_capacity(conn, alpha_session_id),
            }
            if start_errors:
                data["start_errors"] = start_errors
                return {
                    "ok": False, "tool": name, "result": data,
                    "error": {"code": "job_start_failed", "message": "Some jobs remained queued; inspect the returned job cards."},
                }
        elif name == "start_plan":
            job = create_alpha_plan(conn, app, user, alpha_session_id, args)
            data = {
                "job": {"id": job["id"], "title": job["title"], "status": job["status"], "engine": job.get("engine")},
                "capacity": alpha_capacity(conn, alpha_session_id),
            }
            if job.get("_start_error"):
                return {
                    "ok": False, "tool": name, "result": data,
                    "error": {"code": "plan_start_failed", "message": str(job["_start_error"])},
                }
        elif name == "set_unattended":
            enabled = args.get("enabled")
            if not isinstance(enabled, bool):
                raise AlphaToolError("invalid_boolean", "enabled must be true or false")
            app_settings.set_alpha_settings(conn, unattended=enabled)
            data = {"unattended": enabled}
            conn.execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                "VALUES (?, 'alpha.settings.change', 'settings', 'alpha.unattended', ?)",
                (user["id"], json.dumps({"enabled": enabled})),
            )
        elif name == "set_budgets":
            tokens: int | None | object = ...
            if "tokens" in args:
                tokens = None if args["tokens"] is None else _as_int(args["tokens"])
            data = app_settings.set_alpha_settings(
                conn,
                budget_turns=_as_int(args.get("turns")),
                budget_wall_seconds=_as_int(args.get("wall_seconds")),
                budget_tokens=tokens,
            )
            conn.execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                "VALUES (?, 'alpha.settings.change', 'settings', 'alpha.budgets', ?)",
                (user["id"], json.dumps({"turns": data["budget_turns"], "wall_seconds": data["budget_wall_seconds"], "tokens": data["budget_tokens"]})),
            )
        elif name == "create_attention":
            title = str(args.get("title") or "").strip()
            message = str(args.get("message") or "").strip()
            if not title or not message:
                raise AlphaToolError("invalid_attention", "Attention needs a title and message")
            cur = conn.execute(
                "INSERT INTO attention_items(kind, title, target_json, inline_ok, status, source_key) "
                "VALUES ('alpha_decision', ?, ?, 0, 'open', ?)",
                (title[:200], json.dumps({"view": "alpha", "message": message}), f"alpha:{alpha_session_id}:{iso_now()}"),
            )
            data = {"attention_id": _as_int(cur.lastrowid)}
        else:
            raise AlphaToolError("tool_not_allowed", f"Alpha tool {name!r} is not allowed")
        return {"ok": True, "tool": name, "result": data}
    except AlphaToolError as exc:
        return {"ok": False, "tool": name, "error": {"code": exc.code, "message": str(exc)}}
    except HTTPException as exc:
        detail = exc.detail
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        return {"ok": False, "tool": name, "error": {"code": "product_request_failed", "message": message}}
    except (sqlite3.Error, ValueError, TypeError) as exc:
        return {"ok": False, "tool": name, "error": {"code": "tool_failed", "message": str(exc)}}


def _tool_round(kind: Any) -> int:
    if kind == "alpha":
        return 0
    match = re.fullmatch(r"alpha_tool_(\d+)", str(kind or ""))
    return int(match.group(1)) if match else ALPHA_MAX_TOOL_ROUNDS


def handle_alpha_response(app, conn, run: dict[str, Any], answer: str) -> list[dict[str, Any]]:
    session = conn.execute(
        "SELECT s.mode, s.owner_user_id FROM sessions s WHERE s.id = ?", (run["session_id"],)
    ).fetchone()
    if not session or session["mode"] != "alpha":
        return []
    calls: list[dict[str, Any]] = []
    for raw in ALPHA_TOOL_RE.findall(answer):
        try:
            call = json.loads(raw)
            if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                raise ValueError("tool call must contain a string name")
            args = call.get("arguments") or {}
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
            calls.append(execute_tool(conn, app, {"id": session["owner_user_id"]}, run["session_id"], call["name"], args))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            calls.append({"ok": False, "tool": None, "error": {"code": "invalid_tool_call", "message": str(exc)}})
    if calls:
        result_json = json.dumps(calls, indent=2)
        conn.execute("SAVEPOINT alpha_tool_result")
        try:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'system', ?, 'Proxima', ?)",
                (run["session_id"], "Alpha tool results:\n```json\n" + result_json + "\n```", run["id"]),
            )
            conn.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (run["session_id"],))
            next_run_id = None
            round_number = _tool_round(run.get("kind"))
            if round_number < ALPHA_MAX_TOOL_ROUNDS:
                prompt = (
                    "Proxima executed your in-process product tools. Here are the trusted results:\n"
                    f"<proxima-results>\n{result_json}\n</proxima-results>\n"
                    "Continue the owner's request using these results. Do not repeat a successful mutation. "
                    "Call another product tool only when needed; otherwise report the outcome plainly."
                )
                cur = conn.execute(
                    "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, kind, status, prompt, model, hermes_home) "
                    "VALUES (?, NULL, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (
                        run["session_id"], run["user_id"], run["profile_id"], run["runner_id"],
                        f"alpha_tool_{round_number + 1}", prompt, run.get("model"), run.get("hermes_home"),
                    ),
                )
                next_run_id = _as_int(cur.lastrowid)
            conn.execute("RELEASE SAVEPOINT alpha_tool_result")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT alpha_tool_result")
            conn.execute("RELEASE SAVEPOINT alpha_tool_result")
            raise
        if next_run_id is not None:
            app.state.worker.add_event(
                next_run_id, run["session_id"], None, "run.queued",
                {"runner": run["runner_id"], "alpha": True, "tool_round": round_number + 1},
            )
    return calls

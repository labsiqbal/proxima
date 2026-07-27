"""Master system identity and in-process product-tool runtime."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from fastapi import HTTPException

from . import app_settings
from .auth import iso_now
from .job_checkpoints import create_checkpoint
from .master_persistence import master_identity_rows
from .master_tool_broker import MasterToolBroker, validate_master_tool_call
from .runner_specs import master_runner_conformance, runner_spec
from .task_delegation import (
    DependencyRequest,
    TaskDelegationError,
    TaskDelegationRequest,
    new_idempotency_key,
)

MASTER_MAX_PARALLEL = 3
MASTER_MAX_TOOL_ROUNDS = 6
MASTER_MAX_CALLS_PER_ROUND = 8
MASTER_MAX_TOOL_REQUEST_BYTES = 16 * 1024
MASTER_MAX_ROUND_RESULT_BYTES = 64 * 1024
MASTER_MAX_TURN_OUTPUT_BYTES = 128 * 1024
MASTER_PROFILE_KIND = "master"
MASTER_EMPTY_CAPABILITIES = '{"skills":[],"mcp":[]}'
MASTER_INSTRUCTIONS = """You are Master, Proxima's built-in orchestrator. You delegate outcomes to worker agents and report progress plainly. You are not a coding worker profile.

Your Proxima product tools are server-owned in-process handlers. When the runner exposes native Proxima function tools, call those functions directly and never emit XML. A compatibility harness may instead require exactly:
<proxima-tool>{\"name\":\"tool_name\",\"arguments\":{...}}</proxima-tool>
You may emit several calls. Never use curl, browser requests, localhost, shell commands, or project files to control Proxima.

Allowed tools:
- list_containers
- get_container
- get_live_state
- list_tasks
- list_task_agents
- list_recipes
- query_context
- delegate_tasks
- start_tasks
- create_attention

Use only registered Container, Area, Task-agent, Recipe, and Task IDs returned by these tools. Never request or emit filesystem paths, runner homes, credentials, bearer material, configuration, shell commands, browser actions, skills, MCP calls, or runner-native tools. Every action that changes work must be a delegated Task. Guarded and Autonomous are Task-agent execution policies; repo Tasks still stop for review before landing.
When a tool fails, explain the structured error and offer a safe next step. Do not claim a Task exists until delegate_tasks returns its id.
"""


class MasterToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise MasterToolError("invalid_integer", "expected an integer, got a boolean")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MasterToolError("invalid_integer", f"expected an integer, got {value!r}") from exc


def _system_profile_slug(runner_id: str) -> str:
    suffix = re.sub(r"[^a-z0-9]+", "-", runner_id.lower()).strip("-") or "runner"
    return f"master-system-{suffix}"[:63].rstrip("-")


def prepare_master_runtime(
    cfg: dict[str, Any],
    *,
    runner_id: str,
    managed_home: str,
) -> str:
    """Validate the dedicated home and create one empty read-only scratch."""
    conforming, reason = master_runner_conformance(runner_id)
    if not conforming:
        raise MasterToolError(
            "master_runner_not_conforming",
            f"Runner cannot run Master because its {reason}",
        )
    if not managed_home:
        raise MasterToolError(
            "master_home_unavailable", "Master has no dedicated managed runner home"
        )
    profiles_root = Path(str(cfg["hermes_profiles_root"])).resolve()
    home = Path(managed_home).resolve()
    try:
        home.relative_to(profiles_root)
    except ValueError as exc:
        raise MasterToolError(
            "master_home_invalid",
            "Master runner home is outside the managed profile root",
        ) from exc
    if home == profiles_root:
        raise MasterToolError(
            "master_home_invalid", "Master runner home is not dedicated"
        )
    spec = runner_spec(runner_id)
    skills = home / "skills"
    if skills.is_symlink():
        skills.unlink()
    elif skills.exists():
        shutil.rmtree(skills)
    skills.mkdir(mode=0o700, parents=True)
    for relative, content in spec.master_home_templates:
        target = home / relative
        try:
            target.resolve().relative_to(home)
        except ValueError as exc:
            raise MasterToolError(
                "master_home_invalid",
                "Master runner template escapes its dedicated home",
            ) from exc
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    runtime_root = Path(str(cfg["workspace_root"])) / "master-runtime"
    scratch = runtime_root / "scratch"
    runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runtime_root.is_symlink() or scratch.is_symlink():
        raise MasterToolError(
            "master_scratch_invalid", "Master scratch cannot be a symlink"
        )
    scratch.mkdir(mode=0o555, exist_ok=True)
    if any(scratch.iterdir()):
        raise MasterToolError(
            "master_scratch_not_empty", "Master scratch is not empty"
        )
    try:
        scratch.chmod(0o555)
    except OSError as exc:
        raise MasterToolError(
            "master_scratch_not_read_only",
            "Master scratch could not be made read-only",
        ) from exc
    if scratch.stat().st_mode & 0o222:
        raise MasterToolError(
            "master_scratch_not_read_only", "Master scratch is writable"
        )
    return str(scratch)


def ensure_master_identity(
    conn,
    user: dict[str, Any],
    *,
    create_profile_for: Callable[..., dict[str, Any]],
    managed_profiles_root: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile, session = master_identity_rows(conn, int(user["id"]))
    selected_runner = app_settings.get_setting(conn, "master.runner_id")
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
    if not selected_runner:
        raise MasterToolError("runner_unavailable", "Set up a runnable agent before opening Master")
    needs_managed_home = False
    if profile and managed_profiles_root is not None:
        try:
            Path(str(profile["hermes_home"])).resolve().relative_to(
                Path(managed_profiles_root).resolve()
            )
        except ValueError:
            needs_managed_home = True
    if profile and (
        profile["runner_id"] != selected_runner or needs_managed_home
    ):
        # Keep one durable Master identity. Stage the selected runner once so the
        # existing profile receives the correct managed home/credentials, then
        # remove the temporary row without exposing either in Agents.
        staged_slug = _system_profile_slug(str(selected_runner))
        if conn.execute(
            "SELECT 1 FROM profiles WHERE user_id = ? AND slug = ?", (user["id"], staged_slug)
        ).fetchone():
            staged_slug = f"{staged_slug[:52]}-switch-{user['id']}"
        staged = create_profile_for(
            user, staged_slug, "Master runner switch", runner_id=str(selected_runner),
            instructions=MASTER_INSTRUCTIONS,
            force_managed_home=True,
        )
        conn.execute(
            "UPDATE profiles SET runner_id = ?, hermes_home = ?, default_model = ?, "
            "capabilities = ?, instructions = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                staged["runner_id"], staged["hermes_home"], staged["default_model"],
                MASTER_EMPTY_CAPABILITIES, MASTER_INSTRUCTIONS, profile["id"],
            ),
        )
        conn.execute("DELETE FROM profiles WHERE id = ?", (staged["id"],))
        profile = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile["id"],)).fetchone()
    if not profile:
        slug = "master-system"
        if conn.execute(
            "SELECT 1 FROM profiles WHERE user_id = ? AND slug = ?", (user["id"], slug)
        ).fetchone():
            slug = f"master-system-{user['id']}"
        created = create_profile_for(
            user, slug, "Master", runner_id=str(selected_runner), instructions=MASTER_INSTRUCTIONS,
            force_managed_home=True,
        )
        conn.execute(
            "UPDATE profiles SET system_kind = ?, is_default = 0, capabilities = ? "
            "WHERE id = ?",
            (MASTER_PROFILE_KIND, MASTER_EMPTY_CAPABILITIES, created["id"]),
        )
        profile = conn.execute("SELECT * FROM profiles WHERE id = ?", (created["id"],)).fetchone()
    profile_dict = dict(profile)
    if profile_dict.get("capabilities") != MASTER_EMPTY_CAPABILITIES:
        conn.execute(
            "UPDATE profiles SET capabilities = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (MASTER_EMPTY_CAPABILITIES, profile_dict["id"]),
        )
        profile_dict["capabilities"] = MASTER_EMPTY_CAPABILITIES
    # Keep the orchestration contract current across upgrades without exposing a
    # fake editable Master coding persona in the Agents screen.
    if profile_dict.get("instructions") != MASTER_INSTRUCTIONS:
        conn.execute(
            "UPDATE profiles SET instructions = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (MASTER_INSTRUCTIONS, profile_dict["id"]),
        )
        profile_dict["instructions"] = MASTER_INSTRUCTIONS
    if not session:
        cur = conn.execute(
            "INSERT INTO sessions(title, owner_user_id, profile_id, runner_id, visibility, mode, manual_title) "
            "VALUES ('Master', ?, ?, ?, 'private', 'master', 1)",
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


def master_parallel_limit(config: Mapping[str, Any] | None = None) -> int:
    try:
        value = int((config or {}).get("master_max_parallel", MASTER_MAX_PARALLEL))
    except (TypeError, ValueError, OverflowError):
        value = MASTER_MAX_PARALLEL
    return max(1, min(64, value))


def master_active_slots(conn, origin_master_session_id: int) -> int:
    return _as_int(
        conn.execute(
            "SELECT ("
            "  SELECT COUNT(*) FROM runs active_run "
            "  JOIN sessions active_session "
            "  ON active_session.id = active_run.session_id "
            "  JOIN jobs active_job "
            "  ON active_job.id = active_session.job_id "
            "  WHERE active_job.origin_master_session_id = ? "
            "  AND active_run.status IN ('queued', 'running')"
            ") + ("
            "  SELECT COUNT(*) FROM jobs reserved_job "
            "  WHERE reserved_job.origin_master_session_id = ? "
            "  AND reserved_job.status = 'running' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM sessions reserved_session "
            "    JOIN runs reserved_run "
            "    ON reserved_run.session_id = reserved_session.id "
            "    WHERE reserved_session.job_id = reserved_job.id "
            "    AND reserved_run.status IN ('queued', 'running')"
            "  )"
            ") AS c",
            (origin_master_session_id, origin_master_session_id),
        ).fetchone()["c"]
    )


def master_capacity(
    conn,
    origin_master_session_id: int,
    *,
    max_parallel: int = MASTER_MAX_PARALLEL,
) -> dict[str, int]:
    running = conn.execute(
        "SELECT COUNT(DISTINCT r.id) AS c FROM runs r "
        "JOIN sessions s ON s.id = r.session_id JOIN jobs j ON j.id = s.job_id "
        "WHERE j.origin_master_session_id = ? AND r.status = 'running'",
        (origin_master_session_id,),
    ).fetchone()["c"]
    queued = conn.execute(
        "SELECT ("
        "  SELECT COUNT(*) FROM runs r JOIN sessions s ON s.id = r.session_id "
        "  JOIN jobs j ON j.id = s.job_id WHERE j.origin_master_session_id = ? AND r.status = 'queued'"
        ") + ("
        "  SELECT COUNT(*) FROM jobs j WHERE j.origin_master_session_id = ? AND j.status = 'queued' "
        "  AND NOT EXISTS (SELECT 1 FROM sessions s JOIN runs r ON r.session_id = s.id "
        "                  WHERE s.job_id = j.id AND r.status = 'queued')"
        ") AS c",
        (origin_master_session_id, origin_master_session_id),
    ).fetchone()["c"]
    active_slots = master_active_slots(conn, origin_master_session_id)
    running_int = _as_int(running)
    return {
        "running": running_int,
        "max": max_parallel,
        "free": max(0, max_parallel - active_slots),
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
        raise MasterToolError("worker_profile_not_found", "Worker agent is not available")
    return dict(row)


def _project_for_slug(conn, user_id: int, slug: Any) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM projects WHERE slug = ? AND owner_user_id = ? AND archived_at IS NULL",
        (str(slug or ""), user_id),
    ).fetchone()
    if not row:
        raise MasterToolError("project_not_found", f"Project {slug!r} was not found")
    return dict(row)


def _job_payload(conn, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise MasterToolError("job_not_found", f"Job {job_id} was not found")
    data = dict(row)
    for key, fallback in (("input", {}), ("steps_state", [])):
        try:
            data[key] = json.loads(data.get(key) or json.dumps(fallback))
        except (TypeError, ValueError):
            data[key] = fallback
    delegation = conn.execute(
        "SELECT * FROM task_delegations WHERE job_id = ?", (job_id,)
    ).fetchone()
    if delegation:
        data["delegation"] = dict(delegation)
    dependencies = conn.execute(
        "SELECT task_id, depends_on_task_id, required_status, created_at, updated_at "
        "FROM task_dependencies WHERE task_id = ? ORDER BY depends_on_task_id",
        (job_id,),
    ).fetchall()
    if dependencies:
        data["dependencies"] = [dict(row) for row in dependencies]
    return data


def create_master_plan(conn, app, user: dict[str, Any], origin_master_session_id: int, args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _as_int(args.get("workflow_id"))
    workflow = conn.execute(
        "SELECT * FROM workflows WHERE id = ? AND status = 'active' AND (created_by = ? OR project_id IN "
        "(SELECT id FROM projects WHERE owner_user_id = ?))",
        (workflow_id, user["id"], user["id"]),
    ).fetchone()
    if not workflow:
        raise MasterToolError("plan_not_found", f"Plan {workflow_id} was not found")
    profile = _profile_for_worker(conn, user["id"], args.get("profile_id"))
    project_slug = args.get("project_slug")
    project = _project_for_slug(conn, user["id"], project_slug) if project_slug else None
    project_id = project["id"] if project else workflow["project_id"]
    if project_id is None:
        raise MasterToolError(
            "container_required", "A delegated Recipe needs one Container"
        )
    selected_area_id = args.get("target_area_id")
    if selected_area_id is None:
        area = conn.execute(
            "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops' "
            "AND source != 'excluded'",
            (project_id,),
        ).fetchone()
    else:
        area = conn.execute(
            "SELECT id FROM project_areas WHERE id = ? AND project_id = ? "
            "AND source != 'excluded'",
            (_as_int(selected_area_id), project_id),
        ).fetchone()
    if area is None:
        raise MasterToolError(
            "target_area_not_found", "Target Area is not in this Container"
        )
    inputs = args.get("input") if isinstance(args.get("input"), dict) else {}
    should_start = bool(args.get("start", True))
    try:
        result = app.state.task_delegation.create_batch(
            user,
            [
                TaskDelegationRequest(
                    title=str(workflow["name"]),
                    brief=str(
                        inputs.get("brief")
                        or workflow["description"]
                        or workflow["name"]
                    ),
                    container_id=_as_int(project_id),
                    area_id=_as_int(area["id"]),
                    profile_id=_as_int(profile["id"]),
                    execution_policy="autonomous",
                    recipe_id=workflow_id,
                    input_data=inputs,
                    origin_session_id=origin_master_session_id,
                    routing_mode="explicit",
                    routing_reason=(
                        "Master selected the Recipe Area"
                        if selected_area_id is not None
                        else "Master defaulted the Recipe to the Container Ops Area"
                    ),
                    idempotency_key=str(
                        args.get("idempotency_key")
                        or new_idempotency_key("master-recipe")
                    ),
                ),
            ],
            start=should_start,
            defer_start=should_start,
            connection=conn,
        )[0]
    except TaskDelegationError as exc:
        raise MasterToolError(exc.code, str(exc)) from exc
    job_id = _as_int(result.job["id"])
    start_error = None
    if should_start:
        try:
            start_master_job(conn, app, user, job_id)
        except MasterToolError as exc:
            start_error = str(exc)
    if workflow["graph"] is not None and not conn.execute(
        "SELECT 1 FROM job_checkpoints WHERE job_id = ? LIMIT 1", (job_id,)
    ).fetchone():
        create_checkpoint(conn, job_id)
    payload = _job_payload(conn, job_id)
    if start_error:
        payload["_start_error"] = start_error
    return payload


def start_master_job(
    conn,
    app,
    user: dict[str, Any],
    job_id: int,
    *,
    supervisor_budget_turns: int | None = None,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ? AND created_by = ? AND origin_master_session_id IS NOT NULL",
        (job_id, user["id"]),
    ).fetchone()
    if not row:
        raise MasterToolError("job_not_found", f"Master job {job_id} was not found")
    try:
        app.state.task_delegation.start(
            job_id,
            user,
            connection=conn,
            supervisor_budget_turns=supervisor_budget_turns,
        )
    except TaskDelegationError as exc:
        raise MasterToolError(exc.code, str(exc)) from exc
    return _job_payload(conn, job_id)


def _tool_list_projects(conn, user: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT slug, name FROM projects WHERE owner_user_id = ? AND archived_at IS NULL ORDER BY name",
        (user["id"],),
    ).fetchall()
    return {"projects": [dict(row) for row in rows]}


def _tool_list_jobs(conn, user: dict[str, Any], origin_master_session_id: int, args: dict[str, Any]) -> dict[str, Any]:
    status = args.get("status")
    if status and status not in {"queued", "running", "review", "done", "failed", "cancelled"}:
        raise MasterToolError("invalid_status", f"Unknown job status {status!r}")
    rows = conn.execute(
        "SELECT id, title, status, project_id, created_at, updated_at FROM jobs "
        "WHERE origin_master_session_id = ? AND (? IS NULL OR status = ?) ORDER BY id DESC LIMIT 100",
        (origin_master_session_id, status, status),
    ).fetchall()
    return {"jobs": [dict(row) for row in rows]}


def _execute_legacy_tool(conn, app, user: dict[str, Any], origin_master_session_id: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "list_projects":
            data = _tool_list_projects(conn, user)
        elif name == "list_jobs":
            data = _tool_list_jobs(conn, user, origin_master_session_id, args)
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
        elif name in {"get_master_settings", "get_alpha_settings"}:
            data = app_settings.get_master_settings(conn)
        elif name == "capacity":
            data = {"capacity": master_capacity(conn, origin_master_session_id)}
        elif name == "dispatch_jobs":
            tasks = args.get("tasks")
            if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
                raise MasterToolError("invalid_tasks", "dispatch_jobs needs 1 to 20 tasks")
            if not all(isinstance(task, dict) for task in tasks):
                raise MasterToolError("invalid_task", "Every task must be an object")
            batch_key = str(
                args.get("idempotency_key")
                or new_idempotency_key("master-batch")
            )
            requests: list[TaskDelegationRequest] = []
            for index, task in enumerate(tasks):
                title = str(task.get("title") or "").strip()
                brief = str(task.get("brief") or "").strip()
                if not title or not brief:
                    raise MasterToolError(
                        "invalid_task", "Each task needs a title and brief"
                    )
                project = _project_for_slug(
                    conn, user["id"], task.get("project_slug")
                )
                profile = _profile_for_worker(
                    conn, user["id"], task.get("profile_id")
                )
                selected_area_id = task.get("target_area_id")
                if selected_area_id is None:
                    area = conn.execute(
                        "SELECT id FROM project_areas WHERE project_id = ? "
                        "AND kind = 'ops' AND source != 'excluded'",
                        (project["id"],),
                    ).fetchone()
                else:
                    area = conn.execute(
                        "SELECT id FROM project_areas WHERE id = ? "
                        "AND project_id = ? AND source != 'excluded'",
                        (_as_int(selected_area_id), project["id"]),
                    ).fetchone()
                if area is None:
                    raise MasterToolError(
                        "target_area_not_found",
                        "Target Area is not in this Container",
                    )
                client_key = str(
                    task.get("key") or task.get("client_key") or f"task-{index + 1}"
                )
                raw_dependencies = task.get("depends_on") or []
                if not isinstance(raw_dependencies, list):
                    raise MasterToolError(
                        "invalid_dependencies", "depends_on must be a list"
                    )
                dependencies: list[DependencyRequest] = []
                for raw_dependency in raw_dependencies:
                    if isinstance(raw_dependency, dict):
                        dependency_task = raw_dependency.get(
                            "task", raw_dependency.get("key")
                        )
                        required_status = str(
                            raw_dependency.get("required_status") or "done"
                        )
                    else:
                        dependency_task = raw_dependency
                        required_status = "done"
                    if not isinstance(dependency_task, (int, str)):
                        raise MasterToolError(
                            "invalid_dependency",
                            "Each dependency must be a Task id or client-local key",
                        )
                    dependencies.append(
                        DependencyRequest(
                            dependency_task, required_status=required_status
                        )
                    )
                requests.append(
                    TaskDelegationRequest(
                        title=title,
                        brief=brief,
                        container_id=_as_int(project["id"]),
                        area_id=_as_int(area["id"]),
                        profile_id=_as_int(profile["id"]),
                        execution_policy="autonomous",
                        input_data={
                            "brief": brief,
                            "task_kind": "agent",
                            "execution_policy": "autonomous",
                            "master_dispatched": True,
                        },
                        dependencies=tuple(dependencies),
                        origin_session_id=origin_master_session_id,
                        routing_mode="explicit",
                        routing_reason=(
                            "Master selected the Task Area"
                            if selected_area_id is not None
                            else "Master defaulted to the Container Ops Area"
                        ),
                        idempotency_key=str(
                            task.get("idempotency_key")
                            or f"{batch_key}:{client_key}"
                        ),
                        client_key=client_key,
                    )
                )
            try:
                delegated = app.state.task_delegation.create_batch(
                    user,
                    requests,
                    start=bool(args.get("start", True)),
                    defer_start=bool(args.get("start", True)),
                    connection=conn,
                )
            except TaskDelegationError as exc:
                raise MasterToolError(exc.code, str(exc)) from exc
            jobs = [
                _job_payload(conn, _as_int(result.job["id"]))
                for result in delegated
            ]
            start_errors: list[dict[str, Any]] = []
            if args.get("start", True):
                started_jobs: list[dict[str, Any]] = []
                for job in jobs:
                    try:
                        started_jobs.append(start_master_job(conn, app, user, job["id"]))
                    except MasterToolError as exc:
                        queued = _job_payload(conn, job["id"])
                        if exc.code != "master_capacity_full":
                            start_errors.append(
                                {
                                    "job_id": job["id"],
                                    "code": exc.code,
                                    "message": str(exc),
                                }
                            )
                        started_jobs.append(queued)
                jobs = started_jobs
            data = {
                "jobs": [{"id": job["id"], "title": job["title"], "status": job["status"]} for job in jobs],
                "capacity": master_capacity(conn, origin_master_session_id),
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
                raise MasterToolError("invalid_job_ids", "start_jobs needs 1 to 20 job ids")
            job_ids = [_as_int(job_id) for job_id in ids]
            owned = conn.execute(
                f"SELECT id FROM jobs WHERE created_by = ? AND origin_master_session_id = ? AND id IN ({','.join('?' for _ in job_ids)})",
                (user["id"], origin_master_session_id, *job_ids),
            ).fetchall()
            if {row["id"] for row in owned} != set(job_ids):
                raise MasterToolError("job_not_found", "One or more Master jobs were not found")
            jobs: list[dict[str, Any]] = []
            start_errors: list[dict[str, Any]] = []
            for job_id in job_ids:
                try:
                    jobs.append(start_master_job(conn, app, user, job_id))
                except MasterToolError as exc:
                    jobs.append(_job_payload(conn, job_id))
                    start_errors.append({"job_id": job_id, "code": exc.code, "message": str(exc)})
            data = {
                "jobs": [{"id": job["id"], "title": job["title"], "status": job["status"]} for job in jobs],
                "capacity": master_capacity(conn, origin_master_session_id),
            }
            if start_errors:
                data["start_errors"] = start_errors
                return {
                    "ok": False, "tool": name, "result": data,
                    "error": {"code": "job_start_failed", "message": "Some jobs remained queued; inspect the returned job cards."},
                }
        elif name == "start_plan":
            job = create_master_plan(conn, app, user, origin_master_session_id, args)
            data = {
                "job": {"id": job["id"], "title": job["title"], "status": job["status"], "engine": job.get("engine")},
                "capacity": master_capacity(conn, origin_master_session_id),
            }
            if job.get("_start_error"):
                return {
                    "ok": False, "tool": name, "result": data,
                    "error": {"code": "plan_start_failed", "message": str(job["_start_error"])},
                }
        elif name == "set_unattended":
            enabled = args.get("enabled")
            if not isinstance(enabled, bool):
                raise MasterToolError("invalid_boolean", "enabled must be true or false")
            app_settings.set_master_settings(conn, unattended=enabled)
            data = {"unattended": enabled}
            conn.execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                "VALUES (?, 'master.settings.change', 'settings', 'master.unattended', ?)",
                (user["id"], json.dumps({"enabled": enabled})),
            )
        elif name == "set_budgets":
            tokens: int | None | object = ...
            if "tokens" in args:
                tokens = None if args["tokens"] is None else _as_int(args["tokens"])
            data = app_settings.set_master_settings(
                conn,
                budget_turns=_as_int(args.get("turns")),
                budget_wall_seconds=_as_int(args.get("wall_seconds")),
                budget_tokens=tokens,
            )
            conn.execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                "VALUES (?, 'master.settings.change', 'settings', 'master.budgets', ?)",
                (user["id"], json.dumps({"turns": data["budget_turns"], "wall_seconds": data["budget_wall_seconds"], "tokens": data["budget_tokens"]})),
            )
        elif name == "create_attention":
            title = str(args.get("title") or "").strip()
            message = str(args.get("message") or "").strip()
            if not title or not message:
                raise MasterToolError("invalid_attention", "Attention needs a title and message")
            cur = conn.execute(
                "INSERT INTO attention_items(kind, title, target_json, inline_ok, status, source_key) "
                "VALUES ('master_decision', ?, ?, 0, 'open', ?)",
                (title[:200], json.dumps({"view": "master", "message": message}), f"master:{origin_master_session_id}:{iso_now()}"),
            )
            attention_id = _as_int(cur.lastrowid)
            projection = getattr(app.state, "master_projection", None)
            if projection is not None:
                projection.safe_project_attention(attention_id)
            data = {"attention_id": attention_id}
        else:
            raise MasterToolError("tool_not_allowed", f"Master tool {name!r} is not allowed")
        return {"ok": True, "tool": name, "result": data}
    except MasterToolError as exc:
        return {"ok": False, "tool": name, "error": {"code": exc.code, "message": str(exc)}}
    except HTTPException as exc:
        detail = exc.detail
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        return {"ok": False, "tool": name, "error": {"code": "product_request_failed", "message": message}}
    except (sqlite3.Error, ValueError, TypeError) as exc:
        return {"ok": False, "tool": name, "error": {"code": "tool_failed", "message": str(exc)}}


def execute_tool(
    conn,
    app,
    user: dict[str, Any],
    origin_master_session_id: int,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility function for pre-broker server callers and tests.

    The model loop does not call this function. It constructs MasterToolBroker
    directly, so these Alpha-era names are not part of Master's authority.
    """
    if name in {
        "list_projects",
        "list_jobs",
        "list_worker_agents",
        "list_plans",
        "get_master_settings",
        "get_alpha_settings",
        "capacity",
        "dispatch_jobs",
        "start_jobs",
        "start_plan",
        "set_unattended",
        "set_budgets",
        "create_attention",
    }:
        return _execute_legacy_tool(
            conn,
            app,
            user,
            origin_master_session_id,
            name,
            args,
        )
    return MasterToolBroker(
        conn,
        app,
        user,
        origin_master_session_id,
    ).execute(name, args)


@dataclass(frozen=True)
class ParsedMasterToolCall:
    name: str
    arguments: dict[str, Any]


class MasterToolEnvelopeParser:
    """Incremental parser for tool tags that may split across stream chunks."""

    OPEN = "<proxima-tool>"
    CLOSE = "</proxima-tool>"

    def __init__(self, *, request_bytes: int = MASTER_MAX_TOOL_REQUEST_BYTES):
        self.request_bytes = request_bytes
        self._buffer = ""
        self._inside = False
        self._depth = 0
        self._payload: list[str] = []
        self._payload_bytes = 0
        self._invalid: str | None = None
        self.calls: list[ParsedMasterToolCall] = []
        self.errors: list[dict[str, Any]] = []

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": None,
            "error": {"code": code, "message": message},
        }

    def feed(self, chunk: str) -> None:
        self._buffer += chunk
        while self._buffer:
            open_at = self._buffer.find(self.OPEN)
            close_at = self._buffer.find(self.CLOSE)
            positions = [
                (position, token)
                for position, token in (
                    (open_at, self.OPEN),
                    (close_at, self.CLOSE),
                )
                if position >= 0
            ]
            if not positions:
                keep = max(len(self.OPEN), len(self.CLOSE)) - 1
                if self._inside and len(self._buffer) > keep:
                    self._append_payload(self._buffer[:-keep])
                    self._buffer = self._buffer[-keep:]
                elif not self._inside and len(self._buffer) > keep:
                    self._buffer = self._buffer[-keep:]
                return
            position, token = min(positions, key=lambda item: item[0])
            prefix = self._buffer[:position]
            self._buffer = self._buffer[position + len(token):]
            if self._inside:
                self._append_payload(prefix)
            if token == self.OPEN:
                if not self._inside:
                    self._inside = True
                    self._depth = 1
                    self._payload = []
                    self._payload_bytes = 0
                    self._invalid = None
                else:
                    self._depth += 1
                    if self._invalid is None:
                        self._invalid = "nested Master tool envelope"
                continue
            if not self._inside:
                self.errors.append(
                    self._error(
                        "malformed_tool_call",
                        "unexpected Master tool closing tag",
                    )
                )
                continue
            self._depth -= 1
            if self._depth == 0:
                self._finish_envelope()

    def finish(self) -> tuple[list[ParsedMasterToolCall], list[dict[str, Any]]]:
        if self._inside:
            self.errors.append(
                self._error(
                    "malformed_tool_call",
                    self._invalid or "unterminated Master tool envelope",
                )
            )
            self._inside = False
            self._buffer = ""
        elif any(
            token.startswith(self._buffer[-length:])
            for token in (self.OPEN, self.CLOSE)
            for length in range(1, min(len(token), len(self._buffer)) + 1)
            if self._buffer[-length:].startswith("<")
        ):
            self.errors.append(
                self._error(
                    "malformed_tool_call",
                    "partial Master tool envelope marker",
                )
            )
        return self.calls, self.errors

    def _append_payload(self, text: str) -> None:
        if self._depth != 1 or self._invalid is not None:
            return
        self._payload_bytes += len(text.encode("utf-8"))
        if self._payload_bytes > self.request_bytes:
            self._invalid = (
                f"Master tool request exceeds the {self.request_bytes}-byte limit"
            )
            return
        self._payload.append(text)

    def _finish_envelope(self) -> None:
        self._inside = False
        if self._invalid is not None:
            code = (
                "tool_request_too_large"
                if "exceeds" in self._invalid
                else "malformed_tool_call"
            )
            self.errors.append(self._error(code, self._invalid))
            return
        raw = "".join(self._payload).strip()
        try:
            call = json.loads(raw)
        except json.JSONDecodeError:
            self.errors.append(
                self._error(
                    "malformed_tool_call",
                    "Master tool envelope must contain one JSON object",
                )
            )
            return
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            self.errors.append(
                self._error(
                    "malformed_tool_call",
                    "Master tool envelope needs a string name",
                )
            )
            return
        arguments = call.get("arguments", {})
        if not isinstance(arguments, dict):
            self.errors.append(
                self._error(
                    "malformed_tool_call",
                    "Master tool arguments must be an object",
                )
            )
            return
        self.calls.append(
            ParsedMasterToolCall(call["name"], arguments)
        )


def parse_master_tool_envelopes(
    chunks: Iterable[str],
) -> tuple[list[ParsedMasterToolCall], list[dict[str, Any]]]:
    parser = MasterToolEnvelopeParser()
    for chunk in chunks:
        parser.feed(chunk)
    return parser.finish()


def _tool_round(kind: Any) -> int:
    if kind == "master":
        return 0
    match = re.fullmatch(r"master_tool_(\d+)", str(kind or ""))
    return int(match.group(1)) if match else MASTER_MAX_TOOL_ROUNDS


def _turn_root_run_id(conn, run: dict[str, Any]) -> int:
    current = _as_int(run["id"])
    parent = run.get("continued_from_run_id")
    visited = {current}
    while parent is not None:
        parent_id = _as_int(parent)
        if parent_id in visited:
            raise MasterToolError(
                "turn_chain_invalid", "Master turn continuation chain contains a cycle"
            )
        visited.add(parent_id)
        current = parent_id
        row = conn.execute(
            "SELECT continued_from_run_id FROM runs WHERE id = ?", (parent_id,)
        ).fetchone()
        parent = row["continued_from_run_id"] if row else None
    return current


def _master_tool_digest(name: str, arguments: Any) -> tuple[str, int]:
    canonical = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    encoded = canonical.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def execute_master_tool_call(
    app,
    conn,
    run: dict[str, Any],
    name: str,
    arguments: Any,
) -> dict[str, Any]:
    """Execute one durable root-turn call through the typed broker."""
    session = conn.execute(
        "SELECT s.mode, s.owner_user_id FROM sessions s WHERE s.id = ?",
        (run["session_id"],),
    ).fetchone()
    if not session or session["mode"] != "master":
        return {
            "ok": False,
            "tool": name or None,
            "error": {
                "code": "master_session_required",
                "message": "Master product tools require a Master session",
            },
        }
    try:
        digest, request_size = _master_tool_digest(name, arguments)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "tool": name or None,
            "error": {
                "code": "invalid_tool_arguments",
                "message": "Master tool arguments must be JSON-compatible",
            },
        }
    if request_size > MASTER_MAX_TOOL_REQUEST_BYTES:
        return {
            "ok": False,
            "tool": name or None,
            "error": {
                "code": "tool_request_too_large",
                "message": (
                    "Master tool request exceeds the "
                    f"{MASTER_MAX_TOOL_REQUEST_BYTES}-byte limit"
                ),
            },
        }
    turn_root_run_id = _turn_root_run_id(conn, run)
    origin_message = conn.execute(
        "SELECT id FROM messages WHERE run_id = ? AND role = 'user' "
        "ORDER BY id DESC LIMIT 1",
        (turn_root_run_id,),
    ).fetchone()
    ledger = conn.execute(
        "SELECT status FROM master_tool_calls "
        "WHERE turn_root_run_id = ? AND envelope_hash = ?",
        (turn_root_run_id, digest),
    ).fetchone()
    if ledger is not None:
        return {
            "ok": False,
            "tool": name or None,
            "error": {
                "code": "duplicate_tool_call",
                "message": "Duplicate Master tool call was not executed",
            },
        }
    conn.execute(
        "INSERT OR IGNORE INTO master_tool_calls("
        "master_session_id, turn_root_run_id, envelope_hash, tool_name"
        ") VALUES (?, ?, ?, ?)",
        (run["session_id"], turn_root_run_id, digest, name),
    )
    args = dict(arguments) if isinstance(arguments, dict) else arguments
    if (
        isinstance(args, dict)
        and name in {"delegate_tasks", "create_attention"}
        and not args.get("idempotency_key")
    ):
        args["idempotency_key"] = f"master-turn:{turn_root_run_id}:{digest}"
    result = MasterToolBroker(
        conn,
        app,
        {"id": session["owner_user_id"]},
        run["session_id"],
        origin_message_id=(
            _as_int(origin_message["id"]) if origin_message is not None else None
        ),
    ).execute(name, args)
    conn.execute(
        "UPDATE master_tool_calls SET status = 'complete', result_json = ?, "
        "completed_at = CURRENT_TIMESTAMP WHERE turn_root_run_id = ? "
        "AND envelope_hash = ?",
        (
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            turn_root_run_id,
            digest,
        ),
    )
    return result


def handle_master_response(app, conn, run: dict[str, Any], answer: str) -> list[dict[str, Any]]:
    session = conn.execute(
        "SELECT s.mode, s.owner_user_id FROM sessions s WHERE s.id = ?", (run["session_id"],)
    ).fetchone()
    if not session or session["mode"] != "master":
        return []
    parsed, calls = parse_master_tool_envelopes([answer])
    round_number = _tool_round(run.get("kind"))
    if calls:
        parsed = []
    elif parsed:
        validation_errors = [
            error
            for call in parsed
            if (
                error := validate_master_tool_call(
                    call.name,
                    call.arguments,
                )
            )
            is not None
        ]
        digests = [
            _master_tool_digest(call.name, call.arguments)[0]
            for call in parsed
        ]
        duplicates = {
            digest
            for digest in digests
            if digests.count(digest) > 1
        }
        turn_root_run_id = _turn_root_run_id(conn, run)
        replayed = {
            row["envelope_hash"]
            for row in conn.execute(
                "SELECT envelope_hash FROM master_tool_calls "
                "WHERE turn_root_run_id = ?",
                (turn_root_run_id,),
            ).fetchall()
        }.intersection(digests)
        if validation_errors:
            parsed = []
            calls.extend(validation_errors)
        elif duplicates or replayed:
            parsed = []
            calls.append(
                {
                    "ok": False,
                    "tool": None,
                    "error": {
                        "code": "duplicate_tool_call",
                        "message": (
                            "Duplicate Master tool call was not executed"
                        ),
                    },
                }
            )
    if parsed and round_number >= MASTER_MAX_TOOL_ROUNDS:
        parsed = []
        calls.append(
            {
                "ok": False,
                "tool": None,
                "error": {
                    "code": "tool_round_limit",
                    "message": (
                        "Master reached the "
                        f"{MASTER_MAX_TOOL_ROUNDS}-round product-tool limit"
                    ),
                },
            }
        )
    elif len(parsed) > MASTER_MAX_CALLS_PER_ROUND:
        parsed = []
        calls.append(
            {
                "ok": False,
                "tool": None,
                "error": {
                    "code": "too_many_tool_calls",
                    "message": (
                        "Master emitted more than "
                        f"{MASTER_MAX_CALLS_PER_ROUND} tool calls in one round"
                    ),
                },
            }
        )
    result_bytes = len(
        json.dumps(calls, ensure_ascii=False, separators=(",", ":")).encode()
    )
    for call in parsed:
        result = execute_master_tool_call(
            app,
            conn,
            run,
            call.name,
            call.arguments,
        )
        encoded = json.dumps(
            result, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if result_bytes + len(encoded) > MASTER_MAX_ROUND_RESULT_BYTES:
            calls.append(
                {
                    "ok": False,
                    "tool": call.name,
                    "error": {
                        "code": "round_result_too_large",
                        "message": (
                            "Master tool results exceed the "
                            f"{MASTER_MAX_ROUND_RESULT_BYTES}-byte round limit"
                        ),
                    },
                }
            )
            break
        calls.append(result)
        result_bytes += len(encoded)
    if calls:
        result_json = json.dumps(calls, ensure_ascii=False, indent=2)
        conn.execute("SAVEPOINT master_tool_result")
        try:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'system', ?, 'Proxima', ?)",
                (run["session_id"], "Master tool results:\n```json\n" + result_json + "\n```", run["id"]),
            )
            conn.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (run["session_id"],))
            next_run_id = None
            if round_number < MASTER_MAX_TOOL_ROUNDS:
                prompt = (
                    "Proxima executed your in-process product tools. Here are the trusted results:\n"
                    f"<proxima-results>\n{result_json}\n</proxima-results>\n"
                    "Continue the owner's request using these results. Do not repeat a successful mutation. "
                    "Call another product tool only when needed; otherwise report the outcome plainly."
                )
                cur = conn.execute(
                    "INSERT INTO runs(session_id, project_id, user_id, profile_id, "
                    "runner_id, kind, status, prompt, model, hermes_home, "
                    "continued_from_run_id, continuation_count) "
                    "VALUES (?, NULL, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                    (
                        run["session_id"], run["user_id"], run["profile_id"], run["runner_id"],
                        f"master_tool_{round_number + 1}", prompt, run.get("model"),
                        run.get("hermes_home"), run["id"],
                        _as_int(run.get("continuation_count") or 0) + 1,
                    ),
                )
                next_run_id = _as_int(cur.lastrowid)
            conn.execute("RELEASE SAVEPOINT master_tool_result")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT master_tool_result")
            conn.execute("RELEASE SAVEPOINT master_tool_result")
            raise
        if next_run_id is not None:
            app.state.worker.add_event(
                next_run_id, run["session_id"], None, "run.queued",
                {"runner": run["runner_id"], "master": True, "tool_round": round_number + 1},
            )
    return calls

"""Schema-validated, filesystem-isolated product tools available to Master.

This module is the complete authority surface exposed to a Master model. It
accepts IDs and bounded product text, resolves those IDs inside trusted Proxima
code, and returns small product records. It never accepts filesystem paths or
returns absolute host paths, credentials, runner homes, configuration, or
arbitrary MCP/native tools. Graph citations are validated scope-relative
references.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from fastapi import HTTPException
from jsonschema import Draft202012Validator

from . import container_registry
from .auth import iso_now
from .task_delegation import (
    DependencyRequest,
    TaskDelegationError,
    TaskDelegationRequest,
)

log = logging.getLogger("proxima.master.tools")

MASTER_TOOL_RESULT_BYTES = 32 * 1024
MASTER_TOOL_TEXT_CHARS = 8_000
MASTER_TOOL_LIST_LIMIT = 100

_ID = {"type": "integer", "minimum": 1}
_LIMIT = {
    "type": "integer",
    "minimum": 1,
    "maximum": MASTER_TOOL_LIST_LIMIT,
    "default": 50,
}
_EMPTY_OBJECT = {"type": "object", "additionalProperties": False}
_STATUS = {
    "type": "string",
    "enum": ["queued", "running", "review", "done", "failed", "cancelled"],
}
_EXECUTION_POLICY = {"type": "string", "enum": ["guarded", "autonomous"]}
_PATH_TEXT = re.compile(
    r"""(?<![A-Za-z0-9])(?:/[^\s"'<>]+|[A-Za-z]:[/\\][^\s"'<>]+|"""
    r"""\\\\[^\s"'<>]+|(?:\.\.?[/\\]|~[/\\]|file://)[^\s"'<>]*)"""
)
_SECRET_TEXT = re.compile(
    r"""(?i)(?:\bbearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----|"""
    r"""\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,})"""
)


class MasterToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolDefinition:
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_DEPENDENCY_SCHEMA = _object(
    {
        "task": {"oneOf": [_ID, {"type": "string", "minLength": 1, "maxLength": 80}]},
        "required_status": {"type": "string", "enum": ["review", "done"]},
    },
    required=("task",),
)
_TASK_SCHEMA = _object(
    {
        "key": {"type": "string", "minLength": 1, "maxLength": 80},
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "brief": {"type": "string", "minLength": 1, "maxLength": MASTER_TOOL_TEXT_CHARS},
        "container_id": _ID,
        "area_id": _ID,
        "profile_id": _ID,
        "execution_policy": _EXECUTION_POLICY,
        "recipe_id": _ID,
        "depends_on": {
            "type": "array",
            "maxItems": 20,
            "items": {"oneOf": [_ID, {"type": "string", "minLength": 1, "maxLength": 80}, _DEPENDENCY_SCHEMA]},
        },
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    required=("title", "brief", "container_id", "area_id", "profile_id"),
)

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_containers": _object({"limit": _LIMIT}),
    "get_container": _object({"container_id": _ID}, required=("container_id",)),
    "get_live_state": _object({"container_id": _ID, "limit": _LIMIT}),
    "list_tasks": _object(
        {"container_id": _ID, "status": _STATUS, "limit": _LIMIT}
    ),
    "list_task_agents": _EMPTY_OBJECT,
    "list_recipes": _object({"container_id": _ID, "limit": _LIMIT}),
    "query_context": _object(
        {
            "container_id": _ID,
            "area_id": _ID,
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4_000,
            },
        },
        required=("query",),
    ),
    "delegate_tasks": _object(
        {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": _TASK_SCHEMA,
            },
            "start": {"type": "boolean"},
            "idempotency_key": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
            },
        },
        required=("tasks",),
    ),
    "start_tasks": _object(
        {
            "task_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "uniqueItems": True,
                "items": _ID,
            }
        },
        required=("task_ids",),
    ),
    "create_attention": _object(
        {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "message": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4_000,
            },
            "idempotency_key": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
            },
        },
        required=("title", "message"),
    ),
}

_TOOL_DESCRIPTIONS = {
    "list_containers": "List owner-visible Containers without filesystem paths.",
    "get_container": "Get one Container and its safe registered Area IDs.",
    "get_live_state": "Get bounded live Task state for all or one Container.",
    "list_tasks": "List bounded owner-visible Tasks.",
    "list_task_agents": "List Task-agent profiles available for delegation.",
    "list_recipes": "List active Recipes available to the owner.",
    "query_context": (
        "Route a question to Fleet, Live state, one Knowledge graph, "
        "and/or one Code graph with provenance and budgets."
    ),
    "delegate_tasks": "Atomically create an idempotent Task batch or dependency DAG.",
    "start_tasks": "Start existing Master-owned Tasks through the delegation service.",
    "create_attention": "Create one idempotent owner Attention item.",
}

_MASTER_DYNAMIC_TOOLS_JSON = json.dumps(
    [
        {
            "type": "function",
            "name": name,
            "description": _TOOL_DESCRIPTIONS[name],
            "inputSchema": schema,
        }
        for name, schema in TOOL_SCHEMAS.items()
    ],
    ensure_ascii=False,
    separators=(",", ":"),
)


def master_dynamic_tools() -> list[dict[str, Any]]:
    """Render the broker contract as Codex app-server dynamic tools."""
    return json.loads(_MASTER_DYNAMIC_TOOLS_JSON)


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise MasterToolError("invalid_integer", "expected an integer")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MasterToolError("invalid_integer", "expected an integer") from exc


def _validation_error(name: str, arguments: Any) -> MasterToolError | None:
    if name not in TOOL_SCHEMAS:
        return MasterToolError(
            "tool_not_allowed", f"Master tool {name!r} is not allowed"
        )
    errors = sorted(
        Draft202012Validator(TOOL_SCHEMAS[name]).iter_errors(arguments),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if not errors:
        unsafe = _unsafe_text(arguments)
        if unsafe is not None:
            return MasterToolError(
                "unsafe_tool_text",
                f"{name} input contains {unsafe}",
            )
        return None
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "arguments"
    return MasterToolError(
        "invalid_tool_arguments",
        f"{name}.{location}: {error.message}",
    )


def validate_master_tool_call(
    name: str,
    arguments: Any,
) -> dict[str, Any] | None:
    """Return a stable validation error without executing a product handler."""
    error = _validation_error(name, arguments)
    if error is None:
        return None
    return {
        "ok": False,
        "tool": name or None,
        "error": {"code": error.code, "message": str(error)},
    }


def _unsafe_text(value: Any) -> str | None:
    if isinstance(value, str):
        if _PATH_TEXT.search(value):
            return "a filesystem path"
        if _SECRET_TEXT.search(value):
            return "credential-like material"
        return None
    if isinstance(value, dict):
        for nested in value.values():
            unsafe = _unsafe_text(nested)
            if unsafe is not None:
                return unsafe
    elif isinstance(value, list):
        for nested in value:
            unsafe = _unsafe_text(nested)
            if unsafe is not None:
                return unsafe
    return None


def _safe_container(container: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _as_int(container["id"]),
        "slug": str(container["slug"]),
        "name": str(container["name"]),
        "identity": container.get("identity_label"),
        "summary": container.get("summary"),
        "last_activity_at": container.get("last_activity_at"),
        "live": dict(container.get("live") or {}),
        "areas": dict(container.get("area_inventory") or {}),
        "health": dict(container.get("health") or {}),
    }


def _safe_task(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _as_int(row["id"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "container_id": (
            _as_int(row["project_id"]) if row.get("project_id") is not None else None
        ),
        "area_id": (
            _as_int(row["target_area_id"])
            if row.get("target_area_id") is not None
            else None
        ),
        "blocked_reason": row.get("blocked_reason"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class MasterToolBroker:
    """Typed server-owned product API for one Master session and owner."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        app: Any,
        user: Mapping[str, Any],
        origin_master_session_id: int,
        *,
        origin_message_id: int | None = None,
        result_bytes: int = MASTER_TOOL_RESULT_BYTES,
    ):
        self.conn = conn
        self.app = app
        self.user = dict(user)
        self.user_id = _as_int(user["id"])
        self.origin_master_session_id = _as_int(origin_master_session_id)
        self.origin_message_id = (
            _as_int(origin_message_id) if origin_message_id is not None else None
        )
        self.message_context = self._load_message_context()
        self.result_bytes = result_bytes
        self._definitions = {
            "list_containers": ToolDefinition(
                TOOL_SCHEMAS["list_containers"], self._list_containers
            ),
            "get_container": ToolDefinition(
                TOOL_SCHEMAS["get_container"], self._get_container
            ),
            "get_live_state": ToolDefinition(
                TOOL_SCHEMAS["get_live_state"], self._get_live_state
            ),
            "list_tasks": ToolDefinition(
                TOOL_SCHEMAS["list_tasks"], self._list_tasks
            ),
            "list_task_agents": ToolDefinition(
                TOOL_SCHEMAS["list_task_agents"], self._list_task_agents
            ),
            "list_recipes": ToolDefinition(
                TOOL_SCHEMAS["list_recipes"], self._list_recipes
            ),
            "query_context": ToolDefinition(
                TOOL_SCHEMAS["query_context"], self._query_context
            ),
            "delegate_tasks": ToolDefinition(
                TOOL_SCHEMAS["delegate_tasks"], self._delegate_tasks
            ),
            "start_tasks": ToolDefinition(
                TOOL_SCHEMAS["start_tasks"], self._start_tasks
            ),
            "create_attention": ToolDefinition(
                TOOL_SCHEMAS["create_attention"], self._create_attention
            ),
        }

    def _load_message_context(self) -> dict[str, Any] | None:
        if self.origin_message_id is None:
            return None
        row = self.conn.execute(
            "SELECT focus_mode, focus_container_id, target_mode, "
            "target_container_id, target_area_id "
            "FROM master_message_context WHERE message_id = ?",
            (self.origin_message_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def execute(self, name: str, arguments: Any) -> dict[str, Any]:
        error = _validation_error(name, arguments)
        if error:
            return self._error(name, error.code, str(error))
        try:
            data = self._definitions[name].handler(dict(arguments))
            unsafe = _unsafe_text(data)
            if unsafe is not None:
                return self._error(
                    name,
                    "unsafe_tool_result",
                    f"{name} result contained {unsafe}",
                )
            result = {"ok": True, "tool": name, "result": data}
            encoded = json.dumps(
                result, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded) > self.result_bytes:
                return self._error(
                    name,
                    "tool_result_too_large",
                    f"{name} result exceeds the {self.result_bytes}-byte limit",
                )
            return result
        except MasterToolError as exc:
            return self._error(name, exc.code, str(exc))
        except TaskDelegationError as exc:
            return self._error(name, exc.code, str(exc))
        except HTTPException as exc:
            detail = exc.detail
            message = (
                detail.get("message")
                if isinstance(detail, dict)
                else "product request failed"
            )
            return self._error(name, "product_request_failed", str(message))
        except Exception as exc:
            log.error(
                "Master product tool failed: %s (%s)",
                name,
                type(exc).__name__,
            )
            return self._error(
                name,
                "tool_failed",
                f"{name} failed inside Proxima",
            )

    @staticmethod
    def _error(name: str | None, code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": name,
            "error": {"code": code, "message": message},
        }

    def _owned_container(self, container_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT slug FROM projects WHERE id = ? AND owner_user_id = ? "
            "AND archived_at IS NULL",
            (container_id, self.user_id),
        ).fetchone()
        if row is None:
            raise MasterToolError(
                "container_not_found", f"Container {container_id} was not found"
            )
        container = container_registry.get_fleet_container(
            self.conn, self.user_id, str(row["slug"])
        )
        if container is None:
            raise MasterToolError(
                "container_not_found", f"Container {container_id} was not found"
            )
        return container

    def _list_containers(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _as_int(args.get("limit", 50))
        rows = container_registry.list_fleet_containers(self.conn, self.user_id)
        return {"containers": [_safe_container(row) for row in rows[:limit]]}

    def _get_container(self, args: dict[str, Any]) -> dict[str, Any]:
        container_id = _as_int(args["container_id"])
        container = _safe_container(self._owned_container(container_id))
        try:
            validated_area_ids = set(
                container_registry.validated_area_roots(
                    self.conn, container_id
                )
            )
        except (container_registry.ContainerBoundaryError, OSError):
            raise MasterToolError(
                "container_boundary_invalid",
                f"Container {container_id} does not have safe registered Areas",
            ) from None
        rows = self.conn.execute(
            "SELECT id, kind FROM project_areas WHERE project_id = ? "
            "AND source != 'excluded' ORDER BY kind, id",
            (container_id,),
        ).fetchall()
        kind_counts: dict[str, int] = {}
        target_areas: list[dict[str, Any]] = []
        for row in rows:
            if _as_int(row["id"]) not in validated_area_ids:
                continue
            kind = str(row["kind"])
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            target_areas.append(
                {
                    "id": _as_int(row["id"]),
                    "kind": kind,
                    "label": (
                        "Ops"
                        if kind == "ops"
                        else f"Code Area {kind_counts[kind]}"
                    ),
                }
            )
        container["target_areas"] = target_areas
        return {
            "container": container
        }

    def _get_live_state(self, args: dict[str, Any]) -> dict[str, Any]:
        container_id = args.get("container_id")
        if container_id is not None:
            self._owned_container(_as_int(container_id))
        limit = _as_int(args.get("limit", 50))
        where = ["p.owner_user_id = ?"]
        params: list[Any] = [self.user_id]
        if container_id is not None:
            where.append("j.project_id = ?")
            params.append(_as_int(container_id))
        params.append(limit)
        rows = self.conn.execute(
            "SELECT j.id, j.title, j.status, j.project_id, j.target_area_id, "
            "j.blocked_reason, j.created_at, j.updated_at FROM jobs j "
            "LEFT JOIN projects p ON p.id = j.project_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'review' THEN 1 "
            "WHEN 'queued' THEN 2 ELSE 3 END, j.updated_at DESC, j.id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        counts = {
            status: sum(1 for row in rows if row["status"] == status)
            for status in ("queued", "running", "review", "failed")
        }
        return {
            "counts": counts,
            "tasks": [_safe_task(dict(row)) for row in rows],
        }

    def _list_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        container_id = args.get("container_id")
        if container_id is not None:
            self._owned_container(_as_int(container_id))
        where = ["p.owner_user_id = ?"]
        params: list[Any] = [self.user_id]
        if container_id is not None:
            where.append("j.project_id = ?")
            params.append(_as_int(container_id))
        if args.get("status") is not None:
            where.append("j.status = ?")
            params.append(str(args["status"]))
        params.append(_as_int(args.get("limit", 50)))
        rows = self.conn.execute(
            "SELECT j.id, j.title, j.status, j.project_id, j.target_area_id, "
            "j.blocked_reason, j.created_at, j.updated_at FROM jobs j "
            "JOIN projects p ON p.id = j.project_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY j.updated_at DESC, j.id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return {"tasks": [_safe_task(dict(row)) for row in rows]}

    def _list_task_agents(self, _args: dict[str, Any]) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT id, name, runner_id, is_default FROM profiles "
            "WHERE user_id = ? AND COALESCE(system_kind, '') = '' "
            "ORDER BY is_default DESC, name, id",
            (self.user_id,),
        ).fetchall()
        return {"task_agents": [dict(row) for row in rows]}

    def _list_recipes(self, args: dict[str, Any]) -> dict[str, Any]:
        container_id = args.get("container_id")
        if container_id is not None:
            self._owned_container(_as_int(container_id))
        limit = _as_int(args.get("limit", 50))
        rows = self.conn.execute(
            "SELECT id, project_id AS container_id, name, description, category, "
            "CASE WHEN graph IS NULL THEN 'linear' ELSE 'graph' END AS engine "
            "FROM workflows WHERE status = 'active' "
            "AND ((project_id IS NULL AND created_by = ?) OR project_id IN "
            "(SELECT id FROM projects WHERE owner_user_id = ?)) "
            "AND (? IS NULL OR project_id = ?) "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (self.user_id, self.user_id, container_id, container_id, limit),
        ).fetchall()
        return {"recipes": [dict(row) for row in rows]}

    def _query_context(self, args: dict[str, Any]) -> dict[str, Any]:
        router = getattr(self.app.state, "context_router", None)
        if router is None:
            return {
                "available": False,
                "code": "feature_unavailable",
                "message": "Scoped graph context is not available in this release",
            }
        requested_container_id = (
            _as_int(args["container_id"])
            if args.get("container_id") is not None
            else None
        )
        requested_area_id = (
            _as_int(args["area_id"])
            if args.get("area_id") is not None
            else None
        )
        container_id = requested_container_id
        area_id = requested_area_id
        context = self.message_context
        durable_scope = False
        if context and context.get("target_mode") == "explicit":
            durable_scope = True
            raw_container = context.get("target_container_id")
            if raw_container is None:
                raise MasterToolError(
                    "context_scope_unavailable",
                    "The owner-selected context is no longer available",
                )
            container_id = _as_int(raw_container)
            area_id = (
                _as_int(context["target_area_id"])
                if context.get("target_area_id") is not None
                else None
            )
        elif context and context.get("focus_mode") == "container":
            durable_scope = True
            raw_container = context.get("focus_container_id")
            if raw_container is None:
                raise MasterToolError(
                    "context_scope_unavailable",
                    "The owner-selected context is no longer available",
                )
            container_id = _as_int(raw_container)
            area_id = None
        if durable_scope and (
            (
                requested_container_id is not None
                and requested_container_id != container_id
            )
            or (
                requested_area_id is not None
                and requested_area_id != area_id
            )
        ):
            raise MasterToolError(
                "context_scope_conflict",
                "query_context scope conflicts with owner-selected context",
            )
        if container_id is not None:
            self._owned_container(container_id)
        if area_id is not None:
            area = self.conn.execute(
                "SELECT id FROM project_areas "
                "WHERE id = ? AND project_id = ? AND source != 'excluded'",
                (area_id, container_id),
            ).fetchone()
            if area is None:
                raise MasterToolError(
                    "context_scope_invalid",
                    "Query Area is not in the selected Container",
                )
        return router.route(
            owner_user_id=self.user_id,
            query=str(args["query"]),
            container_id=container_id,
            area_id=area_id,
        )

    def _task_request(
        self,
        task: dict[str, Any],
        *,
        batch_key: str,
        index: int,
    ) -> TaskDelegationRequest:
        requested_container_id = _as_int(task["container_id"])
        requested_area_id = _as_int(task["area_id"])
        container_id = requested_container_id
        area_id = requested_area_id
        routing_mode = "auto"
        routing_reason = (
            "Master resolved the requested outcome to one registered "
            "Container Area"
        )
        context = self.message_context
        if context and context["target_mode"] == "explicit":
            container_id = _as_int(context["target_container_id"])
            if context["target_area_id"] is not None:
                area_id = _as_int(context["target_area_id"])
                routing_reason = "Owner explicitly selected the Container and Area"
            else:
                routing_reason = (
                    "Owner explicitly selected the Container; Master selected "
                    "one registered Area inside it"
                )
            routing_mode = "explicit"
        elif (
            context
            and context["target_mode"] == "auto"
            and context["focus_mode"] == "container"
        ):
            container_id = _as_int(context["focus_container_id"])
            routing_reason = (
                "Master auto-routed inside the owner-focused Container"
            )
        profile_id = _as_int(task["profile_id"])
        # TaskDelegationService performs the authoritative ownership, exact
        # Container/Area, profile, Recipe, and dependency validation.
        self._owned_container(container_id)
        area = self.conn.execute(
            "SELECT id FROM project_areas "
            "WHERE id = ? AND project_id = ? AND source != 'excluded'",
            (area_id, container_id),
        ).fetchone()
        if area is None:
            raise MasterToolError(
                "target_area_not_found",
                "Target Area is not in the selected Container",
            )
        dependencies: list[DependencyRequest] = []
        for raw in task.get("depends_on") or []:
            if isinstance(raw, dict):
                dependencies.append(
                    DependencyRequest(
                        raw["task"],
                        required_status=str(raw.get("required_status") or "done"),
                    )
                )
            else:
                dependencies.append(DependencyRequest(raw))
        client_key = str(task.get("key") or f"task-{index + 1}")
        brief = str(task["brief"]).strip()
        input_data = {
            "brief": brief,
            "task_kind": "agent",
            "execution_policy": str(
                task.get("execution_policy") or "guarded"
            ),
            "master_dispatched": True,
        }
        return TaskDelegationRequest(
            title=str(task["title"]).strip(),
            brief=brief,
            container_id=container_id,
            area_id=area_id,
            profile_id=profile_id,
            execution_policy=str(task.get("execution_policy") or "guarded"),
            recipe_id=(
                _as_int(task["recipe_id"])
                if task.get("recipe_id") is not None
                else None
            ),
            input_data=input_data,
            dependencies=tuple(dependencies),
            origin_session_id=self.origin_master_session_id,
            origin_message_id=self.origin_message_id,
            routing_mode=routing_mode,
            routing_reason=routing_reason,
            idempotency_key=str(
                task.get("idempotency_key")
                or f"{batch_key}:{client_key}"
            ),
            client_key=client_key,
        )

    def _delegate_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        batch_key = str(args.get("idempotency_key") or "").strip()
        if not batch_key:
            raise MasterToolError(
                "idempotency_key_required",
                "delegate_tasks requires a turn-derived idempotency key",
            )
        requests = [
            self._task_request(task, batch_key=batch_key, index=index)
            for index, task in enumerate(args["tasks"])
        ]
        start = bool(args.get("start", False))
        delegated = self.app.state.task_delegation.create_batch(
            self.user,
            requests,
            start=start,
            defer_start=start,
            connection=self.conn,
        )
        tasks = [
            {
                **_safe_task(result.job),
                "created": bool(result.created),
                "started": bool(result.started),
            }
            for result in delegated
        ]
        if start:
            started = self._start_task_ids(
                [_as_int(result.job["id"]) for result in delegated]
            )
            by_id = {task["id"]: task for task in started}
            tasks = [
                {
                    **task,
                    **{
                        key: value
                        for key, value in by_id[task["id"]].items()
                        if key not in {"id", "created"}
                    },
                }
                for task in tasks
            ]
        return {"tasks": tasks}

    def _start_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        for task_id in args["task_ids"]:
            row = self.conn.execute(
                "SELECT j.* FROM jobs j JOIN projects p ON p.id = j.project_id "
                "WHERE j.id = ? AND p.owner_user_id = ? "
                "AND j.origin_master_session_id = ?",
                (
                    _as_int(task_id),
                    self.user_id,
                    self.origin_master_session_id,
                ),
            ).fetchone()
            if row is None:
                raise MasterToolError(
                    "task_not_found", f"Task {task_id} was not found"
                )
        return {"tasks": self._start_task_ids(args["task_ids"])}

    def _start_task_ids(self, task_ids: list[int]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for task_id in task_ids:
            try:
                result = self.app.state.task_delegation.start(
                    _as_int(task_id), self.user, connection=self.conn
                )
                tasks.append(
                    {
                        **_safe_task(result.job),
                        "started": bool(result.started),
                    }
                )
            except TaskDelegationError as exc:
                tasks.append(
                    {
                        "id": _as_int(task_id),
                        "started": False,
                        "error": {"code": exc.code, "message": str(exc)},
                    }
                )
            except Exception:
                log.exception("Master could not start Task %s", task_id)
                tasks.append(
                    {
                        "id": _as_int(task_id),
                        "started": False,
                        "error": {
                            "code": "task_start_failed",
                            "message": f"Task {task_id} could not be started",
                        },
                    }
                )
        return tasks

    def _create_attention(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("idempotency_key") or "").strip()
        if not key:
            raise MasterToolError(
                "idempotency_key_required",
                "create_attention requires a turn-derived idempotency key",
            )
        source_key = f"master:{self.origin_master_session_id}:{key}"
        self.conn.execute(
            "INSERT OR IGNORE INTO attention_items("
            "kind, title, target_json, inline_ok, status, source_key"
            ") VALUES ('master_decision', ?, ?, 0, 'open', ?)",
            (
                str(args["title"]).strip(),
                json.dumps(
                    {
                        "view": "master",
                        "message": str(args["message"]).strip(),
                    },
                    ensure_ascii=False,
                ),
                source_key,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM attention_items WHERE source_key = ?", (source_key,)
        ).fetchone()
        if row is None:
            raise MasterToolError(
                "attention_failed", "Proxima could not create Attention"
            )
        projection = getattr(self.app.state, "master_projection", None)
        if projection is not None:
            projection.safe_project_attention(_as_int(row["id"]))
        return {"attention_id": _as_int(row["id"]), "created_at": iso_now()}

"""Server-owned Task creation, idempotency, dependency, and start boundary.

Every scoped Task created by Work, Home, or Master enters through
``TaskDelegationService``. The service commits the worker session, job,
delegation audit, and dependency edges before it attempts the retryable start.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from . import features, workflows as wf, worktrees
from .auth import iso_now
from .graph import GraphValidationError, normalize_graph, repo_target_paths
from .job_checkpoints import create_checkpoint
from .master_persistence import canonical_job_payload


REQUIRED_DEPENDENCY_STATUSES = frozenset({"review", "done"})
TERMINAL_FAILURE_STATUSES = frozenset({"failed", "cancelled"})
STATUS_RANK = {"queued": 0, "running": 1, "review": 2, "done": 3}
DEFAULT_MASTER_PARALLEL = 3


class TaskDelegationError(RuntimeError):
    """A stable product error returned by every delegation caller."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class DependencyRequest:
    task: int | str
    required_status: str = "done"


@dataclass(frozen=True)
class TaskDelegationRequest:
    title: str
    brief: str
    container_id: int
    area_id: int
    profile_id: int | None
    execution_policy: str
    idempotency_key: str
    recipe_id: int | None = None
    input_data: Mapping[str, Any] = field(default_factory=dict)
    dependencies: tuple[DependencyRequest, ...] = ()
    origin_session_id: int | None = None
    origin_message_id: int | None = None
    routing_mode: str = "explicit"
    routing_reason: str | None = None
    client_key: str = "task"


@dataclass(frozen=True)
class DelegatedTask:
    job: dict[str, Any]
    created: bool
    started: bool
    blocked_reason: str | None


def new_idempotency_key(prefix: str = "task") -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def completed_landing_status(
    conn: sqlite3.Connection, job: sqlite3.Row | Mapping[str, Any]
) -> str:
    """Return the final execution status without weakening repo review.

    Delegated Ops work already lands in place and therefore finishes directly.
    A repo Task always stops for diff review, regardless of its in-run
    permission policy. Historical unscoped jobs retain their previous guarded
    versus autonomous behavior. Explicit Recipe gates remain separate and are
    handled by the advancer before applying this landing status.
    """
    if worktrees.repo_area_for_job(conn, job) is not None:
        return "review"
    delegated = conn.execute(
        "SELECT 1 FROM task_delegations WHERE job_id = ?", (job["id"],)
    ).fetchone()
    if delegated:
        return "done"
    try:
        inputs = json.loads(job["input"] or "{}")
    except (TypeError, json.JSONDecodeError):
        inputs = {}
    autonomous = (
        isinstance(inputs, dict) and inputs.get("execution_policy") == "autonomous"
    )
    return "done" if autonomous else "review"


class TaskDelegationService:
    def __init__(self, app: Any, db_factory: Callable[[], sqlite3.Connection]):
        self.app = app
        self.db_factory = db_factory

    @staticmethod
    def _as_int(value: Any, field_name: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TaskDelegationError(
                f"invalid_{field_name}", f"{field_name} must be an integer"
            ) from exc
        if parsed <= 0:
            raise TaskDelegationError(
                f"invalid_{field_name}", f"{field_name} must be positive"
            )
        return parsed

    @staticmethod
    def _fingerprint(request: TaskDelegationRequest) -> str:
        payload = {
            "title": request.title.strip(),
            "brief": request.brief.strip(),
            "container_id": request.container_id,
            "area_id": request.area_id,
            "profile_id": request.profile_id,
            "execution_policy": request.execution_policy,
            "recipe_id": request.recipe_id,
            "input": dict(request.input_data),
            "dependencies": [
                {"task": dependency.task, "required_status": dependency.required_status}
                for dependency in request.dependencies
            ],
            "origin_session_id": request.origin_session_id,
            "origin_message_id": request.origin_message_id,
            "routing_mode": request.routing_mode,
            "routing_reason": request.routing_reason,
            "client_key": request.client_key,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _identity(user_id: int, key: str) -> str:
        return hashlib.sha256(f"{user_id}\0{key}".encode()).hexdigest()

    @staticmethod
    def _job_payload(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise TaskDelegationError(
                "task_not_found", f"Task {job_id} was not found", 404
            )
        payload = dict(row)
        for key, fallback in (("input", {}), ("steps_state", []), ("graph", None)):
            raw = payload.get(key)
            if raw is None:
                payload[key] = fallback
                continue
            try:
                payload[key] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                payload[key] = fallback
        delegation = conn.execute(
            "SELECT * FROM task_delegations WHERE job_id = ?", (job_id,)
        ).fetchone()
        if delegation:
            payload["delegation"] = dict(delegation)
        dependencies = conn.execute(
            "SELECT task_id, depends_on_task_id, required_status, created_at, updated_at "
            "FROM task_dependencies WHERE task_id = ? ORDER BY depends_on_task_id",
            (job_id,),
        ).fetchall()
        if dependencies:
            payload["dependencies"] = [dict(row) for row in dependencies]
        return canonical_job_payload(payload)

    def _validate_master_start_contract(
        self,
        conn: sqlite3.Connection,
        job: sqlite3.Row,
    ) -> None:
        """Fail closed when a Master Task lost any scoped ownership link."""
        row = conn.execute(
            "SELECT "
            "origin.mode AS origin_mode, "
            "origin.owner_user_id AS origin_owner_user_id, "
            "origin.project_id AS origin_project_id, "
            "container.owner_user_id AS container_owner_user_id, "
            "container.archived_at AS container_archived_at, "
            "area.project_id AS area_project_id, "
            "area.source AS area_source, "
            "worker.owner_user_id AS worker_owner_user_id, "
            "worker.project_id AS worker_project_id, "
            "worker.job_id AS worker_job_id, "
            "worker.mode AS worker_mode, "
            "profile.user_id AS profile_user_id, "
            "profile.system_kind AS profile_system_kind, "
            "delegation.origin_session_id AS delegation_origin_session_id, "
            "delegation.container_id AS delegation_container_id, "
            "delegation.target_area_id AS delegation_target_area_id, "
            "delegation.created_by AS delegation_created_by "
            "FROM jobs task "
            "LEFT JOIN sessions origin "
            "ON origin.id = task.origin_master_session_id "
            "LEFT JOIN projects container ON container.id = task.project_id "
            "LEFT JOIN project_areas area ON area.id = task.target_area_id "
            "LEFT JOIN sessions worker ON worker.id = task.session_id "
            "LEFT JOIN profiles profile ON profile.id = worker.profile_id "
            "LEFT JOIN task_delegations delegation "
            "ON delegation.job_id = task.id "
            "WHERE task.id = ?",
            (job["id"],),
        ).fetchone()
        if row is None:
            raise TaskDelegationError(
                "master_task_inconsistent",
                "Master Task ownership links are missing",
                409,
            )
        owner_id = int(job["created_by"] or 0)
        required = {
            "origin_mode": row["origin_mode"] == "master",
            "origin_owner": int(row["origin_owner_user_id"] or 0) == owner_id,
            "origin_unbound": row["origin_project_id"] is None,
            "container_owner": int(row["container_owner_user_id"] or 0)
            == owner_id,
            "container_active": row["container_archived_at"] is None,
            "area_container": int(row["area_project_id"] or 0)
            == int(job["project_id"] or 0),
            "area_active": row["area_source"] not in {None, "excluded"},
            "worker_owner": int(row["worker_owner_user_id"] or 0) == owner_id,
            "worker_container": int(row["worker_project_id"] or 0)
            == int(job["project_id"] or 0),
            "worker_task": int(row["worker_job_id"] or 0) == int(job["id"]),
            "worker_mode": row["worker_mode"] == "chat",
            "profile_owner": int(row["profile_user_id"] or 0) == owner_id,
            "profile_non_system": not str(row["profile_system_kind"] or ""),
        }
        failed = [name for name, valid in required.items() if not valid]
        if failed:
            raise TaskDelegationError(
                "master_task_inconsistent",
                "Master Task failed scoped ownership validation: "
                + ", ".join(failed),
                409,
            )
        if row["delegation_created_by"] is not None:
            delegation_valid = (
                int(row["delegation_created_by"]) == owner_id
                and int(row["delegation_container_id"] or 0)
                == int(job["project_id"])
                and int(row["delegation_target_area_id"] or 0)
                == int(job["target_area_id"])
                and int(row["delegation_origin_session_id"] or 0)
                == int(job["origin_master_session_id"])
            )
            if not delegation_valid:
                raise TaskDelegationError(
                    "master_task_inconsistent",
                    "Master Task delegation audit does not match its routing",
                    409,
                )

    def _master_parallel_limit(self) -> int:
        try:
            value = int(
                self.app.state.config.get(
                    "master_max_parallel",
                    DEFAULT_MASTER_PARALLEL,
                )
            )
        except (TypeError, ValueError, OverflowError):
            value = DEFAULT_MASTER_PARALLEL
        return max(1, min(64, value))

    @staticmethod
    def _master_active_slots(
        conn: sqlite3.Connection,
        origin_master_session_id: int,
    ) -> int:
        return int(
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
                ")",
                (origin_master_session_id, origin_master_session_id),
            ).fetchone()[0]
        )

    def _validate_request(
        self,
        conn: sqlite3.Connection,
        user: Mapping[str, Any],
        request: TaskDelegationRequest,
    ) -> dict[str, Any]:
        user_id = self._as_int(user.get("id"), "user_id")
        title = request.title.strip()
        brief = request.brief.strip()
        if not title or not brief:
            raise TaskDelegationError(
                "invalid_task", "Each Task needs a title and brief"
            )
        if len(title) > 200 or len(brief) > 50_000:
            raise TaskDelegationError(
                "task_too_large", "Task title or brief is too long"
            )
        if request.execution_policy not in {"guarded", "autonomous"}:
            raise TaskDelegationError(
                "invalid_execution_policy",
                "execution policy must be guarded or autonomous",
            )
        if request.routing_mode not in {"explicit", "auto"}:
            raise TaskDelegationError(
                "invalid_routing_mode", "routing mode must be explicit or auto"
            )
        if request.routing_mode == "auto" and not (
            request.routing_reason or ""
        ).strip():
            raise TaskDelegationError(
                "routing_reason_required", "auto routing needs an audit reason"
            )
        idempotency_key = request.idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 240:
            raise TaskDelegationError(
                "invalid_idempotency_key",
                "idempotency key must contain 1 to 240 characters",
            )
        if not request.client_key.strip() or len(request.client_key) > 120:
            raise TaskDelegationError(
                "invalid_client_key",
                "client Task key must contain 1 to 120 characters",
            )

        container_id = self._as_int(request.container_id, "container_id")
        area_id = self._as_int(request.area_id, "area_id")
        container = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND owner_user_id = ? "
            "AND archived_at IS NULL",
            (container_id, user_id),
        ).fetchone()
        if container is None:
            raise TaskDelegationError(
                "container_not_found", "Container was not found for this owner", 404
            )
        area = conn.execute(
            "SELECT * FROM project_areas WHERE id = ? AND project_id = ? "
            "AND source != 'excluded'",
            (area_id, container_id),
        ).fetchone()
        if area is None:
            raise TaskDelegationError(
                "area_not_in_container",
                "Area was not found in this Container",
            )
        profile_id = request.profile_id
        if profile_id is None:
            profile = conn.execute(
                "SELECT * FROM profiles WHERE user_id = ? AND is_default = 1 "
                "AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
                (user_id,),
            ).fetchone()
        else:
            profile = conn.execute(
                "SELECT * FROM profiles WHERE id = ? AND user_id = ? "
                "AND COALESCE(system_kind, '') = ''",
                (self._as_int(profile_id, "profile_id"), user_id),
            ).fetchone()
        if profile is None:
            raise TaskDelegationError(
                "task_agent_not_found", "Task-agent profile was not found", 404
            )

        origin_session = None
        if request.origin_session_id is not None:
            origin_session = conn.execute(
                "SELECT * FROM sessions WHERE id = ? AND owner_user_id = ?",
                (
                    self._as_int(request.origin_session_id, "origin_session_id"),
                    user_id,
                ),
            ).fetchone()
            if origin_session is None:
                raise TaskDelegationError(
                    "origin_session_not_found",
                    "Origin session was not found for this owner",
                    404,
                )
        if request.origin_message_id is not None:
            if origin_session is None:
                raise TaskDelegationError(
                    "origin_session_required",
                    "An origin message requires its origin session",
                )
            message = conn.execute(
                "SELECT id FROM messages WHERE id = ? AND session_id = ?",
                (
                    self._as_int(request.origin_message_id, "origin_message_id"),
                    origin_session["id"],
                ),
            ).fetchone()
            if message is None:
                raise TaskDelegationError(
                    "origin_message_not_found",
                    "Origin message was not found in the origin session",
                    404,
                )

        recipe = None
        engine = "linear"
        graph = None
        if request.recipe_id is not None:
            recipe = conn.execute(
                "SELECT * FROM workflows WHERE id = ? AND status = 'active' "
                "AND (created_by = ? OR project_id IN "
                "(SELECT id FROM projects WHERE owner_user_id = ?))",
                (self._as_int(request.recipe_id, "recipe_id"), user_id, user_id),
            ).fetchone()
            if recipe is None:
                raise TaskDelegationError(
                    "recipe_not_found", "Recipe was not found", 404
                )
            if recipe["project_id"] is not None and int(recipe["project_id"]) != container_id:
                raise TaskDelegationError(
                    "recipe_container_mismatch",
                    "Recipe belongs to a different Container",
                )
            if recipe["graph"] is not None:
                engine = "graph"
                try:
                    graph = normalize_graph(recipe["graph"])
                except GraphValidationError as exc:
                    raise TaskDelegationError(
                        "invalid_recipe", f"Recipe graph is invalid: {exc}"
                    ) from exc
                targets = repo_target_paths(graph)
                if area["kind"] == "ops" and targets:
                    raise TaskDelegationError(
                        "recipe_area_mismatch",
                        "A Recipe with repo work cannot target the Ops Area",
                    )
                if area["kind"] == "code" and targets and set(targets) != {
                    str(area["rel_path"])
                }:
                    raise TaskDelegationError(
                        "recipe_area_mismatch",
                        "Recipe names a different repo Area than this Task",
                    )

        if engine == "graph":
            steps_state: list[dict[str, Any]] = []
        elif recipe is not None:
            try:
                steps = json.loads(recipe["steps"] or "[]")
            except json.JSONDecodeError as exc:
                raise TaskDelegationError(
                    "invalid_recipe", "Recipe steps are invalid"
                ) from exc
            if not isinstance(steps, list) or not steps:
                raise TaskDelegationError(
                    "invalid_recipe", "Recipe has no runnable steps"
                )
            steps_state = [
                wf.step_state_from(step, dict(request.input_data)) for step in steps
            ]
        else:
            step = wf.normalize_steps(
                [{"name": "Task", "instruction": brief}]
            )[0]
            steps_state = [wf.step_state_from(step, dict(request.input_data))]

        input_data = dict(request.input_data)
        # Preserve the existing Work API's input projection. Missing policy has
        # always meant guarded, while autonomous must be explicit for the run
        # advancers. The brief remains the Task's own field and frozen step
        # instruction unless the caller also supplied it as Recipe input.
        if (
            request.execution_policy == "autonomous"
            or "execution_policy" in input_data
        ):
            input_data["execution_policy"] = request.execution_policy
        return {
            "user_id": user_id,
            "title": title,
            "brief": brief,
            "container": container,
            "area": area,
            "profile": profile,
            "origin_session": origin_session,
            "recipe": recipe,
            "engine": engine,
            "graph": graph,
            "steps_state": steps_state,
            "input_data": input_data,
            "idempotency_key": idempotency_key,
            "fingerprint": self._fingerprint(request),
        }

    def create_and_start(
        self,
        user: Mapping[str, Any],
        request: TaskDelegationRequest,
        *,
        start: bool = True,
        connection: sqlite3.Connection | None = None,
    ) -> DelegatedTask:
        return self.create_batch(
            user, [request], start=start, connection=connection
        )[0]

    def create_batch(
        self,
        user: Mapping[str, Any],
        requests: Iterable[TaskDelegationRequest],
        *,
        start: bool = True,
        defer_start: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[DelegatedTask]:
        """Create a Task DAG atomically, then optionally attempt its starts.

        ``defer_start`` persists the start intent in the creation transaction
        but leaves the immediate attempts to a compatibility caller that needs
        to report each start error independently. Restart recovery sees the
        same durable intent if that caller exits before making the attempts.
        """
        if defer_start and not start:
            raise TaskDelegationError(
                "invalid_start_mode",
                "A deferred start requires durable start intent",
            )
        batch = list(requests)
        if not batch or len(batch) > 100:
            raise TaskDelegationError(
                "invalid_batch", "A delegation batch needs 1 to 100 Tasks"
            )
        client_keys = [request.client_key.strip() for request in batch]
        if len(client_keys) != len(set(client_keys)):
            raise TaskDelegationError(
                "duplicate_client_key", "Client-local Task keys must be unique"
            )
        idempotency_keys = [request.idempotency_key.strip() for request in batch]
        if len(idempotency_keys) != len(set(idempotency_keys)):
            raise TaskDelegationError(
                "duplicate_idempotency_key",
                "Each Task in a batch needs a distinct idempotency key",
            )
        if any(not key or len(key) > 240 for key in idempotency_keys):
            raise TaskDelegationError(
                "invalid_idempotency_key",
                "idempotency key must contain 1 to 240 characters",
            )

        conn = connection or self.db_factory()
        user_id = self._as_int(user.get("id"), "user_id")
        created = False
        with self.app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_rows = [
                    conn.execute(
                        "SELECT * FROM task_delegations "
                        "WHERE idempotency_identity = ?",
                        (
                            self._identity(user_id, idempotency_key),
                        ),
                    ).fetchone()
                    for idempotency_key in idempotency_keys
                ]
                existing_count = sum(row is not None for row in existing_rows)
                if existing_count:
                    if existing_count != len(batch):
                        raise TaskDelegationError(
                            "partial_batch_replay",
                            "A repeated batch must replay all of its Task keys",
                            409,
                        )
                    for row, request in zip(existing_rows, batch, strict=True):
                        if row["request_fingerprint"] != self._fingerprint(request):
                            raise TaskDelegationError(
                                "idempotency_conflict",
                                "Idempotency key was already used for a different Task",
                                409,
                            )
                        if start and not row["start_requested"]:
                            conn.execute(
                                "UPDATE task_delegations SET start_requested = 1, "
                                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (row["id"],),
                            )
                    job_ids = [int(row["job_id"]) for row in existing_rows]
                    conn.execute("COMMIT")
                else:
                    validated = [
                        self._validate_request(conn, user, request)
                        for request in batch
                    ]
                    created = True
                    job_ids = []
                    key_to_job: dict[str, int] = {}
                    for request, item in zip(batch, validated, strict=True):
                        visibility = "project"
                        session_cur = conn.execute(
                            "INSERT INTO sessions("
                            "title, project_id, owner_user_id, profile_id, runner_id, "
                            "visibility, mode) VALUES (?, ?, ?, ?, ?, ?, 'chat')",
                            (
                                item["title"][:80],
                                item["container"]["id"],
                                user_id,
                                item["profile"]["id"],
                                item["profile"]["runner_id"],
                                visibility,
                            ),
                        )
                        session_id = int(session_cur.lastrowid)
                        job_cur = conn.execute(
                            "INSERT INTO jobs("
                            "project_id, workflow_id, session_id, title, input, "
                            "steps_state, engine, graph, target_area_id, created_by"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                item["container"]["id"],
                                request.recipe_id,
                                session_id,
                                item["title"],
                                json.dumps(item["input_data"], ensure_ascii=False),
                                json.dumps(item["steps_state"], ensure_ascii=False),
                                item["engine"],
                                (
                                    json.dumps(item["graph"], ensure_ascii=False)
                                    if item["graph"] is not None
                                    else None
                                ),
                                item["area"]["id"],
                                user_id,
                            ),
                        )
                        job_id = int(job_cur.lastrowid)
                        job_ids.append(job_id)
                        key_to_job[request.client_key.strip()] = job_id
                        conn.execute(
                            "UPDATE sessions SET job_id = ? WHERE id = ?",
                            (job_id, session_id),
                        )
                        if item["graph"] is not None:
                            for node in item["graph"]["nodes"]:
                                conn.execute(
                                    "INSERT INTO node_states("
                                    "job_id, node_id, status, output_kind"
                                    ") VALUES (?, ?, 'pending', ?)",
                                    (job_id, node["id"], node["output_kind"]),
                                )
                        identity = self._identity(
                            user_id, item["idempotency_key"]
                        )
                        conn.execute(
                            "INSERT INTO task_delegations("
                            "origin_session_id, origin_message_id, container_id, "
                            "target_area_id, job_id, routing_mode, routing_reason, "
                            "created_by, idempotency_key, idempotency_identity, "
                            "request_fingerprint, start_requested"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                request.origin_session_id,
                                request.origin_message_id,
                                item["container"]["id"],
                                item["area"]["id"],
                                job_id,
                                request.routing_mode,
                                (request.routing_reason or "").strip() or None,
                                user_id,
                                item["idempotency_key"],
                                identity,
                                item["fingerprint"],
                                int(start),
                            ),
                        )
                        action = (
                            "master.job.create"
                            if item["origin_session"] is not None
                            and item["origin_session"]["mode"] == "master"
                            else "task.delegation.create"
                        )
                        if action == "master.job.create":
                            conn.execute(
                                "UPDATE jobs SET origin_master_session_id = ? WHERE id = ?",
                                (request.origin_session_id, job_id),
                            )
                        conn.execute(
                            "INSERT INTO audit_log("
                            "actor_user_id, action, target_type, target_id, metadata"
                            ") VALUES (?, ?, 'job', ?, ?)",
                            (
                                user_id,
                                action,
                                str(job_id),
                                json.dumps(
                                    {
                                        "origin_session_id": request.origin_session_id,
                                        "container_id": item["container"]["id"],
                                        "target_area_id": item["area"]["id"],
                                        "routing_mode": request.routing_mode,
                                    }
                                ),
                            ),
                        )

                    for request, task_id in zip(batch, job_ids, strict=True):
                        seen_dependencies: set[int] = set()
                        for dependency in request.dependencies:
                            if dependency.required_status not in REQUIRED_DEPENDENCY_STATUSES:
                                raise TaskDelegationError(
                                    "invalid_required_status",
                                    "Dependency required status must be review or done",
                                )
                            if isinstance(dependency.task, str):
                                depends_on = key_to_job.get(dependency.task.strip())
                                if depends_on is None:
                                    raise TaskDelegationError(
                                        "dependency_key_not_found",
                                        f"Dependency key {dependency.task!r} is not in this batch",
                                    )
                            else:
                                depends_on = self._as_int(
                                    dependency.task, "dependency_task_id"
                                )
                            if depends_on in seen_dependencies:
                                raise TaskDelegationError(
                                    "duplicate_dependency",
                                    f"Task #{task_id} names prerequisite #{depends_on} more than once",
                                )
                            seen_dependencies.add(depends_on)
                            if depends_on == task_id:
                                raise TaskDelegationError(
                                    "self_dependency",
                                    "A Task cannot depend on itself",
                                )
                            prerequisite = conn.execute(
                                "SELECT * FROM jobs WHERE id = ? AND "
                                "(created_by = ? OR project_id IN "
                                "(SELECT id FROM projects WHERE owner_user_id = ?))",
                                (depends_on, user_id, user_id),
                            ).fetchone()
                            if prerequisite is None:
                                raise TaskDelegationError(
                                    "dependency_not_found",
                                    f"Prerequisite Task #{depends_on} was not found for this owner",
                                    404,
                                )
                            if prerequisite["status"] in TERMINAL_FAILURE_STATUSES:
                                raise TaskDelegationError(
                                    "impossible_prerequisite",
                                    f"Prerequisite Task #{depends_on} is already {prerequisite['status']}",
                                    409,
                                )
                            try:
                                conn.execute(
                                    "INSERT INTO task_dependencies("
                                    "task_id, depends_on_task_id, required_status"
                                    ") VALUES (?, ?, ?)",
                                    (
                                        task_id,
                                        depends_on,
                                        dependency.required_status,
                                    ),
                                )
                            except sqlite3.IntegrityError as exc:
                                message = str(exc).lower()
                                if "cycle" in message:
                                    raise TaskDelegationError(
                                        "dependency_cycle",
                                        "Task dependency graph contains a cycle",
                                    ) from exc
                                raise
                    conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

        results: list[DelegatedTask] = []
        for job_id in job_ids:
            if start and not defer_start:
                results.append(
                    self.start(job_id, user, connection=conn, created=created)
                )
            else:
                job = self._job_payload(conn, job_id)
                results.append(
                    DelegatedTask(
                        job=job,
                        created=created,
                        started=job["status"] != "queued",
                        blocked_reason=job.get("blocked_reason"),
                    )
                )
        return results

    def create_legacy_unscoped(
        self,
        user: Mapping[str, Any],
        *,
        title: str,
        brief: str,
        profile: Mapping[str, Any],
        input_data: Mapping[str, Any],
        recipe_id: int | None = None,
        steps: list[dict[str, Any]] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Compatibility for historical project-less Work API jobs.

        New delegated work cannot use this path because it has no Container or
        Area to audit. It stays server-owned so old API clients retain their
        scratch-work behavior while scoped UI and Master callers use the durable
        delegation contract.
        """
        conn = connection or self.db_factory()
        user_id = self._as_int(user.get("id"), "user_id")
        state_steps = steps or [
            wf.step_state_from(
                wf.normalize_steps([{"name": "Task", "instruction": brief}])[0],
                dict(input_data),
            )
        ]
        with self.app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                session_cur = conn.execute(
                    "INSERT INTO sessions("
                    "title, owner_user_id, profile_id, runner_id, visibility"
                    ") VALUES (?, ?, ?, ?, 'private')",
                    (title[:80], user_id, profile["id"], profile["runner_id"]),
                )
                job_cur = conn.execute(
                    "INSERT INTO jobs("
                    "workflow_id, session_id, title, input, steps_state, created_by"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        recipe_id,
                        session_cur.lastrowid,
                        title,
                        json.dumps(dict(input_data), ensure_ascii=False),
                        json.dumps(state_steps, ensure_ascii=False),
                        user_id,
                    ),
                )
                conn.execute(
                    "UPDATE sessions SET job_id = ? WHERE id = ?",
                    (job_cur.lastrowid, session_cur.lastrowid),
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return self._job_payload(conn, int(job_cur.lastrowid))

    def _dependency_blocker(
        self, conn: sqlite3.Connection, job_id: int
    ) -> str | None:
        rows = conn.execute(
            "SELECT dependency.depends_on_task_id, dependency.required_status, "
            "prerequisite.status, prerequisite.title, "
            "EXISTS("
            "SELECT 1 FROM node_states "
            "WHERE job_id = prerequisite.id AND status = 'failed'"
            ") AS has_failed_node "
            "FROM task_dependencies AS dependency "
            "JOIN jobs AS prerequisite ON prerequisite.id = dependency.depends_on_task_id "
            "WHERE dependency.task_id = ? ORDER BY dependency.depends_on_task_id",
            (job_id,),
        ).fetchall()
        for row in rows:
            current = str(row["status"])
            required = str(row["required_status"])
            if current in TERMINAL_FAILURE_STATUSES:
                return (
                    f"Blocked by prerequisite Task #{row['depends_on_task_id']} "
                    f"({row['title']}), which {current}"
                )
            if row["has_failed_node"]:
                return (
                    f"Blocked by prerequisite Task #{row['depends_on_task_id']} "
                    f"({row['title']}), which has a failed Recipe step"
                )
            if STATUS_RANK.get(current, -1) < STATUS_RANK[required]:
                return (
                    f"Waiting for prerequisite Task #{row['depends_on_task_id']} "
                    f"({row['title']}) to reach {required}; currently {current}"
                )
        return None

    def start(
        self,
        job_id: int,
        user: Mapping[str, Any] | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        created: bool = False,
        supervisor_budget_turns: int | None = None,
    ) -> DelegatedTask:
        conn = connection or self.db_factory()
        job_id = self._as_int(job_id, "task_id")
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise TaskDelegationError(
                "task_not_found", f"Task {job_id} was not found", 404
            )
        if job["origin_master_session_id"] is not None and not features.enabled(
            self.app.state.config,
            features.MASTER_ORCHESTRATOR,
        ):
            return DelegatedTask(
                job=self._job_payload(conn, job_id),
                created=created,
                started=False,
                blocked_reason=job["blocked_reason"],
            )
        if job["origin_master_session_id"] is not None:
            self._validate_master_start_contract(conn, job)
        user_id = int(job["created_by"])
        if user is not None and int(user.get("id") or 0) != user_id:
            owned = conn.execute(
                "SELECT 1 FROM projects WHERE id = ? AND owner_user_id = ?",
                (job["project_id"], user.get("id")),
            ).fetchone()
            if owned is None:
                raise TaskDelegationError(
                    "task_not_found", f"Task {job_id} was not found", 404
                )
            user_id = int(user["id"])

        delegation = conn.execute(
            "SELECT * FROM task_delegations WHERE job_id = ?", (job_id,)
        ).fetchone()
        if delegation is not None:
            conn.execute(
                "UPDATE task_delegations SET start_requested = 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (job_id,),
            )
            blocker = self._dependency_blocker(conn, job_id)
            if blocker:
                conn.execute(
                    "UPDATE jobs SET blocked_reason = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'queued'",
                    (blocker, job_id),
                )
                conn.execute(
                    "UPDATE task_delegations SET start_state = 'blocked', "
                    "blocked_reason = ?, last_start_error = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                    (blocker, job_id),
                )
                return DelegatedTask(
                    job=self._job_payload(conn, job_id),
                    created=created,
                    started=False,
                    blocked_reason=blocker,
                )
            conn.execute(
                "UPDATE jobs SET blocked_reason = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (job_id,),
            )
            conn.execute(
                "UPDATE task_delegations SET start_state = 'starting', "
                "blocked_reason = NULL, last_start_error = NULL, "
                "start_attempts = start_attempts + 1, "
                "start_attempted_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (job_id,),
            )

        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job["status"] != "queued":
            if (
                delegation is not None
                and job["status"] == "running"
                and job["engine"] == "graph"
            ):
                try:
                    self._start_graph(conn, job, user_id, recover_running=True)
                except Exception as exc:
                    conn.execute(
                        "UPDATE task_delegations SET start_state = 'failed', "
                        "last_start_error = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE job_id = ?",
                        (str(exc)[:1000], job_id),
                    )
                    raise
            if delegation is not None:
                conn.execute(
                    "UPDATE task_delegations SET start_state = 'started', "
                    "started_at = COALESCE(started_at, CURRENT_TIMESTAMP), "
                    "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                    (job_id,),
                )
            return DelegatedTask(
                job=self._job_payload(conn, job_id),
                created=created,
                started=True,
                blocked_reason=None,
            )

        try:
            if job["engine"] == "graph":
                self._start_graph(
                    conn,
                    job,
                    user_id,
                    supervisor_budget_turns=supervisor_budget_turns,
                )
            else:
                self._start_linear(
                    conn,
                    job,
                    user_id,
                    supervisor_budget_turns=supervisor_budget_turns,
                )
        except Exception as exc:
            if delegation is not None:
                conn.execute(
                    "UPDATE task_delegations SET start_state = 'failed', "
                    "last_start_error = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE job_id = ?",
                    (str(exc)[:1000], job_id),
                )
            raise
        if delegation is not None:
            conn.execute(
                "UPDATE task_delegations SET start_state = 'started', "
                "started_at = COALESCE(started_at, CURRENT_TIMESTAMP), "
                "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (job_id,),
            )
        return DelegatedTask(
            job=self._job_payload(conn, job_id),
            created=created,
            started=True,
            blocked_reason=None,
        )

    @staticmethod
    def _reserve_supervisor_turn(
        conn: sqlite3.Connection,
        budget_turns: int | None,
    ) -> None:
        if budget_turns is None:
            return
        row = conn.execute(
            "SELECT value FROM app_settings "
            "WHERE key = 'master.budget.turns_used'"
        ).fetchone()
        try:
            turns_used = max(0, int(row["value"])) if row else 0
        except (TypeError, ValueError, OverflowError):
            turns_used = 0
        if turns_used >= budget_turns:
            raise TaskDelegationError(
                "master_budget_exhausted",
                "Master unattended turn budget is exhausted",
                409,
            )
        conn.execute(
            "INSERT INTO app_settings(key, value, updated_at) "
            "VALUES ('master.budget.turns_used', ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (str(turns_used + 1),),
        )

    def _start_linear(
        self,
        conn: sqlite3.Connection,
        job: sqlite3.Row,
        user_id: int,
        *,
        supervisor_budget_turns: int | None = None,
    ) -> None:
        try:
            steps = json.loads(job["steps_state"] or "[]")
        except json.JSONDecodeError as exc:
            raise TaskDelegationError(
                "task_not_startable", "Task steps are invalid", 409
            ) from exc
        if not steps or not job["session_id"]:
            raise TaskDelegationError(
                "task_not_startable", "Task has no runnable session or step", 409
            )
        if features.enabled(self.app.state.config, features.REPO_WORKTREES):
            try:
                worktrees.ensure_job_worktree(conn, self.app.state.config, job)
            except worktrees.WorktreeError as exc:
                raise TaskDelegationError(
                    "worktree_failed", f"Cannot start repo Task: {exc}", 409
                ) from exc
        delegation = conn.execute(
            "SELECT origin_session_id FROM task_delegations WHERE job_id = ?",
            (job["id"],),
        ).fetchone()
        if delegation and delegation["origin_session_id"] is not None and not conn.execute(
            "SELECT 1 FROM job_checkpoints WHERE job_id = ? LIMIT 1", (job["id"],)
        ).fetchone():
            create_checkpoint(conn, int(job["id"]))
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (job["session_id"],)
        ).fetchone()
        profile = (
            conn.execute(
                "SELECT * FROM profiles WHERE id = ? AND user_id = ?",
                (session["profile_id"], user_id),
            ).fetchone()
            if session
            else None
        )
        if profile is None:
            raise TaskDelegationError(
                "task_agent_not_found",
                "Task-agent profile is no longer available",
                409,
            )
        inputs = json.loads(job["input"] or "{}")
        prompt = wf.build_step_prompt(steps[0], 0, len(steps), inputs)
        with self.app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if (
                    job["origin_master_session_id"] is not None
                    and self._master_active_slots(
                        conn,
                        int(job["origin_master_session_id"]),
                    )
                    >= self._master_parallel_limit()
                ):
                    raise TaskDelegationError(
                        "master_capacity_full",
                        "Master Task capacity is full",
                        409,
                    )
                claimed = conn.execute(
                    "UPDATE jobs SET status = 'running', started_at = CURRENT_TIMESTAMP, "
                    "current_step_idx = 0, blocked_reason = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
                    (job["id"],),
                )
                if claimed.rowcount == 0:
                    conn.execute("ROLLBACK")
                    return
                self._reserve_supervisor_turn(
                    conn,
                    supervisor_budget_turns,
                )
                run_cur = conn.execute(
                    "INSERT INTO runs("
                    "session_id, project_id, user_id, profile_id, runner_id, "
                    "status, prompt, model, hermes_home"
                    ") VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (
                        job["session_id"],
                        job["project_id"],
                        user_id,
                        profile["id"],
                        profile["runner_id"],
                        prompt,
                        profile["default_model"],
                        profile["hermes_home"],
                    ),
                )
                run_id = int(run_cur.lastrowid)
                steps[0].update(
                    {
                        "status": "running",
                        "run_id": run_id,
                        "started_at": iso_now(),
                    }
                )
                conn.execute(
                    "UPDATE jobs SET steps_state = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (json.dumps(steps, ensure_ascii=False), job["id"]),
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        self.app.state.worker.add_event(
            run_id,
            int(job["session_id"]),
            job["project_id"],
            "run.queued",
            {"runner": profile["runner_id"], "job": int(job["id"])},
        )

    def _start_graph(
        self,
        conn: sqlite3.Connection,
        job: sqlite3.Row,
        user_id: int,
        *,
        recover_running: bool = False,
        supervisor_budget_turns: int | None = None,
    ) -> None:
        if not features.enabled(self.app.state.config, features.WORKFLOW_GRAPH):
            raise TaskDelegationError(
                "graph_disabled", "Workflow graph feature is disabled", 409
            )
        if features.enabled(self.app.state.config, features.REPO_WORKTREES):
            try:
                worktrees.ensure_job_worktree(conn, self.app.state.config, job)
            except worktrees.WorktreeError as exc:
                raise TaskDelegationError(
                    "worktree_failed", f"Cannot start repo Task: {exc}", 409
                ) from exc
        delegation = conn.execute(
            "SELECT origin_session_id FROM task_delegations WHERE job_id = ?",
            (job["id"],),
        ).fetchone()
        if delegation and delegation["origin_session_id"] is not None and not conn.execute(
            "SELECT 1 FROM job_checkpoints WHERE job_id = ? LIMIT 1", (job["id"],)
        ).fetchone():
            create_checkpoint(conn, int(job["id"]))
        if not recover_running:
            with self.app.state.db_lock:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if (
                        job["origin_master_session_id"] is not None
                        and self._master_active_slots(
                            conn,
                            int(job["origin_master_session_id"]),
                        )
                        >= self._master_parallel_limit()
                    ):
                        raise TaskDelegationError(
                            "master_capacity_full",
                            "Master Task capacity is full",
                            409,
                        )
                    claimed = conn.execute(
                        "UPDATE jobs SET status = 'running', "
                        "started_at = CURRENT_TIMESTAMP, "
                        "blocked_reason = NULL, "
                        "updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND status = 'queued' "
                        "AND engine = 'graph'",
                        (job["id"],),
                    )
                    if claimed.rowcount != 1:
                        conn.execute("ROLLBACK")
                        return
                    self._reserve_supervisor_turn(
                        conn,
                        supervisor_budget_turns,
                    )
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
        try:
            run_ids = self.app.state.worker.graph_executor.dispatch_ready(
                int(job["id"])
            )
        except Exception as exc:
            raise TaskDelegationError(
                "graph_start_failed", str(exc), 409
            ) from exc
        if not run_ids:
            unfinished = conn.execute(
                "SELECT status FROM node_states "
                "WHERE job_id = ? AND status != 'done'",
                (job["id"],),
            ).fetchall()
            if unfinished:
                if any(
                    row["status"] in {"ready", "running"} for row in unfinished
                ):
                    return
                conn.execute(
                    "UPDATE jobs SET status = 'queued', started_at = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                    (job["id"],),
                )
                raise TaskDelegationError(
                    "graph_not_dispatchable",
                    "Graph Task has no dispatchable node",
                    409,
                )
            final_status = completed_landing_status(conn, job)
            conn.execute(
                "UPDATE jobs SET status = ?, finished_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                (final_status, job["id"]),
            )

    def prerequisite_changed(
        self,
        prerequisite_job_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[int]:
        """Refresh blockers and start every newly ready dependent exactly once."""
        conn = connection or self.db_factory()
        projection = getattr(self.app.state, "master_projection", None)
        if projection is not None:
            projection.safe_project_task(prerequisite_job_id)
        dependent_ids = [
            int(row["task_id"])
            for row in conn.execute(
                "SELECT task_id FROM task_dependencies "
                "WHERE depends_on_task_id = ? ORDER BY task_id",
                (prerequisite_job_id,),
            ).fetchall()
        ]
        started: list[int] = []
        for task_id in dependent_ids:
            requested = conn.execute(
                "SELECT start_requested FROM task_delegations WHERE job_id = ?",
                (task_id,),
            ).fetchone()
            blocker = self._dependency_blocker(conn, task_id)
            conn.execute(
                "UPDATE jobs SET blocked_reason = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'queued'",
                (blocker, task_id),
            )
            conn.execute(
                "UPDATE task_delegations SET start_state = ?, "
                "blocked_reason = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE job_id = ? AND start_requested = 0",
                ("blocked" if blocker else "pending", blocker, task_id),
            )
            if projection is not None:
                projection.safe_project_task(task_id)
            if not requested or not requested["start_requested"]:
                continue
            result = self.start(task_id, connection=conn)
            if projection is not None:
                projection.safe_project_task(task_id)
            if result.started:
                started.append(task_id)
        return started

    def resume_committed(
        self, *, connection: sqlite3.Connection | None = None
    ) -> list[int]:
        """Retry durable start intents left pending by timeout, crash, or restart."""
        conn = connection or self.db_factory()
        rows = conn.execute(
            "SELECT delegation.job_id FROM task_delegations AS delegation "
            "JOIN jobs ON jobs.id = delegation.job_id "
            "WHERE delegation.start_requested = 1 AND ("
            "jobs.status = 'queued' OR delegation.start_state != 'started'"
            ") "
            "ORDER BY delegation.created_at, delegation.id"
        ).fetchall()
        started: list[int] = []
        for row in rows:
            try:
                result = self.start(int(row["job_id"]), connection=conn)
            except (TaskDelegationError, worktrees.WorktreeError):
                continue
            if result.started:
                started.append(int(row["job_id"]))
        return started

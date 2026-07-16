"""Completion/failure advancement for ADR-0001 graph workflow nodes."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import exceptions as jsonschema_exceptions  # pyright: ignore[reportMissingModuleSource]
from jsonschema import validators as jsonschema_validators  # pyright: ignore[reportMissingModuleSource]

from . import features, state
from .graph import normalize_graph, parse_output_contract
from .graph_executor import GraphExecutor  # pyright: ignore[reportMissingImports]

AddEvent = Callable[[int, int, int | None, str, dict[str, Any]], None]


class NodeOutputError(ValueError):
    """A node response did not satisfy its declared output contract."""


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


def _node_by_id(graph: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return dict(node)
    raise NodeOutputError(f"node '{node_id}' is missing from the frozen graph")


def _parse_json_output(answer: str) -> Any:
    try:
        return json.loads(answer)
    except json.JSONDecodeError as exc:
        raise NodeOutputError(f"invalid JSON output: {exc.msg}") from exc


def _artifact_root(app: Any, job: Mapping[str, Any]) -> Path:
    if job.get("project_id"):
        row = app.state.worker_db.execute(
            "SELECT path FROM projects WHERE id = ?", (job["project_id"],)
        ).fetchone()
        if not row or not row["path"]:
            raise NodeOutputError("graph job project path is unavailable")
        return Path(row["path"]).resolve()
    return (
        Path(app.state.config["workspace_root"])
        / "scratch"
        / "workflow-runs"
        / f"job-{job['job_id']}"
    ).resolve()


def _validate_artifact_refs(app: Any, job: Mapping[str, Any], value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "artifacts" in value:
        value = value["artifacts"]
    elif isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not value:
        raise NodeOutputError(
            "artifact-ref output must be a non-empty object, array, or {artifacts:[...]}"
        )

    root = _artifact_root(app, job)
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise NodeOutputError(f"artifact reference {index} must be an object")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise NodeOutputError(f"artifact reference {index} requires a path")
        normalized_path = raw_path.strip().replace("\\", "/")
        relative = PurePosixPath(normalized_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise NodeOutputError(
                f"artifact reference {index} path must stay inside the job workspace"
            )
        target = (root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise NodeOutputError(
                f"artifact reference {index} escapes the job workspace"
            ) from exc
        if not target.exists():
            raise NodeOutputError(
                f"artifact reference {index} does not exist: {normalized_path}"
            )

        ref: dict[str, Any] = {"path": relative.as_posix()}
        for key in ("type", "title", "id"):
            if item.get(key) is not None:
                if not isinstance(item[key], str):
                    raise NodeOutputError(
                        f"artifact reference {index} field '{key}' must be a string"
                    )
                ref[key] = item[key]
        refs.append(ref)
    return refs


def validate_node_output(
    app: Any,
    job: Mapping[str, Any],
    node: Mapping[str, Any],
    answer: str,
) -> Any:
    """Parse and validate one runner answer into its canonical typed value."""
    stripped = answer.strip()
    if not stripped:
        raise NodeOutputError("node produced empty output")
    if stripped.upper().startswith("BLOCKED:"):
        raise NodeOutputError(stripped[:1000])
    if stripped.startswith("Agent produced no output"):
        raise NodeOutputError(stripped[:1000])

    contract = parse_output_contract(node)
    if contract.kind == "text":
        return stripped
    value = _parse_json_output(stripped)
    if contract.kind == "json":
        if contract.schema is not None:
            validator_cls = jsonschema_validators.validator_for(contract.schema)
            try:
                validator_cls(contract.schema).validate(value)
            except jsonschema_exceptions.ValidationError as exc:
                location = ".".join(str(part) for part in exc.absolute_path)
                suffix = f" at {location}" if location else ""
                raise NodeOutputError(
                    f"JSON output does not match schema{suffix}: {exc.message}"
                ) from exc
        return value
    return _validate_artifact_refs(app, job, value)


class GraphAdvancers:
    """Advance graph jobs after worker run completion or failure."""

    def __init__(self, app: Any, executor: GraphExecutor):
        self.app = app
        self.executor = executor

    def _attempt(self, run_id: int) -> dict[str, Any] | None:
        row = self.app.state.worker_db.execute(
            """
            SELECT ns.id AS node_state_id, ns.job_id, ns.node_id,
                   ns.status AS node_status, ns.run_id AS node_run_id,
                   ns.version AS node_version,
                   j.status AS job_status, j.graph AS job_graph,
                   j.project_id, j.input AS job_input
            FROM node_states ns
            JOIN jobs j ON j.id = ns.job_id
            WHERE ns.run_id = ? AND j.engine = 'graph'
            """,
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def _pause_failed_attempt(
        self,
        attempt: Mapping[str, Any],
        run: Mapping[str, Any],
        error: str,
    ) -> bool:
        db = self.app.state.worker_db
        transitioned = state.guarded_node_transition(
            db,
            _as_int(attempt["node_state_id"]),
            "failed",
            ("running",),
            _as_int(attempt["node_version"]),
            error=error[:1000],
            mark_finished=True,
            expected_run_id=_as_int(run["id"]),
        )
        if not transitioned:
            return False
        state.guarded_transition(
            db,
            "jobs",
            _as_int(attempt["job_id"]),
            "review",
            ("running",),
            set_extra="updated_at=CURRENT_TIMESTAMP",
        )
        return True

    @staticmethod
    def _emit_update(
        run: Mapping[str, Any],
        attempt: Mapping[str, Any],
        status: str,
        add_event: AddEvent,
        **extra: Any,
    ) -> None:
        add_event(
            _as_int(run["id"]),
            _as_int(run["session_id"]),
            run.get("project_id"),
            "graph.node.update",
            {
                "job_id": attempt["job_id"],
                "node_id": attempt["node_id"],
                "status": status,
                **extra,
            },
        )

    def advance_run(self, run: Mapping[str, Any], answer: str, add_event: AddEvent) -> bool:
        """Validate/persist a completed node, pause for gates, or dispatch next."""
        run_id = _as_int(run["id"])
        db = self.app.state.worker_db
        dispatch_job_id: int | None = None
        with self.app.state.db_lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                attempt = self._attempt(run_id)
                # A paused ('review') job still accepts results from nodes that were
                # already in flight when a sibling gated or failed. Rejecting them
                # would drop finished work and strand the node in 'running' forever.
                if (
                    not attempt
                    or attempt["node_status"] != "running"
                    or attempt["job_status"] not in {"running", "review"}
                ):
                    db.execute("COMMIT")
                    return False

                if not features.enabled(
                    self.app.state.config, features.WORKFLOW_GRAPH
                ):
                    error = "feature_disabled:workflow_graph"
                    failed = self._pause_failed_attempt(attempt, run, error)
                    db.execute("COMMIT")
                    if failed:
                        self._emit_update(
                            run, attempt, "failed", add_event, error=error
                        )
                    return failed

                graph = normalize_graph(attempt["job_graph"] or "")
                node = _node_by_id(graph, str(attempt["node_id"]))
                job_context = {
                    "job_id": attempt["job_id"],
                    "project_id": attempt["project_id"],
                }
                try:
                    value = validate_node_output(
                        self.app, job_context, node, answer
                    )
                except NodeOutputError as exc:
                    error = str(exc)
                    failed = self._pause_failed_attempt(attempt, run, error)
                    db.execute("COMMIT")
                    if failed:
                        self._emit_update(
                            run, attempt, "failed", add_event, error=error[:1000]
                        )
                    return failed

                serialized = json.dumps(value, ensure_ascii=False)
                gate = bool(node.get("review_required"))
                target_status = "review" if gate else "done"
                transitioned = state.guarded_node_transition(
                    db,
                    _as_int(attempt["node_state_id"]),
                    target_status,
                    ("running",),
                    _as_int(attempt["node_version"]),
                    output_kind=str(node["output_kind"]),
                    output=serialized,
                    error=None,
                    mark_finished=True,
                    expected_run_id=run_id,
                )
                if not transitioned:
                    db.execute("COMMIT")
                    return False

                remaining = db.execute(
                    "SELECT COUNT(*) AS c FROM node_states "
                    "WHERE job_id = ? AND status != 'done'",
                    (attempt["job_id"],),
                ).fetchone()["c"]
                job_status = str(attempt["job_status"])
                if gate or remaining == 0:
                    state.guarded_transition(
                        db,
                        "jobs",
                        _as_int(attempt["job_id"]),
                        "review",
                        ("running",),
                        set_extra="updated_at=CURRENT_TIMESTAMP",
                    )
                    job_status = "review"
                elif job_status == "running":
                    # Only a job that is still running may pull more work forward.
                    # If a sibling already paused it, this node's dependents wait
                    # for the owner to resolve that pause.
                    dispatch_job_id = _as_int(attempt["job_id"])
                db.execute("COMMIT")
            except Exception as exc:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise exc

            self._emit_update(
                run,
                attempt,
                target_status,
                add_event,
                job_status=job_status,
            )

        if dispatch_job_id is not None:
            self.executor.dispatch_ready(dispatch_job_id)
        return True

    def fail_run(self, run: Mapping[str, Any], error: str, add_event: AddEvent) -> bool:
        """Pause a graph job when its current runner attempt fails."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                attempt = self._attempt(_as_int(run["id"]))
                if not attempt or attempt["node_status"] != "running":
                    db.execute("COMMIT")
                    return False
                failed = self._pause_failed_attempt(attempt, run, error)
                db.execute("COMMIT")
            except Exception as exc:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise exc
            if failed:
                self._emit_update(
                    run, attempt, "failed", add_event, error=error[:1000]
                )
            return failed

"""Completion/failure advancement for ADR-0001 graph workflow nodes."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import exceptions as jsonschema_exceptions  # pyright: ignore[reportMissingModuleSource]
from jsonschema import validators as jsonschema_validators  # pyright: ignore[reportMissingModuleSource]

from . import features, satpam, state
from .graph import normalize_graph, parse_output_contract, ready_node_ids
from .graph_executor import GraphExecutor  # pyright: ignore[reportMissingImports]

AddEvent = Callable[[int, int, int | None, str, dict[str, Any]], None]

# Decision-hold (Phase-1 slice 12, T10 #4): the structured output-contract
# marker a node agent uses to surface a genuine open owner decision instead of
# guessing. The node parks in 'review' with the question attached; dependents
# hold (their dependency never reaches 'done'), independent branches keep
# dispatching, and the owner's answer re-runs the node.
DECISION_MARKER = "DECISION_NEEDED:"


def parse_decision_question(answer: str) -> str | None:
    """The question in a DECISION_NEEDED reply, or None for ordinary output."""
    stripped = answer.strip()
    if not stripped.upper().startswith(DECISION_MARKER):
        return None
    question = stripped[len(DECISION_MARKER):].strip()
    return (question or "The agent flagged an open decision but did not state the question.")[:1000]


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

    def _forward_progress(self, db: Any, graph: Mapping[str, Any], job_id: int) -> bool:
        """Can this plan still move without owner input? True when any node is
        in flight or the dispatcher could start one. A node parked in review
        (decision-hold) blocks only its own dependents - they never enter
        ``ready_node_ids`` because their dependency is not 'done'."""
        rows = db.execute(
            "SELECT node_id, status FROM node_states WHERE job_id = ?", (job_id,)
        ).fetchall()
        statuses = {str(r["node_id"]): str(r["status"]) for r in rows}
        if any(s in ("ready", "running") for s in statuses.values()):
            return True
        return bool(ready_node_ids(graph, statuses))

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

                # Decision-hold (slice 12, T10 #4): a genuine open decision parks
                # THIS node in review with the question - the job stays running
                # while independent branches can still move, and parks only when
                # they drain. Dependents hold on their own: this node never
                # reaches 'done', so they never become ready.
                question = parse_decision_question(answer)
                if question is not None:
                    parked = state.guarded_node_transition(
                        db,
                        _as_int(attempt["node_state_id"]),
                        "review",
                        ("running",),
                        _as_int(attempt["node_version"]),
                        error=None,
                        mark_finished=True,
                        expected_run_id=run_id,
                    )
                    if not parked:
                        db.execute("COMMIT")
                        return False
                    db.execute(
                        "UPDATE node_states SET question = ?, answer = NULL, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (question, _as_int(attempt["node_state_id"])),
                    )
                    job_status = str(attempt["job_status"])
                    if job_status == "running":
                        if self._forward_progress(
                            db, graph, _as_int(attempt["job_id"])
                        ):
                            dispatch_job_id = _as_int(attempt["job_id"])
                        else:
                            state.guarded_transition(
                                db,
                                "jobs",
                                _as_int(attempt["job_id"]),
                                "review",
                                ("running",),
                                set_extra="updated_at=CURRENT_TIMESTAMP",
                            )
                            job_status = "review"
                    db.execute("COMMIT")
                    self._emit_update(
                        run, attempt, "review", add_event,
                        question=question, job_status=job_status,
                    )
                    if dispatch_job_id is not None:
                        self.executor.dispatch_ready(dispatch_job_id)
                    return True

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
                    contract_failures = 0
                    if failed:
                        # 'Confused' signal (slice 12): count contract failures
                        # across this node's attempts; a repeat becomes an
                        # owner-facing escalation record, not just a failed node.
                        db.execute(
                            "UPDATE node_states SET contract_failures = contract_failures + 1, "
                            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (_as_int(attempt["node_state_id"]),),
                        )
                        contract_failures = _as_int(db.execute(
                            "SELECT contract_failures FROM node_states WHERE id = ?",
                            (_as_int(attempt["node_state_id"]),),
                        ).fetchone()["contract_failures"])
                    db.execute("COMMIT")
                    if failed:
                        self._emit_update(
                            run, attempt, "failed", add_event, error=error[:1000]
                        )
                        if contract_failures >= satpam.CONTRACT_FAILURES_ESCALATE:
                            satpam.record_escalation(
                                self.app,
                                job_id=_as_int(attempt["job_id"]),
                                node_id=str(attempt["node_id"]),
                                detection=satpam.DETECTION_CONFUSED,
                                # Event-framed reason: this row survives on the
                                # job after the owner reruns/merges, so it must
                                # not claim "the plan is paused" as live state.
                                reason=(
                                    f"This job's output failed its declared contract {contract_failures} "
                                    "times - the agent seemed confused about what to produce. "
                                    "Review the job, sharpen its instruction or output contract, "
                                    "then rerun the failed step."
                                ),
                                run_id=run_id,
                                session_id=_as_int(run["session_id"]),
                                project_id=run.get("project_id"),
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
                    if self._forward_progress(db, graph, _as_int(attempt["job_id"])):
                        dispatch_job_id = _as_int(attempt["job_id"])
                    else:
                        # Nothing left can move without the owner - a sibling is
                        # parked (decision-hold) and its dependents are all that
                        # remain. The independent branches have drained; park the
                        # plan instead of idling in 'running' forever.
                        state.guarded_transition(
                            db,
                            "jobs",
                            _as_int(attempt["job_id"]),
                            "review",
                            ("running",),
                            set_extra="updated_at=CURRENT_TIMESTAMP",
                        )
                        job_status = "review"
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

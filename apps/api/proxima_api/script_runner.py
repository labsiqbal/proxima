"""Deterministic executor for graph script nodes (Phase-1 slice 6, T6).

A ``wf_script_node`` run is claimed from the ordinary runs queue like any
other, but instead of a runner/ACP session the worker hands it here: the frozen
node names a library script (``scripts/<command>``), and this module executes
it as a subprocess — exec array, never a shell string — with the project
container as cwd. stdin carries the graph engine's typed hand-off (the same
``{"job_input": …, "upstream": […]}`` payload an agent node gets in its
prompt), stdout becomes the node output (validated against the node's output
contract by the ordinary graph advancer), and the exit code decides
success/failure.

Trust gate (T6 #5, the captain's hash-binding decision): before anything
executes, the script's current bytes are hashed and compared to the approved
hash in ``script_trust``. First run — or any run after the bytes changed —
blocks instead of executing: the run fails with a ``script_approval_required:``
error, the node pauses the plan in review, and the owner's one-time approval
(`.../approve-script`) records the hash and reruns the node. An unchanged
trusted script then runs with no per-run approval, which is the whole
"deterministic + free" payoff.

Environment boundary: the subprocess gets a minimal environment (PATH, HOME,
locale) rather than the server's, so scripts cannot read Proxima's own config
or secrets out of the API process environment.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from . import app_settings, scripts_library
from .graph import normalize_graph
from .workflows import substitute

logger = logging.getLogger("proxima.script_runner")

# Guardrail, not a tuning knob: node output is persisted in SQLite and rendered
# in the UI, so a runaway script must fail loudly rather than store megabytes.
MAX_OUTPUT_BYTES = 1_000_000
_STDERR_TAIL = 500
_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")

APPROVAL_ERROR_PREFIX = "script_approval_required:"


def approval_required_error(rel_path: str, digest: str) -> str:
    """The structured node error the UI recognizes as an approval ask."""
    return (
        f"{APPROVAL_ERROR_PREFIX} scripts/{rel_path} (sha256 {digest}) — this "
        "script's content has not been approved yet. Approve it once and it "
        "runs without asking until its content changes."
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


class ScriptRunner:
    """Execute one queued script-node run end to end.

    Constructed by ``RunWorker`` with itself, so completion reuses the worker's
    event feed, output-link scan, and the graph advancers — a script attempt
    walks exactly the same node state machine as an agent attempt.
    """

    def __init__(self, app: Any, worker: Any):
        self.app = app
        self.worker = worker

    def _attempt(self, run_id: int) -> dict[str, Any] | None:
        row = self.app.state.worker_db.execute(
            """
            SELECT ns.id AS node_state_id, ns.node_id, ns.status AS node_status,
                   ns.inputs AS node_inputs, ns.job_id,
                   j.graph AS job_graph, j.project_id AS job_project_id
            FROM node_states ns
            JOIN jobs j ON j.id = ns.job_id
            WHERE ns.run_id = ? AND j.engine = 'graph'
            """,
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    async def _heartbeat(self, run_id: int, interval: float) -> None:
        db = self.app.state.worker_db
        while True:
            await asyncio.sleep(interval)
            with self.app.state.db_lock:
                db.execute(
                    "UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (run_id,),
                )

    def _fail(self, run: dict[str, Any], error: str) -> None:
        """Fail the run, then pause the node/plan through the graph advancer."""
        db = self.app.state.worker_db
        run_id = _as_int(run["id"])
        with self.app.state.db_lock:
            failed = db.execute(
                "UPDATE runs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'running'",
                (error, run_id),
            ).rowcount > 0
            if not failed:
                return  # cancelled concurrently — nothing to advance
            self.worker.add_event(
                run_id,
                _as_int(run["session_id"]),
                run.get("project_id"),
                "run.failed",
                {"error": error[:1000], "kind": "wf_script_node"},
            )
        # Outside db_lock: fail_run takes the lock itself.
        self.worker.graph_advancers.fail_run(run, error, self.worker.add_event)

    async def execute(self, run: dict[str, Any]) -> None:
        db = self.app.state.worker_db
        cfg = self.app.state.config
        run_id = _as_int(run["id"])
        session_id = _as_int(run["session_id"])
        project_id = run.get("project_id")
        run_start_ts = time.time()

        attempt = self._attempt(run_id)
        if not attempt or attempt["node_status"] != "running":
            self._fail(run, "script run has no live node attempt")
            return
        if not attempt["job_project_id"]:
            self._fail(run, "script steps need a project container to run in")
            return
        project = db.execute(
            "SELECT path FROM projects WHERE id = ?", (attempt["job_project_id"],)
        ).fetchone()
        if not project or not project["path"]:
            self._fail(run, "script step's project path is unavailable")
            return
        project_root = Path(project["path"])

        try:
            graph = normalize_graph(attempt["job_graph"] or "")
        except Exception as exc:
            self._fail(run, f"stored plan graph is invalid: {exc}")
            return
        node = next(
            (n for n in graph.get("nodes", []) if n.get("id") == attempt["node_id"]),
            None,
        )
        if not node or node.get("type") != "script":
            self._fail(run, "run does not correspond to a script node in the plan")
            return

        try:
            script_path = scripts_library.resolve_script(project_root, str(node["command"]))
            digest = scripts_library.content_hash(script_path)
        except (scripts_library.ScriptResolutionError, OSError) as exc:
            self._fail(run, str(exc))
            return
        rel_path = scripts_library.normalize_script_rel_path(str(node["command"]))

        # The trust gate — checked against the exact bytes about to run, every
        # run, so an edit between approval and execution cannot slip through.
        trusted = scripts_library.trusted_hash(db, _as_int(attempt["job_project_id"]), rel_path)
        if trusted != digest:
            with self.app.state.db_lock:
                self.worker.add_event(
                    run_id,
                    session_id,
                    project_id,
                    "script.approval.required",
                    {
                        "job_id": attempt["job_id"],
                        "node_id": attempt["node_id"],
                        "script": f"scripts/{rel_path}",
                        "content_hash": digest,
                        "previously_trusted": trusted is not None,
                    },
                )
            self._fail(run, approval_required_error(rel_path, digest))
            return

        try:
            argv = scripts_library.exec_argv(script_path)
        except scripts_library.ScriptResolutionError as exc:
            self._fail(run, str(exc))
            return

        # The typed hand-off, exactly as resolved at dispatch: job input +
        # upstream outputs. {{var}} placeholders in args fill from the job
        # input the same way an agent node's instruction text does.
        try:
            inputs = json.loads(attempt["node_inputs"] or "{}")
        except json.JSONDecodeError:
            inputs = {}
        job_input = inputs.get("job_input") if isinstance(inputs, dict) else {}
        if not isinstance(job_input, dict):
            job_input = {}
        args = [substitute(str(arg), job_input) for arg in (node.get("args") or [])]
        stdin_bytes = json.dumps(inputs, ensure_ascii=False).encode("utf-8")

        env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
        timeout = app_settings.get_run_timeout_seconds(db, cfg)
        heartbeat = asyncio.create_task(
            self._heartbeat(run_id, float(cfg.get("run_heartbeat_seconds") or 10))
        )
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    *args,
                    cwd=str(project_root),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except FileNotFoundError as exc:
                self._fail(run, f"script interpreter not found: {exc}")
                return
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=stdin_bytes), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                # No auto-continuation for scripts: a deterministic step that
                # overruns the quota is broken or mis-sized, not "still going".
                self._fail(run, f"script timed out after {timeout}s")
                return

            if len(stdout) > MAX_OUTPUT_BYTES:
                self._fail(
                    run,
                    f"script produced more than {MAX_OUTPUT_BYTES // 1_000_000} MB of "
                    "output - a plan step's output must be its result, not a data dump",
                )
                return
            if proc.returncode != 0:
                tail = stderr.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL:]
                error = f"script exited with code {proc.returncode}"
                if tail:
                    error += f": {tail}"
                self._fail(run, error)
                return

            answer = stdout.decode("utf-8", errors="replace").strip()
            self._complete(run, answer, run_start_ts)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    def _complete(self, run: dict[str, Any], answer: str, run_start_ts: float) -> None:
        """Persist the output message, close the run, advance the graph."""
        db = self.app.state.worker_db
        run_id = _as_int(run["id"])
        session_id = _as_int(run["session_id"])
        project_id = run.get("project_id")
        output_links = self.worker.outputs.output_links_for_project(project_id, run_start_ts)
        self.worker.outputs.save_assistant_message(
            run_id,
            session_id,
            project_id,
            answer or "(script produced no output)",
            "Script",
            output_links,
            self.worker.add_event,
        )
        with self.app.state.db_lock:
            completed = db.execute(
                "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'running'",
                (run_id,),
            ).rowcount > 0
            if completed:
                self.worker.add_event(
                    run_id, session_id, project_id, "run.completed",
                    {"stop_reason": "script_exit", "kind": "wf_script_node"},
                )
        if not completed:
            return  # cancelled mid-run — do not advance the plan
        try:
            self.worker.graph_advancers.advance_run(run, answer, self.worker.add_event)
        except Exception:
            logger.exception("script node advance failed (non-fatal)")

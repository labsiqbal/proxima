"""Satpam - the fleet-level supervision loop (Phase-1 slice 12, T10).

ONE watchman over all running jobs, hosted on the worker loop's cadence as a
sibling of RunReaper: the reaper owns DEAD runs (stale heartbeat - unchanged;
the satpam consumes its outcome), the satpam owns ALIVE-BUT-UNPRODUCTIVE ones.
It reads DURABLE signals only - DB rows and worktree diff signatures, never an
agent's stream - evaluates once per continuation turn (not per token), and
makes NO LLM calls. Fail-quiet by contract: every entry point swallows and
logs its own errors, because supervision must never crash the worker or a run.

Detection ladder (cheap, objective; N and the sweep cadence are Settings):
- dead:     heartbeat/reaper, unchanged.
- stalled:  a repo chain whose continuation turns leave the worktree signature
            (branch head + uncommitted status) unchanged N turns in a row.
- looping:  consecutive turns whose salvaged output + repo state hash to the
            same fingerprint N turns in a row.
- confused: continuation cap reached (T5), or a node failing its output
            contract repeatedly (the graph engine's validation, re-used).

Action ladder (captain-ratified automation line, T10):
a. steer         - one corrective prompt into the job's next continuation turn:
                   AUTOMATIC, recorded in the job timeline.
b. restart-clean - re-run the stuck work fresh: AUTOMATIC only for non-repo
                   work; for repo work it becomes a PENDING approval card
                   (restart discards the worktree - destructive, owner-gated).
c. escalate      - pause the plan in review with a plain-language record.
                   Anything destructive/irreversible always lands here or in b's
                   approval card - the satpam never discards repo work on its own.

Every action is a ``satpam_interventions`` row + a ``satpam.*`` timeline event:
no silent interventions (T10 #5).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from . import app_settings, state
from . import workflows as wf
from . import worktrees
from .auth import iso_now
from .graph import descendant_node_ids, node_touches_repo, normalize_graph

log = logging.getLogger("proxima.satpam")

ACTION_STEER = "steer"
ACTION_RESTART = "restart"
ACTION_ESCALATE = "escalate"

DETECTION_STALLED = "stalled"
DETECTION_LOOPING = "looping"
DETECTION_CONFUSED = "confused"

STATUS_APPLIED = "applied"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DISMISSED = "dismissed"

EVENT_STEERED = "satpam.steered"
EVENT_RESTART_QUEUED = "satpam.restart.queued"
EVENT_RESTARTED = "satpam.restarted"
EVENT_ESCALATED = "satpam.escalated"

# A node whose output fails its declared contract this many times across
# attempts is a confused agent (T10 detection rung 4). The first failure keeps
# the existing behavior (node failed, plan pauses); reaching this count adds
# the plain-language escalation record so the owner sees a pattern, not noise.
CONTRACT_FAILURES_ESCALATE = 2


class SatpamRestartError(RuntimeError):
    """An approved restart that cannot proceed; the message is owner-facing."""


# ── durable records (shared with routes/advancers) ─────────────────────────


def record_intervention(
    conn: sqlite3.Connection,
    job_id: int,
    node_id: str | None,
    action: str,
    detection: str,
    status: str,
    reason: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO satpam_interventions(job_id, node_id, action, detection, status, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, node_id, action, detection, status, reason[:1000]),
    )
    return int(cur.lastrowid)


def interventions_payload(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    """The job's satpam timeline, newest first, for the Tasks surfaces."""
    rows = conn.execute(
        "SELECT id, job_id, node_id, action, detection, status, reason, created_at, resolved_at "
        "FROM satpam_interventions WHERE job_id = ? ORDER BY id DESC LIMIT 50",
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_escalation(
    app: Any,
    *,
    job_id: int,
    node_id: str | None,
    detection: str,
    reason: str,
    run_id: int,
    session_id: int,
    project_id: int | None,
) -> None:
    """Record an escalation + its timeline event from outside the loop (the
    worker's continuation-cap path, the graph advancers' repeated contract
    failure). Fail-quiet: the caller's run/plan handling must never break
    because the supervision record could not be written."""
    try:
        with app.state.db_lock:
            record_intervention(
                app.state.worker_db, job_id, node_id,
                ACTION_ESCALATE, detection, STATUS_APPLIED, reason,
            )
        worker = getattr(app.state, "worker", None)
        if worker is not None:
            worker.add_event(
                run_id, session_id, project_id, EVENT_ESCALATED,
                {"job_id": job_id, "node_id": node_id, "detection": detection, "reason": reason},
            )
    except Exception:
        log.exception("satpam: failed to record escalation for job %s (fail-quiet)", job_id)


# ── signal helpers ─────────────────────────────────────────────────────────


def _normalized_output(text: str | None) -> str:
    return " ".join((text or "").lower().split())[:4000]


def output_signature(salvaged: str | None, repo_signature: str | None) -> str:
    """The looping fingerprint: salvaged turn output + repo work state. Two
    consecutive turns with the same fingerprint said the same thing and touched
    the same files - repetition, not progress. Pure hashing, no LLM (T10)."""
    material = _normalized_output(salvaged) + "\0" + (repo_signature or "")
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


class Satpam:
    """The supervision loop. One instance per RunWorker, ticked from its loop."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._last_tick = 0.0
        self._interval = float(app_settings.SATPAM_CHECK_SECONDS_DEFAULT)

    # ── cadence ────────────────────────────────────────────────────────────

    def maybe_tick(self, now: float) -> None:
        """Called every worker-loop iteration; runs a sweep when the cadence is
        due. The cadence setting is re-read once per sweep, not per poll."""
        try:
            if now - self._last_tick < self._interval:
                return
            self._last_tick = now
            with self.app.state.db_lock:
                settings = app_settings.get_satpam_settings(self.app.state.worker_db)
            self._interval = float(settings["check_seconds"])
            self.tick(stall_turns=settings["stall_turns"])
        except Exception:
            log.exception("satpam sweep failed (fail-quiet)")

    def tick(self, stall_turns: int | None = None) -> None:
        """One fleet sweep: evaluate every running job's active continuation
        chain. Jobs without a continuation chain are healthy by definition here
        and are never read, let alone touched."""
        if stall_turns is None:
            with self.app.state.db_lock:
                stall_turns = app_settings.get_satpam_settings(self.app.state.worker_db)["stall_turns"]
        chains = self._active_chains()
        for chain in chains:
            try:
                self._evaluate(chain, stall_turns)
            except Exception:
                log.exception(
                    "satpam: evaluating job %s session %s failed (fail-quiet)",
                    chain.get("job_id"), chain.get("session_id"),
                )

    # ── detection ──────────────────────────────────────────────────────────

    def _active_chains(self) -> list[dict[str, Any]]:
        """Every running job session whose LATEST run is a continuation turn.
        The chain (runs.continued_from_run_id / continuation_count, T5) is the
        durable unit that has turns; a first-turn run has nothing to compare."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            rows = db.execute(
                """
                SELECT s.id AS session_id, s.job_id,
                       j.engine, j.project_id, j.graph,
                       r.id AS latest_run_id, r.status AS latest_run_status,
                       r.continuation_count AS turn, r.continued_from_run_id AS prev_run_id,
                       r.user_id, r.profile_id, r.runner_id, r.model, r.hermes_home
                FROM runs r
                JOIN sessions s ON s.id = r.session_id
                JOIN jobs j ON j.id = s.job_id AND j.status = 'running'
                WHERE r.continuation_count > 0
                  AND r.id = (SELECT MAX(r2.id) FROM runs r2 WHERE r2.session_id = r.session_id)
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def _evaluate(self, chain: dict[str, Any], stall_limit: int) -> None:
        db = self.app.state.worker_db
        session_id = int(chain["session_id"])
        job_id = int(chain["job_id"])
        turn = int(chain["turn"])
        is_graph = chain["engine"] == "graph"

        with self.app.state.db_lock:
            watch = db.execute(
                "SELECT * FROM satpam_watch WHERE session_id = ?", (session_id,)
            ).fetchone()
            last_turn = int(watch["last_turn"]) if watch else 0
            if turn <= last_turn:
                return  # no new turn boundary since the last sweep
            node_id: str | None = None
            touches_repo = False
            if is_graph:
                node = db.execute(
                    "SELECT node_id, status FROM node_states WHERE run_id = ? AND job_id = ?",
                    (chain["latest_run_id"], job_id),
                ).fetchone()
                if not node or node["status"] != "running":
                    return  # a rerun/cancel superseded this chain
                node_id = str(node["node_id"])
                touches_repo = node_touches_repo(chain["graph"] or "", node_id)
            wt = db.execute(
                "SELECT * FROM job_worktrees WHERE job_id = ? AND status = 'active'",
                (job_id,),
            ).fetchone()
            if not is_graph:
                touches_repo = wt is not None
            salvaged = None
            if chain["prev_run_id"]:
                salvage_row = db.execute(
                    "SELECT content FROM messages WHERE run_id = ? AND role = 'assistant' "
                    "ORDER BY id DESC LIMIT 1",
                    (chain["prev_run_id"],),
                ).fetchone()
                salvaged = salvage_row["content"] if salvage_row else None

        # Worktree fingerprint outside the DB lock: two read-only git calls.
        repo_sig: str | None = None
        baseline_sig: str | None = None
        if touches_repo and wt is not None and Path(wt["worktree_path"]).is_dir():
            repo_sig = worktrees.work_signature(wt["worktree_path"])
            baseline_sig = worktrees.fresh_signature(wt["base_commit"])
        loop_sig = output_signature(salvaged, repo_sig)

        with self.app.state.db_lock:
            delta = turn - last_turn
            prev_sig = watch["diff_signature"] if watch else baseline_sig
            stall = 0
            if repo_sig is not None and prev_sig is not None and repo_sig == prev_sig:
                stall = (int(watch["stall_turns"]) if watch else 0) + delta
            prev_loop = watch["output_signature"] if watch else None
            loops = 0
            if prev_loop is not None and loop_sig == prev_loop:
                loops = (int(watch["loop_turns"]) if watch else 0) + delta
            db.execute(
                """
                INSERT INTO satpam_watch(session_id, job_id, node_id, last_turn,
                  diff_signature, stall_turns, output_signature, loop_turns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  job_id = excluded.job_id, node_id = excluded.node_id,
                  last_turn = excluded.last_turn, diff_signature = excluded.diff_signature,
                  stall_turns = excluded.stall_turns, output_signature = excluded.output_signature,
                  loop_turns = excluded.loop_turns, updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, job_id, node_id, turn, repo_sig, stall, loop_sig, loops),
            )
            detection = None
            if loops >= stall_limit:
                detection = DETECTION_LOOPING
            elif touches_repo and stall >= stall_limit:
                detection = DETECTION_STALLED
            if detection is None:
                return
            if chain["latest_run_status"] not in ("queued", "running"):
                return  # the chain just ended; the worker's own paths own it now
            pending = db.execute(
                "SELECT 1 FROM satpam_interventions WHERE job_id = ? AND action = ? AND status = ? LIMIT 1",
                (job_id, ACTION_RESTART, STATUS_PENDING),
            ).fetchone()
            if pending:
                return  # the decision is already with the owner; don't pile on
            steer_count = int(watch["steer_count"]) if watch else 0
            prior_restarts = db.execute(
                "SELECT COUNT(*) AS c FROM satpam_interventions "
                "WHERE job_id = ? AND COALESCE(node_id, '') = COALESCE(?, '') "
                "AND action = ? AND status IN (?, ?)",
                (job_id, node_id, ACTION_RESTART, STATUS_APPLIED, STATUS_APPROVED),
            ).fetchone()["c"]

        # Action ladder: steer once, then restart (auto only for non-repo work),
        # then escalate when even a restart did not help.
        if steer_count == 0:
            self._steer(chain, node_id, detection, stall_limit)
        elif prior_restarts > 0:
            self._escalate_stuck(chain, node_id, detection)
        elif touches_repo:
            self._queue_restart(chain, node_id, detection)
        else:
            self._restart_clean(chain, node_id, detection)

    # ── actions ────────────────────────────────────────────────────────────

    def _emit(self, chain: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        worker = getattr(self.app.state, "worker", None)
        if worker is None:
            return
        worker.add_event(
            int(chain["latest_run_id"]), int(chain["session_id"]),
            chain.get("project_id"), event_type, payload,
        )

    def _steer_note(self, detection: str) -> str:
        if detection == DETECTION_STALLED:
            return (
                "An automated progress check found your recent turns produced NO new "
                "changes in your working tree - you may be stuck. Stop, re-read the "
                "original instruction earlier in this conversation, decide the single "
                "most useful next concrete action, and do it now. If something external "
                "genuinely blocks you, reply starting with 'BLOCKED:' and state exactly "
                "what is missing."
            )
        return (
            "An automated progress check found your recent turns produced nearly "
            "identical output - you appear to be repeating the same work. Stop "
            "repeating. Re-read the original instruction, identify what is actually "
            "still missing, and do only that. If the work is genuinely complete, "
            "produce the final expected output now. If something external blocks you, "
            "reply starting with 'BLOCKED:'."
        )

    def _steer(self, chain: dict[str, Any], node_id: str | None, detection: str, n: int) -> None:
        """Rung a: one corrective prompt into the job's next continuation turn.
        Automatic and logged. If that turn is already queued its prompt is
        amended in place; otherwise the note waits in satpam_watch and the
        worker folds it into the next continuation it builds."""
        db = self.app.state.worker_db
        session_id = int(chain["session_id"])
        note = self._steer_note(detection)
        what = (
            f"No new repo changes for {n} continuation turns in a row"
            if detection == DETECTION_STALLED
            else f"{n} continuation turns in a row produced nearly identical output"
        )
        with self.app.state.db_lock:
            amended = db.execute(
                "UPDATE runs SET prompt = prompt || ? WHERE session_id = ? "
                "AND status = 'queued' AND continuation_count > 0",
                ("\n\n" + wf.steer_block(note), session_id),
            ).rowcount
            db.execute(
                "UPDATE satpam_watch SET steer_pending = ?, steer_count = steer_count + 1, "
                "stall_turns = 0, loop_turns = 0, updated_at = CURRENT_TIMESTAMP "
                "WHERE session_id = ?",
                (None if amended else note, session_id),
            )
            record_intervention(
                db, int(chain["job_id"]), node_id, ACTION_STEER, detection, STATUS_APPLIED,
                f"{what} - steered the agent with a corrective note on its next turn.",
            )
        self._emit(chain, EVENT_STEERED, {
            "job_id": chain["job_id"], "node_id": node_id, "detection": detection,
        })

    def _queue_restart(self, chain: dict[str, Any], node_id: str | None, detection: str) -> None:
        """Rung b for REPO work: restart-clean discards the worktree, so it is
        never automatic - it becomes a pending approval card in Tasks. The job
        keeps its turns meanwhile; the continuation cap stays the backstop."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            record_intervention(
                db, int(chain["job_id"]), node_id, ACTION_RESTART, detection, STATUS_PENDING,
                "Still no progress after a corrective steer. Restarting clean would "
                "DISCARD this job's worktree (all unmerged repo work from this plan) "
                "and re-run its repo work from a fresh cut - approve or dismiss in Tasks.",
            )
        self._emit(chain, EVENT_RESTART_QUEUED, {
            "job_id": chain["job_id"], "node_id": node_id, "detection": detection,
        })

    def _restart_clean(self, chain: dict[str, Any], node_id: str | None, detection: str) -> None:
        """Rung b for NON-repo work: automatic. There is no worktree to lose -
        the stuck attempt is cancelled and the work re-runs from a clean start
        (a fresh node session for plans; step one with fresh context for linear
        jobs). Recorded + surfaced like every intervention."""
        db = self.app.state.worker_db
        job_id = int(chain["job_id"])
        session_id = int(chain["session_id"])
        cancelled: list[dict[str, Any]] = []
        with self.app.state.db_lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                if chain["engine"] == "graph":
                    ok = self._reset_graph_node(db, job_id, node_id, cancelled)
                else:
                    ok = self._reset_linear_job(db, chain, cancelled)
                if not ok:
                    db.execute("COMMIT")
                    return
                db.execute("DELETE FROM satpam_watch WHERE session_id = ?", (session_id,))
                record_intervention(
                    db, job_id, node_id, ACTION_RESTART, detection, STATUS_APPLIED,
                    "Still no progress after a corrective steer - restarted this "
                    "non-repo work fresh (automatic; nothing merged or destructive).",
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        self._finish_cancelled(cancelled)
        self._emit(chain, EVENT_RESTARTED, {
            "job_id": job_id, "node_id": node_id, "detection": detection, "automatic": True,
        })
        if chain["engine"] == "graph":
            self.app.state.worker.graph_executor.dispatch_ready(job_id)

    def _escalate_stuck(self, chain: dict[str, Any], node_id: str | None, detection: str) -> None:
        """Rung c: steer and restart both failed - stop burning turns, cancel
        the chain, and park the plan in review with a plain-language record."""
        db = self.app.state.worker_db
        job_id = int(chain["job_id"])
        session_id = int(chain["session_id"])
        reason = (
            "A corrective steer and a clean restart did not get this job unstuck - "
            "Proxima paused the plan for your review. Look at what the agent produced, "
            "then rerun the job with a sharper instruction, split it into smaller jobs, "
            "or reject it."
        )
        cancelled: list[dict[str, Any]] = []
        with self.app.state.db_lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                cancelled = self._cancel_session_runs(db, session_id)
                if chain["engine"] == "graph" and node_id is not None:
                    row = db.execute(
                        "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
                        (job_id, node_id),
                    ).fetchone()
                    if row and row["status"] == "running":
                        state.guarded_node_transition(
                            db, int(row["id"]), "failed", ("running",), int(row["version"]),
                            run_id=None, error=reason[:1000], mark_finished=True,
                        )
                else:
                    # Re-read inside the transaction: the sweep's snapshot may
                    # predate a step advance, and stale steps must never clobber
                    # newer state.
                    fresh = db.execute(
                        "SELECT steps_state, current_step_idx FROM jobs WHERE id = ? AND status = 'running'",
                        (job_id,),
                    ).fetchone()
                    steps = json.loads(fresh["steps_state"] or "[]") if fresh else []
                    idx = int(fresh["current_step_idx"] or 0) if fresh else -1
                    if 0 <= idx < len(steps):
                        steps[idx]["status"] = "failed"
                        steps[idx]["error"] = reason[:500]
                        steps[idx]["finished_at"] = iso_now()
                        db.execute(
                            "UPDATE jobs SET steps_state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (json.dumps(steps), job_id),
                        )
                state.guarded_transition(
                    db, "jobs", job_id, "review", ("running",),
                    set_extra="updated_at=CURRENT_TIMESTAMP",
                )
                db.execute("DELETE FROM satpam_watch WHERE session_id = ?", (session_id,))
                record_intervention(
                    db, job_id, node_id, ACTION_ESCALATE, detection, STATUS_APPLIED, reason,
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        self._finish_cancelled(cancelled)
        self._emit(chain, EVENT_ESCALATED, {
            "job_id": job_id, "node_id": node_id, "detection": detection, "reason": reason,
        })

    def record_cap_escalation(self, run: dict[str, Any], limit: int, timeout: int) -> None:
        """Confused rung, cap variant: the worker just stopped a job honestly at
        its continuation cap (T5). Add the owner-facing escalation record; the
        job/plan state itself was already handled by the worker's fail path."""
        try:
            db = self.app.state.worker_db
            session_id = int(run["session_id"])
            with self.app.state.db_lock:
                srow = db.execute("SELECT job_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if not srow or not srow["job_id"]:
                    return
                job_id = int(srow["job_id"])
                node = db.execute(
                    "SELECT node_id FROM node_states WHERE run_id = ? AND job_id = ?",
                    (run["id"], job_id),
                ).fetchone()
                db.execute("DELETE FROM satpam_watch WHERE session_id = ?", (session_id,))
            record_escalation(
                self.app,
                job_id=job_id,
                node_id=str(node["node_id"]) if node else None,
                detection=DETECTION_CONFUSED,
                reason=(
                    f"This job used all {limit} automatic continuations without finishing "
                    f"(every turn hit the {timeout}s quota) and stopped honestly. Review "
                    "what it produced, then split the work into smaller jobs, raise the "
                    "turn quota in Settings, or restart it."
                ),
                run_id=int(run["id"]),
                session_id=session_id,
                project_id=run.get("project_id"),
            )
        except Exception:
            log.exception("satpam: cap escalation record failed (fail-quiet)")

    # ── restart mechanics ──────────────────────────────────────────────────

    def _cancel_session_runs(self, db: sqlite3.Connection, session_id: int) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT id, session_id, project_id FROM runs "
            "WHERE session_id = ? AND status IN ('queued', 'running')",
            (session_id,),
        ).fetchall()
        entries = [dict(r) for r in rows]
        for entry in entries:
            db.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status IN ('queued', 'running')",
                (entry["id"],),
            )
        return entries

    def _finish_cancelled(self, cancelled: list[dict[str, Any]]) -> None:
        """Post-commit: signal the live agent (best effort) and mark the runs
        cancelled on the timeline, mirroring the user cancel route."""
        worker = getattr(self.app.state, "worker", None)
        if worker is None:
            return
        for entry in cancelled:
            try:
                worker.cancel(int(entry["id"]))
            except Exception:
                log.exception("satpam: agent cancel signal failed for run %s", entry["id"])
            worker.add_event(
                int(entry["id"]), int(entry["session_id"]), entry.get("project_id"),
                "run.cancelled", {"by": "satpam"},
            )

    def _reset_graph_node(
        self,
        db: sqlite3.Connection,
        job_id: int,
        node_id: str | None,
        cancelled: list[dict[str, Any]],
    ) -> bool:
        """Cancel a node's active attempt and put it back to 'stale' so the
        dispatcher re-runs it in a FRESH session (clean context). Caller holds
        the transaction."""
        if node_id is None:
            return False
        row = db.execute(
            "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
            (job_id, node_id),
        ).fetchone()
        if not row or row["status"] not in ("running", "ready", "review", "failed", "done"):
            return False
        if row["run_id"]:
            srow = db.execute("SELECT session_id FROM runs WHERE id = ?", (row["run_id"],)).fetchone()
            if srow:
                cancelled.extend(self._cancel_session_runs(db, int(srow["session_id"])))
        return state.guarded_node_transition(
            db, int(row["id"]), "stale", (str(row["status"]),), int(row["version"]),
            run_id=None, error=None, output=None,
            clear_started=True, clear_finished=True,
        )

    def _reset_linear_job(
        self,
        db: sqlite3.Connection,
        chain: dict[str, Any],
        cancelled: list[dict[str, Any]],
    ) -> bool:
        """Re-run a linear job from step one with fresh agent context: the
        chain's runs are cancelled, every step resets, and the ACP session
        mapping is dropped so the next run starts clean. Caller holds the
        transaction."""
        job_id = int(chain["job_id"])
        session_id = int(chain["session_id"])
        job = db.execute(
            "SELECT * FROM jobs WHERE id = ? AND status = 'running'", (job_id,)
        ).fetchone()
        if not job:
            return False
        steps = json.loads(job["steps_state"] or "[]")
        if not steps:
            return False
        cancelled.extend(self._cancel_session_runs(db, session_id))
        for step in steps:
            step["status"] = "pending"
            for key in (
                "run_id", "error", "output_summary", "started_at", "finished_at",
                "produced_artifacts", "produced_designs",
            ):
                step.pop(key, None)
        db.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
        inputs = json.loads(job["input"] or "{}")
        prompt = wf.build_step_prompt(steps[0], 0, len(steps), inputs)
        cur = db.execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (
                session_id, chain.get("project_id"), chain["user_id"], chain["profile_id"],
                chain["runner_id"], prompt, chain.get("model"), chain.get("hermes_home"),
            ),
        )
        steps[0]["status"] = "running"
        steps[0]["run_id"] = int(cur.lastrowid)
        steps[0]["started_at"] = iso_now()
        db.execute(
            "UPDATE jobs SET current_step_idx = 0, steps_state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(steps), job_id),
        )
        return True

    def execute_restart(self, job_id: int, intervention_id: int) -> dict[str, Any]:
        """The owner approved a pending restart-clean of a REPO job: discard the
        worktree, cut a fresh one from the repo's current HEAD, and put the
        job's repo work (plus anything built on it) back through the dispatcher.
        Raises SatpamRestartError with an owner-facing reason when refused."""
        db = self.app.state.worker_db
        cancelled: list[dict[str, Any]] = []
        with self.app.state.db_lock:
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            iv = db.execute(
                "SELECT * FROM satpam_interventions WHERE id = ? AND job_id = ?",
                (intervention_id, job_id),
            ).fetchone()
            if not job or not iv or iv["action"] != ACTION_RESTART or iv["status"] != STATUS_PENDING:
                raise SatpamRestartError("this restart is no longer pending")
            if job["status"] not in ("running", "review"):
                raise SatpamRestartError(
                    "the job already ended - dismiss this card and rerun or restart the job itself"
                )
            # The destructive part first: a refused re-cut (dirty repo, detached
            # HEAD) leaves the card pending and changes nothing else.
            try:
                worktrees.recut_job_worktree(db, self.app.state.config, job)
            except worktrees.WorktreeError as exc:
                raise SatpamRestartError(str(exc)) from exc
            db.execute("BEGIN IMMEDIATE")
            try:
                if job["engine"] == "graph":
                    self._reset_graph_repo_work(db, job, str(iv["node_id"]) if iv["node_id"] else None, cancelled)
                else:
                    chain = self._linear_chain_stub(db, job)
                    if chain is None or not self._reset_linear_job(db, chain, cancelled):
                        raise SatpamRestartError("the job has no restartable run chain")
                if job["status"] == "review":
                    state.guarded_transition(
                        db, "jobs", job_id, "running", ("review",),
                        set_extra="updated_at=CURRENT_TIMESTAMP, finished_at=NULL",
                    )
                db.execute(
                    "UPDATE satpam_interventions SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (STATUS_APPROVED, intervention_id),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        self._finish_cancelled(cancelled)
        worker = getattr(self.app.state, "worker", None)
        if worker is not None and cancelled:
            # The restart event rides on the cancelled attempt's timeline - the
            # run the owner was watching when they approved the restart.
            entry = cancelled[0]
            worker.add_event(
                int(entry["id"]), int(entry["session_id"]), entry.get("project_id"),
                EVENT_RESTARTED,
                {"job_id": job_id, "node_id": iv["node_id"], "detection": iv["detection"], "automatic": False},
            )
        if job["engine"] == "graph":
            self.app.state.worker.graph_executor.dispatch_ready(job_id)
        with self.app.state.db_lock:
            return {"satpam": interventions_payload(db, job_id)}

    def _linear_chain_stub(self, db: sqlite3.Connection, job: sqlite3.Row) -> dict[str, Any] | None:
        """Rebuild the chain-shaped context _reset_linear_job needs from the
        job's own session and its most recent run (the execution identity)."""
        if not job["session_id"]:
            return None
        run = db.execute(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (job["session_id"],),
        ).fetchone()
        if not run:
            return None
        return {
            "job_id": job["id"],
            "session_id": job["session_id"],
            "project_id": job["project_id"],
            "user_id": run["user_id"],
            "profile_id": run["profile_id"],
            "runner_id": run["runner_id"],
            "model": run["model"],
            "hermes_home": run["hermes_home"],
        }

    def _reset_graph_repo_work(
        self,
        db: sqlite3.Connection,
        job: sqlite3.Row,
        stuck_node_id: str | None,
        cancelled: list[dict[str, Any]],
    ) -> None:
        """An approved repo restart discarded the plan's worktree, so EVERY
        repo-touching node's work is gone with it - reset them all (and any
        node built on their outputs) to 'stale' so the dispatcher re-runs that
        slice of the plan from the fresh cut. Caller holds the transaction."""
        graph_raw = job["graph"] or ""
        graph = normalize_graph(graph_raw)
        job_id = int(job["id"])
        to_reset: set[str] = set()
        for node in graph["nodes"]:
            if node_touches_repo(graph_raw, str(node["id"])):
                to_reset.add(str(node["id"]))
        if stuck_node_id:
            to_reset.add(stuck_node_id)
        for node_id in list(to_reset):
            to_reset.update(str(d) for d in descendant_node_ids(graph, node_id))
        for node_id in sorted(to_reset):
            self._stale_node(db, job_id, node_id, cancelled)

    def _stale_node(
        self,
        db: sqlite3.Connection,
        job_id: int,
        node_id: str,
        cancelled: list[dict[str, Any]],
    ) -> None:
        row = db.execute(
            "SELECT * FROM node_states WHERE job_id = ? AND node_id = ?",
            (job_id, node_id),
        ).fetchone()
        if not row or row["status"] in ("pending", "stale", "skipped"):
            return
        if row["run_id"]:
            srow = db.execute("SELECT session_id FROM runs WHERE id = ?", (row["run_id"],)).fetchone()
            if srow:
                cancelled.extend(self._cancel_session_runs(db, int(srow["session_id"])))
        state.guarded_node_transition(
            db, int(row["id"]), "stale", (str(row["status"]),), int(row["version"]),
            run_id=None, error=None, output=None,
            clear_started=True, clear_finished=True,
        )

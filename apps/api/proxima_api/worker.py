"""Run worker for the Proxima API.

RunWorker executes agent runs (over ACP), advances autonomous goal loops and
workflow jobs, and finalizes/cancels runs. Self-contained — it reaches shared
state via app.state (config, worker_db, db_lock, hub, acp_manager), not via
create_app closures. EventHub (event_hub.py), goal-loop helpers (goal_loop.py),
artifact scanning (artifacts.py), reaper/watchdog logic (run_reaper.py),
post-run summary/logging helpers (run_summaries.py), and goal/job advancement
(run_advancers.py) were extracted into their own modules.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import subprocess
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .runner_specs import runner_spec
from . import wiki_memory
from . import app_settings
from . import features
from . import state
from .artifacts import artifacts_for_output_links, scan_project_artifacts
from .message_reviews import parse_review_output, review_payload
from .prompt_collaborations import (
    build_brainstorm_synthesis_prompt,
    build_debate_followup_prompt,
    build_debate_rebuttal_prompt,
    build_debate_synthesis_prompt,
    collaboration_card_payload,
    debate_round_role,
    final_header,
    format_final,
    loads_list,
)
from .run_reaper import RunReaper
from .run_summaries import RunSummaries
from .run_advancers import RunAdvancers
from .run_prompting import RunPrompting
from .run_outputs import RunOutputs
from .run_drafts import RunDrafts


class RunWorker:
    def __init__(self, app: FastAPI):
        self.app = app
        self.reaper = RunReaper(app, self._fail_interrupted)
        self.summaries = RunSummaries(app)
        self.advancers = RunAdvancers(app)
        self.prompting = RunPrompting(app)
        self.outputs = RunOutputs(app)
        self.drafts = RunDrafts(app)
        self.task: asyncio.Task | None = None
        self.processes: dict[int, asyncio.subprocess.Process] = {}
        self.active_runs: dict[int, tuple] = {}
        self.run_tasks: dict[int, asyncio.Task] = {}
        self.stop_event = asyncio.Event()

    def start(self) -> None:
        self.task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        for task in list(self.run_tasks.values()):
            task.cancel()
        if self.run_tasks:
            await asyncio.gather(*self.run_tasks.values(), return_exceptions=True)
            self.run_tasks.clear()
        for proc in list(self.processes.values()):
            if proc.returncode is None:
                proc.terminate()

    def _collect_finished_run_tasks(self) -> None:
        for run_id, task in list(self.run_tasks.items()):
            if not task.done():
                continue
            self.run_tasks.pop(run_id, None)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logging.getLogger("proxima.worker").exception("run task %s crashed: %s", run_id, exc)

    async def loop(self) -> None:
        cfg = self.app.state.config
        poll = max(0.05, int(cfg.get("run_worker_poll_interval_ms", 250)) / 1000)
        concurrency = max(1, int(cfg.get("run_worker_concurrency") or 1))
        stale_seconds = int(cfg.get("run_stale_seconds") or 60)
        reap_every = max(5.0, stale_seconds / 2)
        last_reap = 0.0
        while not self.stop_event.is_set():
            try:
                self._collect_finished_run_tasks()
                now = time.monotonic()
                if now - last_reap >= reap_every:
                    last_reap = now
                    self.reap_stale_runs(stale_seconds)
                    self.reap_orphaned_jobs()
                claimed = False
                while len(self.run_tasks) < concurrency:
                    run = self.claim_run()
                    if not run:
                        break
                    run_id = int(run["id"])
                    self.run_tasks[run_id] = asyncio.create_task(self.execute_run(run), name=f"proxima-run-{run_id}")
                    claimed = True
                if not claimed:
                    await asyncio.sleep(poll)
                else:
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"run worker error: {exc}", flush=True)
                await asyncio.sleep(poll)

    def claim_run(self) -> dict[str, Any] | None:
        db = self.app.state.worker_db
        stale_seconds = int(getattr(self.app.state, "config", {}).get("run_stale_seconds") or 60)
        self.reap_stale_run_blockers(stale_seconds)
        with self.app.state.db_lock:
            # Per-session serialization: normal chat runs must not overlap. Prompt
            # collaboration children are the exception: their parent run stays
            # 'running' as the visible busy indicator, while child agent runs fan out
            # behind it and never write raw outputs to the main chat.
            row = db.execute(
                """
                SELECT * FROM runs r WHERE r.status = 'queued'
                  AND (
                    (r.kind LIKE 'collab_%'
                     AND NOT EXISTS (
                       SELECT 1 FROM runs rr
                       WHERE rr.session_id = r.session_id
                         AND rr.status = 'running'
                         AND rr.kind NOT LIKE 'collab_%'
                     ))
                    OR
                    (r.kind NOT LIKE 'collab_%'
                     AND NOT EXISTS (
                       SELECT 1 FROM runs rr
                       WHERE rr.session_id = r.session_id
                         AND rr.status = 'running'
                     ))
                  )
                ORDER BY r.id LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE runs SET status = 'running', started_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
                (row["id"],),
            )
            self.add_event(row["id"], row["session_id"], row["project_id"], "run.started", {"runner": row["runner_id"]})
            return dict(db.execute("SELECT * FROM runs WHERE id = ?", (row["id"],)).fetchone())

    def _reconstruct_text(self, run_id: int) -> str:
        """Rebuild the agent's message from streamed deltas already in the DB —
        so output is never lost even if a run is interrupted before its final save."""
        db = self.app.state.worker_db
        rows = db.execute(
            "SELECT payload FROM events WHERE run_id = ? AND type = 'message.delta' ORDER BY seq",
            (run_id,),
        ).fetchall()
        parts = []
        for r in rows:
            try:
                parts.append(json.loads(r["payload"]).get("text", ""))
            except Exception:
                pass
        return "".join(parts).strip()

    def _agent_name(self, run_id: int) -> str | None:
        return self.summaries.agent_name(run_id)

    def _wiki_root_for_run(self, run: dict[str, Any]) -> Path | None:
        return self.summaries.wiki_root_for_run(run)

    def _autolog_enabled(self, project_id: int | None) -> bool:
        return self.summaries.autolog_enabled(project_id)

    async def _generate_title(self, proc: Any, cwd: str, user_msg: str, assistant_msg: str) -> str:
        return await self.summaries.generate_title(proc, cwd, user_msg, assistant_msg)

    async def _write_auto_log(self, run: dict[str, Any], proc: Any, acp_sid: str) -> None:
        await self.summaries.write_auto_log(run, proc, acp_sid)

    def _fail_interrupted(self, run_id: int, session_id: int, project_id: int | None, reason: str) -> None:
        """Terminally close a run that lost its in-memory state (shutdown / crash /
        stale heartbeat). Salvages chat output, but keeps sidecar review output out of
        the main transcript."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            cur = db.execute("SELECT status, kind FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not cur or cur["status"] != "running":
                return  # already finalized by someone else
            if str(cur["kind"]).startswith("message_review"):
                self._fail_message_review(run_id, session_id, project_id, reason)
                return
            if str(cur["kind"]).startswith("collab_"):
                self._fail_collaboration_run(run_id, session_id, project_id, reason)
                return
            salvaged = self._reconstruct_text(run_id)
            if salvaged:
                db.execute("INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'assistant', ?, ?, ?)", (session_id, salvaged, self._agent_name(run_id), run_id))
            self.add_event(run_id, session_id, project_id, "run.failed", {"error": reason})
            db.execute(
                "UPDATE runs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                (reason, run_id),
            )
            self._revert_task(session_id)
        self._fail_job(session_id, reason)

    def _is_recoverable_agent_history_error(self, exc: Exception) -> bool:
        detail = str(exc)
        return (
            "property_name_above_max_length" in detail
            or "Invalid property name" in detail
            or ("input[" in detail and ".arguments" in detail and "too long" in detail)
        )

    def _revert_task(self, session_id: int) -> None:
        """A failed/cancelled run must not strand its task in 'doing'. Revert it to
        'todo' so the kanban reflects reality (the error stays in the thread).
        Caller holds app.state.db_lock."""
        self.app.state.worker_db.execute(
            "UPDATE tasks SET status = 'todo', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = (SELECT task_id FROM sessions WHERE id = ?) AND status = 'doing'",
            (session_id,),
        )

    def reap_stale_runs(self, stale_seconds: int) -> None:
        self.reaper.reap_stale_runs(stale_seconds)

    def reap_stale_run_blockers(self, stale_seconds: int) -> int:
        return self.reaper.reap_stale_run_blockers(stale_seconds)

    def _mark_job_failed(self, job: sqlite3.Row | dict[str, Any], error: str) -> int:
        return self.reaper.mark_job_failed(job, error)

    def reap_orphaned_jobs(self) -> int:
        return self.reaper.reap_orphaned_jobs()

    async def _heartbeat(self, run_id: int, interval: float) -> None:
        db = self.app.state.worker_db
        try:
            while True:
                await asyncio.sleep(interval)
                with self.app.state.db_lock:
                    db.execute("UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
                    row = db.execute("SELECT collaboration_id FROM runs WHERE id = ?", (run_id,)).fetchone()
                    if row and row["collaboration_id"]:
                        db.execute(
                            "UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = (SELECT parent_run_id FROM prompt_collaborations WHERE id = ?)",
                            (row["collaboration_id"],),
                        )
        except asyncio.CancelledError:
            raise

    def add_event(self, run_id: int, session_id: int, project_id: int | None, event_type: str, payload: dict[str, Any]) -> None:
        db = self.app.state.worker_db
        live = db.execute(
            "SELECT r.status FROM runs r JOIN sessions s ON s.id = r.session_id WHERE r.id = ? AND s.id = ?",
            (run_id, session_id),
        ).fetchone()
        if not live:
            return
        if event_type in {"message.delta", "reasoning.delta", "tool.start", "tool.complete", "approval.request"} and live["status"] != "running":
            return
        seq_row = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events WHERE run_id = ?", (run_id,)).fetchone()
        seq = int(seq_row["next_seq"])
        db.execute(
            "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, session_id, project_id, seq, event_type, json.dumps(payload)),
        )
        self.app.state.hub.notify(session_id)  # wake live streams immediately

    def _advance_goal(self, run: dict[str, Any], answer: str) -> None:
        self.advancers.advance_goal(run, answer, self.add_event)

    def _advance_job(self, run: dict[str, Any], answer: str) -> None:
        self.advancers.advance_job(run, answer, self.add_event, self._produced_artifacts)

    def _complete_message_review(self, run: dict[str, Any], answer: str, stop_reason: str | None) -> bool:
        kind = run.get("kind")
        if kind not in {"message_review", "message_review_merge"}:
            return False
        db = self.app.state.worker_db
        run_id = int(run["id"])
        session_id = int(run["session_id"])
        project_id = run.get("project_id")
        if kind == "message_review_merge":
            with self.app.state.db_lock:
                db.execute(
                    """
                    UPDATE message_reviews
                    SET status = 'done', revised_content = ?, suggested_next_move = 'replace_answer',
                        merge_transcript = ?, error = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE run_id = ?
                    """,
                    (answer, answer, run_id),
                )
                row = db.execute("SELECT * FROM message_reviews WHERE run_id = ?", (run_id,)).fetchone()
                if row:
                    self.add_event(run_id, session_id, project_id, "message_review.completed", {"review": review_payload(row)})
                completed = db.execute(
                    "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                    (run_id,),
                ).rowcount > 0
                if completed:
                    self.add_event(run_id, session_id, project_id, "run.completed", {"stop_reason": stop_reason, "kind": kind})
            return True
        parsed = parse_review_output(answer)
        with self.app.state.db_lock:
            db.execute(
                """
                UPDATE message_reviews
                SET status = 'done', verdict = ?, gaps = ?, depends_on_input = ?, revised_content = ?,
                    suggested_next_move = ?, raw_transcript = ?, error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                """,
                (
                    parsed["verdict"],
                    json.dumps(parsed["gaps"]),
                    json.dumps(parsed["depends_on_input"]),
                    parsed["revised_content"],
                    parsed["suggested_next_move"],
                    parsed["raw_transcript"],
                    run_id,
                ),
            )
            row = db.execute("SELECT * FROM message_reviews WHERE run_id = ?", (run_id,)).fetchone()
            if row:
                self.add_event(run_id, session_id, project_id, "message_review.completed", {"review": review_payload(row)})
            completed = db.execute(
                "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                (run_id,),
            ).rowcount > 0
            if completed:
                self.add_event(run_id, session_id, project_id, "run.completed", {"stop_reason": stop_reason, "kind": kind})
        return True

    def _collaboration_profile(self, profile_id: int) -> dict[str, Any]:
        row = self.app.state.worker_db.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return dict(row) if row else {}

    def _emit_collaboration_child_event(
        self,
        event_type: str,
        run: dict[str, Any] | sqlite3.Row,
        status: str,
        text: str = "",
        error: str | None = None,
        collab: dict[str, Any] | sqlite3.Row | None = None,
        profile: dict[str, Any] | None = None,
    ) -> None:
        kind = str(run["kind"] if isinstance(run, sqlite3.Row) else run.get("kind") or "")
        if not kind.startswith("collab_") or kind in {"collab_brainstorm", "collab_debate"}:
            return
        db = self.app.state.worker_db
        collab_id = run["collaboration_id"] if isinstance(run, sqlite3.Row) else run.get("collaboration_id")
        if not collab_id:
            return
        collab = collab or db.execute("SELECT * FROM prompt_collaborations WHERE id = ?", (collab_id,)).fetchone()
        if not collab:
            return
        profile = profile or self._collaboration_profile(int(run["profile_id"] if isinstance(run, sqlite3.Row) else run.get("profile_id") or 0))
        run_id = int(run["id"] if isinstance(run, sqlite3.Row) else run["id"])
        session_id = int(run["session_id"] if isinstance(run, sqlite3.Row) else run["session_id"])
        project_id = run["project_id"] if isinstance(run, sqlite3.Row) else run.get("project_id")
        role = run["collaboration_role"] if isinstance(run, sqlite3.Row) else run.get("collaboration_role")
        self.add_event(run_id, session_id, project_id, f"collaboration.child.{event_type}", collaboration_card_payload(dict(collab), run_id, profile, role, status, text, error))

    def _debate_round_target(self) -> int:
        return app_settings.get_collaboration_settings(self.app.state.worker_db)["debate_rounds"]

    def _queue_collaboration_run(self, collab: sqlite3.Row, profile: dict[str, Any], prompt: str, kind: str, role: str) -> int:
        db = self.app.state.worker_db
        cur = db.execute(
            """
            INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind, collaboration_id, collaboration_role)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (collab["session_id"], collab["project_id"], collab["user_id"], profile["id"], profile["runner_id"], prompt, profile["default_model"], profile["hermes_home"], kind, collab["id"], role),
        )
        run_id = int(cur.lastrowid)
        ids = [int(x) for x in json.loads(collab["child_run_ids"] or "[]")]
        ids.append(run_id)
        db.execute("UPDATE prompt_collaborations SET child_run_ids = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(ids), collab["id"]))
        self.add_event(run_id, collab["session_id"], collab["project_id"], "run.queued", {"runner": profile["runner_id"], "kind": kind, "collaboration_id": collab["id"], "role": role})
        run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run:
            self._emit_collaboration_child_event("queued", run, "queued", collab=collab, profile=profile)
        return run_id

    def _add_collaboration_progress(self, collab: sqlite3.Row, text: str | None = None) -> None:
        # Heartbeat the parent run (keeps the stale reaper away). Progress prose
        # is optional now — the collaboration cards already show per-agent
        # status, so the parent bubble only streams the final synthesis.
        parent_id = collab["parent_run_id"]
        if not parent_id:
            return
        self.app.state.worker_db.execute("UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE id = ?", (parent_id,))
        if text:
            self.add_event(int(parent_id), collab["session_id"], collab["project_id"], "message.delta", {"text": text})

    def _finish_collaboration(self, collab: sqlite3.Row, outputs: list[dict[str, Any]], synthesis: str, stop_reason: str | None) -> None:
        db = self.app.state.worker_db
        parent_id = int(collab["parent_run_id"])
        final = format_final(collab["mode"], collab["prompt"], outputs, synthesis)
        self.outputs.save_assistant_message(parent_id, collab["session_id"], collab["project_id"], final, collab["mode"].title(), [], self.add_event)
        msg = db.execute("SELECT id FROM messages WHERE run_id = ? ORDER BY id DESC LIMIT 1", (parent_id,)).fetchone()
        # Guarded: a concurrent cancel (chat.py cancel_run) may have set this
        # collaboration terminal — don't overwrite it back to 'done'.
        state.guarded_transition(
            db, "prompt_collaborations", int(collab["id"]), "done",
            state.non_terminal(state.COLLABORATION),
            set_extra="final_message_id = ?, updated_at = CURRENT_TIMESTAMP",
            set_params=(msg["id"] if msg else None,),
        )
        completed = db.execute(
            "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
            (parent_id,),
        ).rowcount > 0
        if completed:
            self.add_event(parent_id, collab["session_id"], collab["project_id"], "run.completed", {"stop_reason": stop_reason, "kind": f"collab_{collab['mode']}"})

    def _complete_collaboration_run(self, run: dict[str, Any], answer: str, stop_reason: str | None) -> bool:
        kind = str(run.get("kind") or "")
        if not kind.startswith("collab_") or kind in {"collab_brainstorm", "collab_debate"}:
            return False
        db = self.app.state.worker_db
        collab_id = run.get("collaboration_id")
        if not collab_id:
            return False
        run_id = int(run["id"])
        session_id = int(run["session_id"])
        project_id = run.get("project_id")
        with self.app.state.db_lock:
            collab = db.execute("SELECT * FROM prompt_collaborations WHERE id = ?", (collab_id,)).fetchone()
            if not collab:
                return False
            profile = self._collaboration_profile(int(run.get("profile_id") or 0))
            outputs = loads_list(collab["child_outputs"])
            outputs = [o for o in outputs if int(o.get("run_id") or 0) != run_id]
            if not kind.endswith("_synthesis"):
                outputs.append({
                    "run_id": run_id,
                    "profile_id": profile.get("id"),
                    "profile_name": profile.get("name") or self._agent_name(run_id) or "Agent",
                    "runner_id": profile.get("runner_id") or run.get("runner_id"),
                    "role": run.get("collaboration_role") or "participant",
                    "content": answer,
                })
                db.execute(
                    "UPDATE prompt_collaborations SET child_outputs = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(outputs), collab_id),
                )
            completed = db.execute(
                "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                (run_id,),
            ).rowcount > 0
            if completed:
                self.add_event(run_id, session_id, project_id, "run.completed", {"stop_reason": stop_reason, "kind": kind, "collaboration_id": collab_id})
                self._emit_collaboration_child_event("completed", run, "done", answer, collab=collab, profile=profile)
            collab = db.execute("SELECT * FROM prompt_collaborations WHERE id = ?", (collab_id,)).fetchone()
            if not collab:
                return True

            if kind == "collab_brainstorm_child":
                # Completeness comes from the live runs table, NOT from the
                # child_run_ids JSON — the request thread may not have persisted
                # that list yet when the first child finishes, and an empty list
                # used to make this conclude "all done" and synthesize after one
                # child. profile_ids is written when the collaboration row is
                # created (before any child is queued), so it is the reliable
                # expected child count: synthesize only once every expected child
                # exists AND none is still in flight.
                expected = len(json.loads(collab["profile_ids"] or "[]"))
                created = db.execute(
                    "SELECT COUNT(*) AS c FROM runs WHERE collaboration_id = ? AND kind = 'collab_brainstorm_child'",
                    (collab_id,),
                ).fetchone()["c"]
                in_flight = db.execute(
                    "SELECT COUNT(*) AS c FROM runs WHERE collaboration_id = ? AND kind = 'collab_brainstorm_child' AND status IN ('queued','running')",
                    (collab_id,),
                ).fetchone()["c"]
                all_children_done = created >= max(expected, 1) and in_flight == 0
                self._add_collaboration_progress(collab)
                if all_children_done and not collab["synthesis_run_id"]:
                    outputs = loads_list(collab["child_outputs"])
                    synth_profile = self._collaboration_profile(int(json.loads(collab["profile_ids"] or "[]")[0]))
                    synth_id = self._queue_collaboration_run(collab, synth_profile, build_brainstorm_synthesis_prompt(collab["prompt"], outputs), "collab_brainstorm_synthesis", "synthesis")
                    db.execute("UPDATE prompt_collaborations SET synthesis_run_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (synth_id, collab_id))
                    # Stream the result header now; the synthesis text follows via
                    # forwarded deltas, so the final message flows in live.
                    self._add_collaboration_progress(collab, final_header(collab["mode"], collab["prompt"]) + "\n\n")
                return True

            if kind.startswith("collab_debate_") and kind != "collab_debate_synthesis":
                profile_ids = [int(x) for x in json.loads(collab["profile_ids"] or "[]")]
                target_rounds = self._debate_round_target()
                debate_outputs = [o for o in outputs if o.get("role") != "synthesis"]
                if len(debate_outputs) < target_rounds:
                    round_number = len(debate_outputs) + 1
                    role = debate_round_role(round_number)
                    next_profile = self._collaboration_profile(profile_ids[(round_number - 1) % max(1, len(profile_ids))])
                    if role == "rebuttal":
                        prompt = build_debate_rebuttal_prompt(collab["prompt"], debate_outputs[-1], next_profile)
                    else:
                        prompt = build_debate_followup_prompt(collab["prompt"], debate_outputs, next_profile, role)
                    self._queue_collaboration_run(collab, next_profile, prompt, f"collab_debate_{role}", role)
                    self._add_collaboration_progress(collab)
                    return True
                synth_profile = self._collaboration_profile(profile_ids[0])
                synth_id = self._queue_collaboration_run(collab, synth_profile, build_debate_synthesis_prompt(collab["prompt"], outputs), "collab_debate_synthesis", "synthesis")
                db.execute("UPDATE prompt_collaborations SET synthesis_run_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (synth_id, collab_id))
                self._add_collaboration_progress(collab, final_header(collab["mode"], collab["prompt"]) + "\n\n")
                return True

            if kind in {"collab_brainstorm_synthesis", "collab_debate_synthesis"}:
                self._finish_collaboration(collab, loads_list(collab["child_outputs"]), answer, stop_reason)
                return True
        return True

    def _fail_collaboration_run(self, run_id: int, session_id: int, project_id: int | None, error: str) -> None:
        db = self.app.state.worker_db
        row = db.execute("SELECT collaboration_id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row or not row["collaboration_id"]:
            return
        collab = db.execute("SELECT * FROM prompt_collaborations WHERE id = ?", (row["collaboration_id"],)).fetchone()
        if not collab:
            return
        state.guarded_transition(
            db, "prompt_collaborations", int(collab["id"]), "failed",
            state.non_terminal(state.COLLABORATION),
            set_extra="error = ?, updated_at = CURRENT_TIMESTAMP", set_params=(error,),
        )
        parent_id = collab["parent_run_id"]
        failed_run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        failed_profile = self._collaboration_profile(int(failed_run["profile_id"] if failed_run else 0))
        siblings = db.execute(
            "SELECT * FROM runs WHERE collaboration_id = ? AND id != ? AND (? IS NULL OR id != ?) AND status IN ('queued','running')",
            (collab["id"], run_id, parent_id, parent_id),
        ).fetchall()
        db.execute(
            "UPDATE runs SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP WHERE collaboration_id = ? AND id != ? AND (? IS NULL OR id != ?) AND status IN ('queued','running')",
            (collab["id"], run_id, parent_id, parent_id),
        )
        for sibling in siblings:
            sid = int(sibling["id"])
            self.add_event(sid, sibling["session_id"], sibling["project_id"], "run.cancelled", {"kind": "collaboration"})
            self._emit_collaboration_child_event("cancelled", sibling, "cancelled", collab=collab)
            self.cancel(sid)
        state.guarded_transition(
            db, "runs", run_id, "failed", ("queued", "running"),
            set_extra="error = ?, finished_at = CURRENT_TIMESTAMP", set_params=(error,),
        )
        if failed_run:
            self._emit_collaboration_child_event("failed", failed_run, "failed", error=error, collab=collab, profile=failed_profile)
        if parent_id:
            db.execute("UPDATE runs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'", (error, parent_id))
            self.add_event(int(parent_id), collab["session_id"], collab["project_id"], "run.failed", {"error": error, "kind": f"collab_{collab['mode']}"})
        self.add_event(run_id, session_id, project_id, "run.failed", {"error": error, "kind": "collaboration"})

    def _fail_message_review(self, run_id: int, session_id: int, project_id: int | None, error: str) -> None:
        db = self.app.state.worker_db
        db.execute(
            "UPDATE message_reviews SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            (error, run_id),
        )
        row = db.execute("SELECT * FROM message_reviews WHERE run_id = ?", (run_id,)).fetchone()
        if row:
            self.add_event(run_id, session_id, project_id, "message_review.failed", {"review": review_payload(row), "error": error})
        self.add_event(run_id, session_id, project_id, "run.failed", {"error": error, "kind": "message_review"})
        db.execute(
            "UPDATE runs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
            (error, run_id),
        )

    def _fail_job(self, session_id: int, error: str) -> None:
        """If the failed run belonged to a workflow job, mark the job + its current
        step failed so it doesn't hang in 'running'."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            srow = db.execute("SELECT job_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not srow or not srow["job_id"]:
                return
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (srow["job_id"],)).fetchone()
            if not job or job["status"] != "running":
                return
            self._mark_job_failed(job, error)

    def _produced_artifacts(self, job: dict[str, Any] | sqlite3.Row, since_iso: str | None) -> list[dict[str, Any]]:
        """What this step produced (files modified since it started), typed for preview."""
        db = self.app.state.worker_db
        if not job["project_id"]:
            return []
        prow = db.execute("SELECT path FROM projects WHERE id = ?", (job["project_id"],)).fetchone()
        if not prow:
            return []
        from datetime import timezone
        ts = since_iso or job["started_at"]
        start = 0.0
        if ts:
            try:
                dt = datetime.fromisoformat(ts)  # iso_now() is ISO-8601; DB timestamps also parse
            except Exception:
                try:
                    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                start = dt.timestamp() - 5
        return scan_project_artifacts(Path(prow["path"]), start)

    async def execute_run(self, run: dict[str, Any]) -> None:
        db = self.app.state.worker_db
        cfg = self.app.state.config
        run_id = int(run["id"])
        session_id = int(run["session_id"])
        project_id = run["project_id"]
        mode_row = db.execute("SELECT mode FROM sessions WHERE id = ?", (session_id,)).fetchone()
        session_mode = (mode_row["mode"] if mode_row else None) or "chat"
        gated_feature = features.queued_run_feature(run, session_mode)
        if gated_feature and not features.enabled(cfg, gated_feature):
            error = f"feature_disabled:{gated_feature}"
            with self.app.state.db_lock:
                db.execute(
                    "UPDATE runs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status IN ('queued', 'running')",
                    (error, run_id),
                )
                self.add_event(run_id, session_id, project_id, "run.failed", features.disabled_payload(gated_feature))
            return
        hermes_home = run["hermes_home"] or ""
        spec = runner_spec(run["runner_id"])
        cwd = str(Path(cfg["workspace_root"]) / "scratch")
        project_name: str | None = None
        project_slug: str | None = None
        project_wiki: Path | None = None
        if project_id:
            row = db.execute("SELECT name, slug, path FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row and row["path"]:
                cwd = row["path"]
                project_name, project_slug = row["name"], row["slug"]
                project_wiki = Path(row["path"]) / "wiki"
        # A workflow job runs at the PROJECT ROOT (so it naturally uses the project's
        # artifacts/design, wiki, and files like any project session). A project-less
        # job gets its own folder under scratch instead of polluting the shared dir.
        jrow = db.execute("SELECT job_id, workflow_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        is_job = bool(jrow and jrow["job_id"])
        is_build = bool(jrow and jrow["workflow_id"])  # a workflow iterate/test chat
        if is_job and not project_id:
            cwd = str(Path(cwd) / "workflow-runs" / f"job-{jrow['job_id']}")
        Path(cwd).mkdir(parents=True, exist_ok=True)

        chunks: list[str] = []
        hb_task: asyncio.Task | None = None

        # Collaboration synthesis streams into the PARENT run's bubble too, so
        # the Brainstorm/Debate result flows in like a normal reply instead of
        # appearing all at once (the header was already streamed at queue time).
        synth_parent_id: int | None = None
        if str(run.get("kind") or "").endswith("_synthesis") and run.get("collaboration_id"):
            crow = db.execute(
                "SELECT parent_run_id FROM prompt_collaborations WHERE id = ?",
                (run["collaboration_id"],),
            ).fetchone()
            if crow and crow["parent_run_id"]:
                synth_parent_id = int(crow["parent_run_id"])

        def on_update(u: dict[str, Any]) -> None:
            kind = u.get("sessionUpdate")
            if kind == "agent_message_chunk":
                text = (u.get("content") or {}).get("text", "")
                if text:
                    chunks.append(text)
                    with self.app.state.db_lock:
                        self.add_event(run_id, session_id, project_id, "message.delta", {"text": text})
                        self._emit_collaboration_child_event("delta", run, "running", text)
                        if synth_parent_id:
                            self.add_event(synth_parent_id, session_id, project_id, "message.delta", {"text": text})
            elif kind == "agent_thought_chunk":
                text = (u.get("content") or {}).get("text", "")
                if text:
                    with self.app.state.db_lock:
                        self.add_event(run_id, session_id, project_id, "reasoning.delta", {"text": text})
            elif kind == "tool_call":
                with self.app.state.db_lock:
                    self.add_event(run_id, session_id, project_id, "tool.start", {"id": u.get("toolCallId"), "title": u.get("title") or u.get("kind") or "tool"})
            elif kind == "tool_call_update" and u.get("status") in ("completed", "failed"):
                with self.app.state.db_lock:
                    self.add_event(run_id, session_id, project_id, "tool.complete", {"id": u.get("toolCallId"), "status": u.get("status")})

        def on_permission(acp_session_id: str, request_id: str, options: list, params: dict[str, Any]) -> None:
            # Agent asked permission for a tool (run a command, edit files, …).
            tc = params.get("toolCall") or {}
            title = tc.get("title") or params.get("title") or "Permission required"
            # Auto-approve (default ON): pick the allow option and resolve immediately,
            # logging an approval.auto event so the activity feed still shows what ran.
            if self._auto_approve_on():
                allow = next((o for o in options if o.get("kind") in ("allow_always", "allow_once")), None)
                allow = allow or (options[0] if options else None)
                if allow and allow.get("optionId"):
                    with self.app.state.db_lock:
                        self.add_event(run_id, session_id, project_id, "approval.auto", {"title": title})
                    self.resolve_permission(run_id, request_id, allow["optionId"])
                    return
            # Otherwise surface an interactive card; the user's pick comes back via
            # POST /api/runs/{id}/permission.
            with self.app.state.db_lock:
                self.add_event(run_id, session_id, project_id, "approval.request", {
                    "request_id": request_id,
                    "title": title,
                    "options": [{"optionId": o.get("optionId"), "name": o.get("name"), "kind": o.get("kind")} for o in options],
                })

        try:
            await self.prompting.refresh_credentials_if_needed(cfg, spec, hermes_home, cwd)
            self.prompting.reapply_capabilities(cfg, spec, hermes_home, run.get("profile_id"))
            proc, acp_sid, fresh_session = await self.prompting.load_or_create_agent_session(
                run_id,
                session_id,
                spec,
                hermes_home,
                cwd,
                self.active_runs,
            )
            hb_task = asyncio.create_task(self._heartbeat(run_id, float(cfg.get("run_heartbeat_seconds") or 10)))
            timeout = int(cfg.get("run_timeout_seconds") or 600)
            # Session kind is authoritative: a design session's runs are always framed as
            # design server-side, so a message that reaches it by any path is handled as a
            # design edit — never misread as a generic request (defense behind the UI gating).
            # Cancel-during-setup guard: a cancel that arrived while we were spawning /
            # loading the agent already flipped runs.status='cancelled'. The post-prompt
            # check below would only catch it AFTER the agent ran a full turn of real
            # work — bail here so the cancel actually short-circuits before the prompt.
            if db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()["status"] == "cancelled":
                return
            if str(run.get("kind", "")).startswith("collab_"):
                with self.app.state.db_lock:
                    self._emit_collaboration_child_event("started", run, "running")
            if str(run.get("kind", "")).startswith("message_review"):
                with self.app.state.db_lock:
                    db.execute("UPDATE message_reviews SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE run_id = ?", (run_id,))
                    row = db.execute("SELECT * FROM message_reviews WHERE run_id = ?", (run_id,)).fetchone()
                    if row:
                        self.add_event(run_id, session_id, project_id, "message_review.started", {"review": review_payload(row)})
            prompt_text = self.prompting.build_prompt_text(
                run,
                session_id,
                project_name,
                project_slug,
                project_wiki,
                is_job,
                is_build,
                jrow,
                session_mode,
                fresh_session,
            )
            run_start_ts = time.time()
            try:
                stop_reason = await proc.prompt(acp_sid, prompt_text, on_update, on_permission=on_permission, timeout=timeout)
            except Exception as exc:
                if not self._is_recoverable_agent_history_error(exc):
                    raise
                proc, acp_sid = await self.prompting.reset_agent_session(
                    run_id,
                    session_id,
                    spec,
                    hermes_home,
                    cwd,
                    acp_sid,
                    self.active_runs,
                    str(exc),
                )
                fresh_session = True
                prompt_text = self.prompting.build_prompt_text(
                    run,
                    session_id,
                    project_name,
                    project_slug,
                    project_wiki,
                    is_job,
                    is_build,
                    jrow,
                    session_mode,
                    True,
                )
                chunks.clear()
                stop_reason = await proc.prompt(acp_sid, prompt_text, on_update, on_permission=on_permission, timeout=timeout)

            status_row = db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if status_row and status_row["status"] == "cancelled":
                return
            answer = "".join(chunks).strip()
            if not answer:
                # Empty output usually means the agent hit an error (auth, rate
                # limit, refusal) — surface the real reason instead of a blank
                # "no output" so it's diagnosable.
                detail = proc.recent_stderr() if hasattr(proc, "recent_stderr") else ""
                answer = f"Agent produced no output (stop reason: {stop_reason})."
                if detail:
                    answer += f"\n\nAgent error log:\n```\n{detail}\n```"
            if self._complete_message_review(run, answer, stop_reason):
                return
            if self._complete_collaboration_run(run, answer, stop_reason):
                return
            if self.drafts.handle_draft_run(run, answer, stop_reason, self.add_event):
                return
            output_links = self.outputs.output_links_for_project(project_id, run_start_ts)
            trow = self.outputs.save_assistant_message(
                run_id,
                session_id,
                project_id,
                answer,
                self._agent_name(run_id),
                output_links,
                self.add_event,
            )
            # Auto-name the chat from a ≤3-word recap on the first exchange (chats
            # only). Done BEFORE run.completed so the sidebar shows the recap as soon
            # as the run leaves the active set. Best-effort; never fails the run.
            try:
                is_task = bool(trow and (trow["task_id"] or trow["job_id"]))
                trow_title = db.execute("SELECT title, manual_title FROM sessions WHERE id = ?", (session_id,)).fetchone()
                is_design = bool(trow_title and (trow_title["title"] or "").startswith("Design: "))
                account = db.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id = ? AND role = 'assistant'", (session_id,)).fetchone()["c"]
                # Skip iterate/test chats (is_build): their '⚙ <workflow>' title is
                # their identity in the Workflows panel — auto-titling would erase it.
                if not is_task and not is_design and not is_build and not (trow_title and trow_title["manual_title"]) and account == 1:
                    title_before = trow_title["title"] if trow_title else ""
                    title = await self._generate_title(proc, cwd, run["prompt"], answer)
                    if title:
                        with self.app.state.db_lock:
                            db.execute(
                                "UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP "
                                "WHERE id = ? AND title = ? AND manual_title = 0",
                                (title, session_id, title_before),
                            )
            except Exception:
                logging.getLogger("proxima.worker").exception("auto-title failed (non-fatal)")
            # Re-catalog the wiki in case the agent wrote/updated notes this run, so
            # the next session's preamble reflects the new knowledge.
            try:
                if project_wiki is not None and project_wiki.is_dir():
                    wiki_memory.rebuild_index(project_wiki)
            except Exception:
                logging.getLogger("proxima.worker").exception("wiki index rebuild failed (non-fatal)")
            # Finalize atomically: only complete a run that's still 'running'. If the
            # user cancelled during the post-prompt awaits (auto-title can take ~30s),
            # the cancel set status='cancelled' — don't resurrect it to 'completed',
            # and crucially don't advance the goal/job loop (which would keep the agent
            # working after the user pressed Cancel).
            with self.app.state.db_lock:
                completed = db.execute(
                    "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                    (run_id,),
                ).rowcount > 0
                if completed:
                    self.add_event(run_id, session_id, project_id, "run.completed", {"stop_reason": stop_reason})
            if not completed:
                return  # cancelled mid-window — stop here; do not advance goal/job
            # Autonomous goal loop: enqueue the next turn or finish the goal. Best-effort.
            try:
                self._advance_goal(run, answer)
            except Exception:
                logging.getLogger("proxima.worker").exception("goal advance failed (non-fatal)")
            # Workflow job: record this step's output and enqueue the next (or finish). Best-effort.
            try:
                self._advance_job(run, answer)
            except Exception:
                logging.getLogger("proxima.worker").exception("job advance failed (non-fatal)")
            # Layer 1: append a one-line memory-log entry for this turn. Best-effort —
            # a logging failure must never fail the user's actual run.
            if answer and not answer.startswith("Agent produced no output"):
                try:
                    await self._write_auto_log(run, proc, acp_sid)
                except Exception:
                    logging.getLogger("proxima.worker").exception("auto-log failed (non-fatal)")
        except asyncio.CancelledError:
            # Graceful shutdown: close the run cleanly (salvaging streamed text)
            # instead of leaving it orphaned in 'running'.
            self._fail_interrupted(run_id, session_id, project_id, "Interrupted by server shutdown")
            raise
        except asyncio.TimeoutError:
            # Abort the agent's turn so it stops working in the background and the
            # next message isn't "queued for the next turn" against a busy session.
            # session/cancel is best-effort and a turn wedged inside a blocking
            # tool call can't process it, so also recycle (kill) the cached agent
            # process — otherwise the wedged session is reused and every later
            # message in this project returns "Queued for the next turn".
            entry = self.active_runs.get(run_id)
            if entry:
                try: entry[0].cancel(entry[1])
                except Exception: pass
            try:
                await self.app.state.acp_manager.recycle(spec, hermes_home, cwd)
            except Exception:
                logging.getLogger("proxima.worker").exception("failed to recycle agent process after timeout")
            with self.app.state.db_lock:
                failed = db.execute(
                    "UPDATE runs SET status = 'failed', error = 'Hermes runner timed out', finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                    (run_id,),
                ).rowcount > 0
                if not failed:
                    return
                if str(run.get("kind", "")).startswith("message_review"):
                    self._fail_message_review(run_id, session_id, project_id, "Hermes runner timed out")
                    return
                if str(run.get("kind", "")).startswith("collab_"):
                    self._fail_collaboration_run(run_id, session_id, project_id, "Hermes runner timed out")
                    return
                salvaged = self._reconstruct_text(run_id)
                if salvaged:
                    db.execute("INSERT INTO messages(session_id, role, content, author, run_id) VALUES (?, 'assistant', ?, ?, ?)", (session_id, salvaged, self._agent_name(run_id), run_id))
                self.add_event(run_id, session_id, project_id, "run.failed", {"error": "Hermes runner timed out"})
                self._revert_task(session_id)
            self._fail_job(session_id, "Hermes runner timed out")
        except Exception as exc:
            detail = str(exc)[-2000:]
            with self.app.state.db_lock:
                failed = db.execute(
                    "UPDATE runs SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
                    (detail, run_id),
                ).rowcount > 0
                if not failed:
                    return
                if str(run.get("kind", "")).startswith("message_review"):
                    self._fail_message_review(run_id, session_id, project_id, detail)
                    return
                if str(run.get("kind", "")).startswith("collab_"):
                    self._fail_collaboration_run(run_id, session_id, project_id, detail)
                    return
                cur = db.execute("INSERT INTO messages(session_id, role, content) VALUES (?, 'error', ?)", (session_id, f"Run failed: {detail}"))
                self.add_event(run_id, session_id, project_id, "message.complete", {"message_id": cur.lastrowid, "text": f"Run failed: {detail}"})
                self.add_event(run_id, session_id, project_id, "run.failed", {"error": detail})
                self._revert_task(session_id)
            self._fail_job(session_id, detail)
        finally:
            if hb_task:
                hb_task.cancel()
                with suppress(asyncio.CancelledError):
                    await hb_task
            self.active_runs.pop(run_id, None)

    def cancel(self, run_id: int) -> None:
        entry = self.active_runs.get(run_id)
        if entry:
            proc, sid = entry
            proc.cancel(sid)

    def _auto_approve_on(self) -> bool:
        """Global 'bypass agent permission prompts' toggle. Default ON (unset ⇒ on).
        Fail-safe: if the setting can't be read, DON'T auto-approve (surface the card)."""
        try:
            with self.app.state.db_lock:
                return app_settings.get_setting(self.app.state.worker_db, "auto_approve_permissions", "1") != "0"
        except Exception:
            return False

    def resolve_permission(self, run_id: int, request_id: str, option_id: str) -> bool:
        entry = self.active_runs.get(run_id)
        if not entry:
            return False
        proc, _sid = entry
        return bool(proc.resolve_permission(request_id, option_id))

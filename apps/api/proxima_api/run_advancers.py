"""Goal-loop and workflow-job continuation helpers for RunWorker."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from . import workflows as wf
from .auth import iso_now
from .goal_loop import build_goal_prompt, parse_goal_status

AddEvent = Callable[[int, int, int | None, str, dict[str, Any]], None]
ProducedArtifacts = Callable[[Any, str | None], list[dict[str, Any]]]


class RunAdvancers:
    def __init__(self, app: Any) -> None:
        self.app = app

    def advance_goal(self, run: dict[str, Any], answer: str, add_event: AddEvent) -> None:
        """After a goal-mode turn, enqueue the next turn or finish the goal.
        Self-perpetuating: each continuation is a fresh queued run in the SAME
        ACP session (same profile/runner), so the agent keeps its context."""
        db = self.app.state.worker_db
        session_id = int(run["session_id"])
        with self.app.state.db_lock:
            s = db.execute(
                "SELECT goal_text, goal_status, goal_iteration, goal_max FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not s or s["goal_status"] != "running" or not s["goal_text"]:
                return
            objective = s["goal_text"]
            iteration = (s["goal_iteration"] or 0) + 1
            max_iter = s["goal_max"] or 20
            status = parse_goal_status(answer)
            if status == "DONE":
                new_status = "done"
            elif status == "BLOCKED":
                new_status = "blocked"
            elif iteration >= max_iter:
                new_status = "capped"
            else:
                new_status = "running"
            db.execute("UPDATE sessions SET goal_iteration = ?, goal_status = ? WHERE id = ?", (iteration, new_status, session_id))
            add_event(
                int(run["id"]),
                session_id,
                run.get("project_id"),
                "goal.update",
                {"status": new_status, "iteration": iteration, "max": max_iter, "objective": objective},
            )
            if new_status == "running":
                cur = db.execute(
                    "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
                    "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (
                        session_id,
                        run.get("project_id"),
                        run["user_id"],
                        run["profile_id"],
                        run["runner_id"],
                        build_goal_prompt(objective, False),
                        run.get("model"),
                        run.get("hermes_home"),
                    ),
                )
                add_event(int(cur.lastrowid), session_id, run.get("project_id"), "run.queued", {"runner": run["runner_id"], "goal": True})

    def advance_job(
        self,
        run: dict[str, Any],
        answer: str,
        add_event: AddEvent,
        produced_artifacts: ProducedArtifacts,
    ) -> None:
        """After a workflow-job step run completes: record its output, then either
        enqueue the next step (same session/profile, so context carries) or move the
        job to 'review' when the last step is done. Mirrors advance_goal but driven
        by a fixed step list instead of agent self-judgment."""
        db = self.app.state.worker_db
        session_id = int(run["session_id"])
        with self.app.state.db_lock:
            srow = db.execute("SELECT job_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not srow or not srow["job_id"]:
                return
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (srow["job_id"],)).fetchone()
            if not job or job["status"] != "running":
                return
            steps = json.loads(job["steps_state"] or "[]")
            idx = int(job["current_step_idx"])
            if idx >= len(steps):
                return
            # A step the agent reports it can't do (BLOCKED: contract) or that produced
            # nothing (auth/rate-limit/refusal sentinel) must NOT be recorded as success
            # and built upon — fail the job so the real problem surfaces.
            if answer.lstrip().upper().startswith("BLOCKED:") or answer.startswith("Agent produced no output"):
                steps[idx]["status"] = "failed"
                steps[idx]["error"] = answer[:600]
                steps[idx]["finished_at"] = iso_now()
                db.execute(
                    "UPDATE jobs SET status='failed', steps_state=?, finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(steps), job["id"]),
                )
                add_event(int(run["id"]), session_id, run.get("project_id"), "job.update", {"status": "failed", "step": idx, "reason": "blocked"})
                return
            steps[idx]["status"] = "done"
            steps[idx]["output_summary"] = answer
            steps[idx]["finished_at"] = iso_now()
            try:
                arts = produced_artifacts(job, steps[idx].get("started_at"))
                if arts:
                    steps[idx]["produced_artifacts"] = arts
                    designs = [{"id": a["id"], "title": a["title"]} for a in arts if a["type"] == "design"]
                    if designs:
                        steps[idx]["produced_designs"] = designs
            except Exception:
                logging.getLogger("proxima.worker").exception("produced-artifact scan failed (non-fatal)")
            # Pause for the human if this was the LAST step (final review) or this
            # step is a mid-workflow review gate. Either way -> 'review'; the human
            # resumes via /approve (which enqueues the next step when one remains).
            last = idx + 1 >= len(steps)
            gate = bool(steps[idx].get("review_required"))
            if last or gate:
                db.execute(
                    "UPDATE jobs SET status = 'review', steps_state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(steps), job["id"]),
                )
                add_event(int(run["id"]), session_id, run.get("project_id"), "job.update", {"status": "review", "step": idx, "gate": gate})
                return
            inputs = json.loads(job["input"] or "{}")
            nxt = idx + 1
            prompt = wf.build_step_prompt(steps[nxt], nxt, len(steps), inputs)
            cur = db.execute(
                "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (
                    session_id,
                    run.get("project_id"),
                    run["user_id"],
                    run["profile_id"],
                    run["runner_id"],
                    prompt,
                    run.get("model"),
                    run.get("hermes_home"),
                ),
            )
            steps[nxt]["status"] = "running"
            steps[nxt]["run_id"] = int(cur.lastrowid)
            steps[nxt]["started_at"] = iso_now()
            db.execute(
                "UPDATE jobs SET current_step_idx = ?, steps_state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (nxt, json.dumps(steps), job["id"]),
            )
            add_event(int(cur.lastrowid), session_id, run.get("project_id"), "run.queued", {"runner": run["runner_id"], "job": job["id"]})

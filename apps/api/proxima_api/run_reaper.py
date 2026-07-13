"""Watchdog/reaper helpers for RunWorker.

RunReaper owns stale-run/stale-job cleanup paths. It reaches shared state through
app.state, like RunWorker, but delegates terminal run failure back to the worker
so output salvage and event emission stay centralized.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from .auth import iso_now


class RunReaper:
    def __init__(self, app: Any, fail_interrupted: Callable[[int, int, int | None, str], None]) -> None:
        self.app = app
        self._fail_interrupted = fail_interrupted

    def _actively_running(self) -> set[int]:
        """Run ids THIS worker is still executing (a live asyncio task). A stale
        heartbeat on one of these means the loop is busy/slow, NOT that the run
        died — reaping it would kill a live run mid-flight. Only runs with no live
        task (orphaned by a crash/restart) are genuine reap targets."""
        worker = getattr(self.app.state, "worker", None)
        if worker is None:
            return set()
        return {rid for rid, task in list(worker.run_tasks.items()) if not task.done()}

    def reap_stale_runs(self, stale_seconds: int) -> None:
        """Mark runs whose worker stopped checking in (crash without a clean
        restart) as failed — the watchdog for hangs. Skips runs this worker is
        still actively executing so a busy event loop can't false-positive them."""
        db = self.app.state.worker_db
        alive = self._actively_running()
        with self.app.state.db_lock:
            stale = db.execute(
                f"SELECT id, session_id, project_id FROM runs WHERE status = 'running' "
                f"AND (heartbeat_at IS NULL OR heartbeat_at < datetime('now', '-{int(stale_seconds)} seconds'))"
            ).fetchall()
            rows = [dict(r) for r in stale if r["id"] not in alive]
        for r in rows:
            self._fail_interrupted(r["id"], r["session_id"], r["project_id"], "Run stalled (no heartbeat)")

    def reap_stale_run_blockers(self, stale_seconds: int) -> int:
        """Fail stale running runs that are blocking a queued turn in the same session.
        Skips runs this worker is still executing (alive but slow) — the queue waits
        rather than killing a live run."""
        db = self.app.state.worker_db
        alive = self._actively_running()
        with self.app.state.db_lock:
            stale = db.execute(
                f"""
                SELECT r.id, r.session_id, r.project_id FROM runs r
                WHERE r.status = 'running'
                  AND (r.heartbeat_at IS NULL OR r.heartbeat_at < datetime('now', '-{int(stale_seconds)} seconds'))
                  AND EXISTS (
                    SELECT 1 FROM runs q
                    WHERE q.session_id = r.session_id
                      AND q.status = 'queued'
                  )
                """
            ).fetchall()
            rows = [dict(r) for r in stale if r["id"] not in alive]
        for r in rows:
            self._fail_interrupted(r["id"], r["session_id"], r["project_id"], "Run stalled (no heartbeat)")
        return len(rows)

    def mark_job_failed(self, job: sqlite3.Row | dict[str, Any], error: str) -> int:
        """Mark a running job and its current step failed. Caller holds db_lock."""
        db = self.app.state.worker_db
        steps = json.loads(job["steps_state"] or "[]")
        idx = int(job["current_step_idx"])
        if 0 <= idx < len(steps) and steps[idx].get("status") not in {"done", "failed"}:
            steps[idx]["status"] = "failed"
            steps[idx]["error"] = (error or "")[:500]
            steps[idx]["finished_at"] = iso_now()
        cur = db.execute(
            "UPDATE jobs SET status='failed', steps_state=?, finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
            (json.dumps(steps), job["id"]),
        )
        return int(cur.rowcount or 0)

    def reap_orphaned_jobs(self) -> int:
        """Fail running jobs that no longer have a queued/running run to advance them."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            rows = db.execute(
                """
                SELECT * FROM jobs j
                WHERE j.status = 'running'
                  AND NOT EXISTS (
                    SELECT 1 FROM runs r
                    WHERE r.session_id = j.session_id
                      AND r.status IN ('queued', 'running')
                  )
                """
            ).fetchall()
            failed = 0
            for job in rows:
                failed += self.mark_job_failed(job, "Job stalled (no active run)")
            return failed

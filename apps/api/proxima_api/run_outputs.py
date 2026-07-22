"""Output-link discovery and assistant-message persistence for RunWorker."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import artifact_registry
from .artifacts import artifacts_for_output_links, scan_project_artifacts, update_produced_artifacts

AddEvent = Callable[[int, int, int | None, str, dict[str, Any]], None]


class RunOutputs:
    def __init__(self, app: Any) -> None:
        self.app = app

    def output_links_for_project(self, project_id: int | None, run_start_ts: float) -> list[dict[str, Any]]:
        """Scan project files touched by this run and normalize them for chat cards."""
        if not project_id:
            return []
        db = self.app.state.worker_db
        try:
            prow = db.execute("SELECT path, slug FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not prow:
                return []
            fresh = scan_project_artifacts(Path(prow["path"]), run_start_ts - 5)
            return artifacts_for_output_links(fresh, prow["slug"])
        except Exception:
            logging.getLogger("proxima.worker").exception("chat artifact scan failed (non-fatal)")
            return []

    def save_assistant_message(
        self,
        run_id: int,
        session_id: int,
        project_id: int | None,
        answer: str,
        author: str | None,
        output_links: list[dict[str, Any]],
        add_event: AddEvent,
    ) -> Any:
        """Persist the final assistant message, track run artifacts on the session,
        and move linked tasks to review. Returns the session task/job row used by
        downstream auto-title logic."""
        db = self.app.state.worker_db
        with self.app.state.db_lock:
            cur = db.execute(
                "INSERT INTO messages(session_id, role, content, author, run_id, output_links) VALUES (?, 'assistant', ?, ?, ?, ?)",
                (session_id, answer, author, run_id, json.dumps(output_links)),
            )
            db.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
            add_event(run_id, session_id, project_id, "message.complete", {"message_id": cur.lastrowid, "text": answer, "output_links": output_links})
            # Attribute artifacts THIS run produced to the session. For normal chat
            # this powers result cards/back-navigation; for iterate sessions it keeps
            # the panggung Result scoped to the iteration's own output.
            if output_links:
                try:
                    def _merge(current: list) -> list:
                        merged = {(a.get("type"), a.get("path")): a for a in current if isinstance(a, dict)}
                        for item in output_links:
                            merged[(item["type"], item.get("path"))] = item
                        return list(merged.values())
                    update_produced_artifacts(db, session_id, _merge)
                except Exception:
                    logging.getLogger("proxima.worker").exception("session artifact track failed (non-fatal)")
                # Durable registry (T4): the same scan feeds the deliverable
                # records - the scanner discovers, the registry remembers.
                try:
                    artifact_registry.record_run_outputs(db, run_id, session_id, project_id, output_links)
                except Exception:
                    logging.getLogger("proxima.worker").exception("artifact registry feed failed (non-fatal)")
            trow = db.execute("SELECT job_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return trow

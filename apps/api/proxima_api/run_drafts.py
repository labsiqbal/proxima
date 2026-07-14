"""Special draft-run finalization for RunWorker.execute_run."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import wiki_memory
from . import workflows as wf

AddEvent = Callable[[int, int, int | None, str, dict[str, Any]], None]


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


class RunDrafts:
    def __init__(self, app: Any) -> None:
        self.app = app

    def handle_draft_run(
        self,
        run: dict[str, Any],
        answer: str,
        stop_reason: str | None,
        add_event: AddEvent,
    ) -> bool:
        """Finalize wiki/workflow draft runs.

        Returns True when this was a special draft run and normal chat message
        persistence should stop.
        """
        kind = run.get("kind", "chat")
        if kind not in {"wiki_draft", "workflow_draft", "workflow_graph_draft"}:
            return False

        db = self.app.state.worker_db
        run_id = _as_int(run["id"])
        session_id = _as_int(run["session_id"])
        project_id = run.get("project_id")

        if kind == "wiki_draft":
            # A Save-to-wiki draft turn: emit the parsed note as an event for the
            # preview modal instead of saving a chat message or moving a task.
            draft = wiki_memory.parse_note_draft(answer)
            event_type = "wiki.draft"
        else:
            # Promote-to-workflow turn: parse the agent's blueprint JSON into an
            # unsaved workflow draft and emit it as an event for the editor to open.
            try:
                draft = wf.parse_blueprint(answer)
            except Exception as exc:
                draft = {"error": f"Could not parse a workflow from this chat: {exc}"}
            event_type = "workflow.draft"

        with self.app.state.db_lock:
            add_event(run_id, session_id, project_id, event_type, draft)
            add_event(run_id, session_id, project_id, "run.completed", {"stop_reason": stop_reason})
            db.execute("UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
        return True

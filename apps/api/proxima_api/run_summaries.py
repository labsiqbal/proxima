"""Post-run summary, title, and wiki auto-log helpers for RunWorker."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import wiki_memory


class RunSummaries:
    def __init__(self, app: Any) -> None:
        self.app = app

    def agent_name(self, run_id: int) -> str | None:
        """Display name for the agent that produced a run = its profile's name."""
        db = self.app.state.worker_db
        row = db.execute(
            "SELECT pr.name FROM runs r LEFT JOIN profiles pr ON pr.id = r.profile_id WHERE r.id = ?",
            (run_id,),
        ).fetchone()
        return row["name"] if row and row["name"] else None

    def wiki_root_for_run(self, run: dict[str, Any]) -> Path | None:
        """The project's wiki. Wiki is project-scoped — a project-less (ad-hoc)
        chat has no wiki and is not logged."""
        if not run["project_id"]:
            return None
        db = self.app.state.worker_db
        row = db.execute("SELECT path FROM projects WHERE id = ?", (run["project_id"],)).fetchone()
        if row and row["path"]:
            return Path(row["path"]) / "wiki"
        return None

    def autolog_enabled(self, project_id: int | None) -> bool:
        if project_id is None:
            return True
        db = self.app.state.worker_db
        row = db.execute("SELECT value FROM app_settings WHERE key = ?", (f"project:{project_id}:wiki_autolog",)).fetchone()
        return (row["value"] if row else "on") != "off"

    async def generate_title(self, proc: Any, cwd: str, user_msg: str, assistant_msg: str) -> str:
        """Ask the agent (in a throwaway ACP session so the chat isn't polluted)
        for a ≤3-word Title-Case recap of the first exchange."""
        chunks: list[str] = []

        def on_u(u: dict[str, Any]) -> None:
            if u.get("sessionUpdate") == "agent_message_chunk":
                chunks.append((u.get("content") or {}).get("text", ""))

        convo = f"User: {user_msg[:600]}\nAssistant: {assistant_msg[:600]}"
        prompt = (
            "Give a short title for this chat: AT MOST 3 words, Title Case, no quotes, "
            "no punctuation, no preamble — output only the title.\n\n" + convo
        )
        sid = await proc.new_session(cwd)
        await proc.prompt(sid, prompt, on_u, timeout=30)
        title_text = "".join(chunks).strip().strip('"\'')
        raw = title_text.splitlines()[0] if title_text.splitlines() else ""
        words = raw.replace("\n", " ").split()
        return " ".join(words[:3])[:48]

    async def write_auto_log(self, run: dict[str, Any], proc: Any, acp_sid: str) -> None:
        """Best-effort: summarize the just-finished turn and append to the log."""
        db = self.app.state.worker_db
        if not self.autolog_enabled(run["project_id"]):
            return
        root = self.wiki_root_for_run(run)
        if root is None:
            return
        sum_chunks: list[str] = []

        def _on_sum(u: dict[str, Any]) -> None:
            if u.get("sessionUpdate") == "agent_message_chunk":
                t = (u.get("content") or {}).get("text", "")
                if t:
                    sum_chunks.append(t)

        await proc.prompt(acp_sid, wiki_memory.SUMMARIZE_PROMPT, _on_sum, timeout=60)
        summary = "".join(sum_chunks).strip()
        if not summary:
            return
        urow = db.execute("SELECT username FROM users WHERE id = ?", (run["user_id"],)).fetchone()
        author = urow["username"] if urow else "agent"
        wiki_memory.append_log_entry(root, datetime.now(), author, summary, None)

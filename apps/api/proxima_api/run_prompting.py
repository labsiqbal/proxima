"""ACP session setup and prompt framing helpers for RunWorker.execute_run."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import wiki_memory
from . import workflows as wf
from . import features
from .capabilities import apply_capabilities, parse_selection
from .profile_seed import refresh_agent_credentials


class RunPrompting:
    def __init__(self, app: Any) -> None:
        self.app = app

    def reapply_capabilities(self, cfg: dict[str, Any], spec: Any, hermes_home: str, profile_id: Any) -> None:
        """Re-activate the run's profile skill/MCP selection into its home before the
        run. Idempotent (symlinks/config write) and self-healing: newly installed host
        skills show up, and profiles created before this feature get their selection
        applied. Live-home claude is a no-op (home already IS the host config)."""
        if not hermes_home or profile_id in (None, 0):
            return
        if cfg.get("claude_live_home") and getattr(spec, "id", "") == "claude-code":
            return
        try:
            row = self.app.state.worker_db.execute(
                "SELECT capabilities FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            selection = parse_selection(row["capabilities"] if row else None)
            override = cfg.get("source_hermes_home") if getattr(spec, "id", "") == "hermes" else None
            apply_capabilities(spec, Path(hermes_home), selection, override)
        except Exception:
            logging.getLogger("proxima.worker").exception("capability re-apply failed (non-fatal)")

    async def refresh_credentials_if_needed(self, cfg: dict[str, Any], spec: Any, hermes_home: str, cwd: str) -> None:
        """Refresh runner auth files before a run and recycle stale cached agents."""
        # Keep this profile's credentials current: a copy made at account
        # creation goes stale when the host rotates its OAuth token, which
        # shows up as the agent producing "no output". Refresh the runner's
        # auth files from the host before each run so shared-account profiles
        # keep working (applies to any runner with refresh_files).
        if spec.refresh_files and hermes_home and cfg.get("refresh_credentials", True):
            try:
                if spec.id == "hermes":
                    src = Path(cfg.get("source_hermes_home") or os.path.expanduser("~/.hermes"))
                else:
                    src = Path(os.path.expanduser(spec.source_dir)) if spec.source_dir else Path("/nonexistent")
                changed = refresh_agent_credentials(src, Path(hermes_home), spec.refresh_files)
                if changed:
                    # A cached agent process holds the old auth in memory; drop
                    # it so the next get() spawns one that reads the fresh token.
                    await self.app.state.acp_manager.recycle(spec, hermes_home, cwd)
            except Exception:
                logging.getLogger("proxima.worker").exception("agent credential refresh failed")

    async def load_or_create_agent_session(
        self,
        run_id: int,
        session_id: int,
        spec: Any,
        hermes_home: str,
        cwd: str,
        active_runs: dict[int, tuple[Any, str]],
    ) -> tuple[Any, str, bool]:
        """Get an ACP process and a per-home ACP session for this Proxima session."""
        db = self.app.state.worker_db
        proc = await self.app.state.acp_manager.get(spec, hermes_home, cwd)
        # ACP sessions are home-specific: look up THIS home's session for the
        # thread (each collaborator has their own). Loading another home's id
        # silently fails on the agent side -> prompt to a missing session ->
        # "no output". Per-home mapping avoids that.
        arow = db.execute(
            "SELECT acp_session_id FROM agent_sessions WHERE session_id = ? AND hermes_home = ?",
            (session_id, hermes_home),
        ).fetchone()
        acp_sid = arow["acp_session_id"] if arow else None
        if acp_sid:
            try:
                await proc.load_session(acp_sid, cwd)
            except Exception:
                acp_sid = None  # stale/unknown session -> start fresh
        fresh_session = False
        if not acp_sid:
            acp_sid = await proc.new_session(cwd)
            fresh_session = True
            with self.app.state.db_lock:
                db.execute(
                    "INSERT OR REPLACE INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (?, ?, ?)",
                    (session_id, hermes_home, acp_sid),
                )
        active_runs[run_id] = (proc, acp_sid)
        return proc, acp_sid, fresh_session

    def build_prompt_text(
        self,
        run: dict[str, Any],
        session_id: int,
        project_name: str | None,
        project_slug: str | None,
        project_wiki: Path | None,
        is_job: bool,
        is_build: bool,
        jrow: Any,
        session_mode: str,
        is_fresh_session: bool,
    ) -> str:
        db = self.app.state.worker_db
        cfg = self.app.state.config
        include_design_studio = features.enabled(cfg, features.DESIGN_STUDIO)
        include_video = features.enabled(cfg, features.VIDEO)
        prompt_text = run["prompt"]
        if is_fresh_session and run.get("kind", "chat") != "wiki_draft":
            try:
                # Per-profile instructions (the profile's "soul"/AGENTS.md): prepend
                # on the first turn so they steer the whole session.
                prow = db.execute(
                    "SELECT p.instructions FROM sessions s JOIN profiles p ON p.id = s.profile_id WHERE s.id = ?",
                    (session_id,),
                ).fetchone()
                instr = (prow["instructions"] if prow else None) or ""
                if instr.strip():
                    prompt_text = f"# Profile instructions\n\n{instr.strip()}\n\n---\n\n" + prompt_text
                # Generate the catalog on first sight so the preamble can point at it.
                if project_wiki is not None and project_wiki.is_dir() and not (project_wiki / "index.md").exists():
                    wiki_memory.rebuild_index(project_wiki)
                preamble = wiki_memory.build_run_preamble(
                    project_name,
                    project_slug,
                    project_wiki,
                    include_design_studio=include_design_studio,
                    include_video=include_video,
                )
                if preamble:
                    prompt_text = preamble + "\n\n---\n\n" + prompt_text
                # Workflow steps additionally get a "proxima capabilities" brief so the
                # agent can decide to produce a real Design Studio design, use project
                # files, etc. — straight from the step's instruction (AI auto-detects).
                if is_job:
                    prompt_text = wf.build_capability_preamble(
                        include_design_studio=include_design_studio,
                        include_video=include_video,
                    ) + "\n\n---\n\n" + prompt_text
                elif is_build:
                    wfb = db.execute("SELECT name, steps FROM workflows WHERE id = ?", (jrow["workflow_id"],)).fetchone()
                    if wfb:
                        prompt_text = (
                            wf.build_iteration_preamble(
                                wfb["name"],
                                json.loads(wfb["steps"] or "[]"),
                                include_design_studio=include_design_studio,
                            )
                            + "\n"
                            + wf.build_capability_preamble(
                                include_design_studio=include_design_studio,
                                include_video=include_video,
                            )
                            + "\n\n---\n\n"
                            + prompt_text
                        )
            except Exception:
                logging.getLogger("proxima.worker").exception("preamble build failed (non-fatal)")
        # Iterate chats keep the agent in sync with the recipe AFTER the first turn:
        # the user may have edited steps directly in the stage editor, so re-inject the
        # current recipe each turn (the full sandbox preamble already covered turn 1).
        if is_build and not is_fresh_session:
            try:
                wfc = db.execute("SELECT name, steps FROM workflows WHERE id = ?", (jrow["workflow_id"],)).fetchone()
                if wfc:
                    prompt_text = wf.build_recipe_context(wfc["name"], json.loads(wfc["steps"] or "[]")) + "\n\n---\n\n" + prompt_text
            except Exception:
                logging.getLogger("proxima.worker").exception("recipe context inject failed (non-fatal)")
        # A design session is always framed as design (every turn), regardless of
        # what the client sent — keeps the agent editing the scene, never launching
        # workflows or unrelated tasks.
        if session_mode == "design":
            prompt_text = wiki_memory.DESIGN_SESSION_GUARDRAIL + "\n\n---\n\n" + prompt_text
        return prompt_text

    async def reset_agent_session(
        self,
        run_id: int,
        session_id: int,
        spec: Any,
        hermes_home: str,
        cwd: str,
        acp_sid: str,
        active_runs: dict[int, tuple[Any, str]],
        reason: str,
    ) -> tuple[Any, str]:
        db = self.app.state.worker_db
        logging.getLogger("proxima.worker").warning("resetting ACP session %s for chat %s: %s", acp_sid, session_id, reason[-240:])
        with self.app.state.db_lock:
            db.execute("DELETE FROM agent_sessions WHERE session_id = ? AND hermes_home = ?", (session_id, hermes_home))
        try:
            await self.app.state.acp_manager.recycle(spec, hermes_home, cwd)
        except Exception:
            logging.getLogger("proxima.worker").exception("failed to recycle agent process after ACP history error")
        proc2 = await self.app.state.acp_manager.get(spec, hermes_home, cwd)
        sid2 = await proc2.new_session(cwd)
        with self.app.state.db_lock:
            db.execute(
                "INSERT OR REPLACE INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (?, ?, ?)",
                (session_id, hermes_home, sid2),
            )
        active_runs[run_id] = (proc2, sid2)
        return proc2, sid2

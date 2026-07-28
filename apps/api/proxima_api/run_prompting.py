"""ACP session setup and prompt framing helpers for RunWorker.execute_run."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import fsapi
from . import recommended_tools
from . import wiki_memory

logger = logging.getLogger("proxima.run_prompting")
MASTER_HISTORY_BYTES = 64 * 1024

# A design run appends ⟦VISION:relpath|relpath⟧ to its prompt so the worker can read
# those project files and send them to the model as image content blocks (vision).
_VISION_MARKER = re.compile(r"\n*⟦VISION:([^⟧]*)⟧\s*$")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_VISION_MAX_BYTES = 8_000_000
_VISION_MAX_COUNT = 10
_VISION_MAX_TOTAL_BYTES = 32_000_000


def markdown_image_paths(text: str, limit: int = _VISION_MAX_COUNT) -> list[str]:
    """Return explicit local image-reference paths from Markdown, in owner order.

    This only recognizes references emitted by the project-file picker/attachment
    control. Filesystem validation and MIME/size checks happen when bytes are loaded.
    """
    paths: list[str] = []
    for match in _MARKDOWN_IMAGE.finditer(text or ""):
        rel = match.group(1).strip()
        if (
            not rel
            or rel in paths
            or len(paths) >= max(0, limit)
            or "|" in rel
            or "⟧" in rel
            or "\n" in rel
            or "\r" in rel
            or re.match(r"^(?:https?:|data:|blob:)", rel, re.IGNORECASE)
        ):
            continue
        paths.append(rel)
    return paths


def append_vision_references(text: str, paths: Iterable[str]) -> str:
    """Append the worker's private vision marker for explicit local references."""
    marker = _VISION_MARKER.search(text or "")
    existing = marker.group(1).split("|") if marker else []
    base = text[: marker.start()].rstrip() if marker else text.rstrip()
    safe: list[str] = []
    for raw in [*existing, *paths]:
        rel = str(raw).strip()
        if (
            not rel
            or rel in safe
            or "|" in rel
            or "⟧" in rel
            or "\n" in rel
            or "\r" in rel
        ):
            continue
        safe.append(rel)
        if len(safe) >= _VISION_MAX_COUNT:
            break
    if not safe:
        return text
    return f"{base}\n\n⟦VISION:{'|'.join(safe)}⟧"


def load_project_images(
    project_root: str | Path,
    paths: Iterable[str],
    fallback_root: str | Path | None = None,
) -> list[tuple[bytes, str]]:
    """Load bounded, jailed image references from a project.

    Bad paths are skipped rather than failing the run: the prompt remains useful even
    if a file was renamed between picker selection and execution.
    """
    root = Path(project_root)
    images: list[tuple[bytes, str]] = []
    total_bytes = 0
    for rel in paths:
        rel = str(rel).strip()
        if not rel or len(images) >= _VISION_MAX_COUNT:
            continue
        try:
            path = fsapi.resolve_in_project(root, rel)
            if not path.is_file() and fallback_root is not None:
                path = fsapi.resolve_in_project(Path(fallback_root), rel)
            mime = mimetypes.guess_type(path.name)[0] or ""
            size = path.stat().st_size if path.is_file() else 0
            if (
                not mime.startswith("image/")
                or size <= 0
                or size > _VISION_MAX_BYTES
                or total_bytes + size > _VISION_MAX_TOTAL_BYTES
            ):
                continue
            data = path.read_bytes()
            if (
                not data
                or len(data) > _VISION_MAX_BYTES
                or total_bytes + len(data) > _VISION_MAX_TOTAL_BYTES
            ):
                continue
        except Exception:
            logger.debug("vision image skipped: %s", rel, exc_info=True)
            continue
        images.append((data, mime))
        total_bytes += len(data)
    return images


def extract_vision_images(
    text: str,
    project_root: str,
    fallback_root: str | Path | None = None,
) -> tuple[str, list[tuple[bytes, str]]]:
    """Pull the ⟦VISION:...⟧ marker off a prompt, returning the cleaned text and the
    referenced images as (bytes, mime). Paths are jailed to the project root; anything
    missing/oversized is skipped so vision is best-effort and never breaks the run."""
    m = _VISION_MARKER.search(text or "")
    if not m:
        return text, []
    clean = text[: m.start()].rstrip()
    images = load_project_images(
        project_root,
        m.group(1).split("|"),
        fallback_root=fallback_root,
    )
    return clean, images
from . import workflows as wf
from . import features
from .capabilities import (
    apply_capabilities,
    parse_selection,
    remove_fixed_code_graph_mcp,
)
from .profile_seed import refresh_agent_credentials


class RunPrompting:
    def __init__(self, app: Any) -> None:
        self.app = app

    def reapply_capabilities(
        self,
        cfg: dict[str, Any],
        spec: Any,
        hermes_home: str,
        profile_id: Any,
        required_skill_ids: Iterable[str] = (),
        require_explicit_empty: bool = False,
        fixed_code_graph_path: str | Path | None = None,
    ) -> None:
        """Re-activate the run's profile skill/MCP selection into its home before the
        run. Idempotent (symlinks/config write) and self-healing: newly installed host
        skills show up, and profiles created before this feature get their selection
        applied. A first-class command may require one bundled methodology for this
        run even when the profile normally opts out. Live-home claude is a no-op
        (home already IS the host config). Repo Task runs may receive a
        server-managed Code graph MCP fixed to their selected Area."""
        if not hermes_home or profile_id in (None, 0):
            return
        if (
            not require_explicit_empty
            and cfg.get("claude_live_home")
            and getattr(spec, "id", "") == "claude-code"
            and fixed_code_graph_path is None
        ):
            # Live home skips full capability re-apply, but still strip any
            # leftover Area-locked Code graph MCP from a prior Task run.
            try:
                remove_fixed_code_graph_mcp("claude-code", Path(hermes_home))
            except Exception:
                logging.getLogger("proxima.worker").exception(
                    "live-home Code graph MCP cleanup failed (non-fatal)"
                )
            return
        try:
            row = self.app.state.worker_db.execute(
                "SELECT capabilities FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            selection = parse_selection(row["capabilities"] if row else None)
            if require_explicit_empty and selection != {
                "skills": [],
                "mcp": [],
            }:
                raise RuntimeError(
                    "Master requires an explicit empty skill and MCP selection"
                )
            required = [str(skill_id) for skill_id in required_skill_ids if str(skill_id)]
            # None, or a selection without an explicit skills list, already means
            # inherit every detected skill. Only an explicit subset needs a
            # temporary addition. Do not rewrite profiles.capabilities: invoking
            # one command must not silently change the owner's normal profile.
            if selection is not None and isinstance(selection.get("skills"), list) and required:
                selection = {
                    **selection,
                    "skills": list(dict.fromkeys([*selection["skills"], *required])),
                }
            override = cfg.get("source_hermes_home") if getattr(spec, "id", "") == "hermes" else None
            from . import app_settings as _app_settings
            custom_roots: list[str] = []
            try:
                custom_roots = _app_settings.get_custom_skill_roots(self.app.state.worker_db)
            except Exception:
                custom_roots = []
            # Master must never receive the Code graph MCP entry.
            graph_path = None if require_explicit_empty else fixed_code_graph_path
            applied = apply_capabilities(
                spec,
                Path(hermes_home),
                selection,
                override,
                bundle_dir=cfg.get("bundled_skills_dir"),
                custom_roots=custom_roots,
                strict=require_explicit_empty,
                fixed_code_graph_path=graph_path,
            )
            if require_explicit_empty and applied != {
                "skills": [],
                "mcp": [],
            }:
                raise RuntimeError(
                    "Master capability activation was not empty"
                )
        except Exception:
            if require_explicit_empty:
                raise
            logging.getLogger("proxima.worker").exception(
                "capability re-apply failed (non-fatal)"
            )

    async def refresh_credentials_if_needed(
        self,
        cfg: dict[str, Any],
        spec: Any,
        hermes_home: str,
        cwd: str,
        *,
        master_chat_only: bool = False,
    ) -> None:
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
                    if master_chat_only:
                        await self.app.state.acp_manager.recycle(
                            spec,
                            hermes_home,
                            cwd,
                            master_chat_only=True,
                        )
                    else:
                        await self.app.state.acp_manager.recycle(
                            spec, hermes_home, cwd
                        )
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
        *,
        master_dynamic_tools: list[dict[str, Any]] | None = None,
    ) -> tuple[Any, str, bool]:
        """Get an ACP process and a per-home ACP session for this Proxima session."""
        db = self.app.state.worker_db
        restricted = master_dynamic_tools is not None
        proc = (
            await self.app.state.acp_manager.get(
                spec,
                hermes_home,
                cwd,
                master_chat_only=True,
            )
            if restricted
            else await self.app.state.acp_manager.get(
                spec, hermes_home, cwd
            )
        )
        if restricted:
            with self.app.state.db_lock:
                db.execute(
                    "DELETE FROM agent_sessions "
                    "WHERE session_id = ? AND hermes_home = ?",
                    (session_id, hermes_home),
                )
            acp_sid = await proc.new_master_session(
                cwd,
                master_dynamic_tools,
            )
            with self.app.state.db_lock:
                db.execute(
                    "INSERT OR REPLACE INTO agent_sessions("
                    "session_id, hermes_home, acp_session_id"
                    ") VALUES (?, ?, ?)",
                    (session_id, hermes_home, acp_sid),
                )
            active_runs[run_id] = (proc, acp_sid)
            return proc, acp_sid, True
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
        prompt_text = run["prompt"]
        if session_mode == "master":
            if is_fresh_session:
                history = self._master_history(
                    db,
                    session_id,
                    current_prompt=str(run["prompt"]),
                )
                if history:
                    prompt_text = (
                        "# Durable Master history\n\n"
                        + history
                        + "\n\n---\n\n"
                        + prompt_text
                    )
            routing = self._master_routing_context(db, int(run["id"]))
            if routing:
                prompt_text += (
                    "\n\n---\n\n"
                    "# Proxima routing context\n\n"
                    + routing
                )
        moodboard_references: list[dict[str, Any]] = []
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
                # Brand guidelines live at <project>/design.md (a sibling of wiki/); read
                # them so the design agent composes on-brand without a tool call.
                design_guidelines = (
                    wiki_memory.read_design_guidelines(project_wiki.parent)
                    if (include_design_studio and project_wiki is not None)
                    else None
                )
                moodboard_references = (
                    wiki_memory.read_moodboard_references(project_wiki.parent)
                    if (include_design_studio and project_wiki is not None)
                    else []
                )
                preamble = wiki_memory.build_run_preamble(
                    project_name,
                    project_slug,
                    project_wiki,
                    include_design_studio=include_design_studio,
                    design_guidelines=design_guidelines,
                    moodboard_references=moodboard_references,
                    # T8 detect-and-advertise: probe PATH for the bundle's
                    # recommended tools (cheap, first turn only) so present ones
                    # are advertised to the agent. Missing ones stay silent here.
                    host_tools=recommended_tools.probe_recommended_tools(
                        cfg.get("bundled_skills_dir")),
                )
                if preamble:
                    prompt_text = preamble + "\n\n---\n\n" + prompt_text
                # Workflow steps additionally get a "proxima capabilities" brief so the
                # agent can decide to produce a real Design Studio design, use project
                # files, etc. — straight from the step's instruction (AI auto-detects).
                if is_job:
                    prompt_text = wf.build_capability_preamble(
                        include_design_studio=include_design_studio,
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
            if not moodboard_references and include_design_studio and project_wiki is not None:
                try:
                    moodboard_references = wiki_memory.read_moodboard_references(project_wiki.parent)
                except Exception:
                    logger.exception("moodboard context read failed (non-fatal)")
            if not is_fresh_session:
                moodboard_context = wiki_memory.moodboard_reference_context(moodboard_references)
                if moodboard_context:
                    prompt_text = moodboard_context + "\n\n---\n\n" + prompt_text
            prompt_text = wiki_memory.DESIGN_SESSION_GUARDRAIL + "\n\n---\n\n" + prompt_text
            prompt_text = append_vision_references(
                prompt_text,
                [
                    str(item.get("imagePath"))
                    for item in moodboard_references
                    if item.get("imagePath")
                ],
            )
        return prompt_text

    @staticmethod
    def _master_history(
        db: Any,
        session_id: int,
        *,
        current_prompt: str,
    ) -> str:
        """Render a bounded durable transcript for a fresh restricted thread."""
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id = ? "
                "AND role IN ('user', 'assistant', 'system', 'error') "
                "ORDER BY id",
                (session_id,),
            ).fetchall()
        ]
        if (
            rows
            and rows[-1]["role"] == "user"
            and rows[-1]["content"] == current_prompt
        ):
            rows.pop()
        selected: list[dict[str, str]] = []
        used = 2
        for row in reversed(rows):
            item = {
                "role": str(row["role"]),
                "content": str(row["content"]),
            }
            encoded = json.dumps(
                item, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded) + used > MASTER_HISTORY_BYTES:
                break
            selected.append(item)
            used += len(encoded) + 1
        selected.reverse()
        if not selected:
            return ""
        return json.dumps(
            selected,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _master_routing_context(db: Any, run_id: int) -> str:
        row = db.execute(
            "SELECT mc.focus_mode, mc.focus_container_id, mc.target_mode, "
            "mc.target_container_id, mc.target_area_id "
            "FROM messages m JOIN master_message_context mc "
            "ON mc.message_id = m.id "
            "WHERE m.run_id = ? AND m.role = 'user' "
            "ORDER BY m.id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return ""
        if row["target_mode"] == "explicit":
            text = (
                "The owner explicitly targeted registered Container id "
                f"{row['target_container_id']}."
            )
            if row["target_area_id"] is not None:
                return text + f" Use registered Area id {row['target_area_id']}."
            return text + " Choose one registered Area inside that Container."
        if row["focus_mode"] == "container":
            return (
                "Route automatically, but stay inside the focused registered "
                f"Container id {row['focus_container_id']}."
            )
        return "Route automatically across the registered Fleet."

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
        *,
        master_dynamic_tools: list[dict[str, Any]] | None = None,
    ) -> tuple[Any, str]:
        db = self.app.state.worker_db
        logging.getLogger("proxima.worker").warning("resetting ACP session %s for chat %s: %s", acp_sid, session_id, reason[-240:])
        with self.app.state.db_lock:
            db.execute("DELETE FROM agent_sessions WHERE session_id = ? AND hermes_home = ?", (session_id, hermes_home))
        try:
            if master_dynamic_tools is not None:
                await self.app.state.acp_manager.recycle(
                    spec,
                    hermes_home,
                    cwd,
                    master_chat_only=True,
                )
            else:
                await self.app.state.acp_manager.recycle(
                    spec, hermes_home, cwd
                )
        except Exception:
            logging.getLogger("proxima.worker").exception("failed to recycle agent process after ACP history error")
        proc2 = (
            await self.app.state.acp_manager.get(
                spec,
                hermes_home,
                cwd,
                master_chat_only=True,
            )
            if master_dynamic_tools is not None
            else await self.app.state.acp_manager.get(
                spec, hermes_home, cwd
            )
        )
        sid2 = (
            await proc2.new_master_session(cwd, master_dynamic_tools)
            if master_dynamic_tools is not None
            else await proc2.new_session(cwd)
        )
        with self.app.state.db_lock:
            db.execute(
                "INSERT OR REPLACE INTO agent_sessions(session_id, hermes_home, acp_session_id) VALUES (?, ?, ?)",
                (session_id, hermes_home, sid2),
            )
        active_runs[run_id] = (proc2, sid2)
        return proc2, sid2

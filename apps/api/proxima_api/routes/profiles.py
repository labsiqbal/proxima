"""Profile, runner, and command routes for the Proxima API.

Extracted via the register() pattern — handler bodies verbatim. No behavior change.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from ..commands import command_catalog, execute_command
from ..capabilities import detect_for_runner
from ..recommended_tools import probe_recommended_tools
from ..runners import detect_runners, hermes_status, runner_readiness
from ..runner_specs import runner_is_selectable, runner_spec
from ..profile_seed import seed_agent_home
from ..settings import hermes_home_for
from ..schemas import CommandRequest, ProfileCreateRequest, ProfileUpdateRequest


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    profile_payload = deps["profile_payload"]
    profile_for_user = deps["profile_for_user"]
    create_profile_for = deps["create_profile_for"]
    ensure_default_profile = deps["ensure_default_profile"]
    runner_source_dir = deps["runner_source_dir"]
    apply_profile_capabilities = deps["apply_profile_capabilities"]
    visible_project = deps["visible_project"]

    @app.get("/api/profiles")
    def list_profiles(user: dict[str, Any] = Depends(current_user)):
        ensure_default_profile(user)
        rows = db().execute("SELECT * FROM profiles WHERE user_id = ? ORDER BY is_default DESC, name", (user["id"],)).fetchall()
        return {"profiles": [profile_payload(dict(row)) for row in rows]}

    @app.post("/api/profiles", status_code=201)
    def create_profile(payload: ProfileCreateRequest, user: dict[str, Any] = Depends(current_user)):
        if not runner_is_selectable(payload.runner_id):
            raise HTTPException(status_code=400, detail="unknown runner")
        # Slug is automatic: derive from name, de-duplicate with a numeric suffix.
        base = (re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-") or "profile")[:50]
        slug, n = base, 1
        while db().execute("SELECT 1 FROM profiles WHERE user_id = ? AND slug = ?", (user["id"], slug)).fetchone():
            n += 1; slug = f"{base}-{n}"
        profile = create_profile_for(user, slug, payload.name, runner_id=payload.runner_id, instructions=payload.instructions)
        return profile_payload(profile)

    @app.patch("/api/profiles/{profile_id}")
    def update_profile(profile_id: int, payload: ProfileUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        profile = profile_for_user(profile_id, user)
        if payload.is_default:
            db().execute("UPDATE profiles SET is_default = 0 WHERE user_id = ?", (user["id"],))
            db().execute("UPDATE profiles SET is_default = 1 WHERE id = ?", (profile_id,))
        if payload.name is not None:
            db().execute("UPDATE profiles SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payload.name, profile_id))
        if payload.default_model is not None:
            db().execute("UPDATE profiles SET default_model = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payload.default_model, profile_id))
        if payload.runner_id is not None and payload.runner_id != profile.get("runner_id"):
            if not runner_is_selectable(payload.runner_id):
                raise HTTPException(status_code=400, detail="unknown runner")
            # Switching runner must move the home too, else the old runner's home
            # (e.g. ~/.claude) is reused by the new one — the misconfig that left a
            # Hermes profile pointed at the Claude config dir.
            spec = runner_spec(payload.runner_id)
            if cfg.get("claude_live_home") and payload.runner_id == "claude-code":
                home = Path(os.path.expanduser("~/.claude"))
                home.mkdir(parents=True, exist_ok=True)
            else:
                home = hermes_home_for(cfg, user["username"], profile["slug"])
                home.mkdir(parents=True, exist_ok=True)
                if spec.seed_files:
                    seed_agent_home(runner_source_dir(spec), home, spec.seed_files)
            db().execute("UPDATE profiles SET runner_id = ?, hermes_home = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payload.runner_id, str(home), profile_id))
            # New runner → re-activate this profile's skill/MCP selection into the new home.
            apply_profile_capabilities(dict(db().execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()))
        if payload.instructions is not None:
            db().execute("UPDATE profiles SET instructions = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payload.instructions, profile_id))
        if payload.capabilities is not None:
            # Persist the selection, then reactivate the home to match (symlink the
            # chosen skills, filter MCP). config_sig picks up the home change and the
            # cached agent recycles on its next run.
            db().execute("UPDATE profiles SET capabilities = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (json.dumps(payload.capabilities), profile_id))
            apply_profile_capabilities(dict(db().execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()))
        return profile_payload(dict(db().execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()))

    @app.get("/api/runners/{runner_id}/capabilities")
    def runner_capabilities(runner_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """Skills + MCP servers detected on the host for this runner (portable —
        read from the runner's own config dir). Read-only; the per-profile selection
        of which to enable lives on the profile (PATCH /api/profiles/{id})."""
        if not runner_is_selectable(runner_id):
            raise HTTPException(status_code=400, detail="unknown runner")
        spec = runner_spec(runner_id)
        override = str(runner_source_dir(spec)) if runner_id == "hermes" else None
        return {"runner_id": runner_id,
                **detect_for_runner(spec, override, bundle_dir=cfg.get("bundled_skills_dir"))}

    @app.get("/api/tools/recommended")
    def tools_recommended(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """The capability bundle's recommended-tools list with a PATH-probe result
        per tool (T8 detect-and-advertise). Advisory only: missing tools are a
        quiet Settings hint, never a blocker; Proxima never installs binaries."""
        return {"tools": probe_recommended_tools(cfg.get("bundled_skills_dir"))}

    @app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: int, user: dict[str, Any] = Depends(current_user)):
        profile = profile_for_user(profile_id, user)
        count = db().execute("SELECT COUNT(*) AS c FROM profiles WHERE user_id = ?", (user["id"],)).fetchone()["c"]
        if count <= 1 or profile["is_default"]:
            raise HTTPException(status_code=400, detail="cannot delete last or default profile")
        db().execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return {"ok": True}

    @app.get("/api/runners/detect")
    def runners_detect(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        # Runnability comes from the runner registry (RunnerDefinition.runnable),
        # not a hardcoded vendor — Proxima is bring-your-own-agent.
        runners = detect_runners()
        return {
            "user": user["username"],
            "runners": runners,
            "hermes": hermes_status(source_home=cfg.get("source_hermes_home"), binary=cfg.get("hermes_bin"), path_env=None),
            "runnerReadiness": runner_readiness(),
        }

    @app.get("/api/commands/catalog")
    def commands_catalog(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"user": user["username"], **command_catalog(cfg)}

    @app.post("/api/commands/execute")
    def commands_execute(payload: CommandRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if payload.project_slug:
            visible_project(payload.project_slug, user)
        return execute_command(payload.command, user=user, project_slug=payload.project_slug, runner_id=payload.runner_id, config=cfg)

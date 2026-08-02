"""Profile, runner, and command routes for the Proxima API.

Extracted via the register() pattern — handler bodies verbatim. No behavior change.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Query

from .. import app_settings
from ..commands import (
    build_skill_slash_commands,
    command_catalog,
    execute_command,
    skill_command_map,
)
from ..capabilities import clear_skill_scan_cache, detect_for_runner, parse_selection
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

    def _custom_skill_roots() -> list[str]:
        try:
            return app_settings.get_custom_skill_roots(db())
        except Exception:
            return []

    def _detect(runner_id: str, *, force_rescan: bool = False) -> dict[str, Any]:
        spec = runner_spec(runner_id)
        override = str(runner_source_dir(spec)) if runner_id == "hermes" else None
        return detect_for_runner(
            spec,
            override,
            bundle_dir=cfg.get("bundled_skills_dir"),
            custom_roots=_custom_skill_roots(),
            force_rescan=force_rescan,
        )

    def _profile_skill_context(
        user: dict[str, Any],
        *,
        profile_id: int | None = None,
        runner_id: str | None = None,
        force_rescan: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
        """Detected skills + selection for catalog/execute. Prefer profile_id."""
        profile: dict[str, Any] | None = None
        if profile_id is not None:
            try:
                profile = profile_for_user(profile_id, user)
            except HTTPException:
                profile = None
        if profile is None:
            try:
                ensure_default_profile(user)
                row = db().execute(
                    "SELECT * FROM profiles WHERE user_id = ? AND is_default = 1 "
                    "AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
                    (user["id"],),
                ).fetchone()
                profile = dict(row) if row else None
            except Exception:
                profile = None
        rid = runner_id or (profile.get("runner_id") if profile else None)
        if not rid or not runner_is_selectable(rid):
            return [], parse_selection(profile.get("capabilities") if profile else None), rid
        detected = _detect(rid, force_rescan=force_rescan)
        selection = parse_selection(profile.get("capabilities") if profile else None)
        return list(detected.get("skills") or []), selection, rid

    @app.get("/api/profiles")
    def list_profiles(user: dict[str, Any] = Depends(current_user)):
        ensure_default_profile(user)
        rows = db().execute(
            "SELECT * FROM profiles WHERE user_id = ? AND COALESCE(system_kind, '') = '' "
            "ORDER BY is_default DESC, name", (user["id"],)
        ).fetchall()
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
        if profile.get("system_kind"):
            raise HTTPException(status_code=404, detail="profile not found")
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
    def runner_capabilities(
        runner_id: str,
        rescan: bool = Query(False),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        """Skills + MCP servers detected on the host for this runner (portable —
        multi-root OS-aware scan + custom roots). Read-only; the per-profile
        selection of which to enable lives on the profile (PATCH /api/profiles/{id}).
        Pass rescan=1 to bust the detection cache (manual Rescan / open settings)."""
        if not runner_is_selectable(runner_id):
            raise HTTPException(status_code=400, detail="unknown runner")
        if rescan:
            clear_skill_scan_cache()
        body = _detect(runner_id, force_rescan=rescan)
        return {"runner_id": runner_id, **body}

    @app.post("/api/runners/{runner_id}/capabilities/rescan")
    def runner_capabilities_rescan(
        runner_id: str,
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        """Manual rescan: clear the skill/MCP detection cache and re-walk roots."""
        if not runner_is_selectable(runner_id):
            raise HTTPException(status_code=400, detail="unknown runner")
        clear_skill_scan_cache()
        body = _detect(runner_id, force_rescan=True)
        return {"runner_id": runner_id, **body}

    @app.get("/api/tools/recommended")
    def tools_recommended(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """The capability bundle's recommended-tools list with a PATH-probe result
        per tool (T8 detect-and-advertise). Advisory only: missing tools are a
        quiet Settings hint, never a blocker; Proxima never installs binaries."""
        return {"tools": probe_recommended_tools(cfg.get("bundled_skills_dir"))}

    @app.get("/api/settings/skill-roots")
    def get_skill_roots(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        """Global custom skill directories included in every runner's multi-root scan."""
        return {"roots": app_settings.get_custom_skill_roots(db())}

    @app.put("/api/settings/skill-roots")
    def put_skill_roots(
        payload: dict[str, Any],
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        """Replace custom skill roots. Invalid paths are kept in the list but
        skipped at scan time with a warning (no crash). Busts the detect cache."""
        try:
            roots = app_settings.set_custom_skill_roots(db(), payload.get("roots"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_skill_scan_cache()
        return {"roots": roots}

    @app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: int, user: dict[str, Any] = Depends(current_user)):
        profile = profile_for_user(profile_id, user)
        if profile.get("system_kind"):
            raise HTTPException(status_code=404, detail="profile not found")
        count = db().execute(
            "SELECT COUNT(*) AS c FROM profiles WHERE user_id = ? AND COALESCE(system_kind, '') = ''",
            (user["id"],),
        ).fetchone()["c"]
        if count <= 1 or profile["is_default"]:
            raise HTTPException(status_code=400, detail="cannot delete last or default profile")
        db().execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return {"ok": True}

    @app.get("/api/runners/detect")
    def runners_detect(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        # Runnability comes from the runner registry (RunnerDefinition.runnable),
        # not a hardcoded vendor — Proxima is bring-your-own-agent.
        runtime_path = str(cfg.get("_runtime_path") or "")
        runners = detect_runners(
            path_env=runtime_path,
            create_shim=False,
        )
        return {
            "user": user["username"],
            "runners": runners,
            "hermes": hermes_status(
                source_home=cfg.get("source_hermes_home"),
                binary=cfg.get("hermes_bin"),
                path_env=runtime_path,
            ),
            "runnerReadiness": runner_readiness(
                path_env=runtime_path,
                create_shim=False,
            ),
        }

    @app.get("/api/commands/catalog")
    def commands_catalog(
        profile_id: int | None = Query(None),
        runner_id: str | None = Query(None),
        rescan: bool = Query(False),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        """Built-in Proxima commands plus enabled skills for the active profile
        as `/skill-name` agent turns. MCP is never promoted to slash. Catalog is
        built from the detection cache; pass rescan=1 after profile/runner change
        or a manual Rescan if skills may have been installed on disk."""
        if rescan:
            clear_skill_scan_cache()
        skills, selection, rid = _profile_skill_context(
            user, profile_id=profile_id, runner_id=runner_id, force_rescan=rescan
        )
        catalog = command_catalog(skills=skills, selection=selection)
        return {
            "user": user["username"],
            "profileId": profile_id,
            "runnerId": rid,
            **catalog,
        }

    @app.post("/api/commands/execute")
    def commands_execute(payload: CommandRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if payload.project_slug:
            visible_project(payload.project_slug, user)
        skills, selection, _rid = _profile_skill_context(
            user,
            profile_id=payload.profile_id,
            runner_id=payload.runner_id,
        )
        skill_cmds = build_skill_slash_commands(skills, selection)
        skill_map = skill_command_map(skills, selection)
        return execute_command(
            payload.command,
            user=user,
            project_slug=payload.project_slug,
            runner_id=payload.runner_id,
            skill_map=skill_map,
            skill_commands=skill_cmds,
        )

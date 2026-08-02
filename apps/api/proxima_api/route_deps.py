"""Route dependency factory for create_app.

The route modules still consume the historical string-keyed dependency dict, but
its helper implementations live here instead of inside create_app.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import json as _json

from .auth import expiry, hash_token, iso_now, new_token
from .capabilities import apply_capabilities, parse_selection
from .container_registry import (
    ContainerBoundaryError,
    compatibility_project_payload,
    container_quiescence_lock,
    container_mutation_lock,
    container_root,
    ops_root,
    recover_container_activity_guardians,
    root_for_virtual_path,
)
from .profile_seed import seed_agent_home
from .master_runtime import ensure_master_identity
from . import features, master_decisions, master_focus
from .project_areas import areas_payload
from .provisioning import provision_user_workspace
from .runner_specs import default_runner, runner_spec
from .settings import hermes_home_for, validate_slug

logger = logging.getLogger("proxima.api")

DbFactory = Callable[[], Any]
FastApiCallable = Callable[..., Any]


def _cap_parse(raw: Any) -> dict[str, Any] | None:
    """profiles.capabilities JSON string → dict for the API payload (None = inherit all)."""
    if not raw:
        return None
    try:
        v = _json.loads(raw)
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None


def build_route_deps(
    app: Any,
    cfg: dict[str, Any],
    db: DbFactory,
    *,
    depends: FastApiCallable,
    header: FastApiCallable,
    cookie: FastApiCallable,
    http_exception: Any,
    status_module: Any,
) -> dict[str, Any]:
    """Build the dependency dictionary consumed by routes/*.register()."""

    def ensure_single_user_owner() -> dict[str, Any]:
        """Single-user cockpit: guarantee one owner exists and return it. Created
        password-less so first-run setup can establish the owner's password."""
        name = validate_slug(cfg.get("single_user_name") or "owner")
        with app.state.db_lock:
            row = db().execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
            if row:
                user = dict(row)
                ensure_default_profile(user)
                return user
            cur = db().execute(
                "INSERT INTO users(username, os_user, role, password_hash, password_set_at) VALUES (?, ?, 'environment_admin', NULL, ?)",
                (name, name, iso_now()),
            )
            user = dict(
                db()
                .execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,))
                .fetchone()
            )
        try:
            ensure_default_profile(user)
            provision_user_workspace(db(), cfg, user)
            if features.enabled(cfg, features.MASTER_ORCHESTRATOR):
                ensure_master_identity(
                    db(),
                    user,
                    create_profile_for=create_profile_for,
                    managed_profiles_root=cfg["hermes_profiles_root"],
                )
        except Exception:
            logger.exception("single-user owner provisioning failed (non-fatal)")
        return user

    def current_user(
        authorization: str | None = header(default=None),
        proxima_session: str | None = cookie(default=None),
    ) -> dict[str, Any]:
        # Single-user cockpit, one owner. Defense-in-depth over the network boundary:
        # if the owner has set a password, every request must carry a valid session
        # (bearer token or the HttpOnly proxima_session cookie). Before a password is
        # set (first run / passwordless mode) the app is open so setup can happen.
        owner = ensure_single_user_owner()
        if not owner.get("password_hash"):
            return owner
        token = authorization.removeprefix("Bearer ").strip() if authorization else None
        token = token or proxima_session
        if token:
            row = (
                db()
                .execute(
                    "SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id "
                    "WHERE s.token_hash=? AND s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at > ?)",
                    (hash_token(token), iso_now()),
                )
                .fetchone()
            )
            if row:
                return dict(row)
        raise http_exception(
            status_code=status_module.HTTP_401_UNAUTHORIZED, detail="login required"
        )

    def current_user_strict_token(
        authorization: str | None = header(default=None),
        proxima_session: str | None = cookie(default=None),
    ) -> dict[str, Any]:
        # Validate any presented token (so frontend boot detects a stale session) and
        # enforce the password gate: a set password means no token = not logged in.
        owner = ensure_single_user_owner()
        token = authorization.removeprefix("Bearer ").strip() if authorization else None
        token = token or proxima_session
        if token:
            row = (
                db()
                .execute(
                    "SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id "
                    "WHERE s.token_hash=? AND s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at > ?)",
                    (hash_token(token), iso_now()),
                )
                .fetchone()
            )
            if row:
                return dict(row)
            raise http_exception(
                status_code=status_module.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired token",
            )
        if owner.get("password_hash"):
            raise http_exception(
                status_code=status_module.HTTP_401_UNAUTHORIZED, detail="login required"
            )
        return owner

    def admin_user(user: dict[str, Any] = depends(current_user)) -> dict[str, Any]:
        # Single-user: the sole owner is always the admin.
        return user

    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "os_user": user["os_user"],
        }

    def create_token(user_id: int) -> str:
        token = new_token()
        db().execute(
            "INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (
                hash_token(token),
                user_id,
                expiry(int(cfg.get("auth_token_ttl_hours") or 0)),
            ),
        )
        return token

    def runner_source_dir(spec) -> Path:
        """Host dir to seed/refresh this runner's credentials from. Hermes honors
        the configured source_hermes_home override; others use the spec default."""
        if spec.id == "hermes":
            return Path(cfg["source_hermes_home"])
        return (
            Path(os.path.expanduser(spec.source_dir or ""))
            if spec.source_dir
            else Path("/nonexistent")
        )

    def _cap_source_override(spec) -> str | None:
        """Capability detection reads the same host dir as credential seeding; only
        Hermes needs the configured override (others use spec.source_dir)."""
        return str(runner_source_dir(spec)) if spec.id == "hermes" else None

    def create_profile_for(
        user: dict[str, Any],
        slug: str,
        name: str,
        default_model: str | None = None,
        is_default: bool = False,
        runner_id: str | None = None,
        instructions: str | None = None,
        force_managed_home: bool = False,
    ) -> dict[str, Any]:
        slug = validate_slug(slug)
        runner_id = runner_id or default_runner()
        spec = runner_spec(runner_id)
        # Live-home: point claude-code at the real ~/.claude so the agent inherits
        # the operator's full setup (skills, plugins, CLAUDE.md rules, memory, MCP).
        # No seeding — it IS the live config. Other runners keep isolated profiles.
        if (
            cfg.get("claude_live_home")
            and runner_id == "claude-code"
            and not force_managed_home
        ):
            home = Path(os.path.expanduser("~/.claude"))
            home.mkdir(parents=True, exist_ok=True)
        else:
            home = hermes_home_for(cfg, user["username"], slug)
            home.mkdir(parents=True, exist_ok=True)
            # Seed the chosen runner's credentials from the host so the profile's agent
            # is authenticated out of the box (same idea as Hermes, now per-runner).
            if spec.seed_files:
                seed_agent_home(runner_source_dir(spec), home, spec.seed_files)
            # Activate detected skills/MCP into the home. New profile → inherit ALL
            # (selection None) so the host's skills AND the bundled skills work
            # out of the box.
            from . import app_settings as _app_settings

            apply_capabilities(
                spec,
                home,
                None,
                _cap_source_override(spec),
                bundle_dir=cfg.get("bundled_skills_dir"),
                custom_roots=_app_settings.get_custom_skill_roots(db()),
            )
        if is_default:
            db().execute(
                "UPDATE profiles SET is_default = 0 WHERE user_id = ?", (user["id"],)
            )
        cur = db().execute(
            "INSERT INTO profiles(user_id, slug, name, hermes_home, runner_id, default_model, instructions, is_default) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user["id"],
                slug,
                name,
                str(home),
                runner_id,
                default_model,
                instructions,
                1 if is_default else 0,
            ),
        )
        return dict(
            db()
            .execute("SELECT * FROM profiles WHERE id = ?", (cur.lastrowid,))
            .fetchone()
        )

    def apply_profile_capabilities(profile: dict[str, Any]) -> dict[str, list[str]]:
        """(Re)activate a profile's selected skills/MCP into its home. Reads the
        profile's runner + home + capabilities selection. Live-home claude profiles
        already have the host's skills, so this is a no-op there. Best-effort."""
        try:
            spec = runner_spec(profile.get("runner_id") or default_runner())
        except Exception:
            return {"skills": [], "mcp": []}
        if cfg.get("claude_live_home") and profile.get("runner_id") == "claude-code":
            return {"skills": [], "mcp": []}  # home IS ~/.claude — nothing to seed
        home = Path(profile["hermes_home"]) if profile.get("hermes_home") else None
        if not home:
            return {"skills": [], "mcp": []}
        selection = parse_selection(profile.get("capabilities"))
        from . import app_settings as _app_settings

        return apply_capabilities(
            spec,
            home,
            selection,
            _cap_source_override(spec),
            bundle_dir=cfg.get("bundled_skills_dir"),
            custom_roots=_app_settings.get_custom_skill_roots(db()),
        )

    def ensure_default_profile(user: dict[str, Any]) -> dict[str, Any]:
        row = (
            db()
            .execute(
                "SELECT * FROM profiles WHERE user_id = ? AND is_default = 1 "
                "AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
                (user["id"],),
            )
            .fetchone()
        )
        if row:
            return dict(row)
        row = (
            db()
            .execute(
                "SELECT * FROM profiles WHERE user_id = ? AND COALESCE(system_kind, '') = '' ORDER BY id LIMIT 1",
                (user["id"],),
            )
            .fetchone()
        )
        if row:
            db().execute(
                "UPDATE profiles SET is_default = 1 WHERE id = ?", (row["id"],)
            )
            return dict(row)
        return create_profile_for(user, "default", "Default", is_default=True)

    def profile_for_user(
        profile_id: int | None, user: dict[str, Any]
    ) -> dict[str, Any]:
        if profile_id is None:
            return ensure_default_profile(user)
        row = (
            db()
            .execute(
                "SELECT * FROM profiles WHERE id = ? AND user_id = ? AND COALESCE(system_kind, '') = ''",
                (profile_id, user["id"]),
            )
            .fetchone()
        )
        if not row:
            raise http_exception(status_code=404, detail="profile not found")
        return dict(row)

    def visible_project(slug: str, user: dict[str, Any]) -> dict[str, Any]:
        # Single-user: every non-archived project belongs to the owner.
        row = (
            db()
            .execute(
                "SELECT p.*, u.username AS owner, 'owner' AS role FROM projects p "
                "JOIN users u ON u.id = p.owner_user_id WHERE p.slug = ? AND p.archived_at IS NULL",
                (slug,),
            )
            .fetchone()
        )
        if not row:
            raise http_exception(status_code=404, detail="project not found")
        return dict(row)

    def session_for_user(session_id: int, user: dict[str, Any]) -> dict[str, Any]:
        # Single-user: every session belongs to the owner.
        row = (
            db()
            .execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            .fetchone()
        )
        if not row:
            raise http_exception(status_code=404, detail="session not found")
        return dict(row)

    def require_generic_run_mode(mode: str | None) -> None:
        if mode in {"master", "alpha"}:
            raise http_exception(
                status_code=409,
                detail="Master turns must use /api/master/messages",
            )

    def run_projectctl(*args: str) -> None:
        # Single-user $HOME deployments don't manage OS ownership/ACLs: the dir is
        # scaffolded by scaffold_project_dir and access is enforced by the single-owner
        # session plus project-root checks. Only privileged installs opt into ACL ops.
        if not cfg.get("manage_os_acl"):
            return
        base_command = cfg.get("projectctl_command") or [str(cfg["projectctl_path"])]
        command = [*base_command, "--root", str(cfg["workspace_root"]), *args]
        try:
            subprocess.run(command, check=True, text=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise http_exception(status_code=500, detail=detail) from exc

    def project_payload(row: dict[str, Any]) -> dict[str, Any]:
        return compatibility_project_payload(
            row,
            areas_payload(db(), row["id"]),
        )

    def profile_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "runner_id": row["runner_id"],
            "default_model": row["default_model"],
            "instructions": row["instructions"]
            if "instructions" in row.keys()
            else None,
            "is_default": bool(row["is_default"]),
            "hermes_home": row["hermes_home"],
            "capabilities": _cap_parse(row["capabilities"])
            if "capabilities" in row.keys()
            else None,
        }

    def session_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "runner_id": row["runner_id"],
            "profile_id": row["profile_id"],
            "profile_slug": row.get("profile_slug"),
            "profile_name": row.get("profile_name"),
            "project_slug": row.get("project_slug"),
            "project_name": row.get("project_name"),
            "visibility": row["visibility"],
            "updated_at": row["updated_at"],
            "job_id": row.get("job_id"),
            "workflow_id": row.get("workflow_id"),
            "mode": row.get("mode") or "chat",
        }

    def _purge_project_locked(project: dict[str, Any]) -> None:
        """Delete a project's on-disk dir (jailed to workspace root) + its DB row."""
        conn = db()
        focus_notifications: list[int] = []
        with app.state.db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                external_dependents = conn.execute(
                    "SELECT dependent.id, dependent.title "
                    "FROM task_dependencies AS dependency "
                    "JOIN jobs AS prerequisite "
                    "ON prerequisite.id = dependency.depends_on_task_id "
                    "JOIN jobs AS dependent ON dependent.id = dependency.task_id "
                    "WHERE prerequisite.project_id = ? "
                    "AND (dependent.project_id IS NULL OR dependent.project_id != ?) "
                    "ORDER BY dependent.id LIMIT 20",
                    (project["id"], project["id"]),
                ).fetchall()
                if external_dependents:
                    raise http_exception(
                        status_code=409,
                        detail={
                            "code": "container_tasks_have_external_dependents",
                            "message": (
                                "Delete dependent Tasks in other Containers first: "
                                + ", ".join(
                                    f"#{row['id']} {row['title']}"
                                    for row in external_dependents
                                )
                            ),
                            "dependent_task_ids": [
                                row["id"] for row in external_dependents
                            ],
                        },
                    )
                focus_rows = conn.execute(
                    "SELECT state.master_session_id "
                    "FROM master_focus_state AS state "
                    "LEFT JOIN master_focus_epochs AS epoch "
                    "ON epoch.id = state.current_epoch_id "
                    "WHERE epoch.container_id = ? "
                    "OR (state.pending_focus = 1 "
                    "AND state.pending_container_id = ?) "
                    "ORDER BY state.master_session_id",
                    (project["id"], project["id"]),
                ).fetchall()
                for focus_row in focus_rows:
                    master_session_id = int(focus_row["master_session_id"])
                    active = conn.execute(
                        "SELECT 1 FROM runs WHERE session_id = ? "
                        "AND status IN ('queued', 'running') LIMIT 1",
                        (master_session_id,),
                    ).fetchone()
                    if active is not None:
                        raise http_exception(
                            status_code=409,
                            detail={
                                "code": "container_is_master_focus",
                                "message": (
                                    "Wait for the active Master turn or cancel it "
                                    "before deleting this Container"
                                ),
                            },
                        )
                    state = master_focus.state_payload(
                        conn,
                        master_session_id,
                    )
                    if state["pending_container_id"] == project["id"]:
                        state = master_focus.request_pending_focus(
                            conn,
                            master_session_id=master_session_id,
                            container_id=state["current_container_id"],
                            expected_version=state["version"],
                        )
                    elif state["pending"]:
                        changed = master_focus.apply_pending_if_idle(
                            conn,
                            master_session_id=master_session_id,
                        )
                        if changed is not None:
                            focus_notifications.append(master_session_id)
                            state = changed
                    if state["current_container_id"] == project["id"]:
                        changed = master_focus.change_focus(
                            conn,
                            master_session_id=master_session_id,
                            container_id=None,
                            expected_version=state["version"],
                        )
                        if changed.get("changed"):
                            focus_notifications.append(master_session_id)
                path = project.get("path")
                root = str(Path(cfg["workspace_root"]).resolve())
                if path:
                    try:
                        rp = Path(path).resolve()
                        if str(rp).startswith(root + os.sep) and rp.exists():
                            shutil.rmtree(rp)
                    except Exception:
                        logging.getLogger("proxima.projects").exception(
                            "project dir removal failed for %s",
                            project.get("slug"),
                        )
                # Sessions only SET NULL on project delete, which would orphan every
                # chat and task thread. Delete them first so messages/events/runs/
                # agent_sessions cascade away cleanly.
                # Delegated Tasks also retain a required Container/Area audit binding,
                # so remove internal dependency edges, then the Container's jobs.
                # Incoming edges from another Container were refused before any
                # filesystem change.
                job_ids = [
                    int(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM jobs WHERE project_id = ? ORDER BY id",
                        (project["id"],),
                    ).fetchall()
                ]
                settled_decision_ids = master_decisions.settle_open_decisions_for_jobs(
                    conn,
                    job_ids=job_ids,
                    actor_user_id=project.get("owner_user_id"),
                    reason="Container deleted",
                )
                focus_notifications.extend(
                    master_decisions.project_settled_decisions(
                        app,
                        settled_decision_ids,
                        conn=conn,
                        external_transaction=True,
                    )
                )
                conn.execute(
                    "DELETE FROM task_dependencies WHERE "
                    "task_id IN (SELECT id FROM jobs WHERE project_id = ?) "
                    "AND depends_on_task_id IN "
                    "(SELECT id FROM jobs WHERE project_id = ?)",
                    (project["id"], project["id"]),
                )
                conn.execute("DELETE FROM jobs WHERE project_id = ?", (project["id"],))
                conn.execute(
                    "DELETE FROM sessions WHERE project_id = ?", (project["id"],)
                )
                conn.execute("DELETE FROM projects WHERE id = ?", (project["id"],))
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        for session_id in set(focus_notifications):
            app.state.hub.notify(session_id)

    def _purge_activity_blocked_detail(recovery: Any) -> dict[str, Any]:
        message = (
            "Container has active processes; stop them before retrying"
            if recovery.active
            else (
                "Project process ownership could not be verified; "
                "exclusive work is blocked until process identity is reconciled"
            )
        )
        return {
            "message": message,
            "active_processes": recovery.active,
            "unresolved_processes": recovery.unresolved,
        }

    def _purge_project(project: dict[str, Any]) -> None:
        with container_mutation_lock(db(), project):
            recovery = recover_container_activity_guardians(db(), project)
            if recovery.active or recovery.unresolved:
                raise http_exception(
                    status_code=409,
                    detail=_purge_activity_blocked_detail(recovery),
                )
            try:
                with container_quiescence_lock(db(), project):
                    recovery = recover_container_activity_guardians(
                        db(),
                        project,
                    )
                    if recovery.active or recovery.unresolved:
                        raise http_exception(
                            status_code=409,
                            detail=_purge_activity_blocked_detail(recovery),
                        )
                    _purge_project_locked(project)
            except ContainerBoundaryError as exc:
                recovery = recover_container_activity_guardians(db(), project)
                raise http_exception(
                    status_code=409,
                    detail={
                        "message": str(exc),
                        "active_processes": recovery.active,
                        "unresolved_processes": recovery.unresolved,
                    },
                ) from exc

    def _can_access(_created_by: Any, _project_id: Any, _user: dict[str, Any]) -> bool:
        # Single-user: everything belongs to the owner.
        return True

    def _member_project_id(
        project_id: int | None, project_slug: str | None, user: dict[str, Any]
    ) -> int | None:
        """Resolve a project ref (slug or raw id) to an id. Single-user: no membership check."""
        if project_slug:
            return visible_project(project_slug, user)["id"]
        return project_id

    def _boundary_http(exc: ContainerBoundaryError) -> Any:
        """Client-facing 400 for a fail-closed Container/Area boundary refusal,
        matching the filesystem routes' fsapi.FsError response shape."""
        return http_exception(status_code=400, detail=str(exc))

    def _project_root(slug: str, user: dict[str, Any]) -> Path:
        project = visible_project(slug, user)
        try:
            return container_root(project)
        except ContainerBoundaryError as exc:
            raise _boundary_http(exc) from exc

    def _ops_root(slug: str, user: dict[str, Any]) -> Path:
        project = visible_project(slug, user)
        try:
            return ops_root(db(), project)
        except ContainerBoundaryError as exc:
            raise _boundary_http(exc) from exc

    def _virtual_root(slug: str, path: str, user: dict[str, Any]) -> Path:
        project = visible_project(slug, user)
        try:
            return root_for_virtual_path(db(), project, path)
        except ContainerBoundaryError as exc:
            raise _boundary_http(exc) from exc

    def user_from_token_query(token: str) -> dict[str, Any]:
        with app.state.db_lock:
            row = (
                db()
                .execute(
                    "SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND (s.expires_at IS NULL OR s.expires_at > ?)",
                    (hash_token(token), iso_now()),
                )
                .fetchone()
            )
        if not row:
            raise http_exception(
                status_code=status_module.HTTP_401_UNAUTHORIZED, detail="invalid token"
            )
        return dict(row)

    return {
        "db": db,
        "cfg": cfg,
        "current_user": current_user,
        "current_user_strict_token": current_user_strict_token,
        "admin_user": admin_user,
        "visible_project": visible_project,
        "session_for_user": session_for_user,
        "require_generic_run_mode": require_generic_run_mode,
        "profile_for_user": profile_for_user,
        "project_payload": project_payload,
        "profile_payload": profile_payload,
        "session_payload": session_payload,
        "_can_access": _can_access,
        "_member_project_id": _member_project_id,
        "create_profile_for": create_profile_for,
        "ensure_default_profile": ensure_default_profile,
        "runner_source_dir": runner_source_dir,
        "apply_profile_capabilities": apply_profile_capabilities,
        "run_projectctl": run_projectctl,
        "_purge_project": _purge_project,
        "_project_root": _project_root,
        "_ops_root": _ops_root,
        "_virtual_root": _virtual_root,
        "user_from_token_query": user_from_token_query,
        "create_token": create_token,
        "public_user": public_user,
        "ensure_single_user_owner": ensure_single_user_owner,
    }

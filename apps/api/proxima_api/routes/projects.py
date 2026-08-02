"""Project routes for the Proxima API.

Single-user cockpit: every project belongs to the sole owner. No membership,
sharing, invites, or visibility — those were the multi-user surface.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .. import (
    container_registry,
    container_relocate,
    fsapi,
    layout_map,
    repo_remote,
)
from ..directory_handles import directory_identity_for_path
from ..project_browse import (
    AllowedRoots,
    CreatedDirectory,
    DirectoryBrowseUnavailable,
    DirectoryComponentInvalid,
    DirectoryPublishConflict,
    PathOutsideRoots,
    PathResolutionUnavailable,
    browse_directory,
    create_directory_component,
    directory_identity,
    split_directory_target,
)
from ..project_areas import areas_payload, ensure_ops_area, sync_code_areas
from ..provisioning import scaffold_project_dir
from ..schemas import (
    MemoryWritesRequest,
    ProjectAreaAddRequest,
    ProjectAreaUpdateRequest,
    ProjectCreateRequest,
    ProjectLinkRequest,
    ProjectRebindRequest,
    ProjectUpdateRequest,
)
from ..settings import validate_slug


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]

    def _notify_code_graphs(
        *,
        owner_user_id: int,
        container_slug: str,
        project_id: int,
        added_rels: list[str] | None = None,
    ) -> None:
        """Enqueue Code graph builds for newly registered Areas (Group 10)."""
        lifecycle = getattr(app.state, "code_graph_lifecycle", None)
        if lifecycle is None:
            return
        try:
            if added_rels is not None:
                if not added_rels:
                    return
                rows = (
                    db()
                    .execute(
                        "SELECT id FROM project_areas "
                        "WHERE project_id = ? AND kind = 'code' AND source != 'excluded' "
                        "AND rel_path IN (" + ",".join("?" * len(added_rels)) + ")",
                        (project_id, *added_rels),
                    )
                    .fetchall()
                )
            else:
                rows = (
                    db()
                    .execute(
                        "SELECT id FROM project_areas "
                        "WHERE project_id = ? AND kind = 'code' AND source != 'excluded'",
                        (project_id,),
                    )
                    .fetchall()
                )
            if not rows:
                return
            lifecycle.on_code_areas_registered(
                owner_user_id=owner_user_id,
                container_slug=container_slug,
                area_ids=[int(row["id"]) for row in rows],
            )
        except Exception:
            logging.getLogger("proxima.projects").exception(
                "Code graph registration hook failed (non-fatal)"
            )

    def _notify_knowledge_graph(
        *,
        owner_user_id: int,
        container_slug: str,
    ) -> None:
        """Enqueue the Container Knowledge graph on create/link (Group 11)."""
        lifecycle = getattr(app.state, "knowledge_graph_lifecycle", None)
        if lifecycle is None:
            return
        try:
            lifecycle.on_container_registered(
                owner_user_id=owner_user_id,
                container_slug=container_slug,
            )
        except Exception:
            logging.getLogger("proxima.projects").exception(
                "Knowledge graph registration hook failed (non-fatal)"
            )

    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    project_payload = deps["project_payload"]
    run_projectctl = deps["run_projectctl"]
    _purge_project = deps["_purge_project"]

    @app.get("/api/projects")
    def list_projects(user: dict[str, Any] = Depends(current_user)):
        return {
            "projects": container_registry.list_compatibility_projects(
                db(),
                int(user["id"]),
            )
        }

    def _link_roots() -> list[str | Path]:
        return list(cfg.get("link_roots") or [os.path.expanduser("~")])

    @app.get("/api/fs/dirs")
    def fs_dirs(
        path: str = "",
        root_id: str = "",
        user: dict[str, Any] = Depends(current_user),
    ):
        """Browse directories under the configured link roots, to pick an existing
        folder to register as a project."""
        if path and not root_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "configured folder root identity is required",
                    "field": "path",
                },
            )
        try:
            return browse_directory(path, _link_roots(), root_id or None)
        except DirectoryBrowseUnavailable as exc:
            raise HTTPException(
                status_code=403,
                detail={"message": str(exc), "field": "path"},
            ) from exc

    def _validate_new_folder_name(name: str) -> str:
        """Single path component only - no separators, traversal, or empty names."""
        cleaned = name.strip()
        if not cleaned or cleaned in (".", ".."):
            raise HTTPException(
                status_code=400,
                detail={"message": "invalid folder name", "field": "folder"},
            )
        if cleaned != name or any(sep in cleaned for sep in ("/", "\\", "\0")):
            raise HTTPException(
                status_code=400,
                detail={"message": "invalid folder name", "field": "folder"},
            )
        return cleaned

    def _link_error(status_code: int, message: str, field: str) -> HTTPException:
        return HTTPException(
            status_code=status_code, detail={"message": message, "field": field}
        )

    @app.post("/api/projects/link", status_code=201)
    def link_project(
        payload: ProjectLinkRequest, user: dict[str, Any] = Depends(current_user)
    ):
        """Register a folder as a project without writing anything into it
        (prune C2): no moves, no scaffold, no git-exclude appends. The
        project's path points at the real folder, so chat/terminal/files
        operate on it. Pass mkdir=true to create a brand-new empty directory
        first (parent must already exist inside the link roots). The Ops
        path is per project, picked here (prune C3): ops_path overrides the
        detected default (an existing ops/ folder, else the root '.') and the
        choice persists on the project's Ops Area."""
        created_dir: CreatedDirectory | None = None
        pid: int | None = None
        made_dir = False
        try:
            error_field = "parent" if payload.mkdir else "path"
            if payload.mkdir and payload.ops_path not in (None, "."):
                # mkdir creates one empty folder: nothing inside it exists
                # yet, so only the root itself can be its Ops path.
                raise _link_error(
                    400,
                    "a brand-new folder has no Ops subfolder yet - leave the Ops path on the project root",
                    "ops_path",
                )
            try:
                allowed_roots = AllowedRoots.from_configured(_link_roots())
            except PathResolutionUnavailable as exc:
                raise _link_error(403, str(exc), error_field) from exc
            if payload.mkdir:
                try:
                    raw_parent, raw_name = split_directory_target(payload.path)
                except PathResolutionUnavailable as exc:
                    raise _link_error(
                        400, "parent directory is not reachable", "parent"
                    ) from exc
                folder_name = _validate_new_folder_name(raw_name)
                try:
                    parent = allowed_roots.resolve(raw_parent, payload.root_id)
                except PathOutsideRoots as exc:
                    raise _link_error(403, str(exc), "parent") from exc
                except PathResolutionUnavailable as exc:
                    raise _link_error(
                        400, "parent directory is not reachable", "parent"
                    ) from exc
                try:
                    created_dir = create_directory_component(parent, folder_name)
                except DirectoryComponentInvalid as exc:
                    raise _link_error(400, str(exc), "folder") from exc
                except PermissionError as exc:
                    raise _link_error(
                        403, "permission denied - cannot create folder here", "parent"
                    ) from exc
                except FileExistsError as exc:
                    raise _link_error(
                        409, "a folder with that name already exists", "folder"
                    ) from exc
                except FileNotFoundError as exc:
                    raise _link_error(
                        400, "parent directory does not exist", "parent"
                    ) from exc
                except NotADirectoryError as exc:
                    raise _link_error(
                        400, "parent directory is not reachable", "parent"
                    ) from exc
                except (PathOutsideRoots, PathResolutionUnavailable) as exc:
                    raise _link_error(
                        400, "parent directory is not reachable", "parent"
                    ) from exc
                except OSError as exc:
                    raise _link_error(
                        400, f"could not create folder: {exc.strerror or exc}", "parent"
                    ) from exc
                try:
                    target = created_dir.require_staged()
                except (PathOutsideRoots, PathResolutionUnavailable) as exc:
                    raise _link_error(
                        400, "created folder is not reachable", "parent"
                    ) from exc
                path_identity = created_dir.identity
            else:
                try:
                    resolved_target = allowed_roots.resolve(
                        payload.path,
                        payload.root_id,
                    )
                except PathOutsideRoots as exc:
                    raise _link_error(403, str(exc), "path") from exc
                except PathResolutionUnavailable as exc:
                    raise _link_error(
                        400, "selected folder is not reachable", "path"
                    ) from exc
                try:
                    path_identity = directory_identity(resolved_target)
                except PermissionError as exc:
                    raise _link_error(
                        403,
                        "permission denied - selected folder is not accessible",
                        "path",
                    ) from exc
                except (OSError, PathResolutionUnavailable) as exc:
                    raise _link_error(
                        400, "selected folder is not reachable", "path"
                    ) from exc
                target = resolved_target.path
            # The per-project Ops path (prune C3): validate an explicit owner
            # choice against the real folder, else detect the default. The
            # choice persists on the Ops Area row via the settle below.
            if payload.ops_path is not None:
                try:
                    ops_choice = container_registry.validate_ops_path_choice(
                        target,
                        payload.ops_path,
                    )
                except container_registry.ContainerBoundaryError as exc:
                    raise _link_error(400, str(exc), "ops_path") from exc
                ops_source = "manual"
            else:
                ops_choice = container_registry.detect_ops_path(target)
                ops_source = "auto"
            name = (payload.name or target.name).strip()
            # strip("-") AFTER the 63-char truncation too: [:63] can re-cut a collapsed
            # run mid-hyphen and leave a trailing '-', which validate_slug would reject.
            base_slug = (
                (payload.slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[
                    :63
                ].strip("-")
            ) or "project"
            try:
                slug = validate_slug(base_slug)
            except ValueError as exc:
                raise _link_error(
                    422,
                    f"invalid project slug: {exc}",
                    "slug" if payload.slug else "name",
                ) from exc
            if (
                db()
                .execute("SELECT id FROM projects WHERE slug = ?", (slug,))
                .fetchone()
            ):
                message = (
                    f"slug '{slug}' already exists - pick another"
                    if payload.slug
                    else "A project with that display name already exists - choose another display name"
                )
                raise _link_error(409, message, "slug" if payload.slug else "name")
            cur = db().execute(
                "INSERT INTO projects("
                "slug, name, path, path_identity, owner_user_id, visibility"
                ") VALUES (?, ?, ?, ?, ?, 'private')",
                (slug, name, str(target), path_identity, user["id"]),
            )
            pid = cur.lastrowid
            made_dir = created_dir is not None
            if created_dir is not None:
                try:
                    created_dir.commit()
                except DirectoryPublishConflict as exc:
                    raise _link_error(409, str(exc), "folder") from exc
                except DirectoryComponentInvalid as exc:
                    raise _link_error(400, str(exc), "folder") from exc
                except (OSError, PathOutsideRoots, PathResolutionUnavailable) as exc:
                    raise _link_error(
                        400,
                        "created folder is not reachable",
                        "parent",
                    ) from exc
            registered = {
                "id": pid,
                "path": str(target),
                "path_identity": path_identity,
            }
            try:
                container_registry.container_root(registered)
            except container_registry.ContainerBoundaryError as exc:
                raise _link_error(
                    400,
                    "selected folder identity changed",
                    "parent" if made_dir else "path",
                ) from exc
            # Container areas (T1): register the ops area + auto-detect code
            # areas. Linking never writes into the folder (prune C2): settle
            # adopts the chosen Ops folder as-is (the detected default or the
            # owner's link-time override, prune C3) and otherwise keeps the
            # "." layout; the move-based migration is only the explicit,
            # previewed opt-in on the ops-migration surface.
            ensure_ops_area(db(), pid, rel_path=".", source=ops_source)
            container_registry.container_root(registered)
            container_registry.settle_container_ops(db(), pid, ops_path=ops_choice)
            container_registry.container_root(registered)
            summary = sync_code_areas(db(), pid, target)
            # Seed the per-project layout map from the settled Ops path
            # (prune C4): zero-write detection of where this folder actually
            # keeps its wiki/artifacts/scripts/uploads.
            layout_map.seed_project_layout(db(), pid)
            # Project the identity from the folder's existing docs right away
            # (prune C5) so the Fleet shows it without waiting for the
            # background refresh cycle. Read-only toward the tree.
            container_registry.refresh_registry_projection(db(), pid)
            audit_action = "project.link.mkdir" if made_dir else "project.link"
            db().execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, ?, 'project', ?, ?)",
                (
                    user["id"],
                    audit_action,
                    slug,
                    json.dumps({"path": str(target), "mkdir": made_dir}),
                ),
            )
            _notify_code_graphs(
                owner_user_id=int(user["id"]),
                container_slug=slug,
                project_id=int(pid),
                added_rels=list(summary.get("added") or []),
            )
            _notify_knowledge_graph(
                owner_user_id=int(user["id"]),
                container_slug=slug,
            )
            row = dict(
                db()
                .execute(
                    "SELECT p.*, u.username AS owner, 'owner' AS role FROM projects p JOIN users u ON u.id = p.owner_user_id WHERE p.id = ?",
                    (pid,),
                )
                .fetchone()
            )
            container_registry.container_root(row)
            if created_dir is not None:
                created_dir.finish()
                created_dir = None
            return project_payload(row)
        except container_registry.ContainerBoundaryError as exc:
            if pid is not None:
                db().execute("DELETE FROM projects WHERE id = ?", (pid,))
            raise _link_error(
                400,
                "selected folder identity changed",
                "parent" if made_dir else "path",
            ) from exc
        except BaseException:
            if pid is not None:
                db().execute("DELETE FROM projects WHERE id = ?", (pid,))
            raise
        finally:
            if created_dir is not None:
                created_dir.rollback()

    @app.post("/api/projects", status_code=201)
    def create_project(
        payload: ProjectCreateRequest, user: dict[str, Any] = Depends(current_user)
    ):
        if (
            db()
            .execute("SELECT id FROM projects WHERE slug = ?", (payload.slug,))
            .fetchone()
        ):
            raise HTTPException(status_code=409, detail="project slug already exists")
        path = str(Path(cfg["workspace_root"]) / "projects" / payload.slug)
        run_projectctl("create-project", payload.slug, "--owner", user["os_user"])
        scaffold_project_dir(cfg, payload.slug)
        path_identity = directory_identity_for_path(Path(path))
        cur = db().execute(
            "INSERT INTO projects("
            "slug, name, path, path_identity, owner_user_id, visibility"
            ") VALUES (?, ?, ?, ?, ?, 'private')",
            (payload.slug, payload.name, path, path_identity, user["id"]),
        )
        project_id = cur.lastrowid
        ensure_ops_area(db(), project_id)
        summary = sync_code_areas(db(), project_id, path)
        container_registry.refresh_registry_projection(db(), project_id)
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.create', 'project', ?)",
            (user["id"], payload.slug),
        )
        _notify_code_graphs(
            owner_user_id=int(user["id"]),
            container_slug=payload.slug,
            project_id=int(project_id),
            added_rels=list(summary.get("added") or []),
        )
        _notify_knowledge_graph(
            owner_user_id=int(user["id"]),
            container_slug=payload.slug,
        )
        row = dict(
            db()
            .execute(
                "SELECT p.*, ? AS owner, 'owner' AS role FROM projects p WHERE p.id = ?",
                (user["username"], project_id),
            )
            .fetchone()
        )
        return project_payload(row)

    @app.get("/api/projects/{slug}")
    def get_project(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Return one owner-visible Project compatibility payload."""
        return project_payload(visible_project(slug, user))

    @app.get("/api/projects/{slug}/layout")
    def get_project_layout(slug: str, user: dict[str, Any] = Depends(current_user)):
        """The per-project layout map (prune C4): where this project keeps its
        wiki, artifacts, scripts, and uploads - detected from the real tree,
        today's fixed names as the defaults when nothing was detected. Also
        reports the per-project memory-writes toggle (prune C5)."""
        project = visible_project(slug, user)
        try:
            layout = layout_map.project_layout(db(), project)
        except container_registry.ContainerBoundaryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ops_path": layout.ops_rel,
            "areas": {
                area: {
                    "path": layout.rel_paths[area],
                    "source": layout.sources[area],
                    "exists": layout.dir(area).is_dir(),
                }
                for area in layout_map.LAYOUT_AREAS
            },
            "memory_writes": {"enabled": layout.memory_writes},
        }

    @app.get("/api/projects/{slug}/location")
    def get_project_location(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Where this project's folder should be and whether it is there
        (prune C6): the actionable replacement for the raw boundary error a
        moved, renamed, or restored folder used to raise on every operation.
        Read-only - one lstat plus one directory-handle open."""
        project = visible_project(slug, user)
        return container_relocate.location_payload(db(), project)

    @app.post("/api/projects/{slug}/rebind")
    def rebind_project(
        slug: str,
        payload: ProjectRebindRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Re-pin a project to its folder's real location (prune C6, #141).

        The target comes from the same onboarding folder picker, so it is
        jailed to the configured link roots exactly like a link. Identity is
        read from the docs the new location already has (prune C5) and
        compared with the stored projection: a mismatch is refused with the
        comparison in the body, and `confirm` is the owner's override.
        Metadata only - nothing is written into either folder."""
        project = visible_project(slug, user)
        try:
            allowed_roots = AllowedRoots.from_configured(_link_roots())
        except PathResolutionUnavailable as exc:
            raise _link_error(403, str(exc), "path") from exc
        try:
            resolved_target = allowed_roots.resolve(payload.path, payload.root_id)
        except PathOutsideRoots as exc:
            raise _link_error(403, str(exc), "path") from exc
        except PathResolutionUnavailable as exc:
            raise _link_error(400, "selected folder is not reachable", "path") from exc
        try:
            path_identity = directory_identity(resolved_target)
        except PermissionError as exc:
            raise _link_error(
                403, "permission denied - selected folder is not accessible", "path"
            ) from exc
        except (OSError, PathResolutionUnavailable) as exc:
            raise _link_error(400, "selected folder is not reachable", "path") from exc
        target = resolved_target.path
        clash = (
            db()
            .execute(
                "SELECT slug FROM projects WHERE id != ? AND archived_at IS NULL "
                "AND (path = ? OR path_identity = ?)",
                (project["id"], str(target), path_identity),
            )
            .fetchone()
        )
        if clash is not None:
            raise _link_error(
                409,
                f"that folder is already linked as project '{clash['slug']}'",
                "path",
            )
        try:
            result = container_relocate.rebind_container(
                db(),
                project,
                target,
                path_identity,
                confirm=payload.confirm,
            )
        except container_relocate.RebindRefused as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": exc.message,
                    "field": "path",
                    "confirmable": True,
                    **exc.preview,
                },
            ) from exc
        except container_registry.ContainerBoundaryError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "field": "path"},
            ) from exc
        if result["rebound"]:
            db().execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                "VALUES (?, 'project.rebind', 'project', ?, ?)",
                (
                    user["id"],
                    slug,
                    json.dumps(
                        {
                            "path": result["path"],
                            "previous_path": result["previous_path"],
                            "identity_confirmed": result["identity"]["matches"],
                            "override": bool(payload.confirm),
                            "repaired": result["repaired"],
                        }
                    ),
                ),
            )
        row = dict(
            db()
            .execute(
                "SELECT p.*, u.username AS owner, 'owner' AS role FROM projects p "
                "JOIN users u ON u.id = p.owner_user_id WHERE p.id = ?",
                (project["id"],),
            )
            .fetchone()
        )
        return {**result, "project": project_payload(row)}

    @app.put("/api/projects/{slug}/memory-writes")
    def set_memory_writes(
        slug: str,
        payload: MemoryWritesRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """The per-project memory-writes toggle (prune C5, decision #121):
        default ON, Proxima's automatic memory (log.md append + wiki index
        regeneration) follows the project's own detected wiki location; OFF
        disables those writers entirely for this project."""
        project = visible_project(slug, user)
        layout_map.set_memory_writes_enabled(
            db(), int(project["id"]), payload.enabled
        )
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
            "VALUES (?, 'project.memory_writes', 'project', ?, ?)",
            (
                user["id"],
                project["slug"],
                json.dumps({"enabled": payload.enabled}),
            ),
        )
        return {"enabled": payload.enabled}

    @app.get("/api/projects/{slug}/ops-migration")
    def get_ops_migration(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Inspect one Project's physical Ops migration without changing its files."""
        project = visible_project(slug, user)
        try:
            return container_registry.inspect_ops_migration(db(), project)
        except (container_registry.ContainerBoundaryError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/{slug}/ops-migration/validate")
    def validate_ops_migration(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Refresh the read-only collision and retry-safety projection."""
        project = visible_project(slug, user)
        try:
            return container_registry.inspect_ops_migration(db(), project)
        except (container_registry.ContainerBoundaryError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/{slug}/ops-migration/retry")
    def retry_ops_migration(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Retry only a currently safe layout using the durable migration marker."""
        project = visible_project(slug, user)
        try:
            before = container_registry.inspect_ops_migration(db(), project)
        except (container_registry.ContainerBoundaryError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not before["retry_safe"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Ops migration retry is disabled until validation is safe.",
                    "migration": before,
                },
            )
        recovery = container_registry.recover_container_activity_guardians(
            db(),
            project,
        )
        if recovery.active or recovery.unresolved:
            message = (
                "Project processes are still active; stop them before retrying."
                if recovery.active
                else "Project process ownership could not be verified; retry is blocked."
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "message": message,
                    "active_processes": recovery.active,
                    "unresolved_processes": recovery.unresolved,
                    "migration": before,
                },
            )
        if not container_registry.migrate_container_ops(db(), project):
            after = container_registry.inspect_ops_migration(db(), project)
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "The layout changed or migration could not complete safely.",
                    "migration": after,
                },
            )
        db().execute(
            """
            INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata)
            VALUES (?, 'container.ops_migration.retry', 'project', ?, ?)
            """,
            (
                user["id"],
                slug,
                json.dumps(
                    {"migration_version": container_registry.OPS_MIGRATION_VERSION}
                ),
            ),
        )
        return container_registry.inspect_ops_migration(db(), project)

    @app.patch("/api/projects/{slug}")
    def update_project(
        slug: str,
        payload: ProjectUpdateRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        project = visible_project(slug, user)
        if payload.name is not None:
            db().execute(
                "UPDATE projects SET name = ? WHERE id = ?",
                (payload.name.strip(), project["id"]),
            )
            db().execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.rename', 'project', ?)",
                (user["id"], slug),
            )
        row = dict(
            db()
            .execute(
                "SELECT p.*, u.username AS owner, 'owner' AS role FROM projects p JOIN users u ON u.id = p.owner_user_id WHERE p.id = ?",
                (project["id"],),
            )
            .fetchone()
        )
        return project_payload(row)

    @app.delete("/api/projects/{slug}")
    def delete_project(slug: str, user: dict[str, Any] = Depends(current_user)):
        project = visible_project(slug, user)
        try:
            _purge_project(project)
        except container_registry.ContainerBoundaryError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc)},
            ) from exc
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.delete', 'project', ?)",
            (user["id"], slug),
        )
        return {"ok": True, "slug": slug}

    # ── Work-container areas (Phase-1 slice 1, T1): code areas + ops area ──

    def _with_remotes(
        project: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Pair each code area with its detected git remote (T9, slice 11) so
        the settings UI knows whether to offer the push-after-merge toggle -
        no remote, no toggle. Only the dedicated areas endpoints pay for this
        (it shells out to the host's git); project list payloads never do."""
        for area in payload["code_areas"]:
            try:
                repo = container_registry.resolve_area_root(
                    db(), project, int(area["id"])
                )
            except container_registry.ContainerBoundaryError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            area["remote"] = repo_remote.detect_remote(repo)
        return payload

    @app.get("/api/projects/{slug}/areas")
    def list_project_areas(slug: str, user: dict[str, Any] = Depends(current_user)):
        """The project's container areas: code areas (git-repo subfolders,
        auto-detected or manual, each with its detected remote) and its
        single ops area."""
        project = visible_project(slug, user)
        return _with_remotes(project, areas_payload(db(), project["id"]))

    @app.post("/api/projects/{slug}/areas", status_code=201)
    def add_project_area(
        slug: str,
        payload: ProjectAreaAddRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Manually register (or correct) a code area - T1's hybrid override.
        The folder must exist inside the project but need not be a git repo yet
        (not-yet-`git init`'d code is a valid code area). A manual row is never
        clobbered by re-detection; re-adding a removed area revives it."""
        project = visible_project(slug, user)
        with container_registry.container_mutation_lock(db(), project):
            try:
                root = container_registry.container_root(project)
            except container_registry.ContainerBoundaryError:
                raise HTTPException(
                    status_code=400, detail="project folder is missing on disk"
                )
            try:
                target = fsapi.resolve_in_project(root, payload.rel_path)
            except fsapi.FsError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not target.is_dir():
                raise HTTPException(
                    status_code=400, detail="not a directory inside the project"
                )
            rel = "." if target == root else target.relative_to(root).as_posix()
            existing = (
                db()
                .execute(
                    "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'code' AND rel_path = ?",
                    (project["id"], rel),
                )
                .fetchone()
            )
            db().execute("BEGIN")
            try:
                if existing:
                    area_id = existing["id"]
                    db().execute(
                        "UPDATE project_areas SET source = 'manual', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (area_id,),
                    )
                else:
                    area_id = (
                        db()
                        .execute(
                            "INSERT INTO project_areas(project_id, kind, rel_path, source) VALUES (?, 'code', ?, 'manual')",
                            (project["id"], rel),
                        )
                        .lastrowid
                    )
                container_registry.validated_area_roots(
                    db(), project, deep_ops_scan=True
                )
                db().execute(
                    "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
                    "VALUES (?, 'project.area.add', 'project', ?, ?)",
                    (user["id"], slug, json.dumps({"rel_path": rel})),
                )
                db().execute("COMMIT")
            except container_registry.ContainerBoundaryError as exc:
                db().execute("ROLLBACK")
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception:
                db().execute("ROLLBACK")
                raise
        if area_id is not None:
            _notify_code_graphs(
                owner_user_id=int(user["id"]),
                container_slug=slug,
                project_id=int(project["id"]),
                added_rels=[rel],
            )
        return {"id": area_id, "rel_path": rel, "source": "manual"}

    @app.patch("/api/projects/{slug}/areas/{area_id}")
    def update_project_area(
        slug: str,
        area_id: int,
        payload: ProjectAreaUpdateRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Per-area settings - today that is the T9 push-after-merge toggle
        (default off). Turning it ON requires a detected git remote: the
        toggle is only ever offered where a push could go somewhere, and the
        connector is BYO - Proxima pushes with the host's own git, so there
        is no remote to configure in-app. Turning it OFF always works."""
        project = visible_project(slug, user)
        with container_registry.container_mutation_lock(db(), project):
            row = (
                db()
                .execute(
                    "SELECT id, kind, source, rel_path, push_on_merge FROM project_areas WHERE id = ? AND project_id = ?",
                    (area_id, project["id"]),
                )
                .fetchone()
            )
            if not row or row["kind"] != "code" or row["source"] == "excluded":
                raise HTTPException(status_code=404, detail="code area not found")
            remote = None
            if payload.push_on_merge:
                try:
                    repo = container_registry.resolve_area_root(
                        db(), project, int(row["id"])
                    )
                except container_registry.ContainerBoundaryError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                remote = repo_remote.detect_remote(repo)
                if remote is None:
                    raise HTTPException(
                        status_code=409,
                        detail="this code area has no git remote - add one with your own git (git remote add ...) and re-open settings",
                    )
            # Enabling PINS the remote URL the owner is opting into (audit F3):
            # the push-time target must still match it, so an agent rewriting the
            # repo's own .git/config cannot silently redirect a later push.
            # Disabling clears the pin - re-enabling re-reads and re-pins.
            pinned_url = remote["url"] if payload.push_on_merge and remote else None
            db().execute(
                "UPDATE project_areas SET push_on_merge = ?, push_remote_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (1 if payload.push_on_merge else 0, pinned_url, area_id),
            )
            db().execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, 'project.area.push_on_merge', 'project', ?, ?)",
                (
                    user["id"],
                    slug,
                    json.dumps(
                        {
                            "rel_path": row["rel_path"],
                            "push_on_merge": payload.push_on_merge,
                            "push_remote_url": pinned_url,
                        }
                    ),
                ),
            )
        return {
            "id": area_id,
            "rel_path": row["rel_path"],
            "push_on_merge": payload.push_on_merge,
            "push_remote_url": pinned_url,
            "remote": remote,
        }

    @app.delete("/api/projects/{slug}/areas/{area_id}")
    def remove_project_area(
        slug: str, area_id: int, user: dict[str, Any] = Depends(current_user)
    ):
        """Remove a code area. The row becomes an 'excluded' tombstone (not a
        delete) so auto-re-detection cannot resurrect an area the owner
        explicitly removed; the tombstone is garbage-collected once the folder
        stops being detectable."""
        project = visible_project(slug, user)
        with container_registry.container_mutation_lock(db(), project):
            row = (
                db()
                .execute(
                    "SELECT id, kind, source, rel_path FROM project_areas WHERE id = ? AND project_id = ?",
                    (area_id, project["id"]),
                )
                .fetchone()
            )
            if not row or row["kind"] != "code" or row["source"] == "excluded":
                raise HTTPException(status_code=404, detail="code area not found")
            db().execute(
                "UPDATE project_areas SET source = 'excluded', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (area_id,),
            )
            db().execute(
                "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, 'project.area.remove', 'project', ?, ?)",
                (user["id"], slug, json.dumps({"rel_path": row["rel_path"]})),
            )
        return {"ok": True, "id": area_id}

    @app.post("/api/projects/{slug}/areas/detect")
    def detect_project_areas(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Re-run code-area auto-detection on demand. Only auto rows follow the
        filesystem; manual and excluded rows are never clobbered."""
        project = visible_project(slug, user)
        with container_registry.container_mutation_lock(db(), project):
            # Repair a missing Ops row at "." - never scaffold ops/ into a
            # linked folder from a detection request (prune C2).
            ensure_ops_area(db(), project["id"], rel_path=".")
            summary = sync_code_areas(db(), project["id"], project["path"])
        _notify_code_graphs(
            owner_user_id=int(user["id"]),
            container_slug=slug,
            project_id=int(project["id"]),
            added_rels=list(summary.get("added") or []),
        )
        return {
            **_with_remotes(project, areas_payload(db(), project["id"])),
            "detect": summary,
        }

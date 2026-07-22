"""Project routes for the Proxima API.

Single-user cockpit: every project belongs to the sole owner. No membership,
sharing, invites, or visibility — those were the multi-user surface.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .. import fsapi, repo_remote
from ..project_areas import areas_payload, ensure_ops_area, sync_code_areas
from ..settings import validate_slug
from ..provisioning import scaffold_project_dir
from ..schemas import ProjectAreaAddRequest, ProjectAreaUpdateRequest, ProjectCreateRequest, ProjectLinkRequest, ProjectUpdateRequest


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    project_payload = deps["project_payload"]
    run_projectctl = deps["run_projectctl"]
    _purge_project = deps["_purge_project"]

    @app.get("/api/projects")
    def list_projects(user: dict[str, Any] = Depends(current_user)):
        rows = db().execute(
            "SELECT p.id, p.slug, p.name, p.path, p.visibility, u.username AS owner, 'owner' AS role "
            "FROM projects p JOIN users u ON u.id = p.owner_user_id "
            "WHERE p.archived_at IS NULL ORDER BY p.created_at DESC, p.id DESC"
        ).fetchall()
        return {"projects": [project_payload(dict(row)) for row in rows]}

    def _link_roots() -> list[Path]:
        return [Path(p).expanduser().resolve() for p in (cfg.get("link_roots") or [os.path.expanduser("~")])]

    def _within_link_roots(p: Path) -> bool:
        rp = p.resolve()
        return any(rp == r or r in rp.parents for r in _link_roots())

    @app.get("/api/fs/dirs")
    def fs_dirs(path: str = "", user: dict[str, Any] = Depends(current_user)):
        """Browse directories under the configured link roots, to pick an existing
        folder to register as a project."""
        roots = _link_roots()
        base = Path(path).expanduser().resolve() if path else roots[0]
        if not (base in roots or _within_link_roots(base)) or not base.is_dir():
            base = roots[0]
        dirs = []
        try:
            for child in sorted(base.iterdir(), key=lambda c: c.name.lower()):
                try:
                    if child.is_dir() and not child.name.startswith("."):
                        dirs.append({"name": child.name, "path": str(child)})
                except OSError:
                    pass
        except OSError:
            pass
        parent = str(base.parent) if (base not in roots and _within_link_roots(base.parent)) else None
        return {"path": str(base), "parent": parent, "dirs": dirs, "roots": [str(r) for r in roots]}

    @app.post("/api/projects/link", status_code=201)
    def link_project(payload: ProjectLinkRequest, user: dict[str, Any] = Depends(current_user)):
        """Register an EXISTING folder as a project (no scaffold). The project's
        path points at the real folder, so chat/terminal/files operate on it."""
        target = Path(payload.path).expanduser().resolve()
        if not _within_link_roots(target):
            raise HTTPException(status_code=403, detail="path is outside the allowed roots")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="not a directory")
        name = (payload.name or target.name).strip()
        # strip("-") AFTER the 63-char truncation too: [:63] can re-cut a collapsed
        # run mid-hyphen and leave a trailing '-', which validate_slug would reject.
        base_slug = ((payload.slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:63].strip("-")) or "project"
        try:
            slug = validate_slug(base_slug)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid project slug: {exc}") from exc
        if db().execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone():
            raise HTTPException(status_code=409, detail=f"slug '{slug}' already exists — pick another")
        cur = db().execute(
            "INSERT INTO projects(slug, name, path, owner_user_id, visibility) VALUES (?, ?, ?, ?, 'private')",
            (slug, name, str(target), user["id"]),
        )
        pid = cur.lastrowid
        # Container areas (T1): register the ops area + auto-detect code areas.
        ensure_ops_area(db(), pid)
        sync_code_areas(db(), pid, target)
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, 'project.link', 'project', ?, ?)", (user["id"], slug, json.dumps({"path": str(target)})))
        row = dict(db().execute("SELECT p.*, u.username AS owner, 'owner' AS role FROM projects p JOIN users u ON u.id = p.owner_user_id WHERE p.id = ?", (pid,)).fetchone())
        return project_payload(row)

    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectCreateRequest, user: dict[str, Any] = Depends(current_user)):
        if db().execute("SELECT id FROM projects WHERE slug = ?", (payload.slug,)).fetchone():
            raise HTTPException(status_code=409, detail="project slug already exists")
        path = str(Path(cfg["workspace_root"]) / "projects" / payload.slug)
        run_projectctl("create-project", payload.slug, "--owner", user["os_user"])
        scaffold_project_dir(cfg, payload.slug)
        cur = db().execute(
            "INSERT INTO projects(slug, name, path, owner_user_id, visibility) VALUES (?, ?, ?, ?, 'private')",
            (payload.slug, payload.name, path, user["id"]),
        )
        project_id = cur.lastrowid
        ensure_ops_area(db(), project_id)
        sync_code_areas(db(), project_id, path)
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.create', 'project', ?)", (user["id"], payload.slug))
        row = dict(db().execute("SELECT p.*, ? AS owner, 'owner' AS role FROM projects p WHERE p.id = ?", (user["username"], project_id)).fetchone())
        return project_payload(row)

    @app.get("/api/projects/{slug}")
    def get_project(slug: str, user: dict[str, Any] = Depends(current_user)):
        return project_payload(visible_project(slug, user))

    @app.patch("/api/projects/{slug}")
    def update_project(slug: str, payload: ProjectUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        project = visible_project(slug, user)
        if payload.name is not None:
            db().execute("UPDATE projects SET name = ? WHERE id = ?", (payload.name.strip(), project["id"]))
            db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.rename', 'project', ?)", (user["id"], slug))
        row = dict(db().execute(
            "SELECT p.*, u.username AS owner, 'owner' AS role FROM projects p JOIN users u ON u.id = p.owner_user_id WHERE p.id = ?",
            (project["id"],),
        ).fetchone())
        return project_payload(row)

    @app.delete("/api/projects/{slug}")
    def delete_project(slug: str, user: dict[str, Any] = Depends(current_user)):
        project = visible_project(slug, user)
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.delete', 'project', ?)", (user["id"], slug))
        _purge_project(project)  # rm dir (jailed) + DB row; cascades tasks, nulls session/run links
        return {"ok": True, "slug": slug}

    # ── Work-container areas (Phase-1 slice 1, T1): code areas + ops area ──

    def _with_remotes(project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Pair each code area with its detected git remote (T9, slice 11) so
        the settings UI knows whether to offer the push-after-merge toggle -
        no remote, no toggle. Only the dedicated areas endpoints pay for this
        (it shells out to the host's git); project list payloads never do."""
        root = Path(project["path"])
        for area in payload["code_areas"]:
            repo = root if area["rel_path"] == "." else root / area["rel_path"]
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
    def add_project_area(slug: str, payload: ProjectAreaAddRequest, user: dict[str, Any] = Depends(current_user)):
        """Manually register (or correct) a code area - T1's hybrid override.
        The folder must exist inside the project but need not be a git repo yet
        (not-yet-`git init`'d code is a valid code area). A manual row is never
        clobbered by re-detection; re-adding a removed area revives it."""
        project = visible_project(slug, user)
        root = Path(project["path"]).resolve()
        if not root.is_dir():
            raise HTTPException(status_code=400, detail="project folder is missing on disk")
        try:
            target = fsapi.resolve_in_project(root, payload.rel_path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="not a directory inside the project")
        rel = "." if target == root else target.relative_to(root).as_posix()
        existing = db().execute(
            "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'code' AND rel_path = ?",
            (project["id"], rel),
        ).fetchone()
        if existing:
            area_id = existing["id"]
            db().execute("UPDATE project_areas SET source = 'manual', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (area_id,))
        else:
            area_id = db().execute(
                "INSERT INTO project_areas(project_id, kind, rel_path, source) VALUES (?, 'code', ?, 'manual')",
                (project["id"], rel),
            ).lastrowid
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, 'project.area.add', 'project', ?, ?)", (user["id"], slug, json.dumps({"rel_path": rel})))
        return {"id": area_id, "rel_path": rel, "source": "manual"}

    @app.patch("/api/projects/{slug}/areas/{area_id}")
    def update_project_area(slug: str, area_id: int, payload: ProjectAreaUpdateRequest, user: dict[str, Any] = Depends(current_user)):
        """Per-area settings - today that is the T9 push-after-merge toggle
        (default off). Turning it ON requires a detected git remote: the
        toggle is only ever offered where a push could go somewhere, and the
        connector is BYO - Proxima pushes with the host's own git, so there
        is no remote to configure in-app. Turning it OFF always works."""
        project = visible_project(slug, user)
        row = db().execute(
            "SELECT id, kind, source, rel_path, push_on_merge FROM project_areas WHERE id = ? AND project_id = ?",
            (area_id, project["id"]),
        ).fetchone()
        if not row or row["kind"] != "code" or row["source"] == "excluded":
            raise HTTPException(status_code=404, detail="code area not found")
        remote = None
        if payload.push_on_merge:
            root = Path(project["path"])
            repo = root if row["rel_path"] == "." else root / row["rel_path"]
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
            (user["id"], slug, json.dumps({"rel_path": row["rel_path"], "push_on_merge": payload.push_on_merge, "push_remote_url": pinned_url})),
        )
        return {"id": area_id, "rel_path": row["rel_path"], "push_on_merge": payload.push_on_merge, "push_remote_url": pinned_url, "remote": remote}

    @app.delete("/api/projects/{slug}/areas/{area_id}")
    def remove_project_area(slug: str, area_id: int, user: dict[str, Any] = Depends(current_user)):
        """Remove a code area. The row becomes an 'excluded' tombstone (not a
        delete) so auto-re-detection cannot resurrect an area the owner
        explicitly removed; the tombstone is garbage-collected once the folder
        stops being detectable."""
        project = visible_project(slug, user)
        row = db().execute(
            "SELECT id, kind, source, rel_path FROM project_areas WHERE id = ? AND project_id = ?",
            (area_id, project["id"]),
        ).fetchone()
        if not row or row["kind"] != "code" or row["source"] == "excluded":
            raise HTTPException(status_code=404, detail="code area not found")
        db().execute("UPDATE project_areas SET source = 'excluded', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (area_id,))
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, 'project.area.remove', 'project', ?, ?)", (user["id"], slug, json.dumps({"rel_path": row["rel_path"]})))
        return {"ok": True, "id": area_id}

    @app.post("/api/projects/{slug}/areas/detect")
    def detect_project_areas(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Re-run code-area auto-detection on demand. Only auto rows follow the
        filesystem; manual and excluded rows are never clobbered."""
        project = visible_project(slug, user)
        ensure_ops_area(db(), project["id"])
        summary = sync_code_areas(db(), project["id"], project["path"])
        return {**_with_remotes(project, areas_payload(db(), project["id"])), "detect": summary}

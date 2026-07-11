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

from .. import features
from ..settings import validate_slug
from ..provisioning import scaffold_project_dir
from ..schemas import ProjectCreateRequest, ProjectLinkRequest, ProjectVisibilityRequest


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    project_payload = deps["project_payload"]
    run_projectctl = deps["run_projectctl"]
    _purge_project = deps["_purge_project"]

    def _parse_video_studio_id(studio_id: str) -> tuple[str, str] | None:
        prefix = "proxima-video__"
        if not studio_id.startswith(prefix):
            return None
        parts = studio_id[len(prefix):].split("__", 1)
        if len(parts) != 2:
            return None
        slug, video_id = parts
        if not slug or not video_id or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", video_id):
            return None
        return slug, video_id

    def _video_studio_payload(studio_id: str, user: dict[str, Any]) -> dict[str, Any] | None:
        parsed = _parse_video_studio_id(studio_id)
        if not parsed:
            return None
        features.require(cfg, features.VIDEO)
        slug, video_id = parsed
        project = visible_project(slug, user)
        root = Path(project["path"]).resolve()
        video_dir = (root / "artifacts" / "video" / video_id).resolve()
        if root not in video_dir.parents or not (video_dir / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        files: list[str] = []
        for p in sorted(video_dir.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(video_dir)))
        compositions = [p for p in files if p == "index.html" or (p.endswith(".html") and p.startswith("compositions/"))]
        return {
            "id": studio_id,
            "slug": studio_id,
            "name": video_id,
            "dir": str(video_dir),
            "files": files,
            "compositions": compositions or ["index.html"],
        }

    @app.get("/api/projects")
    def list_projects(user: dict[str, Any] = Depends(current_user)):
        rows = db().execute(
            "SELECT p.slug, p.name, p.path, p.visibility, u.username AS owner, 'owner' AS role "
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
        db().execute("INSERT INTO audit_log(actor_user_id, action, target_type, target_id) VALUES (?, 'project.create', 'project', ?)", (user["id"], payload.slug))
        row = dict(db().execute("SELECT p.*, ? AS owner, 'owner' AS role FROM projects p WHERE p.id = ?", (user["username"], project_id)).fetchone())
        return project_payload(row)

    @app.get("/api/projects/{slug}")
    def get_project(slug: str, user: dict[str, Any] = Depends(current_user)):
        video_payload = _video_studio_payload(slug, user)
        if video_payload:
            return video_payload
        return project_payload(visible_project(slug, user))

    @app.patch("/api/projects/{slug}")
    def update_project(slug: str, payload: ProjectVisibilityRequest, user: dict[str, Any] = Depends(current_user)):
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

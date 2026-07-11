"""Wiki routes (project wiki + personal per-user wiki) for the Proxima API.

Extracted via the register() pattern — handler bodies verbatim. No behavior change.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .. import fsapi
from ..settings import validate_slug
from ..schemas import FileWriteRequest, FsPathRequest, FsRenameRequest


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    _project_root = deps["_project_root"]

    @app.get("/api/projects/{slug}/wiki/all")
    def project_wiki_all(slug: str, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        return {"notes": fsapi.walk_files(root, "wiki")}

    # ── Personal per-user wiki (workspace_root/users/<username>/wiki) ──
    def _wiki_root(user: dict[str, Any]) -> Path:
        root = Path(cfg["workspace_root"]) / "users" / validate_slug(user["username"]) / "wiki"
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
            (root / "index.md").write_text(f"# {user['username']}'s wiki\n\nYour personal notes.\n", encoding="utf-8")
        return root

    def _audit_wiki(user: dict[str, Any], action: str, path: str) -> None:
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, ?, 'wiki', ?, ?)",
            (user["id"], action, user["username"], json.dumps({"path": path})),
        )

    @app.get("/api/wiki/all")
    def wiki_all(user: dict[str, Any] = Depends(current_user)):
        return {"notes": fsapi.walk_files(_wiki_root(user))}

    @app.get("/api/wiki/tree")
    def wiki_tree(path: str = "", user: dict[str, Any] = Depends(current_user)):
        try:
            return {"path": path, "entries": fsapi.list_tree(_wiki_root(user), path)}
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/wiki/file")
    def wiki_read_file(path: str, user: dict[str, Any] = Depends(current_user)):
        try:
            return {"path": path, "content": fsapi.read_file(_wiki_root(user), path)}
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/wiki/file")
    def wiki_write_file(path: str, payload: FileWriteRequest, user: dict[str, Any] = Depends(current_user)):
        try:
            fsapi.write_file(_wiki_root(user), path, payload.content)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_wiki(user, "wiki.write", path)
        return {"ok": True, "path": path}
    @app.post("/api/wiki/fs/mkdir")
    def wiki_mkdir(payload: FsPathRequest, user: dict[str, Any] = Depends(current_user)):
        try:
            fsapi.mkdir(_wiki_root(user), payload.path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_wiki(user, "wiki.mkdir", payload.path)
        return {"ok": True, "path": payload.path}

    @app.post("/api/wiki/fs/rename")
    def wiki_rename(payload: FsRenameRequest, user: dict[str, Any] = Depends(current_user)):
        try:
            fsapi.rename(_wiki_root(user), payload.from_, payload.to)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_wiki(user, "wiki.rename", f"{payload.from_} -> {payload.to}")
        return {"ok": True}

    @app.delete("/api/wiki/fs")
    def wiki_delete(path: str, user: dict[str, Any] = Depends(current_user)):
        try:
            fsapi.delete(_wiki_root(user), path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_wiki(user, "wiki.delete", path)
        return {"ok": True, "path": path}

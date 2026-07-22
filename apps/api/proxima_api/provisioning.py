from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from .project_areas import ensure_ops_area, sync_code_areas

logger = logging.getLogger("proxima.provisioning")


def _projects_root(cfg: dict[str, Any]) -> Path:
    return Path(cfg["workspace_root"]) / "projects"


def scaffold_project_dir(cfg: dict[str, Any], slug: str) -> Path:
    """Create projects/<slug>/ with starter subdirs + README. Idempotent, no ACL."""
    # Belt-and-suspenders: reject slugs that could escape the projects root.
    if "/" in slug or "\\" in slug or ".." in slug or slug.startswith("."):
        raise ValueError(f"unsafe slug: {slug!r}")
    path = _projects_root(cfg) / slug
    path.mkdir(parents=True, exist_ok=True)
    for sub in cfg.get("provision_starter_dirs") or ["wiki", "tasks", "artifacts"]:
        (path / sub).mkdir(parents=True, exist_ok=True)
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {slug}\n\nProxima project workspace.\n", encoding="utf-8")
    return path


def _audit(conn: sqlite3.Connection, actor_user_id: int | None, action: str, slug: str, metadata: str = "{}") -> None:
    conn.execute(
        "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) "
        "VALUES (?, ?, 'project', ?, ?)",
        (actor_user_id, action, slug, metadata),
    )


def _resolve_private_slug(conn: sqlite3.Connection, user: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Return (slug, existing_row_or_None) for the user's private project.

    Invariant: if a row is returned it is guaranteed to be visibility=='private'
    AND owner_user_id==user['id'], so it is safe to adopt.  If no row is
    returned the slug is free and a new project should be created there.
    """
    base = user["username"]
    candidates = [base, f"{base}-home", f"{base}-{user['id']}"]
    for slug in candidates:
        row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            return slug, None  # free slug — create new
        if row["visibility"] == "private" and row["owner_user_id"] == user["id"]:
            return slug, dict(row)  # this user's own existing private project — adopt
        # slug is taken by another (possibly legacy) project — try next candidate
    # Extremely unlikely fallback: guaranteed-unique slug
    return f"{base}-{user['id']}-home", None


def provision_private_project(conn: sqlite3.Connection, cfg: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    slug, existing = _resolve_private_slug(conn, user)
    if existing:
        # Only ever reached when the row is verified as this user's own private project.
        scaffold_project_dir(cfg, slug)
        ensure_ops_area(conn, existing["id"])
        return existing
    path = str(scaffold_project_dir(cfg, slug))
    cur = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id, visibility) VALUES (?, ?, ?, ?, 'private')",
        (slug, f"{user['username']} (personal)", path, user["id"]),
    )
    project_id = cur.lastrowid
    # Container areas (T1): ops area + code-area auto-detect at creation.
    ensure_ops_area(conn, project_id)
    sync_code_areas(conn, project_id, path)
    _audit(conn, user["id"], "workspace.provision.private", slug)
    return dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())


def provision_user_workspace(conn: sqlite3.Connection, cfg: dict[str, Any], user: dict[str, Any]) -> None:
    """Provision a user's private project. Never raises.

    Single-user access is owner_user_id-scoped; project_members was removed as
    inert multi-user plumbing.
    """
    if not cfg.get("auto_provision", True):
        return
    try:
        provision_private_project(conn, cfg, user)
    except Exception:
        logger.exception("provision_user_workspace failed for user %s", user.get("username"))
        try:
            _audit(conn, user.get("id"), "workspace.provision.error", str(user.get("username")))
        except Exception:
            pass


def backfill(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    """Ensure every user has a private project.

    Single-user access is owner_user_id-scoped; shared-project membership rows are
    no longer maintained.
    """
    if not cfg.get("auto_provision", True):
        return {"users": 0}
    users = [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()]
    for user in users:
        provision_private_project(conn, cfg, user)
    return {"users": len(users)}

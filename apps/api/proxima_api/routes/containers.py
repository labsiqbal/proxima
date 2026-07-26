"""Authenticated Container and Fleet registry routes."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from .. import container_registry, repo_remote
from ..schemas import Container, ContainerAreas, ContainerListResponse


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]

    def _owned_container(slug: str, user: dict[str, Any]) -> dict[str, Any]:
        container = container_registry.get_fleet_container(db(), int(user["id"]), slug)
        if container is None:
            raise HTTPException(status_code=404, detail="container not found")
        return container

    @app.get("/api/containers", response_model=ContainerListResponse)
    def list_containers(user: dict[str, Any] = Depends(current_user)):
        """List the owner's Fleet registry with directly aggregated Live state."""
        return {
            "containers": container_registry.list_fleet_containers(
                db(),
                int(user["id"]),
            )
        }

    @app.get("/api/containers/{slug}", response_model=Container)
    def get_container(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Read one owner-scoped Container and its current Fleet indicators."""
        return _owned_container(slug, user)

    @app.get("/api/containers/{slug}/areas", response_model=ContainerAreas)
    def list_container_areas(
        slug: str,
        user: dict[str, Any] = Depends(current_user),
    ):
        """List targetable Areas after canonical Container-boundary validation."""
        container = _owned_container(slug, user)
        try:
            roots = container_registry.validated_area_roots(db(), container)
        except container_registry.ContainerBoundaryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        rows = db().execute(
            """
            SELECT id, kind, rel_path, source, push_on_merge, push_remote_url
            FROM project_areas
            WHERE project_id = ? AND source != 'excluded'
            ORDER BY kind, rel_path, id
            """,
            (container["id"],),
        ).fetchall()
        areas: list[dict[str, Any]] = []
        for row in rows:
            area = {
                "id": int(row["id"]),
                "kind": row["kind"],
                "rel_path": row["rel_path"],
                "source": row["source"],
                "push_on_merge": bool(row["push_on_merge"]),
                "push_remote_url": row["push_remote_url"],
                "remote": None,
            }
            if row["kind"] == "code":
                area["remote"] = repo_remote.detect_remote(roots[int(row["id"])])
            areas.append(area)
        ops_area = next((area for area in areas if area["kind"] == "ops"), None)
        if ops_area is None:
            raise HTTPException(
                status_code=409,
                detail="Container must have exactly one active Ops Area",
            )
        return {
            "container_id": container["id"],
            "container_slug": container["slug"],
            "code_areas": [area for area in areas if area["kind"] == "code"],
            "ops_area": ops_area,
        }

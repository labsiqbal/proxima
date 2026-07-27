"""Authenticated, feature-gated Graphify state and explicit rebuild routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException

from .. import features
from ..graph_context import (
    GraphBuildError,
    GraphContextError,
    GraphScopeError,
)
from ..schemas import (
    GraphRebuildRequest,
    GraphStatePayload,
    GraphStatesResponse,
)


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]

    def _require_master() -> None:
        features.require(app.state.config, features.MASTER_ORCHESTRATOR)

    def _detail(exc: GraphContextError) -> dict[str, str]:
        return {"code": exc.code, "message": str(exc)}

    @app.get("/api/containers/{slug}/graphs", response_model=GraphStatesResponse)
    def list_graph_states(
        slug: str,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Read exact scoped graph freshness without exposing filesystem paths."""
        _require_master()
        try:
            graphs = app.state.graph_context.list_states(
                owner_user_id=int(user["id"]),
                container_slug=slug,
            )
        except GraphScopeError as exc:
            raise HTTPException(status_code=409, detail=_detail(exc)) from exc
        return {"graphs": graphs}

    @app.post("/api/containers/{slug}/graphs/rebuild", response_model=GraphStatePayload)
    def rebuild_graph(
        slug: str,
        request: GraphRebuildRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Run one bounded explicit build through the server-owned adapter."""
        _require_master()
        try:
            result = app.state.graph_context.rebuild(
                owner_user_id=int(user["id"]),
                container_slug=slug,
                kind=request.kind,
                area_id=request.area_id,
            )
        except GraphScopeError as exc:
            raise HTTPException(status_code=422, detail=_detail(exc)) from exc
        except GraphBuildError as exc:
            raise HTTPException(status_code=409, detail=_detail(exc)) from exc
        db().execute(
            "INSERT INTO audit_log("
            "actor_user_id, action, target_type, target_id, metadata"
            ") VALUES (?, 'graph.rebuild', 'container', ?, ?)",
            (
                int(user["id"]),
                str(result["scope"]["container_id"]),
                json.dumps(
                    {
                        "graph_state_id": result["id"],
                        "kind": result["scope"]["kind"],
                        "area_id": result["scope"]["area_id"],
                        "state": result["state"],
                        "generation": result["generation"],
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        return result

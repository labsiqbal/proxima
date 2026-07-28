"""Typed Master context router over Fleet, Live state, Knowledge, and Code layers.

ADR-6: never merge fleet-wide graphs. Live state always comes from SQLite.
Focused Knowledge/Code queries never leak another Container's graph nodes.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Callable, Mapping

from . import container_registry
from .graph_context import (
    GraphContextError,
    GraphContextService,
    GraphScopeError,
    SEMANTIC_BACKEND_LOCAL,
)

log = logging.getLogger("proxima.context_router")

LAYER_FLEET = "fleet"
LAYER_LIVE = "live"
LAYER_KNOWLEDGE = "knowledge"
LAYER_CODE = "code"
ALL_LAYERS = frozenset({LAYER_FLEET, LAYER_LIVE, LAYER_KNOWLEDGE, LAYER_CODE})

_FLEET_RE = re.compile(
    r"\b(containers?|fleet|projects?|which containers|what containers|"
    r"list containers|my containers)\b",
    re.IGNORECASE,
)
_LIVE_RE = re.compile(
    r"\b(running|blocked|green|status|queued|in review|live state|"
    r"what is running|what's running|whats running|currently running|"
    r"attention|stuck|failed jobs?)\b",
    re.IGNORECASE,
)
_KNOWLEDGE_RE = re.compile(
    r"\b(know about|knowledge|decision|decisions|facts?|about |"
    r"relationship|history|wiki|ops notes?|container\.md|"
    r"what do we know|remember about)\b",
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"\b(function|class|symbol|import|call graph|code structure|"
    r"who calls|what calls|impact of|module|method|repo structure|"
    r"source code|implementation)\b",
    re.IGNORECASE,
)


def classify_layers(query: str) -> list[str]:
    """Return ordered layers for a natural-language query (bounded, mixed OK)."""
    text = (query or "").strip()
    if not text:
        return []
    layers: list[str] = []
    if _LIVE_RE.search(text):
        layers.append(LAYER_LIVE)
    if _FLEET_RE.search(text):
        layers.append(LAYER_FLEET)
    if _KNOWLEDGE_RE.search(text):
        layers.append(LAYER_KNOWLEDGE)
    if _CODE_RE.search(text):
        layers.append(LAYER_CODE)
    # Default: a bare question with a container leans Knowledge; otherwise Fleet.
    if not layers:
        layers.append(LAYER_KNOWLEDGE)
    # Stable order: fleet, live, knowledge, code.
    order = [LAYER_FLEET, LAYER_LIVE, LAYER_KNOWLEDGE, LAYER_CODE]
    return [layer for layer in order if layer in set(layers)]


class ContextRouter:
    """Server-owned routing of Master ``query_context`` to scoped layers."""

    def __init__(
        self,
        app: Any,
        db_factory: Callable[[], sqlite3.Connection],
        graphs: GraphContextService,
    ):
        self.app = app
        self._db_factory = db_factory
        self.graphs = graphs

    @property
    def config(self) -> Mapping[str, Any]:
        return getattr(self.app.state, "config", {}) or {}

    def route(
        self,
        *,
        owner_user_id: int,
        query: str,
        container_id: int | None = None,
        area_id: int | None = None,
        focus_container_id: int | None = None,
        token_budget: int | None = None,
        result_limit: int | None = None,
    ) -> dict[str, Any]:
        layers = classify_layers(query)
        # Cap mixed requests so one turn cannot open every graph in the fleet.
        max_layers = max(1, min(4, int(self.config.get("context_router_max_layers", 3))))
        layers = layers[:max_layers]

        resolved_container_id = container_id
        if resolved_container_id is None and focus_container_id is not None:
            resolved_container_id = focus_container_id

        budget = self._token_budget(token_budget)
        limit = self._result_limit(result_limit)
        per_layer_budget = max(256, budget // max(1, len(layers)))

        results: list[dict[str, Any]] = []
        for layer in layers:
            if layer == LAYER_FLEET:
                results.append(
                    self._fleet(
                        owner_user_id=owner_user_id,
                        container_id=resolved_container_id,
                        limit=limit,
                    )
                )
            elif layer == LAYER_LIVE:
                results.append(
                    self._live(
                        owner_user_id=owner_user_id,
                        container_id=resolved_container_id,
                        limit=limit,
                    )
                )
            elif layer == LAYER_KNOWLEDGE:
                results.append(
                    self._knowledge(
                        owner_user_id=owner_user_id,
                        container_id=resolved_container_id,
                        query=query,
                        token_budget=per_layer_budget,
                        result_limit=limit,
                    )
                )
            elif layer == LAYER_CODE:
                results.append(
                    self._code(
                        owner_user_id=owner_user_id,
                        container_id=resolved_container_id,
                        area_id=area_id,
                        query=query,
                        token_budget=per_layer_budget,
                        result_limit=limit,
                    )
                )

        egress = bool(self.config.get("graph_semantic_egress_enabled"))
        return {
            "available": True,
            "query": query.strip()[:4000],
            "layers": layers,
            "results": results,
            "policy": {
                "semantic_egress_enabled": egress,
                "semantic_backend_default": (
                    "disabled" if egress else SEMANTIC_BACKEND_LOCAL
                ),
                "local_only": not egress,
                "merges_fleet_graphs": False,
            },
            "budgets": {
                "token_budget": budget,
                "result_limit": limit,
                "max_layers": max_layers,
            },
        }

    def _token_budget(self, requested: int | None) -> int:
        ceiling = min(
            16_000,
            max(256, int(self.config.get("graph_query_token_budget", 2000))),
        )
        if requested is None:
            return ceiling
        return min(ceiling, max(256, int(requested)))

    def _result_limit(self, requested: int | None) -> int:
        ceiling = min(
            200,
            max(1, int(self.config.get("graph_query_result_limit", 40))),
        )
        if requested is None:
            return min(ceiling, 20)
        return min(ceiling, max(1, int(requested)))

    def _container_slug(
        self,
        *,
        owner_user_id: int,
        container_id: int,
    ) -> str | None:
        row = self._db_factory().execute(
            "SELECT slug FROM projects "
            "WHERE id = ? AND owner_user_id = ? AND archived_at IS NULL",
            (container_id, owner_user_id),
        ).fetchone()
        return str(row["slug"]) if row is not None else None

    def _fleet(
        self,
        *,
        owner_user_id: int,
        container_id: int | None,
        limit: int,
    ) -> dict[str, Any]:
        rows = container_registry.list_fleet_containers(
            self._db_factory(),
            owner_user_id,
        )
        if container_id is not None:
            rows = [row for row in rows if int(row["id"]) == int(container_id)]
        containers = []
        for row in rows[:limit]:
            containers.append(
                {
                    "id": int(row["id"]),
                    "slug": str(row["slug"]),
                    "name": str(row.get("name") or row["slug"]),
                    "status": row.get("status") or row.get("registry_status"),
                }
            )
        return {
            "layer": LAYER_FLEET,
            "available": True,
            "source": "fleet_registry",
            "items": containers,
            "citations": [],
            "provenance": [],
            "freshness": {"state": "live", "source": "sqlite"},
        }

    def _live(
        self,
        *,
        owner_user_id: int,
        container_id: int | None,
        limit: int,
    ) -> dict[str, Any]:
        where = ["p.owner_user_id = ?"]
        params: list[Any] = [owner_user_id]
        if container_id is not None:
            where.append("j.project_id = ?")
            params.append(int(container_id))
        params.append(limit)
        rows = self._db_factory().execute(
            "SELECT j.id, j.title, j.status, j.project_id AS container_id, "
            "j.target_area_id AS area_id, j.blocked_reason, j.updated_at "
            "FROM jobs j "
            "LEFT JOIN projects p ON p.id = j.project_id "
            f"WHERE {' AND '.join(where)} "
            "AND j.status IN ('queued', 'running', 'review', 'blocked') "
            "ORDER BY "
            "CASE j.status "
            "WHEN 'running' THEN 0 WHEN 'blocked' THEN 1 "
            "WHEN 'review' THEN 2 ELSE 3 END, "
            "j.updated_at DESC, j.id DESC "
            "LIMIT ?",
            params,
        ).fetchall()
        items = [
            {
                "id": int(row["id"]),
                "title": str(row["title"]),
                "status": str(row["status"]),
                "container_id": (
                    int(row["container_id"])
                    if row["container_id"] is not None
                    else None
                ),
                "area_id": (
                    int(row["area_id"]) if row["area_id"] is not None else None
                ),
                "blocked_reason": row["blocked_reason"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        return {
            "layer": LAYER_LIVE,
            "available": True,
            "source": "sqlite_live_state",
            "items": items,
            "citations": [],
            "provenance": [],
            "freshness": {"state": "live", "source": "sqlite"},
            # Explicit so missing graphs never look like missing live state.
            "independent_of_graphs": True,
        }

    def _knowledge(
        self,
        *,
        owner_user_id: int,
        container_id: int | None,
        query: str,
        token_budget: int,
        result_limit: int,
    ) -> dict[str, Any]:
        if container_id is None:
            return {
                "layer": LAYER_KNOWLEDGE,
                "available": False,
                "source": "knowledge_graph",
                "error": {
                    "code": "container_required",
                    "message": (
                        "Knowledge graph queries require one Container Focus "
                        "or container_id"
                    ),
                },
                "items": [],
                "citations": [],
                "provenance": [],
            }
        slug = self._container_slug(
            owner_user_id=owner_user_id,
            container_id=container_id,
        )
        if slug is None:
            return {
                "layer": LAYER_KNOWLEDGE,
                "available": False,
                "source": "knowledge_graph",
                "error": {
                    "code": "container_not_found",
                    "message": "Container was not found",
                },
                "items": [],
                "citations": [],
                "provenance": [],
            }
        try:
            result = self.graphs.query(
                owner_user_id=owner_user_id,
                container_slug=slug,
                kind="knowledge",
                question=query,
                area_id=None,
                token_budget=token_budget,
                result_limit=result_limit,
            )
        except GraphScopeError as exc:
            return {
                "layer": LAYER_KNOWLEDGE,
                "available": False,
                "source": "knowledge_graph",
                "error": {"code": exc.code, "message": str(exc)},
                "items": [],
                "citations": [],
                "provenance": [],
            }
        except GraphContextError as exc:
            return {
                "layer": LAYER_KNOWLEDGE,
                "available": False,
                "source": "knowledge_graph",
                "error": {"code": exc.code, "message": str(exc)},
                "items": [],
                "citations": [],
                "provenance": [],
            }
        return self._graph_layer_payload(
            layer=LAYER_KNOWLEDGE,
            result=result,
            container_id=container_id,
        )

    def _code(
        self,
        *,
        owner_user_id: int,
        container_id: int | None,
        area_id: int | None,
        query: str,
        token_budget: int,
        result_limit: int,
    ) -> dict[str, Any]:
        if container_id is None:
            return {
                "layer": LAYER_CODE,
                "available": False,
                "source": "code_graph",
                "error": {
                    "code": "container_required",
                    "message": (
                        "Code graph queries require one Container and one "
                        "registered repo Area"
                    ),
                },
                "items": [],
                "citations": [],
                "provenance": [],
            }
        slug = self._container_slug(
            owner_user_id=owner_user_id,
            container_id=container_id,
        )
        if slug is None:
            return {
                "layer": LAYER_CODE,
                "available": False,
                "source": "code_graph",
                "error": {
                    "code": "container_not_found",
                    "message": "Container was not found",
                },
                "items": [],
                "citations": [],
                "provenance": [],
            }
        resolved_area = area_id
        if resolved_area is None:
            sample = self._db_factory().execute(
                "SELECT id FROM project_areas "
                "WHERE project_id = ? AND kind = 'code' AND source != 'excluded' "
                "ORDER BY rel_path, id LIMIT 2",
                (container_id,),
            ).fetchall()
            if len(sample) == 1:
                resolved_area = int(sample[0]["id"])
            elif not sample:
                return {
                    "layer": LAYER_CODE,
                    "available": False,
                    "source": "code_graph",
                    "error": {
                        "code": "area_required",
                        "message": "No code Area is registered in this Container",
                    },
                    "items": [],
                    "citations": [],
                    "provenance": [],
                }
            else:
                candidates = self._db_factory().execute(
                    "SELECT id FROM project_areas "
                    "WHERE project_id = ? AND kind = 'code' "
                    "AND source != 'excluded' "
                    "ORDER BY rel_path, id",
                    (container_id,),
                ).fetchall()
                return {
                    "layer": LAYER_CODE,
                    "available": False,
                    "source": "code_graph",
                    "error": {
                        "code": "area_required",
                        "message": (
                            "Code graph queries require an exact area_id when "
                            "multiple code Areas exist"
                        ),
                    },
                    "items": [],
                    "citations": [],
                    "provenance": [],
                    "candidate_area_ids": [int(r["id"]) for r in candidates],
                }
        try:
            result = self.graphs.query(
                owner_user_id=owner_user_id,
                container_slug=slug,
                kind="code",
                question=query,
                area_id=int(resolved_area),
                token_budget=token_budget,
                result_limit=result_limit,
            )
        except GraphScopeError as exc:
            return {
                "layer": LAYER_CODE,
                "available": False,
                "source": "code_graph",
                "error": {"code": exc.code, "message": str(exc)},
                "items": [],
                "citations": [],
                "provenance": [],
            }
        except GraphContextError as exc:
            return {
                "layer": LAYER_CODE,
                "available": False,
                "source": "code_graph",
                "error": {"code": exc.code, "message": str(exc)},
                "items": [],
                "citations": [],
                "provenance": [],
            }
        return self._graph_layer_payload(
            layer=LAYER_CODE,
            result=result,
            container_id=container_id,
        )

    def _graph_layer_payload(
        self,
        *,
        layer: str,
        result: Mapping[str, Any],
        container_id: int,
    ) -> dict[str, Any]:
        scope = dict(result.get("scope") or {})
        # Hard isolation: refuse any result that does not match the requested
        # Container id (defense in depth against adapter mistakes).
        if scope.get("container_id") not in (None, container_id):
            log.error(
                "context router refused cross-container graph result "
                "requested=%s got=%s layer=%s",
                container_id,
                scope.get("container_id"),
                layer,
            )
            return {
                "layer": layer,
                "available": False,
                "source": f"{layer}_graph",
                "error": {
                    "code": "scope_isolation",
                    "message": "Graph result escaped its Container scope",
                },
                "items": [],
                "citations": [],
                "provenance": [],
            }
        available = bool(result.get("available", True)) and not result.get("error")
        payload = {
            "layer": layer,
            "available": available,
            "source": f"{layer}_graph",
            "scope": {
                "container_id": scope.get("container_id"),
                "container_slug": scope.get("container_slug"),
                "kind": scope.get("kind"),
                "area_id": scope.get("area_id"),
            },
            "generation": result.get("generation"),
            "freshness": result.get("freshness") or {},
            "items": list(result.get("items") or []),
            "citations": list(result.get("citations") or []),
            "provenance": list(result.get("provenance") or []),
            "limits": result.get("limits") or {},
        }
        if result.get("error"):
            payload["error"] = result["error"]
            payload["available"] = False
        return payload

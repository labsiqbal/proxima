"""Pure graph primitives for the ADR-0001 workflow engine.

This module owns the stable, runner-agnostic shape of a workflow graph: parsing,
normalization, DAG validation, deterministic topological ordering, ready-set
selection, and typed output contracts. It deliberately performs no database,
HTTP, or runner I/O so the executor and API can share one definition.
"""
from __future__ import annotations

import heapq
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

from jsonschema import exceptions as jsonschema_exceptions  # pyright: ignore[reportMissingModuleSource]
from jsonschema import validators as jsonschema_validators  # pyright: ignore[reportMissingModuleSource]

OutputKind: TypeAlias = Literal["text", "json", "artifact-ref"]
Graph: TypeAlias = dict[str, Any]

_OUTPUT_KINDS: frozenset[str] = frozenset({"text", "json", "artifact-ref"})
_RUNNABLE_STATUSES: frozenset[str] = frozenset({"pending", "stale"})


class GraphValidationError(ValueError):
    """Raised when a workflow graph or node output contract is invalid."""


@dataclass(frozen=True, slots=True)
class OutputContract:
    """The typed value a node promises to produce.

    ``schema`` is a validated JSON Schema document. Runtime *value* validation
    belongs to the graph advancer; invalid contract definitions are rejected here.
    """

    kind: OutputKind = "text"
    schema: dict[str, Any] | None = None


def parse_output_contract(raw: Mapping[str, Any]) -> OutputContract:
    """Parse a node's flat ``output_kind`` / ``output_schema`` declaration."""
    kind = raw["output_kind"] if "output_kind" in raw else "text"
    if not isinstance(kind, str) or kind not in _OUTPUT_KINDS:
        allowed = ", ".join(sorted(_OUTPUT_KINDS))
        raise GraphValidationError(f"output_kind must be one of: {allowed}")

    schema = raw.get("output_schema")
    if schema is not None:
        if kind != "json":
            raise GraphValidationError("output_schema is only valid when output_kind is 'json'")
        if not isinstance(schema, dict):
            raise GraphValidationError("output_schema must be an object")
        schema = deepcopy(schema)
        try:
            validator = jsonschema_validators.validator_for(schema)
            validator.check_schema(schema)
        except jsonschema_exceptions.SchemaError as exc:
            raise GraphValidationError(f"output_schema is invalid: {exc.message}") from exc

    return OutputContract(kind=cast(OutputKind, kind), schema=schema)


def _parse_raw_graph(raw: Mapping[str, Any] | str) -> Mapping[str, Any]:
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GraphValidationError(f"graph is not valid JSON: {exc.msg}") from exc
        if not isinstance(decoded, dict):
            raise GraphValidationError("graph must be a JSON object")
        return decoded
    if not isinstance(raw, Mapping):
        raise GraphValidationError("graph must be an object or JSON object string")
    return raw


def normalize_graph(raw: Mapping[str, Any] | str) -> Graph:
    """Return a canonical, validated ``{nodes, edges}`` DAG.

    Canonical edges use ``{"from": <upstream>, "to": <downstream>}``. For
    planner/UI interoperability, ``source``/``target`` aliases are accepted.
    A node's optional ``depends_on`` list is accepted as input and materialized
    as edges, then removed so canonical graphs have one dependency source.
    """
    source = _parse_raw_graph(raw)
    raw_nodes = source.get("nodes")
    raw_edges = source.get("edges", [])
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise GraphValidationError("graph.nodes must be a non-empty array")
    if not isinstance(raw_edges, list):
        raise GraphValidationError("graph.edges must be an array")

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            raise GraphValidationError(f"node at index {index} must be an object")
        node_id = raw_node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise GraphValidationError(f"node at index {index} must have a non-empty string id")
        node_id = node_id.strip()
        if node_id in node_ids:
            raise GraphValidationError(f"duplicate node id: {node_id}")
        node_ids.add(node_id)

        name = raw_node.get("name", node_id)
        instruction = raw_node.get("instruction", "")
        depends_on = raw_node.get("depends_on", [])
        if not isinstance(name, str) or not name.strip():
            raise GraphValidationError(f"node '{node_id}' must have a non-empty name")
        if not isinstance(instruction, str):
            raise GraphValidationError(f"node '{node_id}' instruction must be a string")
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list) or any(
            not isinstance(dep, str) or not dep.strip() for dep in depends_on
        ):
            raise GraphValidationError(f"node '{node_id}' depends_on must be an array of node ids")

        contract = parse_output_contract(raw_node)
        node = deepcopy(dict(raw_node))
        node.update(
            {
                "id": node_id,
                "name": name.strip(),
                "instruction": instruction.strip(),
                "depends_on": list(dict.fromkeys(dep.strip() for dep in depends_on)),
                "output_kind": contract.kind,
            }
        )
        if contract.schema is not None:
            node["output_schema"] = contract.schema
        else:
            node.pop("output_schema", None)
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str]] = set()

    def add_edge(edge_source: str, edge_target: str, raw_edge: Mapping[str, Any] | None = None) -> None:
        if edge_source not in node_ids:
            raise GraphValidationError(f"edge references unknown source node: {edge_source}")
        if edge_target not in node_ids:
            raise GraphValidationError(f"edge references unknown target node: {edge_target}")
        if edge_source == edge_target:
            raise GraphValidationError(f"self-edge is not allowed for node: {edge_source}")
        key = (edge_source, edge_target)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edge = deepcopy(dict(raw_edge)) if raw_edge is not None else {}
        edge.pop("source", None)
        edge.pop("target", None)
        edge.update({"from": edge_source, "to": edge_target})
        edges.append(edge)

    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, Mapping):
            raise GraphValidationError(f"edge at index {index} must be an object")
        edge_source = raw_edge.get("from", raw_edge.get("source"))
        edge_target = raw_edge.get("to", raw_edge.get("target"))
        if not isinstance(edge_source, str) or not edge_source.strip():
            raise GraphValidationError(f"edge at index {index} must have a source")
        if not isinstance(edge_target, str) or not edge_target.strip():
            raise GraphValidationError(f"edge at index {index} must have a target")
        add_edge(edge_source.strip(), edge_target.strip(), raw_edge)

    for node in nodes:
        for dependency in node["depends_on"]:
            add_edge(dependency, node["id"])
        # Edges are canonical. Keeping depends_on too would let a canvas edit
        # delete an edge only for normalize_graph() to silently recreate it.
        node.pop("depends_on", None)

    graph: Graph = deepcopy(dict(source))
    graph["nodes"] = nodes
    graph["edges"] = edges
    # Computing the order is also the acyclicity check. Keep the canonical graph
    # free of derived state so it remains stable as a frozen job snapshot.
    topological_order(graph)
    return graph


def dependency_map(graph: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Return each node's direct upstream dependencies in graph order."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    result = {node["id"]: [] for node in nodes}
    for edge in edges:
        result[edge["to"]].append(edge["from"])
    return {node_id: tuple(upstream) for node_id, upstream in result.items()}


def _find_cycle(downstream: Mapping[str, list[str]], node_order: list[str]) -> list[str]:
    """Return one deterministic cycle, excluding merely blocked descendants."""
    state: dict[str, int] = {node_id: 0 for node_id in node_order}
    stack: list[str] = []
    stack_positions: dict[str, int] = {}

    def visit(node_id: str) -> list[str]:
        state[node_id] = 1
        stack_positions[node_id] = len(stack)
        stack.append(node_id)
        for child_id in downstream[node_id]:
            if state[child_id] == 0:
                cycle = visit(child_id)
                if cycle:
                    return cycle
            elif state[child_id] == 1:
                return stack[stack_positions[child_id] :]
        stack.pop()
        stack_positions.pop(node_id, None)
        state[node_id] = 2
        return []

    for node_id in node_order:
        if state[node_id] == 0:
            cycle = visit(node_id)
            if cycle:
                return cycle
    return []


def topological_order(graph: Mapping[str, Any]) -> list[str]:
    """Return a deterministic topological node order; reject cyclic graphs."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_order = [node["id"] for node in nodes]
    node_positions = {node_id: index for index, node_id in enumerate(node_order)}
    indegree = {node_id: 0 for node_id in node_order}
    downstream = {node_id: [] for node_id in node_order}
    for edge in edges:
        edge_source = edge["from"]
        edge_target = edge["to"]
        if edge_source not in indegree or edge_target not in indegree:
            raise GraphValidationError("graph contains an edge whose node is missing")
        downstream[edge_source].append(edge_target)
        indegree[edge_target] += 1

    ready = [
        (node_positions[node_id], node_id)
        for node_id in node_order
        if indegree[node_id] == 0
    ]
    heapq.heapify(ready)
    result: list[str] = []
    while ready:
        _, node_id = heapq.heappop(ready)
        result.append(node_id)
        for child_id in downstream[node_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                heapq.heappush(ready, (node_positions[child_id], child_id))

    if len(result) != len(node_order):
        cyclic = _find_cycle(downstream, node_order)
        raise GraphValidationError(f"graph must be acyclic; cycle involves: {', '.join(cyclic)}")
    return result


def descendant_node_ids(graph: Mapping[str, Any], node_id: str) -> list[str]:
    """Return all transitive downstream nodes in deterministic topological order."""
    node_order = topological_order(graph)
    if node_id not in node_order:
        raise GraphValidationError(f"graph node not found: {node_id}")
    downstream: dict[str, list[str]] = {candidate: [] for candidate in node_order}
    for edge in graph.get("edges", []):
        downstream[edge["from"]].append(edge["to"])

    seen: set[str] = set()
    stack = list(reversed(downstream[node_id]))
    while stack:
        candidate = stack.pop()
        if candidate in seen:
            continue
        seen.add(candidate)
        stack.extend(reversed(downstream[candidate]))
    return [candidate for candidate in node_order if candidate in seen]


def ready_node_ids(
    graph: Mapping[str, Any],
    statuses: Mapping[str, str],
    *,
    runnable_statuses: frozenset[str] = _RUNNABLE_STATUSES,
) -> list[str]:
    """Return runnable nodes whose direct dependencies are all ``done``.

    Missing node-state rows are treated as ``pending``. The result follows the
    deterministic topological order, which gives the Phase-1 sequential executor
    a stable dispatch order while remaining suitable for Phase-2 parallelism.
    """
    dependencies = dependency_map(graph)
    return [
        node_id
        for node_id in topological_order(graph)
        if statuses.get(node_id, "pending") in runnable_statuses
        and all(statuses.get(parent_id) == "done" for parent_id in dependencies[node_id])
    ]

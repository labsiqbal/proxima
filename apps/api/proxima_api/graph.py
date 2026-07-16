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
NodeType: TypeAlias = Literal["agent", "trigger"]
Graph: TypeAlias = dict[str, Any]

_OUTPUT_KINDS: frozenset[str] = frozenset({"text", "json", "artifact-ref"})
_RUNNABLE_STATUSES: frozenset[str] = frozenset({"pending", "stale"})
_NODE_TYPES: frozenset[str] = frozenset({"agent", "trigger"})
# Only manual entry exists today. Schedule/webhook/event arrive as further kinds
# on this node, which is the whole point of modelling the entry point as a node:
# adding one is a new kind here, not a new execution path.
_TRIGGER_KINDS: frozenset[str] = frozenset({"manual"})
TRIGGER_OUTPUT_KIND: OutputKind = "json"


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


def parse_node_type(raw: Mapping[str, Any], node_id: str) -> NodeType:
    """Parse a node's ``type``, defaulting to the agent node every graph had."""
    node_type = raw["type"] if "type" in raw else "agent"
    if not isinstance(node_type, str) or node_type not in _NODE_TYPES:
        allowed = ", ".join(sorted(_NODE_TYPES))
        raise GraphValidationError(f"node '{node_id}' type must be one of: {allowed}")
    return cast(NodeType, node_type)


def _parse_trigger_kind(raw: Mapping[str, Any], node_id: str) -> str:
    kind = raw["trigger_kind"] if "trigger_kind" in raw else "manual"
    if not isinstance(kind, str) or kind not in _TRIGGER_KINDS:
        allowed = ", ".join(sorted(_TRIGGER_KINDS))
        raise GraphValidationError(
            f"node '{node_id}' trigger_kind must be one of: {allowed}"
        )
    return kind


def _parse_profile_id(raw: Mapping[str, Any], node_id: str) -> int | None:
    """Parse the optional per-node execution agent.

    The value is only a *reference*. Whether the profile exists and belongs to the
    job's owner is settled by the executor against the database at dispatch time —
    this module performs no I/O, and an authorization check that ran here would be
    stale by the time the node runs anyway.
    """
    value = raw.get("profile_id")
    if value is None:
        return None
    # bool is an int subclass; profile_id=True must not silently mean profile 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise GraphValidationError(
            f"node '{node_id}' profile_id must be an integer or null"
        )
    return value


def _parse_coordinate(raw: Mapping[str, Any], node_id: str, axis: str) -> float | None:
    value = raw.get(axis)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphValidationError(f"node '{node_id}' {axis} must be a number or null")
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
        raise GraphValidationError(f"node '{node_id}' {axis} must be a finite number")
    return float(value)


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

        node_type = parse_node_type(raw_node, node_id)
        node = deepcopy(dict(raw_node))
        node.update(
            {
                "id": node_id,
                "type": node_type,
                "name": name.strip(),
                "instruction": instruction.strip(),
                "depends_on": list(dict.fromkeys(dep.strip() for dep in depends_on)),
            }
        )

        if node_type == "trigger":
            # A trigger emits the job input, so its contract is fixed rather than
            # authored: forcing it here keeps every downstream node's hand-off
            # typed the same way whether the graph starts at a trigger or not.
            node["trigger_kind"] = _parse_trigger_kind(raw_node, node_id)
            node["output_kind"] = TRIGGER_OUTPUT_KIND
            node.pop("output_schema", None)
            node.pop("profile_id", None)
            node.pop("review_required", None)
        else:
            contract = parse_output_contract(raw_node)
            node["output_kind"] = contract.kind
            node.pop("trigger_kind", None)
            if contract.schema is not None:
                node["output_schema"] = contract.schema
            else:
                node.pop("output_schema", None)
            profile_id = _parse_profile_id(raw_node, node_id)
            if profile_id is None:
                node.pop("profile_id", None)
            else:
                node["profile_id"] = profile_id

        for axis in ("x", "y"):
            coordinate = _parse_coordinate(raw_node, node_id, axis)
            if coordinate is None:
                node.pop(axis, None)
            else:
                node[axis] = coordinate
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

    _validate_triggers(nodes, edges)

    graph: Graph = deepcopy(dict(source))
    graph["nodes"] = nodes
    graph["edges"] = edges
    # Computing the order is also the acyclicity check. Keep the canonical graph
    # free of derived state so it remains stable as a frozen job snapshot.
    topological_order(graph)
    return graph


def _validate_triggers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Enforce that a trigger is an entry point, and that there is only one.

    A trigger with an upstream would have to wait for work to finish before the
    workflow could start, which is a contradiction. One trigger per graph keeps
    "when does this run" a single answer the owner can read off the canvas.
    """
    triggers = [node["id"] for node in nodes if node["type"] == "trigger"]
    if len(triggers) > 1:
        raise GraphValidationError(
            f"graph must have at most one trigger node; found: {', '.join(triggers)}"
        )
    if not triggers:
        return
    trigger_id = triggers[0]
    if any(edge["to"] == trigger_id for edge in edges):
        raise GraphValidationError(
            f"trigger node '{trigger_id}' must have no dependencies"
        )


def trigger_node_id(graph: Mapping[str, Any]) -> str | None:
    """Return the graph's trigger node id, if it has one."""
    for node in graph.get("nodes", []):
        if node.get("type") == "trigger":
            return str(node["id"])
    return None


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

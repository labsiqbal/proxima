from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

# pyrightconfig.json gives fresh language-server processes the local apps/api
# package path. The targeted ignore only covers pi's already-running process,
# whose module index predates this newly-created module.
from proxima_api.graph import (  # pyright: ignore[reportMissingImports]
    GraphValidationError,
    OutputContract,
    dependency_map,
    normalize_graph,
    parse_output_contract,
    ready_node_ids,
    topological_order,
)


def _expect_graph_error(pattern: str, call: Callable[[], Any]) -> None:
    try:
        call()
    except GraphValidationError as exc:
        assert re.search(pattern, str(exc)), str(exc)
    else:
        raise AssertionError("GraphValidationError was not raised")


def _diamond_graph():
    return normalize_graph(
        {
            "nodes": [
                {"id": "collect", "name": "Collect", "instruction": "Collect facts"},
                {"id": "draft-a", "name": "Draft A", "depends_on": ["collect"]},
                {"id": "draft-b", "name": "Draft B"},
                {"id": "merge", "name": "Merge", "depends_on": ["draft-a", "draft-b"]},
            ],
            "edges": [{"source": "collect", "target": "draft-b"}],
        }
    )


def test_normalize_graph_canonicalizes_edges_and_contracts():
    graph = normalize_graph(
        json.dumps(
            {
                "nodes": [
                    {"id": "research", "name": "Research"},
                    {
                        "id": "write",
                        "name": "Write",
                        "depends_on": ["research", "research"],
                        "output_kind": "json",
                        "output_schema": {"type": "object"},
                    },
                ],
                "edges": [{"source": "research", "target": "write", "label": "facts"}],
            }
        )
    )

    assert graph["nodes"][0]["output_kind"] == "text"
    assert "depends_on" not in graph["nodes"][1]
    assert graph["edges"] == [{"label": "facts", "from": "research", "to": "write"}]
    expected_dependencies = {"research": (), "write": ("research",)}
    assert dependency_map(graph) == expected_dependencies


def test_topological_order_is_stable_for_a_diamond():
    graph = _diamond_graph()

    assert topological_order(graph) == ["collect", "draft-a", "draft-b", "merge"]


def test_ready_set_requires_all_dependencies_done():
    graph = _diamond_graph()

    assert ready_node_ids(graph, {}) == ["collect"]
    assert ready_node_ids(graph, {"collect": "done"}) == ["draft-a", "draft-b"]
    assert ready_node_ids(
        graph,
        {"collect": "done", "draft-a": "done", "draft-b": "running"},
    ) == []
    assert ready_node_ids(
        graph,
        {"collect": "done", "draft-a": "done", "draft-b": "done", "merge": "stale"},
    ) == ["merge"]


def test_cycle_is_rejected_without_mislabeling_blocked_descendants():
    graph = {
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": "after-cycle"}],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},
            {"from": "b", "to": "after-cycle"},
        ],
    }
    _expect_graph_error(r"cycle involves: a, b$", lambda: normalize_graph(graph))


def test_canonical_graph_edge_can_be_deleted_without_depends_on_recreating_it():
    graph = normalize_graph(
        {"nodes": [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]}
    )
    graph["edges"] = []

    renormalized = normalize_graph(graph)

    assert renormalized["edges"] == []
    assert topological_order(renormalized) == ["a", "b"]


def test_invalid_graph_shapes_are_rejected():
    cases = [
        ({"nodes": []}, "non-empty"),
        ({"nodes": [{"id": "same"}, {"id": "same"}]}, "duplicate node id"),
        (
            {"nodes": [{"id": "a"}], "edges": [{"from": "a", "to": "missing"}]},
            "unknown target",
        ),
        (
            {"nodes": [{"id": "a"}], "edges": [{"from": "a", "to": "a"}]},
            "self-edge",
        ),
        (
            {"nodes": [{"id": "a", "depends_on": ["missing"]}]},
            "unknown source",
        ),
    ]
    for graph, message in cases:
        _expect_graph_error(message, lambda graph=graph: normalize_graph(graph))


def test_output_contract_parse_and_validation():
    assert parse_output_contract({}) == OutputContract(kind="text")
    assert parse_output_contract(
        {"output_kind": "json", "output_schema": {"type": "array"}}
    ) == OutputContract(kind="json", schema={"type": "array"})
    assert parse_output_contract({"output_kind": "artifact-ref"}) == OutputContract(
        kind="artifact-ref"
    )

    for invalid_kind in ("xml", "", None, False, 0):
        _expect_graph_error(
            "output_kind",
            lambda invalid_kind=invalid_kind: parse_output_contract(
                {"output_kind": invalid_kind}
            ),
        )
    _expect_graph_error(
        "only valid",
        lambda: parse_output_contract({"output_kind": "text", "output_schema": {}}),
    )
    _expect_graph_error(
        "must be an object",
        lambda: parse_output_contract({"output_kind": "json", "output_schema": []}),
    )
    _expect_graph_error(
        "output_schema is invalid",
        lambda: parse_output_contract(
            {"output_kind": "json", "output_schema": {"type": "not-a-json-type"}}
        ),
    )

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
    descendant_node_ids,
    node_touches_repo,
    normalize_graph,
    parse_output_contract,
    plan_target_problems,
    ready_node_ids,
    repo_target_paths,
    topological_order,
    unresolved_target_questions,
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


def test_descendants_follow_topological_order_without_duplicates():
    graph = _diamond_graph()

    assert descendant_node_ids(graph, "collect") == ["draft-a", "draft-b", "merge"]
    assert descendant_node_ids(graph, "draft-a") == ["merge"]
    assert descendant_node_ids(graph, "merge") == []


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
        ({"nodes": [{"id": "a", "type": "webhook"}]}, "type must be one of"),
        (
            {"nodes": [{"id": "a", "type": "trigger", "trigger_kind": "cron"}]},
            "trigger_kind must be one of",
        ),
        (
            {
                "nodes": [
                    {"id": "start", "type": "trigger"},
                    {"id": "also", "type": "trigger"},
                ]
            },
            "at most one trigger",
        ),
        (
            {
                "nodes": [{"id": "work"}, {"id": "start", "type": "trigger"}],
                "edges": [{"from": "work", "to": "start"}],
            },
            "must have no dependencies",
        ),
        ({"nodes": [{"id": "a", "expected_output": 5}]}, "expected_output must be a string"),
        ({"nodes": [{"id": "a", "rules": ["no"]}]}, "rules must be a string"),
        ({"nodes": [{"id": "a", "profile_id": "3"}]}, "profile_id must be"),
        ({"nodes": [{"id": "a", "profile_id": True}]}, "profile_id must be"),
        ({"nodes": [{"id": "a", "x": "12"}]}, "x must be a number"),
        ({"nodes": [{"id": "a", "y": float("inf")}]}, "y must be a finite"),
    ]
    for graph, message in cases:
        _expect_graph_error(message, lambda graph=graph: normalize_graph(graph))


def test_canvas_and_agent_fields_survive_normalization():
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "start", "type": "trigger", "name": "When I run it", "x": 10, "y": 20.5},
                {"id": "work", "name": "Work", "profile_id": 7, "depends_on": ["start"]},
            ]
        }
    )
    trigger, work = graph["nodes"]

    assert trigger["type"] == "trigger"
    assert trigger["trigger_kind"] == "manual"
    # A trigger emits the job input, so its contract is fixed rather than authored.
    assert trigger["output_kind"] == "json"
    assert (trigger["x"], trigger["y"]) == (10.0, 20.5)
    assert work["type"] == "agent"
    assert work["profile_id"] == 7
    # Positions are optional: a node the owner never dragged stays auto-laid-out.
    assert "x" not in work


def test_step_detail_survives_normalization_and_blanks_are_dropped():
    graph = normalize_graph(
        {
            "nodes": [
                {
                    "id": "write",
                    "name": "Write",
                    "expected_output": "  A 200-word brief  ",
                    "rules": "  Never invent a number  ",
                },
                {"id": "quiet", "name": "Quiet", "expected_output": "   ", "rules": ""},
            ]
        }
    )
    write, quiet = graph["nodes"]

    assert write["expected_output"] == "A 200-word brief"
    assert write["rules"] == "Never invent a number"
    # Absent, not empty: a blank field is not a constraint.
    assert "expected_output" not in quiet
    assert "rules" not in quiet


def test_trigger_ignores_authored_execution_fields():
    graph = normalize_graph(
        {
            "nodes": [
                {
                    "id": "start",
                    "type": "trigger",
                    "output_kind": "text",
                    "profile_id": 3,
                    "review_required": True,
                }
            ]
        }
    )

    trigger = graph["nodes"][0]
    assert trigger["output_kind"] == "json"
    assert "profile_id" not in trigger
    assert "review_required" not in trigger


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


def test_skill_ids_are_deduped_and_dropped_when_empty():
    graph = normalize_graph({
        "nodes": [
            {"id": "a", "name": "A", "skill_ids": [" research ", "research", "", "write"]},
            {"id": "b", "name": "B", "skill_ids": []},
        ]
    })
    a, b = graph["nodes"]

    assert a["skill_ids"] == ["research", "write"]
    # Absent, not empty — same rule as the prose fields.
    assert "skill_ids" not in b


# ── per-job target tags (Phase-1 slice 3, T1/T2) ─────────────────────────


def test_target_normalizes_and_touches_repo_is_derived_never_trusted():
    graph = normalize_graph({
        "nodes": [
            # An authored touches_repo that disagrees with its target is a lie
            # a repo job could use to dodge its worktree — always recomputed.
            {"id": "fix", "name": "Fix", "target": " apps/web ", "touches_repo": False},
            {"id": "report", "name": "Report", "target": "ops", "touches_repo": True},
            {"id": "legacy", "name": "Legacy"},
        ]
    })
    fix, report, legacy = graph["nodes"]

    assert fix["target"] == "apps/web"
    assert fix["touches_repo"] is True
    assert report["target"] == "ops"
    assert report["touches_repo"] is False
    # Pre-slice-3 nodes: no target, no repo binding — behavior unchanged.
    assert "target" not in legacy
    assert legacy["touches_repo"] is False


def test_ambiguous_target_is_a_question_not_a_guess():
    graph = normalize_graph({
        "nodes": [
            {"id": "a", "name": "A", "target": None, "target_ambiguous": True,
             "target_question": " which repo holds the docs? "},
            # A question with no target is ambiguous even without the flag.
            {"id": "b", "name": "B", "target_question": "web or api?"},
            # A chosen target IS the resolution: ambiguity flags are dropped.
            {"id": "c", "name": "C", "target": "ops", "target_ambiguous": True,
             "target_question": "stale question"},
        ]
    })
    a, b, c = graph["nodes"]

    assert a["target_ambiguous"] is True
    assert a["target_question"] == "which repo holds the docs?"
    assert a["touches_repo"] is False
    assert b["target_ambiguous"] is True
    assert "target_ambiguous" not in c and "target_question" not in c

    questions = unresolved_target_questions(graph)
    assert questions == [
        "job 'A': which repo holds the docs?",
        "job 'B': web or api?",
    ]


def test_target_shape_is_validated_and_triggers_carry_no_target():
    _expect_graph_error(
        "target must be a string",
        lambda: normalize_graph({"nodes": [{"id": "a", "name": "A", "target": 7}]}),
    )
    _expect_graph_error(
        "target_question must be a string",
        lambda: normalize_graph(
            {"nodes": [{"id": "a", "name": "A", "target_question": ["?"]}]}
        ),
    )
    graph = normalize_graph({
        "nodes": [
            {"id": "go", "name": "Go", "type": "trigger", "target": "apps/web",
             "target_ambiguous": True, "touches_repo": True},
        ]
    })
    trigger = graph["nodes"][0]
    for field in ("target", "target_ambiguous", "target_question", "touches_repo"):
        assert field not in trigger


def test_plan_target_problems_names_unknown_areas_only():
    graph = normalize_graph({
        "nodes": [
            {"id": "a", "name": "Fix web", "target": "apps/web"},
            {"id": "b", "name": "Write report", "target": "ops"},
            {"id": "c", "name": "Ghost", "target": "apps/ghost"},
            {"id": "d", "name": "Unbound"},
        ]
    })

    problems = plan_target_problems(graph, ["apps/web", "apps/api"])

    assert len(problems) == 1
    assert "Ghost" in problems[0] and "apps/ghost" in problems[0]
    assert "apps/web" in problems[0]  # the fix is named, not just the failure
    assert plan_target_problems(graph, []) != []
    assert plan_target_problems(
        normalize_graph({"nodes": [{"id": "x", "name": "X", "target": "ops"}]}), []
    ) == []


def test_repo_target_paths_are_distinct_in_graph_order():
    graph = normalize_graph({
        "nodes": [
            {"id": "a", "name": "A", "target": "apps/web"},
            {"id": "b", "name": "B", "target": "ops"},
            {"id": "c", "name": "C", "target": "apps/api"},
            {"id": "d", "name": "D", "target": "apps/web"},
        ]
    })
    assert repo_target_paths(graph) == ["apps/web", "apps/api"]


def test_node_touches_repo_reads_stored_graphs_tolerantly():
    stored = json.dumps(normalize_graph({
        "nodes": [
            {"id": "fix", "name": "Fix", "target": "."},
            {"id": "report", "name": "Report", "target": "ops"},
        ]
    }))

    assert node_touches_repo(stored, "fix") is True
    assert node_touches_repo(stored, "report") is False
    # The worker seam must degrade to "no repo binding", never raise.
    assert node_touches_repo(stored, "missing") is False
    assert node_touches_repo("not json", "fix") is False
    assert node_touches_repo("[1,2]", "fix") is False
    assert node_touches_repo(None, "fix") is False
    assert node_touches_repo('{"nodes": [{"id": "fix"}]}', "fix") is False

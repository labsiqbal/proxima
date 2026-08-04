"""Shape-aware sanitization of Master tool results (#165).

The broker used to refuse a whole tool response when any host path appeared in
it. Follow-the-folder (#130-#138) made a project a real folder, so container
payloads inherently carry absolute paths and the blunt rule killed
``list_containers`` and ``query_context``. These tests pin the replacement:
allowlist-shaped sanitization per payload, with the same hard boundary - no
absolute host path, home directory, or config location ever survives into the
model-visible result.
"""
from __future__ import annotations

import json

import pytest

from proxima_api.master_tool_sanitizer import (
    PATH_PLACEHOLDER,
    SECRET_PLACEHOLDER,
    UnsanitizablePayload,
    host_path_leak,
    redact_host_paths,
    sanitize_tool_result,
)

#: Paths an attacker (or an ordinary README) can plant in product text.
PLANTED_PATHS = [
    "/home/owner/secret-project",
    "/home/owner/.config/proxima/proxima.env",
    "~/.local/share/proxima/proxima.db",
    "~/.ssh/id_ed25519",
    "/etc/passwd",
    "/Users/owner/Library/Application Support/proxima",
    r"C:\Users\owner\proxima",
    r"\\fileserver\owner\proxima",
    "file:///home/owner/secret-project",
    "vscode://file/home/owner/secret-project",
    "/var/lib/proxima/workspace",
    "../../../etc/shadow",
]

#: Distinct fragments that must never survive anywhere in a sanitized payload.
PLANTED_FRAGMENTS = [
    "secret-project",
    "proxima.env",
    "proxima.db",
    "id_ed25519",
    "passwd",
    "Application Support",
    r"C:\Users",
    "fileserver",
    "shadow",
]


def _no_planted_paths(payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False)
    for fragment in PLANTED_FRAGMENTS:
        assert fragment not in encoded, f"{fragment!r} survived in {encoded!r}"
    assert host_path_leak(encoded) is None, encoded


# --------------------------------------------------------------------------
# The text redactor and the host-path verifier
# --------------------------------------------------------------------------


@pytest.mark.parametrize("planted", PLANTED_PATHS)
def test_redactor_removes_every_absolute_path_form(planted: str):
    redacted = redact_host_paths(f"Work lives in {planted} today")

    assert PATH_PLACEHOLDER in redacted
    assert host_path_leak(redacted) is None
    _no_planted_paths(redacted)


def test_redactor_keeps_scope_relative_product_text():
    text = "Ships docs/architecture.md and apps/web/src/App.tsx; see README.md."

    assert redact_host_paths(text) == text
    assert host_path_leak(text) is None


def test_redactor_keeps_ordinary_prose_and_remote_urls():
    text = "Frontend/backend split; see https://example.com/docs/reference (CI/CD)."

    assert redact_host_paths(text) == text
    assert host_path_leak(text) is None


def test_redactor_removes_credential_material_and_secret_files():
    redacted = redact_host_paths(
        "Token bearer sk-abcdefghijklmnop lives in .env next to id_rsa"
    )

    assert SECRET_PLACEHOLDER in redacted
    assert "sk-abcdefghijklmnop" not in redacted
    assert ".env" not in redacted
    assert "id_rsa" not in redacted


def test_a_path_with_spaces_loses_its_absolute_prefix():
    """An unquoted path containing a space cannot be matched past the space.

    The absolute prefix - the part that carries the home directory and the host
    layout - is always removed. A trailing fragment can survive, and it is then
    indistinguishable from ordinary scope-relative product text. Recorded as a
    test so the limitation is deliberate rather than discovered.
    """
    redacted = redact_host_paths("/Users/owner/Library/Application Support/proxima")

    assert redacted.startswith(PATH_PLACEHOLDER)
    assert "/Users" not in redacted and "owner" not in redacted
    assert host_path_leak(redacted) is None


def test_host_path_verifier_sees_through_percent_encoding():
    assert host_path_leak("%2Fhome%2Fowner%2Fsecret") is not None


# --------------------------------------------------------------------------
# Per-payload shapes
# --------------------------------------------------------------------------


def _container(**overrides: object) -> dict:
    payload = {
        "id": 7,
        "slug": "minarflow",
        "name": "Minarflow",
        "identity": "Minarflow",
        "summary": "Scheduling app.",
        "last_activity_at": "2026-08-02T10:00:00Z",
        "live": {"running_tasks": 1, "queued_tasks": 0, "open_attention": 2},
        "areas": {"total": 2, "code": 1, "ops": 1},
        "health": {
            "registry": "ready",
            "areas": "ready",
            "ops_migration": "complete",
            "graph_freshness": None,
        },
    }
    payload.update(overrides)
    return payload


def test_list_containers_passes_known_safe_fields_through():
    result = sanitize_tool_result(
        "list_containers", {"containers": [_container()]}
    )

    assert result == {"containers": [_container()]}


def test_list_containers_redacts_absolute_paths_inside_identity_text():
    result = sanitize_tool_result(
        "list_containers",
        {
            "containers": [
                _container(
                    name="Minarflow (/home/owner/secret-project)",
                    identity="checked out at ~/.local/share/proxima/proxima.db",
                    summary=(
                        'Config {"path": "/home/owner/.config/proxima/proxima.env"} '
                        "but docs/architecture.md is fine"
                    ),
                )
            ]
        },
    )

    container = result["containers"][0]
    assert container["slug"] == "minarflow"
    assert container["name"].startswith("Minarflow (")
    assert "docs/architecture.md" in container["summary"]
    _no_planted_paths(result)


def test_container_payload_drops_undeclared_fields():
    result = sanitize_tool_result(
        "list_containers",
        {
            "containers": [_container(path="/home/owner/secret-project")],
            "debug": {"root": "/home/owner/secret-project"},
        },
    )

    assert "path" not in result["containers"][0]
    assert "debug" not in result
    _no_planted_paths(result)


def test_get_container_keeps_area_ids_and_labels():
    result = sanitize_tool_result(
        "get_container",
        {
            "container": {
                **_container(),
                "target_areas": [
                    {"id": 3, "kind": "ops", "label": "Ops"},
                    {
                        "id": 4,
                        "kind": "code",
                        "label": "Code Area 1 (/home/owner/secret-project)",
                    },
                ],
            }
        },
    )

    areas = result["container"]["target_areas"]
    assert [area["id"] for area in areas] == [3, 4]
    assert areas[0]["label"] == "Ops"
    _no_planted_paths(result)


def test_task_payloads_keep_ids_statuses_and_timestamps():
    task = {
        "id": 11,
        "title": "Ship the runner",
        "status": "running",
        "container_id": 7,
        "area_id": 3,
        "blocked_reason": None,
        "created_at": "2026-08-02T09:00:00Z",
        "updated_at": "2026-08-02T09:30:00Z",
    }

    listed = sanitize_tool_result("list_tasks", {"tasks": [task]})
    live = sanitize_tool_result(
        "get_live_state",
        {
            "counts": {"queued": 1, "running": 1, "review": 0, "failed": 0},
            "tasks": [task],
        },
    )

    assert listed == {"tasks": [task]}
    assert live["counts"]["running"] == 1
    assert live["tasks"][0]["status"] == "running"


def test_delegate_and_start_results_keep_per_task_errors():
    result = sanitize_tool_result(
        "delegate_tasks",
        {
            "tasks": [
                {
                    "id": 12,
                    "title": "Task",
                    "status": "queued",
                    "container_id": 7,
                    "area_id": 3,
                    "blocked_reason": None,
                    "created_at": "2026-08-02T09:00:00Z",
                    "updated_at": "2026-08-02T09:00:00Z",
                    "created": True,
                    "started": False,
                    "error": {
                        "code": "task_start_failed",
                        "message": "cwd /home/owner/secret-project is gone",
                    },
                }
            ]
        },
    )

    task = result["tasks"][0]
    assert task["created"] is True and task["started"] is False
    assert task["error"]["code"] == "task_start_failed"
    _no_planted_paths(result)


def test_task_agents_and_recipes_keep_identity_fields():
    agents = sanitize_tool_result(
        "list_task_agents",
        {
            "task_agents": [
                {"id": 1, "name": "Default", "runner_id": "hermes", "is_default": 1}
            ]
        },
    )
    recipes = sanitize_tool_result(
        "list_recipes",
        {
            "recipes": [
                {
                    "id": 2,
                    "container_id": 7,
                    "name": "Nightly",
                    "description": "Runs at /home/owner/secret-project",
                    "category": "ops",
                    "engine": "graph",
                }
            ]
        },
    )

    assert agents["task_agents"][0]["runner_id"] == "hermes"
    assert recipes["recipes"][0]["name"] == "Nightly"
    _no_planted_paths(recipes)


def test_create_attention_result_is_ids_and_state_only():
    result = sanitize_tool_result(
        "create_attention",
        {
            "attention_id": 5,
            "decision_id": 6,
            "task_id": 7,
            "state": "open",
            "created_at": "2026-08-02T10:00:00Z",
        },
    )

    assert result["attention_id"] == 5
    assert result["state"] == "open"


# --------------------------------------------------------------------------
# query_context: the layered payload with scope-relative citations
# --------------------------------------------------------------------------


def _query_context_payload(**overrides: object) -> dict:
    payload = {
        "available": True,
        "query": "apa update terakhir",
        "layers": ["fleet", "live", "knowledge"],
        "results": [
            {
                "layer": "fleet",
                "available": True,
                "source": "fleet_registry",
                "items": [_container()],
                "citations": [],
                "provenance": [],
                "freshness": {"state": "live", "source": "sqlite"},
            },
            {
                "layer": "live",
                "available": True,
                "source": "sqlite_live_state",
                "items": [
                    {
                        "id": 11,
                        "title": "Ship the runner",
                        "status": "running",
                        "container_id": 7,
                        "area_id": 3,
                        "blocked_reason": None,
                        "updated_at": "2026-08-02T09:30:00Z",
                    }
                ],
                "citations": [],
                "provenance": [],
                "freshness": {"state": "live", "source": "sqlite"},
                "independent_of_graphs": True,
            },
            {
                "layer": "knowledge",
                "available": True,
                "source": "knowledge_graph",
                "scope": {
                    "container_id": 7,
                    "container_slug": "minarflow",
                    "kind": "knowledge",
                    "area_id": None,
                },
                "generation": "gen-3",
                "freshness": {"state": "fresh", "generation": "gen-3"},
                "items": [
                    {
                        "id": "node-1",
                        "label": "wiki/roadmap.md",
                        "type": "markdown",
                        "distance": 0,
                        "citations": [
                            {
                                "path": "wiki/roadmap.md",
                                "path_kind": "scope_relative",
                                "location": "line 12",
                            }
                        ],
                        "provenance": ["EXTRACTED"],
                        "relations": [
                            {
                                "direction": "out",
                                "relation": "mentions",
                                "node_id": "node-2",
                                "provenance": "EXTRACTED",
                            }
                        ],
                    }
                ],
                "citations": [
                    {
                        "path": "wiki/roadmap.md",
                        "path_kind": "scope_relative",
                        "location": "line 12",
                    }
                ],
                "provenance": [{"kind": "EXTRACTED", "edge_count": 4}],
                "limits": {"depth": 2, "result_limit": 20},
            },
        ],
        "policy": {
            "semantic_egress_enabled": False,
            "semantic_backend_default": "local",
            "local_only": True,
            "merges_fleet_graphs": False,
        },
        "budgets": {"token_budget": 2000, "result_limit": 20, "max_layers": 3},
    }
    payload.update(overrides)
    return payload


def test_query_context_keeps_layers_items_and_scope_relative_citations():
    result = sanitize_tool_result("query_context", _query_context_payload())

    assert result["layers"] == ["fleet", "live", "knowledge"]
    knowledge = result["results"][2]
    assert knowledge["citations"][0] == {
        "path": "wiki/roadmap.md",
        "path_kind": "scope_relative",
        "location": "line 12",
    }
    assert knowledge["items"][0]["label"] == "wiki/roadmap.md"
    assert result["results"][0]["items"][0]["slug"] == "minarflow"
    assert result["results"][1]["items"][0]["title"] == "Ship the runner"


def test_query_context_unavailable_form_passes_through():
    result = sanitize_tool_result(
        "query_context",
        {
            "available": False,
            "code": "feature_unavailable",
            "message": "Scoped graph context is not available in this release",
        },
    )

    assert result["available"] is False
    assert result["code"] == "feature_unavailable"


def test_query_context_refuses_an_absolute_citation_loudly():
    payload = _query_context_payload()
    payload["results"][2]["citations"][0]["path"] = "/home/owner/secret-project/x.md"

    with pytest.raises(UnsanitizablePayload) as raised:
        sanitize_tool_result("query_context", payload)

    message = str(raised.value)
    assert "query_context" in message
    assert "citations" in message
    assert "secret-project" not in message
    assert raised.value.next_step


def test_query_context_refuses_a_traversal_citation_loudly():
    payload = _query_context_payload()
    payload["results"][2]["items"][0]["citations"][0]["path"] = "../../etc/shadow"

    with pytest.raises(UnsanitizablePayload):
        sanitize_tool_result("query_context", payload)


def test_unknown_tool_is_refused_rather_than_passed_through():
    with pytest.raises(UnsanitizablePayload):
        sanitize_tool_result("not_a_tool", {"anything": 1})


# --------------------------------------------------------------------------
# Adversarial planting: nothing absolute survives, in any declared field
# --------------------------------------------------------------------------


@pytest.mark.parametrize("planted", PLANTED_PATHS)
def test_planted_paths_never_reach_the_model_in_any_payload(planted: str):
    payloads = {
        "list_containers": {
            "containers": [
                _container(
                    name=f"Project {planted}",
                    identity=planted,
                    summary=f'Notes: {{"root": "{planted}"}} and more prose',
                )
            ]
        },
        "get_container": {
            "container": {
                **_container(summary=f"cwd is {planted}"),
                "target_areas": [
                    {"id": 3, "kind": "ops", "label": f"Ops at {planted}"}
                ],
            }
        },
        "list_tasks": {
            "tasks": [
                {
                    "id": 11,
                    "title": f"Fix {planted}",
                    "status": "failed",
                    "container_id": 7,
                    "area_id": 3,
                    "blocked_reason": f"missing {planted}",
                    "created_at": "2026-08-02T09:00:00Z",
                    "updated_at": "2026-08-02T09:30:00Z",
                }
            ]
        },
        "list_recipes": {
            "recipes": [
                {
                    "id": 2,
                    "container_id": 7,
                    "name": f"Build {planted}",
                    "description": f"writes to {planted}",
                    "category": "ops",
                    "engine": "linear",
                }
            ]
        },
        "list_task_agents": {
            "task_agents": [
                {
                    "id": 1,
                    "name": f"Agent {planted}",
                    "runner_id": "hermes",
                    "is_default": 0,
                }
            ]
        },
    }
    for tool, payload in payloads.items():
        _no_planted_paths(sanitize_tool_result(tool, payload))

    graph_payload = _query_context_payload()
    graph_payload["query"] = f"where is {planted}"
    graph_payload["results"][0]["items"][0]["summary"] = f"root {planted}"
    graph_payload["results"][1]["items"][0]["blocked_reason"] = planted
    knowledge = graph_payload["results"][2]
    knowledge["items"][0]["label"] = f"node {planted}"
    knowledge["items"][0]["relations"][0]["relation"] = f"reads {planted}"
    knowledge["error"] = {"code": "graph_tampered", "message": f"at {planted}"}

    _no_planted_paths(sanitize_tool_result("query_context", graph_payload))


def test_planted_path_hidden_in_a_nested_unknown_structure_is_dropped():
    payload = {
        "containers": [
            {
                **_container(),
                "internal": {
                    "roots": [
                        {"absolute": "/home/owner/secret-project"},
                        "~/.config/proxima/proxima.env",
                    ]
                },
            }
        ]
    }

    _no_planted_paths(sanitize_tool_result("list_containers", payload))

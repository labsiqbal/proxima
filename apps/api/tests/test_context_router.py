"""Group 11: typed context router - layering, isolation, live-state independence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api.context_router import classify_layers
from proxima_api.graph_context import (
    GraphContextError,
    GraphScopeError,
    SEMANTIC_BACKEND_LOCAL,
)
from proxima_api.main import create_app
from proxima_api.master_tool_broker import MasterToolBroker


def _api(tmp_path: Path, **config) -> tuple[TestClient, dict[str, str]]:
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "auto_provision": False,
            "start_worker": False,
            "update_check": False,
            "container_registry_refresh_seconds": 0,
            "feature_master_orchestrator": True,
            **config,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def _container(api: TestClient, headers: dict[str, str], slug: str) -> dict:
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": slug, "name": slug},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    row = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = ?",
        (slug,),
    ).fetchone()
    assert row is not None
    payload["id"] = int(row["id"])
    return payload


def test_classify_layers_routes_intents():
    assert classify_layers("What is running?") == ["live"]
    assert classify_layers("Are any tasks failing?") == ["live"]
    assert "fleet" in classify_layers("Which containers do I have?")
    assert "knowledge" in classify_layers("What do we know about Acme?")
    assert "code" in classify_layers("What calls BillingService?")
    assert classify_layers("Where is BillingService defined?") == ["code"]
    assert "code" in classify_layers(
        "What would changing BillingService impact?"
    )
    assert "code" in classify_layers("blast radius of the auth module")
    mixed = classify_layers(
        "What is running and what do we know about Acme code structure?"
    )
    assert "live" in mixed
    assert "knowledge" in mixed
    assert "code" in mixed
    assert classify_layers(
        "Summarize the current context",
        container_scoped=True,
    ) == ["knowledge"]
    assert classify_layers("Give me an update") == ["fleet", "live"]


def test_query_context_uses_durable_scope_and_rejects_model_overrides(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    owner = _container(api, headers, "owner-scope")
    other = _container(api, headers, "model-scope")
    for project, repo_names in (
        (owner, ("repo-a", "repo-b")),
        (other, ("repo-other",)),
    ):
        root = Path(project["path"])
        for repo_name in repo_names:
            repo = root / repo_name
            (repo / ".git").mkdir(parents=True)
            (repo / "service.py").write_text(
                "class BillingService:\n    pass\n",
                encoding="utf-8",
            )
        detected = api.post(
            f"/api/projects/{project['slug']}/areas/detect",
            headers=headers,
        )
        assert detected.status_code == 200, detected.text
    owner_code_areas = api.get(
        "/api/projects/owner-scope/areas",
        headers=headers,
    ).json()["code_areas"]
    other_code_area_id = int(
        api.get(
            "/api/projects/model-scope/areas",
            headers=headers,
        ).json()["code_areas"][0]["id"]
    )
    owner_area_id = int(
        api.app.state.db.execute(
            "SELECT id FROM project_areas "
            "WHERE project_id = ? AND kind = 'ops'",
            (owner["id"],),
        ).fetchone()["id"]
    )
    other_area_id = int(
        api.app.state.db.execute(
            "SELECT id FROM project_areas "
            "WHERE project_id = ? AND kind = 'ops'",
            (other["id"],),
        ).fetchone()["id"]
    )
    session_id = api.get("/api/master/desk", headers=headers).json()["session"][
        "id"
    ]
    explicit_message_id = api.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) "
        "VALUES (?, 'user', 'Use my explicit target')",
        (session_id,),
    ).lastrowid
    api.app.state.db.execute(
        "INSERT INTO master_message_context("
        "message_id, focus_mode, focus_container_id, target_mode, "
        "target_container_id, target_area_id"
        ") VALUES (?, 'container', ?, 'explicit', ?, ?)",
        (
            explicit_message_id,
            owner["id"],
            owner["id"],
            owner_area_id,
        ),
    )
    explicit_broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        session_id,
        origin_message_id=explicit_message_id,
    )

    conflict = explicit_broker.execute(
        "query_context",
        {
            "query": "What do we know?",
            "container_id": int(other["id"]),
            "area_id": other_area_id,
        },
    )
    assert conflict["error"]["code"] == "context_scope_conflict"

    accepted = explicit_broker.execute(
        "query_context",
        {"query": "What do we know?"},
    )
    assert accepted["ok"] is True
    knowledge = accepted["result"]["results"][0]
    assert knowledge["scope"]["container_id"] == int(owner["id"])

    pinned_area_conflict = explicit_broker.execute(
        "query_context",
        {
            "query": "Where is BillingService defined?",
            "container_id": int(owner["id"]),
            "area_id": int(owner_code_areas[0]["id"]),
        },
    )
    assert pinned_area_conflict["error"]["code"] == "context_scope_conflict"

    explicit_container_message_id = api.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) "
        "VALUES (?, 'user', 'Use my explicit Container')",
        (session_id,),
    ).lastrowid
    api.app.state.db.execute(
        "INSERT INTO master_message_context("
        "message_id, focus_mode, focus_container_id, target_mode, "
        "target_container_id, target_area_id"
        ") VALUES (?, 'container', ?, 'explicit', ?, NULL)",
        (
            explicit_container_message_id,
            owner["id"],
            owner["id"],
        ),
    )
    explicit_container_broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        session_id,
        origin_message_id=explicit_container_message_id,
    )
    explicit_area = explicit_container_broker.execute(
        "query_context",
        {
            "query": "Where is BillingService defined?",
            "container_id": int(owner["id"]),
            "area_id": int(owner_code_areas[1]["id"]),
        },
    )
    assert explicit_area["ok"] is True
    explicit_code = next(
        item
        for item in explicit_area["result"]["results"]
        if item["layer"] == "code"
    )
    assert explicit_code["scope"]["area_id"] == int(owner_code_areas[1]["id"])
    explicit_cross_container = explicit_container_broker.execute(
        "query_context",
        {
            "query": "Where is BillingService defined?",
            "container_id": int(owner["id"]),
            "area_id": other_code_area_id,
        },
    )
    assert (
        explicit_cross_container["error"]["code"]
        == "context_scope_conflict"
    )

    focus_message_id = api.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) "
        "VALUES (?, 'user', 'Use my focused Container')",
        (session_id,),
    ).lastrowid
    api.app.state.db.execute(
        "INSERT INTO master_message_context("
        "message_id, focus_mode, focus_container_id, target_mode, "
        "target_container_id, target_area_id"
        ") VALUES (?, 'container', ?, 'auto', NULL, NULL)",
        (focus_message_id, owner["id"]),
    )
    focus_broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        session_id,
        origin_message_id=focus_message_id,
    )
    area_override = focus_broker.execute(
        "query_context",
        {
            "query": "Where is BillingService defined?",
            "container_id": int(owner["id"]),
            "area_id": int(owner_code_areas[0]["id"]),
        },
    )
    assert area_override["ok"] is True
    code = next(
        item
        for item in area_override["result"]["results"]
        if item["layer"] == "code"
    )
    assert code["scope"]["area_id"] == int(owner_code_areas[0]["id"])

    cross_container = focus_broker.execute(
        "query_context",
        {
            "query": "Where is BillingService defined?",
            "container_id": int(owner["id"]),
            "area_id": other_code_area_id,
        },
    )
    assert cross_container["error"]["code"] == "context_scope_conflict"


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            GraphScopeError("ops/private-note.md"),
            "graph_scope_invalid",
            "Graph scope is unavailable.",
        ),
        (
            GraphContextError("ops/private-note.md"),
            "graph_context_error",
            "Graph context is unavailable.",
        ),
    ],
)
def test_query_context_maps_graph_errors_to_path_free_public_failures(
    tmp_path: Path,
    monkeypatch,
    error: GraphContextError,
    code: str,
    message: str,
):
    api, headers = _api(tmp_path)
    project = _container(api, headers, "graph-error")
    desk = api.get("/api/master/desk", headers=headers).json()
    broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        desk["session"]["id"],
    )

    def fail_query(**_kwargs):
        raise error

    monkeypatch.setattr(api.app.state.graph_context, "query", fail_query)
    result = broker.execute(
        "query_context",
        {
            "query": "What do we know?",
            "container_id": int(project["id"]),
        },
    )

    public_error = result["result"]["results"][0]["error"]
    assert public_error == {"code": code, "message": message}
    assert "private-note" not in json.dumps(result)


def test_what_is_running_is_correct_with_missing_graphs(tmp_path: Path):
    api, headers = _api(tmp_path)
    project = _container(api, headers, "live-one")
    container_id = int(project["id"])
    # Seed a running job without building any graph.
    api.app.state.db.execute(
        "INSERT INTO jobs("
        "project_id, title, status, engine, created_by, steps_state"
        ") VALUES (?, 'Live job', 'running', 'linear', 1, '[]')",
        (container_id,),
    )
    desk = api.get("/api/master/desk", headers=headers).json()
    broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        desk["session"]["id"],
    )
    result = broker.execute(
        "query_context",
        {"query": "What is running?", "container_id": container_id},
    )
    assert result["ok"] is True
    payload = result["result"]
    assert payload["available"] is True
    assert payload["layers"] == ["live"]
    live = next(item for item in payload["results"] if item["layer"] == "live")
    assert live["available"] is True
    assert live["independent_of_graphs"] is True
    assert any(item["title"] == "Live job" for item in live["items"])
    assert payload["policy"]["local_only"] is True
    assert "graphify-out" not in json.dumps(payload)


def test_terminal_status_queries_filter_live_jobs_before_result_limit(
    tmp_path: Path,
):
    api, headers = _api(tmp_path, graph_query_result_limit=1)
    project = _container(api, headers, "terminal-live")
    container_id = int(project["id"])
    for title, status in (
        ("Completed job", "done"),
        ("Cancelled job", "cancelled"),
        ("Running job", "running"),
    ):
        api.app.state.db.execute(
            "INSERT INTO jobs("
            "project_id, title, status, engine, created_by, steps_state"
            ") VALUES (?, ?, ?, 'linear', 1, '[]')",
            (container_id, title, status),
        )
    desk = api.get("/api/master/desk", headers=headers).json()
    broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        desk["session"]["id"],
    )

    for query, expected_status in (
        ("What's done?", "done"),
        ("What completed?", "done"),
        ("What is green?", "done"),
        ("Which tasks were successful?", "done"),
        ("What was cancelled?", "cancelled"),
    ):
        result = broker.execute(
            "query_context",
            {"query": query, "container_id": container_id},
        )
        assert result["ok"] is True
        payload = result["result"]
        assert payload["layers"] == ["live"]
        live = payload["results"][0]
        assert [item["status"] for item in live["items"]] == [expected_status]


def test_knowledge_context_stays_in_focused_container(tmp_path: Path):
    api, headers = _api(tmp_path)
    acme = _container(api, headers, "acme")
    other = _container(api, headers, "other")
    for project, name in ((acme, "Acme"), (other, "Other")):
        ops = Path(project["path"]) / "ops"
        (ops / "container.md").write_text(
            f"# {name}\n\nUnique fact about {name}.\n",
            encoding="utf-8",
        )
        (ops / "wiki").mkdir(exist_ok=True)
        (ops / "wiki" / "note.md").write_text(
            f"# {name} note\n\nDecision for {name} only.\n",
            encoding="utf-8",
        )
        rebuilt = api.post(
            f"/api/containers/{project['slug']}/graphs/rebuild",
            headers=headers,
            json={"kind": "knowledge"},
        )
        assert rebuilt.status_code == 200, rebuilt.text

    desk = api.get("/api/master/desk", headers=headers).json()
    broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        desk["session"]["id"],
    )
    result = broker.execute(
        "query_context",
        {
            "query": "What do we know about this container?",
            "container_id": int(acme["id"]),
        },
    )
    assert result["ok"] is True
    payload = result["result"]
    knowledge = next(
        item for item in payload["results"] if item["layer"] == "knowledge"
    )
    assert knowledge["available"] is True
    assert knowledge["scope"]["container_id"] == int(acme["id"])
    serialized = json.dumps(payload)
    assert "Other" not in serialized
    assert str(other["id"]) not in serialized or knowledge["scope"]["container_id"] == int(
        acme["id"]
    )
    # Focused graph nodes must not include the other container id.
    assert knowledge["scope"]["container_id"] != int(other["id"])
    assert payload["policy"]["merges_fleet_graphs"] is False
    assert payload["policy"]["semantic_backend_default"] == SEMANTIC_BACKEND_LOCAL


def test_mixed_request_is_bounded_and_does_not_merge_graphs(tmp_path: Path):
    api, headers = _api(tmp_path)
    project = _container(api, headers, "mixed")
    ops = Path(project["path"]) / "ops"
    (ops / "container.md").write_text("# Mixed\n\nFacts.\n", encoding="utf-8")
    api.post(
        "/api/containers/mixed/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    desk = api.get("/api/master/desk", headers=headers).json()
    broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        desk["session"]["id"],
    )
    result = broker.execute(
        "query_context",
        {
            "query": "What is running and what do we know about Mixed?",
            "container_id": int(project["id"]),
        },
    )
    assert result["ok"] is True
    payload = result["result"]
    layers = payload["layers"]
    assert "live" in layers
    assert "knowledge" in layers
    assert len(layers) <= payload["budgets"]["max_layers"]
    # Separate layer results - no single merged fleet graph object.
    assert all("layer" in item for item in payload["results"])
    assert payload["policy"]["merges_fleet_graphs"] is False


def test_query_context_preserves_provenance_and_budgets(tmp_path: Path):
    api, headers = _api(tmp_path)
    project = _container(api, headers, "prov")
    ops = Path(project["path"]) / "ops"
    (ops / "container.md").write_text(
        "# Prov\n\n## Decision\nKeep provenance.\n",
        encoding="utf-8",
    )
    (ops / "wiki").mkdir(exist_ok=True)
    (ops / "wiki" / "note.md").write_text(
        "# Billing provenance\n\nKeep source-relative citations.\n",
        encoding="utf-8",
    )
    rebuilt = api.post(
        "/api/containers/prov/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    desk = api.get("/api/master/desk", headers=headers).json()
    broker = MasterToolBroker(
        api.app.state.db,
        api.app,
        {"id": 1},
        desk["session"]["id"],
    )
    result = broker.execute(
        "query_context",
        {
            "query": "What do we know about Billing provenance?",
            "container_id": int(project["id"]),
        },
    )
    assert result["ok"] is True
    knowledge = next(
        item for item in result["result"]["results"] if item["layer"] == "knowledge"
    )
    assert knowledge.get("generation", 0) >= 1
    assert "freshness" in knowledge
    assert any(
        citation["path"] == "wiki/note.md"
        and citation["path_kind"] == "scope_relative"
        for citation in knowledge["citations"]
    )
    assert "provenance" in knowledge
    assert result["result"]["budgets"]["token_budget"] >= 256

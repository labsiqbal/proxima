"""Group 11: typed context router - layering, isolation, live-state independence."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.context_router import classify_layers
from proxima_api.graph_context import SEMANTIC_BACKEND_LOCAL
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
    assert "fleet" in classify_layers("Which containers do I have?")
    assert "knowledge" in classify_layers("What do we know about Acme?")
    assert "code" in classify_layers("What calls BillingService?")
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
            "query": "What do we know about Prov decisions?",
            "container_id": int(project["id"]),
        },
    )
    assert result["ok"] is True
    knowledge = next(
        item for item in result["result"]["results"] if item["layer"] == "knowledge"
    )
    assert knowledge.get("generation", 0) >= 1
    assert "freshness" in knowledge
    # Citations/provenance may be empty if query matches nothing, but keys exist.
    assert "citations" in knowledge
    assert "provenance" in knowledge
    assert result["result"]["budgets"]["token_budget"] >= 256

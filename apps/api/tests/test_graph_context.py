from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import graph_context
from proxima_api.graph_context import (
    GRAPHIFY_VERSION,
    GraphBuildTimeout,
)
from proxima_api.main import create_app


def _api(
    tmp_path: Path,
    *,
    feature_enabled: bool = True,
    **config,
) -> tuple[TestClient, dict[str, str]]:
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
            "feature_master_orchestrator": feature_enabled,
            **config,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def _container(
    api: TestClient,
    headers: dict[str, str],
    *,
    slug: str = "graph-one",
    name: str = "Graph One",
    with_code: bool = True,
) -> tuple[dict, int | None]:
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": slug, "name": name},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    code_area_id = None
    if with_code:
        root = Path(project["path"])
        (root / ".git").mkdir()
        (root / "app.py").write_text(
            "class BillingService:\n"
            "    def charge(self):\n"
            "        return 'paid'\n",
            encoding="utf-8",
        )
        detected = api.post(
            f"/api/projects/{slug}/areas/detect",
            headers=headers,
        )
        assert detected.status_code == 200, detected.text
        code_area_id = int(detected.json()["code_areas"][0]["id"])
    return project, code_area_id


def _rebuild_code(
    api: TestClient,
    headers: dict[str, str],
    *,
    slug: str,
    area_id: int,
):
    response = api.post(
        f"/api/containers/{slug}/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_graph_routes_are_authenticated_feature_gated_and_path_free(
    tmp_path: Path,
):
    disabled, disabled_headers = _api(
        tmp_path / "disabled",
        feature_enabled=False,
    )
    _container(disabled, disabled_headers, with_code=False)
    off = disabled.get(
        "/api/containers/graph-one/graphs",
        headers=disabled_headers,
    )
    assert off.status_code == 503
    assert disabled.app.state.db.execute(
        "SELECT COUNT(*) FROM graph_states"
    ).fetchone()[0] == 0

    api, headers = _api(tmp_path / "enabled")
    project, area_id = _container(api, headers)
    password = api.post(
        "/auth/set-password",
        json={"password": "correct horse battery"},
    )
    assert password.status_code == 200
    auth = {"Authorization": f"Bearer {password.json()['token']}"}
    fresh = TestClient(api.app)

    assert fresh.get("/api/containers/graph-one/graphs").status_code == 401
    response = fresh.get(
        "/api/containers/graph-one/graphs",
        headers=auth,
    )
    assert response.status_code == 200, response.text
    graphs = response.json()["graphs"]
    assert [(item["scope"]["kind"], item["scope"]["area_id"]) for item in graphs] == [
        ("knowledge", None),
        ("code", area_id),
    ]
    by_kind = {item["scope"]["kind"]: item for item in graphs}
    # Knowledge remains missing until its later lifecycle group; Code is enqueued
    # at Area registration (Group 10).
    # Container registration enqueues Knowledge (Group 11); Code Areas enqueue
    # Code graphs (Group 10). Either may still be missing when Graphify is absent.
    assert by_kind["knowledge"]["state"] in {"missing", "queued", "building", "fresh"}
    assert by_kind["code"]["state"] in {"queued", "missing", "building", "fresh"}
    assert all(
        item["freshness"]["semantic_backend"] == "disabled"
        for item in graphs
    )
    serialized = json.dumps(graphs)
    assert project["path"] not in serialized
    assert "graphify-out" not in serialized


def test_code_rebuild_and_query_include_scope_freshness_citations_and_provenance(
    tmp_path: Path,
):
    api, headers = _api(
        tmp_path,
        graph_query_max_depth=2,
        graph_query_timeout_ms=5000,
        graph_query_token_budget=1000,
        graph_query_result_limit=10,
    )
    project, area_id = _container(api, headers)
    assert area_id is not None
    Path(project["path"], "ops", "must-not-leak.py").write_text(
        "OPS_SECRET = 'not code graph context'\n",
        encoding="utf-8",
    )

    rebuilt = _rebuild_code(
        api,
        headers,
        slug="graph-one",
        area_id=area_id,
    )
    assert rebuilt["state"] == "fresh"
    assert rebuilt["generation"] == 1
    assert rebuilt["freshness"]["tool_version"] == GRAPHIFY_VERSION
    assert len(rebuilt["freshness"]["source_fingerprint"]) == 64
    assert len(rebuilt["freshness"]["graph_sha256"]) == 64

    result = api.app.state.graph_context.query(
        owner_user_id=1,
        container_slug="graph-one",
        kind="code",
        area_id=area_id,
        question="Where is BillingService charge defined?",
        depth=99,
        timeout_ms=99_999,
        token_budget=99_999,
        result_limit=99_999,
    )
    assert result["scope"] == {
        "container_id": rebuilt["scope"]["container_id"],
        "container_slug": "graph-one",
        "kind": "code",
        "area_id": area_id,
        "area_rel_path": ".",
    }
    assert result["generation"] == 1
    assert result["freshness"]["state"] == "fresh"
    assert result["freshness"]["generation"] == 1
    assert result["limits"] == {
        "depth": 2,
        "timeout_ms": 5000,
        "token_budget": 1000,
        "result_limit": 10,
    }
    assert result["error"] is None
    assert result["items"]
    assert any("BillingService" in item["label"] for item in result["items"])
    assert result["citations"]
    assert {item["path"] for item in result["citations"]} == {"app.py"}
    assert any(
        item["kind"] == "EXTRACTED" and item["edge_count"] > 0
        for item in result["provenance"]
    )
    assert all("citations" in item and "provenance" in item for item in result["items"])
    assert len(json.dumps(result).encode("utf-8")) <= 4000
    graph_path = Path(
        api.app.state.db.execute(
            "SELECT graph_path FROM graph_states WHERE id = ?",
            (rebuilt["id"],),
        ).fetchone()["graph_path"]
    )
    persisted = json.loads(graph_path.read_text(encoding="utf-8"))
    assert all(
        not str(node.get("source_file") or "").startswith("ops/")
        for node in persisted["nodes"]
    )


def test_knowledge_rebuild_is_local_and_limited_to_allowlist(
    tmp_path: Path,
):
    from proxima_api.graph_context import SEMANTIC_BACKEND_LOCAL

    api, headers = _api(tmp_path)
    project, _ = _container(api, headers, with_code=False)
    root = Path(project["path"])
    (root / "ops" / "wiki").mkdir(exist_ok=True)
    (root / "ops" / "wiki" / "note.md").write_text(
        "# Note\n\nCurated knowledge.\n",
        encoding="utf-8",
    )
    (root / "ops" / "tasks").mkdir(exist_ok=True)
    (root / "ops" / "tasks" / "transcript.md").write_text(
        "# Transcript\n\nDo not include.\n",
        encoding="utf-8",
    )

    response = api.post(
        "/api/containers/graph-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["freshness"]["semantic_backend"] == SEMANTIC_BACKEND_LOCAL

    graph_path = Path(
        api.app.state.db.execute(
            "SELECT graph_path FROM graph_states WHERE kind = 'knowledge'"
        ).fetchone()["graph_path"]
    )
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    sources = {
        node.get("source_file")
        for node in graph["nodes"]
        if node.get("source_file")
    }
    assert "container.md" in sources
    assert "wiki/note.md" in sources
    assert "tasks/transcript.md" not in sources
    assert graph["graph"]["proxima"]["semantic_backend"] == SEMANTIC_BACKEND_LOCAL
    assert graph["graph"]["proxima"]["tool_version"] == GRAPHIFY_VERSION
    assert api.app.state.config["graph_semantic_egress_enabled"] is False


@pytest.mark.parametrize(
    "mode",
    ["killed", "malformed", "incomplete", "wrong-scope"],
)
def test_failed_generation_keeps_previous_canonical_byte_for_byte(
    tmp_path: Path,
    monkeypatch,
    mode: str,
):
    api, headers = _api(tmp_path)
    project, area_id = _container(api, headers)
    assert area_id is not None
    first = _rebuild_code(
        api,
        headers,
        slug="graph-one",
        area_id=area_id,
    )
    graph_path = Path(
        api.app.state.db.execute(
            "SELECT graph_path FROM graph_states WHERE id = ?",
            (first["id"],),
        ).fetchone()["graph_path"]
    )
    before = graph_path.read_bytes()
    service = api.app.state.graph_context

    def failing_builder(*, scope, stage, metadata, timeout_seconds):
        del timeout_seconds
        if mode == "killed":
            raise GraphBuildTimeout("simulated killed build")
        if mode == "malformed":
            stage.write_text("{not-json", encoding="utf-8")
            return {
                "source_files": ["app.py"],
                "source_fingerprint": "0" * 64,
            }
        source_files, fingerprint = service._current_sources(
            scope,
            stage.parent,
        )
        data = json.loads(before)
        next_metadata = dict(metadata)
        next_metadata["source_fingerprint"] = fingerprint
        if mode == "incomplete":
            next_metadata["complete"] = False
        else:
            next_metadata["container_id"] += 999
        data["graph"]["proxima"] = next_metadata
        stage.write_text(json.dumps(data), encoding="utf-8")
        return {
            "source_files": source_files,
            "source_fingerprint": fingerprint,
        }

    monkeypatch.setattr(service, "_run_builder", failing_builder)
    failed = api.post(
        "/api/containers/graph-one/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_id},
    )
    assert failed.status_code == 409, failed.text
    assert graph_path.read_bytes() == before
    row = api.app.state.db.execute(
        "SELECT state, generation, graph_sha256 FROM graph_states WHERE id = ?",
        (first["id"],),
    ).fetchone()
    assert row["state"] == "failed"
    assert row["generation"] == 1
    assert row["graph_sha256"] == hashlib_sha256(before)
    assert str(tmp_path) not in failed.text
    assert Path(project["path"]).exists()


def hashlib_sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def test_successful_replacement_retains_previous_last_good_generation(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project, area_id = _container(api, headers)
    assert area_id is not None
    first = _rebuild_code(
        api,
        headers,
        slug="graph-one",
        area_id=area_id,
    )
    graph_path = Path(
        api.app.state.db.execute(
            "SELECT graph_path FROM graph_states WHERE id = ?",
            (first["id"],),
        ).fetchone()["graph_path"]
    )
    first_bytes = graph_path.read_bytes()
    Path(project["path"], "app.py").write_text(
        "class BillingService:\n"
        "    def refund(self):\n"
        "        return 'refunded'\n",
        encoding="utf-8",
    )

    second = _rebuild_code(
        api,
        headers,
        slug="graph-one",
        area_id=area_id,
    )
    assert second["generation"] == 2
    assert graph_path.read_bytes() != first_bytes
    assert graph_path.with_name("graph.last-good.json").read_bytes() == first_bytes


def test_query_revalidates_symlinks_and_never_reads_outside_registered_area(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project, area_id = _container(api, headers)
    assert area_id is not None
    _rebuild_code(api, headers, slug="graph-one", area_id=area_id)

    source = Path(project["path"], "app.py")
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET_OUTSIDE_SCOPE = True\n", encoding="utf-8")
    source.unlink()
    source.symlink_to(outside)
    result = api.app.state.graph_context.query(
        owner_user_id=1,
        container_slug="graph-one",
        kind="code",
        area_id=area_id,
        question="BillingService",
    )

    assert result["items"] == []
    assert result["citations"] == []
    assert result["provenance"] == []
    assert result["error"]["code"] == "graph_validation_failed"
    assert str(outside) not in json.dumps(result)
    assert "SECRET_OUTSIDE_SCOPE" not in json.dumps(result)


def test_registered_area_symlink_escape_fails_closed_before_state_read(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project, _ = _container(api, headers, with_code=False)
    outside = tmp_path / "outside-repo"
    outside.mkdir()
    (outside / ".git").mkdir()
    escape = Path(project["path"], "escape")
    escape.symlink_to(outside, target_is_directory=True)
    container_id = int(
        api.app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'graph-one'"
        ).fetchone()["id"]
    )
    api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'escape', 'manual')",
        (container_id,),
    )

    response = api.get(
        "/api/containers/graph-one/graphs",
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert "escapes its Container" in response.text
    assert str(outside) not in response.text


def test_missing_graphify_is_explicit_and_live_state_remains_independent(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    _, area_id = _container(api, headers)
    assert area_id is not None
    monkeypatch.setattr(graph_context, "_installed_graphify_version", lambda: None)

    response = api.post(
        "/api/containers/graph-one/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_id},
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["state"] == "missing"
    assert state["generation"] == 0
    assert "not installed" in state["freshness"]["last_error"]

    fleet = api.get("/api/containers", headers=headers)
    assert fleet.status_code == 200, fleet.text
    assert fleet.json()["containers"][0]["live"] == {
        "running_tasks": 0,
        "queued_tasks": 0,
        "open_attention": 0,
    }
    assert api.get("/api/jobs", headers=headers).status_code == 200


def test_graph_state_events_use_master_stream_without_paths(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project, area_id = _container(api, headers)
    assert area_id is not None
    _rebuild_code(api, headers, slug="graph-one", area_id=area_id)
    master_session_id = int(
        api.app.state.db.execute(
            "SELECT id FROM sessions WHERE mode = 'master'"
        ).fetchone()["id"]
    )

    response = api.get(
        f"/api/sessions/{master_session_id}/events",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    events = [
        event
        for event in response.json()["events"]
        if event["type"].startswith("graph.state.")
    ]
    # Area registration may already enqueue; rebuild always contributes the
    # terminal queued → building → fresh sequence.
    assert "graph.state.queued" in [event["type"] for event in events]
    assert "graph.state.building" in [event["type"] for event in events]
    assert events[-1]["type"] == "graph.state.fresh"
    assert all(event["session_id"] == master_session_id for event in events)
    serialized = json.dumps(events)
    assert project["path"] not in serialized
    assert "graphify-out" not in serialized
    assert all(
        event["payload"]["scope"]["container_slug"] == "graph-one"
        for event in events
    )


def test_graph_semantic_egress_opt_in_is_refused_without_cloud_adapter(
    tmp_path: Path,
):
    api, headers = _api(
        tmp_path,
        graph_semantic_egress_enabled=True,
    )
    _container(api, headers, with_code=False)

    response = api.post(
        "/api/containers/graph-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert response.status_code == 409, response.text
    assert "not implemented" in response.text
    assert "graphify-out" not in response.text

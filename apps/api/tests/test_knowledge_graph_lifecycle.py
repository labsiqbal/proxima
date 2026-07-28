"""Group 11: Knowledge graph lifecycle - allowlist, privacy, rebuild, isolation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api.graph_context import (
    GRAPHIFY_VERSION,
    SEMANTIC_BACKEND_LOCAL,
    _knowledge_path_allowed,
    _select_knowledge_sources,
)
from proxima_api.knowledge_graph_lifecycle import (
    REASON_OPS_CONTENT_CHANGED,
    REASON_OPS_TASK_DONE,
    KnowledgeGraphLifecycle,
)
from proxima_api.main import create_app


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
            "knowledge_graph_tick_seconds": 1,
            "knowledge_graph_audit_seconds": 30,
            "knowledge_graph_dirty_debounce_seconds": 1,
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
    slug: str = "know-one",
) -> dict:
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": slug, "name": slug},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_ops(root: Path) -> None:
    ops = root / "ops"
    (ops / "container.md").write_text(
        "# Acme\n\nClient for billing work.\n\n## Decision\nUse local graphs.\n",
        encoding="utf-8",
    )
    (ops / "wiki").mkdir(exist_ok=True)
    (ops / "wiki" / "billing.md").write_text(
        "# Billing notes\n\nCharge monthly.\n",
        encoding="utf-8",
    )
    (ops / "wiki" / "index.md").write_text("# Index\n\ngenerated\n", encoding="utf-8")
    (ops / "wiki" / "log.md").write_text("# Log\n\nliving\n", encoding="utf-8")
    (ops / "reports").mkdir(exist_ok=True)
    (ops / "reports" / "kickoff.md").write_text(
        "# Kickoff report\n\nGreen.\n",
        encoding="utf-8",
    )
    (ops / "artifacts").mkdir(exist_ok=True)
    (ops / "artifacts" / "deliverable.meta.json").write_text(
        '{"name":"deck","status":"approved"}\n',
        encoding="utf-8",
    )
    (ops / "artifacts" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-real")
    (ops / "tasks").mkdir(exist_ok=True)
    (ops / "tasks" / "transcript.md").write_text(
        "# Transcript\n\nsecret chat\n",
        encoding="utf-8",
    )


def test_knowledge_path_allowlist_contract():
    assert _knowledge_path_allowed("container.md")
    assert _knowledge_path_allowed("design.md")
    assert _knowledge_path_allowed("wiki/billing.md")
    assert _knowledge_path_allowed("reports/kickoff.md")
    assert _knowledge_path_allowed("artifacts/deliverable.meta.json")
    assert _knowledge_path_allowed("artifacts/METADATA.md")
    assert not _knowledge_path_allowed("artifacts/notes.md")
    assert not _knowledge_path_allowed("artifacts/design/scene.json")
    assert not _knowledge_path_allowed("wiki/index.md")
    assert not _knowledge_path_allowed("wiki/log.md")
    assert not _knowledge_path_allowed("tasks/transcript.md")
    assert not _knowledge_path_allowed("scripts/run.sh")
    assert not _knowledge_path_allowed("secrets/token.md")
    assert not _knowledge_path_allowed("wiki/api_key.md")
    assert not _knowledge_path_allowed("graphify-out/graph.json")
    assert not _knowledge_path_allowed("artifacts/photo.png")
    assert not _knowledge_path_allowed("../etc/passwd")


def test_select_knowledge_sources_never_leaves_allowlist(tmp_path: Path):
    ops = tmp_path / "ops"
    ops.mkdir()
    _seed_ops(tmp_path)
    # Adversarial fixtures outside allowlist.
    (ops / "secrets").mkdir()
    (ops / "secrets" / "creds.md").write_text("password=1\n", encoding="utf-8")
    (ops / "graphify-out").mkdir()
    (ops / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    nested = ops / "wiki" / "nested-repo"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / "secret.md").write_text("# Nested\n", encoding="utf-8")
    # Symlink escape attempt.
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    try:
        (ops / "wiki" / "escape.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink not supported")

    rels, errors = _select_knowledge_sources(ops)
    assert not errors
    assert "container.md" in rels
    assert "wiki/billing.md" in rels
    assert "reports/kickoff.md" in rels
    assert "artifacts/deliverable.meta.json" in rels
    assert "wiki/index.md" not in rels
    assert "wiki/log.md" not in rels
    assert "tasks/transcript.md" not in rels
    assert "secrets/creds.md" not in rels
    assert "graphify-out/graph.json" not in rels
    assert "wiki/escape.md" not in rels
    assert "wiki/nested-repo/secret.md" not in rels
    assert "artifacts/photo.png" not in rels
    assert all(_knowledge_path_allowed(rel) for rel in rels)


def test_knowledge_rebuild_includes_allowlist_only_and_is_local(tmp_path: Path):
    api, headers = _api(tmp_path)
    project = _container(api, headers)
    root = Path(project["path"])
    _seed_ops(root)

    response = api.post(
        "/api/containers/know-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["state"] == "fresh"
    assert state["freshness"]["semantic_backend"] == SEMANTIC_BACKEND_LOCAL
    assert state["scope"]["kind"] == "knowledge"
    assert state["scope"]["area_id"] is None

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
    assert "wiki/billing.md" in sources
    assert "reports/kickoff.md" in sources
    # JSON metadata is allowlisted for fingerprinting; Graphify may emit zero
    # structural nodes for sparse JSON, which must not pull in excluded paths.
    assert "wiki/index.md" not in sources
    assert "tasks/transcript.md" not in sources
    assert "artifacts/photo.png" not in sources
    assert all(
        not str(src).startswith("tasks/")
        and "secret" not in str(src).lower()
        for src in sources
    )
    assert graph["graph"]["proxima"]["semantic_backend"] == SEMANTIC_BACKEND_LOCAL
    assert graph["graph"]["proxima"]["tool_version"] == GRAPHIFY_VERSION
    assert api.app.state.config["graph_semantic_egress_enabled"] is False
    # Fingerprint must still cover allowlisted JSON metadata when present.
    rels, errors = _select_knowledge_sources(root / "ops")
    assert not errors
    assert "artifacts/deliverable.meta.json" in rels


def test_knowledge_rebuild_preserves_last_good_on_failure(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    project = _container(api, headers)
    root = Path(project["path"])
    _seed_ops(root)
    first = api.post(
        "/api/containers/know-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert first.status_code == 200, first.text
    graph_path = Path(
        api.app.state.db.execute(
            "SELECT graph_path FROM graph_states WHERE kind = 'knowledge'"
        ).fetchone()["graph_path"]
    )
    before = graph_path.read_bytes()
    service = api.app.state.graph_context

    def boom(**_kwargs):
        raise RuntimeError("simulated ENOSPC")

    monkeypatch.setattr(service, "_run_builder", boom)
    failed = api.post(
        "/api/containers/know-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert failed.status_code == 409, failed.text
    assert graph_path.read_bytes() == before
    row = api.app.state.db.execute(
        "SELECT state, generation FROM graph_states WHERE kind = 'knowledge'"
    ).fetchone()
    assert row["state"] in {"failed", "stale", "queued"}
    assert int(row["generation"]) == int(first.json()["generation"])


def test_ops_task_done_marks_only_that_container_stale(tmp_path: Path):
    api, headers = _api(tmp_path)
    a = _container(api, headers, slug="acme")
    b = _container(api, headers, slug="other")
    _seed_ops(Path(a["path"]))
    _seed_ops(Path(b["path"]))
    for slug in ("acme", "other"):
        rebuilt = api.post(
            f"/api/containers/{slug}/graphs/rebuild",
            headers=headers,
            json={"kind": "knowledge"},
        )
        assert rebuilt.status_code == 200, rebuilt.text

    a_id = int(
        api.app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'acme'"
        ).fetchone()["id"]
    )
    b_id = int(
        api.app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'other'"
        ).fetchone()["id"]
    )
    lifecycle: KnowledgeGraphLifecycle = api.app.state.knowledge_graph_lifecycle
    lifecycle.on_ops_task_done(
        owner_user_id=1,
        container_id=a_id,
    )
    rows = {
        row["container_id"]: dict(row)
        for row in api.app.state.db.execute(
            "SELECT container_id, state, rebuild_reason FROM graph_states "
            "WHERE kind = 'knowledge'"
        ).fetchall()
    }
    assert rows[a_id]["state"] == "queued"
    assert rows[a_id]["rebuild_reason"] == REASON_OPS_TASK_DONE
    assert rows[b_id]["state"] == "fresh"


def test_content_debounce_hashes_only_after_cheap_marker_changes(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    project = _container(api, headers)
    root = Path(project["path"])
    _seed_ops(root)
    rebuilt = api.post(
        "/api/containers/know-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    service = api.app.state.graph_context
    lifecycle: KnowledgeGraphLifecycle = api.app.state.knowledge_graph_lifecycle
    original_signature = service.knowledge_source_signature
    signature_calls = 0

    def counted_signature(**kwargs):
        nonlocal signature_calls
        signature_calls += 1
        return original_signature(**kwargs)

    now = [100.0]
    monkeypatch.setattr(service, "knowledge_source_signature", counted_signature)
    monkeypatch.setattr(
        "proxima_api.knowledge_graph_lifecycle.time.monotonic",
        lambda: now[0],
    )

    lifecycle._debounce_ops_content()
    now[0] = 101.0
    lifecycle._debounce_ops_content()
    assert signature_calls == 0

    (root / "ops" / "container.md").write_text(
        "# Acme\n\nClient for billing and collections work.\n",
        encoding="utf-8",
    )
    now[0] = 102.0
    lifecycle._debounce_ops_content()
    assert signature_calls == 1

    now[0] = 104.0
    lifecycle._debounce_ops_content()
    assert signature_calls == 1
    row = api.app.state.db.execute(
        "SELECT state, rebuild_reason FROM graph_states "
        "WHERE kind = 'knowledge'"
    ).fetchone()
    assert row["state"] == "queued"
    assert row["rebuild_reason"] == REASON_OPS_CONTENT_CHANGED


def test_semantic_egress_opt_in_still_fails_closed(tmp_path: Path):
    api, headers = _api(tmp_path, graph_semantic_egress_enabled=True)
    project = _container(api, headers)
    _seed_ops(Path(project["path"]))
    response = api.post(
        "/api/containers/know-one/graphs/rebuild",
        headers=headers,
        json={"kind": "knowledge"},
    )
    assert response.status_code == 409, response.text
    assert "local-structural" in response.text or "not implemented" in response.text
    assert "graphify-out" not in response.text


def test_master_settings_exposes_local_only_graph_policy(tmp_path: Path):
    api, headers = _api(tmp_path)
    response = api.get("/api/settings/master", headers=headers)
    assert response.status_code == 200, response.text
    policy = response.json()["graph_policy"]
    assert policy["local_only"] is True
    assert policy["semantic_egress_enabled"] is False
    assert policy["semantic_backend_default"] == SEMANTIC_BACKEND_LOCAL
    assert "local" in policy["description"].lower()

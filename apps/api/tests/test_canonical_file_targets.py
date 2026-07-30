from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import artifact_registry, file_targets
from proxima_api.main import create_app


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc000000301010018dd8db1"
    "0000000049454e44ae426082"
)
PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] >>endobj\n"
    b"trailer<< /Root 1 0 R /Size 4 >>\n%%EOF\n"
)


def _api(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "identity", "name": "File Identity"},
    )
    assert response.status_code == 201, response.text
    return api, headers, Path(response.json()["path"])


def _by_name(api: TestClient, headers: dict[str, str]) -> dict[str, dict]:
    response = api.get("/api/projects/identity/tree", headers=headers)
    assert response.status_code == 200, response.text
    return {entry["name"]: entry for entry in response.json()["entries"]}


def _target_params(target: dict) -> dict[str, str]:
    return {"target": json.dumps(target, separators=(",", ":"))}


def test_physical_ops_direct_files_keep_server_owned_identity_across_surfaces(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    ops = root / "ops"
    (root / "brief.md").write_text("# Container shadow", encoding="utf-8")
    (root / "visual.png").write_bytes(b"container image shadow")
    (root / "handout.pdf").write_bytes(b"container PDF shadow")
    (ops / "brief.md").write_text("# Ops direct brief", encoding="utf-8")
    (ops / "ops-only.md").write_text("# Ops only", encoding="utf-8")
    (ops / "visual.png").write_bytes(PNG_1X1)
    (ops / "handout.pdf").write_bytes(PDF)
    (root / "site").mkdir()
    (root / "site" / "theme.css").write_text(
        "body { color: wrong-container; }",
        encoding="utf-8",
    )
    (ops / "site").mkdir()
    (ops / "site" / "index.html").write_text(
        '<link rel="stylesheet" href="theme.css"><main>Ops page</main>',
        encoding="utf-8",
    )
    (ops / "site" / "theme.css").write_text(
        "body { color: canonical-ops; }",
        encoding="utf-8",
    )

    entries = _by_name(api, headers)
    ops_only = entries["ops-only.md"]["target"]
    container_brief = entries["brief.md"]["target"]
    ops_area_id = api.app.state.db.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = (SELECT id FROM projects WHERE slug = 'identity') "
        "AND kind = 'ops' AND source != 'excluded'"
    ).fetchone()["id"]

    assert ops_only == {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "ops-only.md",
    }
    assert container_brief == {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "brief.md",
    }
    read = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(ops_only),
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# Ops only"
    assert read.json()["target"] == ops_only

    # The merge policy keeps the Container entry for a generic same-name
    # collision, while an Ops artifact target remains authoritative.
    shadow_read = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(container_brief),
    )
    assert shadow_read.json()["content"] == "# Container shadow"

    artifact_items = api.get(
        "/api/projects/identity/artifacts?since_minutes=525600",
        headers=headers,
    ).json()["artifacts"]
    brief_artifact = next(item for item in artifact_items if item["path"] == "brief.md")
    assert brief_artifact["target"] == {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "brief.md",
    }
    ops_brief = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(brief_artifact["target"]),
    )
    assert ops_brief.json()["content"] == "# Ops direct brief"

    for name, expected in (("visual.png", PNG_1X1), ("handout.pdf", PDF)):
        target = {
            "project": "identity",
            "area": {"kind": "ops", "id": ops_area_id},
            "path": name,
        }
        raw = api.get(
            "/api/projects/identity/raw",
            headers=headers,
            params=_target_params(target),
        )
        assert raw.status_code == 200, raw.text
        assert raw.content == expected
        preview = api.get(
            f"/api/target-preview/identity/ops/{ops_area_id}/{name}",
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.content == expected

    image_target = {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "visual.png",
    }
    design = api.post(
        "/api/projects/identity/designs/from-image",
        headers=headers,
        json={
            "path": "visual.png",
            "target": image_target,
            "title": "Canonical visual",
        },
    )
    assert design.status_code == 200, design.text
    scene = json.loads(
        (
            ops
            / design.json()["path"]
            / "scene.json"
        ).read_text(encoding="utf-8")
    )
    image_layer = scene["artboards"][0]["layers"][0]
    assert image_layer["src"] == "visual.png"
    assert image_layer["target"] == image_target

    malformed = api.post(
        "/api/projects/identity/designs/from-image",
        headers=headers,
        json={"path": "visual.png", "target": 1},
    )
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "invalid file target"

    page = api.get(
        f"/api/target-preview/identity/ops/{ops_area_id}/site/index.html",
        headers=headers,
    )
    assert page.status_code == 200, page.text
    assert "Ops page" in page.text
    nested_asset = api.get(
        f"/api/target-preview/identity/ops/{ops_area_id}/site/theme.css",
        headers=headers,
    )
    assert nested_asset.status_code == 200, nested_asset.text
    assert nested_asset.text == "body { color: canonical-ops; }"

    legacy_collision = root / "area" / "ops" / str(ops_area_id) / "site"
    legacy_collision.mkdir(parents=True)
    (legacy_collision / "theme.css").write_text(
        "body { color: legacy-container; }",
        encoding="utf-8",
    )
    legacy_preview = api.get(
        f"/api/preview/identity/area/ops/{ops_area_id}/site/theme.css",
        headers=headers,
    )
    assert legacy_preview.status_code == 200, legacy_preview.text
    assert legacy_preview.text == "body { color: legacy-container; }"
    escaped_namespace = api.get(
        f"/api/target-preview/identity/ops/{ops_area_id}/site/"
        "../../../../brief.md",
        headers=headers,
    )
    assert escaped_namespace.status_code == 404

    # Documented path-only callers remain compatible, including an explicit
    # physical ops/ path. Their response is upgraded to the canonical target.
    explicit = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params={"path": "ops/ops-only.md"},
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["content"] == "# Ops only"
    assert explicit.json()["target"] == ops_only


def test_archive_targets_resolve_direct_ops_files_without_registering_workspace_scan(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, root = _api(tmp_path)
    ops = root / "ops"
    (root / "brief.md").write_text("# Container shadow", encoding="utf-8")
    (root / "visual.png").write_bytes(b"container image shadow")
    (root / "handout.pdf").write_bytes(b"container PDF shadow")
    (ops / "brief.md").write_text("# Ops archive brief", encoding="utf-8")
    (ops / "visual.png").write_bytes(PNG_1X1)
    (ops / "handout.pdf").write_bytes(PDF)

    # Merely existing in the workspace must not create durable Archive rows.
    empty = api.get("/api/archive?project=identity", headers=headers).json()
    assert empty["total"] == 0

    project = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'identity'"
    ).fetchone()
    artifact_registry.record_artifacts(
        api.app.state.db,
        project["id"],
        ops,
        [
            {"type": "doc", "title": "brief.md", "path": "brief.md"},
            {"type": "image", "title": "visual.png", "path": "visual.png"},
            {"type": "doc", "title": "handout.pdf", "path": "handout.pdf"},
        ],
    )

    context_calls = 0
    original_context = file_targets.target_context

    def counted_context(*args, **kwargs):
        nonlocal context_calls
        context_calls += 1
        return original_context(*args, **kwargs)

    monkeypatch.setattr(file_targets, "target_context", counted_context)
    archive = api.get("/api/archive?project=identity", headers=headers).json()
    assert archive["total"] == 3
    assert context_calls == 1
    assert all(item["target"]["area"]["kind"] == "ops" for item in archive["items"])
    assert all(item["target"]["project"] == "identity" for item in archive["items"])
    assert all(item["file_missing"] is False for item in archive["items"])

    brief = next(item for item in archive["items"] if item["path"] == "brief.md")
    opened = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(brief["target"]),
    )
    assert opened.json()["content"] == "# Ops archive brief"

    # Presence must follow the record's Ops identity, not a same-name Container
    # shadow that still exists.
    (ops / "visual.png").unlink()
    calls_before_refresh = context_calls
    refreshed = api.get("/api/archive?project=identity", headers=headers).json()
    assert context_calls == calls_before_refresh + 1
    visual = next(item for item in refreshed["items"] if item["path"] == "visual.png")
    assert visual["file_missing"] is True


def test_resolver_rejects_cross_area_aliases_and_tree_switches_to_code_identity(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, root = _api(tmp_path)
    repo = root / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Code Area", encoding="utf-8")
    project = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'identity'"
    ).fetchone()
    cursor = api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo', 'manual')",
        (project["id"],),
    )
    code_area_id = cursor.lastrowid

    binding_calls = 0
    original_bindings = file_targets._area_bindings

    def counted_bindings(*args, **kwargs):
        nonlocal binding_calls
        binding_calls += 1
        return original_bindings(*args, **kwargs)

    monkeypatch.setattr(file_targets, "_area_bindings", counted_bindings)
    root_entries = _by_name(api, headers)
    assert binding_calls == 1
    repo_target = root_entries["repo"]["target"]
    assert repo_target == {
        "project": "identity",
        "area": {"kind": "code", "id": code_area_id},
        "path": "",
    }
    repo_tree = api.get(
        "/api/projects/identity/tree",
        headers=headers,
        params=_target_params(repo_target),
    )
    assert repo_tree.status_code == 200, repo_tree.text
    assert binding_calls == 2
    readme_target = repo_tree.json()["entries"][0]["target"]
    assert readme_target == {
        "project": "identity",
        "area": {"kind": "code", "id": code_area_id},
        "path": "README.md",
    }

    forged_code_alias = {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "repo/README.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(forged_code_alias),
    ).status_code == 400

    ops_area_id = api.app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (project["id"],),
    ).fetchone()["id"]
    forged_ops_alias = {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "ops/container.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(forged_ops_alias),
    ).status_code == 400
    canonical_ops = {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "container.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(canonical_ops),
    ).status_code == 200

    legacy_code = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params={"path": "repo/README.md"},
    )
    assert legacy_code.status_code == 200, legacy_code.text
    assert legacy_code.json()["target"] == readme_target


def test_ops_at_dot_scans_and_task_outputs_derive_nested_code_ownership(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    project = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'identity'"
    ).fetchone()
    ops_area = api.app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (project["id"],),
    ).fetchone()
    api.app.state.db.execute(
        "UPDATE project_areas SET rel_path = '.' WHERE id = ?",
        (ops_area["id"],),
    )
    repo = root / "repo"
    repo.mkdir()
    cursor = api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo', 'manual')",
        (project["id"],),
    )
    code_area_id = int(cursor.lastrowid)
    (repo / "output.md").write_text("# Nested code output", encoding="utf-8")

    scanned = api.get(
        "/api/projects/identity/artifacts?since_minutes=525600",
        headers=headers,
    ).json()["artifacts"]
    output = next(item for item in scanned if item["path"] == "repo/output.md")
    expected_target = {
        "project": "identity",
        "area": {"kind": "code", "id": code_area_id},
        "path": "output.md",
    }
    assert output["target"] == expected_target

    produced = api.app.state.worker._produced_artifacts(
        {
            "project_id": project["id"],
            "started_at": "1970-01-01T00:00:00+00:00",
        },
        None,
    )
    produced_output = next(
        item for item in produced if item["path"] == "repo/output.md"
    )
    assert produced_output["target"] == expected_target

    artifact_registry.record_artifacts(
        api.app.state.db,
        project["id"],
        root,
        [produced_output],
    )
    archive = api.get("/api/archive?project=identity", headers=headers).json()
    record = next(
        item for item in archive["items"] if item["path"] == "repo/output.md"
    )
    assert record["target"] == expected_target
    opened = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(record["target"]),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["content"] == "# Nested code output"


def test_session_artifact_reads_and_deletion_keep_ops_target(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    (root / "brief.md").write_text("# Container shadow", encoding="utf-8")
    (root / "ops" / "brief.md").write_text("# Ops artifact", encoding="utf-8")
    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Artifact session", "project_slug": "identity"},
    ).json()
    artifact = {"type": "doc", "title": "brief.md", "path": "brief.md"}
    api.app.state.db.execute(
        "UPDATE sessions SET produced_artifacts = ? WHERE id = ?",
        (json.dumps([artifact]), session["id"]),
    )
    message = api.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, output_links) "
        "VALUES (?, 'assistant', 'Done', ?)",
        (session["id"], json.dumps([artifact])),
    )

    session_items = api.get(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
    ).json()["artifacts"]
    target = session_items[0]["target"]
    assert target["area"]["kind"] == "ops"
    messages = api.get(
        f"/api/sessions/{session['id']}/messages",
        headers=headers,
    ).json()["messages"]
    assert messages[0]["output_links"][0]["target"] == target

    forged = {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "brief.md",
    }
    rejected = api.delete(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
        params=_target_params(forged),
    )
    assert rejected.status_code == 400
    assert (root / "brief.md").is_file()
    assert (root / "ops" / "brief.md").is_file()

    deleted = api.delete(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
        params=_target_params(target),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["target"] == target
    assert (root / "brief.md").read_text(encoding="utf-8") == "# Container shadow"
    assert not (root / "ops" / "brief.md").exists()
    stored = api.app.state.db.execute(
        "SELECT output_links FROM messages WHERE id = ?",
        (message.lastrowid,),
    ).fetchone()
    assert json.loads(stored["output_links"]) == []


def test_locator_validation_rejects_cross_container_area_and_symlink_escape(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    other = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "other", "name": "Other"},
    ).json()
    other_area_id = other["ops_area"]["id"]
    foreign = {
        "project": "identity",
        "area": {"kind": "ops", "id": other_area_id},
        "path": "container.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(foreign),
    ).status_code == 400

    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (root / "ops" / "escape.md").symlink_to(outside)
    ops_target = _by_name(api, headers)["escape.md"]["target"]
    escaped = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(ops_target),
    )
    assert escaped.status_code == 400


def test_legacy_ops_at_dot_keeps_area_identity_and_does_not_rewrite_ops_prefix(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    row = api.app.state.db.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = (SELECT id FROM projects WHERE slug = 'identity') "
        "AND kind = 'ops'"
    ).fetchone()
    api.app.state.db.execute(
        "UPDATE project_areas SET rel_path = '.' WHERE id = ?",
        (row["id"],),
    )
    (root / "legacy.md").write_text("# Legacy root Ops", encoding="utf-8")

    legacy = _by_name(api, headers)["legacy.md"]["target"]
    assert legacy == {
        "project": "identity",
        "area": {"kind": "ops", "id": row["id"]},
        "path": "legacy.md",
    }
    read = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(legacy),
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# Legacy root Ops"

    # On a legacy Area rooted at '.', an explicit ops/ prefix still addresses
    # the real Container child named ops. It must not be stripped to legacy.md.
    protected = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params={"path": "ops/legacy.md"},
    )
    assert protected.status_code == 400

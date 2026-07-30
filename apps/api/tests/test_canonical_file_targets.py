from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import artifact_registry
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
            f"/api/preview/identity/{name}",
            headers=headers,
            params=_target_params(target),
        )
        assert preview.status_code == 200, preview.text
        assert preview.content == expected

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

    archive = api.get("/api/archive?project=identity", headers=headers).json()
    assert archive["total"] == 3
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
    refreshed = api.get("/api/archive?project=identity", headers=headers).json()
    visual = next(item for item in refreshed["items"] if item["path"] == "visual.png")
    assert visual["file_missing"] is True


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

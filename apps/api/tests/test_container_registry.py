from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import container_registry, scripts_library
from proxima_api.container_registry import (
    ContainerBoundaryError,
    migrate_container_ops,
    migrate_legacy_ops_containers,
    ops_root,
    validated_area_roots,
)
from proxima_api.db import connect, init_db
from proxima_api.main import create_app


def _legacy_container(conn, root: Path, slug: str = "legacy") -> int:
    root.mkdir(parents=True, exist_ok=True)
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        (f"owner-{slug}", f"owner-{slug}"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        (slug, slug.title(), str(root), user_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', '.', 'auto')",
        (container_id,),
    )
    return int(container_id)


def _database(tmp_path: Path):
    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    return conn


def test_clean_legacy_ops_migration_preserves_bytes_and_is_idempotent(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "legacy"
    container_id = _legacy_container(conn, root)
    original = {
        "wiki/note.md": b"# note\n\x00exact\n",
        "artifacts/media/result.bin": bytes(range(256)),
        "reports/report.txt": b"report\r\n",
        "exports/result.csv": b"a,b\n1,2\n",
        "scripts/build.sh": b"#!/bin/sh\nprintf done\n",
        "design.md": b"# Brand\n",
    }
    for rel, data in original.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    assert migrate_container_ops(conn, container_id) is True
    assert ops_root(conn, container_id) == (root / "ops").resolve()
    for rel, data in original.items():
        assert not (root / rel).exists()
        assert (root / "ops" / rel).read_bytes() == data

    marker = conn.execute(
        "SELECT status, manifest_json, manifest_hash FROM container_ops_migrations "
        "WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    assert marker["status"] == "complete"
    manifest = json.loads(marker["manifest_json"])
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    assert marker["manifest_hash"] == hashlib.sha256(canonical).hexdigest()
    assert all(entry["sha256"] for entry in manifest["entries"])
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "ops").rglob("*")
        if path.is_file()
    }

    assert migrate_legacy_ops_containers(conn) == {"complete": 1, "attention": 0}
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "ops").rglob("*")
        if path.is_file()
    }
    assert after == before


def test_collision_leaves_legacy_row_and_every_file_unchanged(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "collision"
    container_id = _legacy_container(conn, root, "collision")
    (root / "wiki").mkdir()
    (root / "wiki" / "keep.md").write_bytes(b"legacy bytes")
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "keep.md").write_bytes(b"new bytes")
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    assert migrate_container_ops(conn, container_id) is False

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert conn.execute(
        "SELECT rel_path FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["rel_path"] == "."
    attention = conn.execute(
        "SELECT status, target_json FROM attention_items WHERE source_key = ?",
        (f"container-ops-migration:{container_id}",),
    ).fetchone()
    assert attention["status"] == "open"
    assert "physical Ops root is not empty" in json.loads(attention["target_json"])["reason"]
    assert migrate_container_ops(conn, container_id) is False
    rerun = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert rerun == before


def test_partial_move_recovers_from_durable_manifest(tmp_path: Path, monkeypatch):
    conn = _database(tmp_path)
    root = tmp_path / "partial"
    container_id = _legacy_container(conn, root, "partial")
    for dirname, data in (("wiki", b"wiki"), ("artifacts", b"artifact")):
        (root / dirname).mkdir()
        (root / dirname / "data.bin").write_bytes(data)

    real_replace = container_registry.os.replace
    moves = 0

    class SimulatedProcessDeath(BaseException):
        pass

    def die_after_one_move(source, destination):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise SimulatedProcessDeath
        return real_replace(source, destination)

    monkeypatch.setattr(container_registry.os, "replace", die_after_one_move)
    with pytest.raises(SimulatedProcessDeath):
        migrate_container_ops(conn, container_id)

    marker = conn.execute(
        "SELECT status, manifest_json FROM container_ops_migrations WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    assert marker["status"] == "moving"
    assert marker["manifest_json"]
    monkeypatch.setattr(container_registry.os, "replace", real_replace)

    assert migrate_container_ops(conn, container_id) is True
    assert (root / "ops" / "wiki" / "data.bin").read_bytes() == b"wiki"
    assert (root / "ops" / "artifacts" / "data.bin").read_bytes() == b"artifact"
    assert conn.execute(
        "SELECT rel_path FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()["rel_path"] == "ops"


def test_area_validation_rejects_escape_duplicate_overlap_and_ops_symlink(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "boundaries"
    container_id = _legacy_container(conn, root, "boundaries")
    (root / "ops").mkdir()
    conn.execute(
        "UPDATE project_areas SET rel_path = 'ops' WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    )
    (root / "repo").mkdir()
    first = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo', 'manual')",
        (container_id,),
    ).lastrowid
    assert validated_area_roots(conn, container_id)[int(first)] == (root / "repo").resolve()

    (root / "repo" / "nested").mkdir()
    nested = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo/nested', 'manual')",
        (container_id,),
    ).lastrowid
    with pytest.raises(ContainerBoundaryError, match="overlap"):
        validated_area_roots(conn, container_id)
    conn.execute("DELETE FROM project_areas WHERE id = ?", (nested,))

    duplicate = root / "repo-alias"
    duplicate.symlink_to(root / "repo", target_is_directory=True)
    duplicate_id = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo-alias', 'manual')",
        (container_id,),
    ).lastrowid
    with pytest.raises(ContainerBoundaryError, match="same root"):
        validated_area_roots(conn, container_id)
    conn.execute("DELETE FROM project_areas WHERE id = ?", (duplicate_id,))

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    escape_id = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'escape', 'manual')",
        (container_id,),
    ).lastrowid
    with pytest.raises(ContainerBoundaryError, match="escapes"):
        validated_area_roots(conn, container_id)
    conn.execute("DELETE FROM project_areas WHERE id = ?", (escape_id,))

    traversal_id = conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', '../outside', 'manual')",
        (container_id,),
    ).lastrowid
    with pytest.raises(ContainerBoundaryError, match="invalid Area path"):
        validated_area_roots(conn, container_id)
    conn.execute("DELETE FROM project_areas WHERE id = ?", (traversal_id,))

    (root / "ops" / "outside").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContainerBoundaryError, match="contains a symlink"):
        validated_area_roots(conn, container_id, deep_ops_scan=True)


def test_ops_descendant_symlink_scan_is_opt_in(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "scan"
    container_id = _legacy_container(conn, root, "scan")
    (root / "ops").mkdir()
    conn.execute(
        "UPDATE project_areas SET rel_path = 'ops' WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    )
    outside = tmp_path / "scan-outside"
    outside.mkdir()
    (root / "ops" / "linked").symlink_to(outside, target_is_directory=True)

    assert validated_area_roots(conn, container_id)  # hot path skips the walk
    with pytest.raises(ContainerBoundaryError, match="contains a symlink"):
        validated_area_roots(conn, container_id, deep_ops_scan=True)


def test_migrate_isolates_unhealthy_already_migrated_container(tmp_path: Path):
    conn = _database(tmp_path)
    healthy_root = tmp_path / "healthy"
    healthy_id = _legacy_container(conn, healthy_root, "healthy")
    (healthy_root / "wiki").mkdir()
    (healthy_root / "wiki" / "note.md").write_bytes(b"# note\n")
    assert migrate_container_ops(conn, healthy_id) is True

    missing_root = tmp_path / "gone"
    missing_id = _legacy_container(conn, missing_root, "gone")
    conn.execute(
        "UPDATE project_areas SET rel_path = 'ops' WHERE project_id = ? AND kind = 'ops'",
        (missing_id,),
    )
    shutil.rmtree(missing_root)

    summary = migrate_legacy_ops_containers(conn)
    assert summary["complete"] >= 1
    assert summary["attention"] >= 1
    attention = conn.execute(
        "SELECT status FROM attention_items WHERE source_key = ?",
        (f"container-ops-migration:{missing_id}",),
    ).fetchone()
    assert attention is not None and attention["status"] == "open"
    assert conn.execute(
        "SELECT rel_path FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (healthy_id,),
    ).fetchone()["rel_path"] == "ops"


def _api(tmp_path: Path, database_path: Path | None = None) -> tuple[TestClient, dict[str, str]]:
    app = create_app(
        {
            "database_path": str(database_path or tmp_path / "api.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "start_worker": False,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def test_fresh_container_ops_features_keep_virtual_paths(tmp_path: Path):
    api, headers = _api(tmp_path)
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "fresh", "name": "Fresh"},
    )
    assert response.status_code == 201, response.text
    root = Path(response.json()["path"])
    container_doc = root / "ops" / "container.md"
    assert container_doc.is_file()
    registry = api.app.state.db.execute(
        "SELECT identity_label, summary, source_hash FROM container_registry "
        "WHERE container_id = (SELECT id FROM projects WHERE slug = 'fresh')"
    ).fetchone()
    assert registry["identity_label"] == "General"
    assert registry["summary"] == "Work and durable context for Fresh."
    assert len(registry["source_hash"]) == 64

    container_doc.write_text(
        "---\nidentity: Client success\nsummary: Launch workspace.\n---\n",
        encoding="utf-8",
    )
    container_registry.refresh_registry_projection(
        api.app.state.db,
        api.app.state.db.execute(
            "SELECT id, path FROM projects WHERE slug = 'fresh'"
        ).fetchone(),
    )
    refreshed = api.app.state.db.execute(
        "SELECT identity_label, summary, source_hash FROM container_registry "
        "WHERE container_id = (SELECT id FROM projects WHERE slug = 'fresh')"
    ).fetchone()
    assert refreshed["identity_label"] == "Client success"
    assert refreshed["summary"] == "Launch workspace."
    assert refreshed["source_hash"] != registry["source_hash"]

    assert api.put(
        "/api/projects/fresh/file?path=wiki/note.md",
        headers=headers,
        json={"content": "# Note"},
    ).status_code == 200
    assert (root / "ops" / "wiki" / "note.md").read_text() == "# Note"
    notes = api.get("/api/projects/fresh/wiki/all", headers=headers).json()["notes"]
    assert notes == [{"path": "note.md", "content": "# Note"}]

    image = root / "ops" / "artifacts" / "media" / "images" / "source.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    )
    artifacts = api.get(
        "/api/projects/fresh/artifacts?since_minutes=525600",
        headers=headers,
    ).json()["artifacts"]
    assert any(item["path"] == "artifacts/media/images/source.png" for item in artifacts)
    assert api.get(
        "/api/projects/fresh/raw?path=artifacts/media/images/source.png",
        headers=headers,
    ).status_code == 200

    design = api.post(
        "/api/projects/fresh/designs/from-image",
        headers=headers,
        json={"path": "artifacts/media/images/source.png"},
    )
    assert design.status_code == 200, design.text
    assert (root / "ops" / design.json()["path"] / "scene.json").is_file()

    script = root / "ops" / "scripts" / "report.sh"
    script.parent.mkdir()
    script.write_text("# Description: build the report\n", encoding="utf-8")
    assert scripts_library.scan_catalog(root / "ops") == [
        {"rel_path": "report.sh", "description": "build the report"}
    ]


def test_project_list_survives_ops_descendant_symlink(tmp_path: Path):
    api, headers = _api(tmp_path)
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "fresh", "name": "Fresh"},
    )
    assert response.status_code == 201, response.text
    root = Path(response.json()["path"])
    outside = tmp_path / "list-outside"
    outside.mkdir()
    (root / "ops" / "linked").symlink_to(outside, target_is_directory=True)

    listing = api.get("/api/projects", headers=headers)
    assert listing.status_code == 200, listing.text
    assert any(p["slug"] == "fresh" for p in listing.json()["projects"])


def test_collision_container_keeps_legacy_ops_features_available(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = connect(db_path)
    init_db(conn, [])
    root = tmp_path / "legacy-api"
    container_id = _legacy_container(conn, root, "legacy-api")
    (root / "wiki").mkdir()
    (root / "wiki" / "note.md").write_text("# Legacy", encoding="utf-8")
    (root / "artifacts").mkdir()
    (root / "artifacts" / "keep.txt").write_bytes(b"legacy artifact")
    design_scene = root / "artifacts" / "design" / "legacy" / "scene.json"
    design_scene.parent.mkdir(parents=True)
    design_scene.write_text(
        json.dumps({"id": "legacy", "title": "Legacy design"}),
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "keep.sh").write_text(
        "# Description: legacy script\n", encoding="utf-8"
    )
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "collision.md").write_text(
        "# Collision", encoding="utf-8"
    )
    conn.close()

    api, headers = _api(tmp_path, db_path)
    notes = api.get(
        "/api/projects/legacy-api/wiki/all", headers=headers
    ).json()["notes"]
    assert notes == [{"path": "note.md", "content": "# Legacy"}]
    artifacts = api.get(
        "/api/projects/legacy-api/artifacts?since_minutes=525600",
        headers=headers,
    ).json()["artifacts"]
    assert any(item["path"] == "artifacts/keep.txt" for item in artifacts)
    assert any(
        item["type"] == "design"
        and item["path"] == "artifacts/design/legacy"
        for item in artifacts
    )
    assert api.get(
        "/api/projects/legacy-api/raw?path=artifacts/keep.txt",
        headers=headers,
    ).content == b"legacy artifact"
    archive = api.get(
        "/api/archive?project=legacy-api",
        headers=headers,
    ).json()
    assert archive["total"] >= 1
    assert all(item["file_missing"] is False for item in archive["items"])
    assert scripts_library.scan_catalog(root) == [
        {"rel_path": "keep.sh", "description": "legacy script"}
    ]

    row = api.app.state.db.execute(
        "SELECT rel_path FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    ).fetchone()
    assert row["rel_path"] == "."
    attention = api.app.state.db.execute(
        "SELECT status FROM attention_items WHERE source_key = ?",
        (f"container-ops-migration:{container_id}",),
    ).fetchone()
    assert attention["status"] == "open"
    visible_attention = api.get("/api/attention", headers=headers).json()["items"]
    assert any(item["kind"] == "container_ops_migration" for item in visible_attention)

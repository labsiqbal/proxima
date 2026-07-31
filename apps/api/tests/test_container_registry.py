from __future__ import annotations

import json
import hashlib
import errno
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import artifact_registry, container_registry, scripts_library
from proxima_api.container_registry import (
    ContainerBoundaryError,
    migrate_container_ops,
    migrate_legacy_ops_containers,
    ops_root,
    validated_area_roots,
)
from proxima_api.db import connect, init_db
from proxima_api.directory_handles import directory_identity_for_path
from proxima_api.main import create_app


def _legacy_container(conn, root: Path, slug: str = "legacy") -> int:
    root.mkdir(parents=True, exist_ok=True)
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        (f"owner-{slug}", f"owner-{slug}"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects("
        "slug, name, path, path_identity, owner_user_id"
        ") VALUES (?, ?, ?, ?, ?)",
        (
            slug,
            slug.title(),
            str(root),
            directory_identity_for_path(root),
            user_id,
        ),
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


def _v1_manifest(root: Path, *names: str) -> dict:
    entries = []
    for name in names:
        path = root / name
        digest, files = container_registry._hash_entry(path)
        entries.append(
            {
                "name": name,
                "kind": "directory" if path.is_dir() else "file",
                "sha256": digest,
                "files": files,
            }
        )
    return {
        "version": 1,
        "container_root": str(root),
        "ops_root": str(root / "ops"),
        "entries": entries,
    }


def _store_moving_manifest(conn, container_id: int, manifest: dict) -> None:
    conn.execute(
        """
        INSERT INTO container_ops_migrations(
          container_id, migration_version, status, manifest_json, manifest_hash,
          started_at, updated_at
        ) VALUES (?, 1, 'moving', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            container_id,
            json.dumps(manifest, sort_keys=True),
            container_registry._manifest_digest(manifest),
        ),
    )


def _prepare_completed_filesystem_move(
    conn,
    container_id: int,
    root: Path,
) -> dict:
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    strategy, content, _ = container_registry._manifest_container_doc(manifest)
    assert strategy == "generate"
    assert content is not None
    (root / "ops").mkdir()
    document = root / "ops" / "container.md"
    document.write_text(content, encoding="utf-8")
    recovery = manifest["container_doc"]["recovery_temp"]
    recovery["phase"] = "complete"
    recovery["identity"] = container_registry._stat_identity(document.lstat())
    container_registry._upsert_marker(conn, container_id, "moving", manifest)
    (root / "wiki").rename(root / "ops" / "wiki")
    return manifest


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
    planned_doc = manifest["container_doc"]
    assert planned_doc["path"] == "container.md"
    assert planned_doc["strategy"] == "generate"
    assert planned_doc["sha256"] == hashlib.sha256(
        planned_doc["content"].encode("utf-8")
    ).hexdigest()
    assert (root / "ops" / "container.md").read_text(
        encoding="utf-8"
    ) == planned_doc["content"]
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


@pytest.mark.parametrize("moved", [(), ("wiki",)])
def test_v1_moving_manifest_upgrades_unambiguous_partial_layouts(
    tmp_path: Path,
    moved: tuple[str, ...],
):
    conn = _database(tmp_path)
    root = tmp_path / f"v1-partial-{len(moved)}"
    container_id = _legacy_container(conn, root, f"v1-partial-{len(moved)}")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")
    manifest = _v1_manifest(root, "wiki", "artifacts")
    if moved:
        (root / "ops").mkdir()
        for name in moved:
            (root / name).rename(root / "ops" / name)
        (root / "ops" / "container.md").write_text(
            container_registry._container_doc_text(
                f"V1-Partial-{len(moved)}"
            ),
            encoding="utf-8",
        )
    _store_moving_manifest(conn, container_id, manifest)

    assert migrate_container_ops(conn, container_id) is True
    assert (root / "ops" / "wiki" / "data.txt").read_text(
        encoding="utf-8"
    ) == "wiki"
    assert (root / "ops" / "artifacts" / "data.txt").read_text(
        encoding="utf-8"
    ) == "artifact"
    marker = conn.execute(
        "SELECT migration_version, manifest_json FROM container_ops_migrations "
        "WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    assert marker["migration_version"] == container_registry.OPS_MIGRATION_VERSION
    upgraded = json.loads(marker["manifest_json"])
    assert upgraded["version"] == container_registry.OPS_MIGRATION_VERSION
    assert upgraded["container_doc"]["strategy"] == "generate"


def test_v1_completed_moves_upgrade_only_with_exact_generated_document(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "v1-completed"
    container_id = _legacy_container(conn, root, "v1-completed")
    (root / "wiki").mkdir()
    (root / "wiki" / "data.txt").write_text("wiki", encoding="utf-8")
    manifest = _v1_manifest(root, "wiki")
    (root / "ops").mkdir()
    (root / "wiki").rename(root / "ops" / "wiki")
    generated = container_registry._container_doc_text("V1-Completed")
    (root / "ops" / "container.md").write_text(generated, encoding="utf-8")
    _store_moving_manifest(conn, container_id, manifest)

    assert migrate_container_ops(conn, container_id) is True
    assert (root / "ops" / "container.md").read_text(
        encoding="utf-8"
    ) == generated


def test_v1_planned_document_metadata_upgrades_partial_move(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "v1-planned-document"
    container_id = _legacy_container(conn, root, "v1-planned-document")
    for name in ("wiki", "artifacts"):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(name, encoding="utf-8")
    manifest = _v1_manifest(root, "wiki", "artifacts")
    generated = container_registry._container_doc_text("V1-Planned-Document")
    manifest["container_doc"] = {
        "path": "container.md",
        "content": generated,
        "sha256": hashlib.sha256(generated.encode("utf-8")).hexdigest(),
    }
    (root / "ops").mkdir()
    (root / "wiki").rename(root / "ops" / "wiki")
    (root / "ops" / "container.md").write_text(generated, encoding="utf-8")
    _store_moving_manifest(conn, container_id, manifest)

    assert migrate_container_ops(conn, container_id) is True
    assert (root / "ops" / "artifacts" / "data.txt").read_text(
        encoding="utf-8"
    ) == "artifacts"


def test_v1_upgrade_preserves_ambiguous_container_documents_for_owner(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    root = tmp_path / "v1-ambiguous-docs"
    container_id = _legacy_container(conn, root, "v1-ambiguous-docs")
    (root / "wiki").mkdir()
    (root / "wiki" / "data.txt").write_text("wiki", encoding="utf-8")
    manifest = _v1_manifest(root, "wiki")
    (root / "ops").mkdir()
    legacy = b"# Owner legacy document\n"
    physical = b"# Physical candidate\n"
    (root / "container.md").write_bytes(legacy)
    (root / "ops" / "container.md").write_bytes(physical)
    _store_moving_manifest(conn, container_id, manifest)

    assert migrate_container_ops(conn, container_id) is False
    assert (root / "container.md").read_bytes() == legacy
    assert (root / "ops" / "container.md").read_bytes() == physical
    assert (root / "wiki" / "data.txt").read_text(encoding="utf-8") == "wiki"


def test_legacy_owner_container_document_is_hash_bound_and_migrated(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "legacy-owner-document"
    container_id = _legacy_container(conn, root, "legacy-owner-document")
    owner_document = b"---\r\nidentity: Owner\r\n---\r\n\r\n# Exact bytes\r\n"
    (root / "container.md").write_bytes(owner_document)

    assert migrate_container_ops(conn, container_id) is True
    assert not (root / "container.md").exists()
    assert (root / "ops" / "container.md").read_bytes() == owner_document
    marker = conn.execute(
        "SELECT manifest_json FROM container_ops_migrations WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    manifest = json.loads(marker["manifest_json"])
    assert manifest["container_doc"] == {
        "path": "container.md",
        "strategy": "move",
        "sha256": hashlib.sha256(owner_document).hexdigest(),
    }
    assert any(
        entry["name"] == "container.md"
        and entry["sha256"] == hashlib.sha256(owner_document).hexdigest()
        for entry in manifest["entries"]
    )


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


def test_collision_recovery_detail_is_exact_and_read_only(tmp_path: Path):
    root = tmp_path / "collision-detail"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "legacy.md").write_text("legacy", encoding="utf-8")
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "physical.md").write_text("physical", encoding="utf-8")
    api, headers = _api(tmp_path)

    linked = api.post(
        "/api/projects/link",
        headers=headers,
        json={"path": str(root), "name": "Collision detail", "slug": "collision-detail"},
    )
    assert linked.status_code == 201, linked.text
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    response = api.get(
        "/api/projects/collision-detail/ops-migration",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"] == {
        "id": body["project"]["id"],
        "slug": "collision-detail",
        "name": "Collision detail",
    }
    assert body["stored_reason"] == "physical Ops root is not empty"
    assert body["phase"] == "attention"
    assert body["active_ops_path"] == "."
    assert body["physical_ops"]["state"] == "populated"
    assert body["retry_safe"] is False
    assert body["what_remains_usable"]["legacy_ops_active"] is True
    assert {
        item["path"]: item["layout"]
        for item in body["legacy_owned_paths"]
        if item["path"] == "wiki"
    } == {"wiki": "both"}

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_partial_move_recovers_from_durable_manifest(tmp_path: Path, monkeypatch):
    conn = _database(tmp_path)
    root = tmp_path / "partial"
    container_id = _legacy_container(conn, root, "partial")
    for dirname, data in (("wiki", b"wiki"), ("artifacts", b"artifact")):
        (root / dirname).mkdir()
        (root / dirname / "data.bin").write_bytes(data)

    real_replace = container_registry._rename_noreplace
    moves = 0

    class SimulatedProcessDeath(BaseException):
        pass

    def die_after_one_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise SimulatedProcessDeath
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", die_after_one_move)
    with pytest.raises(SimulatedProcessDeath):
        migrate_container_ops(conn, container_id)

    marker = conn.execute(
        "SELECT status, manifest_json FROM container_ops_migrations WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    assert marker["status"] == "moving"
    assert marker["manifest_json"]
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

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


def _owned_api_legacy(api: TestClient, root: Path, slug: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    conn = api.app.state.db
    owner_id = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        (slug, slug.replace("-", " ").title(), str(root), owner_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', '.', 'auto')",
        (container_id,),
    )
    return int(container_id)


def test_ops_migration_detail_rejects_symlink_without_following_it(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = tmp_path / "symlink-layout"
    outside = tmp_path / "outside-owned-layout"
    outside.mkdir()
    (outside / "do-not-read.md").write_text("outside bytes", encoding="utf-8")
    container_id = _owned_api_legacy(api, root, "symlink-layout")
    (root / "wiki").symlink_to(outside, target_is_directory=True)

    assert migrate_container_ops(api.app.state.db, container_id) is False
    before = (outside / "do-not-read.md").read_bytes()
    detail = api.get(
        "/api/projects/symlink-layout/ops-migration",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["retry_safe"] is False
    assert "symlink" in body["stored_reason"]
    assert next(
        item for item in body["legacy_owned_paths"] if item["path"] == "wiki"
    )["legacy_state"] == "symlink"
    retry = api.post(
        "/api/projects/symlink-layout/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 409
    assert (outside / "do-not-read.md").read_bytes() == before


def test_ops_migration_detail_reports_repo_overlap_and_keeps_legacy_active(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = tmp_path / "overlap-layout"
    container_id = _owned_api_legacy(api, root, "overlap-layout")
    (root / "wiki").mkdir()
    (root / "wiki" / "repo-note.md").write_text("repo", encoding="utf-8")
    api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'wiki', 'manual')",
        (container_id,),
    )

    assert migrate_container_ops(api.app.state.db, container_id) is False
    detail = api.post(
        "/api/projects/overlap-layout/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["retry_safe"] is False
    assert body["active_ops_path"] == "."
    assert body["stored_reason"] == "legacy Ops path overlaps a repo Area: wiki"
    assert (root / "wiki" / "repo-note.md").read_text(encoding="utf-8") == "repo"


def test_interrupted_move_is_visible_and_owner_retry_resolves_attention(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "interrupted-layout"
    container_id = _owned_api_legacy(api, root, "interrupted-layout")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")

    real_replace = container_registry._rename_noreplace
    moves = 0

    def interrupt_second_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise OSError("simulated interrupted move")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", interrupt_second_move)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

    detail = api.get(
        "/api/projects/interrupted-layout/ops-migration",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["phase"] == "moving"
    assert body["retry_safe"] is True
    assert body["attention"]["status"] == "open"
    assert body["what_remains_usable"]["unavailable_paths"]
    assert {item["layout"] for item in body["legacy_owned_paths"]} == {
        "legacy",
        "physical",
    }

    retried = api.post(
        "/api/projects/interrupted-layout/ops-migration/retry",
        headers=headers,
    )
    assert retried.status_code == 200, retried.text
    resolved = retried.json()
    assert resolved["phase"] == "complete"
    assert resolved["active_ops_path"] == "ops"
    assert resolved["attention"]["status"] == "resolved"
    assert resolved["retry_safe"] is False
    assert (root / "ops" / "wiki" / "data.txt").read_text(encoding="utf-8") == "wiki"
    assert (
        root / "ops" / "artifacts" / "data.txt"
    ).read_text(encoding="utf-8") == "artifact"


def test_interrupted_retry_rechecks_late_code_area_before_any_remaining_move(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "late-overlap-layout"
    container_id = _owned_api_legacy(api, root, "late-overlap-layout")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")

    real_replace = container_registry._rename_noreplace
    moves = 0

    def interrupt_second_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise OSError("simulated interrupted move")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", interrupt_second_move)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

    registered = api.post(
        "/api/projects/late-overlap-layout/areas",
        headers=headers,
        json={"rel_path": "artifacts"},
    )
    assert registered.status_code == 201, registered.text

    detail = api.post(
        "/api/projects/late-overlap-layout/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["retry_safe"] is False
    assert "overlaps a repo Area" in detail.json()["validation_reason"]

    retry = api.post(
        "/api/projects/late-overlap-layout/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 409
    assert (root / "artifacts" / "data.txt").read_text(encoding="utf-8") == "artifact"
    assert not (root / "ops" / "artifacts").exists()


def test_interrupted_retry_rejects_late_physical_ops_root_area_before_any_move(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "late-ops-root-overlap"
    container_id = _owned_api_legacy(api, root, "late-ops-root-overlap")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")

    real_replace = container_registry._rename_noreplace
    moves = 0

    def interrupt_second_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise OSError("simulated interrupted move")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", interrupt_second_move)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

    registered = api.post(
        "/api/projects/late-ops-root-overlap/areas",
        headers=headers,
        json={"rel_path": "ops"},
    )
    assert registered.status_code == 201, registered.text

    detail = api.post(
        "/api/projects/late-ops-root-overlap/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["retry_safe"] is False
    assert "overlaps a repo Area" in detail.json()["validation_reason"]

    retry = api.post(
        "/api/projects/late-ops-root-overlap/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 409
    assert (root / "artifacts" / "data.txt").read_text(encoding="utf-8") == "artifact"
    assert not (root / "ops" / "artifacts").exists()


def test_interrupted_retry_rejects_container_doc_symlink_before_remaining_move(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "container-doc-symlink-layout"
    container_id = _owned_api_legacy(api, root, "container-doc-symlink-layout")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")

    real_replace = container_registry._rename_noreplace
    moves = 0

    def interrupt_second_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise OSError("simulated interrupted move")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", interrupt_second_move)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

    outside = tmp_path / "outside-container.md"
    outside.write_text("outside", encoding="utf-8")
    container_doc = root / "ops" / "container.md"
    container_doc.unlink()
    container_doc.symlink_to(outside)

    detail = api.post(
        "/api/projects/container-doc-symlink-layout/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["retry_safe"] is False
    assert "container.md" in detail.json()["validation_reason"]
    assert "symlink" in detail.json()["validation_reason"]

    retry = api.post(
        "/api/projects/container-doc-symlink-layout/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 409
    assert (root / "artifacts" / "data.txt").read_text(encoding="utf-8") == "artifact"
    assert not (root / "ops" / "artifacts").exists()
    assert outside.read_text(encoding="utf-8") == "outside"


def test_interrupted_retry_rejects_changed_container_doc_before_remaining_move(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "container-doc-tampering"
    container_id = _owned_api_legacy(api, root, "container-doc-tampering")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")

    real_replace = container_registry._rename_noreplace
    moves = 0

    def interrupt_second_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise OSError("simulated interrupted move")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", interrupt_second_move)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

    (root / "ops" / "container.md").write_text(
        "# Unplanned authority\n",
        encoding="utf-8",
    )

    detail = api.post(
        "/api/projects/container-doc-tampering/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["retry_safe"] is False
    assert "container.md" in detail.json()["validation_reason"]
    assert "changed" in detail.json()["validation_reason"]

    retry = api.post(
        "/api/projects/container-doc-tampering/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 409
    assert (root / "artifacts" / "data.txt").read_text(encoding="utf-8") == "artifact"
    assert not (root / "ops" / "artifacts").exists()
    assert (root / "ops" / "container.md").read_text(
        encoding="utf-8"
    ) == "# Unplanned authority\n"


def test_retry_serializes_late_area_registration_before_manifest_apply(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "serialized-area-registration"
    container_id = _owned_api_legacy(api, root, "serialized-area-registration")
    for name, content in (("wiki", "wiki"), ("artifacts", "artifact")):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(content, encoding="utf-8")

    real_replace = container_registry._rename_noreplace
    moves = 0

    def interrupt_second_move(source, destination, **kwargs):
        nonlocal moves
        if Path(source).parent == root:
            moves += 1
            if moves == 2:
                raise OSError("simulated interrupted move")
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(container_registry, "_rename_noreplace", interrupt_second_move)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    monkeypatch.setattr(container_registry, "_rename_noreplace", real_replace)

    real_snapshot_at = container_registry._entry_snapshot_at
    apply_entered = threading.Event()
    release_apply = threading.Event()
    artifact_hashes = 0

    def pause_manifest_apply(directory_fd: int, name: str):
        nonlocal artifact_hashes
        result = real_snapshot_at(directory_fd, name)
        if name == "artifacts":
            artifact_hashes += 1
            if artifact_hashes == 2:
                apply_entered.set()
                assert release_apply.wait(timeout=5)
        return result

    monkeypatch.setattr(
        container_registry,
        "_entry_snapshot_at",
        pause_manifest_apply,
    )
    area_finished = threading.Event()

    def add_area():
        try:
            return api.post(
                "/api/projects/serialized-area-registration/areas",
                headers=headers,
                json={"rel_path": "ops"},
            )
        finally:
            area_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_future = pool.submit(
            lambda: api.post(
                "/api/projects/serialized-area-registration/ops-migration/retry",
                headers=headers,
            )
        )
        assert apply_entered.wait(timeout=5)
        area_future = pool.submit(add_area)
        area_was_blocked = not area_finished.wait(timeout=0.25)
        release_apply.set()
        retry = retry_future.result(timeout=5)
        area = area_future.result(timeout=5)

    assert area_was_blocked is True
    assert retry.status_code == 200, retry.text
    assert area.status_code == 409, area.text
    assert (root / "ops" / "artifacts" / "data.txt").read_text(
        encoding="utf-8"
    ) == "artifact"


def test_container_mutation_lock_excludes_another_process(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "multi-process-lock"
    container_id = _legacy_container(conn, root, "multi-process-lock")
    database = tmp_path / "proxima.db"
    attempted = tmp_path / "attempted"
    acquired = tmp_path / "acquired"
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from proxima_api.container_registry import container_mutation_lock",
            "from proxima_api.db import connect",
            "db_path, container_id, attempted, acquired = sys.argv[1:]",
            "Path(attempted).write_text('attempted', encoding='utf-8')",
            "with container_mutation_lock(connect(db_path), int(container_id)):",
            "    Path(acquired).write_text('acquired', encoding='utf-8')",
        )
    )
    api_root = Path(__file__).resolve().parents[1]

    with container_registry.container_mutation_lock(conn, container_id):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(database),
                str(container_id),
                str(attempted),
                str(acquired),
            ],
            cwd=api_root,
        )
        deadline = time.monotonic() + 5
        while not attempted.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempted.exists()
        time.sleep(0.2)
        assert not acquired.exists()

    assert process.wait(timeout=5) == 0
    assert acquired.read_text(encoding="utf-8") == "acquired"


def test_retry_waits_for_active_container_process_lease(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "active-process-lease"
    container_id = _legacy_container(conn, root, "active-process-lease")
    (root / "wiki").mkdir()
    (root / "wiki" / "keep.md").write_text("keep", encoding="utf-8")
    with container_registry.container_mutation_lock(conn, container_id):
        lease = container_registry.acquire_container_activity_lease(
            conn,
            container_id,
        )
    finished = threading.Event()
    result: list[bool] = []

    def migrate():
        try:
            result.append(migrate_container_ops(conn, container_id))
        finally:
            finished.set()

    thread = threading.Thread(target=migrate)
    thread.start()
    try:
        assert finished.wait(timeout=0.25) is False
    finally:
        lease.release()
    thread.join(timeout=5)

    assert finished.is_set()
    assert result == [True]
    assert (root / "ops" / "wiki" / "keep.md").is_file()


def test_container_activity_lease_excludes_quiescence_in_another_process(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    root = tmp_path / "multi-process-activity"
    container_id = _legacy_container(conn, root, "multi-process-activity")
    database = tmp_path / "proxima.db"
    attempted = tmp_path / "activity-attempted"
    acquired = tmp_path / "activity-acquired"
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from proxima_api.container_registry import container_quiescence_lock",
            "from proxima_api.db import connect",
            "db_path, container_id, attempted, acquired = sys.argv[1:]",
            "Path(attempted).write_text('attempted', encoding='utf-8')",
            "with container_quiescence_lock(connect(db_path), int(container_id)):",
            "    Path(acquired).write_text('acquired', encoding='utf-8')",
        )
    )
    api_root = Path(__file__).resolve().parents[1]
    lease = container_registry.acquire_container_activity_lease(
        conn,
        container_id,
    )
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(database),
                str(container_id),
                str(attempted),
                str(acquired),
            ],
            cwd=api_root,
        )
        deadline = time.monotonic() + 5
        while not attempted.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempted.exists()
        time.sleep(0.2)
        assert not acquired.exists()
    finally:
        lease.release()

    assert process.wait(timeout=5) == 0
    assert acquired.read_text(encoding="utf-8") == "acquired"


def test_activity_guardian_survives_parent_exit_and_detached_writer(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    root = tmp_path / "guardian-parent-exit"
    container_id = _legacy_container(conn, root, "guardian-parent-exit")
    database = tmp_path / "proxima.db"
    ready = tmp_path / "guardian-ready"
    acquired = tmp_path / "guardian-acquired"
    api_root = Path(__file__).resolve().parents[1]
    descendant = "import time; time.sleep(0.8)"
    writer = "\n".join(
        (
            "import subprocess, sys",
            f"subprocess.Popen([sys.executable, '-c', {descendant!r}], start_new_session=True)",
        )
    )
    launcher = "\n".join(
        (
            "import os, subprocess, sys",
            "from pathlib import Path",
            "from proxima_api.container_registry import acquire_container_activity_lease",
            "from proxima_api.db import connect",
            "db_path, container_id, ready, writer = sys.argv[1:]",
            "lease = acquire_container_activity_lease(connect(db_path), int(container_id))",
            "command, options = lease.guard_process([sys.executable, '-c', writer])",
            "subprocess.Popen(command, cwd=os.getcwd(), **options)",
            "lease.mark_process_started()",
            "Path(ready).write_text('ready', encoding='utf-8')",
            "os._exit(0)",
        )
    )
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            launcher,
            str(database),
            str(container_id),
            str(ready),
            writer,
        ],
        cwd=api_root,
    )
    assert parent.wait(timeout=5) == 0
    assert ready.is_file()
    finished = threading.Event()

    def acquire_quiescence() -> None:
        with container_registry.container_quiescence_lock(conn, container_id):
            acquired.write_text("acquired", encoding="utf-8")
        finished.set()

    thread = threading.Thread(target=acquire_quiescence)
    thread.start()
    assert finished.wait(timeout=0.25) is False
    thread.join(timeout=5)

    assert finished.is_set()
    assert acquired.read_text(encoding="utf-8") == "acquired"


def test_generated_container_doc_creation_never_clobbers_late_content(
    tmp_path: Path,
    monkeypatch,
):
    conn = _database(tmp_path)
    root = tmp_path / "late-container-doc"
    container_id = _legacy_container(conn, root, "late-container-doc")
    (root / "wiki").mkdir()
    (root / "wiki" / "keep.md").write_text("keep", encoding="utf-8")
    real_publish = container_registry._publish_anonymous_file
    injected = False

    def inject_destination(temp_fd: int, parent_fd: int, target_name: str):
        nonlocal injected
        if not injected:
            injected = True
            (root / "ops" / "container.md").write_bytes(b"late owner content")
        return real_publish(temp_fd, parent_fd, target_name)

    monkeypatch.setattr(
        container_registry,
        "_publish_anonymous_file",
        inject_destination,
    )

    assert migrate_container_ops(conn, container_id) is False
    assert (root / "ops" / "container.md").read_bytes() == b"late owner content"
    assert (root / "wiki" / "keep.md").read_text(encoding="utf-8") == "keep"


def test_manifest_rename_never_clobbers_late_destination(
    tmp_path: Path,
    monkeypatch,
):
    conn = _database(tmp_path)
    root = tmp_path / "late-manifest-destination"
    container_id = _legacy_container(conn, root, "late-manifest-destination")
    (root / "design.md").write_bytes(b"legacy design")
    real_rename = container_registry._rename_noreplace
    injected = False

    def inject_destination(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            (root / "ops" / "design.md").write_bytes(b"late destination")
        return real_rename(*args, **kwargs)

    monkeypatch.setattr(
        container_registry,
        "_rename_noreplace",
        inject_destination,
    )

    assert migrate_container_ops(conn, container_id) is False
    assert (root / "design.md").read_bytes() == b"legacy design"
    assert (root / "ops" / "design.md").read_bytes() == b"late destination"


@pytest.mark.parametrize(
    "stage",
    ("before_temp", "after_create", "after_ready", "after_publish"),
)
def test_generated_document_recovers_each_owned_write_stage(
    tmp_path: Path,
    stage: str,
):
    conn = _database(tmp_path)
    root = tmp_path / f"document-stage-{stage}"
    container_id = _legacy_container(conn, root, f"document-stage-{stage}")
    (root / "wiki").mkdir()
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    planned = manifest["container_doc"]
    recovery = planned["recovery_temp"]
    container_registry._upsert_marker(conn, container_id, "moving", manifest)
    (root / "ops").mkdir()
    recovery_path = root / "ops" / recovery["path"]
    if stage in {"after_create", "after_ready", "after_publish"}:
        recovery_path.write_bytes(
            b"" if stage == "after_create" else planned["content"].encode()
        )
        recovery["phase"] = "created" if stage == "after_create" else "ready"
        recovery["identity"] = container_registry._stat_identity(recovery_path.lstat())
        container_registry._upsert_marker(
            conn,
            container_id,
            "moving",
            manifest,
        )
    if stage == "after_publish":
        (root / "ops" / "container.md").hardlink_to(recovery_path)

    assert migrate_container_ops(conn, container_id) is True
    assert (root / "ops" / "container.md").read_text(
        encoding="utf-8"
    ) == planned["content"]
    assert not (root / "ops" / recovery["path"]).exists()


@pytest.mark.parametrize("phase", ("prepared", "ready"))
def test_anonymous_document_publication_resumes_each_link_boundary(
    tmp_path: Path,
    phase: str,
):
    parent = tmp_path / "anonymous-publication"
    parent.mkdir()
    target = parent / "container.md"
    content = "durable generated document"
    expected_hash = hashlib.sha256(content.encode()).hexdigest()
    recovery = container_registry._planned_recovery_temp(expected_hash)

    class SimulatedCrash(BaseException):
        pass

    def persist() -> None:
        if recovery["phase"] == phase:
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        container_registry._atomic_write_if_missing(
            target,
            content,
            expected_hash=expected_hash,
            recovery_temp=recovery,
            persist_recovery=persist,
        )

    container_registry._atomic_write_if_missing(
        target,
        content,
        expected_hash=expected_hash,
        recovery_temp=recovery,
        persist_recovery=lambda: None,
    )

    assert target.read_text(encoding="utf-8") == content
    assert recovery["phase"] == "complete"
    assert not (parent / recovery["path"]).exists()


def test_named_recovery_reconciles_only_owned_hidden_artifact(
    tmp_path: Path,
    monkeypatch,
):
    parent = tmp_path / "named-publication"
    parent.mkdir()
    target = parent / "container.md"
    content = "durable generated document"
    expected_hash = hashlib.sha256(content.encode()).hexdigest()
    recovery = container_registry._planned_recovery_temp(expected_hash)
    real_open = os.open
    real_fsync = os.fsync
    crashed = False

    def no_anonymous(path, flags, *args, **kwargs):
        if hasattr(os, "O_TMPFILE") and flags & os.O_TMPFILE == os.O_TMPFILE:
            raise OSError(errno.ENOTSUP, "anonymous files unavailable")
        return real_open(path, flags, *args, **kwargs)

    def crash_after_hidden_identity(fd: int):
        nonlocal crashed
        if recovery["phase"] == "creating" and not crashed:
            crashed = True
            raise OSError("simulated process exit")
        return real_fsync(fd)

    monkeypatch.setattr(container_registry.os, "open", no_anonymous)
    monkeypatch.setattr(container_registry.os, "fsync", crash_after_hidden_identity)
    with pytest.raises(OSError, match="simulated process exit"):
        container_registry._atomic_write_if_missing(
            target,
            content,
            expected_hash=expected_hash,
            recovery_temp=recovery,
            persist_recovery=lambda: None,
        )

    monkeypatch.setattr(container_registry.os, "fsync", real_fsync)
    container_registry._atomic_write_if_missing(
        target,
        content,
        expected_hash=expected_hash,
        recovery_temp=recovery,
        persist_recovery=lambda: None,
    )

    assert target.read_text(encoding="utf-8") == content
    assert recovery["phase"] == "complete"


def test_v2_generated_document_manifest_upgrades_with_owned_recovery(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    root = tmp_path / "v2-generated-document"
    container_id = _legacy_container(conn, root, "v2-generated-document")
    (root / "wiki").mkdir()
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    manifest["version"] = 2
    manifest["container_doc"].pop("recovery_temp")
    _store_moving_manifest(conn, container_id, manifest)

    assert migrate_container_ops(conn, container_id) is True
    marker = conn.execute(
        "SELECT migration_version, manifest_json "
        "FROM container_ops_migrations WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    upgraded = json.loads(marker["manifest_json"])
    assert marker["migration_version"] == container_registry.OPS_MIGRATION_VERSION
    assert upgraded["version"] == container_registry.OPS_MIGRATION_VERSION
    recovery = upgraded["container_doc"]["recovery_temp"]
    assert recovery["path"].startswith(container_registry.RECOVERY_TEMP_PREFIX)
    assert recovery["phase"] == "complete"
    assert recovery["identity"]


def test_v4_planned_document_upgrades_to_owned_recovery_protocol(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    root = tmp_path / "v4-generated-document"
    container_id = _legacy_container(conn, root, "v4-generated-document")
    (root / "wiki").mkdir()
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    manifest["version"] = 4
    manifest["container_doc"]["recovery_temp"].pop("ownership_token")
    _store_moving_manifest(conn, container_id, manifest)

    assert migrate_container_ops(conn, container_id) is True
    marker = conn.execute(
        "SELECT migration_version, manifest_json "
        "FROM container_ops_migrations WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    upgraded = json.loads(marker["manifest_json"])
    recovery = upgraded["container_doc"]["recovery_temp"]
    assert marker["migration_version"] == container_registry.OPS_MIGRATION_VERSION
    assert upgraded["version"] == container_registry.OPS_MIGRATION_VERSION
    assert len(recovery["ownership_token"]) == 64
    assert recovery["phase"] == "complete"


def test_recovery_temp_cleanup_requires_exact_manifest_ownership(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "ambiguous-recovery-temp"
    container_id = _legacy_container(conn, root, "ambiguous-recovery-temp")
    (root / "wiki").mkdir()
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    recovery = manifest["container_doc"]["recovery_temp"]
    container_registry._upsert_marker(conn, container_id, "moving", manifest)
    (root / "ops").mkdir()
    ambiguous = b"owner bytes at internal-looking path"
    (root / "ops" / recovery["path"]).write_bytes(ambiguous)

    assert migrate_container_ops(conn, container_id) is False
    assert (root / "ops" / recovery["path"]).read_bytes() == ambiguous
    assert not (root / "ops" / "container.md").exists()


def test_recovery_temp_spoof_with_exact_bytes_is_preserved(tmp_path: Path):
    conn = _database(tmp_path)
    root = tmp_path / "spoofed-recovery-temp"
    container_id = _legacy_container(conn, root, "spoofed-recovery-temp")
    (root / "wiki").mkdir()
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    planned = manifest["container_doc"]
    recovery = planned["recovery_temp"]
    container_registry._upsert_marker(conn, container_id, "moving", manifest)
    (root / "ops").mkdir()
    spoof = root / "ops" / recovery["path"]
    spoof.write_text(planned["content"], encoding="utf-8")

    assert migrate_container_ops(conn, container_id) is False
    assert spoof.read_text(encoding="utf-8") == planned["content"]
    assert not (root / "ops" / "container.md").exists()


def test_unproven_generated_document_with_exact_bytes_is_preserved(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    root = tmp_path / "unproven-generated-document"
    container_id = _legacy_container(
        conn,
        root,
        "unproven-generated-document",
    )
    (root / "wiki").mkdir()
    manifest = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, container_id),
    )
    planned = manifest["container_doc"]
    container_registry._upsert_marker(conn, container_id, "moving", manifest)
    (root / "ops").mkdir()
    target = root / "ops" / "container.md"
    target.write_text(planned["content"], encoding="utf-8")

    assert migrate_container_ops(conn, container_id) is False
    assert target.read_text(encoding="utf-8") == planned["content"]
    assert (root / "wiki").is_dir()


def test_recovery_artifact_intent_is_unpredictable_and_manifest_bound(
    tmp_path: Path,
):
    conn = _database(tmp_path)
    first_root = tmp_path / "recovery-intent-first"
    second_root = tmp_path / "recovery-intent-second"
    first_id = _legacy_container(conn, first_root, "recovery-intent-first")
    second_id = _legacy_container(
        conn,
        second_root,
        "recovery-intent-second",
    )
    first = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, first_id),
    )["container_doc"]["recovery_temp"]
    second = container_registry._build_manifest(
        conn,
        container_registry.get_container(conn, second_id),
    )["container_doc"]["recovery_temp"]

    assert first["path"] != second["path"]
    assert first["phase"] == second["phase"] == "planned"
    assert first["identity"] is second["identity"] is None


def test_retry_serializes_virtual_file_write_before_root_resolution(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "serialized-file-write"
    container_id = _owned_api_legacy(api, root, "serialized-file-write")
    (root / "wiki").mkdir()
    (root / "wiki" / "existing.md").write_text("existing", encoding="utf-8")
    _prepare_completed_filesystem_move(api.app.state.db, container_id, root)

    apply_entered = threading.Event()
    release_apply = threading.Event()
    real_exclude = container_registry.exclude_ops_from_root_repo

    def pause_before_database_switch(path: Path, **kwargs):
        apply_entered.set()
        assert release_apply.wait(timeout=5)
        return real_exclude(path, **kwargs)

    monkeypatch.setattr(
        container_registry,
        "exclude_ops_from_root_repo",
        pause_before_database_switch,
    )
    write_finished = threading.Event()

    def write_file():
        try:
            return api.put(
                "/api/projects/serialized-file-write/file",
                params={"path": "wiki/new.md"},
                headers=headers,
                json={"content": "late owner edit"},
            )
        finally:
            write_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_future = pool.submit(
            lambda: api.post(
                "/api/projects/serialized-file-write/ops-migration/retry",
                headers=headers,
            )
        )
        assert apply_entered.wait(timeout=5)
        write_future = pool.submit(write_file)
        write_was_blocked = not write_finished.wait(timeout=0.25)
        release_apply.set()
        retry = retry_future.result(timeout=5)
        write = write_future.result(timeout=5)

    assert write_was_blocked is True
    assert retry.status_code == 200, retry.text
    assert write.status_code == 200, write.text
    assert not (root / "wiki").exists()
    assert (root / "ops" / "wiki" / "new.md").read_text(
        encoding="utf-8"
    ) == "late owner edit"


def test_retry_serializes_complete_project_purge(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "serialized-project-delete"
    container_id = _owned_api_legacy(api, root, "serialized-project-delete")
    (root / "wiki").mkdir()
    (root / "wiki" / "existing.md").write_text("existing", encoding="utf-8")
    _prepare_completed_filesystem_move(api.app.state.db, container_id, root)

    apply_entered = threading.Event()
    release_apply = threading.Event()
    real_exclude = container_registry.exclude_ops_from_root_repo

    def pause_before_database_switch(path: Path, **kwargs):
        apply_entered.set()
        assert release_apply.wait(timeout=5)
        return real_exclude(path, **kwargs)

    monkeypatch.setattr(
        container_registry,
        "exclude_ops_from_root_repo",
        pause_before_database_switch,
    )
    delete_finished = threading.Event()

    def delete_project():
        try:
            return api.delete(
                "/api/projects/serialized-project-delete",
                headers=headers,
            )
        finally:
            delete_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_future = pool.submit(
            lambda: api.post(
                "/api/projects/serialized-project-delete/ops-migration/retry",
                headers=headers,
            )
        )
        assert apply_entered.wait(timeout=5)
        delete_future = pool.submit(delete_project)
        delete_was_blocked = not delete_finished.wait(timeout=0.25)
        release_apply.set()
        try:
            retry = retry_future.result(timeout=5)
        except Exception as exc:
            retry = exc
        delete = delete_future.result(timeout=5)

    assert delete_was_blocked is True
    assert not isinstance(retry, Exception)
    assert retry.status_code == 200, retry.text
    assert delete.status_code == 200, delete.text
    assert root.is_dir()
    assert (root / "ops" / "wiki" / "existing.md").read_text(
        encoding="utf-8"
    ) == "existing"
    assert api.app.state.db.execute(
        "SELECT 1 FROM projects WHERE id = ?",
        (container_id,),
    ).fetchone() is None


def test_retry_serializes_design_writer_before_root_resolution(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "serialized-design-writer"
    container_id = _owned_api_legacy(api, root, "serialized-design-writer")
    image = root / "artifacts" / "media" / "images" / "source.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    )
    (root / "wiki").mkdir()
    (root / "wiki" / "existing.md").write_text("existing", encoding="utf-8")
    _prepare_completed_filesystem_move(api.app.state.db, container_id, root)
    apply_entered = threading.Event()
    release_apply = threading.Event()
    real_exclude = container_registry.exclude_ops_from_root_repo

    def pause_before_database_switch(path: Path, **kwargs):
        apply_entered.set()
        assert release_apply.wait(timeout=5)
        return real_exclude(path, **kwargs)

    monkeypatch.setattr(
        container_registry,
        "exclude_ops_from_root_repo",
        pause_before_database_switch,
    )
    design_finished = threading.Event()

    def create_design():
        try:
            return api.post(
                "/api/projects/serialized-design-writer/designs/from-image",
                headers=headers,
                json={"path": "artifacts/media/images/source.png"},
            )
        finally:
            design_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_future = pool.submit(
            lambda: api.post(
                "/api/projects/serialized-design-writer/ops-migration/retry",
                headers=headers,
            )
        )
        assert apply_entered.wait(timeout=5)
        design_future = pool.submit(create_design)
        design_was_blocked = not design_finished.wait(timeout=0.25)
        release_apply.set()
        retry = retry_future.result(timeout=5)
        design = design_future.result(timeout=5)

    assert design_was_blocked is True
    assert retry.status_code == 200, retry.text
    assert design.status_code == 200, design.text
    scene = root / "ops" / design.json()["path"] / "scene.json"
    assert scene.is_file()


def test_parent_symlink_swap_cannot_redirect_manifest_move(
    tmp_path: Path,
    monkeypatch,
):
    conn = _database(tmp_path)
    root = tmp_path / "parent-swap"
    outside = tmp_path / "outside-parent-swap"
    parked = tmp_path / "parked-physical-ops"
    outside.mkdir()
    container_id = _legacy_container(conn, root, "parent-swap")
    (root / "design.md").write_bytes(b"legacy design")
    real_rename = container_registry._rename_noreplace
    swapped = False

    def swap_parent_then_rename(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            (root / "ops").rename(parked)
            (root / "ops").symlink_to(outside, target_is_directory=True)
        return real_rename(*args, **kwargs)

    monkeypatch.setattr(
        container_registry,
        "_rename_noreplace",
        swap_parent_then_rename,
    )

    assert migrate_container_ops(conn, container_id) is False
    assert not (outside / "design.md").exists()
    assert (parked / "design.md").read_bytes() == b"legacy design"


def test_manifest_move_rejects_same_content_inode_swap(
    tmp_path: Path,
    monkeypatch,
):
    conn = _database(tmp_path)
    root = tmp_path / "inode-swap"
    container_id = _legacy_container(conn, root, "inode-swap")
    source = root / "design.md"
    source.write_bytes(b"owner design")
    parked = root / "design-original.md"
    real_rename = container_registry._rename_noreplace
    swapped = False

    def swap_inode_then_rename(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            source.rename(parked)
            source.write_bytes(b"owner design")
        return real_rename(*args, **kwargs)

    monkeypatch.setattr(
        container_registry,
        "_rename_noreplace",
        swap_inode_then_rename,
    )

    assert migrate_container_ops(conn, container_id) is False
    assert source.read_bytes() == b"owner design"
    assert parked.read_bytes() == b"owner design"
    assert not (root / "ops" / "design.md").exists()


def test_git_exclude_uses_opened_root_after_path_replacement(
    tmp_path: Path,
    monkeypatch,
):
    conn = _database(tmp_path)
    root = tmp_path / "exclude-root-swap"
    parked = tmp_path / "exclude-root-parked"
    outside = tmp_path / "exclude-outside"
    container_id = _legacy_container(conn, root, "exclude-root-swap")
    (root / "wiki").mkdir()
    (root / ".git" / "info").mkdir(parents=True)
    (outside / ".git" / "info").mkdir(parents=True)
    real_exclude = container_registry._exclude_ops_from_root_repo_at
    swapped = False

    def replace_root_then_exclude(root_fd: int):
        nonlocal swapped
        if not swapped:
            swapped = True
            root.rename(parked)
            root.symlink_to(outside, target_is_directory=True)
        return real_exclude(root_fd)

    monkeypatch.setattr(
        container_registry,
        "_exclude_ops_from_root_repo_at",
        replace_root_then_exclude,
    )

    assert migrate_container_ops(conn, container_id) is False
    assert "/ops/" in (parked / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert not (outside / ".git" / "info" / "exclude").exists()


def test_migration_detail_exposes_unavailable_root_inspectability(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = tmp_path / "unavailable-inspection-root"
    _owned_api_legacy(api, root, "unavailable-inspection-root")
    unavailable = tmp_path / "moved-unavailable-inspection-root"
    root.rename(unavailable)

    detail = api.get(
        "/api/projects/unavailable-inspection-root/ops-migration",
        headers=headers,
    )

    assert detail.status_code == 200, detail.text
    legacy = detail.json()["inspection"]["legacy_root"]
    assert legacy["inspectable"] is False
    assert "missing" in legacy["reason"]


def test_repaired_physical_layout_can_retry_to_resolve_open_attention(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = tmp_path / "repaired-physical-layout"
    container_id = _owned_api_legacy(api, root, "repaired-physical-layout")
    (root / "wiki").mkdir()
    (root / "wiki" / "data.txt").write_text("wiki", encoding="utf-8")
    assert migrate_container_ops(api.app.state.db, container_id) is True

    unavailable = tmp_path / "temporarily-unavailable-ops"
    (root / "ops").rename(unavailable)
    assert migrate_container_ops(api.app.state.db, container_id) is False
    unavailable.rename(root / "ops")

    detail = api.post(
        "/api/projects/repaired-physical-layout/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["phase"] == "complete"
    assert body["attention"]["status"] == "open"
    assert body["retry_safe"] is True

    retry = api.post(
        "/api/projects/repaired-physical-layout/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 200, retry.text
    resolved = retry.json()
    assert resolved["phase"] == "complete"
    assert resolved["attention"]["status"] == "resolved"
    assert resolved["retry_safe"] is False
    assert (root / "ops" / "wiki" / "data.txt").read_text(encoding="utf-8") == "wiki"


def test_file_api_can_inspect_explicit_container_side_after_migration(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = tmp_path / "explicit-container-side"
    container_id = _owned_api_legacy(api, root, "explicit-container-side")
    (root / "wiki").mkdir()
    (root / "wiki" / "physical.txt").write_text("physical", encoding="utf-8")
    assert migrate_container_ops(api.app.state.db, container_id) is True
    (root / "wiki").mkdir()
    (root / "wiki" / "legacy.txt").write_text("legacy", encoding="utf-8")

    virtual = api.get(
        "/api/projects/explicit-container-side/file",
        params={"path": "wiki/physical.txt"},
        headers=headers,
    )
    assert virtual.status_code == 200, virtual.text
    assert virtual.json()["content"] == "physical"

    legacy = api.get(
        "/api/projects/explicit-container-side/file",
        params={"path": "wiki/legacy.txt", "root_side": "container"},
        headers=headers,
    )
    physical = api.get(
        "/api/projects/explicit-container-side/file",
        params={"path": "ops/wiki/physical.txt", "root_side": "container"},
        headers=headers,
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["content"] == "legacy"
    assert physical.status_code == 200, physical.text
    assert physical.json()["content"] == "physical"


def test_explicit_container_side_rejects_every_file_mutation(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = tmp_path / "read-only-container-side"
    container_id = _owned_api_legacy(api, root, "read-only-container-side")
    (root / "wiki").mkdir()
    (root / "wiki" / "physical.txt").write_text("physical", encoding="utf-8")
    assert migrate_container_ops(api.app.state.db, container_id) is True
    (root / "wiki").mkdir()
    (root / "wiki" / "write.txt").write_text("keep write", encoding="utf-8")
    (root / "wiki" / "rename.txt").write_text("keep rename", encoding="utf-8")
    (root / "wiki" / "delete.txt").write_text("keep delete", encoding="utf-8")

    responses = [
        api.put(
            "/api/projects/read-only-container-side/file",
            params={"path": "wiki/write.txt", "root_side": "container"},
            headers=headers,
            json={"content": "changed"},
        ),
        api.post(
            "/api/projects/read-only-container-side/fs/mkdir",
            params={"root_side": "container"},
            headers=headers,
            json={"path": "wiki/created"},
        ),
        api.post(
            "/api/projects/read-only-container-side/fs/rename",
            params={"root_side": "container"},
            headers=headers,
            json={"from": "wiki/rename.txt", "to": "wiki/renamed.txt"},
        ),
        api.delete(
            "/api/projects/read-only-container-side/fs",
            params={"path": "wiki/delete.txt", "root_side": "container"},
            headers=headers,
        ),
    ]

    assert [response.status_code for response in responses] == [422, 422, 422, 422]
    assert (root / "wiki" / "write.txt").read_text(encoding="utf-8") == "keep write"
    assert not (root / "wiki" / "created").exists()
    assert (root / "wiki" / "rename.txt").read_text(encoding="utf-8") == "keep rename"
    assert not (root / "wiki" / "renamed.txt").exists()
    assert (root / "wiki" / "delete.txt").read_text(encoding="utf-8") == "keep delete"


def test_validation_blocks_cross_filesystem_retry_before_any_move(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    root = tmp_path / "cross-device-layout"
    container_id = _owned_api_legacy(api, root, "cross-device-layout")
    (root / "wiki").mkdir()
    (root / "wiki" / "keep.md").write_text("keep", encoding="utf-8")
    assert migrate_container_ops(api.app.state.db, container_id) is True

    # Restore a legacy marker-free layout so validation, not migration, owns the check.
    (root / "wiki").mkdir()
    (root / "wiki" / "keep.md").write_text("keep", encoding="utf-8")
    shutil.rmtree(root / "ops")
    api.app.state.db.execute(
        "UPDATE project_areas SET rel_path = '.' WHERE project_id = ? AND kind = 'ops'",
        (container_id,),
    )
    api.app.state.db.execute(
        "DELETE FROM container_ops_migrations WHERE container_id = ?",
        (container_id,),
    )
    real_device = container_registry._path_device

    def different_device(path: Path) -> int:
        device = real_device(path)
        return device + 1 if path == root / "wiki" else device

    monkeypatch.setattr(container_registry, "_path_device", different_device)
    detail = api.post(
        "/api/projects/cross-device-layout/ops-migration/validate",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["retry_safe"] is False
    assert "different filesystems" in detail.json()["validation_reason"]
    retry = api.post(
        "/api/projects/cross-device-layout/ops-migration/retry",
        headers=headers,
    )
    assert retry.status_code == 409
    assert (root / "wiki" / "keep.md").read_text(encoding="utf-8") == "keep"
    assert not (root / "ops").exists()


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
            "SELECT id, path, path_identity FROM projects WHERE slug = 'fresh'"
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


def test_fresh_container_uses_windows_no_reparse_boundary(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "windows-fresh"
    root.mkdir()
    called: list[tuple[Path, str, tuple[str, ...]]] = []

    def create_windows(
        container: Path,
        name: str,
        starter_dirs: tuple[str, ...],
    ) -> Path:
        called.append((container, name, starter_dirs))
        physical = container / "ops"
        physical.mkdir()
        (physical / "container.md").write_text("owned", encoding="utf-8")
        return physical

    monkeypatch.setattr(
        container_registry,
        "_platform_is_windows",
        lambda: True,
    )
    monkeypatch.setattr(
        container_registry,
        "_create_physical_ops_root_windows",
        create_windows,
    )

    physical = container_registry.create_physical_ops_root(
        root,
        "Windows",
    )

    assert physical == root.absolute() / "ops"
    assert called == [
        (
            root.absolute(),
            "Windows",
            container_registry.DEFAULT_STARTER_DIRS,
        )
    ]


def test_windows_starter_directories_are_created_relative_to_stable_handles(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "windows-relative"
    root.mkdir()
    handles: dict[int, Path] = {}
    next_handle = 10
    created: list[tuple[Path, str]] = []

    def open_directory(path: Path):
        nonlocal next_handle
        handle = next_handle
        next_handle += 1
        handles[handle] = path
        return handle, (1, hash(path))

    def create_directory(parent_handle: int, name: str):
        nonlocal next_handle
        parent = handles[parent_handle]
        target = parent / name
        target.mkdir(exist_ok=True)
        created.append((parent, name))
        handle = next_handle
        next_handle += 1
        handles[handle] = target
        return handle, (1, hash(target))

    def create_file(parent_handle: int, name: str, content: bytes):
        (handles[parent_handle] / name).write_bytes(content)

    monkeypatch.setattr(
        container_registry,
        "_windows_open_directory",
        open_directory,
    )
    monkeypatch.setattr(
        container_registry,
        "_windows_create_directory_at",
        create_directory,
    )
    monkeypatch.setattr(
        container_registry,
        "_windows_create_file_at",
        create_file,
    )
    monkeypatch.setattr(
        container_registry,
        "_windows_close_handle",
        lambda _handle: None,
    )

    physical = container_registry._create_physical_ops_root_windows(
        root,
        "Windows relative",
        ("wiki/deep", "artifacts"),
    )

    assert physical == root / "ops"
    assert created == [
        (physical, "wiki"),
        (physical / "wiki", "deep"),
        (physical, "artifacts"),
    ]
    assert (physical / "wiki" / "deep").is_dir()
    assert (physical / "container.md").is_file()


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


def test_dashboard_survives_unavailable_container(tmp_path: Path):
    api, headers = _api(tmp_path)
    healthy = api.post(
        "/api/projects", headers=headers, json={"slug": "healthy", "name": "Healthy"}
    )
    assert healthy.status_code == 201, healthy.text
    report = Path(healthy.json()["path"]) / "ops" / "reports" / "summary.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Summary", encoding="utf-8")

    broken = api.post(
        "/api/projects", headers=headers, json={"slug": "broken", "name": "Broken"}
    )
    assert broken.status_code == 201, broken.text
    shutil.rmtree(Path(broken.json()["path"]))

    dashboard = api.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.text
    artifacts = dashboard.json()["recentArtifacts"]
    assert any(a["project_slug"] == "healthy" for a in artifacts)


def test_archive_list_survives_unavailable_container(tmp_path: Path):
    api, headers = _api(tmp_path)
    broken = api.post(
        "/api/projects", headers=headers, json={"slug": "broken", "name": "Broken"}
    )
    assert broken.status_code == 201, broken.text
    root = Path(broken.json()["path"])
    report = root / "ops" / "reports" / "summary.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Summary", encoding="utf-8")
    project_id = int(
        api.app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'broken'"
        ).fetchone()["id"]
    )
    assert artifact_registry.seed_project(api.app.state.db, project_id, root / "ops") >= 1

    shutil.rmtree(root)

    listing = api.get("/api/archive", headers=headers)
    assert listing.status_code == 200, listing.text
    assert any(item["project_slug"] == "broken" for item in listing.json()["items"])


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

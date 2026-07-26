from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import container_registry
from proxima_api.main import create_app


def _api(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
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
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def _create(
    api: TestClient,
    headers: dict[str, str],
    slug: str = "fleet-one",
    name: str = "Fleet One",
) -> dict:
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": slug, "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_container_routes_are_authenticated_and_keep_project_reader_identity(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project = _create(api, headers)
    root = Path(project["path"])
    (root / ".git").mkdir()
    detected = api.post("/api/projects/fleet-one/areas/detect", headers=headers)
    assert detected.status_code == 200, detected.text

    password = api.post(
        "/auth/set-password",
        json={"password": "correct horse battery"},
    )
    assert password.status_code == 200
    authenticated = {
        "Authorization": f"Bearer {password.json()['token']}"
    }
    fresh = TestClient(api.app)
    assert fresh.get("/api/containers").status_code == 401

    fleet_response = fresh.get("/api/containers", headers=authenticated)
    assert fleet_response.status_code == 200, fleet_response.text
    fleet = fleet_response.json()["containers"]
    container = next(item for item in fleet if item["slug"] == "fleet-one")
    detail = fresh.get(
        "/api/containers/fleet-one",
        headers=authenticated,
    ).json()
    legacy_list = fresh.get("/api/projects", headers=authenticated).json()["projects"]
    legacy_detail = fresh.get(
        "/api/projects/fleet-one",
        headers=authenticated,
    ).json()
    legacy = next(item for item in legacy_list if item["slug"] == "fleet-one")

    assert detail == container
    assert set(legacy) == {
        "slug",
        "name",
        "path",
        "owner",
        "role",
        "visibility",
        "code_areas",
        "ops_area",
    }
    assert set(legacy_detail) == set(legacy)
    for field in ("slug", "name", "path", "owner", "visibility"):
        assert legacy[field] == container[field]
        assert legacy_detail[field] == container[field]

    areas = fresh.get(
        "/api/containers/fleet-one/areas",
        headers=authenticated,
    )
    assert areas.status_code == 200, areas.text
    public_areas = areas.json()
    legacy_areas = fresh.get(
        "/api/projects/fleet-one/areas",
        headers=authenticated,
    ).json()
    assert public_areas["container_id"] == container["id"]
    assert public_areas["container_slug"] == container["slug"]
    assert [
        (area["id"], area["rel_path"])
        for area in public_areas["code_areas"]
    ] == [
        (area["id"], area["rel_path"])
        for area in legacy_areas["code_areas"]
    ]
    assert public_areas["ops_area"]["id"] == legacy_areas["ops_area"]["id"]
    assert public_areas["ops_area"]["rel_path"] == "ops"


def test_registry_refresh_cycle_projects_free_text_identity_and_bounded_summary(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project = _create(api, headers)
    root = Path(project["path"])
    summary = "s" * 700

    written = api.put(
        "/api/projects/fleet-one/file?path=container.md",
        headers=headers,
        json={
            "content": (
                "---\n"
                "identity: Client, internal lab, and anything else\n"
                f"summary: {summary}\n"
                "---\n"
            )
        },
    )
    assert written.status_code == 200, written.text
    immediate = api.get("/api/containers/fleet-one", headers=headers).json()
    assert immediate["identity_label"] == "Client, internal lab, and anything else"
    assert immediate["summary"] == summary[:500]
    assert len(immediate["source_hash"]) == 64
    assert immediate["indexed_at"]

    source_hash = immediate["source_hash"]
    (root / "ops" / "container.md").write_text(
        "---\n"
        "identity: Proxima itself, still free text\n"
        "summary: Changed directly on disk.\n"
        "---\n",
        encoding="utf-8",
    )
    cycle = container_registry.refresh_registry_projections(api.app.state.db)
    assert cycle["refreshed"] == 1
    refreshed = api.get("/api/containers/fleet-one", headers=headers).json()
    assert refreshed["identity_label"] == "Proxima itself, still free text"
    assert refreshed["summary"] == "Changed directly on disk."
    assert refreshed["source_hash"] != source_hash


def test_live_counts_and_activity_follow_committed_database_state(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    _create(api, headers)
    conn = api.app.state.db
    container_id = int(
        conn.execute(
            "SELECT id FROM projects WHERE slug = 'fleet-one'"
        ).fetchone()["id"]
    )
    owner_id = int(conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"])
    running_id = conn.execute(
        """
        INSERT INTO jobs(
          project_id, title, status, created_by, updated_at, started_at
        ) VALUES (?, 'Running', 'running', ?, '2099-01-02T03:04:05+00:00',
                  '2099-01-02T03:04:05+00:00')
        """,
        (container_id, owner_id),
    ).lastrowid
    queued_id = conn.execute(
        """
        INSERT INTO jobs(project_id, title, status, created_by, updated_at)
        VALUES (?, 'Queued', 'queued', ?, '2099-01-01T00:00:00+00:00')
        """,
        (container_id, owner_id),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO attention_items(
          kind, title, target_json, status, source_key
        ) VALUES ('decision', 'Direct', ?, 'open', 'fleet-direct')
        """,
        (json.dumps({"container_id": container_id}),),
    )
    conn.execute(
        """
        INSERT INTO attention_items(
          kind, title, target_json, status, source_key
        ) VALUES ('permission_job', 'Job', ?, 'open', 'fleet-job')
        """,
        (json.dumps({"job_id": running_id}),),
    )

    def no_projection_read(_path):
        raise AssertionError("Fleet reads must not inspect ops/container.md")

    monkeypatch.setattr(container_registry, "_parse_container_doc", no_projection_read)
    detail = api.get("/api/containers/fleet-one", headers=headers).json()
    assert detail["live"] == {
        "running_tasks": 1,
        "queued_tasks": 1,
        "open_attention": 2,
    }
    assert detail["last_activity_at"] == "2099-01-02T03:04:05+00:00"
    assert detail["area_inventory"] == {"total": 1, "code": 0, "ops": 1}
    assert detail["health"]["areas"] == "ready"
    assert detail["health"]["ops_migration"] == "not_required"
    assert detail["health"]["graph_freshness"] is None

    conn.execute(
        "UPDATE jobs SET status = 'done', updated_at = CURRENT_TIMESTAMP "
        "WHERE id IN (?, ?)",
        (running_id, queued_id),
    )
    conn.execute(
        "UPDATE attention_items SET status = 'resolved' "
        "WHERE source_key IN ('fleet-direct', 'fleet-job')"
    )
    changed = api.get("/api/containers/fleet-one", headers=headers).json()
    assert changed["live"] == {
        "running_tasks": 0,
        "queued_tasks": 0,
        "open_attention": 0,
    }


def test_container_detail_and_areas_are_owner_scoped_and_boundary_checked(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    visible = _create(api, headers, "visible", "Visible")
    hidden = _create(api, headers, "hidden", "Hidden")
    conn = api.app.state.db
    other_id = conn.execute(
        """
        INSERT INTO users(
          username, os_user, role, password_hash, password_set_at
        ) VALUES ('other-owner', 'other-owner', 'environment_admin', NULL,
                  CURRENT_TIMESTAMP)
        """
    ).lastrowid
    conn.execute(
        "UPDATE projects SET owner_user_id = ? WHERE slug = 'hidden'",
        (other_id,),
    )

    fleet = api.get("/api/containers", headers=headers).json()["containers"]
    assert [item["slug"] for item in fleet] == ["visible"]
    assert api.get("/api/containers/hidden", headers=headers).status_code == 404
    assert api.get("/api/containers/hidden/areas", headers=headers).status_code == 404

    shutil.rmtree(Path(visible["path"]) / "ops")
    invalid = api.get("/api/containers/visible/areas", headers=headers)
    assert invalid.status_code == 409
    assert "missing" in invalid.json()["detail"].lower()
    assert Path(hidden["path"]).is_dir()


def test_one_thousand_container_fleet_uses_one_sql_statement_and_no_file_scan(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    conn = api.app.state.db
    owner_id = int(conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"])
    containers = [
        (
            f"container-{index:04d}",
            f"Container {index:04d}",
            str(tmp_path / "missing" / f"container-{index:04d}"),
            owner_id,
        )
        for index in range(1000)
    ]
    conn.executemany(
        """
        INSERT INTO projects(slug, name, path, owner_user_id, visibility)
        VALUES (?, ?, ?, ?, 'private')
        """,
        containers,
    )
    conn.execute(
        """
        INSERT INTO project_areas(project_id, kind, rel_path, source)
        SELECT id, 'ops', 'ops', 'auto' FROM projects
        """
    )
    conn.execute(
        """
        INSERT INTO container_registry(
          container_id, identity_label, summary, source_hash, indexed_at,
          last_activity_at
        )
        SELECT
          id, 'Any free-text identity', 'Synthetic Container',
          printf('%064d', id), CURRENT_TIMESTAMP, created_at
        FROM projects
        """
    )

    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    fleet = container_registry.list_fleet_containers(conn, owner_id)
    conn.set_trace_callback(None)
    reads = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert len(fleet) == 1000
    assert len(reads) == 1

    def no_filesystem(*_args, **_kwargs):
        raise AssertionError("Fleet request touched the Container filesystem")

    monkeypatch.setattr(container_registry, "container_root", no_filesystem)
    monkeypatch.setattr(container_registry, "ops_root", no_filesystem)
    monkeypatch.setattr(container_registry, "_parse_container_doc", no_filesystem)
    response = api.get("/api/containers", headers=headers)
    assert response.status_code == 200, response.text
    assert len(response.json()["containers"]) == 1000

"""Relocate/rebind a moved or renamed folder (prune C6, #141).

A project is the folder as it exists on disk (decision #121) - and folders on
disk get moved and renamed. Rebinding re-pins a project record to its folder's
new location through the same onboarding picker: the owner points Proxima at
the new path, identity is confirmed from the docs the folder already has (the
#137 machinery), and the project's history, records, layout map, Ops path, and
memory settings all survive.

The dead-end error paths from audit #120 part 2 item 6 close here: a project
whose folder is missing surfaces an actionable state (find / rebind / unlink)
instead of erroring or hanging, and unlink keeps working with the folder gone.

Rebind is metadata-only: zero writes into either location, read-only
validation, realpath jail respected.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import artifact_registry
from proxima_api.main import create_app


def _api(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    app = create_app(
        {
            "database_path": str(tmp_path / "api.db"),
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


def _root_id(api: TestClient, headers: dict[str, str]) -> str:
    roots = api.get("/api/fs/dirs", headers=headers)
    assert roots.status_code == 200, roots.text
    return roots.json()["root_id"]


def _link(api: TestClient, headers: dict[str, str], root: Path, slug: str, **extra):
    return api.post(
        "/api/projects/link",
        headers=headers,
        json={
            "path": str(root),
            "root_id": _root_id(api, headers),
            "name": slug,
            "slug": slug,
            **extra,
        },
    )


def _rebind(
    api: TestClient,
    headers: dict[str, str],
    slug: str,
    target: Path,
    *,
    confirm: bool = False,
):
    return api.post(
        f"/api/projects/{slug}/rebind",
        headers=headers,
        json={
            "path": str(target),
            "root_id": _root_id(api, headers),
            "confirm": confirm,
        },
    )


def _snapshot(root: Path) -> dict[str, tuple]:
    snapshot: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        snapshot[path.relative_to(root).as_posix()] = (
            info.st_mode,
            info.st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
        )
    return snapshot


def _project_row(api: TestClient, slug: str):
    return api.app.state.db.execute(
        "SELECT id, slug, path, path_identity FROM projects WHERE slug = ?",
        (slug,),
    ).fetchone()


def _location(api: TestClient, headers: dict[str, str], slug: str) -> dict:
    response = api.get(f"/api/projects/{slug}/location", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _listed(api: TestClient, headers: dict[str, str], slug: str) -> dict:
    body = api.get("/api/projects", headers=headers)
    assert body.status_code == 200, body.text
    return next(p for p in body.json()["projects"] if p["slug"] == slug)


def _seed_record(api: TestClient, project_id: int, rel: str) -> None:
    """One deliverable record + its chat lineage, the way a run produces it."""
    conn = api.app.state.db
    session_id = int(
        conn.execute(
            "INSERT INTO sessions(title, project_id, owner_user_id) "
            "VALUES ('chat', ?, 1)",
            (project_id,),
        ).lastrowid
    )
    run_id = int(
        conn.execute(
            "INSERT INTO runs(session_id, project_id, user_id, kind, status, prompt) "
            "VALUES (?, ?, 1, 'chat', 'completed', 'p')",
            (session_id, project_id),
        ).lastrowid
    )
    artifact_registry.record_run_outputs(
        conn,
        run_id,
        session_id,
        project_id,
        [{"type": "doc", "title": Path(rel).name, "path": rel}],
    )


def _restore_in_place(root: Path, staging: Path) -> None:
    """Same content, same path, DIFFERENT directory - what restoring a backup
    over a folder does. Staged through a rename so the original inode stays
    allocated and the restored copy can never reuse it."""
    root.rename(staging)
    shutil.copytree(staging, root)
    shutil.rmtree(staging)


def _fixture(tmp_path: Path, name: str) -> Path:
    """A real-world-shaped folder: identity doc, ops area, root wiki, report."""
    root = tmp_path / name
    (root / "ops" / "reports").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "AGENTS.md").write_text(
        "# Fixture Client\n\nThe fixture client workspace.\n", encoding="utf-8"
    )
    (root / "wiki" / "log.md").write_text("# log\n", encoding="utf-8")
    (root / "ops" / "reports" / "plan.md").write_text("# plan", encoding="utf-8")
    return root


# ── the missing-folder dead end is now an actionable state ───────────────


def test_moved_folder_surfaces_an_actionable_location(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "moves")
    assert _link(api, headers, root, "moves").status_code == 201
    assert _location(api, headers, "moves")["state"] == "bound"

    root.rename(tmp_path / "moved-away")

    location = _location(api, headers, "moves")
    assert location["state"] == "missing"
    assert location["path"] == str(root)
    assert "rebind" in location["actions"] and "unlink" in location["actions"]
    assert location["message"]
    # The stored identity projection is what the owner recognizes the project by.
    assert location["identity"]["label"] == "Fixture Client"


def test_project_list_reports_the_missing_binding_without_failing(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "listed")
    assert _link(api, headers, root, "listed").status_code == 201
    assert _listed(api, headers, "listed")["location"]["state"] == "bound"

    root.rename(tmp_path / "listed-moved")

    listed = _listed(api, headers, "listed")
    assert listed["location"]["state"] == "missing"
    assert listed["location"]["message"]


def test_a_different_folder_at_the_stored_path_reads_as_moved(tmp_path: Path):
    """Restored-from-backup / recreated in place: the path resolves but its
    filesystem identity changed - the old hard dead end (#120 item 12)."""
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "restored")
    assert _link(api, headers, root, "restored").status_code == 201

    _restore_in_place(root, tmp_path / "backup")

    location = _location(api, headers, "restored")
    assert location["state"] == "moved"
    assert "rebind" in location["actions"]


def test_unlink_still_works_when_the_folder_is_missing(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "gone")
    assert _link(api, headers, root, "gone").status_code == 201
    shutil.rmtree(root)

    removed = api.delete("/api/projects/gone", headers=headers)
    assert removed.status_code == 200, removed.text
    assert api.get("/api/projects/gone", headers=headers).status_code == 404


# ── rebind: the project follows its folder ───────────────────────────────


def test_rebind_repins_the_moved_folder_and_history_survives(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "client")
    assert _link(api, headers, root, "client").status_code == 201
    project_id = int(_project_row(api, "client")["id"])
    _seed_record(api, project_id, "ops/reports/plan.md")
    assert api.put(
        "/api/projects/client/memory-writes", headers=headers, json={"enabled": False}
    ).status_code == 200
    layout_before = api.get("/api/projects/client/layout", headers=headers).json()

    moved = tmp_path / "renamed-client"
    root.rename(moved)

    response = _rebind(api, headers, "client", moved)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rebound"] is True
    assert body["path"] == str(moved)
    assert body["previous_path"] == str(root)
    assert body["identity"]["matches"] is True

    # The record row is the same row - id, approval state and lineage untouched.
    assert int(_project_row(api, "client")["id"]) == project_id
    location = _location(api, headers, "client")
    assert location["state"] == "bound"
    assert location["path"] == str(moved)

    # Everything resolves at the new path.
    layout_after = api.get("/api/projects/client/layout", headers=headers).json()
    assert layout_after["ops_path"] == layout_before["ops_path"] == "ops"
    assert layout_after["areas"]["wiki"]["path"] == "wiki"
    assert layout_after["areas"]["wiki"]["source"] == "detected"
    # The per-project memory-writes choice survives the rebind.
    assert layout_after["memory_writes"]["enabled"] is False

    archive = api.get("/api/archive?project=client", headers=headers)
    assert archive.status_code == 200, archive.text
    items = archive.json()["items"]
    assert [item["path"] for item in items] == ["ops/reports/plan.md"]
    assert items[0]["file_missing"] is False

    tree = api.get("/api/projects/client/tree", headers=headers)
    assert tree.status_code == 200, tree.text
    assert any(entry["name"] == "wiki" for entry in tree.json()["entries"])


def test_rebind_is_metadata_only_and_writes_nothing(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "untouched")
    assert _link(api, headers, root, "untouched").status_code == 201

    moved = tmp_path / "untouched-elsewhere"
    root.rename(moved)
    before = _snapshot(moved)

    assert _rebind(api, headers, "untouched", moved).status_code == 200
    assert _snapshot(moved) == before
    # Nothing was recreated at the old location either.
    assert not root.exists()


def test_rebind_to_the_same_path_is_a_noop(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "stable")
    assert _link(api, headers, root, "stable").status_code == 201
    row_before = _project_row(api, "stable")
    before = _snapshot(root)

    response = _rebind(api, headers, "stable", root)
    assert response.status_code == 200, response.text
    assert response.json()["rebound"] is False

    row_after = _project_row(api, "stable")
    assert row_after["path"] == row_before["path"]
    assert row_after["path_identity"] == row_before["path_identity"]
    assert _snapshot(root) == before


def test_rebind_repins_a_folder_restored_at_the_same_path(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "backup-restore")
    assert _link(api, headers, root, "backup-restore").status_code == 201
    identity_before = _project_row(api, "backup-restore")["path_identity"]

    _restore_in_place(root, tmp_path / "safe-copy")
    assert _location(api, headers, "backup-restore")["state"] == "moved"

    response = _rebind(api, headers, "backup-restore", root)
    assert response.status_code == 200, response.text
    assert response.json()["rebound"] is True
    assert _project_row(api, "backup-restore")["path_identity"] != identity_before
    assert _location(api, headers, "backup-restore")["state"] == "bound"


def test_same_path_rebind_repairs_a_deleted_ops_folder(tmp_path: Path):
    """Deleting just the Ops folder broke every file operation with no way
    back (audit #120 part 2, item 6). Re-pinning in place repairs it."""
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "opsless")
    assert _link(api, headers, root, "opsless").status_code == 201
    shutil.rmtree(root / "ops")
    assert api.get("/api/projects/opsless/tree", headers=headers).status_code != 200

    response = _rebind(api, headers, "opsless", root, confirm=True)
    assert response.status_code == 200, response.text
    assert response.json()["rebound"] is True
    assert response.json()["repaired"]["ops_path"] == "."
    assert api.get("/api/projects/opsless/tree", headers=headers).status_code == 200


# ── identity confirmation (the #137 machinery) ───────────────────────────


def test_rebind_to_a_wrong_folder_warns_and_is_refused_without_override(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "right")
    assert _link(api, headers, root, "right").status_code == 201
    other = tmp_path / "someone-elses"
    (other / "ops").mkdir(parents=True)
    (other / "AGENTS.md").write_text("# Other Project\n", encoding="utf-8")
    root.rename(tmp_path / "right-moved")

    refused = _rebind(api, headers, "right", other)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["identity"]["matches"] is False
    assert detail["identity"]["stored"]["label"] == "Fixture Client"
    assert detail["identity"]["found"]["label"] == "Other Project"
    # Single-owner product: the warning is overridable, and says so.
    assert detail["confirmable"] is True
    # Refusal changes nothing.
    assert _project_row(api, "right")["path"] == str(root)

    confirmed = _rebind(api, headers, "right", other, confirm=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["identity"]["matches"] is False
    assert _project_row(api, "right")["path"] == str(other)


def test_confirmed_rebind_rebases_the_ops_path_and_layout_map(tmp_path: Path):
    """An override lands on a folder shaped differently: the persisted Ops
    path and every layout entry whose folder is gone re-detect in place."""
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "shaped")  # ops/ + root wiki/
    assert _link(api, headers, root, "shaped").status_code == 201
    before = api.get("/api/projects/shaped/layout", headers=headers).json()
    assert before["ops_path"] == "ops"
    assert before["areas"]["wiki"]["path"] == "wiki"

    flat = tmp_path / "flat-shape"
    (flat / "wiki").mkdir(parents=True)  # no ops/ here: Ops must fall back to '.'
    (flat / "AGENTS.md").write_text("# Flat\n", encoding="utf-8")
    root.rename(tmp_path / "shaped-moved")

    response = _rebind(api, headers, "shaped", flat, confirm=True)
    assert response.status_code == 200, response.text
    assert response.json()["repaired"]["ops_path"] == "."

    after = api.get("/api/projects/shaped/layout", headers=headers).json()
    assert after["ops_path"] == "."
    assert after["areas"]["wiki"]["path"] == "wiki"
    assert after["areas"]["wiki"]["exists"] is True
    # An area with nothing on disk falls back to today's default name.
    assert after["areas"]["artifacts"]["path"] == "artifacts"


# ── fail-closed edges ────────────────────────────────────────────────────


def test_rebind_refuses_a_folder_already_linked_to_another_project(tmp_path: Path):
    api, headers = _api(tmp_path)
    first = _fixture(tmp_path, "first")
    second = _fixture(tmp_path, "second")
    assert _link(api, headers, first, "first").status_code == 201
    assert _link(api, headers, second, "second").status_code == 201
    first.rename(tmp_path / "first-moved")

    refused = _rebind(api, headers, "first", second, confirm=True)
    assert refused.status_code == 409, refused.text
    assert _project_row(api, "first")["path"] == str(first)


def test_rebind_refuses_a_target_outside_the_link_roots(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "jailed")
    assert _link(api, headers, root, "jailed").status_code == 201
    root.rename(tmp_path / "jailed-moved")

    refused = _rebind(api, headers, "jailed", Path("/etc"), confirm=True)
    assert refused.status_code in (400, 403), refused.text
    assert _project_row(api, "jailed")["path"] == str(root)


def test_rebind_refuses_a_missing_or_non_directory_target(tmp_path: Path):
    api, headers = _api(tmp_path)
    root = _fixture(tmp_path, "picky")
    assert _link(api, headers, root, "picky").status_code == 201
    moved = tmp_path / "picky-moved"
    root.rename(moved)
    (tmp_path / "not-a-dir.txt").write_text("x", encoding="utf-8")

    missing = _rebind(api, headers, "picky", tmp_path / "nowhere", confirm=True)
    assert missing.status_code in (400, 403), missing.text
    a_file = _rebind(api, headers, "picky", tmp_path / "not-a-dir.txt", confirm=True)
    assert a_file.status_code in (400, 403), a_file.text
    assert _project_row(api, "picky")["path"] == str(root)

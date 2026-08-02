"""Symlink policy softened for READS only (prune C7, #142).

A symlink met on a read path is warn-and-skip: the entry is acknowledged in
the listing as a skipped symlink, its siblings keep working, and nothing
errors the whole view. Nothing is ever followed - reading *through* a symlink
stays refused, writes stay refused, and migration stays fail-closed. The
realpath jail therefore cannot move: no read can reach content outside the
linked folder, because no read follows a link at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import fsapi
from proxima_api.main import create_app


def _api(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    app = create_app(
        {
            "database_path": str(tmp_path / "api.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "start_worker": False,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def _link(api: TestClient, headers: dict[str, str], root: Path, slug: str, **extra):
    roots = api.get("/api/fs/dirs", headers=headers)
    assert roots.status_code == 200, roots.text
    return api.post(
        "/api/projects/link",
        headers=headers,
        json={
            "path": str(root),
            "root_id": roots.json()["root_id"],
            "name": slug,
            "slug": slug,
            **extra,
        },
    )


def _fixture(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path, Path]:
    """A linked project holding one benign symlink and one escaping symlink."""
    root = tmp_path / "linked"
    (root / "ops").mkdir(parents=True)
    (root / "ops" / "brief.md").write_text("# Brief\n", encoding="utf-8")
    (root / "real.md").write_text("# Real\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "nested.md").write_text("# Nested\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# Secret\n", encoding="utf-8")
    # benign: points back inside the linked folder
    (root / "alias.md").symlink_to(root / "ops" / "brief.md")
    # escaping: points at content outside the jail
    (root / "escape.md").symlink_to(outside / "secret.md")
    (root / "escape-dir").symlink_to(outside, target_is_directory=True)
    (root / "etc-link").symlink_to(Path("/etc"), target_is_directory=True)
    # and one inside the detected Ops root, where the deep scan used to live
    (root / "ops" / "shared").symlink_to(outside, target_is_directory=True)

    api, headers = _api(tmp_path)
    response = _link(api, headers, root, "linked", ops_path="ops")
    assert response.status_code == 201, response.text
    return api, headers, root, outside


def _entries(api: TestClient, headers: dict[str, str], path: str = "") -> dict[str, dict]:
    response = api.get(
        "/api/projects/linked/tree",
        headers=headers,
        params={"path": path},
    )
    assert response.status_code == 200, response.text
    return {entry["name"]: entry for entry in response.json()["entries"]}


def _target_params(target: dict) -> dict[str, str]:
    return {"target": json.dumps(target, separators=(",", ":"))}


# --- reads: warn and skip, never fail the whole view ------------------------


def test_tree_listing_keeps_siblings_and_marks_skipped_symlinks(tmp_path: Path):
    api, headers, _root, _outside = _fixture(tmp_path)

    entries = _entries(api, headers)

    # siblings are untouched by the stray links
    assert entries["real.md"]["type"] == "file"
    assert entries["sub"]["type"] == "dir"
    assert "target" in entries["real.md"]
    # every symlink is acknowledged, marked skipped, and carries a reason
    for name in ("alias.md", "escape.md", "escape-dir", "etc-link"):
        entry = entries[name]
        assert entry["type"] == "symlink", entry
        assert entry["skipped"] is True, entry
        assert "symlink" in entry["reason"].lower(), entry
        # a skipped entry is not openable, so it carries no file target
        assert "target" not in entry, entry
    # nested listings still work through the same root
    assert _entries(api, headers, "sub")["nested.md"]["type"] == "file"


def test_container_side_listing_also_skips_symlinks(tmp_path: Path):
    api, headers, _root, _outside = _fixture(tmp_path)

    response = api.get(
        "/api/projects/linked/tree",
        headers=headers,
        params={"path": "", "root_side": "container"},
    )

    assert response.status_code == 200, response.text
    entries = {entry["name"]: entry for entry in response.json()["entries"]}
    assert entries["real.md"]["type"] == "file"
    assert entries["etc-link"] == {
        "name": "etc-link",
        "type": "symlink",
        "size": 0,
        "skipped": True,
        "reason": fsapi.SYMLINK_SKIP_REASON,
    }


def test_reading_a_symlink_is_refused_with_a_clear_reason(tmp_path: Path):
    api, headers, _root, _outside = _fixture(tmp_path)

    for name in ("alias.md", "escape.md"):
        response = api.get(
            "/api/projects/linked/file",
            headers=headers,
            params={"path": name},
        )
        assert response.status_code == 400, response.text
        assert "symlink" in response.json()["detail"].lower(), response.text
        assert "Secret" not in response.text
        assert "Brief" not in response.text


def test_no_read_crosses_the_jail_through_a_symlink(tmp_path: Path):
    api, headers, root, outside = _fixture(tmp_path)

    # through a symlinked directory
    for params in (
        {"path": "escape-dir/secret.md"},
        {"path": "etc-link/hostname"},
        {"path": "escape-dir"},
    ):
        response = api.get("/api/projects/linked/file", headers=headers, params=params)
        assert response.status_code == 400, response.text
        assert "Secret" not in response.text

    listing = api.get(
        "/api/projects/linked/tree",
        headers=headers,
        params={"path": "escape-dir"},
    )
    assert listing.status_code == 400, listing.text

    # and at the primitive itself, for both the link and anything beneath it
    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.resolve_in_project(root, "escape-dir/secret.md")
    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.resolve_in_project(root, "etc-link/hostname")
    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.read_file(root, "escape.md")
    assert (outside / "secret.md").read_text(encoding="utf-8") == "# Secret\n"


def test_reference_index_and_bulk_read_skip_symlinks(tmp_path: Path):
    api, headers, root, _outside = _fixture(tmp_path)

    response = api.get("/api/projects/linked/reference-files", headers=headers)

    assert response.status_code == 200, response.text
    paths = {item["path"] for item in response.json()["files"]}
    assert "real.md" in paths
    assert "sub/nested.md" in paths
    assert not any(
        path.startswith(("alias.md", "escape", "etc-link")) for path in paths
    )
    assert [item["path"] for item in fsapi.walk_files(root, "")] == [
        "ops/brief.md",
        "real.md",
        "sub/nested.md",
    ]


def test_ops_reads_survive_a_symlink_inside_the_ops_root(tmp_path: Path):
    """The deep descendant scan is gone from every read path: a stray link
    under `ops/` no longer refuses graph scope, Home, or the Ops listing."""
    api, headers, _root, outside = _fixture(tmp_path)

    ops_entries = _entries(api, headers, "ops")
    assert ops_entries["brief.md"]["type"] == "file"
    assert ops_entries["shared"]["skipped"] is True

    scope = api.app.state.graph_context.resolve_scope(
        owner_user_id=1,
        container_slug="linked",
        kind="knowledge",
        area_id=None,
        create_output=False,
    )
    assert scope.root.name == "ops"
    assert outside.resolve() not in scope.excluded_roots
    assert api.get("/api/projects", headers=headers).status_code == 200


# --- writes: unchanged, still fail-closed -----------------------------------


def test_writes_through_a_symlink_stay_refused(tmp_path: Path):
    api, headers, root, outside = _fixture(tmp_path)

    for name in ("alias.md", "escape.md", "escape-dir/secret.md"):
        response = api.put(
            "/api/projects/linked/file",
            headers=headers,
            params={"path": name},
            json={"content": "overwritten"},
        )
        assert response.status_code == 400, response.text

    assert (outside / "secret.md").read_text(encoding="utf-8") == "# Secret\n"
    assert (root / "ops" / "brief.md").read_text(encoding="utf-8") == "# Brief\n"

    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.write_file(root, "alias.md", "overwritten")
    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.mkdir(root, "escape-dir/new")
    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.delete(root, "escape-dir/secret.md")
    with pytest.raises(fsapi.FsError, match="symlink"):
        fsapi.rename(root, "real.md", "escape-dir/real.md")
    assert (outside / "secret.md").exists()
    assert (root / "real.md").exists()


# --- link and migration -----------------------------------------------------


def test_link_tolerates_a_symlink_inside_the_chosen_ops_folder(tmp_path: Path):
    """The #131 blocker: one stray link no longer refuses the whole link."""
    root = tmp_path / "adopt"
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "note.md").write_text("# Note\n", encoding="utf-8")
    outside = tmp_path / "adopt-outside"
    outside.mkdir()
    (root / "ops" / "shared").symlink_to(outside, target_is_directory=True)

    api, headers = _api(tmp_path)
    response = _link(api, headers, root, "adopt", ops_path="ops")

    assert response.status_code == 201, response.text
    entries = {
        entry["name"]: entry
        for entry in api.get(
            "/api/projects/adopt/tree",
            headers=headers,
            params={"path": "ops"},
        ).json()["entries"]
    }
    assert entries["wiki"]["type"] == "dir"
    assert entries["shared"]["skipped"] is True


def test_a_symlinked_ops_folder_itself_stays_refused(tmp_path: Path):
    root = tmp_path / "symlinked-ops"
    (root / "real").mkdir(parents=True)
    (root / "linked").symlink_to(root / "real", target_is_directory=True)

    api, headers = _api(tmp_path)
    response = _link(api, headers, root, "symlinked-ops", ops_path="linked")

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["field"] == "ops_path"


def test_migration_with_symlinked_content_stays_refused(tmp_path: Path):
    """Content moves keep the full recursive fail-closed scan."""
    root = tmp_path / "migrate"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "note.md").write_text("# Note\n", encoding="utf-8")
    outside = tmp_path / "migrate-outside"
    outside.mkdir()
    (outside / "shared.md").write_text("# Shared\n", encoding="utf-8")
    (root / "wiki" / "shared.md").symlink_to(outside / "shared.md")

    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "migrate").status_code == 201

    inspection = api.post("/api/projects/migrate/ops-migration/validate", headers=headers)
    assert inspection.status_code == 200, inspection.text
    body = inspection.json()
    assert body["retry_safe"] is False, body
    assert "symlink" in json.dumps(body).lower(), body

    retry = api.post("/api/projects/migrate/ops-migration/retry", headers=headers)
    assert retry.status_code == 409, retry.text
    # nothing moved
    assert (root / "wiki" / "note.md").exists()
    assert not (root / "ops").exists()

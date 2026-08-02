"""Per-project Ops path, picked at link time (prune C3, #135).

The Ops root is a per-project value persisted on the ops Area row: the link
flow offers the detected default (an existing ``ops/`` folder, else the
project root ``.``), the owner may override it at link time, and every Ops
feature resolves through the persisted path - no global assumption that Ops
lives at ``ops/``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import container_registry
from proxima_api.container_registry import (
    migrate_legacy_ops_containers,
    ops_root,
)
from proxima_api.db import connect, init_db
from proxima_api.directory_handles import directory_identity_for_path
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


def _strict_tree_snapshot(root: Path) -> dict[str, tuple]:
    snapshot: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        snapshot[path.relative_to(root).as_posix()] = (
            info.st_mode,
            info.st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
        )
    return snapshot


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


def _ops_row(conn, slug: str):
    return conn.execute(
        """
        SELECT pa.rel_path, pa.source FROM project_areas pa
        JOIN projects p ON p.id = pa.project_id
        WHERE p.slug = ? AND pa.kind = 'ops' AND pa.source != 'excluded'
        """,
        (slug,),
    ).fetchone()


def test_link_with_detected_ops_folder_defaults_to_ops(tmp_path: Path):
    """An existing ops/ folder is the detected default: the persisted
    per-project path becomes 'ops' without the owner naming it."""
    root = tmp_path / "with-ops"
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "note.md").write_bytes(b"# note\n")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "with-ops")
    assert linked.status_code == 201, linked.text

    row = _ops_row(api.app.state.db, "with-ops")
    assert row["rel_path"] == "ops"
    # The detected default is recorded as detection, not an owner override.
    assert row["source"] == "auto"
    assert _strict_tree_snapshot(root) == before
    areas = api.get("/api/projects/with-ops/areas", headers=headers)
    assert areas.status_code == 200, areas.text
    assert areas.json()["ops_area"]["rel_path"] == "ops"


def test_link_with_empty_existing_ops_folder_still_defaults_to_ops(tmp_path: Path):
    """The detected default follows the folder that exists on disk even when
    it is empty - and linking writes nothing into it."""
    root = tmp_path / "empty-ops"
    (root / "ops").mkdir(parents=True)
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "empty-ops")
    assert linked.status_code == 201, linked.text

    row = _ops_row(api.app.state.db, "empty-ops")
    assert row["rel_path"] == "ops"
    assert _strict_tree_snapshot(root) == before
    open_items = api.app.state.db.execute(
        "SELECT COUNT(*) FROM attention_items WHERE status = 'open'"
    ).fetchone()[0]
    assert open_items == 0


def test_link_without_ops_folder_defaults_to_project_root(tmp_path: Path):
    root = tmp_path / "rootless"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "note.md").write_bytes(b"# note\n")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "rootless")
    assert linked.status_code == 201, linked.text

    row = _ops_row(api.app.state.db, "rootless")
    assert row["rel_path"] == "."
    assert row["source"] == "auto"
    assert _strict_tree_snapshot(root) == before


def test_link_explicit_ops_path_override_persists(tmp_path: Path):
    """The owner can point Ops at any real folder; the choice is persisted
    per project and survives the startup settle sweep."""
    root = tmp_path / "custom"
    (root / "ops").mkdir(parents=True)  # detected default that loses
    (root / "runbook" / "wiki").mkdir(parents=True)
    (root / "runbook" / "wiki" / "note.md").write_bytes(b"# runbook note\n")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "custom", ops_path="runbook")
    assert linked.status_code == 201, linked.text

    conn = api.app.state.db
    row = _ops_row(conn, "custom")
    assert row["rel_path"] == "runbook"
    assert row["source"] == "manual"
    assert _strict_tree_snapshot(root) == before
    container = conn.execute(
        "SELECT * FROM projects WHERE slug = 'custom'"
    ).fetchone()
    assert ops_root(conn, container) == (root / "runbook").resolve()

    # The sweep validates the persisted choice; it never rewrites it.
    assert migrate_legacy_ops_containers(conn)["attention"] == 0
    row = _ops_row(conn, "custom")
    assert row["rel_path"] == "runbook"
    assert _strict_tree_snapshot(root) == before


def test_link_explicit_root_override_wins_over_detected_ops(tmp_path: Path):
    """Choosing '.' over a populated ops/ persists: the sweep must not adopt
    the ops/ folder away from the owner's explicit choice."""
    root = tmp_path / "root-choice"
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "note.md").write_bytes(b"# note\n")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "root-choice", ops_path=".")
    assert linked.status_code == 201, linked.text

    conn = api.app.state.db
    row = _ops_row(conn, "root-choice")
    assert row["rel_path"] == "."
    assert row["source"] == "manual"

    assert migrate_legacy_ops_containers(conn)["attention"] == 0
    row = _ops_row(conn, "root-choice")
    assert row["rel_path"] == "."
    assert _strict_tree_snapshot(root) == before


def test_link_rejects_unusable_ops_path(tmp_path: Path):
    api, headers = _api(tmp_path)

    missing = tmp_path / "missing-ops-dir"
    missing.mkdir()
    response = _link(api, headers, missing, "missing-ops-dir", ops_path="nope")
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["field"] == "ops_path"

    file_target = tmp_path / "file-ops"
    file_target.mkdir()
    (file_target / "notes.md").write_bytes(b"# not a folder\n")
    response = _link(api, headers, file_target, "file-ops", ops_path="notes.md")
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["field"] == "ops_path"

    escape = tmp_path / "escape-ops"
    escape.mkdir()
    response = _link(api, headers, escape, "escape-ops", ops_path="../outside")
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["field"] == "ops_path"

    symlinked = tmp_path / "symlink-ops"
    (symlinked / "real").mkdir(parents=True)
    (symlinked / "linked").symlink_to(symlinked / "real")
    response = _link(api, headers, symlinked, "symlink-ops", ops_path="linked")
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["field"] == "ops_path"

    # mkdir creates an empty folder: only the root itself can be its Ops path.
    response = api.post(
        "/api/projects/link",
        headers=headers,
        json={
            "path": str(tmp_path / "brand-new"),
            "root_id": api.get("/api/fs/dirs", headers=headers).json()["root_id"],
            "name": "brand-new",
            "slug": "brand-new",
            "mkdir": True,
            "ops_path": "runbook",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["field"] == "ops_path"

    # None of the failed links left a project behind.
    assert (
        api.app.state.db.execute(
            "SELECT COUNT(*) FROM projects WHERE slug IN "
            "('missing-ops-dir', 'file-ops', 'escape-ops', 'symlink-ops', 'brand-new')"
        ).fetchone()[0]
        == 0
    )


def test_ops_features_resolve_through_the_persisted_path(tmp_path: Path):
    """File features route through the per-project Ops path, not 'ops/'."""
    root = tmp_path / "resolved"
    (root / "runbook" / "wiki").mkdir(parents=True)
    (root / "runbook" / "wiki" / "existing.md").write_bytes(b"# existing\n")
    (root / "README.md").write_bytes(b"# readme\n")
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "resolved", ops_path="runbook")
    assert linked.status_code == 201, linked.text

    # A virtual Ops write lands inside the chosen folder.
    written = api.put(
        "/api/projects/resolved/file",
        headers=headers,
        params={"path": "wiki/note.md"},
        json={"content": "# routed\n"},
    )
    assert written.status_code == 200, written.text
    assert (root / "runbook" / "wiki" / "note.md").read_text(
        encoding="utf-8"
    ) == "# routed\n"
    assert not (root / "wiki").exists()
    assert not (root / "ops").exists()

    # An explicit Ops-folder path resolves into the same Area.
    read = api.get(
        "/api/projects/resolved/file",
        headers=headers,
        params={"path": "runbook/wiki/existing.md"},
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# existing\n"

    # The virtual root listing overlays the chosen Ops folder, without
    # duplicating it as a plain directory entry.
    listing = api.get(
        "/api/projects/resolved/tree",
        headers=headers,
        params={"path": ""},
    )
    assert listing.status_code == 200, listing.text
    names = {entry["name"] for entry in listing.json()["entries"]}
    assert "wiki" in names
    assert "README.md" in names
    assert "runbook" not in names

    # Reference autocomplete merges through the same per-project path.
    references = api.get(
        "/api/projects/resolved/reference-files",
        headers=headers,
    )
    assert references.status_code == 200, references.text
    paths = {item["path"] for item in references.json()["files"]}
    assert "wiki/note.md" in paths
    assert "README.md" in paths
    assert not any(path.startswith("runbook/") for path in paths)

    # The migration surface reports the persisted path as settled.
    detail = api.get("/api/projects/resolved/ops-migration", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["active_ops_path"] == "runbook"
    assert body["physical_ops"]["path"] == "runbook"
    assert body["retry_safe"] is False
    assert body["attention"]["status"] == "none"
    assert body["what_remains_usable"]["physical_ops_active"] is True


def test_custom_ops_path_row_settles_without_attention(tmp_path: Path):
    """A persisted non-'ops' Ops path is a first-class layout for the settle
    sweep - never 'unsupported'."""
    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / "first-class"
    (root / "runbook").mkdir(parents=True)
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, path_identity, owner_user_id) "
        "VALUES ('first-class', 'First class', ?, ?, ?)",
        (str(root), directory_identity_for_path(root), user_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', 'runbook', 'manual')",
        (container_id,),
    )

    assert migrate_legacy_ops_containers(conn) == {"complete": 1, "attention": 0}
    assert (
        conn.execute(
            "SELECT rel_path FROM project_areas WHERE project_id = ? AND kind = 'ops'",
            (container_id,),
        ).fetchone()["rel_path"]
        == "runbook"
    )
    open_items = conn.execute(
        "SELECT COUNT(*) FROM attention_items WHERE status = 'open'"
    ).fetchone()[0]
    assert open_items == 0
    body = container_registry.inspect_ops_migration(conn, container_id)
    assert body["active_ops_path"] == "runbook"
    assert body["what_remains_usable"]["physical_ops_active"] is True


def test_existing_linked_projects_keep_their_resolved_path(tmp_path: Path):
    """Legacy rows are already the persisted per-project value: an adopted
    'ops' project and a root '.' project resolve exactly as before."""
    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES ('owner', 'owner')"
    ).lastrowid

    adopted = tmp_path / "adopted-shape"
    (adopted / "ops" / "wiki").mkdir(parents=True)
    (adopted / "ops" / "wiki" / "note.md").write_bytes(b"# adopted\n")
    adopted_id = conn.execute(
        "INSERT INTO projects(slug, name, path, path_identity, owner_user_id) "
        "VALUES ('adopted-shape', 'Adopted', ?, ?, ?)",
        (str(adopted), directory_identity_for_path(adopted), user_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', 'ops', 'auto')",
        (adopted_id,),
    )

    legacy = tmp_path / "root-shape"
    (legacy / "wiki").mkdir(parents=True)
    (legacy / "wiki" / "note.md").write_bytes(b"# legacy\n")
    legacy_id = conn.execute(
        "INSERT INTO projects(slug, name, path, path_identity, owner_user_id) "
        "VALUES ('root-shape', 'Root', ?, ?, ?)",
        (str(legacy), directory_identity_for_path(legacy), user_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', '.', 'auto')",
        (legacy_id,),
    )
    before_adopted = _strict_tree_snapshot(adopted)
    before_legacy = _strict_tree_snapshot(legacy)

    assert migrate_legacy_ops_containers(conn) == {"complete": 2, "attention": 0}

    assert ops_root(conn, container_registry.get_container(conn, adopted_id)) == (
        adopted / "ops"
    ).resolve()
    assert ops_root(conn, container_registry.get_container(conn, legacy_id)) == (
        legacy
    ).resolve()
    assert _strict_tree_snapshot(adopted) == before_adopted
    assert _strict_tree_snapshot(legacy) == before_legacy
    # BIP-shape projects keep the explicit, previewed migration available.
    body = container_registry.inspect_ops_migration(conn, legacy_id)
    assert body["retry_action"] == "migrate"


def test_code_area_detection_skips_the_custom_ops_tree(tmp_path: Path):
    """A git repo inside the chosen Ops folder is Ops content, not a code
    area - exactly like a repo inside a classic ops/."""
    root = tmp_path / "repo-inside"
    (root / "runbook" / ".git").mkdir(parents=True)
    (root / "runbook" / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (root / "site" / ".git").mkdir(parents=True)
    (root / "site" / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "repo-inside", ops_path="runbook")
    assert linked.status_code == 201, linked.text

    areas = api.get("/api/projects/repo-inside/areas", headers=headers)
    assert areas.status_code == 200, areas.text
    body = areas.json()
    assert [area["rel_path"] for area in body["code_areas"]] == ["site"]
    assert body["ops_area"]["rel_path"] == "runbook"

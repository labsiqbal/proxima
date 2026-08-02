"""Reserved-name virtual rerouting is gone (prune Stage 4 closer, #138).

Paths mean exactly what they say on disk (decision #121): names like wiki,
scripts, tasks, artifacts, and uploads no longer shadow real folders anywhere -
Files browsing, the file APIs, uploads, previews, and turn restore all resolve
a path literally against the container root, with Area identity assigned by
physical ownership. Legacy rows written under the reroute era are migrated
once (idempotently) so their historical meaning is frozen, and the moodboard
store performs the same upgrade at its read boundary.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import layout_map, migrations, moodboard
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


def _ops_project(tmp_path: Path, name: str) -> Path:
    """A wingoh-shaped project: real ops/ with wiki+artifacts, repo files at root."""
    root = tmp_path / name
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "ops-note.md").write_text("# ops note\n", encoding="utf-8")
    (root / "ops" / "artifacts").mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "README.md").write_text("# readme\n", encoding="utf-8")
    return root


# ── Files browsing + file APIs: real folders only ─────────────────────────


def test_real_root_wiki_is_browsable_as_itself(tmp_path: Path):
    """A real folder named wiki/ at the container root is just that folder,
    even while ops/wiki also exists - no shadowing, in either direction."""
    root = _ops_project(tmp_path, "real-wiki")
    (root / "wiki").mkdir()
    (root / "wiki" / "real.md").write_text("# real root wiki\n", encoding="utf-8")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "real-wiki").status_code == 201

    listing = api.get(
        "/api/projects/real-wiki/tree", headers=headers, params={"path": "wiki"}
    )
    assert listing.status_code == 200, listing.text
    names = {entry["name"] for entry in listing.json()["entries"]}
    assert names == {"real.md"}

    read = api.get(
        "/api/projects/real-wiki/file",
        headers=headers,
        params={"path": "wiki/real.md"},
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# real root wiki\n"
    assert read.json()["target"]["area"]["kind"] == "container"

    ops_read = api.get(
        "/api/projects/real-wiki/file",
        headers=headers,
        params={"path": "ops/wiki/ops-note.md"},
    )
    assert ops_read.status_code == 200, ops_read.text
    assert ops_read.json()["content"] == "# ops note\n"
    assert ops_read.json()["target"]["area"]["kind"] == "ops"

    written = api.put(
        "/api/projects/real-wiki/file",
        headers=headers,
        params={"path": "wiki/new.md"},
        json={"content": "# root\n"},
    )
    assert written.status_code == 200, written.text
    assert (root / "wiki" / "new.md").read_text(encoding="utf-8") == "# root\n"
    assert not (root / "ops" / "wiki" / "new.md").exists()


def test_root_listing_is_the_real_folder(tmp_path: Path):
    """The Files root listing shows the real container root - the Ops folder is
    a normal entry, and ops content is not overlaid over root names."""
    root = _ops_project(tmp_path, "real-root")
    (root / "ops" / "tasks").mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "real-root").status_code == 201

    listing = api.get(
        "/api/projects/real-root/tree", headers=headers, params={"path": ""}
    )
    assert listing.status_code == 200, listing.text
    names = {entry["name"] for entry in listing.json()["entries"]}
    assert names == {"ops", "src", "README.md"}

    ops_listing = api.get(
        "/api/projects/real-root/tree", headers=headers, params={"path": "ops"}
    )
    assert ops_listing.status_code == 200, ops_listing.text
    ops_names = {entry["name"] for entry in ops_listing.json()["entries"]}
    assert ops_names == {"wiki", "artifacts", "tasks"}


def test_reserved_write_lands_at_the_literal_path(tmp_path: Path):
    """Writing tasks/todo.md means the container root tasks/ folder - Proxima
    no longer invents an ops/tasks tree for a reserved first segment."""
    root = _ops_project(tmp_path, "literal-write")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "literal-write").status_code == 201

    written = api.put(
        "/api/projects/literal-write/file",
        headers=headers,
        params={"path": "tasks/todo.md"},
        json={"content": "- [ ] be literal\n"},
    )
    assert written.status_code == 200, written.text
    assert (root / "tasks" / "todo.md").is_file()
    assert not (root / "ops" / "tasks").exists()


def test_ops_at_dot_project_keeps_root_paths(tmp_path: Path):
    """A BIP-shaped project (ops at `.`) resolves wiki/... exactly as before:
    the root wiki IS the ops wiki."""
    root = tmp_path / "dot-ops"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "log.md").write_text("# log\n", encoding="utf-8")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "dot-ops").status_code == 201

    read = api.get(
        "/api/projects/dot-ops/file", headers=headers, params={"path": "wiki/log.md"}
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# log\n"
    assert read.json()["target"]["area"]["kind"] == "ops"

    listing = api.get(
        "/api/projects/dot-ops/tree", headers=headers, params={"path": ""}
    )
    assert listing.status_code == 200
    assert {e["name"] for e in listing.json()["entries"]} == {"wiki"}


def test_reference_files_use_real_container_paths(tmp_path: Path):
    """@-reference autocomplete lists real container-relative paths: ops files
    keep their ops/ prefix, root files their own names."""
    root = _ops_project(tmp_path, "real-refs")
    (root / "wiki").mkdir()
    (root / "wiki" / "root-note.md").write_text("root\n", encoding="utf-8")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "real-refs").status_code == 201

    references = api.get("/api/projects/real-refs/reference-files", headers=headers)
    assert references.status_code == 200, references.text
    paths = {item["path"] for item in references.json()["files"]}
    assert "ops/wiki/ops-note.md" in paths
    assert "wiki/root-note.md" in paths
    assert "README.md" in paths
    assert "wiki/ops-note.md" not in paths


# ── Uploads: layout-map default, literal explicit dir ─────────────────────


def test_upload_default_returns_container_relative_path(tmp_path: Path):
    root = _ops_project(tmp_path, "upload-default")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "upload-default").status_code == 201

    response = api.post(
        "/api/projects/upload-default/upload",
        headers=headers,
        files={"file": ("shot.png", b"png-bytes", "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "ops/uploads/shot.png"
    assert (root / "ops" / "uploads" / "shot.png").read_bytes() == b"png-bytes"


def test_upload_default_follows_detected_root_uploads(tmp_path: Path):
    root = _ops_project(tmp_path, "upload-detected")
    (root / "uploads").mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "upload-detected").status_code == 201

    response = api.post(
        "/api/projects/upload-detected/upload",
        headers=headers,
        files={"file": ("shot.png", b"png-bytes", "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "uploads/shot.png"
    assert (root / "uploads" / "shot.png").read_bytes() == b"png-bytes"


def test_upload_explicit_dir_is_container_relative(tmp_path: Path):
    """An explicit dir is a literal container-relative folder - a reserved
    first segment no longer reroutes the upload into the Ops Area."""
    root = _ops_project(tmp_path, "upload-explicit")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "upload-explicit").status_code == 201

    response = api.post(
        "/api/projects/upload-explicit/upload",
        headers=headers,
        params={"dir": "artifacts/refs"},
        files={"file": ("ref.png", b"ref-bytes", "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "artifacts/refs/ref.png"
    assert (root / "artifacts" / "refs" / "ref.png").read_bytes() == b"ref-bytes"
    assert not (root / "ops" / "artifacts" / "refs").exists()


# ── Turn restore: literal container semantics ─────────────────────────────


def test_turn_restore_targets_the_literal_path(tmp_path: Path):
    """A journal entry wiki/x.md restores the real root wiki file - restore no
    longer reroutes reserved names into the Ops Area."""
    root = _ops_project(tmp_path, "restore-literal")
    (root / "wiki").mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "restore-literal").status_code == 201
    app = api.app

    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Journal", "project_slug": "restore-literal"},
    ).json()
    message_id = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) "
        "VALUES (?, 'assistant', 'changed')",
        (session["id"],),
    ).lastrowid
    entries = [
        {
            "path": "wiki/turn.md",
            "before_hash": None,
            "before_content_b64": base64.b64encode(b"restored").decode("ascii"),
            "after_hash": "unused",
        }
    ]
    app.state.db.execute(
        "INSERT INTO turn_file_journals("
        "message_id, session_id, entries_json, root_semantics"
        ") VALUES (?, ?, ?, 'container-virtual-v2')",
        (message_id, session["id"], json.dumps(entries)),
    )

    restored = api.post(
        f"/api/chat/messages/{message_id}/restore-turn",
        headers=headers,
        json={"confirm": True},
    )
    assert restored.status_code == 200, restored.text
    assert (root / "wiki" / "turn.md").read_text(encoding="utf-8") == "restored"
    assert not (root / "ops" / "wiki" / "turn.md").exists()


# ── Legacy migration: reroute-era rows are frozen once, idempotently ──────


def test_migration_freezes_reroute_era_journal_paths(tmp_path: Path):
    """v60 rewrites reserved-name journal entries for non-dot Ops projects to
    their historical (ops-prefixed) meaning; a dot-Ops project is untouched.
    Running the migration twice changes nothing further."""
    root = _ops_project(tmp_path, "journal-legacy")
    dot_root = tmp_path / "journal-dot"
    (dot_root / "wiki").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "journal-legacy").status_code == 201
    assert _link(api, headers, dot_root, "journal-dot").status_code == 201
    app = api.app

    def seed(slug: str) -> int:
        session = api.post(
            "/api/sessions",
            headers=headers,
            json={"title": "Legacy", "project_slug": slug},
        ).json()
        message_id = app.state.db.execute(
            "INSERT INTO messages(session_id, role, content) "
            "VALUES (?, 'assistant', 'changed')",
            (session["id"],),
        ).lastrowid
        entries = [
            {
                "path": "wiki/legacy.md",
                "before_hash": None,
                "before_content_b64": base64.b64encode(b"legacy").decode("ascii"),
                "after_hash": "unused",
            },
            {
                "path": "src/app.py",
                "before_hash": None,
                "before_content_b64": base64.b64encode(b"code").decode("ascii"),
                "after_hash": "unused",
            },
        ]
        app.state.db.execute(
            "INSERT INTO turn_file_journals(message_id, session_id, entries_json) "
            "VALUES (?, ?, ?)",
            (message_id, session["id"], json.dumps(entries)),
        )
        return int(message_id)

    ops_message = seed("journal-legacy")
    dot_message = seed("journal-dot")

    migrations._freeze_reroute_era_paths(app.state.db)

    def paths_for(message_id: int) -> list[str]:
        row = app.state.db.execute(
            "SELECT entries_json FROM turn_file_journals WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return [entry["path"] for entry in json.loads(row["entries_json"])]

    assert paths_for(ops_message) == ["ops/wiki/legacy.md", "src/app.py"]
    assert paths_for(dot_message) == ["wiki/legacy.md", "src/app.py"]

    migrations._freeze_reroute_era_paths(app.state.db)
    assert paths_for(ops_message) == ["ops/wiki/legacy.md", "src/app.py"]

    # The frozen journal restores into the Ops Area - the reroute-era meaning -
    # without recreating a hidden root-level tree.
    restored = api.post(
        f"/api/chat/messages/{ops_message}/restore-turn",
        headers=headers,
        json={"confirm": True},
    )
    assert restored.status_code == 200, restored.text
    assert (root / "ops" / "wiki" / "legacy.md").read_text(
        encoding="utf-8"
    ) == "legacy"


def test_migration_freezes_reroute_era_message_refs(tmp_path: Path):
    """Markdown file references in chat text keep rendering: v60 prefixes
    reserved-name refs with the project's Ops path, only for non-dot Ops
    projects, idempotently. Prose (no markdown-ref position) is untouched."""
    root = _ops_project(tmp_path, "message-legacy")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "message-legacy").status_code == 201
    app = api.app

    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Legacy chat", "project_slug": "message-legacy"},
    ).json()
    content = (
        "Attached ![shot](uploads/shot.png) and [the doc](artifacts/report.pdf).\n"
        "Prose about uploads/shot.png stays untouched, as does src/app.py.\n"
        "Already-real ref: ![x](ops/uploads/other.png)."
    )
    message_id = app.state.db.execute(
        "INSERT INTO messages(session_id, role, content) VALUES (?, 'user', ?)",
        (session["id"], content),
    ).lastrowid

    migrations._freeze_reroute_era_paths(app.state.db)
    migrated = app.state.db.execute(
        "SELECT content FROM messages WHERE id = ?", (message_id,)
    ).fetchone()["content"]
    assert "![shot](ops/uploads/shot.png)" in migrated
    assert "[the doc](ops/artifacts/report.pdf)" in migrated
    assert "Prose about uploads/shot.png stays untouched" in migrated
    assert "![x](ops/uploads/other.png)." in migrated
    assert "ops/ops/" not in migrated

    migrations._freeze_reroute_era_paths(app.state.db)
    again = app.state.db.execute(
        "SELECT content FROM messages WHERE id = ?", (message_id,)
    ).fetchone()["content"]
    assert again == migrated


# ── Moodboard follows the artifacts map ───────────────────────────────────


def test_moodboard_store_follows_the_artifacts_map(tmp_path: Path):
    """A project whose artifacts live at the container root (Ops elsewhere)
    keeps its moodboard there - and the API speaks container-relative paths."""
    root = tmp_path / "moodboard-root"
    (root / "runbook").mkdir(parents=True)
    (root / "artifacts").mkdir()
    (root / "uploads").mkdir()
    (root / "uploads" / "shot.png").write_bytes(_PNG)
    api, headers = _api(tmp_path)
    assert _link(
        api, headers, root, "moodboard-root", ops_path="runbook"
    ).status_code == 201

    added = api.post(
        "/api/projects/moodboard-root/design/moodboard",
        headers=headers,
        json={"imagePath": "uploads/shot.png", "title": "Shot"},
    )
    assert added.status_code == 200, added.text
    assert (root / "artifacts" / "moodboard" / "items.json").is_file()
    assert not (root / "runbook" / "artifacts").exists()

    items = api.get(
        "/api/projects/moodboard-root/design/moodboard", headers=headers
    ).json()["items"]
    assert [item["imagePath"] for item in items] == ["uploads/shot.png"]


def test_moodboard_legacy_ops_relative_paths_upgrade_at_read(tmp_path: Path):
    """Reroute-era moodboard items stored ops-relative resolve and list as the
    container-relative real paths - without touching the store on read."""
    root = _ops_project(tmp_path, "moodboard-legacy")
    store = root / "ops" / "artifacts" / "moodboard"
    (store / "images").mkdir(parents=True)
    (store / "images" / "a.png").write_bytes(_PNG)
    (root / "ops" / "uploads").mkdir()
    (root / "ops" / "uploads" / "shot.png").write_bytes(_PNG)
    legacy_items = [
        {
            "id": "mb-1",
            "kind": "link",
            "url": "https://example.com",
            "imagePath": "artifacts/moodboard/images/a.png",
            "title": "Cached",
            "siteName": "example.com",
            "useAsReference": True,
        },
        {
            "id": "mb-2",
            "kind": "upload",
            "url": None,
            "imagePath": "uploads/shot.png",
            "title": "Uploaded",
            "siteName": "Uploaded screenshot",
            "useAsReference": False,
        },
    ]
    (store / "items.json").write_text(
        json.dumps({"version": 1, "items": legacy_items}), encoding="utf-8"
    )
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "moodboard-legacy").status_code == 201

    before = _strict_tree_snapshot(root)
    listed = api.get(
        "/api/projects/moodboard-legacy/design/moodboard", headers=headers
    )
    assert listed.status_code == 200, listed.text
    image_paths = [item["imagePath"] for item in listed.json()["items"]]
    assert image_paths == [
        "ops/artifacts/moodboard/images/a.png",
        "ops/uploads/shot.png",
    ]
    # Listing is a read: the store bytes are untouched (zero-write reads).
    assert _strict_tree_snapshot(root) == before

    # Repeated reads are stable (the upgrade is idempotent).
    again = api.get(
        "/api/projects/moodboard-legacy/design/moodboard", headers=headers
    ).json()["items"]
    assert [item["imagePath"] for item in again] == image_paths

    # Deleting the cached-image item removes the real cached file.
    removed = api.delete(
        "/api/projects/moodboard-legacy/design/moodboard/mb-1", headers=headers
    )
    assert removed.status_code == 200, removed.text
    assert not (store / "images" / "a.png").exists()


def test_moodboard_active_references_speak_container_paths(tmp_path: Path):
    """Design-run moodboard references carry container-relative image paths so
    the vision loader and the agent resolve them against the project root."""
    root = _ops_project(tmp_path, "moodboard-refs")
    store = root / "ops" / "artifacts" / "moodboard"
    (store / "images").mkdir(parents=True)
    (store / "images" / "a.png").write_bytes(_PNG)
    (store / "items.json").write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "id": "mb-1",
                        "kind": "link",
                        "url": "https://example.com",
                        "imagePath": "artifacts/moodboard/images/a.png",
                        "useAsReference": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "moodboard-refs").status_code == 201

    project = api.app.state.db.execute(
        "SELECT id, slug, path, path_identity FROM projects WHERE slug = ?",
        ("moodboard-refs",),
    ).fetchone()
    layout = layout_map.project_layout(api.app.state.db, project)
    references = moodboard.active_references(moodboard.store_for_layout(layout))
    assert [item["imagePath"] for item in references] == [
        "ops/artifacts/moodboard/images/a.png"
    ]


_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)

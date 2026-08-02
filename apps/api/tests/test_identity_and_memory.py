"""Identity from existing docs + adaptive memory writes (prune C5, #137).

A project's identity (label + one-line summary) is read from the docs the
folder already has - AGENTS.md, README.md, HANDOFF.md (root first, then the
Ops root), with a legacy ops/container.md still honored - and falls back to
the folder name when none exist. No Proxima frontmatter is required anywhere.

Memory writes are adaptive and default ON: the automatic writers (log.md
append, index.md regeneration) target the project's own DETECTED wiki
location through the layout map's write seam, and a per-project toggle can
turn them off entirely. Writes stay fail-closed through symlinks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import container_registry, layout_map, wiki_memory
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


def _link(api: TestClient, headers: dict[str, str], root: Path, slug: str):
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
        },
    )


def _container(api: TestClient, headers: dict[str, str], slug: str) -> dict:
    response = api.get(f"/api/containers/{slug}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


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


def _project_row(conn, slug: str):
    return conn.execute(
        "SELECT id, slug, path, path_identity FROM projects WHERE slug = ?",
        (slug,),
    ).fetchone()


# ── Identity from existing docs ──────────────────────────────────────────


def test_identity_reads_agents_md_without_any_frontmatter(tmp_path: Path):
    """The flagship case (BIP-like): a folder with a plain AGENTS.md - no
    Proxima frontmatter anywhere - gets its identity from that doc: the H1 is
    the label, the first body line is the summary. Zero writes."""
    root = tmp_path / "bip-like"
    (root / "wiki").mkdir(parents=True)
    (root / "AGENTS.md").write_text(
        "# Business Insight Platform\n\n"
        "Internal analytics workspace for the insight crew.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# bip-like\n\nBuild scripts.\n", encoding="utf-8")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    assert _link(api, headers, root, "bip-like").status_code == 201
    container = _container(api, headers, "bip-like")
    assert container["identity_label"] == "Business Insight Platform"
    assert container["summary"] == "Internal analytics workspace for the insight crew."
    assert container["identity_source"] == "AGENTS.md"
    assert container["health"]["registry"] == "ready"
    assert _strict_tree_snapshot(root) == before


def test_identity_falls_back_to_readme(tmp_path: Path):
    root = tmp_path / "readme-only"
    root.mkdir()
    (root / "README.md").write_text(
        "# Wingoh Client Site\n\nMarketing site and booking flow.\n",
        encoding="utf-8",
    )
    api, headers = _api(tmp_path)

    assert _link(api, headers, root, "readme-only").status_code == 201
    container = _container(api, headers, "readme-only")
    assert container["identity_label"] == "Wingoh Client Site"
    assert container["summary"] == "Marketing site and booking flow."
    assert container["identity_source"] == "README.md"


def test_identity_frontmatter_is_honored_when_present_but_never_required(
    tmp_path: Path,
):
    root = tmp_path / "frontmatter"
    root.mkdir()
    (root / "HANDOFF.md").write_text(
        "---\n"
        "title: Handoff Project\n"
        "description: Everything the next agent needs.\n"
        "---\n\n"
        "# Ignored Heading\n\nIgnored body line.\n",
        encoding="utf-8",
    )
    api, headers = _api(tmp_path)

    assert _link(api, headers, root, "frontmatter").status_code == 201
    container = _container(api, headers, "frontmatter")
    assert container["identity_label"] == "Handoff Project"
    assert container["summary"] == "Everything the next agent needs."
    assert container["identity_source"] == "HANDOFF.md"


def test_bare_folder_links_fine_and_identity_is_the_folder_name(tmp_path: Path):
    """No AGENTS.md, no README.md, no HANDOFF.md, no container.md - the link
    succeeds with no metadata requirement and the identity is simply the
    folder's name."""
    root = tmp_path / "bare-folder"
    root.mkdir()
    api, headers = _api(tmp_path)

    assert _link(api, headers, root, "bare-folder").status_code == 201
    container = _container(api, headers, "bare-folder")
    assert container["identity_label"] == "bare-folder"
    assert container["summary"] is None
    assert container["identity_source"] is None
    # The registry is still a first-class, indexed projection - a folder
    # without docs is not "unavailable".
    assert container["health"]["registry"] == "ready"
    assert container["source_hash"]


def test_legacy_container_doc_is_still_read_when_no_real_docs_exist(
    tmp_path: Path,
):
    root = tmp_path / "legacy-doc"
    (root / "ops").mkdir(parents=True)
    (root / "ops" / "container.md").write_text(
        "---\nidentity: Legacy Client\nsummary: Curated by the owner.\n---\n",
        encoding="utf-8",
    )
    api, headers = _api(tmp_path)

    assert _link(api, headers, root, "legacy-doc").status_code == 201
    container = _container(api, headers, "legacy-doc")
    assert container["identity_label"] == "Legacy Client"
    assert container["summary"] == "Curated by the owner."
    assert container["identity_source"] == "ops/container.md"


def test_registry_refresh_follows_identity_doc_edits_on_disk(tmp_path: Path):
    root = tmp_path / "editable"
    root.mkdir()
    (root / "AGENTS.md").write_text("# First Title\n\nFirst summary.\n", encoding="utf-8")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "editable").status_code == 201
    first = _container(api, headers, "editable")
    assert first["identity_label"] == "First Title"

    (root / "AGENTS.md").write_text(
        "# Second Title\n\nSecond summary.\n", encoding="utf-8"
    )
    cycle = container_registry.refresh_registry_projections(api.app.state.db)
    assert cycle["refreshed"] == 1
    refreshed = _container(api, headers, "editable")
    assert refreshed["identity_label"] == "Second Title"
    assert refreshed["summary"] == "Second summary."
    assert refreshed["source_hash"] != first["source_hash"]


# ── Adaptive memory writes (default ON) ──────────────────────────────────


def test_memory_writes_follow_the_detected_wiki_location(tmp_path: Path):
    """A wiki detected AWAY from the default location (root wiki/ with Ops at
    ops/) now receives the automatic memory writes: the seam returns the
    detected wiki, and a real memory event lands log.md there."""
    root = tmp_path / "adaptive"
    (root / "ops").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "wiki" / "note.md").write_bytes(b"# root note\n")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "adaptive").status_code == 201
    project = _project_row(api.app.state.db, "adaptive")

    write_root = layout_map.wiki_memory_write_root(api.app.state.db, project)
    assert write_root == root / "wiki"

    # An actual memory event writes into the detected wiki - and only there.
    wiki_memory.append_log_entry(write_root, datetime(2026, 8, 2, 10, 0), "owner", "did a thing")
    assert (root / "wiki" / "log.md").is_file()
    assert not (root / "ops" / "wiki").exists()


def test_memory_writes_default_location_is_unchanged(tmp_path: Path):
    """A project with nothing detected keeps today's behavior exactly: the
    memory write root is <ops>/wiki even before the folder exists."""
    root = tmp_path / "plain"
    (root / "ops").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "plain").status_code == 201
    project = _project_row(api.app.state.db, "plain")
    assert layout_map.wiki_memory_write_root(api.app.state.db, project) == (
        root / "ops" / "wiki"
    )


def test_memory_toggle_off_disables_all_automatic_memory_writes(tmp_path: Path):
    """The per-project toggle (default ON) fully disables the automatic
    writers: the seam returns None and a wiki-note commit no longer
    regenerates index.md (the explicitly committed note itself still lands)."""
    root = tmp_path / "toggled"
    (root / "wiki").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "toggled").status_code == 201
    project = _project_row(api.app.state.db, "toggled")

    layout = api.get("/api/projects/toggled/layout", headers=headers)
    assert layout.status_code == 200, layout.text
    assert layout.json()["memory_writes"] == {"enabled": True}

    flipped = api.put(
        "/api/projects/toggled/memory-writes",
        headers=headers,
        json={"enabled": False},
    )
    assert flipped.status_code == 200, flipped.text
    assert flipped.json() == {"enabled": False}
    assert api.get("/api/projects/toggled/layout", headers=headers).json()[
        "memory_writes"
    ] == {"enabled": False}
    assert layout_map.wiki_memory_write_root(api.app.state.db, project) is None

    session = api.post(
        "/api/sessions", headers=headers, json={"project_slug": "toggled"}
    )
    assert session.status_code in (200, 201), session.text
    committed = api.post(
        f"/api/sessions/{session.json()['id']}/wiki-note/commit",
        headers=headers,
        json={"path": "howto.md", "content": "# Howto\n\nSteps.\n", "mode": "replace"},
    )
    assert committed.status_code == 200, committed.text
    assert (root / "wiki" / "howto.md").is_file()
    assert not (root / "wiki" / "index.md").exists()

    # Flipping back on restores the automatic index regeneration.
    restored = api.put(
        "/api/projects/toggled/memory-writes",
        headers=headers,
        json={"enabled": True},
    )
    assert restored.status_code == 200, restored.text
    assert layout_map.wiki_memory_write_root(api.app.state.db, project) == (
        root / "wiki"
    )


def test_wiki_note_commit_regenerates_index_in_the_detected_wiki(tmp_path: Path):
    """With memory writes ON (the default), committing a wiki note into a
    detected non-default wiki also regenerates index.md THERE - the first
    intentional write into a linked real folder."""
    root = tmp_path / "indexed"
    (root / "ops").mkdir(parents=True)
    (root / "wiki").mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "indexed").status_code == 201

    session = api.post(
        "/api/sessions", headers=headers, json={"project_slug": "indexed"}
    )
    assert session.status_code in (200, 201), session.text
    committed = api.post(
        f"/api/sessions/{session.json()['id']}/wiki-note/commit",
        headers=headers,
        json={"path": "howto.md", "content": "# Howto\n\nSteps.\n", "mode": "replace"},
    )
    assert committed.status_code == 200, committed.text
    assert (root / "wiki" / "index.md").is_file()
    assert "howto.md" in (root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert not (root / "ops" / "wiki").exists()


def test_memory_writes_stay_fail_closed_through_symlinks(tmp_path: Path):
    """A wiki position occupied by a symlink is never a write target - the
    fail-closed write rule stays until the explicit symlink rework."""
    root = tmp_path / "symlinked"
    (root / "ops").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere-wiki"
    elsewhere.mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "symlinked").status_code == 201
    project = _project_row(api.app.state.db, "symlinked")

    # The default position appears later as a symlink: refuse to write.
    (root / "ops" / "wiki").symlink_to(elsewhere)
    assert layout_map.wiki_memory_write_root(api.app.state.db, project) is None


# ── The migration plan imposes no identity document ──────────────────────


def test_explicit_migration_no_longer_generates_container_md(tmp_path: Path):
    """The explicit opt-in migration plans no container.md generation: the
    preview lists no generated identity document, and after the migrate the
    Ops root holds only the moved content."""
    root = tmp_path / "migratable"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "note.md").write_text("# note\n", encoding="utf-8")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "migratable").status_code == 201

    inspected = api.get("/api/projects/migratable/ops-migration", headers=headers)
    assert inspected.status_code == 200, inspected.text
    detail = inspected.json()
    assert detail["retry_action"] == "migrate"
    assert detail["planned_writes"]["container_doc"] is None

    retried = api.post(
        "/api/projects/migratable/ops-migration/retry", headers=headers
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["phase"] == "complete"
    assert (root / "ops" / "wiki" / "note.md").is_file()
    assert not (root / "ops" / "container.md").exists()

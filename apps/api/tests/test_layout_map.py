"""Per-project layout map for wiki/artifacts/scripts/uploads (prune C4, #136).

Where a project keeps its wiki, artifacts, scripts, and uploads is per-project
data seeded by zero-write detection from the real tree at link time (and
lazily for already-linked projects). Today's fixed names are only the
DEFAULTS when nothing is detected; features resolve those locations through
the persisted map instead of hardcoded names. Memory WRITES follow the
detected wiki too (adaptive, per-project toggleable - prune C5, #137; see
test_identity_and_memory.py for the toggle and fail-closed rules).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import layout_map
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


def _layout_rows(conn, slug: str) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT pl.area, pl.rel_path, pl.source FROM project_layout pl
        JOIN projects p ON p.id = pl.project_id
        WHERE p.slug = ? ORDER BY pl.area
        """,
        (slug,),
    ).fetchall()
    return {row["area"]: (row["rel_path"], row["source"]) for row in rows}


def _project_row(conn, slug: str):
    return conn.execute(
        "SELECT id, slug, path, path_identity FROM projects WHERE slug = ?",
        (slug,),
    ).fetchone()


# ── Detection (pure, zero-write) ─────────────────────────────────────────


def test_detect_layout_defaults_when_nothing_exists(tmp_path: Path):
    """An empty tree detects nothing: every area falls back to today's fixed
    name under the Ops root, recorded as a default."""
    root = tmp_path / "empty"
    root.mkdir()
    detected = layout_map.detect_layout(root, ".")
    assert detected == {
        "wiki": ("wiki", "default"),
        "artifacts": ("artifacts", "default"),
        "scripts": ("scripts", "default"),
        "uploads": ("uploads", "default"),
    }


def test_detect_layout_prefers_ops_then_root(tmp_path: Path):
    """Detection looks under the Ops root first, then at the container root;
    a real directory is detected, anything else keeps the default."""
    root = tmp_path / "mixed"
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "uploads").write_text("a file, not a folder", encoding="utf-8")
    detected = layout_map.detect_layout(root, "ops")
    assert detected == {
        "wiki": ("ops/wiki", "detected"),
        "artifacts": ("ops/artifacts", "default"),
        "scripts": ("scripts", "detected"),
        "uploads": ("ops/uploads", "default"),
    }


def test_detect_layout_ignores_symlinked_dirs(tmp_path: Path):
    root = tmp_path / "symlinked"
    root.mkdir()
    real = tmp_path / "elsewhere-wiki"
    real.mkdir()
    (root / "wiki").symlink_to(real)
    detected = layout_map.detect_layout(root, ".")
    assert detected["wiki"] == ("wiki", "default")


def test_detect_layout_is_zero_write(tmp_path: Path):
    root = tmp_path / "readonly"
    (root / "ops").mkdir(parents=True)
    (root / "wiki").mkdir()
    before = _strict_tree_snapshot(root)
    layout_map.detect_layout(root, "ops")
    assert _strict_tree_snapshot(root) == before


# ── Seeding at link time ─────────────────────────────────────────────────


def test_link_seeds_layout_map_from_detection(tmp_path: Path):
    """Linking seeds the persisted per-project map: detected locations where
    the tree has them, today's names as defaults elsewhere - without writing
    anything into the folder."""
    root = tmp_path / "fixture"
    (root / "ops" / "wiki").mkdir(parents=True)
    (root / "ops" / "wiki" / "note.md").write_bytes(b"# note\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "fetch.sh").write_bytes(b"# Description: fetch\n")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "fixture")
    assert linked.status_code == 201, linked.text

    assert _layout_rows(api.app.state.db, "fixture") == {
        "wiki": ("ops/wiki", "detected"),
        "artifacts": ("ops/artifacts", "default"),
        "scripts": ("scripts", "detected"),
        "uploads": ("ops/uploads", "default"),
    }
    layout = api.get("/api/projects/fixture/layout", headers=headers)
    assert layout.status_code == 200, layout.text
    body = layout.json()
    assert body["ops_path"] == "ops"
    assert body["areas"]["wiki"] == {
        "path": "ops/wiki",
        "source": "detected",
        "exists": True,
    }
    assert body["areas"]["uploads"] == {
        "path": "ops/uploads",
        "source": "default",
        "exists": False,
    }
    assert _strict_tree_snapshot(root) == before


def test_root_project_detects_root_wiki_as_the_wiki_location(tmp_path: Path):
    """The flagship example (BIP): a '.' project keeping wiki/ at the project
    root has that folder DETECTED as its wiki location, and the wiki surface
    reads it - all without a single write into the folder."""
    root = tmp_path / "bip-like"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "decisions.md").write_bytes(b"# Decisions\n\nkeep it simple\n")
    before = _strict_tree_snapshot(root)
    api, headers = _api(tmp_path)

    linked = _link(api, headers, root, "bip-like")
    assert linked.status_code == 201, linked.text

    assert _layout_rows(api.app.state.db, "bip-like")["wiki"] == ("wiki", "detected")

    # Read-side: the project wiki surface serves the root-wiki notes.
    notes = api.get("/api/projects/bip-like/wiki/all", headers=headers)
    assert notes.status_code == 200, notes.text
    assert [n["path"] for n in notes.json()["notes"]] == ["decisions.md"]

    # Read-side: the wiki-note draft surface lists the existing root-wiki
    # notes for the drafting agent - the read resolved through the map.
    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"project_slug": "bip-like"},
    )
    assert session.status_code in (200, 201), session.text
    session_id = session.json()["id"]
    draft = api.post(
        f"/api/sessions/{session_id}/wiki-note/draft",
        headers=headers,
        json={},
    )
    assert draft.status_code == 202, draft.text
    run = api.app.state.db.execute(
        "SELECT prompt FROM runs WHERE id = ?", (draft.json()["run_id"],)
    ).fetchone()
    assert "decisions.md" in run["prompt"]
    assert _strict_tree_snapshot(root) == before


# ── Persistence + lazy backfill ──────────────────────────────────────────


def test_layout_map_survives_restart(tmp_path: Path):
    """The map is persisted state, not a per-boot detection: a detected entry
    in the default position survives a restart even after its folder is gone."""
    root = tmp_path / "durable"
    (root / "ops" / "wiki").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "durable").status_code == 201, "link failed"
    assert _layout_rows(api.app.state.db, "durable")["wiki"] == ("ops/wiki", "detected")

    (root / "ops" / "wiki").rmdir()
    api2, headers2 = _api(tmp_path)
    layout = api2.get("/api/projects/durable/layout", headers=headers2)
    assert layout.status_code == 200, layout.text
    assert layout.json()["areas"]["wiki"] == {
        "path": "ops/wiki",
        "source": "detected",
        "exists": False,
    }


def test_stale_out_of_ops_entry_self_heals(tmp_path: Path):
    """A detected location OUTSIDE the Ops root stays authoritative only while
    it exists; when the folder disappears the area re-detects (back to the
    default), so a later explicit migration can never leave dangling map rows."""
    root = tmp_path / "self-heal"
    (root / "ops").mkdir(parents=True)
    (root / "scripts").mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "self-heal").status_code == 201
    assert _layout_rows(api.app.state.db, "self-heal")["scripts"] == (
        "scripts",
        "detected",
    )

    (root / "scripts").rmdir()
    layout = api.get("/api/projects/self-heal/layout", headers=headers)
    assert layout.status_code == 200, layout.text
    assert layout.json()["areas"]["scripts"] == {
        "path": "ops/scripts",
        "source": "default",
        "exists": False,
    }
    assert _layout_rows(api.app.state.db, "self-heal")["scripts"] == (
        "ops/scripts",
        "default",
    )


def test_legacy_linked_project_lazily_backfills(tmp_path: Path):
    """A project linked before the map existed (no rows) backfills lazily on
    first resolution - detection against the real tree, today's names as the
    defaults - so behavior is unchanged until detection says otherwise."""
    root = tmp_path / "legacy"
    (root / "ops" / "wiki").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "legacy").status_code == 201
    api.app.state.db.execute(
        "DELETE FROM project_layout WHERE project_id = "
        "(SELECT id FROM projects WHERE slug = 'legacy')"
    )
    assert _layout_rows(api.app.state.db, "legacy") == {}

    layout = api.get("/api/projects/legacy/layout", headers=headers)
    assert layout.status_code == 200, layout.text
    assert layout.json()["areas"]["wiki"]["path"] == "ops/wiki"
    assert _layout_rows(api.app.state.db, "legacy") == {
        "wiki": ("ops/wiki", "detected"),
        "artifacts": ("ops/artifacts", "default"),
        "scripts": ("ops/scripts", "default"),
        "uploads": ("ops/uploads", "default"),
    }


def test_boot_sweep_seeds_already_linked_projects(tmp_path: Path):
    """Existing linked projects get their map on the first boot after the
    upgrade (the startup settle sweep seeds them) - zero tree writes."""
    root = tmp_path / "preexisting"
    (root / "wiki").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "preexisting").status_code == 201
    api.app.state.db.execute(
        "DELETE FROM project_layout WHERE project_id = "
        "(SELECT id FROM projects WHERE slug = 'preexisting')"
    )
    before = _strict_tree_snapshot(root)

    api2, _headers2 = _api(tmp_path)
    assert _layout_rows(api2.app.state.db, "preexisting")["wiki"] == (
        "wiki",
        "detected",
    )
    assert _strict_tree_snapshot(root) == before


# ── Features resolve through the map ─────────────────────────────────────


def test_upload_lands_in_the_mapped_uploads_folder(tmp_path: Path):
    """The upload default folder is the map's uploads location: a project
    keeping uploads/ at its root (Ops at ops/) receives uploads THERE, not in
    a Proxima-invented ops/uploads/."""
    root = tmp_path / "uploader"
    (root / "ops").mkdir(parents=True)
    (root / "uploads").mkdir()
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "uploader").status_code == 201
    assert _layout_rows(api.app.state.db, "uploader")["uploads"] == (
        "uploads",
        "detected",
    )

    uploaded = api.post(
        "/api/projects/uploader/upload",
        headers=headers,
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert (root / "uploads" / "note.txt").read_bytes() == b"hello"
    assert not (root / "ops" / "uploads").exists()


def test_upload_default_location_is_unchanged(tmp_path: Path):
    """With nothing detected the default keeps today's behavior byte-for-byte:
    uploads go to <ops>/uploads."""
    root = tmp_path / "plain-uploader"
    (root / "ops").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "plain-uploader").status_code == 201

    uploaded = api.post(
        "/api/projects/plain-uploader/upload",
        headers=headers,
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["path"] == "uploads/note.txt"
    assert (root / "ops" / "uploads" / "note.txt").read_bytes() == b"hello"


def test_scripts_and_designs_resolve_through_the_map(tmp_path: Path):
    """Library scripts and Design Studio scenes resolve through the map:
    a root-level scripts/ (Ops at ops/) is the script library."""
    root = tmp_path / "scripty"
    (root / "ops" / "artifacts" / "design" / "d1").mkdir(parents=True)
    (root / "ops" / "artifacts" / "design" / "d1" / "scene.json").write_text(
        '{"id": "d1", "title": "One"}', encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "fetch.sh").write_text(
        "# Description: fetches things\n", encoding="utf-8"
    )
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "scripty").status_code == 201

    project = _project_row(api.app.state.db, "scripty")
    layout = layout_map.project_layout(api.app.state.db, project)
    assert layout.dir("scripts") == root / "scripts"
    from proxima_api import scripts_library

    catalog = scripts_library.scan_catalog(layout.dir("scripts"))
    assert [entry["rel_path"] for entry in catalog] == ["fetch.sh"]
    assert catalog[0]["description"] == "fetches things"

    from proxima_api import wiki_memory

    designs = wiki_memory.list_project_designs(layout.dir("artifacts"))
    assert [d["id"] for d in designs] == ["d1"]


# ── The memory-write seam: adaptive, default ON (prune C5, #137) ─────────


def test_wiki_memory_write_root_matches_default_location(tmp_path: Path):
    """Automatic memory writes (log.md / index.md) keep today's location when
    the map agrees with the default - including a '.' project's root wiki
    (BIP), which IS its default location."""
    root = tmp_path / "memory-default"
    (root / "wiki").mkdir(parents=True)
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "memory-default").status_code == 201
    project = _project_row(api.app.state.db, "memory-default")
    assert layout_map.wiki_memory_write_root(api.app.state.db, project) == (
        root / "wiki"
    )


def test_wiki_memory_writes_follow_a_divergent_detected_wiki(tmp_path: Path):
    """Adaptive memory writes (prune C5, #137): a wiki detected AWAY from the
    default location IS the automatic writers' target - Proxima's memory goes
    into the project's own wiki, wherever the folder keeps it."""
    root = tmp_path / "memory-divergent"
    (root / "ops").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "wiki" / "note.md").write_bytes(b"# root note\n")
    api, headers = _api(tmp_path)
    assert _link(api, headers, root, "memory-divergent").status_code == 201
    project = _project_row(api.app.state.db, "memory-divergent")

    layout = layout_map.project_layout(api.app.state.db, project)
    assert layout.rel_paths["wiki"] == "wiki"
    assert layout.dir("wiki") == root / "wiki"
    assert layout_map.wiki_memory_write_root(api.app.state.db, project) == (
        root / "wiki"
    )

"""Work-container areas (Phase-1 slice 1, T1): detection, override API, payload."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.project_areas import detect_code_areas


def _repo(path: Path, gitfile: bool = False) -> Path:
    """Make `path` look like a git repo (.git dir, or a gitfile like a linked
    worktree / submodule checkout)."""
    path.mkdir(parents=True, exist_ok=True)
    if gitfile:
        (path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    else:
        (path / ".git").mkdir()
    return path


# ── detection ────────────────────────────────────────────────────────────


def test_detect_repo_at_root(tmp_path: Path):
    _repo(tmp_path)
    assert detect_code_areas(tmp_path) == ["."]


def test_detect_repos_in_subfolders(tmp_path: Path):
    _repo(tmp_path / "beta")
    _repo(tmp_path / "alpha" / "tool")
    (tmp_path / "notes").mkdir()
    assert detect_code_areas(tmp_path) == ["alpha/tool", "beta"]


def test_detect_no_repo_is_valid(tmp_path: Path):
    (tmp_path / "artifacts").mkdir()
    assert detect_code_areas(tmp_path) == []


def test_detect_gitfile_counts_as_repo(tmp_path: Path):
    _repo(tmp_path / "wt", gitfile=True)
    assert detect_code_areas(tmp_path) == ["wt"]


def test_detect_does_not_descend_into_a_repo(tmp_path: Path):
    # A .git under a detected repo is that repo's submodule/vendored checkout,
    # not a separate code area of the container.
    _repo(tmp_path / "app")
    _repo(tmp_path / "app" / "vendor" / "lib")
    assert detect_code_areas(tmp_path) == ["app"]
    # Same rule when the root itself is the repo.
    _repo(tmp_path)
    assert detect_code_areas(tmp_path) == ["."]


def test_detect_skips_heavy_and_hidden_dirs(tmp_path: Path):
    _repo(tmp_path / "node_modules" / "dep")
    _repo(tmp_path / ".cache-ish")
    _repo(tmp_path / "real")
    assert detect_code_areas(tmp_path) == ["real"]


def test_detect_depth_is_bounded(tmp_path: Path):
    _repo(tmp_path / "a" / "b")            # depth 2: detected
    _repo(tmp_path / "x" / "y" / "z")      # depth 3: beyond the bound
    assert detect_code_areas(tmp_path) == ["a/b"]


# ── API contract ─────────────────────────────────────────────────────────


def client(tmp_path: Path) -> TestClient:
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "start_worker": False,
        }
    )
    return TestClient(app)


def auth_headers(api: TestClient) -> dict[str, str]:
    res = api.post("/auth/auto")
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _link(api: TestClient, h: dict[str, str], path: Path, slug: str) -> dict:
    res = api.post("/api/projects/link", headers=h, json={"path": str(path), "slug": slug})
    assert res.status_code == 201, res.text
    return res.json()


def test_link_detects_repo_at_root_and_ops_area(tmp_path: Path):
    folder = _repo(tmp_path / "myrepo")
    api = client(tmp_path)
    h = auth_headers(api)
    p = _link(api, h, folder, "myrepo")
    assert [a["rel_path"] for a in p["code_areas"]] == ["."]
    assert p["code_areas"][0]["source"] == "auto"
    assert p["ops_area"]["rel_path"] == "."


def test_link_detects_multiple_sub_repos(tmp_path: Path):
    folder = tmp_path / "container"
    _repo(folder / "web")
    _repo(folder / "api")
    (folder / "reports").mkdir(parents=True)
    api = client(tmp_path)
    h = auth_headers(api)
    p = _link(api, h, folder, "container")
    assert [a["rel_path"] for a in p["code_areas"]] == ["api", "web"]


def test_created_project_has_ops_area_and_zero_code_areas(tmp_path: Path):
    api = client(tmp_path)
    h = auth_headers(api)
    res = api.post("/api/projects", headers=h, json={"slug": "fresh", "name": "Fresh"})
    assert res.status_code == 201, res.text
    p = res.json()
    assert p["code_areas"] == []
    assert p["ops_area"]["rel_path"] == "."


def test_areas_surface_on_get_and_list(tmp_path: Path):
    folder = _repo(tmp_path / "repo")
    api = client(tmp_path)
    h = auth_headers(api)
    _link(api, h, folder, "repo")
    got = api.get("/api/projects/repo", headers=h).json()
    assert [a["rel_path"] for a in got["code_areas"]] == ["."]
    listed = api.get("/api/projects", headers=h).json()["projects"]
    mine = next(p for p in listed if p["slug"] == "repo")
    assert [a["rel_path"] for a in mine["code_areas"]] == ["."]
    areas = api.get("/api/projects/repo/areas", headers=h).json()
    assert [a["rel_path"] for a in areas["code_areas"]] == ["."]
    assert areas["ops_area"]["rel_path"] == "."


def test_manual_add_of_non_repo_folder(tmp_path: Path):
    # Not-yet-`git init`'d code is a valid code area (T1 hybrid).
    folder = tmp_path / "container"
    (folder / "newcode").mkdir(parents=True)
    api = client(tmp_path)
    h = auth_headers(api)
    _link(api, h, folder, "container")
    res = api.post("/api/projects/container/areas", headers=h, json={"rel_path": "newcode"})
    assert res.status_code == 201, res.text
    assert res.json() == {"id": res.json()["id"], "rel_path": "newcode", "source": "manual"}
    areas = api.get("/api/projects/container/areas", headers=h).json()
    assert [(a["rel_path"], a["source"]) for a in areas["code_areas"]] == [("newcode", "manual")]


def test_manual_add_rejects_escape_and_missing_dirs(tmp_path: Path):
    folder = tmp_path / "container"
    folder.mkdir()
    api = client(tmp_path)
    h = auth_headers(api)
    _link(api, h, folder, "container")
    assert api.post("/api/projects/container/areas", headers=h, json={"rel_path": "../outside"}).status_code == 400
    assert api.post("/api/projects/container/areas", headers=h, json={"rel_path": "/etc"}).status_code == 400
    assert api.post("/api/projects/container/areas", headers=h, json={"rel_path": "ghost"}).status_code == 400


def test_manual_area_survives_redetection(tmp_path: Path):
    folder = tmp_path / "container"
    (folder / "newcode").mkdir(parents=True)
    api = client(tmp_path)
    h = auth_headers(api)
    _link(api, h, folder, "container")
    api.post("/api/projects/container/areas", headers=h, json={"rel_path": "newcode"})
    res = api.post("/api/projects/container/areas/detect", headers=h)
    assert res.status_code == 200, res.text
    areas = res.json()
    # newcode has no .git, but the manual row is never clobbered by re-detection.
    assert [(a["rel_path"], a["source"]) for a in areas["code_areas"]] == [("newcode", "manual")]


def test_removed_area_is_not_resurrected_by_redetection(tmp_path: Path):
    folder = tmp_path / "container"
    _repo(folder / "app")
    api = client(tmp_path)
    h = auth_headers(api)
    p = _link(api, h, folder, "container")
    area_id = p["code_areas"][0]["id"]
    res = api.delete(f"/api/projects/container/areas/{area_id}", headers=h)
    assert res.status_code == 200, res.text
    assert api.get("/api/projects/container/areas", headers=h).json()["code_areas"] == []
    # The repo still exists on disk, but the owner excluded it - detect must not re-add.
    areas = api.post("/api/projects/container/areas/detect", headers=h).json()
    assert areas["code_areas"] == []
    # Deleting it again 404s (it is already gone from the surface).
    assert api.delete(f"/api/projects/container/areas/{area_id}", headers=h).status_code == 404


def test_readding_a_removed_area_revives_it_as_manual(tmp_path: Path):
    folder = tmp_path / "container"
    _repo(folder / "app")
    api = client(tmp_path)
    h = auth_headers(api)
    p = _link(api, h, folder, "container")
    api.delete(f"/api/projects/container/areas/{p['code_areas'][0]['id']}", headers=h)
    res = api.post("/api/projects/container/areas", headers=h, json={"rel_path": "app"})
    assert res.status_code == 201, res.text
    areas = api.get("/api/projects/container/areas", headers=h).json()
    assert [(a["rel_path"], a["source"]) for a in areas["code_areas"]] == [("app", "manual")]


def test_detect_on_demand_picks_up_new_repo_and_drops_gone_auto(tmp_path: Path):
    folder = tmp_path / "container"
    _repo(folder / "old")
    api = client(tmp_path)
    h = auth_headers(api)
    _link(api, h, folder, "container")
    # A repo appears after link; the old one stops being a repo.
    _repo(folder / "new")
    (folder / "old" / ".git").rmdir()
    res = api.post("/api/projects/container/areas/detect", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [a["rel_path"] for a in body["code_areas"]] == ["new"]
    assert body["detect"]["added"] == ["new"]
    assert body["detect"]["removed"] == ["old"]


def test_areas_are_scoped_to_their_project(tmp_path: Path):
    a = _repo(tmp_path / "a")
    b = tmp_path / "b"
    b.mkdir()
    api = client(tmp_path)
    h = auth_headers(api)
    pa = _link(api, h, a, "a")
    _link(api, h, b, "b")
    assert api.get("/api/projects/b/areas", headers=h).json()["code_areas"] == []
    # Deleting project a's area via project b 404s.
    assert api.delete(f"/api/projects/b/areas/{pa['code_areas'][0]['id']}", headers=h).status_code == 404

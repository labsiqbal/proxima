from __future__ import annotations

import json
from pathlib import Path
from fastapi.testclient import TestClient

from proxima_api import app_settings
from proxima_api.main import create_app


def client(tmp_path: Path) -> TestClient:
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_design_studio": True,
    })
    return TestClient(app)


def setup_project(c: TestClient, tmp_path: Path) -> dict:
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    proj = c.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"}).json()
    # projectctl is /usr/bin/true so create the dir ourselves to mirror real behavior
    Path(proj["path"]).mkdir(parents=True, exist_ok=True)
    return headers


def test_image_generation_defaults_to_codex(tmp_path):
    c = client(tmp_path)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    body = c.get("/api/settings/image-gen", headers=headers).json()

    assert body["provider"] == "codex"
    assert body["defaultProvider"] == "codex"
    assert [p["id"] for p in body["providers"]] == ["codex", "xai-oauth", "higgsfield", "openai-compatible"]


def test_higgsfield_image_provider_is_selectable(tmp_path):
    c = client(tmp_path)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    app_settings.set_json(c.app.state.db, app_settings.IMAGE_GEN_KEY, {"provider": "auto", "apiKey": None, "baseUrl": None, "model": None})

    body = c.get("/api/settings/image-gen", headers=headers).json()
    saved = c.put("/api/settings/image-gen", headers=headers, json={"provider": "higgsfield", "model": "nano_banana_2"})

    assert body["provider"] == "codex"
    assert saved.status_code == 200
    assert saved.json()["provider"] == "higgsfield"


def test_tree_read_write_mkdir_rename_delete(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)

    assert c.put("/api/projects/demo/file?path=notes/a.txt", headers=headers, json={"content": "hello"}).status_code == 200
    tree = c.get("/api/projects/demo/tree?path=notes", headers=headers).json()["entries"]
    assert {"name": "a.txt", "type": "file", "size": 5} in tree

    body = c.get("/api/projects/demo/file?path=notes/a.txt", headers=headers).json()
    assert body["content"] == "hello"

    assert c.post("/api/projects/demo/fs/mkdir", headers=headers, json={"path": "newdir"}).status_code == 200
    assert c.post("/api/projects/demo/fs/rename", headers=headers, json={"from": "notes/a.txt", "to": "notes/b.txt"}).status_code == 200
    assert c.delete("/api/projects/demo/fs?path=notes/b.txt", headers=headers).status_code == 200


def test_reference_files_endpoint_is_authenticated_and_project_scoped(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    other = c.post(
        "/api/projects",
        headers=headers,
        json={"slug": "other", "name": "Other"},
    ).json()
    projects = c.get("/api/projects", headers=headers).json()["projects"]
    demo_root = Path(next(project["path"] for project in projects if project["slug"] == "demo"))
    other_root = Path(other["path"])
    other_root.mkdir(parents=True, exist_ok=True)
    (demo_root / "src").mkdir()
    (demo_root / "src" / "demo.py").write_text("demo", encoding="utf-8")
    (other_root / "other.py").write_text("other", encoding="utf-8")

    password = c.post(
        "/auth/set-password",
        json={"password": "correct horse battery"},
    )
    assert password.status_code == 200
    authenticated = {"Authorization": f"Bearer {password.json()['token']}"}
    fresh = TestClient(c.app)

    assert fresh.get("/api/projects/demo/reference-files").status_code == 401
    response = fresh.get("/api/projects/demo/reference-files", headers=authenticated)

    assert response.status_code == 200
    body = response.json()
    paths = {item["path"] for item in body["files"]}
    assert "src/demo.py" in paths
    assert "other.py" not in paths
    assert all(set(item) == {"path"} for item in body["files"])
    assert body["truncated"] is False
    assert fresh.get("/api/projects/missing/reference-files", headers=authenticated).status_code == 404


def test_reference_files_endpoint_caps_results_and_hides_sensitive_paths(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    projects = c.get("/api/projects", headers=headers).json()["projects"]
    root = Path(next(project["path"] for project in projects if project["slug"] == "demo"))
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")
    (root / ".env.local").write_text("TOKEN=secret", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dependency.js").write_text("dependency", encoding="utf-8")

    response = c.get("/api/projects/demo/reference-files?limit=2", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["files"]) == 2
    assert body["truncated"] is True
    assert all(set(item) == {"path"} for item in body["files"])
    assert all(item["path"] not in {".env.local", "node_modules/dependency.js"} for item in body["files"])
    assert c.get("/api/projects/demo/reference-files?limit=2001", headers=headers).status_code == 422


def test_project_fs_collisions_return_400(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    assert c.put("/api/projects/demo/file?path=notes/a.txt", headers=headers, json={"content": "hello"}).status_code == 200
    assert c.post("/api/projects/demo/fs/mkdir", headers=headers, json={"path": "notes/a.txt"}).status_code == 400
    assert c.post(
        "/api/projects/demo/fs/rename",
        headers=headers,
        json={"from": "notes/a.txt", "to": "notes/a.txt/renamed.txt"},
    ).status_code == 400


def test_upload_parent_collision_returns_400(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    assert c.put("/api/projects/demo/file?path=uploads.txt", headers=headers, json={"content": "not a folder"}).status_code == 200

    res = c.post(
        "/api/projects/demo/upload?dir=uploads.txt",
        headers=headers,
        files={"file": ("image.png", b"png", "image/png")},
    )

    assert res.status_code == 400


def test_upload_streams_content_and_deduplicates_names(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    content = b"proxima" * 200_000  # larger than the one-megabyte copy chunk

    first = c.post(
        "/api/projects/demo/upload",
        headers=headers,
        files={"file": ("bundle.bin", content, "application/octet-stream")},
    )
    second = c.post(
        "/api/projects/demo/upload",
        headers=headers,
        files={"file": ("bundle.bin", b"second", "application/octet-stream")},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["path"] == "uploads/bundle.bin"
    assert second.json()["path"] == "uploads/bundle-1.bin"
    project_root = Path(c.get("/api/projects", headers=headers).json()["projects"][0]["path"])
    assert (project_root / first.json()["path"]).read_bytes() == content
    assert (project_root / second.json()["path"]).read_bytes() == b"second"


def test_upload_limit_rejects_and_removes_partial_file(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "limit.db"),
        "workspace_root": str(tmp_path / "limit-ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "max_upload_bytes": 4,
    })
    c = TestClient(app)
    headers = setup_project(c, tmp_path)

    response = c.post(
        "/api/projects/demo/upload",
        headers=headers,
        files={"file": ("large.bin", b"12345", "application/octet-stream")},
    )

    assert response.status_code == 413
    projects = c.get("/api/projects", headers=headers).json()["projects"]
    project_root = Path(next(p["path"] for p in projects if p["slug"] == "demo"))
    assert not (project_root / "uploads" / "large.bin").exists()


def test_traversal_is_rejected(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    assert c.get("/api/projects/demo/tree?path=../..", headers=headers).status_code == 400


def test_read_missing_file_returns_400(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    assert c.get("/api/projects/demo/file?path=does-not-exist", headers=headers).status_code == 400


def test_wiki_personal_crud(tmp_path):
    c = client(tmp_path)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    # personal wiki is created-on-demand with a seeded index.md
    tree = c.get("/api/wiki/tree", headers=headers).json()["entries"]
    assert any(e["name"] == "index.md" for e in tree)
    assert c.put("/api/wiki/file?path=notes/todo.md", headers=headers, json={"content": "# todo"}).status_code == 200
    assert c.get("/api/wiki/file?path=notes/todo.md", headers=headers).json()["content"] == "# todo"
    assert c.post("/api/wiki/fs/rename", headers=headers, json={"from": "notes/todo.md", "to": "notes/done.md"}).status_code == 200
    assert c.delete("/api/wiki/fs?path=notes/done.md", headers=headers).status_code == 200
    assert c.get("/api/wiki/tree?path=../..", headers=headers).status_code == 400


def test_wiki_fs_collisions_return_400(tmp_path):
    c = client(tmp_path)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert c.put("/api/wiki/file?path=notes/todo.md", headers=headers, json={"content": "# todo"}).status_code == 200
    assert c.post("/api/wiki/fs/mkdir", headers=headers, json={"path": "notes/todo.md"}).status_code == 400
    assert c.post(
        "/api/wiki/fs/rename",
        headers=headers,
        json={"from": "notes/todo.md", "to": "notes/todo.md/done.md"},
    ).status_code == 400


def test_wiki_all_bulk(tmp_path):
    c = client(tmp_path)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    c.put("/api/wiki/file?path=a.md", headers=headers, json={"content": "see [[b]]"})
    c.put("/api/wiki/file?path=b.md", headers=headers, json={"content": "# B"})
    notes = c.get("/api/wiki/all", headers=headers).json()["notes"]
    paths = {n["path"] for n in notes}
    assert {"index.md", "a.md", "b.md"} <= paths
    assert any(n["content"] == "see [[b]]" for n in notes)


# ── chat → studio bridges ─────────────────────────────────────────────────────

PNG_1x2 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000208020000"
    "007bba1feb0000000c4944415408d763f8cfc0f01f0005050202b8dc"
    "7bb50000000049454e44ae426082"
)


def _project_path(c: TestClient, headers: dict) -> Path:
    return Path(c.get("/api/projects", headers=headers).json()["projects"][0]["path"])


def test_design_from_image_seeds_full_bleed_scene(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    root = _project_path(c, headers)
    img = root / "artifacts/media/images/chat-1.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(PNG_1x2)

    res = c.post("/api/projects/demo/designs/from-image", headers=headers, json={"path": "artifacts/media/images/chat-1.png"})

    assert res.status_code == 200, res.text
    body = res.json()
    scene = json.loads((root / body["path"] / "scene.json").read_text())
    ab = scene["artboards"][0]
    assert ab["width"] == 1 and ab["height"] == 2  # dims read from the PNG header
    layer = ab["layers"][0]
    assert layer["type"] == "image" and layer["src"] == "artifacts/media/images/chat-1.png"
    assert layer["width"] == ab["width"] and layer["height"] == ab["height"]  # full-bleed


def test_design_from_image_missing_file_404(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    res = c.post("/api/projects/demo/designs/from-image", headers=headers, json={"path": "artifacts/nope.png"})
    assert res.status_code == 404


def test_design_image_edit_uses_codex_directly(tmp_path, monkeypatch):
    """Codex now supports reference images, so an edit request with codex selected goes
    straight to codex — no xAI fallback — and plain text→image is codex too."""
    from proxima_api import image_providers

    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    root = _project_path(c, headers)
    img = root / "artifacts/media/images/ref.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(PNG_1x2)

    calls: list[str] = []

    def fake_generate(provider_id, key, **kwargs):
        calls.append(provider_id)
        return b"png-bytes"

    monkeypatch.setattr(image_providers, "generate", fake_generate)
    # Even with an xAI OAuth available, codex handles the edit itself now (imageEdit=True),
    # so the fallback never triggers.
    monkeypatch.setattr(image_providers, "xai_oauth_ready", lambda: {"ready": True, "detail": "ok"})
    res = c.post("/api/projects/demo/design/image", headers=headers, json={"prompt": "variation", "image": "artifacts/media/images/ref.png"})
    assert res.status_code == 200, res.text
    assert calls == ["codex"]  # codex edits directly, no fallback

    # plain text→image is codex too
    calls.clear()
    res3 = c.post("/api/projects/demo/design/image", headers=headers, json={"prompt": "fresh image"})
    assert res3.status_code == 200 and calls == ["codex"]

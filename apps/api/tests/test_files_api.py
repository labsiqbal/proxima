from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from proxima_api import app_settings
from proxima_api.main import create_app


def client(tmp_path: Path) -> TestClient:
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_video": True,
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


def test_video_generation_backend_picker_defaults_and_saves(tmp_path):
    c = client(tmp_path)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    body = c.get("/api/settings/video-gen", headers=headers).json()
    assert body["provider"] == "xai-oauth"
    assert [p["id"] for p in body["providers"]] == ["xai-oauth", "higgsfield"]

    saved = c.put("/api/settings/video-gen", headers=headers, json={"provider": "higgsfield", "model": "ray-2", "videoPolicy": "allow-with-limit", "maxVideoCredits": 12})
    assert saved.status_code == 200
    assert saved.json()["provider"] == "higgsfield"
    assert saved.json()["model"] == "ray-2"
    assert saved.json()["videoPolicy"] == "allow-with-limit"


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


def test_video_project_create_list_and_artifact_scan(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)

    res = c.post("/api/projects/demo/videos", headers=headers, json={"title": "Launch Reel", "brief": "Short promo"})
    assert res.status_code == 200
    body = res.json()
    assert body["path"].startswith("artifacts/video/")

    listed = c.get("/api/projects/demo/videos", headers=headers).json()["videos"]
    assert any(v["id"] == body["id"] and v["title"] == "Launch Reel" for v in listed)
    assert any(v["id"] == body["id"] and v["width"] == 1080 and v["height"] == 1920 for v in listed)

    arts = c.get("/api/projects/demo/artifacts?since_minutes=1440", headers=headers).json()["artifacts"]
    assert any(a["type"] == "video" and a["path"] == body["path"] for a in arts)

    demo = next(p for p in c.get("/api/projects", headers=headers).json()["projects"] if p["slug"] == "demo")
    render = Path(demo["path"]) / body["path"] / "renders" / "demo.mp4"
    render.parent.mkdir(parents=True, exist_ok=True)
    render.write_bytes(b"mp4")
    arts = c.get("/api/projects/demo/artifacts?since_minutes=1440", headers=headers).json()["artifacts"]
    assert any(a["type"] == "video-file" and a["path"].endswith("/renders/demo.mp4") for a in arts)

    deleted = c.delete(f"/api/projects/demo/videos/{body['id']}", headers=headers)
    assert deleted.status_code == 200
    assert not (Path(demo["path"]) / body["path"]).exists()
    listed = c.get("/api/projects/demo/videos", headers=headers).json()["videos"]
    assert all(v["id"] != body["id"] for v in listed)


def test_video_lint_and_render_settings_validation(tmp_path, monkeypatch):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    body = c.post("/api/projects/demo/videos", headers=headers, json={"title": "Lintable Reel"}).json()
    monkeypatch.setattr("proxima_api.routes.files.shutil.which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(
        "proxima_api.routes.files.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="lint ok", stderr=""),
    )

    linted = c.post(f"/api/projects/demo/videos/{body['id']}/lint", headers=headers)
    assert linted.status_code == 200
    assert linted.json()["ok"] is True
    assert "lint ok" in linted.json()["log"]

    bad = c.post(f"/api/projects/demo/videos/{body['id']}/render", headers=headers, json={"format": "avi"})
    assert bad.status_code == 400
    assert "format" in bad.json()["detail"]


def test_video_studio_render_job_proxy_routes(tmp_path, monkeypatch):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    video = c.post("/api/projects/demo/videos", headers=headers, json={"title": "Render Proxy"}).json()
    studio_id = f"proxima-video__demo__{video['id']}"

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == f"/api/projects/{video['id']}/render":
            return httpx.Response(200, json={"jobId": "job-proxy-1"})
        if request.method == "GET" and request.url.path == "/api/render/job-proxy-1/progress":
            return httpx.Response(200, content=b'event: progress\ndata: {"status":"complete","progress":100}\n\n', headers={"Content-Type": "text/event-stream"})
        if request.method == "GET" and request.url.path == f"/api/projects/{video['id']}/renders/file/demo.mp4":
            return httpx.Response(200, content=b"mp4", headers={"Content-Type": "video/mp4"})
        if request.method == "DELETE" and request.url.path == "/api/render/job-proxy-1":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(upstream)

    def mock_async_client(*args, **kwargs):
        return real_async_client(*args, **kwargs, transport=transport)

    monkeypatch.setattr("proxima_api.routes.files.httpx.AsyncClient", mock_async_client)
    c.app.state.app_manager._apps["demo"] = {
        "proc": SimpleNamespace(returncode=None),
        "port": 3999,
        "command": "fake",
        "log": [],
    }

    started = c.post(f"/api/projects/{studio_id}/render", headers=headers, json={"format": "mp4"})
    assert started.status_code == 200
    assert started.json()["jobId"] == "job-proxy-1"

    progress = c.get("/api/render/job-proxy-1/progress", headers=headers)
    assert progress.status_code == 200
    assert '"status":"complete"' in progress.text

    render_file = c.get(f"/api/projects/{studio_id}/renders/file/demo.mp4", headers=headers)
    assert render_file.status_code == 200
    assert render_file.content == b"mp4"

    deleted = c.delete("/api/render/job-proxy-1", headers=headers)
    assert deleted.status_code == 200


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


def test_video_import_file_copies_into_assets(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    root = _project_path(c, headers)
    img = root / "artifacts/media/images/chat-2.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(PNG_1x2)
    vid = c.post("/api/projects/demo/videos", headers=headers, json={"title": "Promo"}).json()["id"]

    res = c.post(f"/api/projects/demo/videos/{vid}/import-file", headers=headers, json={"path": "artifacts/media/images/chat-2.png"})

    assert res.status_code == 200, res.text
    assert res.json()["path"] == "assets/chat-2.png"
    assert (root / f"artifacts/video/{vid}/assets/chat-2.png").read_bytes() == PNG_1x2
    # second import of the same name gets a deduped filename
    res2 = c.post(f"/api/projects/demo/videos/{vid}/import-file", headers=headers, json={"path": "artifacts/media/images/chat-2.png"})
    assert res2.json()["path"] == "assets/chat-2-1.png"


def test_video_import_file_rejects_missing_video_and_non_media(tmp_path):
    c = client(tmp_path)
    headers = setup_project(c, tmp_path)
    root = _project_path(c, headers)
    (root / "notes.txt").write_text("hi")
    assert c.post("/api/projects/demo/videos/nope/import-file", headers=headers, json={"path": "notes.txt"}).status_code == 404
    vid = c.post("/api/projects/demo/videos", headers=headers, json={"title": "Promo"}).json()["id"]
    assert c.post(f"/api/projects/demo/videos/{vid}/import-file", headers=headers, json={"path": "notes.txt"}).status_code == 400


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

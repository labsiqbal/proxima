from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from proxima_api import moodboard, run_prompting, wiki_memory
from proxima_api.main import create_app


def _client(tmp_path: Path, *, enabled: bool = True) -> tuple[object, TestClient, dict[str, str]]:
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_design_studio": enabled,
    })
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    return app, client, {"Authorization": f"Bearer {token}"}


def _preview(url: str) -> dict:
    return {
        "url": f"https://{url.strip('/').removeprefix('https://')}/",
        "siteName": "inspiration.test",
        "title": "A strong hero",
        "faviconUrl": "https://inspiration.test/favicon.ico",
        "imageBytes": b"preview",
        "imageMime": "image/png",
        "warning": "",
    }


def test_moodboard_link_crud_and_project_isolation(tmp_path, monkeypatch):
    app, client, headers = _client(tmp_path)
    first = client.post("/api/projects", headers=headers, json={"slug": "first", "name": "First"}).json()
    client.post("/api/projects", headers=headers, json={"slug": "second", "name": "Second"})
    monkeypatch.setattr(moodboard, "fetch_link_preview", _preview)

    created = client.post(
        "/api/projects/first/design/moodboard",
        headers=headers,
        json={"url": "inspiration.test", "note": "Borrow the type scale", "tags": ["Hero", "#Dark"]},
    )
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item["siteName"] == "inspiration.test"
    assert item["tags"] == ["hero", "dark"]
    assert item["imagePath"].startswith("artifacts/moodboard/images/")
    assert Path(first["path"], item["imagePath"]).read_bytes() == b"preview"

    assert client.get("/api/projects/second/design/moodboard", headers=headers).json() == {"items": []}
    listed = client.get("/api/projects/first/design/moodboard", headers=headers).json()["items"]
    assert [candidate["id"] for candidate in listed] == [item["id"]]

    updated = client.patch(
        f"/api/projects/first/design/moodboard/{item['id']}",
        headers=headers,
        json={"note": "Use the spacious composition", "tags": "SaaS, Hero", "useAsReference": True},
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["tags"] == ["saas", "hero"]
    assert updated.json()["item"]["useAsReference"] is True

    removed = client.delete(f"/api/projects/first/design/moodboard/{item['id']}", headers=headers)
    assert removed.status_code == 200
    assert not Path(first["path"], item["imagePath"]).exists()
    assert client.get("/api/projects/first/design/moodboard", headers=headers).json() == {"items": []}
    app.state.db.close()


def test_moodboard_upload_and_failed_preview_fallback(tmp_path, monkeypatch):
    app, client, headers = _client(tmp_path)
    project = client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"}).json()
    root = Path(project["path"])
    upload = root / "artifacts/moodboard/images/upload.png"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"image")

    uploaded = client.post(
        "/api/projects/demo/design/moodboard",
        headers=headers,
        json={"imagePath": "artifacts/moodboard/images/upload.png", "siteName": "Uploaded screenshot"},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["item"]["kind"] == "upload"

    monkeypatch.setattr(moodboard, "fetch_link_preview", lambda url: {
        **_preview(url),
        "imageBytes": None,
        "imageMime": "",
        "warning": "Preview unavailable: timed out",
    })
    fallback = client.post(
        "/api/projects/demo/design/moodboard",
        headers=headers,
        json={"url": "slow.test"},
    )
    assert fallback.status_code == 200
    assert fallback.json()["warning"] == "Preview unavailable: timed out"
    assert fallback.json()["item"]["imagePath"] is None
    assert len(client.get("/api/projects/demo/design/moodboard", headers=headers).json()["items"]) == 2
    app.state.db.close()


def test_moodboard_routes_follow_design_studio_feature_gate(tmp_path):
    app, client, headers = _client(tmp_path, enabled=False)
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    response = client.get("/api/projects/demo/design/moodboard", headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "feature_disabled"
    app.state.db.close()


def test_selected_moodboard_reference_uses_preamble_and_vision_path(tmp_path):
    project = tmp_path / "project"
    wiki = project / "wiki"
    wiki.mkdir(parents=True)
    image = project / "artifacts/moodboard/images/ref.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    moodboard.write_items(project, [{
        "id": "mb-1",
        "siteName": "example.test",
        "url": "https://example.test/",
        "imagePath": "artifacts/moodboard/images/ref.png",
        "note": "Use the quiet spacing",
        "tags": ["minimal"],
        "useAsReference": True,
    }])

    references = wiki_memory.read_moodboard_references(project)
    preamble = wiki_memory.build_run_preamble(
        "Demo",
        "demo",
        wiki,
        include_design_studio=True,
        moodboard_references=references,
    )
    assert "Selected Moodboard references" in preamble
    assert "Use the quiet spacing" in preamble

    prompting = run_prompting.RunPrompting(SimpleNamespace(state=SimpleNamespace(
        config={"feature_design_studio": True},
        worker_db=None,
    )))
    prompt = prompting.build_prompt_text(
        {"prompt": "Update the hero\n\n⟦VISION:assets/logo.png⟧", "kind": "chat"},
        1,
        "Demo",
        "demo",
        wiki,
        False,
        False,
        None,
        "design",
        False,
    )
    assert "Selected Moodboard references" in prompt
    assert prompt.count("⟦VISION:") == 1
    assert "assets/logo.png|artifacts/moodboard/images/ref.png" in prompt


def test_private_preview_urls_are_rejected(monkeypatch):
    monkeypatch.setattr(
        moodboard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(moodboard.socket.AF_INET, moodboard.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    try:
        moodboard.normalize_public_url("http://internal.test")
    except ValueError as exc:
        assert "Private or local" in str(exc)
    else:
        raise AssertionError("private URL was accepted")


def test_unresolvable_preview_host_returns_fallback(monkeypatch):
    def unresolved(*args, **kwargs):
        raise moodboard.socket.gaierror("not found")

    monkeypatch.setattr(moodboard.socket, "getaddrinfo", unresolved)
    preview = moodboard.fetch_link_preview("offline.test")
    assert preview["url"] == "https://offline.test/"
    assert preview["siteName"] == "offline.test"
    assert preview["imageBytes"] is None
    assert preview["warning"].startswith("Preview unavailable:")

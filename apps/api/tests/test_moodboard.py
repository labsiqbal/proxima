from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from proxima_api import moodboard, run_prompting, wiki_memory
from proxima_api.main import create_app


def _addrinfo(ip: str, port: int = 443) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, headers: dict | None = None, body: bytes = b"") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise moodboard.httpx.HTTPError(f"status {self.status_code}")

    def iter_bytes(self):
        yield self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def stream(self, method: str, url: str, *, headers: dict | None = None, extensions: dict | None = None):
        self.calls.append({"url": url, "headers": headers or {}, "extensions": extensions or {}})
        return self._responses.pop(0)


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


def test_bounded_get_pins_validated_ip_against_dns_rebinding(monkeypatch):
    answers = [_addrinfo("93.184.216.34"), _addrinfo("127.0.0.1")]
    calls = {"n": 0}

    def rebinding(*args, **kwargs):
        result = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        return result

    monkeypatch.setattr(moodboard.socket, "getaddrinfo", rebinding)
    client = _FakeClient([_FakeResponse(headers={"content-type": "text/html"}, body=b"<title>ok</title>")])

    body, mime, final = moodboard._bounded_get(client, "https://rebind.test/", max_bytes=1000)

    assert body == b"<title>ok</title>"
    assert final == "https://rebind.test/"
    assert client.calls[0]["url"] == "https://93.184.216.34/"
    assert client.calls[0]["headers"]["Host"] == "rebind.test"
    assert client.calls[0]["extensions"]["sni_hostname"] == "rebind.test"
    assert calls["n"] == 1


def test_bounded_get_rejects_redirect_to_internal_host(monkeypatch):
    def resolve(host, *args, **kwargs):
        return _addrinfo("93.184.216.34") if host == "public.test" else _addrinfo("169.254.169.254")

    monkeypatch.setattr(moodboard.socket, "getaddrinfo", resolve)
    client = _FakeClient([_FakeResponse(status_code=302, headers={"location": "http://metadata.internal/latest"})])

    with pytest.raises(moodboard.UnsafeUrlError):
        moodboard._bounded_get(client, "https://public.test/", max_bytes=1000)


def test_remote_svg_preview_image_is_rejected(monkeypatch):
    monkeypatch.setattr(moodboard.socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))

    def fake_bounded_get(client, url, *, max_bytes):
        if max_bytes == moodboard.MAX_HTML_BYTES:
            return (b'<meta property="og:image" content="/logo.svg">', "text/html", "https://svg.test/")
        return (b"<svg onload=alert(1)></svg>", "image/svg+xml", "https://svg.test/logo.svg")

    monkeypatch.setattr(moodboard, "_bounded_get", fake_bounded_get)
    preview = moodboard.fetch_link_preview("svg.test")

    assert preview["imageBytes"] is None
    assert preview["imageMime"] == ""
    assert "unsupported format" in preview["warning"]


def test_cache_preview_image_refuses_remote_svg(tmp_path):
    assert moodboard.cache_preview_image(tmp_path, "mb-svg", b"<svg></svg>", "image/svg+xml") is None
    assert not (tmp_path / moodboard.IMAGE_DIR).exists()

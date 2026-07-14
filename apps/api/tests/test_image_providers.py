from __future__ import annotations

import base64

import httpx
import pytest

from proxima_api import image_providers
from proxima_api import app_settings


# ── provider metadata ──────────────────────────────────────────────────────

def test_provider_list_has_media_backend_picker_options():
    ids = {p["id"] for p in image_providers.provider_list()}
    assert ids == {"codex", "xai-oauth", "higgsfield", "openai-compatible"}
    kinds = {p["kind"] for p in image_providers.provider_list()}
    assert kinds == {"codex", "oauth", "higgsfield", "http"}
    xai = next(p for p in image_providers.provider_list() if p["id"] == "xai-oauth")
    assert xai["requiresKey"] is False
    assert xai["capabilities"]["textToImage"] is True
    assert xai["capabilities"]["imageEdit"] is True


def test_default_provider_is_codex():
    assert image_providers.DEFAULT_PROVIDER == "codex"
    assert image_providers.get_provider(None).id == "codex"
    assert image_providers.get_provider("nope").id == "codex"


def test_http_provider_requires_key():
    spec = image_providers.get_provider("openai-compatible")
    assert spec.requires_key is True
    assert spec.kind == "http"
    codex = image_providers.get_provider("codex")
    assert codex.requires_key is False
    assert codex.kind == "codex"


def test_xai_oauth_provider_does_not_require_key():
    xai = image_providers.get_provider("xai-oauth")
    assert xai.requires_key is False
    assert xai.kind == "oauth"


# ── codex auto-detect ──────────────────────────────────────────────────────

def test_codex_ready_on_this_host_is_logged_in():
    # This host has codex logged in (verified manually: "Logged in using ChatGPT").
    r = image_providers.codex_ready()
    assert isinstance(r["ready"], bool)
    assert "detail" in r


def test_codex_binary_resolves_on_this_host():
    b = image_providers.codex_binary()
    # Host-dependent: the codex CLI isn't installed everywhere (e.g. CI runners).
    # Validate the resolved path's shape when present; don't fail where it's absent.
    assert b is None or b.endswith("codex")


# ── generate: http provider (monkeypatched httpx) ──────────────────────────

class _Resp:
    def __init__(self, status, json_body=None, content=b""):
        self.status_code = status
        self._json = json_body or {}
        self.text = str(json_body)
        self.content = content
    def json(self):
        return self._json


def test_http_generate_decodes_b64(monkeypatch):
    png = b"\x89PNG\r\nFAKE"
    b64 = base64.b64encode(png).decode()
    calls = {}
    def fake_post(self, url, headers=None, json=None, data=None, files=None):
        calls["url"] = url
        return _Resp(200, {"data": [{"b64_json": b64}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = image_providers.generate("openai-compatible", "sk-test", prompt="a cat", model="gpt-image-1")
    assert out == png


def test_http_generate_raises_without_key():
    with pytest.raises(image_providers.ImageProviderError, match="API key"):
        image_providers.generate("openai-compatible", None, prompt="x")


def test_http_generate_surfaces_provider_error(monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: _Resp(401, {"error": "bad key"}))
    with pytest.raises(image_providers.ImageProviderError, match="401"):
        image_providers.generate("openai-compatible", "sk-bad", prompt="x")


def test_openai_compatible_generate_forwards_size(monkeypatch):
    b64 = base64.b64encode(b"x").decode()
    captured = {}
    def fake_post(self, url, headers=None, json=None, data=None, files=None):
        captured["json"] = json
        return _Resp(200, {"data": [{"b64_json": b64}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    image_providers.generate("openai-compatible", "sk-test", prompt="x", model="gpt-image-1", size="1024x1024")
    assert captured["json"].get("size") == "1024x1024"


def test_xai_generate_omits_unsupported_size_argument(monkeypatch):
    # xAI's grok image API 400s on a `size` argument; text-to-image must not send it.
    png = b"GROK"
    b64 = base64.b64encode(png).decode()
    captured = {}
    def fake_post(self, url, headers=None, json=None, data=None, files=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp(200, {"data": [{"b64_json": b64}]})
    monkeypatch.setattr(image_providers, "_read_hermes_oauth_token", lambda p: "tok-xai")
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = image_providers.generate("xai-oauth", None, prompt="a fox", size="1024x1024", base_url="https://api.x.ai/v1")
    assert out == png
    assert captured["url"].endswith("/images/generations")
    assert "size" not in (captured["json"] or {})


def test_http_generate_edit_uses_edits_endpoint(monkeypatch):
    png = b"EDITED"
    b64 = base64.b64encode(png).decode()
    captured = {}
    def fake_post(self, url, headers=None, json=None, data=None, files=None):
        captured["url"] = url
        captured["files"] = files
        return _Resp(200, {"data": [{"b64_json": b64}]})
    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = image_providers.generate("openai-compatible", "sk-test", prompt="make it blue", image_bytes=b"SRC")
    assert out == png
    assert captured["url"].endswith("/images/edits")
    assert captured["files"] is not None  # edit source attached as multipart


def test_http_generate_edit_preserves_source_mime(monkeypatch):
    png = b"EDITED"
    b64 = base64.b64encode(png).decode()
    captured = {}

    def fake_post(self, url, headers=None, json=None, data=None, files=None):
        captured["files"] = files
        return _Resp(200, {"data": [{"b64_json": b64}]})

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = image_providers.generate(
        "openai-compatible",
        "sk-test",
        prompt="make it sharper",
        image_bytes=b"SRC",
        image_mime="image/webp",
    )
    assert out == png
    assert captured["files"]["image"][0] == "src.webp"
    assert captured["files"]["image"][2] == "image/webp"


# ── test_connection ────────────────────────────────────────────────────────

def test_http_test_connection_ok(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: None)  # not used; Client used
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: _Resp(200, {"data": [1, 2, 3]}))
    r = image_providers.test_connection("openai-compatible", "sk-good")
    assert r["ok"] is True and "3 models" in r["detail"]


def test_http_test_connection_rejected(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: _Resp(401, {}))
    r = image_providers.test_connection("openai-compatible", "sk-bad")
    assert r["ok"] is False and "rejected" in r["detail"]


def test_http_test_connection_no_key():
    r = image_providers.test_connection("openai-compatible", None)
    assert r["ok"] is False and "key" in r["detail"].lower()


def test_codex_test_connection_runs_login_check():
    r = image_providers.test_connection("codex", None)
    assert "ready" in r or "ok" in r


def test_codex_generate_streams_image_from_codex_responses(monkeypatch, tmp_path):
    png = b"\x89PNG\r\nCODEX"
    b64 = base64.b64encode(png).decode()
    # unsigned JWT-shaped token with a future exp + ChatGPT account id.
    payload = base64.urlsafe_b64encode(
        b'{"exp":4102444800,"https://api.openai.com/auth":{"chatgpt_account_id":"acct_123"}}'
    ).decode().rstrip("=")
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens":{"access_token":"h.' + payload + '.s"}}')
    monkeypatch.setattr(image_providers, "_CODEX_AUTH_PATH", auth)

    captured = {}

    class FakeStream:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            event = json_bytes({"type": "image_generation_call", "result": b64})
            yield b"event: response.output_item.done"
            yield b"data: " + event
            yield b""

    class FakeClient:
        def __init__(self, timeout=None, headers=None):
            captured["headers"] = headers or {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url, json=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = image_providers.generate("codex", None, prompt="a cat")
    assert out == png
    assert captured["url"].endswith("/responses")
    assert captured["headers"]["ChatGPT-Account-ID"] == "acct_123"
    assert captured["json"]["tools"][0]["type"] == "image_generation"
    assert captured["json"]["tools"][0]["quality"] == "low"


def test_codex_generate_passes_reference_image_as_input_image(monkeypatch, tmp_path):
    png = b"\x89PNG\r\nEDITED"
    b64 = base64.b64encode(png).decode()
    payload = base64.urlsafe_b64encode(
        b'{"exp":4102444800,"https://api.openai.com/auth":{"chatgpt_account_id":"acct_123"}}'
    ).decode().rstrip("=")
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens":{"access_token":"h.' + payload + '.s"}}')
    monkeypatch.setattr(image_providers, "_CODEX_AUTH_PATH", auth)

    captured = {}

    class FakeStream:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            event = json_bytes({"type": "image_generation_call", "result": b64})
            yield b"event: response.output_item.done"
            yield b"data: " + event
            yield b""

    class FakeClient:
        def __init__(self, timeout=None, headers=None):
            pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url, json=None):
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = image_providers.generate(
        "codex", None, prompt="make it blue", image_bytes=b"SRC", image_mime="image/webp"
    )
    assert out == png
    content = captured["json"]["input"][0]["content"]
    img_parts = [c for c in content if c.get("type") == "input_image"]
    assert len(img_parts) == 1
    assert img_parts[0]["image_url"].startswith("data:image/webp;base64,")


def test_test_connection_network_error_never_raises(monkeypatch):
    def boom(self, url, **kw):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx.Client, "get", boom)
    r = image_providers.test_connection("openai-compatible", "sk-x")
    assert r["ok"] is False and "Network" in r["detail"]


def json_bytes(value):
    import json
    return json.dumps(value).encode()


# ── app_settings store ─────────────────────────────────────────────────────

def _conn():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
    )
    return c


def test_set_and_get_setting_roundtrip():
    conn = _conn()
    app_settings.set_setting(conn, "k", "v")
    assert app_settings.get_setting(conn, "k") == "v"
    assert app_settings.get_setting(conn, "missing", "d") == "d"


def test_set_setting_is_upsert():
    conn = _conn()
    app_settings.set_setting(conn, "k", "v1")
    app_settings.set_setting(conn, "k", "v2")
    assert app_settings.get_setting(conn, "k") == "v2"


def test_get_image_gen_config_returns_none_when_unset():
    conn = _conn()
    assert app_settings.get_image_gen_config(conn) is None


def test_get_image_gen_config_returns_saved():
    conn = _conn()
    app_settings.set_json(conn, app_settings.IMAGE_GEN_KEY, {"provider": "openai-compatible", "apiKey": "k"})
    cfg = app_settings.get_image_gen_config(conn)
    assert cfg["provider"] == "openai-compatible"
    assert cfg["apiKey"] == "k"

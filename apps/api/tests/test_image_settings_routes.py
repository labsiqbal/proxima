from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import image_providers
from proxima_api.main import create_app


def _client(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_design_studio": True,
    })
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def test_image_gen_settings_default_codex(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/settings/image-gen")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["provider"] == "codex"
    assert data["defaultProvider"] == "codex"
    assert {p["id"] for p in data["providers"]} == {"codex", "xai-oauth", "higgsfield", "openai-compatible"}
    assert any(p["id"] == "xai-oauth" and p["kind"] == "oauth" for p in data["providers"])
    assert "codexReady" in data


def test_image_gen_test_codex_accepts_ready_shape(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/settings/image-gen/test", json={"provider": "codex"})
    assert r.status_code == 200, r.text
    data = r.json()
    # Codex readiness returns {ready, detail}, not {ok, detail}; the route must
    # accept both shapes without KeyError.
    assert "detail" in data
    assert "ready" in data or "ok" in data


def test_image_gen_save_openai_compatible_masks_key(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/settings/image-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://example.test/v1",
        "model": "image-model",
        "apiKey": "secret-key",
    })
    assert r.status_code == 200, r.text
    assert r.json()["hasApiKey"] is True

    data = c.get("/api/settings/image-gen").json()
    assert data["provider"] == "openai-compatible"
    assert data["baseUrl"] == "https://example.test/v1"
    assert data["model"] == "image-model"
    assert data["hasApiKey"] is True
    assert "secret-key" not in str(data)


def test_image_gen_partial_save_preserves_existing_provider_fields(tmp_path):
    c = _client(tmp_path)
    assert c.put("/api/settings/image-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://example.test/v1",
        "model": "image-model",
        "apiKey": "secret-key",
    }).status_code == 200

    r = c.put("/api/settings/image-gen", json={"apiKey": "rotated-key"})
    assert r.status_code == 200, r.text

    data = c.get("/api/settings/image-gen").json()
    assert data["provider"] == "openai-compatible"
    assert data["baseUrl"] == "https://example.test/v1"
    assert data["model"] == "image-model"
    assert data["hasApiKey"] is True
    assert "rotated-key" not in str(data)


def test_image_gen_switch_to_codex_clears_http_fields(tmp_path):
    c = _client(tmp_path)
    assert c.put("/api/settings/image-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://example.test/v1",
        "model": "image-model",
        "apiKey": "secret-key",
    }).status_code == 200

    r = c.put("/api/settings/image-gen", json={"provider": "codex", "baseUrl": None, "model": None, "apiKey": None})
    assert r.status_code == 200, r.text

    data = c.get("/api/settings/image-gen").json()
    assert data["provider"] == "codex"
    assert data["baseUrl"] is None
    assert data["model"] is None


def test_image_gen_rejects_unknown_provider(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/settings/image-gen", json={"provider": "bogus"})
    assert r.status_code == 400


def test_design_image_edit_passes_source_mime(tmp_path, monkeypatch):
    c = _client(tmp_path)
    c.put("/api/settings/image-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://example.test/v1",
        "model": "image-model",
        "apiKey": "secret-key",
    })
    slug = c.get("/api/projects").json()["projects"][0]["slug"]
    project_path = c.app.state.db.execute("SELECT path FROM projects WHERE slug = ?", (slug,)).fetchone()["path"]
    source = Path(project_path) / "artifacts/design/_assets/source.webp"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"WEBP")

    captured = {}

    def fake_generate(provider_id, key, **kwargs):
        captured.update(kwargs)
        return b"\x89PNG\r\nfake"

    monkeypatch.setattr(image_providers, "generate", fake_generate)
    r = c.post(
        f"/api/projects/{slug}/design/image",
        json={"prompt": "make it pop", "image": "artifacts/design/_assets/source.webp"},
    )

    assert r.status_code == 200, r.text
    assert captured["image_bytes"] == b"WEBP"
    assert captured["image_mime"] == "image/webp"


def test_design_image_edit_bad_source_returns_400(tmp_path, monkeypatch):
    c = _client(tmp_path)
    c.put("/api/settings/image-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://example.test/v1",
        "model": "image-model",
        "apiKey": "secret-key",
    })
    slug = c.get("/api/projects").json()["projects"][0]["slug"]

    def should_not_generate(*_args, **_kwargs):
        raise AssertionError("provider should not be called for invalid source image")

    monkeypatch.setattr(image_providers, "generate", should_not_generate)
    missing = c.post(
        f"/api/projects/{slug}/design/image",
        json={"prompt": "edit", "image": "artifacts/design/_assets/missing.webp"},
    )
    escaping = c.post(
        f"/api/projects/{slug}/design/image",
        json={"prompt": "edit", "image": "../secret.png"},
    )

    assert missing.status_code == 400
    assert escaping.status_code == 400

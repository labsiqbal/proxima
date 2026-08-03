from __future__ import annotations

import json

from fastapi.testclient import TestClient

from proxima_api import app_settings, video_providers
from proxima_api.main import create_app


def _client(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def test_video_gen_settings_default_is_the_openai_compatible_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/settings/video-gen")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["provider"] == "openai-compatible"
    assert data["defaultProvider"] == "openai-compatible"
    assert {p["id"] for p in data["providers"]} == set(video_providers.VIDEO_PROVIDER_IDS)
    assert data["hasApiKey"] is False
    assert data["baseUrl"] == "https://api.openai.com/v1"


def test_video_gen_save_round_trips_and_keeps_the_key_on_empty_submit(tmp_path):
    c = _client(tmp_path)
    saved = c.put("/api/settings/video-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://api.linc.id/v1",
        "model": "xai/grok-imagine-video",
        "apiKey": "sk-secret",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["hasApiKey"] is True

    data = c.get("/api/settings/video-gen").json()
    assert data["baseUrl"] == "https://api.linc.id/v1"
    assert data["model"] == "xai/grok-imagine-video"
    assert data["hasApiKey"] is True
    assert "apiKey" not in data  # the key never travels back to the browser

    # An empty apiKey on a later save must not erase the stored key.
    c.put("/api/settings/video-gen", json={"provider": "openai-compatible", "model": "other-video", "apiKey": ""})
    again = c.get("/api/settings/video-gen").json()
    assert again["hasApiKey"] is True
    assert again["model"] == "other-video"


def test_video_gen_save_leaves_the_image_gen_row_untouched(tmp_path):
    """Video settings are a sibling row, not a rewrite of the working image row."""
    c = _client(tmp_path)
    c.put("/api/settings/image-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://api.linc.id/v1",
        "model": "xai/grok-imagine-image",
        "apiKey": "sk-image",
    })
    c.put("/api/settings/video-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://api.linc.id/v1",
        "model": "xai/grok-imagine-video",
        "apiKey": "sk-video",
    })
    conn = c.app.state.db
    image_cfg = app_settings.get_json(conn, app_settings.IMAGE_GEN_KEY)
    video_cfg = app_settings.get_json(conn, app_settings.VIDEO_GEN_KEY)
    assert image_cfg["model"] == "xai/grok-imagine-image"
    assert image_cfg["apiKey"] == "sk-image"
    assert video_cfg["model"] == "xai/grok-imagine-video"
    assert video_cfg["apiKey"] == "sk-video"


def test_video_gen_rejects_an_unknown_provider(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/settings/video-gen", json={"provider": "nope"})
    assert r.status_code == 400
    assert "nope" in r.json()["detail"]


def test_video_gen_test_connection_reports_a_missing_key(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/settings/video-gen/test", json={"provider": "openai-compatible", "baseUrl": "https://api.linc.id/v1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is False
    assert "API key" in data["detail"]


def test_video_gen_test_is_audited_with_flat_metadata(tmp_path):
    c = _client(tmp_path)
    c.post("/api/settings/video-gen/test", json={"provider": "openai-compatible"})
    row = c.app.state.db.execute(
        "SELECT action, target_type, target_id, metadata FROM audit_log "
        "WHERE action = 'settings.video_gen.test' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["target_type"] == "settings"
    assert row["target_id"] == "video_gen"
    assert json.loads(row["metadata"])["provider"] == "openai-compatible"


def test_video_gen_save_never_audits_the_api_key(tmp_path):
    c = _client(tmp_path)
    c.put("/api/settings/video-gen", json={
        "provider": "openai-compatible",
        "baseUrl": "https://api.linc.id/v1",
        "apiKey": "sk-do-not-log",
    })
    row = c.app.state.db.execute(
        "SELECT metadata FROM audit_log WHERE action = 'settings.video_gen' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert "sk-do-not-log" not in row["metadata"]
    assert json.loads(row["metadata"])["key_set"] is True

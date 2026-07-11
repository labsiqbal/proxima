from __future__ import annotations

from importlib import import_module

from proxima_api.main import create_app


def _dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "icons").mkdir()
    (dist / "index.html").write_text("<html><head><title>Proxima</title></head><body></body></html>", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text('{"name":"Proxima"}', encoding="utf-8")
    (dist / "sw.js").write_text("self.addEventListener('install', () => {})", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (dist / "icons" / "icon-192.png").write_bytes(b"not-a-real-png")
    return dist


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "web_dist_path": str(_dist(tmp_path)),
        "env_name": "STAGING",
    })


def test_frontend_shell_routes_and_cache_headers(tmp_path):
    test_client = import_module("fastapi.testclient").TestClient
    c = test_client(_app(tmp_path))

    index = c.get("/")
    assert index.status_code == 200
    assert "<title>Proxima · STAGING</title>" in index.text
    assert index.headers["cache-control"] == "no-cache, must-revalidate"

    manifest = c.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["cache-control"] == "no-cache, must-revalidate"

    sw = c.get("/sw.js")
    assert sw.status_code == 200
    assert sw.headers["cache-control"] == "no-cache, must-revalidate"

    asset = c.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

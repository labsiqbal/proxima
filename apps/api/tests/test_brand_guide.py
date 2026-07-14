from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from proxima_api import brand_extract
from proxima_api.main import create_app


class _Resp:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status


def test_fetch_url_digest_extracts_colors_fonts_title(monkeypatch):
    html = """
      <html><head><title>Acme &amp; Co</title>
      <meta name="description" content="Bold coffee brand">
      <meta property="og:image" content="https://acme.test/hero.jpg">
      <style>body{font-family:"Space Grotesk",sans-serif;color:#FF6A00;background:#111111}
      .a{color:#ff6a00}</style></head><body><h1>Acme</h1><p>Real copy here.</p></body></html>
    """
    monkeypatch.setattr(httpx.Client, "get", lambda self, url: _Resp(html))
    d = brand_extract.fetch_url_digest("acme.test")
    assert d["ok"] is True
    assert d["title"] == "Acme & Co"
    assert "#FF6A00" in d["colors"] or "#ff6a00" in d["colors"]
    assert "Space Grotesk" in d["fonts"]
    assert "sans-serif" not in [f.lower() for f in d["fonts"]]  # generic families dropped
    assert d["ogImage"] == "https://acme.test/hero.jpg"


def test_fetch_url_digest_never_raises_on_failure(monkeypatch):
    def boom(self, url):
        raise httpx.ConnectError("nope")
    monkeypatch.setattr(httpx.Client, "get", boom)
    d = brand_extract.fetch_url_digest("https://down.test")
    assert d["ok"] is False and "error" in d
    # A failed digest still renders a useful markdown hint.
    assert "could not fetch" in brand_extract.digest_to_markdown(d)


def test_brand_guide_endpoint_queues_run_with_synthesis_prompt(tmp_path, monkeypatch):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_design_studio": True,
    })
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {client.post('/auth/auto').json()['token']}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})

    monkeypatch.setattr(brand_extract, "fetch_url_digest", lambda u, **k: {"url": u, "ok": True, "title": "Ref", "colors": ["#FF6A00"], "fonts": ["Anton"], "description": "", "ogImage": "", "text": "hi"})
    res = client.post("/api/projects/demo/design/brand-guide", headers=headers, json={"urls": ["brand.test"], "notes": "earthy, premium"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["run_id"] and body["session_id"]
    assert body["urls"] == [{"url": "brand.test", "ok": True}]
    # The queued run's prompt tells the agent to write design.md and carries the digest + notes.
    with app.state.db_lock:
        row = app.state.db.execute("SELECT prompt, kind, status FROM runs WHERE id = ?", (body["run_id"],)).fetchone()
    assert row["kind"] == "brand_guide" and row["status"] == "queued"
    assert "design.md" in row["prompt"] and "#FF6A00" in row["prompt"] and "earthy, premium" in row["prompt"]


def test_brand_guide_requires_some_input(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_design_studio": True,
    })
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {client.post('/auth/auto').json()['token']}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    res = client.post("/api/projects/demo/design/brand-guide", headers=headers, json={})
    assert res.status_code == 400

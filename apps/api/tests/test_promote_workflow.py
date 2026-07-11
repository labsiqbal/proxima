from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api import workflows as wf
from proxima_api.main import create_app


def test_parse_blueprint_tolerates_fences_and_normalizes():
    raw = (
        "Here you go:\n```json\n"
        '{"name":"SEO","description":"d","category":"seo",'
        '"steps":[{"name":"Keyword","instruction":"find kw"},{"name":"Draft","instruction":"write"}]}'
        "\n```\n"
    )
    draft = wf.parse_blueprint(raw)
    assert draft["name"] == "SEO"
    assert draft["category"] == "seo"
    assert [s["name"] for s in draft["steps"]] == ["Keyword", "Draft"]
    assert draft["steps"][0]["id"] and draft["steps"][0]["review_required"] is False


def test_parse_blueprint_raises_without_json():
    import pytest

    with pytest.raises(ValueError):
        wf.parse_blueprint("no json here")


def _client(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c, app


def test_promote_enqueues_workflow_draft_run(tmp_path):
    c, app = _client(tmp_path)
    # an ad-hoc job gives us a real session with a couple of messages
    job = c.post("/api/jobs", json={"input": {"brief": "let's make an SEO article pipeline"}}).json()
    sid = job["session_id"]
    c.post(f"/api/sessions/{sid}/messages", json={"role": "user", "content": "keyword research then draft then schema"})

    r = c.post(f"/api/sessions/{sid}/promote-workflow", json={})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    run = app.state.db.execute(
        "SELECT kind FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()
    assert run["kind"] == "workflow_draft"

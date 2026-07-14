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
    assert draft["steps"][0]["id"]
    assert not draft["steps"][0]["review_required"]


def test_parse_blueprint_normalizes_graph_draft():
    raw = (
        "```json\n"
        '{"name":"Launch research","description":"d","category":"research",'
        '"graph":{"nodes":['
        '{"id":"collect","name":"Collect","instruction":"Collect facts",'
        '"output_kind":"json","output_schema":{"type":"object"}},'
        '{"id":"write","name":"Write","instruction":"Write brief",'
        '"output_kind":"text","depends_on":["collect"],"review_required":true}],'
        '"edges":[]}}\n'
        "```"
    )

    draft = wf.parse_blueprint(raw)

    assert draft["name"] == "Launch research"
    assert draft["steps"] == []
    assert draft["graph"]["edges"] == [{"from": "collect", "to": "write"}]
    assert draft["graph"]["nodes"][1]["review_required"]
    assert "depends_on" not in draft["graph"]["nodes"][1]


def test_parse_blueprint_raises_without_json():
    import pytest

    with pytest.raises(ValueError):
        wf.parse_blueprint("no json here")


def _client(tmp_path, **overrides):
    config = {
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
        "start_worker": False,
    }
    config.update(overrides)
    app = create_app(config)
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
    prompt = app.state.db.execute(
        "SELECT prompt FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()["prompt"]
    assert '"steps"' in prompt


def test_promote_enqueues_graph_architect_run_when_enabled(tmp_path):
    c, app = _client(tmp_path, feature_workflow_graph=True)
    job = c.post("/api/jobs", json={"input": {"brief": "research then publish"}}).json()
    sid = job["session_id"]
    c.post(
        f"/api/sessions/{sid}/messages",
        json={"role": "user", "content": "collect facts, draft, then approve"},
    )

    response = c.post(f"/api/sessions/{sid}/promote-workflow", json={})

    assert response.status_code == 202, response.text
    run = app.state.db.execute(
        "SELECT kind, prompt FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()
    assert run["kind"] == "workflow_graph_draft"
    assert "DAG plan" in run["prompt"]
    assert "review_required" in run["prompt"]


def test_promote_can_force_linear_while_graph_feature_is_enabled(tmp_path):
    c, app = _client(tmp_path, feature_workflow_graph=True)
    job = c.post("/api/jobs", json={"input": {"brief": "revise this recipe"}}).json()
    sid = job["session_id"]

    response = c.post(
        f"/api/sessions/{sid}/promote-workflow",
        json={"engine": "linear"},
    )

    assert response.status_code == 202, response.text
    run = app.state.db.execute(
        "SELECT kind FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()
    assert run["kind"] == "workflow_draft"

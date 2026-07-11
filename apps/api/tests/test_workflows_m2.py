from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api import workflows as wf
from proxima_api.main import create_app


def _app(tmp_path):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "ws"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )


def _client(app):
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _latest_run(app, sid):
    return dict(app.state.db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone())


# --- unit: substitution + rules + skills in the prompt ---


def test_substitute_fills_known_and_keeps_unknown():
    assert wf.substitute("topic {{topic}} kw {{kw}}", {"topic": "cats"}) == "topic cats kw {{kw}}"


def test_prompt_injects_inputs_rules_and_skills():
    s = wf.normalize_step({
        "name": "Draft", "instruction": "write about {{topic}}",
        "expected_output": "article on {{topic}}", "rules": "exactly 3 links",
        "skill_ids": ["firecrawl"],
    })
    p = wf.build_step_prompt(s, 0, 2, {"topic": "cats"})
    assert "write about cats" in p
    assert "article on cats" in p
    assert "RULES (must follow exactly):\nexactly 3 links" in p
    assert "firecrawl" in p


# --- inputs persisted on the workflow ---


def test_workflow_stores_inputs(tmp_path):
    c = _client(_app(tmp_path))
    w = c.post("/api/workflows", json={
        "name": "SEO", "steps": [{"name": "A", "instruction": "do {{topic}}"}],
        "inputs": [{"id": "topic", "label": "Topic", "kind": "text", "required": True}],
    }).json()
    assert w["inputs"][0]["id"] == "topic"
    assert c.get(f"/api/workflows/{w['id']}").json()["inputs"][0]["label"] == "Topic"


# --- review gate: pause mid-workflow, resume on approve ---


def test_review_gate_pauses_then_resumes(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "W", "steps": [
        {"name": "A", "instruction": "a"},
        {"name": "B", "instruction": "b", "review_required": True},
        {"name": "C", "instruction": "c"},
    ]}).json()["id"]
    job = c.post("/api/jobs", json={"workflow_id": wid}).json()
    jid, sid = job["id"], job["session_id"]
    c.post(f"/api/jobs/{jid}/start")

    # step 0 completes -> no gate -> step 1 runs
    app.state.worker._advance_job(_latest_run(app, sid), "out a")
    j = c.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "running" and j["current_step_idx"] == 1 and j["steps_state"][1]["status"] == "running"

    # step 1 completes -> review_required -> PAUSE (does not start step 2)
    app.state.worker._advance_job(_latest_run(app, sid), "out b")
    j = c.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "review"
    assert j["current_step_idx"] == 1
    assert j["steps_state"][1]["status"] == "done"
    assert j["steps_state"][2]["status"] == "queued"  # NOT started

    # approve -> resume step 2
    j = c.post(f"/api/jobs/{jid}/approve").json()
    assert j["status"] == "running" and j["current_step_idx"] == 2 and j["steps_state"][2]["status"] == "running"

    # step 2 completes -> last -> final review -> approve -> done
    app.state.worker._advance_job(_latest_run(app, sid), "out c")
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "review"
    assert c.post(f"/api/jobs/{jid}/approve").json()["status"] == "done"


def test_approve_edit_and_continue_replaces_output(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "W", "steps": [
        {"name": "A", "instruction": "a", "review_required": True},
        {"name": "B", "instruction": "b"},
    ]}).json()["id"]
    job = c.post("/api/jobs", json={"workflow_id": wid}).json()
    jid, sid = job["id"], job["session_id"]
    c.post(f"/api/jobs/{jid}/start")
    app.state.worker._advance_job(_latest_run(app, sid), "rough draft")
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "review"
    j = c.post(f"/api/jobs/{jid}/approve", json={"edited_output": "polished draft"}).json()
    assert j["steps_state"][0]["output_summary"] == "polished draft"
    assert j["status"] == "running"  # resumed to step B


def test_failed_step_fails_job(tmp_path):
    app = _app(tmp_path)
    c = _client(app)
    wid = c.post("/api/workflows", json={"name": "W", "steps": [
        {"name": "A", "instruction": "a"}, {"name": "B", "instruction": "b"}]}).json()["id"]
    job = c.post("/api/jobs", json={"workflow_id": wid}).json()
    c.post(f"/api/jobs/{job['id']}/start")
    app.state.worker._fail_job(job["session_id"], "boom")
    j = c.get(f"/api/jobs/{job['id']}").json()
    assert j["status"] == "failed"
    assert j["steps_state"][0]["status"] == "failed"
    assert "boom" in (j["steps_state"][0]["error"] or "")

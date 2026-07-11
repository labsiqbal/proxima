from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from proxima_api import db
from proxima_api import workflows as wf
from proxima_api.main import create_app


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
    return c


def test_schema_has_workflows_and_jobs_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "proxima.db")
    db.init_db(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"workflows", "jobs"} <= names
    jcols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {
        "workflow_id",
        "session_id",
        "status",
        "current_step_idx",
        "input",
        "steps_state",
        "schedule_id",
        "archived_at",
    } <= jcols
    scols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert "job_id" in scols
    assert "manual_title" in scols


# --- workflows.py helpers ---


def test_normalize_step_fills_defaults():
    s = wf.normalize_step({"name": "Keyword Research", "instruction": "find keywords"})
    assert s["id"]
    assert s["expected_output"] == ""
    assert s["type"] == "other"
    assert s["review_required"] is False


def test_build_step_prompt_includes_instruction_and_contract():
    s = wf.normalize_step(
        {"name": "Draft", "instruction": "write the article", "expected_output": "markdown article"}
    )
    p = wf.build_step_prompt(s, idx=2, total=5)
    assert "write the article" in p
    assert "markdown article" in p
    assert "step 3 of 5" in p.lower()
    assert "BLOCKED" in p


def test_step_state_from_snapshots_with_exec_fields():
    s = wf.normalize_step({"name": "A", "instruction": "do a"})
    st = wf.step_state_from(s)
    assert st["status"] == "queued"
    assert st["run_id"] is None
    assert st["output_summary"] is None
    assert st["name"] == "A"


def test_workflow_crud(tmp_path):
    c = _client(tmp_path)
    r = c.post(
        "/api/workflows",
        json={
            "name": "SEO Article",
            "steps": [
                {"name": "Keyword Research", "instruction": "find keywords"},
                {"name": "Draft", "instruction": "write it"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert len(r.json()["steps"]) == 2 and r.json()["steps"][0]["id"]
    assert any(w["name"] == "SEO Article" for w in c.get("/api/workflows").json())
    r2 = c.patch(f"/api/workflows/{wid}", json={"name": "SEO Article v2"})
    assert r2.json()["name"] == "SEO Article v2"
    assert c.patch(f"/api/workflows/{wid}", json={"status": "archived"}).json()["status"] == "archived"
    # archived hidden from default list
    assert not any(w["id"] == wid for w in c.get("/api/workflows").json())


def test_delete_workflow_removes_iterate_session(tmp_path):
    c = _client(tmp_path)
    wf_body = c.post(
        "/api/workflows",
        json={"name": "Recipe", "steps": [{"name": "A", "instruction": "do a"}]},
    ).json()
    sid = c.post(f"/api/workflows/{wf_body['id']}/iterate").json()["id"]
    assert c.app.state.db.execute("SELECT workflow_id FROM sessions WHERE id = ?", (sid,)).fetchone()["workflow_id"] == wf_body["id"]

    r = c.delete(f"/api/workflows/{wf_body['id']}")

    assert r.status_code == 200
    assert c.app.state.db.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone() is None
    assert all(s["id"] != sid for s in c.get("/api/sessions").json()["sessions"])


def test_workflow_list_filters_by_project_slug(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/projects", json={"slug": "alpha", "name": "Alpha"}).status_code == 201
    assert c.post("/api/projects", json={"slug": "beta", "name": "Beta"}).status_code == 201
    wa = c.post(
        "/api/workflows",
        json={"project_slug": "alpha", "name": "Alpha Flow", "steps": [{"name": "A", "instruction": "do alpha"}]},
    ).json()
    wb = c.post(
        "/api/workflows",
        json={"project_slug": "beta", "name": "Beta Flow", "steps": [{"name": "B", "instruction": "do beta"}]},
    ).json()

    alpha = c.get("/api/workflows?project_slug=alpha").json()
    beta = c.get("/api/workflows?project_slug=beta").json()

    assert [w["id"] for w in alpha] == [wa["id"]]
    assert [w["id"] for w in beta] == [wb["id"]]

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import image_providers
from proxima_api.run_prompting import extract_vision_images
from proxima_api.main import create_app
from proxima_api.routes import chat as chat_routes


def wait_media_run(app, run_id: int, timeout: float = 8.0) -> str:
    """Media runs finish in a background thread — poll until terminal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with app.state.db_lock:
            row = app.state.db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row and row["status"] in ("completed", "failed"):
            return row["status"]
        time.sleep(0.05)
    raise AssertionError(f"media run {run_id} did not finish within {timeout}s")


class PermissionSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resolve_permission(self, request_id: str, option_id: str) -> bool:
        self.calls.append((request_id, option_id))
        return True


def test_chat_send_accepts_non_hermes_runner(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    res = client.post("/api/chat/send", headers={"Authorization": f"Bearer {token}"}, json={"message": "hello", "runner_id": "claude-code"})
    assert res.status_code == 202


def test_chat_send_hermes_enqueues_async_run(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]

    res = client.post(
        "/api/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hello", "runner_id": "hermes", "model": "test/model"},
    )
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued"
    assert body["run_id"]

    events = client.get(f"/api/sessions/{body['session_id']}/events", headers={"Authorization": f"Bearer {token}"})
    assert events.status_code == 200
    assert events.json()["events"][0]["type"] == "run.queued"


def test_main_chat_image_request_creates_artifact_first_result(tmp_path, monkeypatch):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    slug = "demo"

    def fake_generate(*args, **kwargs):
        out = kwargs.get("out_path")
        if out:
            Path(out).write_bytes(b"png")
        return b"png"

    monkeypatch.setattr(image_providers, "generate", fake_generate)
    res = client.post(
        "/api/chat/send",
        headers=headers,
        json={"project_slug": slug, "message": "/image neon robot mascot for onboarding"},
    )

    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "queued"  # visible run; generation finishes in background
    assert body["media_action"] == "image"
    assert wait_media_run(app, body["run_id"]) == "completed"
    events = client.get(f"/api/sessions/{body['session_id']}/events", headers=headers).json()["events"]
    complete = [e for e in events if e["type"] == "message.complete"][-1]
    links = complete["payload"]["output_links"]
    assert links[0]["type"] == "image"
    assert links[0]["path"].startswith("artifacts/media/images/")
    messages = client.get(f"/api/sessions/{body['session_id']}/messages", headers=headers).json()["messages"]
    assert "Design Studio" not in messages[-1]["content"]
    assert links[0]["actions"] == ["use-as-reference"]


def test_thin_image_brief_asks_question_form_instead_of_generating(tmp_path, monkeypatch):
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {client.post('/auth/auto').json()['token']}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})

    def boom(*a, **k):
        raise AssertionError("generate must not run on a thin brief")

    monkeypatch.setattr(image_providers, "generate", boom)
    # Bare /image (no subject) — should clarify, not generate.
    res = client.post("/api/chat/send", headers=headers, json={"project_slug": "demo", "message": "/image"})
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["media_action"] == "image_ask"
    messages = client.get(f"/api/sessions/{body['session_id']}/messages", headers=headers).json()["messages"]
    content = messages[-1]["content"]
    assert "<question-form" in content and 'submit-as="/image"' in content
    # No artifact/output links on a clarify turn.
    events = client.get(f"/api/sessions/{body['session_id']}/events", headers=headers).json()["events"]
    complete = [e for e in events if e["type"] == "message.complete"][-1]
    assert complete["payload"]["output_links"] == []


def test_thin_design_brief_asks_form_but_rich_brief_creates_draft(tmp_path):
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

    # Thin /design → clarify form (submit-as re-issues /design).
    thin = client.post("/api/chat/send", headers=headers, json={"project_slug": "demo", "message": "/design"}).json()
    assert thin["media_action"] == "image-studio_ask"
    ask_msg = client.get(f"/api/sessions/{thin['session_id']}/messages", headers=headers).json()["messages"][-1]["content"]
    assert "<question-form" in ask_msg and 'submit-as="/design"' in ask_msg

    # A rich brief (what the answered form re-issues) creates the draft + design session.
    rich = client.post("/api/chat/send", headers=headers, json={
        "project_slug": "demo",
        "message": "/design promo 20% off new coffee menu, IG post, clean minimal",
    }).json()
    assert rich["media_action"] == "image-studio"
    assert rich["artifact"]["type"] == "design"


def test_run_can_store_display_message_separate_from_prompt(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "iterate"}).json()["id"]
    run_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "long internal prompt", "display_message": "Run step 1: Smoke"},
    ).json()["run_id"]

    msg = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"][0]
    run = client.get(f"/api/runs/{run_id}", headers=headers).json()
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert msg["content"] == "Run step 1: Smoke"
    assert run["prompt"] == "long internal prompt"
    assert events[0]["payload"]["label"] == "Run step 1: Smoke"


def test_collaboration_settings_roundtrip_and_validation(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/settings/collaboration", headers=headers).json() == {"brainstorm_agents": 3, "debate_rounds": 2}
    saved = client.put("/api/settings/collaboration", headers=headers, json={"brainstorm_agents": 2, "debate_rounds": 4})
    assert saved.status_code == 200
    assert saved.json() == {"brainstorm_agents": 2, "debate_rounds": 4}
    assert client.put("/api/settings/collaboration", headers=headers, json={"brainstorm_agents": 4, "debate_rounds": 2}).status_code == 400


def test_run_prompt_mode_starts_brainstorm_collaboration(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "brainstorm"}).json()["id"]

    body = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "brainstorm"},
    ).json()

    msg = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"][0]
    parent = client.get(f"/api/runs/{body['run_id']}", headers=headers).json()
    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (body["run_id"],)).fetchone()
    children = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND id != ? ORDER BY id", (collab["id"], body["run_id"])).fetchall()
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert msg["content"] == "pick the best UX"
    assert body["status"] == "running"
    assert parent["kind"] == "collab_brainstorm"
    assert parent["status"] == "running"
    assert collab["mode"] == "brainstorm"
    assert len(children) == 1
    assert children[0]["kind"] == "collab_brainstorm_child"
    assert "Brainstorm lane" in children[0]["prompt"]
    assert events[0]["payload"]["prompt_mode"] == "brainstorm"
    queued_card = next(e for e in events if e["type"] == "collaboration.child.queued")
    assert queued_card["run_id"] == children[0]["id"]
    assert queued_card["payload"]["parent_run_id"] == body["run_id"]
    assert queued_card["payload"]["agent_name"] == "Default"
    assert queued_card["payload"]["round_label"] == "Idea lane 1"


def test_brainstorm_uses_configured_agent_count(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = app.state.db.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()["id"]
    app.state.db.execute("INSERT INTO profiles(user_id, slug, name, runner_id, hermes_home) VALUES (?, 'p2', 'Agent Two', 'codex', ?)", (user_id, str(tmp_path / "h2")))
    app.state.db.execute("INSERT INTO profiles(user_id, slug, name, runner_id, hermes_home) VALUES (?, 'p3', 'Agent Three', 'claude-code', ?)", (user_id, str(tmp_path / "h3")))
    client.put("/api/settings/collaboration", headers=headers, json={"brainstorm_agents": 2, "debate_rounds": 2})
    session_id = client.post("/api/sessions", headers=headers, json={"title": "brainstorm"}).json()["id"]

    parent_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "brainstorm"},
    ).json()["run_id"]

    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)).fetchone()
    children = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND id != ? ORDER BY id", (collab["id"], parent_id)).fetchall()
    assert [c["collaboration_role"] for c in children] == ["idea:1", "idea:2"]


def test_debate_uses_configured_round_count(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = app.state.db.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()["id"]
    app.state.db.execute("INSERT INTO profiles(user_id, slug, name, runner_id, hermes_home) VALUES (?, 'p2', 'Agent Two', 'codex', ?)", (user_id, str(tmp_path / "h2")))
    client.put("/api/settings/collaboration", headers=headers, json={"brainstorm_agents": 3, "debate_rounds": 3})
    session_id = client.post("/api/sessions", headers=headers, json={"title": "debate"}).json()["id"]
    parent_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "debate"},
    ).json()["run_id"]
    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)).fetchone()

    stance = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND kind = 'collab_debate_stance'", (collab["id"],)).fetchone()
    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (stance["id"],))
    app.state.worker._complete_collaboration_run(dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (stance["id"],)).fetchone()), "Opening argument.", "stop")
    rebuttal = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND kind = 'collab_debate_rebuttal'", (collab["id"],)).fetchone()
    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (rebuttal["id"],))
    app.state.worker._complete_collaboration_run(dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (rebuttal["id"],)).fetchone()), "Rebuttal argument.", "stop")

    counter = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND kind = 'collab_debate_counter_rebuttal'", (collab["id"],)).fetchone()
    assert counter is not None
    assert counter["collaboration_role"] == "counter_rebuttal"
    assert "Round 3" in counter["prompt"]
    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (counter["id"],))
    app.state.worker._complete_collaboration_run(dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (counter["id"],)).fetchone()), "Counter-rebuttal.", "stop")
    synth = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND kind = 'collab_debate_synthesis'", (collab["id"],)).fetchone()
    assert synth is not None


def test_brainstorm_collaboration_synthesizes_single_visible_message(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "brainstorm"}).json()["id"]
    parent_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "brainstorm"},
    ).json()["run_id"]
    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)).fetchone()
    child = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND kind = 'collab_brainstorm_child'", (collab["id"],)).fetchone()

    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (child["id"],))
    assert app.state.worker._complete_collaboration_run(dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (child["id"],)).fetchone()), "Try a compact sidecar.", "stop") is True

    synth = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND kind = 'collab_brainstorm_synthesis'", (collab["id"],)).fetchone()
    assert synth["status"] == "queued"
    assert "Try a compact sidecar" in synth["prompt"]
    assert app.state.db.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id = ? AND role = 'assistant'", (session_id,)).fetchone()["c"] == 0

    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (synth["id"],))
    assert app.state.worker._complete_collaboration_run(dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (synth["id"],)).fetchone()), "Use the sidecar and keep actions compact.", "stop") is True

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["run_id"] == parent_id
    assert "# 🧠 Brainstorm result" in messages[1]["content"]
    assert "Use the sidecar" in messages[1]["content"]
    # Synthesis-only final message: per-agent ideas live in the cards, not here.
    assert "Try a compact sidecar" not in messages[1]["content"]
    assert client.get(f"/api/runs/{parent_id}", headers=headers).json()["status"] == "completed"
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    completed_cards = [e for e in events if e["type"] == "collaboration.child.completed"]
    assert [e["payload"]["status"] for e in completed_cards] == ["done", "done"]
    assert completed_cards[0]["payload"]["text"] == "Try a compact sidecar."


def test_brainstorm_child_failure_fails_parent_not_cancelled(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "brainstorm"}).json()["id"]
    parent_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "brainstorm"},
    ).json()["run_id"]
    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)).fetchone()
    child = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND id != ?", (collab["id"], parent_id)).fetchone()
    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (child["id"],))

    app.state.worker._fail_collaboration_run(child["id"], session_id, None, "boom")

    assert client.get(f"/api/runs/{parent_id}", headers=headers).json()["status"] == "failed"
    assert client.get(f"/api/runs/{child['id']}", headers=headers).json()["status"] == "failed"
    assert app.state.db.execute("SELECT status FROM prompt_collaborations WHERE id = ?", (collab["id"],)).fetchone()["status"] == "failed"
    assert app.state.db.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id = ? AND role = 'assistant'", (session_id,)).fetchone()["c"] == 0
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    failed_card = next(e for e in events if e["type"] == "collaboration.child.failed")
    assert failed_card["run_id"] == child["id"]
    assert failed_card["payload"]["status"] == "failed"
    assert failed_card["payload"]["error"] == "boom"


def test_cancel_brainstorm_parent_emits_child_cancelled_card(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "brainstorm"}).json()["id"]
    parent_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "pick the best UX", "prompt_mode": "brainstorm"},
    ).json()["run_id"]
    collab = app.state.db.execute("SELECT * FROM prompt_collaborations WHERE parent_run_id = ?", (parent_id,)).fetchone()
    child = app.state.db.execute("SELECT * FROM runs WHERE collaboration_id = ? AND id != ?", (collab["id"], parent_id)).fetchone()

    res = client.post(f"/api/runs/{parent_id}/cancel", headers=headers)

    assert res.status_code == 200
    assert client.get(f"/api/runs/{parent_id}", headers=headers).json()["status"] == "cancelled"
    assert client.get(f"/api/runs/{child['id']}", headers=headers).json()["status"] == "cancelled"
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    cancelled_card = next(e for e in events if e["type"] == "collaboration.child.cancelled")
    assert cancelled_card["run_id"] == child["id"]
    assert cancelled_card["payload"]["status"] == "cancelled"
    assert cancelled_card["payload"]["round_label"] == "Idea lane 1"


def test_workflow_iterate_instant_result_completes_without_worker(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "iterate"}).json()["id"]

    rejected = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "prompt", "display_message": "Run step 1", "instant_result": "test received"},
    )
    assert rejected.status_code == 400

    _wf_id = app.state.db.execute("INSERT INTO workflows(name) VALUES ('wf')").lastrowid
    app.state.db.execute("UPDATE sessions SET workflow_id = ? WHERE id = ?", (_wf_id, session_id))
    res = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "prompt", "display_message": "Run step 1", "instant_result": "test received"},
    )

    body = res.json()
    assert body["status"] == "completed"
    assert client.get(f"/api/runs/{body['run_id']}", headers=headers).json()["status"] == "completed"
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert [m["content"] for m in messages] == ["Run step 1", "test received"]
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert [e["type"] for e in events] == ["run.queued", "run.started", "message.complete", "run.completed"]


def test_delete_completed_run_removes_result_messages_and_events(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "iterate"}).json()["id"]
    _wf_id = app.state.db.execute("INSERT INTO workflows(name) VALUES ('wf')").lastrowid
    app.state.db.execute("UPDATE sessions SET workflow_id = ? WHERE id = ?", (_wf_id, session_id))
    body = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "prompt", "display_message": "Run step 1", "instant_result": "test received"},
    ).json()

    res = client.delete(f"/api/runs/{body['run_id']}", headers=headers)

    assert res.status_code == 200
    assert client.get(f"/api/runs/{body['run_id']}", headers=headers).status_code == 404
    assert client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"] == []
    assert client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"] == []


def test_delete_session_artifact_scrubs_stale_message_and_event_links(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "alpha", "name": "Alpha"})
    session_id = client.post("/api/sessions", headers=headers, json={"title": "iterate", "project_slug": "alpha"}).json()["id"]
    _wf_id = app.state.db.execute("INSERT INTO workflows(name) VALUES ('wf')").lastrowid
    app.state.db.execute("UPDATE sessions SET workflow_id = ? WHERE id = ?", (_wf_id, session_id))
    run_id = client.post(
        f"/api/sessions/{session_id}/runs",
        headers=headers,
        json={"message": "prompt", "display_message": "Run step 1", "instant_result": "done"},
    ).json()["run_id"]
    link = {"type": "doc", "title": "out.md", "path": "artifacts/out.md", "project_slug": "alpha"}
    app.state.db.execute(
        "UPDATE messages SET output_links = ? WHERE session_id = ? AND run_id = ?",
        (json.dumps([link]), session_id, run_id),
    )
    app.state.db.execute("UPDATE events SET payload = ? WHERE run_id = ? AND type = 'message.complete'", (json.dumps({"output_links": [link]}), run_id))

    res = client.delete(f"/api/sessions/{session_id}/artifacts?path=artifacts%2Fout.md", headers=headers)

    assert res.status_code == 200
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert app.state.db.execute("SELECT output_links FROM messages WHERE run_id = ?", (run_id,)).fetchone()["output_links"] == "[]"
    complete = next(e for e in events if e["type"] == "message.complete")
    assert complete["payload"]["output_links"] == []


def test_cancel_completed_run_is_idempotent_without_cancel_event(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "done"}).json()["id"]
    run_id = client.post(f"/api/sessions/{session_id}/runs", headers=headers, json={"message": "hello"}).json()["run_id"]
    app.state.db.execute(
        "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (run_id,),
    )

    res = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "completed"
    assert client.get(f"/api/runs/{run_id}", headers=headers).json()["status"] == "completed"
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert not any(e["type"] == "run.cancelled" for e in events)


def test_cancel_completed_run_does_not_cancel_new_queued_run(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "done"}).json()["id"]
    old_run_id = client.post(f"/api/sessions/{session_id}/runs", headers=headers, json={"message": "old"}).json()["run_id"]
    app.state.db.execute(
        "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (old_run_id,),
    )
    new_run_id = client.post(f"/api/sessions/{session_id}/runs", headers=headers, json={"message": "new"}).json()["run_id"]

    res = client.post(f"/api/runs/{old_run_id}/cancel", headers=headers)

    assert res.status_code == 200
    assert client.get(f"/api/runs/{old_run_id}", headers=headers).json()["status"] == "completed"
    assert client.get(f"/api/runs/{new_run_id}", headers=headers).json()["status"] == "queued"


def test_permission_response_rejected_for_terminal_run(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    spy = PermissionSpy()
    app.state.acp_manager = spy
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post("/api/sessions", headers=headers, json={"title": "permission"}).json()["id"]
    run_id = client.post(f"/api/sessions/{session_id}/runs", headers=headers, json={"message": "hello"}).json()["run_id"]
    app.state.db.execute(
        "UPDATE runs SET status = 'completed', finished_at = CURRENT_TIMESTAMP WHERE id = ?",
        (run_id,),
    )

    res = client.post(
        f"/api/runs/{run_id}/permission",
        headers=headers,
        json={"request_id": "req-1", "option_id": "allow"},
    )

    assert res.status_code == 409
    assert res.json()["detail"] == "run is not waiting for permission"
    assert spy.calls == []


def test_permission_response_routes_to_matching_active_run(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    sid_a = client.post("/api/sessions", headers=headers, json={"title": "permission a"}).json()["id"]
    sid_b = client.post("/api/sessions", headers=headers, json={"title": "permission b"}).json()["id"]
    run_a = client.post(f"/api/sessions/{sid_a}/runs", headers=headers, json={"message": "a"}).json()["run_id"]
    run_b = client.post(f"/api/sessions/{sid_b}/runs", headers=headers, json={"message": "b"}).json()["run_id"]
    app.state.db.execute("UPDATE runs SET status = 'running' WHERE id IN (?, ?)", (run_a, run_b))
    spy_a = PermissionSpy()
    spy_b = PermissionSpy()
    app.state.worker.active_runs[run_a] = (spy_a, "sid-a")
    app.state.worker.active_runs[run_b] = (spy_b, "sid-b")

    res = client.post(
        f"/api/runs/{run_b}/permission",
        headers=headers,
        json={"request_id": "same-request-id", "option_id": "allow"},
    )

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert spy_a.calls == []
    assert spy_b.calls == [("same-request-id", "allow")]


def _media_app(tmp_path):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
            "start_worker": False,
            "feature_design_studio": True,
        }
    )


def test_session_runs_endpoint_intercepts_media_prompts(tmp_path, monkeypatch):
    """The chat UI posts to /api/sessions/{id}/runs — media prompts must short-circuit
    to the generation provider there too, not reach the ACP agent (regression:
    interception originally lived only in the unused /api/chat/send)."""
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    sid = client.post("/api/sessions", headers=headers, json={"title": "ugc", "project_slug": "demo"}).json()["id"]
    calls: list[str] = []

    def fake_generate(provider_id, key, *, prompt, model=None, **kwargs):
        calls.append(prompt)
        return b"png"

    monkeypatch.setattr(image_providers, "generate", fake_generate)
    res = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "/image ugc product teaser cinematic"})

    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "queued" and body["media_action"] == "image"
    assert wait_media_run(app, body["run_id"]) == "completed"
    assert calls  # provider was called, no ACP run queued
    with app.state.db_lock:
        kind = app.state.db.execute("SELECT kind FROM runs WHERE id = ?", (body["run_id"],)).fetchone()["kind"]
        msgs = [r["role"] for r in app.state.db.execute("SELECT role FROM messages WHERE session_id = ? ORDER BY id", (sid,)).fetchall()]
    assert kind == "media_image"
    assert msgs == ["user", "assistant"]  # the user prompt stays in the thread
    messages = client.get(f"/api/sessions/{sid}/messages", headers=headers).json()["messages"]
    links = messages[-1]["output_links"]
    assert links and links[0]["type"] == "image"  # clickable result card in chat


def test_image_file_mention_reaches_generation_provider_as_pixels(tmp_path, monkeypatch):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post(
        "/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"}
    ).json()
    root = Path(project["path"])
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets/logo.png").write_bytes(b"reference-pixels")
    sid = client.post(
        "/api/sessions",
        headers=headers,
        json={"title": "image ref", "project_slug": "demo"},
    ).json()["id"]
    captured: dict[str, object] = {}

    def fake_generate(provider_id, key, *, prompt, image_bytes=None, image_mime=None, **kwargs):
        captured.update(prompt=prompt, image_bytes=image_bytes, image_mime=image_mime)
        return b"generated"

    monkeypatch.setattr(image_providers, "generate", fake_generate)
    response = client.post(
        f"/api/sessions/{sid}/runs",
        headers=headers,
        json={
            "message": (
                "/image make a polished launch poster using "
                "![logo.png](assets/logo.png)"
            )
        },
    )

    assert response.status_code == 202, response.text
    assert wait_media_run(app, response.json()["run_id"]) == "completed"
    assert captured["image_bytes"] == b"reference-pixels"
    assert captured["image_mime"] == "image/png"
    assert "![logo.png]" not in str(captured["prompt"])


def test_media_run_failure_lands_in_thread(tmp_path, monkeypatch):
    """Provider errors must be visible: run → failed + an assistant message with the
    actionable detail (no more silent 400s or invisible hangs)."""
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    sid = client.post("/api/sessions", headers=headers, json={"title": "ugc", "project_slug": "demo"}).json()["id"]

    def boom(*a, **k):
        raise image_providers.ImageProviderError("Image provider unavailable.")

    monkeypatch.setattr(image_providers, "generate", boom)
    res = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "/image sufficiently rich brief"})
    assert res.status_code == 202
    body = res.json()
    assert wait_media_run(app, body["run_id"]) == "failed"
    messages = client.get(f"/api/sessions/{sid}/messages", headers=headers).json()["messages"]
    assert "Image provider unavailable" in messages[-1]["content"]
    events = client.get(f"/api/sessions/{sid}/events", headers=headers).json()["events"]
    assert any(e["type"] == "run.failed" for e in events)


def test_cancelled_media_run_stays_cancelled_when_provider_finishes(tmp_path, monkeypatch):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    sid = client.post("/api/sessions", headers=headers, json={"title": "image", "project_slug": "demo"}).json()["id"]
    started = threading.Event()
    release = threading.Event()

    def delayed_generate(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return b"png"

    monkeypatch.setattr(image_providers, "generate", delayed_generate)
    created = client.post(
        f"/api/sessions/{sid}/runs",
        headers=headers,
        json={"message": "/image cinematic orange product launch"},
    ).json()
    assert started.wait(2)
    cancelled = client.post(f"/api/runs/{created['run_id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    release.set()

    deadline = time.time() + 2
    while time.time() < deadline:
        row = app.state.db.execute("SELECT status FROM runs WHERE id = ?", (created["run_id"],)).fetchone()
        if row["status"] == "cancelled":
            break
        time.sleep(0.02)
    time.sleep(0.1)
    row = app.state.db.execute("SELECT status FROM runs WHERE id = ?", (created["run_id"],)).fetchone()
    generated_replies = app.state.db.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE run_id = ? AND role = 'assistant'",
        (created["run_id"],),
    ).fetchone()["n"]
    assert row["status"] == "cancelled"
    assert generated_replies == 0


def test_concurrent_same_second_media_runs_use_distinct_files(tmp_path, monkeypatch):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    first_session = client.post(
        "/api/sessions",
        headers=headers,
        json={"title": "first", "project_slug": "demo"},
    ).json()["id"]
    second_session = client.post(
        "/api/sessions",
        headers=headers,
        json={"title": "second", "project_slug": "demo"},
    ).json()["id"]
    barrier = threading.Barrier(2)
    real_monotonic = time.monotonic

    class FrozenClock:
        @staticmethod
        def time() -> float:
            return 1_700_000_000.0

        monotonic = staticmethod(real_monotonic)

    def simultaneous_generate(*args, prompt: str, **kwargs):
        barrier.wait(timeout=3)
        return prompt.encode("utf-8")

    monkeypatch.setattr(chat_routes, "time", FrozenClock)
    monkeypatch.setattr(image_providers, "generate", simultaneous_generate)
    first = client.post(
        f"/api/sessions/{first_session}/runs",
        headers=headers,
        json={"message": "/image cinematic first product launch"},
    ).json()
    second = client.post(
        f"/api/sessions/{second_session}/runs",
        headers=headers,
        json={"message": "/image cinematic second product launch"},
    ).json()
    assert wait_media_run(app, first["run_id"]) == "completed"
    assert wait_media_run(app, second["run_id"]) == "completed"

    first_message = client.get(
        f"/api/sessions/{first_session}/messages", headers=headers
    ).json()["messages"][-1]
    second_message = client.get(
        f"/api/sessions/{second_session}/messages", headers=headers
    ).json()["messages"][-1]
    first_path = first_message["output_links"][0]["path"]
    second_path = second_message["output_links"][0]["path"]
    assert first_path != second_path
    project_root = Path(
        app.state.db.execute(
            "SELECT path FROM projects WHERE slug = 'demo'"
        ).fetchone()["path"]
    )
    assert (project_root / first_path).read_bytes() != (project_root / second_path).read_bytes()


def test_session_runs_media_skips_collab_and_instant(tmp_path, monkeypatch):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    sid = client.post("/api/sessions", headers=headers, json={"title": "ugc", "project_slug": "demo"}).json()["id"]

    def explode(*a, **k):
        raise AssertionError("media provider must not be called")

    monkeypatch.setattr(image_providers, "generate", explode)
    # brainstorm mode goes to collaboration, never the media provider
    res = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "/image product teaser cinematic", "prompt_mode": "brainstorm"})
    assert res.status_code == 202


def test_natural_language_media_phrases_go_to_the_agent(tmp_path, monkeypatch):
    """Command-only by owner decision: 'buat video …' / 'generate video …' must NOT
    auto-generate (it costs credits) — it queues a normal agent run."""
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    sid = client.post("/api/sessions", headers=headers, json={"title": "ugc", "project_slug": "demo"}).json()["id"]

    def explode(*a, **k):
        raise AssertionError("media provider must not fire on natural language")

    monkeypatch.setattr(image_providers, "generate", explode)
    for message in ("test generate video ugc durasi 12 detik", "buat gambar thumbnail buat launching"):
        res = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": message})
        assert res.status_code == 202
        assert res.json()["status"] == "queued"  # normal agent run


def test_session_runs_without_project_falls_back_to_agent(tmp_path, monkeypatch):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    sid = client.post("/api/sessions", headers=headers, json={"title": "no project"}).json()["id"]

    def explode(*a, **k):
        raise AssertionError("media provider must not be called without a project")

    monkeypatch.setattr(image_providers, "generate", explode)
    res = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "/image ugc product demo"})
    assert res.status_code == 202
    assert res.json()["status"] == "queued"  # normal agent run


def test_image_studio_command_creates_design_draft(tmp_path):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"})
    sid = client.post("/api/sessions", headers=headers, json={"title": "ig", "project_slug": "demo"}).json()["id"]

    res = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "/image-studio carousel promo snacktray lawson"})

    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "completed" and body["media_action"] == "image-studio"
    link = body["artifact"]
    assert link["type"] == "design" and link["path"].startswith("artifacts/design/")
    project_path = Path(app.state.db.execute("SELECT path FROM projects WHERE slug = ?", ("demo",)).fetchone()["path"])
    scene = json.loads((project_path / link["path"] / "scene.json").read_text())
    assert scene["id"] == link["id"] and scene["artboards"]
    # a linked design session exists with a queued design-agent run composing the brief
    with app.state.db_lock:
        drow = app.state.db.execute("SELECT id, mode FROM sessions WHERE id = ?", (scene["sessionId"],)).fetchone()
        rrow = app.state.db.execute("SELECT status, prompt FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (scene["sessionId"],)).fetchone()
    assert drow and drow["mode"] == "design"
    assert rrow and rrow["status"] == "queued"
    assert "carousel promo snacktray lawson" in rrow["prompt"] and "Current scene" in rrow["prompt"]


def test_image_mention_in_design_command_becomes_jailed_vision_input(tmp_path):
    app = _media_app(tmp_path)
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post(
        "/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"}
    ).json()
    root = Path(project["path"])
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets/hero.webp").write_bytes(b"hero-pixels")
    sid = client.post(
        "/api/sessions",
        headers=headers,
        json={"title": "design ref", "project_slug": "demo"},
    ).json()["id"]

    response = client.post(
        f"/api/sessions/{sid}/runs",
        headers=headers,
        json={
            "message": (
                "/design create a premium campaign poster around "
                "![hero.webp](assets/hero.webp)"
            )
        },
    )

    assert response.status_code == 202, response.text
    scene_path = root / response.json()["artifact"]["path"] / "scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    with app.state.db_lock:
        prompt = app.state.db.execute(
            "SELECT prompt FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (scene["sessionId"],),
        ).fetchone()["prompt"]
    clean_prompt, images = extract_vision_images(prompt, str(root))
    assert "Current scene" in clean_prompt
    assert images == [(b"hero-pixels", "image/webp")]

from __future__ import annotations

import json

from fastapi.testclient import TestClient  # type: ignore[import-not-found]

from proxima_api.main import create_app


def _client(tmp_path):
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
    return app, client, {"Authorization": f"Bearer {token}"}


def _profile(client: TestClient, headers: dict[str, str], name: str, runner_id: str) -> int:
    res = client.post("/api/profiles", headers=headers, json={"name": name, "runner_id": runner_id})
    assert res.status_code in (200, 201), res.text
    return int(res.json()["id"])


def _assistant_message(client: TestClient, headers: dict[str, str], profile_id: int, content: str = "Ship it") -> tuple[int, int]:
    session_id = client.post("/api/sessions", headers=headers, json={"title": "review me", "profile_id": profile_id}).json()["id"]
    msg = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=headers,
        json={"role": "assistant", "content": content},
    )
    assert msg.status_code == 200, msg.text
    return session_id, int(msg.json()["id"])


def test_create_message_review_queues_sidecar_run_without_chat_message(tmp_path):
    app, client, headers = _client(tmp_path)
    source_profile = _profile(client, headers, "Source Claude", "claude-code")
    _profile(client, headers, "Reviewer Codex", "codex")
    session_id, message_id = _assistant_message(client, headers, source_profile, "Plan A has one risk.")

    res = client.post(f"/api/messages/{message_id}/reviews", headers=headers, json={"mode": "validate"})

    assert res.status_code == 202, res.text
    review = res.json()["review"]
    assert review["source_message_id"] == message_id
    assert review["session_id"] == session_id
    assert review["mode"] == "validate"
    assert review["status"] == "queued"
    assert review["source_runner"] == "claude-code"
    assert review["reviewer_profiles"][0]["runner_id"] != "claude-code"

    run = app.state.db.execute("SELECT * FROM runs WHERE id = ?", (review["run_id"],)).fetchone()
    assert run["kind"] == "message_review"
    assert run["runner_id"] == review["reviewer_profiles"][0]["runner_id"]

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert [m["content"] for m in messages] == ["Plan A has one risk."]
    events = client.get(f"/api/sessions/{session_id}/events", headers=headers).json()["events"]
    assert [e["type"] for e in events] == ["run.queued", "message_review.queued"]
    assert events[0]["payload"]["kind"] == "message_review"


def test_message_review_rejects_same_runner_reviewer(tmp_path):
    _, client, headers = _client(tmp_path)
    source_profile = _profile(client, headers, "Source Claude", "claude-code")
    same_runner = _profile(client, headers, "Other Claude", "claude-code")
    _, message_id = _assistant_message(client, headers, source_profile)

    res = client.post(
        f"/api/messages/{message_id}/reviews",
        headers=headers,
        json={"mode": "validate", "reviewer_profile_id": same_runner},
    )

    assert res.status_code == 400
    assert "different runner" in res.text


def test_complete_message_review_stores_structured_result_not_chat_message(tmp_path):
    app, client, headers = _client(tmp_path)
    source_profile = _profile(client, headers, "Source Claude", "claude-code")
    _profile(client, headers, "Reviewer Codex", "codex")
    session_id, message_id = _assistant_message(client, headers, source_profile)
    review = client.post(f"/api/messages/{message_id}/reviews", headers=headers, json={}).json()["review"]
    run_id = int(review["run_id"])
    app.state.db.execute("UPDATE runs SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
    run = dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    answer = """```json
{
  "verdict": "needs_work",
  "gaps": ["Missing rollback plan"],
  "depends_on_input": ["Deployment target"],
  "revised_content": "Add rollback steps before shipping.",
  "suggested_next_move": "ask_original_to_revise"
}
```"""

    assert app.state.worker._complete_message_review(run, answer, "stop") is True

    body = client.get(f"/api/messages/{message_id}/reviews", headers=headers).json()["reviews"][0]
    assert body["status"] == "done"
    assert body["verdict"] == "needs_work"
    assert body["gaps"] == ["Missing rollback plan"]
    assert body["depends_on_input"] == ["Deployment target"]
    assert body["revised_content"] == "Add rollback steps before shipping."
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"

    replace = client.post(f"/api/message-reviews/{review['id']}/replace-answer", headers=headers)
    assert replace.status_code == 200
    assert replace.json()["message"]["content"] == "Add rollback steps before shipping."
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert messages[0]["content"] == "Add rollback steps before shipping."
    applied = client.get(f"/api/messages/{message_id}/reviews", headers=headers).json()["reviews"][0]
    assert applied["applied_at"]
    assert applied["source_original_content"] == "Ship it"

    restore = client.post(f"/api/message-reviews/{review['id']}/restore-original", headers=headers)
    assert restore.status_code == 200
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert messages[0]["content"] == "Ship it"


def test_cancelled_message_review_rejects_late_completion(tmp_path):
    app, client, headers = _client(tmp_path)
    source_profile = _profile(client, headers, "Source Claude", "claude-code")
    _profile(client, headers, "Reviewer Codex", "codex")
    session_id, message_id = _assistant_message(client, headers, source_profile)
    review = client.post(
        f"/api/messages/{message_id}/reviews",
        headers=headers,
        json={},
    ).json()["review"]
    run_id = int(review["run_id"])
    app.state.db.execute(
        "UPDATE runs SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ?",
        (run_id,),
    )
    app.state.db.execute(
        "UPDATE message_reviews SET status = 'running' WHERE id = ?",
        (review["id"],),
    )
    run = dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())

    cancelled = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert app.state.worker._complete_message_review(run, '{"verdict":"pass"}', "stop") is True

    stored = client.get(
        f"/api/messages/{message_id}/reviews",
        headers=headers,
    ).json()["reviews"][0]
    assert stored["status"] == "cancelled"
    assert stored["verdict"] is None
    events = client.get(
        f"/api/sessions/{session_id}/events",
        headers=headers,
    ).json()["events"]
    assert not any(e["type"] in {"message_review.completed", "run.completed"} for e in events)


def test_ask_original_to_revise_creates_sidecar_merge_run(tmp_path):
    app, client, headers = _client(tmp_path)
    source_profile = _profile(client, headers, "Source Claude", "claude-code")
    _profile(client, headers, "Reviewer Codex", "codex")
    session_id, message_id = _assistant_message(client, headers, source_profile, "Original answer")
    review = client.post(f"/api/messages/{message_id}/reviews", headers=headers, json={}).json()["review"]
    app.state.db.execute(
        "UPDATE message_reviews SET status = 'done', raw_transcript = ?, revised_content = ? WHERE id = ?",
        (json.dumps({"gaps": ["tighten scope"]}), "Revised answer", review["id"]),
    )

    res = client.post(f"/api/message-reviews/{review['id']}/ask-original", headers=headers, json={"note": "keep it concise"})

    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    run = app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["kind"] == "message_review_merge"
    assert run["profile_id"] == source_profile
    assert "keep it concise" in run["prompt"]
    assert "Original answer" in run["prompt"]
    updated = client.get(f"/api/messages/{message_id}/reviews", headers=headers).json()["reviews"][0]
    assert updated["status"] == "queued"
    assert updated["run_id"] == run_id
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert [m["role"] for m in messages] == ["assistant"]
    app.state.db.execute("UPDATE runs SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
    run_dict = dict(app.state.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    assert app.state.worker._complete_message_review(run_dict, "Merged answer", "stop") is True
    merged = client.get(f"/api/messages/{message_id}/reviews", headers=headers).json()["reviews"][0]
    assert merged["status"] == "done"
    assert merged["revised_content"] == "Merged answer"
    assert merged["merge_transcript"] == "Merged answer"

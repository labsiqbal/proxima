from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.runner_specs import default_runner


def authed(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_session_and_messages_lifecycle(tmp_path):
    client, headers = authed(tmp_path)

    created = client.post("/api/sessions", headers=headers, json={"title": "First chat"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    # Empty sessions are deferred from the sidebar until they have a message.
    msg = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=headers,
        json={"role": "user", "content": "/status"},
    )
    assert msg.status_code == 200

    listed = client.get("/api/sessions", headers=headers).json()["sessions"]
    assert listed[0]["title"] == "First chat"
    # A session's runner_id comes from the profile it was created under (the
    # default profile here), not from any value sent in the create payload — so it
    # must equal default_runner() as resolved on this host.
    assert listed[0]["runner_id"] == default_runner()

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "/status"
    assert "output_links" not in messages[0]


def test_session_detail_returns_an_empty_session_for_direct_review_handoffs(tmp_path):
    client, headers = authed(tmp_path)
    slug = client.get("/api/projects", headers=headers).json()["projects"][0]["slug"]
    created = client.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Artifact review", "project_slug": slug},
    ).json()

    # Empty sessions stay out of the sidebar, but an artifact lineage link can still
    # resolve the exact session before placing review feedback in its composer.
    assert client.get("/api/sessions", headers=headers).json()["sessions"] == []
    detail = client.get(f"/api/sessions/{created['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]
    assert detail.json()["project_slug"] == slug


def test_me_rejects_stale_token_so_frontend_can_refresh(tmp_path):
    client, headers = authed(tmp_path)

    assert client.get("/api/me", headers={"Authorization": "Bearer stale-token"}).status_code == 401
    assert client.get("/api/me", headers=headers).status_code == 200
    # Other API routes remain single-user/network-gated; the strict token check is
    # only for the frontend boot probe that decides whether to mint a fresh URL token.
    assert client.get("/api/projects", headers={"Authorization": "Bearer stale-token"}).status_code == 200


def test_messages_return_output_links(tmp_path):
    client, headers = authed(tmp_path)
    slug = client.get("/api/projects", headers=headers).json()["projects"][0]["slug"]
    sid = client.post("/api/sessions", headers=headers, json={"title": "outputs", "project_slug": slug}).json()["id"]
    client.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, author, output_links) VALUES (?, 'assistant', ?, 'Agent', ?)",
        (sid, "Done.", '[{"type":"design","title":"Launch Post","path":"artifacts/design/launch","id":"launch","project_slug":"%s"}]' % slug),
    )

    messages = client.get(f"/api/sessions/{sid}/messages", headers=headers).json()["messages"]

    assert messages[0]["output_links"] == [{
        "type": "design",
        "title": "Launch Post",
        "path": "artifacts/design/launch",
        "id": "launch",
        "project_slug": slug,
    }]


def test_messages_batch_activity_and_duration_for_multiple_runs(tmp_path):
    client, headers = authed(tmp_path)
    sid = client.post("/api/sessions", headers=headers, json={"title": "batch"}).json()["id"]
    session = client.app.state.db.execute(
        "SELECT owner_user_id, profile_id, runner_id, project_id FROM sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    run_ids: list[int] = []
    for index, seconds in enumerate((5, 8), start=1):
        cur = client.app.state.db.execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?, 'completed', ?, '2026-01-01 00:00:00', ?)",
            (
                sid,
                session["project_id"],
                session["owner_user_id"],
                session["profile_id"],
                session["runner_id"],
                f"run {index}",
                f"2026-01-01 00:00:0{seconds}",
            ),
        )
        run_id = int(cur.lastrowid)
        run_ids.append(run_id)
        client.app.state.db.execute(
            "INSERT INTO messages(session_id, role, content, run_id) VALUES (?, 'assistant', ?, ?)",
            (sid, f"answer {index}", run_id),
        )
        client.app.state.db.execute(
            "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) VALUES (?, ?, ?, 1, 'tool.start', ?)",
            (run_id, sid, session["project_id"], json.dumps({"id": f"tool-{index}", "title": f"Tool {index}"})),
        )
        client.app.state.db.execute(
            "INSERT INTO events(run_id, session_id, project_id, seq, type, payload) VALUES (?, ?, ?, 2, 'tool.complete', ?)",
            (run_id, sid, session["project_id"], json.dumps({"id": f"tool-{index}", "status": "completed"})),
        )

    messages = client.get(f"/api/sessions/{sid}/messages", headers=headers).json()["messages"]
    assistants = [message for message in messages if message["role"] == "assistant"]

    assert [message["run_id"] for message in assistants] == run_ids
    assert [message["duration_s"] for message in assistants] == [5, 8]
    assert [message["activity"][0]["title"] for message in assistants] == ["Tool 1", "Tool 2"]
    assert all(message["activity"][0]["status"] == "completed" for message in assistants)


def test_session_rename_and_delete(tmp_path):
    client, headers = authed(tmp_path)
    sid = client.post("/api/sessions", headers=headers, json={"title": "Old name"}).json()["id"]
    # A message makes the session appear in the list (empty ones are deferred).
    client.post(f"/api/sessions/{sid}/messages", headers=headers, json={"role": "user", "content": "hi"})

    renamed = client.patch(f"/api/sessions/{sid}", headers=headers, json={"title": "New name"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "New name"
    assert client.app.state.db.execute("SELECT manual_title FROM sessions WHERE id = ?", (sid,)).fetchone()["manual_title"] == 1
    assert client.get("/api/sessions", headers=headers).json()["sessions"][0]["title"] == "New name"

    deleted = client.delete(f"/api/sessions/{sid}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/api/sessions", headers=headers).json()["sessions"] == []
    # messages of a deleted session are gone (cascade) -> session not found
    assert client.get(f"/api/sessions/{sid}/messages", headers=headers).status_code == 404


def test_events_resume_cursor_is_id_not_per_run_seq(tmp_path):
    # Each run restarts seq at 1, so seq is not a session-level cursor. The events
    # list/stream must resume by events.id (session-monotonic). Regression for the
    # "second Save-to-wiki never opens" bug (both drafts landed at per-run seq 2).
    client, headers = authed(tmp_path)
    sid = client.post("/api/sessions", headers=headers, json={"title": "s"}).json()["id"]
    r1 = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "a"}).json()["run_id"]
    r2 = client.post(f"/api/sessions/{sid}/runs", headers=headers, json={"message": "b"}).json()["run_id"]
    w = client.app.state.worker
    with client.app.state.db_lock:
        w.add_event(r1, sid, None, "wiki.draft", {"n": 1})  # run r1, per-run seq 2
        w.add_event(r2, sid, None, "wiki.draft", {"n": 2})  # run r2, per-run seq 2 again

    allev = client.get(f"/api/sessions/{sid}/events", headers=headers).json()["events"]
    drafts = [e for e in allev if e["type"] == "wiki.draft"]
    assert len(drafts) == 2                                   # both drafts delivered
    assert drafts[0]["id"] < drafts[1]["id"]                  # distinct, increasing ids
    assert drafts[0]["seq"] == drafts[1]["seq"]               # identical per-run seq (why seq-dedup broke)

    # resume past r1's last event: r2's wiki.draft has the SAME per-run seq (2) as
    # r1's, so the old `seq > after_seq` cursor would have dropped it. The id cursor
    # correctly returns it.
    cutoff = max(e["id"] for e in allev if e["run_id"] == r1)
    after = client.get(f"/api/sessions/{sid}/events?after_id={cutoff}", headers=headers).json()["events"]
    assert {e["run_id"] for e in after} == {r2}
    assert any(e["type"] == "wiki.draft" and e["seq"] == 2 for e in after)


def test_generate_title_handles_whitespace_only_runner_output(tmp_path):
    client, _headers = authed(tmp_path)

    class WhitespaceTitleProc:
        async def new_session(self, cwd):
            return "title-session"

        async def prompt(self, sid, text, on_update, timeout=30, images=None):
            on_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "   \n\t  "}})
            return "end_turn"

    title = asyncio.run(client.app.state.worker._generate_title(WhitespaceTitleProc(), str(tmp_path), "hi", "hello"))
    assert title == ""

"""Inbox ledger: persistent notifications, read state, ephemeral header (#157/#158).

The Inbox extends the existing ``attention_items`` table rather than forking a
second notification store, so these tests assert the two axes stay independent:
``read_at`` (has the owner seen it) and ``status`` (does it still need them).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api import app_settings
from proxima_api.main import create_app
from proxima_api.master_runtime import execute_tool


def _client(tmp_path: Path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "owner", "os_user": "owner"}],
            "start_worker": False,
        }
    )
    client = TestClient(app)
    token = client.post("/auth/auto").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    created = client.post(
        "/api/projects", json={"slug": "inbox-project", "name": "Inbox project"}
    )
    assert created.status_code == 201
    return app, client


def _insert_attention(app, *, kind="master_budget", title="Master unattended work stopped", target=None, source_key="master-budget:1:turn budget exhausted"):
    app.state.db.execute(
        "INSERT INTO attention_items(kind, title, target_json, inline_ok, status, source_key) "
        "VALUES (?, ?, ?, 0, 'open', ?)",
        (kind, title, json.dumps(target or {"view": "master", "section": "budgets"}), source_key),
    )
    app.state.db.commit()


def _stop_master_for_budget(app, client):
    """Drive the real supervisor into a budget stop so the item is genuine."""
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    project = client.get("/api/projects").json()["projects"][0]
    execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "tasks": [
                {"title": "Queued A", "brief": "Do A", "project_slug": project["slug"]},
                {"title": "Queued B", "brief": "Do B", "project_slug": project["slug"]},
            ],
            "start": False,
        },
    )
    app_settings.set_master_settings(
        app.state.worker_db, unattended=True, budget_turns=1
    )
    app.state.master_supervisor.tick()
    app.state.master_supervisor.tick()


# ── The Inbox is the persistent home ──────────────────────────────────────────


def test_inbox_lists_notifications_with_unread_state(tmp_path: Path):
    app, client = _client(tmp_path)
    _insert_attention(app)

    body = client.get("/api/inbox").json()

    assert body["unread"] == 1
    item = next(i for i in body["items"] if i["kind"] == "master_budget")
    assert item["read"] is False
    assert item["status"] == "open"
    assert item["title"] == "Master unattended work stopped"


def test_marking_read_keeps_the_inbox_copy_but_clears_the_header(tmp_path: Path):
    app, client = _client(tmp_path)
    _insert_attention(app)
    item_id = client.get("/api/inbox").json()["items"][0]["id"]

    marked = client.post(f"/api/inbox/{item_id}/read", json={"read": True})

    assert marked.status_code == 200
    inbox = client.get("/api/inbox").json()
    assert inbox["unread"] == 0
    assert [i["read"] for i in inbox["items"] if i["id"] == item_id] == [True]
    assert client.get("/api/attention").json()["items"] == []


def test_read_can_be_undone(tmp_path: Path):
    app, client = _client(tmp_path)
    _insert_attention(app)
    item_id = client.get("/api/inbox").json()["items"][0]["id"]
    client.post(f"/api/inbox/{item_id}/read", json={"read": True})

    client.post(f"/api/inbox/{item_id}/read", json={"read": False})

    assert client.get("/api/inbox").json()["unread"] == 1
    assert client.get("/api/attention").json()["count"] == 1


def test_unread_filter_and_read_all(tmp_path: Path):
    app, client = _client(tmp_path)
    _insert_attention(app, source_key="a")
    _insert_attention(app, source_key="b", title="Second")

    assert len(client.get("/api/inbox?unread=1").json()["items"]) == 2
    done = client.post("/api/inbox/read-all", json={})

    assert done.status_code == 200
    assert done.json()["read"] == 2
    assert client.get("/api/inbox?unread=1").json()["items"] == []
    assert len(client.get("/api/inbox").json()["items"]) == 2


# ── Header is ephemeral; dismissal never loses the Inbox copy (#157) ──────────


def test_navigate_only_attention_item_can_be_dismissed_from_the_header(
    tmp_path: Path,
):
    app, client = _client(tmp_path)
    _insert_attention(app)
    item = client.get("/api/attention").json()["items"][0]
    assert item["inline_ok"] is False  # navigate-only: /act refuses it

    dismissed = client.post(f"/api/attention/{item['id']}/dismiss", json={})

    assert dismissed.status_code == 200
    assert client.get("/api/attention").json()["count"] == 0
    inbox_ids = [i["id"] for i in client.get("/api/inbox").json()["items"]]
    assert item["id"] in inbox_ids


def test_dismissing_does_not_resolve_work_that_still_needs_the_owner(tmp_path: Path):
    app, client = _client(tmp_path)
    _insert_attention(
        app,
        kind="container_ops_migration",
        title="Container Ops migration needs attention",
        target={"container_slug": "legacy", "reason": "physical Ops root is not empty"},
        source_key="container-ops-migration:1",
    )
    item = client.get("/api/attention").json()["items"][0]

    client.post(f"/api/attention/{item['id']}/dismiss", json={})

    stored = client.get("/api/inbox").json()["items"][0]
    assert stored["status"] == "open"
    assert stored["requires_action"] is True


def test_acknowledging_a_pure_notice_settles_it(tmp_path: Path):
    """A Master budget notice has no decision behind it, so acknowledging it in
    the header must also clear it from the Master desk's work panel (#157)."""
    app, client = _client(tmp_path)
    _insert_attention(app)
    item = client.get("/api/attention").json()["items"][0]

    client.post(f"/api/attention/{item['id']}/dismiss", json={})

    stored = client.get("/api/inbox").json()["items"][0]
    assert stored["status"] == "resolved"
    assert stored["read"] is True
    assert client.get("/api/attention").json()["count"] == 0


def test_dismiss_rejects_an_unknown_item(tmp_path: Path):
    _, client = _client(tmp_path)

    assert client.post("/api/attention/attention:404/dismiss", json={}).status_code == 404


# ── Stale Master budget items clear themselves (#157) ─────────────────────────


def test_master_budget_item_clears_itself_once_unattended_runs_again(tmp_path: Path):
    app, client = _client(tmp_path)
    _stop_master_for_budget(app, client)
    assert any(
        item["kind"] == "master_budget"
        for item in client.get("/api/attention").json()["items"]
    )

    app_settings.set_master_settings(app.state.worker_db, unattended=True)

    attention = client.get("/api/attention").json()["items"]
    assert [i for i in attention if i["kind"] == "master_budget"] == []
    stale = next(
        i for i in client.get("/api/inbox").json()["items"] if i["kind"] == "master_budget"
    )
    assert stale["status"] == "resolved"


def test_a_second_budget_stop_notifies_again(tmp_path: Path):
    app, client = _client(tmp_path)
    _stop_master_for_budget(app, client)
    first = next(
        i for i in client.get("/api/attention").json()["items"]
        if i["kind"] == "master_budget"
    )
    client.post(f"/api/attention/{first['id']}/dismiss", json={})

    # The owner restarts unattended work; it exhausts its budget again.
    _stop_master_for_budget(app, client)

    fresh = [
        i for i in client.get("/api/attention").json()["items"]
        if i["kind"] == "master_budget"
    ]
    assert len(fresh) == 1
    assert fresh[0]["id"] != first["id"]


# ── Everything in the header is in the Inbox (superset) ───────────────────────


def test_derived_review_item_is_recorded_in_the_inbox(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    job = client.post(
        "/api/jobs",
        json={"title": "Ship it", "project_slug": project["slug"], "steps": ["do"]},
    ).json()
    app.state.db.execute(
        "UPDATE jobs SET status='review', steps_state=? WHERE id=?",
        (json.dumps([{"name": "do", "status": "done"}]), job["id"]),
    )
    app.state.db.commit()

    header = client.get("/api/attention").json()["items"]
    review = next(i for i in header if i["id"] == f"job:{job['id']}")

    inbox = client.get("/api/inbox").json()["items"]
    stored = next(i for i in inbox if i["id"] == review["id"])
    assert stored["title"] == review["title"]
    assert stored["read"] is False


def test_task_outcomes_land_in_the_inbox_with_their_error_detail(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    job = client.post(
        "/api/jobs",
        json={"title": "Nightly build", "project_slug": project["slug"], "steps": ["do"]},
    ).json()
    app.state.db.execute(
        "UPDATE jobs SET status='failed', steps_state=? WHERE id=?",
        (
            json.dumps(
                [{"name": "do", "status": "failed", "error": "step 1 exited with code 2"}]
            ),
            job["id"],
        ),
    )
    app.state.db.commit()

    inbox = client.get("/api/inbox").json()["items"]
    entry = next(i for i in inbox if i["kind"] == "task_outcome")
    assert entry["severity"] == "error"
    assert entry["requires_action"] is False
    assert "step 1 exited with code 2" in entry["body"]
    assert entry["target"]["job_id"] == job["id"]


def test_task_outcome_is_recorded_once_and_stays_read(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    job = client.post(
        "/api/jobs",
        json={"title": "Nightly build", "project_slug": project["slug"], "steps": ["do"]},
    ).json()
    app.state.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))
    app.state.db.commit()
    item_id = next(
        i["id"]
        for i in client.get("/api/inbox").json()["items"]
        if i["kind"] == "task_outcome"
    )
    client.post(f"/api/inbox/{item_id}/read", json={"read": True})

    assert client.get("/api/inbox").json()["unread"] == 0
    entries = [
        i for i in client.get("/api/inbox").json()["items"] if i["kind"] == "task_outcome"
    ]
    assert len(entries) == 1


def test_browser_errors_reach_the_inbox_with_their_detail(tmp_path: Path):
    _, client = _client(tmp_path)

    filed = client.post(
        "/api/inbox/client-error",
        json={
            "key": "api:/api/jobs:500",
            "title": "Failed to start the Task (500)",
            "detail": "TypeError: cannot read property 'id' of undefined",
        },
    )

    assert filed.status_code == 200 and filed.json()["ok"] is True
    entry = next(
        i for i in client.get("/api/inbox").json()["items"] if i["kind"] == "client_error"
    )
    assert entry["severity"] == "error"
    assert entry["requires_action"] is False
    assert "cannot read property" in entry["body"]


def test_repeat_browser_errors_collapse_onto_one_row(tmp_path: Path):
    _, client = _client(tmp_path)
    body = {"key": "chunk-load", "title": "Reload Proxima", "detail": "stale chunk"}

    for _ in range(3):
        client.post("/api/inbox/client-error", json=body)

    entries = [
        i for i in client.get("/api/inbox").json()["items"] if i["kind"] == "client_error"
    ]
    assert len(entries) == 1


def test_browser_errors_cannot_flood_the_ledger(tmp_path: Path):
    _, client = _client(tmp_path)

    for index in range(60):
        client.post(
            "/api/inbox/client-error",
            json={"key": f"loop-{index}", "title": f"Render loop {index}", "detail": "x"},
        )

    entries = [
        i
        for i in client.get("/api/inbox?limit=200").json()["items"]
        if i["kind"] == "client_error"
    ]
    assert len(entries) == 50


def test_inbox_pages_older_notifications_without_skipping_any(tmp_path: Path):
    app, client = _client(tmp_path)
    for index in range(5):
        _insert_attention(app, title=f"Notice {index}", source_key=f"notice-{index}")

    first = client.get("/api/inbox?limit=2").json()
    second = client.get(f"/api/inbox?limit=2&before={first['next_before']}").json()
    third = client.get(f"/api/inbox?limit=2&before={second['next_before']}").json()

    seen = [item["title"] for item in first["items"] + second["items"] + third["items"]]
    assert seen == [f"Notice {index}" for index in reversed(range(5))]


def test_an_oversized_page_request_still_reports_its_next_cursor(tmp_path: Path):
    app, client = _client(tmp_path)
    for index in range(3):
        _insert_attention(app, title=f"Notice {index}", source_key=f"notice-{index}")

    page = client.get("/api/inbox?limit=100000").json()

    # The server serves its own bound; it must not claim there is nothing more
    # simply because it returned fewer rows than the caller asked for.
    assert len(page["items"]) == 3
    assert page["next_before"] is None


def test_work_that_finished_before_the_inbox_existed_is_not_replayed(tmp_path: Path):
    app, client = _client(tmp_path)
    project = client.get("/api/projects").json()["projects"][0]
    job = client.post(
        "/api/jobs",
        json={"title": "Ancient work", "project_slug": project["slug"], "steps": ["do"]},
    ).json()
    app.state.db.execute(
        "UPDATE jobs SET status='done', finished_at='2020-01-01 00:00:00', "
        "updated_at='2020-01-01 00:00:00' WHERE id=?",
        (job["id"],),
    )
    app.state.db.commit()

    inbox = client.get("/api/inbox").json()

    assert [i for i in inbox["items"] if i["kind"] == "task_outcome"] == []

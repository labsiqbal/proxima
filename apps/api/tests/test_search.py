"""Global search returns enough session metadata to open design hits."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "feature_design_studio": True,
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    return c, headers


def test_search_includes_mode_and_project_for_chats_and_messages(tmp_path: Path):
    c, headers = _client(tmp_path)
    proj = c.post("/api/projects", headers=headers, json={"slug": "demo", "name": "Demo"}).json()
    Path(proj["path"]).mkdir(parents=True, exist_ok=True)

    design = c.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Design: farewell announcement card", "project_slug": "demo", "mode": "design"},
    ).json()
    chat = c.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Plan farewell copy", "project_slug": "demo", "mode": "chat"},
    ).json()

    # Messages power message-search hits; design sessions stay out of GET /api/sessions.
    assert c.post(
        f"/api/sessions/{design['id']}/messages",
        headers=headers,
        json={"role": "user", "content": "A farewell announcement card with sunset gradient"},
    ).status_code == 200
    assert c.post(
        f"/api/sessions/{chat['id']}/messages",
        headers=headers,
        json={"role": "user", "content": "Write farewell copy for the demo"},
    ).status_code == 200

    listed = c.get("/api/sessions", headers=headers).json()["sessions"]
    assert all(s["id"] != design["id"] for s in listed)

    body = c.get("/api/search?q=farewell", headers=headers).json()
    chat_hit = next(h for h in body["chats"] if h["id"] == design["id"])
    assert chat_hit["mode"] == "design"
    assert chat_hit["project_slug"] == "demo"
    assert chat_hit["project_name"] == "Demo"

    msg_hit = next(h for h in body["messages"] if h["session_id"] == design["id"])
    assert msg_hit["mode"] == "design"
    assert msg_hit["project_slug"] == "demo"
    assert "farewell" in msg_hit["snippet"].lower()

    plain = next(h for h in body["chats"] if h["id"] == chat["id"])
    assert plain["mode"] == "chat"
    assert plain["project_slug"] == "demo"

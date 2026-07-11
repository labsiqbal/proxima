from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.commands import execute_command, normalize_command
from proxima_api.main import create_app


def test_normalize_command_alias_and_force_raw():
    assert normalize_command("status") == ("/status", "", False)
    assert normalize_command("/reset") == ("/new", "", False)
    assert normalize_command("//model sonnet") == ("/model", "sonnet", True)


def test_execute_command_surfaces():
    user = {"username": "bob", "role": "member"}
    assert execute_command("/help", user=user)["message"].startswith("Proxima commands")
    assert "Command router: ready" in execute_command("/status", user=user, project_slug="demo", runner_id="hermes")["message"]
    assert execute_command("/model", user=user)["surface"] == "ui-owned"
    assert execute_command("/clear", user=user)["surface"] == "terminal-only"
    assert execute_command("//model sonnet", user=user)["kind"] == "runner_raw"
    assert execute_command("/unknown", user=user)["surface"] == "unknown"


def test_command_endpoints_catalog_and_execute(tmp_path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
        }
    )
    client = TestClient(app)

    token = client.post("/auth/auto").json()["token"]

    catalog = client.get("/api/commands/catalog", headers={"Authorization": f"Bearer {token}"})
    assert catalog.status_code == 200
    assert any(group["label"] == "Session" for group in catalog.json()["groups"])

    executed = client.post(
        "/api/commands/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={"command": "/status", "runner_id": "hermes"},
    )
    assert executed.status_code == 200
    assert "Command router: ready" in executed.json()["message"]

    client.post("/api/projects", headers={"Authorization": f"Bearer {token}"}, json={"slug": "alpha", "name": "Alpha"})
    scoped = client.post(
        "/api/commands/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={"command": "/status", "project_slug": "alpha", "runner_id": "codex"},
    )
    assert scoped.status_code == 200
    assert "Project: alpha" in scoped.json()["message"]
    assert "Runner: codex" in scoped.json()["message"]

from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app


def test_invites_disabled_in_single_user(tmp_path):
    # In single-user cockpit mode the whole invite surface is hidden (404) — the
    # access gate is Cloudflare Access, not in-app account creation.
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
        "single_user": True,
        "single_user_name": "alice",
    })
    c = TestClient(app)
    token = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    # admin-authed invite endpoints 404 (not 403) — the surface doesn't exist here
    assert c.post("/api/invites", headers=h, json={"role": "member"}).status_code == 404
    assert c.get("/api/invites", headers=h).status_code == 404
    assert c.delete("/api/invites/whatever", headers=h).status_code == 404
    # public preview + redeem (the account-creation vector) are closed too
    assert c.get("/api/invites/whatever").status_code == 404
    assert c.post("/api/invites/whatever/redeem", json={"username": "intruder", "password": "password123"}).status_code == 404

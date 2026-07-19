from __future__ import annotations

from http.cookies import SimpleCookie

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.preview_proxy import mint_preview_token, valid_preview_token


def _app(tmp_path):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "apps_domain": "apps.example.test",
        "start_worker": False,
    })


def test_preview_capability_is_signed_short_lived_and_tamper_evident():
    secret = b"preview-secret"
    token = mint_preview_token(secret, ttl_seconds=60)

    assert valid_preview_token(secret, token)
    assert not valid_preview_token(b"different-secret", token)
    assert not valid_preview_token(secret, token + "x")
    assert not valid_preview_token(secret, token, now=10**12)


def test_preview_auth_never_copies_owner_bearer_token(tmp_path):
    client = TestClient(_app(tmp_path))
    owner_token = client.post("/auth/auto").json()["token"]
    response = client.post(
        "/api/preview-auth",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 200
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    preview_token = cookie["proxima_preview"].value
    assert preview_token
    assert preview_token != owner_token
    assert owner_token not in response.headers["set-cookie"]


def test_permission_auto_approval_defaults_off(tmp_path):
    client = TestClient(_app(tmp_path))
    token = client.post("/auth/auto").json()["token"]
    response = client.get(
        "/api/settings/permissions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.json() == {"auto_approve": False}

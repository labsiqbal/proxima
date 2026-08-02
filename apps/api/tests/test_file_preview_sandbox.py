"""Boundary tests for the sandboxed canonical file preview (ADR-0042).

The preview is served from Proxima's own origin, so the only thing keeping a
previewed document away from the owner's session is the sandbox: the iframe
never gets `allow-same-origin` and every response repeats that as a CSP
`sandbox` directive. Scripts additionally need explicit owner consent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app

PREVIEW_SESSION = "S" * 32


def _api(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "identity", "name": "File Identity"},
    )
    assert created.status_code == 201, created.text
    return api, headers, Path(created.json()["path"])


def _ops_area_id(api: TestClient) -> int:
    row = api.app.state.db.execute(
        "SELECT pa.id FROM project_areas pa "
        "JOIN projects p ON p.id = pa.project_id "
        "WHERE p.slug = 'identity' AND pa.kind = 'ops'"
    ).fetchone()
    return int(row["id"])


def _target(area_id: int, path: str = "index.html") -> dict:
    return {
        "project": "identity",
        "area": {"kind": "ops", "id": area_id},
        "path": path,
    }


def _target_params(target: dict) -> dict[str, str]:
    return {"target": json.dumps(target, separators=(",", ":"))}


def _write_html(root: Path, name: str = "index.html") -> None:
    (root / "ops" / name).write_text(
        "<script>globalThis.executed = true</script>",
        encoding="utf-8",
    )


def _policy(response) -> dict[str, str]:
    directives = {}
    for part in response.headers["content-security-policy"].split(";"):
        item = part.strip()
        if not item:
            continue
        name, _, value = item.partition(" ")
        directives[name.lower()] = value.strip()
    return directives


def _enable_active(
    api: TestClient,
    headers: dict[str, str],
    target: dict,
    *,
    preview_session: str = PREVIEW_SESSION,
):
    return api.post(
        "/api/projects/identity/preview-mode",
        headers=headers,
        params=_target_params(target),
        json={"active": True, "preview_session": preview_session},
    )


def test_passive_html_preview_is_sandboxed_without_same_origin(
    tmp_path: Path,
) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)

    response = api.get(
        f"/api/target-preview/identity/ops/{area_id}/index.html",
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    policy = _policy(response)
    assert "sandbox" in policy
    assert "allow-same-origin" not in response.headers["content-security-policy"]
    assert "allow-scripts" not in response.headers["content-security-policy"]
    assert policy["default-src"] == "'none'"
    assert policy["frame-ancestors"] == "'self'"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    # No capability choreography: no dedicated origin, no minted cookie.
    assert "set-cookie" not in response.headers
    assert "x-frame-options" not in response.headers


def test_preview_never_leaves_the_proxima_origin(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)

    response = api.get(
        f"/api/target-preview/identity/ops/{area_id}/index.html",
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "location" not in response.headers


def test_active_preview_requires_recorded_owner_consent(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)
    url = f"/api/target-preview/identity/ops/{area_id}/index.html"
    active_params = {
        "__proxima_mode": "active",
        "__proxima_preview_session": PREVIEW_SESSION,
    }

    refused = api.get(
        url,
        headers=headers,
        params=active_params,
        follow_redirects=False,
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "active file preview consent is unavailable"
    )

    enabled = _enable_active(api, headers, _target(area_id))
    assert enabled.status_code == 200, enabled.text
    assert enabled.json() == {"active": True}

    granted = api.get(
        url,
        headers=headers,
        params=active_params,
        follow_redirects=False,
    )
    assert granted.status_code == 200
    policy = _policy(granted)
    assert policy["sandbox"] == "allow-scripts"
    # Scripts, yes. Proxima's origin, never.
    assert "allow-same-origin" not in granted.headers["content-security-policy"]
    assert policy["connect-src"] == "*"


def test_active_consent_requires_the_bearer_token(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)

    cookie_only = api.post(
        "/api/projects/identity/preview-mode",
        params=_target_params(_target(area_id)),
        json={"active": True, "preview_session": PREVIEW_SESSION},
    )

    assert cookie_only.status_code == 401
    assert cookie_only.json()["detail"] == (
        "active preview changes require owner authorization"
    )


def test_active_consent_is_scoped_to_one_area_and_one_viewer(
    tmp_path: Path,
) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    _write_html(root, "other.html")
    area_id = _ops_area_id(api)
    assert _enable_active(api, headers, _target(area_id)).status_code == 200

    other_viewer = api.get(
        f"/api/target-preview/identity/ops/{area_id}/index.html",
        headers=headers,
        params={
            "__proxima_mode": "active",
            "__proxima_preview_session": "O" * 32,
        },
        follow_redirects=False,
    )
    assert other_viewer.status_code == 403

    container_area = api.get(
        "/api/target-preview/identity/container/root/ops/index.html",
        headers=headers,
        params={
            "__proxima_mode": "active",
            "__proxima_preview_session": PREVIEW_SESSION,
        },
        follow_redirects=False,
    )
    assert container_area.status_code in {400, 403}

    # Consent covers the Area, so a sibling document in the same Area shares it -
    # the viewer only ever renders the file it was opened on.
    sibling = api.get(
        f"/api/target-preview/identity/ops/{area_id}/other.html",
        headers=headers,
        params={
            "__proxima_mode": "active",
            "__proxima_preview_session": PREVIEW_SESSION,
        },
        follow_redirects=False,
    )
    assert sibling.status_code == 200


def test_disabling_consent_returns_the_preview_to_passive(
    tmp_path: Path,
) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)
    url = f"/api/target-preview/identity/ops/{area_id}/index.html"
    assert _enable_active(api, headers, _target(area_id)).status_code == 200

    disabled = api.post(
        "/api/projects/identity/preview-mode",
        headers=headers,
        params=_target_params(_target(area_id)),
        json={"active": False, "preview_session": PREVIEW_SESSION},
    )
    assert disabled.status_code == 200
    assert disabled.json() == {"active": False}

    stale = api.get(
        url,
        headers=headers,
        params={
            "__proxima_mode": "active",
            "__proxima_preview_session": PREVIEW_SESSION,
        },
        follow_redirects=False,
    )
    assert stale.status_code == 403

    passive = api.get(url, headers=headers, follow_redirects=False)
    assert passive.status_code == 200
    assert "allow-scripts" not in passive.headers["content-security-policy"]


def test_active_preview_dies_with_the_owner_session(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)
    assert _enable_active(api, headers, _target(area_id)).status_code == 200
    api.app.state.db.execute(
        "UPDATE auth_sessions SET expires_at = ?",
        (
            (datetime.now(timezone.utc) - timedelta(minutes=1))
            .isoformat()
            .replace("+00:00", "Z"),
        ),
    )

    expired = api.get(
        f"/api/target-preview/identity/ops/{area_id}/index.html",
        headers=headers,
        params={
            "__proxima_mode": "active",
            "__proxima_preview_session": PREVIEW_SESSION,
        },
        follow_redirects=False,
    )

    assert expired.status_code == 401


def test_active_mode_is_html_only(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    (root / "ops" / "diagram.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        encoding="utf-8",
    )
    area_id = _ops_area_id(api)

    consent = api.post(
        "/api/projects/identity/preview-mode",
        headers=headers,
        params=_target_params(_target(area_id, "diagram.svg")),
        json={"active": True, "preview_session": PREVIEW_SESSION},
    )
    assert consent.status_code == 400

    svg = api.get(
        f"/api/target-preview/identity/ops/{area_id}/diagram.svg",
        headers=headers,
        follow_redirects=False,
    )
    assert svg.status_code == 200
    assert svg.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in _policy(svg)


def test_legacy_path_preview_is_passive_and_sandboxed(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root, "legacy.html")

    response = api.get(
        "/api/preview/identity/ops/legacy.html",
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 200
    policy = _policy(response)
    assert policy["sandbox"] == ""
    assert policy["default-src"] == "'none'"
    assert "set-cookie" not in response.headers


def test_preview_content_cannot_pull_proxima_routes(tmp_path: Path) -> None:
    api, headers, _ = _api(tmp_path)

    embedded = api.get(
        "/api/projects",
        headers={
            **headers,
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "image",
        },
    )
    assert embedded.status_code == 403
    assert embedded.text == "preview content cannot access Proxima"

    opaque_form = api.get(
        "/api/projects",
        headers={**headers, "Origin": "null"},
    )
    assert opaque_form.status_code == 403

    framed_document = api.get(
        "/api/projects",
        headers={
            **headers,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
        },
    )
    assert framed_document.status_code == 200


def test_app_html_denies_framing(tmp_path: Path) -> None:
    api, headers, root = _api(tmp_path)
    _write_html(root)
    area_id = _ops_area_id(api)

    generated = api.get("/", headers=headers)
    if generated.status_code == 200 and "text/html" in generated.headers.get(
        "content-type",
        "",
    ):
        assert generated.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in (
            generated.headers["content-security-policy"]
        )

    # The preview declares its own framing policy and keeps it.
    preview = api.get(
        f"/api/target-preview/identity/ops/{area_id}/index.html",
        headers=headers,
    )
    assert "x-frame-options" not in preview.headers
    assert _policy(preview)["frame-ancestors"] == "'self'"

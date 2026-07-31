from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import (
    parse_qs,
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from proxima_api import (
    artifact_registry,
    container_registry,
    file_targets,
    target_preview,
)
from proxima_api.main import create_app


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc000000301010018dd8db1"
    "0000000049454e44ae426082"
)
PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] >>endobj\n"
    b"trailer<< /Root 1 0 R /Size 4 >>\n%%EOF\n"
)


def _clean_capability_url(url: str) -> str:
    parsed = urlsplit(url)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
            )
            if key != "__proxima_cap"
        ]
    )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
    )


def _api(
    tmp_path: Path,
    config: dict[str, object] | None = None,
) -> tuple[TestClient, dict[str, str], Path]:
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
            **(config or {}),
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "identity", "name": "File Identity"},
    )
    assert response.status_code == 201, response.text
    return api, headers, Path(response.json()["path"])


def _by_name(api: TestClient, headers: dict[str, str]) -> dict[str, dict]:
    response = api.get("/api/projects/identity/tree", headers=headers)
    assert response.status_code == 200, response.text
    return {entry["name"]: entry for entry in response.json()["entries"]}


def _target_params(target: dict) -> dict[str, str]:
    return {"target": json.dumps(target, separators=(",", ":"))}


def test_physical_ops_direct_files_keep_server_owned_identity_across_surfaces(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    ops = root / "ops"
    (root / "brief.md").write_text("# Container shadow", encoding="utf-8")
    (root / "visual.png").write_bytes(b"container image shadow")
    (root / "handout.pdf").write_bytes(b"container PDF shadow")
    (ops / "brief.md").write_text("# Ops direct brief", encoding="utf-8")
    (ops / "ops-only.md").write_text("# Ops only", encoding="utf-8")
    (ops / "visual.png").write_bytes(PNG_1X1)
    (ops / "handout.pdf").write_bytes(PDF)
    (root / "site").mkdir()
    (root / "site" / "theme.css").write_text(
        "body { color: wrong-container; }",
        encoding="utf-8",
    )
    (ops / "site").mkdir()
    (ops / "site" / "index.html").write_text(
        '<link rel="stylesheet" href="theme.css"><main>Ops page</main>',
        encoding="utf-8",
    )
    (ops / "site" / "theme.css").write_text(
        "body { color: canonical-ops; }",
        encoding="utf-8",
    )
    (ops / "site" / "module.js").write_text(
        "export const canonical = true",
        encoding="utf-8",
    )
    (ops / "site" / "worker.js").write_text(
        "self.postMessage('canonical')",
        encoding="utf-8",
    )
    (ops / "site" / "font.woff2").write_bytes(b"canonical-font")
    (ops / "site" / "data.json").write_text(
        '{"source":"canonical"}',
        encoding="utf-8",
    )
    (ops / "site" / "active.xhtml").write_text(
        "<html xmlns='http://www.w3.org/1999/xhtml'><script>top.name='x'</script></html>",
        encoding="utf-8",
    )
    (ops / "site" / "active.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><script>top.name='svg'</script></svg>",
        encoding="utf-8",
    )
    (root / "shadow.html").write_text(
        "<script>parent.document.body.dataset.previewEscape='true'</script>",
        encoding="utf-8",
    )

    entries = _by_name(api, headers)
    ops_only = entries["ops-only.md"]["target"]
    container_brief = entries["brief.md"]["target"]
    ops_area = api.app.state.db.execute(
        "SELECT id, project_id FROM project_areas "
        "WHERE project_id = (SELECT id FROM projects WHERE slug = 'identity') "
        "AND kind = 'ops' AND source != 'excluded'"
    ).fetchone()
    ops_area_id = ops_area["id"]
    project_id = ops_area["project_id"]

    assert ops_only == {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "ops-only.md",
    }
    assert container_brief == {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "brief.md",
    }
    read = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(ops_only),
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# Ops only"
    assert read.json()["target"] == ops_only

    # The merge policy keeps the Container entry for a generic same-name
    # collision, while an Ops artifact target remains authoritative.
    shadow_read = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(container_brief),
    )
    assert shadow_read.json()["content"] == "# Container shadow"

    artifact_items = api.get(
        "/api/projects/identity/artifacts?since_minutes=525600",
        headers=headers,
    ).json()["artifacts"]
    brief_artifact = next(item for item in artifact_items if item["path"] == "brief.md")
    assert brief_artifact["target"] == {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "brief.md",
    }
    ops_brief = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(brief_artifact["target"]),
    )
    assert ops_brief.json()["content"] == "# Ops direct brief"

    for name, expected in (("visual.png", PNG_1X1), ("handout.pdf", PDF)):
        target = {
            "project": "identity",
            "area": {"kind": "ops", "id": ops_area_id},
            "path": name,
        }
        raw = api.get(
            "/api/projects/identity/raw",
            headers=headers,
            params=_target_params(target),
        )
        assert raw.status_code == 200, raw.text
        assert raw.content == expected
        preview = api.get(
            f"/api/target-preview/identity/ops/{ops_area_id}/{name}",
            headers=headers,
        )
        assert preview.status_code == 200, preview.text
        assert preview.content == expected
        if name == "handout.pdf":
            assert preview.headers["content-security-policy"] == (
                "frame-ancestors http://testserver"
            )

    image_target = {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "visual.png",
    }
    design = api.post(
        "/api/projects/identity/designs/from-image",
        headers=headers,
        json={
            "path": "visual.png",
            "target": image_target,
            "title": "Canonical visual",
        },
    )
    assert design.status_code == 200, design.text
    scene = json.loads(
        (
            ops
            / design.json()["path"]
            / "scene.json"
        ).read_text(encoding="utf-8")
    )
    image_layer = scene["artboards"][0]["layers"][0]
    assert image_layer["src"] == "visual.png"
    assert image_layer["target"] == image_target

    malformed = api.post(
        "/api/projects/identity/designs/from-image",
        headers=headers,
        json={"path": "visual.png", "target": 1},
    )
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "invalid file target"

    preview_entry = api.get(
        f"/api/target-preview/identity/ops/{ops_area_id}/site/index.html",
        headers=headers,
        params={"cache": "7"},
        follow_redirects=False,
    )
    assert preview_entry.status_code == 307
    assert preview_entry.headers["cache-control"] == "private, no-store"
    assert preview_entry.headers["referrer-policy"] == "no-referrer"
    capability_url = preview_entry.headers["location"]
    capability_host = urlsplit(capability_url).hostname or ""
    assert capability_host == (
        f"file-{project_id}-ops-{ops_area_id}.testserver"
    )
    capability_query = parse_qs(urlsplit(capability_url).query)
    assert capability_query["cache"] == ["7"]
    assert capability_query["__proxima_cap"]

    frame_metadata = {
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
    }
    tampered_url = capability_url.replace(
        capability_query["__proxima_cap"][0],
        capability_query["__proxima_cap"][0] + "x",
    )
    assert api.get(
        tampered_url,
        headers=frame_metadata,
        follow_redirects=False,
    ).status_code == 403
    wrong_area_url = capability_url.replace(
        f"file-{project_id}-ops-{ops_area_id}.testserver",
        f"file-{project_id}-container-0.testserver",
    )
    assert api.get(
        wrong_area_url,
        headers=frame_metadata,
        follow_redirects=False,
    ).status_code == 403

    rejected_metadata = (
        {},
        {
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "navigate",
        },
        {
            "Sec-Fetch-Site": "invalid",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
        },
        {
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
        {
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
            "Origin": "null",
        },
    )
    for metadata in rejected_metadata:
        rejected = api.get(
            capability_url,
            headers=metadata,
            follow_redirects=False,
        )
        assert rejected.status_code == 403
        assert rejected.text == "preview request metadata is invalid"

    capability_gate = api.get(
        capability_url,
        headers=frame_metadata,
        follow_redirects=False,
    )
    assert capability_gate.status_code == 200
    assert capability_gate.headers["cache-control"] == "private, no-store"
    assert "SameSite=strict" in capability_gate.headers["set-cookie"]
    assert "Secure" not in capability_gate.headers["set-cookie"]
    assert "Domain=" not in capability_gate.headers["set-cookie"]
    assert "Path=/" in capability_gate.headers["set-cookie"]
    assert (
        f"proxima_file_preview_{project_id}_ops_{ops_area_id}="
        in capability_gate.headers["set-cookie"]
    )
    isolated_url = _clean_capability_url(capability_url)
    assert "content=\"0;url=/site/index.html?cache=7\"" in (
        capability_gate.text
    )
    clean_query = parse_qs(urlsplit(isolated_url).query)
    assert clean_query == {"cache": ["7"]}

    same_origin_metadata = {"Sec-Fetch-Site": "same-origin"}
    without_capability = TestClient(api.app)
    missing_cookie = without_capability.get(
        isolated_url,
        headers=same_origin_metadata,
    )
    assert missing_cookie.status_code == 403
    assert missing_cookie.text == "preview capability is invalid"
    assert without_capability.get(
        "http://file-invalid.testserver/api/health"
    ).status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect(
            f"ws://{capability_host}/api/sessions/1/ws"
        ):
            pass

    page = api.get(isolated_url, headers=same_origin_metadata)
    assert page.status_code == 200, page.text
    assert "Ops page" in page.text
    preview_policy = page.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-same-origin" in preview_policy
    assert "default-src 'self' data: blob:" in preview_policy
    assert "script-src 'self' 'unsafe-inline' blob:" in preview_policy
    assert "font-src 'self' data:" in preview_policy
    assert "connect-src 'self'" in preview_policy
    assert "worker-src 'self' blob:" in preview_policy
    assert "frame-ancestors http://testserver" in preview_policy
    assert f"http://{capability_host}" in preview_policy
    assert page.headers["cross-origin-opener-policy"] == "same-origin"
    assert page.headers["referrer-policy"] == "no-referrer"

    clean_top_level = api.get(
        isolated_url,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
    )
    assert clean_top_level.status_code == 403
    assert clean_top_level.text == "preview request metadata is invalid"

    clean_frame = api.get(
        isolated_url,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
        },
    )
    assert clean_frame.status_code == 200
    assert "Ops page" in clean_frame.text

    nested_asset = api.get(
        urljoin(isolated_url, "theme.css"),
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "style",
        },
    )
    assert nested_asset.status_code == 200, nested_asset.text
    assert nested_asset.text == "body { color: canonical-ops; }"
    module = api.get(
        urljoin(isolated_url, "module.js"),
        headers=same_origin_metadata,
    )
    worker = api.get(
        urljoin(isolated_url, "worker.js"),
        headers={
            **same_origin_metadata,
            "Sec-Fetch-Dest": "worker",
        },
    )
    font = api.get(
        urljoin(isolated_url, "font.woff2"),
        headers=same_origin_metadata,
    )
    fetched = api.get(
        urljoin(isolated_url, "data.json"),
        headers=same_origin_metadata,
    )
    assert module.status_code == 200
    assert module.headers["content-type"].startswith(
        ("text/javascript", "application/javascript")
    )
    assert worker.status_code == 200
    assert "connect-src 'self'" in worker.headers["content-security-policy"]
    assert "worker-src 'none'" in worker.headers["content-security-policy"]
    assert font.status_code == 200
    assert fetched.json() == {"source": "canonical"}
    service_worker = api.get(
        urljoin(isolated_url, "worker.js"),
        headers={
            **same_origin_metadata,
            "Service-Worker": "script",
        },
    )
    assert service_worker.status_code == 403
    assert service_worker.text == "service workers are unavailable"

    for active_name, media_type in (
        ("active.xhtml", "application/xhtml+xml"),
        ("active.svg", "image/svg+xml"),
    ):
        active = api.get(
            urljoin(isolated_url, active_name),
            headers=same_origin_metadata,
        )
        assert active.status_code == 200
        assert active.headers["content-type"].startswith(media_type)
        assert active.headers["content-disposition"].startswith("attachment;")
        assert "sandbox" in active.headers["content-security-policy"]

    legacy_collision = root / "area" / "ops" / str(ops_area_id) / "site"
    legacy_collision.mkdir(parents=True)
    (legacy_collision / "theme.css").write_text(
        "body { color: legacy-container; }",
        encoding="utf-8",
    )
    legacy_preview = api.get(
        f"/api/preview/identity/area/ops/{ops_area_id}/site/theme.css",
        headers=headers,
    )
    assert legacy_preview.status_code == 200, legacy_preview.text
    assert legacy_preview.text == "body { color: legacy-container; }"
    target_on_legacy = api.get(
        "/api/preview/identity/site/index.html",
        headers=headers,
        params=_target_params(
            {
                "project": "identity",
                "area": {"kind": "ops", "id": ops_area_id},
                "path": "site/index.html",
            }
        ),
    )
    assert target_on_legacy.status_code == 400
    assert target_on_legacy.json()["detail"] == (
        "legacy preview does not accept file targets"
    )
    assert api.get(
        "/api/preview/identity/site/index.html?target=",
        headers=headers,
    ).status_code == 400
    legacy_pdf = api.get(
        "/api/preview/identity/handout.pdf",
        headers=headers,
    )
    assert legacy_pdf.status_code == 200
    assert legacy_pdf.headers["content-security-policy"] == (
        "frame-ancestors http://testserver"
    )
    legacy_active = api.get(
        "/api/preview/identity/shadow.html",
        headers=headers,
        follow_redirects=False,
    )
    assert legacy_active.status_code == 307
    assert "/shadow.html?" in legacy_active.headers["location"]
    assert "__proxima_cap=" in legacy_active.headers["location"]
    blocked_absolute_navigation = api.get(
        "/api/preview/identity/shadow.html",
        headers={
            **headers,
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Dest": "iframe",
        },
    )
    assert blocked_absolute_navigation.status_code == 403
    assert blocked_absolute_navigation.text == (
        "preview navigation cannot access Proxima"
    )

    main_document = api.get("/docs")
    assert main_document.status_code == 200
    assert "frame-ancestors 'none'" in main_document.headers[
        "content-security-policy"
    ]
    assert main_document.headers["x-frame-options"] == "DENY"

    escaped_navigation = urljoin(
        isolated_url,
        "../../../../../../api/preview/identity/escape.png",
    )
    assert urlsplit(escaped_navigation).hostname == capability_host
    assert api.get(
        escaped_navigation,
        headers=same_origin_metadata,
    ).status_code == 404
    assert api.get(
        urljoin(isolated_url, "/api/health"),
        headers=same_origin_metadata,
    ).status_code == 404

    invalid_area = api.get(
        "/api/target-preview/identity/ops/999999/site/index.html",
        headers=headers,
        follow_redirects=False,
    )
    assert invalid_area.status_code == 400

    # Documented path-only callers remain compatible, including an explicit
    # physical ops/ path. Their response is upgraded to the canonical target.
    explicit = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params={"path": "ops/ops-only.md"},
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["content"] == "# Ops only"
    assert explicit.json()["target"] == ops_only


def test_https_remote_preview_requires_a_distinct_tls_origin(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, root = _api(tmp_path)
    ops = root / "ops"
    (ops / "index.html").write_text(
        '<script type="module" src="module.js"></script>',
        encoding="utf-8",
    )
    (ops / "module.js").write_text(
        "export const canonical = true",
        encoding="utf-8",
    )
    (ops / "handout.pdf").write_bytes(PDF)
    area = api.app.state.db.execute(
        "SELECT pa.id FROM project_areas pa "
        "JOIN projects p ON p.id = pa.project_id "
        "WHERE p.slug = 'identity' AND pa.kind = 'ops'"
    ).fetchone()

    async def relay_port(*_args):
        raise AssertionError("HTTPS preview must not start a plaintext relay")

    monkeypatch.setattr(api.app.state.target_previews, "_relay_port", relay_port)
    remote = TestClient(api.app, base_url="https://100.64.0.2")
    entry = remote.get(
        f"/api/target-preview/identity/ops/{area['id']}/index.html",
        headers=headers,
        follow_redirects=False,
    )

    assert entry.status_code == 503
    assert entry.json()["detail"] == (
        "dedicated file preview origin is unavailable"
    )
    pdf = remote.get(
        f"/api/target-preview/identity/ops/{area['id']}/handout.pdf",
        headers=headers,
    )
    assert pdf.status_code == 200
    assert pdf.content == PDF
    assert pdf.headers["content-security-policy"] == (
        "frame-ancestors https://100.64.0.2"
    )


def test_http_named_localhost_uses_a_scoped_bootstrap_cookie(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    (root / "ops" / "index.html").write_text(
        "<main>Named-local preview</main>",
        encoding="utf-8",
    )
    area = api.app.state.db.execute(
        "SELECT pa.id, pa.project_id FROM project_areas pa "
        "JOIN projects p ON p.id = pa.project_id "
        "WHERE p.slug = 'identity' AND pa.kind = 'ops'"
    ).fetchone()
    localhost = TestClient(
        api.app,
        base_url="http://localhost:8766",
    )
    entry = localhost.get(
        f"/api/target-preview/identity/ops/{area['id']}/index.html",
        headers=headers,
        follow_redirects=False,
    )

    assert entry.status_code == 307
    location = entry.headers["location"]
    assert urlsplit(location).netloc == (
        f"file-{area['project_id']}-ops-{area['id']}.localhost:8766"
    )
    frame_metadata = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
    }
    gate = localhost.get(
        location,
        headers=frame_metadata,
        follow_redirects=False,
    )

    assert gate.status_code == 200
    cookie_name = (
        f"proxima_file_preview_{area['project_id']}_ops_{area['id']}"
    )
    set_cookie = gate.headers["set-cookie"]
    assert f"{cookie_name}=" in set_cookie
    assert "SameSite=none" in set_cookie
    assert "Secure" in set_cookie
    assert "Domain=" not in set_cookie
    token = parse_qs(urlsplit(location).query)["__proxima_cap"][0]
    clean = localhost.get(
        _clean_capability_url(location),
        headers={
            **frame_metadata,
            "Cookie": f"{cookie_name}={token}",
        },
    )
    assert clean.status_code == 200
    assert "Named-local preview" in clean.text


def test_https_remote_preview_uses_a_distinct_tls_area_origin(
    tmp_path: Path,
):
    api, headers, root = _api(
        tmp_path,
        {"apps_domain": "preview.test"},
    )
    ops = root / "ops"
    (ops / "index.html").write_text(
        '<script>new Worker("worker.js", {type: "module"})</script>',
        encoding="utf-8",
    )
    (ops / "worker.js").write_text(
        "self.postMessage('canonical')",
        encoding="utf-8",
    )
    (ops / "image.png").write_bytes(PNG_1X1)
    (ops / "data.json").write_text(
        '{"source":"canonical"}',
        encoding="utf-8",
    )
    area = api.app.state.db.execute(
        "SELECT pa.id, pa.project_id FROM project_areas pa "
        "JOIN projects p ON p.id = pa.project_id "
        "WHERE p.slug = 'identity' AND pa.kind = 'ops'"
    ).fetchone()
    remote = TestClient(
        api.app,
        base_url="https://proxima.tailnet.test",
    )
    entry = remote.get(
        f"/api/target-preview/identity/ops/{area['id']}/index.html",
        headers=headers,
        follow_redirects=False,
    )

    assert entry.status_code == 307
    location = entry.headers["location"]
    parsed = urlsplit(location)
    preview_origin = (
        f"https://file-{area['project_id']}-ops-{area['id']}."
        "preview.test"
    )
    assert f"{parsed.scheme}://{parsed.netloc}" == preview_origin
    assert parsed.netloc != "proxima.tailnet.test"
    assert parse_qs(parsed.query)["__proxima_cap"]

    external_navigation = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
    }
    for metadata in (
        {},
        {
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
        },
        {
            "Sec-Fetch-Site": "invalid",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
        },
        {
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
    ):
        denied = remote.get(
            location,
            headers=metadata,
            follow_redirects=False,
        )
        assert denied.status_code == 403
        assert denied.text == "preview request metadata is invalid"

    capability_gate = remote.get(
        location,
        headers=external_navigation,
        follow_redirects=False,
    )
    assert capability_gate.status_code == 200
    assert "SameSite=none" in capability_gate.headers["set-cookie"]
    assert "Secure" in capability_gate.headers["set-cookie"]
    assert "Domain=" not in capability_gate.headers["set-cookie"]
    assert "Path=/" in capability_gate.headers["set-cookie"]
    page_url = _clean_capability_url(location)
    assert f'content="0;url={parsed.path}"' in capability_gate.text
    assert (
        "frame-ancestors https://proxima.tailnet.test"
        in capability_gate.headers["content-security-policy"]
    )
    cookie_frame_entry = remote.get(
        page_url,
        headers=external_navigation,
    )
    assert cookie_frame_entry.status_code == 200

    same_origin_navigation = {
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
    }
    page = remote.get(page_url, headers=same_origin_navigation)
    assert page.status_code == 200
    policy = page.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-same-origin" in policy
    assert "worker-src 'self' blob:" in policy
    assert (
        "frame-ancestors https://proxima.tailnet.test "
        f"{preview_origin}"
    ) in policy

    worker_url = urljoin(page_url, "worker.js")
    assert urlsplit(worker_url).netloc == parsed.netloc
    worker = remote.get(
        worker_url,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "same-origin",
            "Sec-Fetch-Dest": "worker",
        },
    )
    assert worker.status_code == 200
    assert "script-src 'self'" in worker.headers[
        "content-security-policy"
    ]
    assert "connect-src 'self'" in worker.headers[
        "content-security-policy"
    ]
    image_url = urljoin(page_url, "image.png")
    image = remote.get(
        image_url,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "image",
        },
    )
    assert image.status_code == 200
    assert image.content == PNG_1X1
    data_url = urljoin(page_url, "data.json")
    data = remote.get(
        data_url,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "",
        },
    )
    assert data.status_code == 200
    assert data.json() == {"source": "canonical"}

    for attack_url, attack_headers in (
        (
            worker_url,
            {
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Dest": "script",
            },
        ),
        (
            image_url,
            {
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Dest": "image",
            },
        ),
        (
            data_url,
            {
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "",
            },
        ),
    ):
        attack = remote.get(attack_url, headers=attack_headers)
        assert attack.status_code == 403
        assert attack.text == "preview request metadata is invalid"
    service_worker = remote.get(
        worker_url,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Service-Worker": "script",
        },
    )
    assert service_worker.status_code == 403


def test_loopback_preview_uses_a_same_host_relay_origin(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, root = _api(tmp_path)
    (root / "ops" / "index.html").write_text(
        "<main>Loopback preview</main>",
        encoding="utf-8",
    )
    area = api.app.state.db.execute(
        "SELECT pa.id FROM project_areas pa "
        "JOIN projects p ON p.id = pa.project_id "
        "WHERE p.slug = 'identity' AND pa.kind = 'ops'"
    ).fetchone()
    requested: list[tuple[int | None, str]] = []

    async def relay_port(preview_area, bind_host):
        requested.append((preview_area.area_id, bind_host))
        return 43123

    monkeypatch.setattr(
        api.app.state.target_previews,
        "_relay_port",
        relay_port,
    )
    loopback = TestClient(api.app, base_url="http://127.0.0.1:8766")
    response = loopback.get(
        f"/api/target-preview/identity/ops/{area['id']}/index.html",
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert urlsplit(response.headers["location"]).netloc == "127.0.0.1:43123"
    assert requested == [(area["id"], "127.0.0.1")]


def test_loopback_relay_uses_shared_frame_only_admission(
    tmp_path: Path,
):
    api, _headers, root = _api(tmp_path)
    (root / "ops" / "index.html").write_text(
        "<script>globalThis.__proximaPreviewExecuted = true</script>",
        encoding="utf-8",
    )
    (root / "ops" / "theme.css").write_text(
        "body { color: canonical-ops; }",
        encoding="utf-8",
    )
    row = api.app.state.db.execute(
        "SELECT pa.id, pa.project_id FROM project_areas pa "
        "JOIN projects p ON p.id = pa.project_id "
        "WHERE p.slug = 'identity' AND pa.kind = 'ops'"
    ).fetchone()
    area = target_preview.PreviewArea(
        project_id=int(row["project_id"]),
        kind="ops",
        area_id=int(row["id"]),
    )
    manager = api.app.state.target_previews
    token = target_preview.mint_file_preview_token(
        manager.secret,
        area,
        frame_origin="http://127.0.0.1:8766",
    )
    capability_path = f"/index.html?__proxima_cap={token}"
    frame_metadata = {
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
    }
    top_level_metadata = {
        **frame_metadata,
        "Sec-Fetch-Dest": "document",
    }
    relay = TestClient(
        manager._relay_app(area),
        base_url="http://127.0.0.1:43123",
    )

    top_level = relay.get(
        capability_path,
        headers=top_level_metadata,
        follow_redirects=False,
    )
    assert top_level.status_code == 403
    assert top_level.text == "preview request metadata is invalid"
    assert "set-cookie" not in top_level.headers

    gate = relay.get(
        capability_path,
        headers=frame_metadata,
        follow_redirects=False,
    )
    assert gate.status_code == 307
    assert gate.headers["location"] == "/index.html"
    assert "SameSite=strict" in gate.headers["set-cookie"]
    assert "Secure" not in gate.headers["set-cookie"]

    clean = relay.get(
        gate.headers["location"],
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "iframe",
        },
    )
    assert clean.status_code == 200
    assert "__proximaPreviewExecuted" in clean.text

    clean_top_level = relay.get(
        gate.headers["location"],
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
    )
    assert clean_top_level.status_code == 403
    assert clean_top_level.text == "preview request metadata is invalid"

    same_origin_resource = relay.get(
        "/theme.css",
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "style",
        },
    )
    assert same_origin_resource.status_code == 200
    assert same_origin_resource.text == "body { color: canonical-ops; }"

    cross_site_resource = relay.get(
        "/index.html",
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "script",
        },
    )
    assert cross_site_resource.status_code == 403
    assert cross_site_resource.text == "preview request metadata is invalid"


def test_archive_targets_resolve_direct_ops_files_without_registering_workspace_scan(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, root = _api(tmp_path)
    ops = root / "ops"
    (root / "brief.md").write_text("# Container shadow", encoding="utf-8")
    (root / "visual.png").write_bytes(b"container image shadow")
    (root / "handout.pdf").write_bytes(b"container PDF shadow")
    (ops / "brief.md").write_text("# Ops archive brief", encoding="utf-8")
    (ops / "visual.png").write_bytes(PNG_1X1)
    (ops / "handout.pdf").write_bytes(PDF)

    # Merely existing in the workspace must not create durable Archive rows.
    empty = api.get("/api/archive?project=identity", headers=headers).json()
    assert empty["total"] == 0

    project = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'identity'"
    ).fetchone()
    artifact_registry.record_artifacts(
        api.app.state.db,
        project["id"],
        ops,
        [
            {"type": "doc", "title": "brief.md", "path": "brief.md"},
            {"type": "image", "title": "visual.png", "path": "visual.png"},
            {"type": "doc", "title": "handout.pdf", "path": "handout.pdf"},
        ],
    )

    context_calls = 0
    original_context = file_targets.target_context

    def counted_context(*args, **kwargs):
        nonlocal context_calls
        context_calls += 1
        return original_context(*args, **kwargs)

    monkeypatch.setattr(file_targets, "target_context", counted_context)
    archive = api.get("/api/archive?project=identity", headers=headers).json()
    assert archive["total"] == 3
    assert context_calls == 1
    assert all(item["target"]["area"]["kind"] == "ops" for item in archive["items"])
    assert all(item["target"]["project"] == "identity" for item in archive["items"])
    assert all(item["file_missing"] is False for item in archive["items"])

    brief = next(item for item in archive["items"] if item["path"] == "brief.md")
    opened = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(brief["target"]),
    )
    assert opened.json()["content"] == "# Ops archive brief"

    # Presence must follow the record's Ops identity, not a same-name Container
    # shadow that still exists.
    (ops / "visual.png").unlink()
    calls_before_refresh = context_calls
    refreshed = api.get("/api/archive?project=identity", headers=headers).json()
    assert context_calls == calls_before_refresh + 1
    visual = next(item for item in refreshed["items"] if item["path"] == "visual.png")
    assert visual["file_missing"] is True


def test_resolver_rejects_cross_area_aliases_and_tree_switches_to_code_identity(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, root = _api(tmp_path)
    repo = root / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Code Area", encoding="utf-8")
    project = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'identity'"
    ).fetchone()
    cursor = api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo', 'manual')",
        (project["id"],),
    )
    code_area_id = cursor.lastrowid

    binding_calls = 0
    original_bindings = file_targets._area_bindings

    def counted_bindings(*args, **kwargs):
        nonlocal binding_calls
        binding_calls += 1
        return original_bindings(*args, **kwargs)

    monkeypatch.setattr(file_targets, "_area_bindings", counted_bindings)
    root_entries = _by_name(api, headers)
    assert binding_calls == 1
    repo_target = root_entries["repo"]["target"]
    assert repo_target == {
        "project": "identity",
        "area": {"kind": "code", "id": code_area_id},
        "path": "",
    }
    repo_tree = api.get(
        "/api/projects/identity/tree",
        headers=headers,
        params=_target_params(repo_target),
    )
    assert repo_tree.status_code == 200, repo_tree.text
    assert binding_calls == 2
    readme_target = repo_tree.json()["entries"][0]["target"]
    assert readme_target == {
        "project": "identity",
        "area": {"kind": "code", "id": code_area_id},
        "path": "README.md",
    }

    forged_code_alias = {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "repo/README.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(forged_code_alias),
    ).status_code == 400

    ops_area_id = api.app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (project["id"],),
    ).fetchone()["id"]
    forged_ops_alias = {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "ops/container.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(forged_ops_alias),
    ).status_code == 400
    canonical_ops = {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "container.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(canonical_ops),
    ).status_code == 200

    legacy_code = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params={"path": "repo/README.md"},
    )
    assert legacy_code.status_code == 200, legacy_code.text
    assert legacy_code.json()["target"] == readme_target


def test_ops_at_dot_scans_and_task_outputs_derive_nested_code_ownership(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    project = api.app.state.db.execute(
        "SELECT id FROM projects WHERE slug = 'identity'"
    ).fetchone()
    ops_area = api.app.state.db.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
        (project["id"],),
    ).fetchone()
    api.app.state.db.execute(
        "UPDATE project_areas SET rel_path = '.' WHERE id = ?",
        (ops_area["id"],),
    )
    repo = root / "repo"
    repo.mkdir()
    cursor = api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'repo', 'manual')",
        (project["id"],),
    )
    code_area_id = int(cursor.lastrowid)
    (repo / "output.md").write_text("# Nested code output", encoding="utf-8")

    scanned = api.get(
        "/api/projects/identity/artifacts?since_minutes=525600",
        headers=headers,
    ).json()["artifacts"]
    output = next(item for item in scanned if item["path"] == "repo/output.md")
    expected_target = {
        "project": "identity",
        "area": {"kind": "code", "id": code_area_id},
        "path": "output.md",
    }
    assert output["target"] == expected_target

    produced = api.app.state.worker._produced_artifacts(
        {
            "project_id": project["id"],
            "started_at": "1970-01-01T00:00:00+00:00",
        },
        None,
    )
    produced_output = next(
        item for item in produced if item["path"] == "repo/output.md"
    )
    assert produced_output["target"] == expected_target

    artifact_registry.record_artifacts(
        api.app.state.db,
        project["id"],
        root,
        [produced_output],
    )
    archive = api.get("/api/archive?project=identity", headers=headers).json()
    record = next(
        item for item in archive["items"] if item["path"] == "repo/output.md"
    )
    assert record["target"] == expected_target
    opened = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(record["target"]),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["content"] == "# Nested code output"


def test_session_artifact_reads_and_deletion_keep_ops_target(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    (root / "brief.md").write_text("# Container shadow", encoding="utf-8")
    (root / "ops" / "brief.md").write_text("# Ops artifact", encoding="utf-8")
    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Artifact session", "project_slug": "identity"},
    ).json()
    artifact = {"type": "doc", "title": "brief.md", "path": "brief.md"}
    api.app.state.db.execute(
        "UPDATE sessions SET produced_artifacts = ? WHERE id = ?",
        (json.dumps([artifact]), session["id"]),
    )
    message = api.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, output_links) "
        "VALUES (?, 'assistant', 'Done', ?)",
        (session["id"], json.dumps([artifact])),
    )

    session_items = api.get(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
    ).json()["artifacts"]
    target = session_items[0]["target"]
    assert target["area"]["kind"] == "ops"
    messages = api.get(
        f"/api/sessions/{session['id']}/messages",
        headers=headers,
    ).json()["messages"]
    assert messages[0]["output_links"][0]["target"] == target

    forged = {
        "project": "identity",
        "area": {"kind": "container", "id": None},
        "path": "brief.md",
    }
    rejected = api.delete(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
        params=_target_params(forged),
    )
    assert rejected.status_code == 400
    assert (root / "brief.md").is_file()
    assert (root / "ops" / "brief.md").is_file()

    deleted = api.delete(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
        params=_target_params(target),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["target"] == target
    assert (root / "brief.md").read_text(encoding="utf-8") == "# Container shadow"
    assert not (root / "ops" / "brief.md").exists()
    stored = api.app.state.db.execute(
        "SELECT output_links FROM messages WHERE id = ?",
        (message.lastrowid,),
    ).fetchone()
    assert json.loads(stored["output_links"]) == []


def test_tree_symlinks_use_resolved_ownership_and_omit_unsafe_entries(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    other = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "other", "name": "Other"},
    ).json()
    other_area_id = other["ops_area"]["id"]
    foreign = {
        "project": "identity",
        "area": {"kind": "ops", "id": other_area_id},
        "path": "container.md",
    }
    assert api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(foreign),
    ).status_code == 400

    (root / "ops" / "brief.md").write_text("# Ops through alias", encoding="utf-8")
    (root / "alias.md").symlink_to("ops/brief.md")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (root / "ops" / "escape.md").symlink_to(outside)

    entries = _by_name(api, headers)
    ops_area_id = api.app.state.db.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = (SELECT id FROM projects WHERE slug = 'identity') "
        "AND kind = 'ops'"
    ).fetchone()["id"]
    assert entries["alias.md"]["target"] == {
        "project": "identity",
        "area": {"kind": "ops", "id": ops_area_id},
        "path": "brief.md",
    }
    assert "escape.md" not in entries
    aliased = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(entries["alias.md"]["target"]),
    )
    assert aliased.status_code == 200
    assert aliased.json()["content"] == "# Ops through alias"


def test_artifact_enrichment_skips_only_unsafe_entries(tmp_path: Path):
    api, headers, root = _api(tmp_path)
    reports = root / "ops" / "reports"
    reports.mkdir()
    (reports / "safe.md").write_text("# Safe", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    (reports / "escape.md").symlink_to(outside)

    response = api.get(
        "/api/projects/identity/artifacts?since_minutes=525600",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    artifacts = {
        item["path"]: item
        for item in response.json()["artifacts"]
    }
    assert artifacts["reports/safe.md"]["target"]["path"] == "reports/safe.md"
    assert "reports/escape.md" not in artifacts

    project = api.app.state.db.execute(
        "SELECT id, slug, path FROM projects WHERE slug = 'identity'"
    ).fetchone()
    context = file_targets.target_context(api.app.state.db, project)
    assert file_targets.add_artifact_targets(
        api.app.state.db,
        project,
        [{"path": "reports/escape.md"}],
        context=context,
    ) == []
    with pytest.raises(file_targets.FileTargetError):
        file_targets.add_artifact_target(
            api.app.state.db,
            project,
            {"path": "reports/escape.md"},
            context=context,
        )

    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Unsafe artifact", "project_slug": "identity"},
    ).json()
    rejected = api.delete(
        f"/api/sessions/{session['id']}/artifacts",
        headers=headers,
        params={"path": "reports/escape.md"},
    )
    assert rejected.status_code == 400


def test_message_artifacts_fail_closed_and_reuse_one_target_context(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, _root = _api(tmp_path)
    session = api.post(
        "/api/sessions",
        headers=headers,
        json={"title": "Artifact links", "project_slug": "identity"},
    ).json()
    links = json.dumps(
        [{"type": "doc", "title": "brief.md", "path": "brief.md"}]
    )
    api.app.state.db.execute(
        "INSERT INTO messages(session_id, role, content, output_links) "
        "VALUES (?, 'assistant', 'One', ?), (?, 'assistant', 'Two', ?)",
        (session["id"], links, session["id"], links),
    )
    original = file_targets.target_context
    context_calls = 0

    def counted_context(*args, **kwargs):
        nonlocal context_calls
        context_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(file_targets, "target_context", counted_context)
    original_get_container = container_registry.get_container
    project_lookups = 0

    def counted_get_container(conn, container):
        nonlocal project_lookups
        if isinstance(container, int):
            project_lookups += 1
        return original_get_container(conn, container)

    monkeypatch.setattr(
        container_registry,
        "get_container",
        counted_get_container,
    )
    messages = api.get(
        f"/api/sessions/{session['id']}/messages",
        headers=headers,
    )
    assert messages.status_code == 200
    assert len(messages.json()["messages"]) == 2
    assert context_calls == 1
    assert project_lookups == 1

    ops_area_id = api.app.state.db.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = (SELECT id FROM projects WHERE slug = 'identity') "
        "AND kind = 'ops'"
    ).fetchone()["id"]
    api.app.state.db.execute(
        "UPDATE project_areas SET rel_path = 'missing' WHERE id = ?",
        (ops_area_id,),
    )
    failed_closed = api.get(
        f"/api/sessions/{session['id']}/messages",
        headers=headers,
    )
    assert failed_closed.status_code == 200
    assert all(
        message["output_links"] == []
        for message in failed_closed.json()["messages"]
    )


def test_media_artifact_rejects_empty_canonical_enrichment(
    tmp_path: Path,
    monkeypatch,
):
    api, headers, _root = _api(tmp_path)

    def reject_artifact(*args, **kwargs):
        raise file_targets.FileTargetError(
            "artifact Area identity is unavailable"
        )

    monkeypatch.setattr(
        file_targets,
        "add_artifact_target",
        reject_artifact,
    )
    response = api.post(
        "/api/chat/send",
        headers=headers,
        json={
            "project_slug": "identity",
            "message": (
                "/design create a premium launch poster with bold type"
            ),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "artifact Area identity is unavailable"
    )


def test_legacy_ops_at_dot_keeps_area_identity_and_does_not_rewrite_ops_prefix(
    tmp_path: Path,
):
    api, headers, root = _api(tmp_path)
    row = api.app.state.db.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = (SELECT id FROM projects WHERE slug = 'identity') "
        "AND kind = 'ops'"
    ).fetchone()
    api.app.state.db.execute(
        "UPDATE project_areas SET rel_path = '.' WHERE id = ?",
        (row["id"],),
    )
    (root / "legacy.md").write_text("# Legacy root Ops", encoding="utf-8")

    legacy = _by_name(api, headers)["legacy.md"]["target"]
    assert legacy == {
        "project": "identity",
        "area": {"kind": "ops", "id": row["id"]},
        "path": "legacy.md",
    }
    read = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params=_target_params(legacy),
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "# Legacy root Ops"

    # On a legacy Area rooted at '.', an explicit ops/ prefix still addresses
    # the real Container child named ops. It must not be stripped to legacy.md.
    protected = api.get(
        "/api/projects/identity/file",
        headers=headers,
        params={"path": "ops/legacy.md"},
    )
    assert protected.status_code == 400

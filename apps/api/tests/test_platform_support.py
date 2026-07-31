from __future__ import annotations

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.platform_support import current_platform, platform_key, support_payload


def _client(tmp_path) -> TestClient:
    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "workspace"),
        "projectctl_path": "/usr/bin/true",
        "start_worker": False,
    })
    return TestClient(app)


def test_platform_family_never_defaults_unknown_hosts_to_linux() -> None:
    assert platform_key("Linux") == "linux"
    assert platform_key("Darwin") == "macos"
    assert platform_key("Windows") == "windows"
    assert platform_key("FreeBSD") == "unsupported"
    assert current_platform("FreeBSD")["tier"] == "unsupported"


def test_support_catalog_has_one_supported_linux_tier() -> None:
    payload = support_payload("Linux")

    assert payload["claim"] == "linux-first-daily-driver"
    assert [(item["label"], item["tier"]) for item in payload["platforms"]] == [
        ("Linux", "supported"),
        ("macOS", "experimental"),
        ("Windows", "experimental"),
    ]
    assert payload["server"]["key"] == "linux"
    assert payload["reference"] == "docs/linux-daily-driver-acceptance.md"


def test_public_config_and_health_publish_same_server_support(tmp_path) -> None:
    client = _client(tmp_path)

    config = client.get("/api/config")
    health = client.get("/api/health")

    assert config.status_code == 200
    assert health.status_code == 200
    support = config.json()["platform_support"]
    assert support["server"]["key"] == "linux"
    assert support["server"]["tier"] == "supported"
    assert health.json()["platform_support"] == support["server"]

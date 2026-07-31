from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _fixture_module():
    script = ROOT / "scripts" / "verify_file_targets_browser.py"
    spec = importlib.util.spec_from_file_location(
        "proxima_file_target_browser",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _browser_module():
    script = ROOT / "trusted-probes" / "safe-update" / "browser.py"
    spec = importlib.util.spec_from_file_location(
        "proxima_file_target_browser_driver",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MANIFEST_URL = (
    "https://file-3-ops-3.preview.test/site/app.webmanifest"
)
_MANIFEST_BODY = {
    "name": "Canonical preview",
    "short_name": "Canonical",
    "start_url": "./index.html",
}
_MANIFEST_EXPECTATION = {
    "mime_type": "application/manifest+json",
    "body_json": _MANIFEST_BODY,
    "fetch_metadata": {
        "site": "same-origin",
        "mode": "cors",
        "dest": "manifest",
    },
}


def _manifest_events() -> list[dict[str, object]]:
    return [
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": "manifest-request",
                "request": {"url": _MANIFEST_URL},
            },
        },
        {
            "method": "Network.requestWillBeSentExtraInfo",
            "params": {
                "requestId": "manifest-request",
                "headers": {
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "manifest",
                },
            },
        },
        {
            "method": "Network.responseReceived",
            "params": {
                "requestId": "manifest-request",
                "response": {
                    "headers": {
                        "Content-Type": "application/manifest+json",
                    },
                    "mimeType": "application/manifest+json",
                    "status": 200,
                    "url": _MANIFEST_URL,
                },
            },
        },
        {
            "method": "Network.loadingFinished",
            "params": {"requestId": "manifest-request"},
        },
    ]


class _NetworkConnection:
    def __init__(
        self,
        events: list[dict[str, object]],
        body: dict[str, object] | None = None,
    ) -> None:
        self.network_events = events
        self.body = _MANIFEST_BODY if body is None else body

    def call(self, method: str, params: dict | None = None) -> dict:
        assert method == "Network.getResponseBody"
        assert params == {"requestId": "manifest-request"}
        return {
            "base64Encoded": False,
            "body": json.dumps(self.body),
        }


def _manifest_summary(
    browser,
    events: list[dict[str, object]],
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    return browser._network_resource_summary(
        _NetworkConnection(events, body),
        expected_url=_MANIFEST_URL,
        expectation=_MANIFEST_EXPECTATION,
    )


def test_missing_openssl_fails_before_browser_fixture_build(
    monkeypatch,
) -> None:
    fixture = _fixture_module()
    monkeypatch.setattr(fixture, "API_PYTHON", Path(__file__))
    monkeypatch.setattr(fixture, "_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(fixture.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        fixture,
        "_build_web",
        lambda: pytest.fail("browser fixture build started"),
    )

    with pytest.raises(
        RuntimeError,
        match="OpenSSL is required",
    ):
        fixture.main()


def test_manifest_network_observation_requires_request() -> None:
    browser = _browser_module()

    with pytest.raises(browser.BrowserProbeError, match="request is missing"):
        _manifest_summary(browser, [])


def test_manifest_network_observation_rejects_404() -> None:
    browser = _browser_module()
    events = _manifest_events()
    events[2]["params"]["response"]["status"] = 404

    with pytest.raises(browser.BrowserProbeError, match="status is invalid"):
        _manifest_summary(browser, events)


def test_manifest_network_observation_rejects_redirect() -> None:
    browser = _browser_module()
    events = _manifest_events()
    events[0]["params"]["redirectResponse"] = {
        "status": 302,
        "url": _MANIFEST_URL,
    }

    with pytest.raises(browser.BrowserProbeError, match="redirected"):
        _manifest_summary(browser, events)


def test_manifest_network_observation_rejects_abort() -> None:
    browser = _browser_module()
    events = _manifest_events()[:-1]
    events.append(
        {
            "method": "Network.loadingFailed",
            "params": {
                "canceled": True,
                "errorText": "net::ERR_ABORTED",
                "requestId": "manifest-request",
            },
        }
    )

    with pytest.raises(browser.BrowserProbeError, match="request failed"):
        _manifest_summary(browser, events)


def test_manifest_network_observation_rejects_block() -> None:
    browser = _browser_module()
    events = _manifest_events()[:-1]
    events.append(
        {
            "method": "Network.loadingFailed",
            "params": {
                "blockedReason": "csp",
                "errorText": "",
                "requestId": "manifest-request",
            },
        }
    )

    with pytest.raises(browser.BrowserProbeError, match="was blocked"):
        _manifest_summary(browser, events)


def test_manifest_network_observation_rejects_wrong_mime() -> None:
    browser = _browser_module()
    events = _manifest_events()
    response = events[2]["params"]["response"]
    response["mimeType"] = "text/plain"
    response["headers"]["Content-Type"] = "text/plain"

    with pytest.raises(browser.BrowserProbeError, match="MIME type is invalid"):
        _manifest_summary(browser, events)


def test_manifest_network_observation_rejects_wrong_content() -> None:
    browser = _browser_module()

    with pytest.raises(browser.BrowserProbeError, match="content is invalid"):
        _manifest_summary(browser, _manifest_events(), {"name": "Wrong"})


def test_manifest_network_observation_proves_success() -> None:
    browser = _browser_module()

    summary = _manifest_summary(browser, _manifest_events())

    assert summary["request_id"] == "manifest-request"
    assert summary["status"] == 200
    assert summary["mime_type"] == "application/manifest+json"
    assert summary["fetch_metadata"] == {
        "site": "same-origin",
        "mode": "cors",
        "dest": "manifest",
    }


def test_browser_manifest_probe_keeps_track_load_independent() -> None:
    fixture = _fixture_module()

    probe = fixture._browser_metadata_recording_probe()

    assert probe["execution_marker"] == "__proximaTrackLoaded"
    assert probe["network_resource"]["url_expression"] == (
        "window.__targetPreviewManifestUrl"
    )


def test_accepted_preview_adr_remains_append_only() -> None:
    adr = ROOT / "docs" / "adr" / "0015-distinct-tls-area-preview-origins.md"
    digest = hashlib.sha256(adr.read_bytes()).hexdigest()
    assert digest == "559d71879df7260d42c015fc0f2063ab828e564baeaac7bd56173d6646ba7a72"

    successor = (
        ROOT / "docs" / "adr" / "0016-frame-bound-area-preview-admission.md"
    ).read_text(encoding="utf-8")
    assert "- Status: Accepted" in successor
    assert "without superseding" in successor

    index = (ROOT / "docs" / "adr" / "README.md").read_text(
        encoding="utf-8",
    )
    assert (
        "| [0016](0016-frame-bound-area-preview-admission.md) "
        "| Area preview admission is frame-bound | Accepted |"
    ) in index

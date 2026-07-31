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
    "?__proxima_request_nonce=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
_MANIFEST_NONCE = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_CAPABILITY_GENERATION = "b" * 64
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
    "response_headers": ["X-Proxima-Preview-Generation"],
}


def _manifest_events() -> list[dict[str, object]]:
    return [
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": "manifest-request",
                "request": {"method": "GET", "url": _MANIFEST_URL},
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
                        "X-Proxima-Preview-Generation": _CAPABILITY_GENERATION,
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


def test_manifest_network_observation_requires_generation_header() -> None:
    browser = _browser_module()
    events = _manifest_events()
    del events[2]["params"]["response"]["headers"][
        "X-Proxima-Preview-Generation"
    ]

    with pytest.raises(browser.BrowserProbeError, match="header is missing"):
        _manifest_summary(browser, events)


def test_manifest_network_observation_proves_success() -> None:
    browser = _browser_module()

    summary = _manifest_summary(browser, _manifest_events())

    assert summary["request_id"] == "manifest-request"
    assert summary["method"] == "GET"
    assert summary["status"] == 200
    assert summary["mime_type"] == "application/manifest+json"
    assert summary["fetch_metadata"] == {
        "site": "same-origin",
        "mode": "cors",
        "dest": "manifest",
    }
    assert summary["response_headers"] == {
        "x-proxima-preview-generation": _CAPABILITY_GENERATION,
    }


def _admission_record(
    *,
    nonce: str = _MANIFEST_NONCE,
    generation: str = _CAPABILITY_GENERATION,
    project: int = 3,
    area: int = 3,
    kind: str = "ops",
) -> dict[str, object]:
    return {
        "area": area,
        "capability_generation": generation,
        "destination": "manifest",
        "final_path": "site/app.webmanifest",
        "kind": kind,
        "method": "GET",
        "mode": "cors",
        "nonce": nonce,
        "project": project,
        "request_target": (
            "/site/app.webmanifest?__proxima_request_nonce=" + nonce
        ),
        "site": "same-origin",
    }


def _manifest_resource() -> dict[str, object]:
    return _manifest_summary(_browser_module(), _manifest_events())


def test_manifest_probe_nonce_uses_cryptographic_source(monkeypatch) -> None:
    fixture = _fixture_module()
    calls: list[int] = []

    def token_urlsafe(size: int) -> str:
        calls.append(size)
        return _MANIFEST_NONCE

    monkeypatch.setattr(fixture.secrets, "token_urlsafe", token_urlsafe)

    assert fixture._new_manifest_probe_nonce() == _MANIFEST_NONCE
    assert calls == [24]


def test_manifest_admission_correlation_proves_exact_request_with_decoy() -> None:
    fixture = _fixture_module()
    decoy = _admission_record(nonce="C" * 32)

    actual = fixture._correlate_manifest_admission(
        [decoy, _admission_record()],
        _manifest_resource(),
        _MANIFEST_NONCE,
    )

    assert actual == _admission_record()


def test_manifest_admission_correlation_rejects_absent_nonce() -> None:
    fixture = _fixture_module()

    with pytest.raises(RuntimeError, match="exactly one"):
        fixture._correlate_manifest_admission(
            [_admission_record(nonce="C" * 32)],
            _manifest_resource(),
            _MANIFEST_NONCE,
        )


def test_manifest_admission_correlation_rejects_duplicate_nonce() -> None:
    fixture = _fixture_module()
    record = _admission_record()

    with pytest.raises(RuntimeError, match="exactly one"):
        fixture._correlate_manifest_admission(
            [record, dict(record)],
            _manifest_resource(),
            _MANIFEST_NONCE,
        )


def test_manifest_admission_correlation_rejects_replay() -> None:
    fixture = _fixture_module()

    with pytest.raises(RuntimeError, match="do not correlate"):
        fixture._correlate_manifest_admission(
            [_admission_record(generation="c" * 64)],
            _manifest_resource(),
            _MANIFEST_NONCE,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"project": 4},
        {"area": 4},
        {"kind": "code"},
    ],
)
def test_manifest_admission_correlation_rejects_mismatched_area(
    changes,
) -> None:
    fixture = _fixture_module()

    with pytest.raises(RuntimeError, match="do not correlate"):
        fixture._correlate_manifest_admission(
            [_admission_record(**changes)],
            _manifest_resource(),
            _MANIFEST_NONCE,
        )


def test_manifest_admission_correlation_rejects_stale_nonce() -> None:
    fixture = _fixture_module()

    with pytest.raises(RuntimeError, match="do not correlate"):
        fixture._correlate_manifest_admission(
            [_admission_record()],
            _manifest_resource(),
            "D" * 32,
        )


def test_manifest_admission_correlation_rejects_redirected_target() -> None:
    fixture = _fixture_module()
    resource = _manifest_resource()
    resource["url"] = (
        "https://file-3-ops-3.preview.test/site/redirected.webmanifest"
        f"?__proxima_request_nonce={_MANIFEST_NONCE}"
    )

    with pytest.raises(RuntimeError, match="do not correlate"):
        fixture._correlate_manifest_admission(
            [_admission_record()],
            resource,
            _MANIFEST_NONCE,
        )


def test_browser_manifest_probe_keeps_track_load_independent() -> None:
    fixture = _fixture_module()

    probe = fixture._browser_metadata_recording_probe()

    assert probe["execution_marker"] == "__proximaTrackLoaded"
    assert probe["network_resource"]["url_expression"] == (
        "window.__targetPreviewManifestUrl"
    )


def test_accepted_preview_adr_remains_append_only() -> None:
    adr = ROOT / "docs" / "adr" / "0034-distinct-tls-area-preview-origins.md"
    digest = hashlib.sha256(adr.read_bytes()).hexdigest()
    assert digest == "559d71879df7260d42c015fc0f2063ab828e564baeaac7bd56173d6646ba7a72"

    successor = (
        ROOT / "docs" / "adr" / "0035-frame-bound-area-preview-admission.md"
    ).read_text(encoding="utf-8")
    assert "- Status: Accepted" in successor
    assert "without superseding" in successor

    index = (ROOT / "docs" / "adr" / "README.md").read_text(
        encoding="utf-8",
    )
    assert (
        "| [0035](0035-frame-bound-area-preview-admission.md) "
        "| Area preview admission is frame-bound | Accepted |"
    ) in index

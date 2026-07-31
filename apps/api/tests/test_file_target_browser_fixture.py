from __future__ import annotations

import hashlib
import importlib.util
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

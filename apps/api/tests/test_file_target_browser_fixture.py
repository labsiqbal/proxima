from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _fixture_module():
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "verify_file_targets_browser.py"
    )
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

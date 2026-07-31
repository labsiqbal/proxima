from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "verify_task_reconciliation_browser",
    ROOT / "scripts" / "verify_task_reconciliation_browser.py",
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def test_browser_runtime_fixture_cleans_up_after_interruption():
    fixture_path = None

    with pytest.raises(KeyboardInterrupt):
        with VERIFIER._runtime_fixture() as fixture:
            fixture_path = fixture
            assert ROOT not in fixture.parents
            (fixture / "started").write_text("started")
            raise KeyboardInterrupt

    assert fixture_path is not None
    assert not fixture_path.exists()


def test_browser_scenarios_require_all_after_screenshots(tmp_path: Path):
    steps = [
        VERIFIER._screenshot_step(tmp_path, name)
        for name in VERIFIER.SCREENSHOT_NAMES
    ]

    assert all(step for step in steps)
    assert [
        Path(step[0]["path"]).name
        for step in steps
    ] == list(VERIFIER.SCREENSHOT_NAMES)

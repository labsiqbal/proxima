from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MINIMAL_PNG = (
    PNG_MAGIC
    + bytes.fromhex(
        "0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
)


def _module():
    script = REPO / "scripts" / "verify_scheduled_workflow_browser.py"
    spec = importlib.util.spec_from_file_location(
        "proxima_verify_scheduled_workflow_browser", script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_screenshot_names_are_stable_before_and_after_pairs():
    module = _module()
    assert module.SCREENSHOT_NAMES == (
        "before-missing-binding",
        "after-missing-binding-refusal",
        "before-run-now",
        "after-run-now-exact-job",
    )
    paths = module.screenshot_paths(Path("/tmp/evidence"))
    assert [path.name for path in paths.values()] == [
        "before-missing-binding.png",
        "after-missing-binding-refusal.png",
        "before-run-now.png",
        "after-run-now-exact-job.png",
    ]


def test_assert_valid_png_rejects_empty_and_non_png(tmp_path: Path):
    module = _module()
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="invalid or empty PNG"):
        module.assert_valid_png(empty)

    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not-a-png-file-contents-here")
    with pytest.raises(RuntimeError, match="invalid or empty PNG"):
        module.assert_valid_png(junk)

    good = tmp_path / "good.png"
    good.write_bytes(MINIMAL_PNG + b"\x00" * 200)
    module.assert_valid_png(good)


def test_assert_screenshot_bundle_requires_all_named_pngs(tmp_path: Path):
    module = _module()
    with pytest.raises(RuntimeError, match="missing screenshot"):
        module.assert_screenshot_bundle(tmp_path)

    paths = module.screenshot_paths(tmp_path)
    first = next(iter(paths.values()))
    first.write_bytes(b"not-png")
    with pytest.raises(RuntimeError, match="missing screenshot|invalid or empty PNG"):
        module.assert_screenshot_bundle(tmp_path)

    for path in paths.values():
        path.write_bytes(MINIMAL_PNG + b"\x00" * 200)
    captured = module.assert_screenshot_bundle(tmp_path)
    assert set(captured) == set(module.SCREENSHOT_NAMES)


def test_browser_scenarios_use_binding_copy_and_screenshot_steps():
    module = _module()
    missing = module._scenario_missing_binding()
    ready = module._scenario_ready_run_now()
    missing_text = " ".join(
        str(step.get("text") or "") for step in missing["steps"]
    )
    ready_names = [
        step["name"]
        for step in missing["steps"] + ready["steps"]
        if step.get("action") == "screenshot"
    ]
    assert "1 needs binding" in missing_text
    assert "Needs binding: Topic" in missing_text
    assert "source" not in missing_text.lower()
    assert ready_names == list(module.SCREENSHOT_NAMES)


def test_docs_describe_single_reusable_workflow_table_not_manual_scheduled_split():
    docs = [
        REPO / "docs" / "CAPABILITIES.md",
        REPO / "docs" / "workflow-graph.md",
        REPO / "docs" / "ui-shell.md",
    ]
    stale_markers = (
        "Manual (on-demand)",
        "split into **Manual",
        "are split into **Manual",
        "derives **Manual (on-demand)**",
        "owns **Manual / Scheduled** mode",
        "Scheduled triggers own cron, overlap, and enabled settings instead",
        "start with no human intake prompt",
        "Manual trigger only",
        "Scheduled trigger only",
        "exposes cron, overlap, and enabled settings instead",
        "with an empty input payload, so cadence execution does not prompt",
    )
    for path in docs:
        text = path.read_text(encoding="utf-8")
        for marker in stale_markers:
            assert marker not in text, f"{path} still teaches {marker!r}"
        assert "Availability" in text
        assert "Automation" in text or "Schedules" in text
    capabilities = (REPO / "docs" / "CAPABILITIES.md").read_text(encoding="utf-8")
    assert "shared **intake contract**" in capabilities
    assert "schedule seed" in capabilities
    assert "durable schedule" in capabilities and "bindings" in capabilities
    assert "IANA timezone" in capabilities
    assert "browser zone" in capabilities and "UTC" in capabilities
    workflow_graph = (REPO / "docs" / "workflow-graph.md").read_text(encoding="utf-8")
    assert "shared" in workflow_graph and "intake" in workflow_graph
    assert "schedule seed" in workflow_graph or "schedule seeds" in workflow_graph
    assert "durable" in workflow_graph and "binding" in workflow_graph
    assert "{cron, timezone, overlap_policy, enabled}" in workflow_graph
    assert "browser zone" in workflow_graph and "defaults to UTC" in workflow_graph


def test_durable_scheduled_workflow_evidence_has_valid_before_after_pngs():
    module = _module()
    evidence = REPO / "docs" / "evidence" / "scheduled-workflow-trust"
    readme = (evidence / "README.md").read_text(encoding="utf-8")
    captured = module.assert_screenshot_bundle(evidence)
    for name in module.SCREENSHOT_NAMES:
        assert f"{name}.png" in readme
    assert set(captured) == set(module.SCREENSHOT_NAMES)
    hub = (REPO / "docs" / "README.md").read_text(encoding="utf-8")
    assert "evidence/scheduled-workflow-trust/README.md" in hub

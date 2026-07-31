from __future__ import annotations

import importlib.util
from pathlib import Path


def _generator_module():
    script = Path(__file__).resolve().parents[3] / "scripts" / "gen_docs.py"
    spec = importlib.util.spec_from_file_location("proxima_gen_docs", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_doc_footer_does_not_create_phantom_drift(tmp_path: Path):
    generator = _generator_module()
    output = tmp_path / "reference.md"

    assert generator._write_generated(output, "# Reference\n", "2026-07-30 00:00 UTC")
    first = output.read_text(encoding="utf-8")

    assert not generator._write_generated(
        output, "# Reference\n", "2026-07-30 00:01 UTC"
    )
    assert output.read_text(encoding="utf-8") == first

    assert generator._write_generated(output, "# Changed\n", "2026-07-30 00:02 UTC")
    changed = output.read_text(encoding="utf-8")
    assert changed != first
    assert "_Generated 2026-07-30 00:02 UTC._" in changed

    output.write_text("# Changed\n", encoding="utf-8")
    assert generator._write_generated(output, "# Changed\n", "2026-07-30 00:03 UTC")
    assert "_Generated 2026-07-30 00:03 UTC._" in output.read_text(encoding="utf-8")


def test_multiline_route_decorator_is_in_generated_api_reference():
    generator = _generator_module()

    endpoints = generator._collect_endpoints()
    rendered = generator._render_api(endpoints)

    assert "| GET | `/api/projects/{slug}/app/status` | `app_status` |" in rendered


def test_task_reconciliation_adr_is_indexed_and_scoped():
    root = Path(__file__).resolve().parents[3]
    adr_path = (
        root
        / "docs"
        / "adr"
        / "0027-durable-task-reconciliation-protocol.md"
    )
    adr = adr_path.read_text(encoding="utf-8")
    index = (root / "docs" / "adr" / "README.md").read_text(
        encoding="utf-8"
    )
    architecture = (
        root / "docs" / "reference" / "architecture.md"
    ).read_text(encoding="utf-8")
    normalized_adr = " ".join(adr.split())

    assert "0027-durable-task-reconciliation-protocol.md" in index
    assert "0027-durable-task-reconciliation-protocol.md" in architecture
    for required in (
        "projection intent",
        "Task-event order",
        "immutable causal gap",
        "aggregate correction marker",
        "authoritative Task-session provenance",
    ):
        assert required in normalized_adr

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

    assert not generator._write_generated(output, "# Reference\n", "2026-07-30 00:01 UTC")
    assert output.read_text(encoding="utf-8") == first

    assert generator._write_generated(output, "# Changed\n", "2026-07-30 00:02 UTC")
    changed = output.read_text(encoding="utf-8")
    assert changed != first
    assert "_Generated 2026-07-30 00:02 UTC._" in changed

    output.write_text("# Changed\n", encoding="utf-8")
    assert generator._write_generated(output, "# Changed\n", "2026-07-30 00:03 UTC")
    assert "_Generated 2026-07-30 00:03 UTC._" in output.read_text(encoding="utf-8")

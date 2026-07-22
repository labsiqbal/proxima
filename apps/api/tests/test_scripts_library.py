"""The script library's pure half (T6): jailing, headers, catalog, exec argv,
and the hash-trust records the approval flow persists."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from proxima_api import scripts_library
from proxima_api.migrations import _add_script_trust


# ── path canonicalization + jailing ──────────────────────────────────────


def test_rel_path_accepts_both_spellings_as_one_script():
    assert scripts_library.normalize_script_rel_path("fetch.sh") == "fetch.sh"
    assert scripts_library.normalize_script_rel_path("scripts/fetch.sh") == "fetch.sh"
    assert scripts_library.normalize_script_rel_path("seo/crawl.py") == "seo/crawl.py"


@pytest.mark.parametrize("bad", ["", "  ", "/etc/passwd", "../escape.sh", "a/../../b.sh", "./x/./../y"])
def test_rel_path_rejects_escapes(bad):
    with pytest.raises(scripts_library.ScriptResolutionError):
        scripts_library.normalize_script_rel_path(bad)


def test_resolve_script_requires_a_real_file_inside_scripts(tmp_path: Path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "hello.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "secret.txt").write_text("nope", encoding="utf-8")

    assert scripts_library.resolve_script(root, "hello.sh").name == "hello.sh"
    with pytest.raises(scripts_library.ScriptResolutionError):
        scripts_library.resolve_script(root, "missing.sh")
    with pytest.raises(scripts_library.ScriptResolutionError):
        scripts_library.resolve_script(root, "../secret.txt")


def test_resolve_script_refuses_a_symlink_escape(tmp_path: Path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    outside = tmp_path / "outside.sh"
    outside.write_text("echo out\n", encoding="utf-8")
    (root / "scripts" / "link.sh").symlink_to(outside)
    with pytest.raises(scripts_library.ScriptResolutionError):
        scripts_library.resolve_script(root, "link.sh")


# ── header parsing + catalog ─────────────────────────────────────────────


def test_parse_header_reads_labelled_fields_after_a_shebang():
    header = scripts_library.parse_header(
        "#!/usr/bin/env bash\n"
        "# Description: Fetch the sitemap and count URLs\n"
        "# Inputs: site url as arg 1\n"
        "# Outputs: one line with the count\n"
        "echo body\n"
    )
    assert header["description"] == "Fetch the sitemap and count URLs"
    assert header["inputs"] == "site url as arg 1"
    assert header["outputs"] == "one line with the count"


def test_parse_header_falls_back_to_first_comment_line_and_supports_slashes():
    header = scripts_library.parse_header("// counts things fast\nconsole.log(1)\n")
    assert header["description"] == "counts things fast"
    # A code line ends the header: later comments never leak in.
    late = scripts_library.parse_header("x = 1\n# Description: too late\n")
    assert late["description"] == ""


def test_scan_catalog_lists_scripts_with_descriptions(tmp_path: Path):
    root = tmp_path / "project"
    scripts = root / "scripts"
    (scripts / "seo").mkdir(parents=True)
    (scripts / "b-fetch.sh").write_text("# Description: fetch stuff\n", encoding="utf-8")
    (scripts / "seo" / "crawl.py").write_text("# crawls the site\n", encoding="utf-8")
    (scripts / ".hidden.sh").write_text("# Description: nope\n", encoding="utf-8")

    catalog = scripts_library.scan_catalog(root)
    assert [entry["rel_path"] for entry in catalog] == ["b-fetch.sh", "seo/crawl.py"]
    assert catalog[0]["description"] == "fetch stuff"
    assert catalog[1]["description"] == "crawls the site"


def test_scan_catalog_without_a_scripts_dir_is_empty(tmp_path: Path):
    assert scripts_library.scan_catalog(tmp_path / "nowhere") == []


def test_catalog_preamble_block_carries_reuse_instruction():
    block = scripts_library.catalog_preamble_block(
        [{"rel_path": "fetch.sh", "description": "fetch stuff"}]
    )
    assert "scripts/fetch.sh — fetch stuff" in block
    assert "REUSING" in block
    assert "stdout" in block
    empty = scripts_library.catalog_preamble_block([])
    assert "none yet" in empty
    # The authoring convention still teaches agents to grow the library.
    assert "Description" in empty


# ── exec argv selection ──────────────────────────────────────────────────


def test_exec_argv_runs_executables_directly_and_maps_extensions(tmp_path: Path):
    exe = tmp_path / "tool"
    exe.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    os.chmod(exe, 0o755)
    assert scripts_library.exec_argv(exe) == [str(exe)]

    sh = tmp_path / "step.sh"
    sh.write_text("echo hi\n", encoding="utf-8")
    assert scripts_library.exec_argv(sh) == ["bash", str(sh)]

    py = tmp_path / "step.py"
    py.write_text("print('hi')\n", encoding="utf-8")
    assert scripts_library.exec_argv(py) == [sys.executable, str(py)]

    unknown = tmp_path / "step.rb"
    unknown.write_text("puts 'hi'\n", encoding="utf-8")
    with pytest.raises(scripts_library.ScriptResolutionError):
        scripts_library.exec_argv(unknown)


# ── hash trust records ───────────────────────────────────────────────────


def _trust_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _add_script_trust(conn)  # the real migration builds the real table
    return conn


def test_trust_roundtrip_and_reapproval_replaces_the_hash(tmp_path: Path):
    conn = _trust_conn()
    assert scripts_library.trusted_hash(conn, 1, "fetch.sh") is None

    scripts_library.record_trust(conn, 1, "scripts/fetch.sh", "aaa", 7)
    # Both spellings are one script — approval and lookup cannot diverge.
    assert scripts_library.trusted_hash(conn, 1, "fetch.sh") == "aaa"
    assert scripts_library.trusted_hash(conn, 2, "fetch.sh") is None

    scripts_library.record_trust(conn, 1, "fetch.sh", "bbb", 7)
    assert scripts_library.trusted_hash(conn, 1, "fetch.sh") == "bbb"
    rows = conn.execute("SELECT COUNT(*) AS c FROM script_trust").fetchone()["c"]
    assert rows == 1


def test_content_hash_tracks_bytes(tmp_path: Path):
    script = tmp_path / "s.sh"
    script.write_text("echo one\n", encoding="utf-8")
    first = scripts_library.content_hash(script)
    script.write_text("echo two\n", encoding="utf-8")
    assert scripts_library.content_hash(script) != first

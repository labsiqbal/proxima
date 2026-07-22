from datetime import datetime
from pathlib import Path

from proxima_api import wiki_memory


def test_format_log_entry_plain():
    when = datetime(2026, 6, 9, 16, 42)
    line = wiki_memory.format_log_entry(when, "Alice", "Did the thing.")
    assert line == "- 16:42 · Alice — Did the thing."


def test_format_log_entry_with_task():
    when = datetime(2026, 6, 9, 16, 42)
    line = wiki_memory.format_log_entry(when, "Alice", "Did it.", task_title="Build login")
    assert line == "- 16:42 · Alice — Did it. ([[task: Build login]])"


def test_append_creates_file_with_heading(tmp_path: Path):
    root = tmp_path / "wiki"
    wiki_memory.append_log_entry(root, datetime(2026, 6, 9, 9, 0), "Alice", "First.")
    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-06-09" in text
    assert "- 09:00 · Alice — First." in text


def test_append_newest_first_same_day(tmp_path: Path):
    root = tmp_path / "wiki"
    wiki_memory.append_log_entry(root, datetime(2026, 6, 9, 9, 0), "Alice", "Older.")
    wiki_memory.append_log_entry(root, datetime(2026, 6, 9, 10, 0), "Alice", "Newer.")
    lines = [l for l in (root / "log.md").read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    assert lines[0].endswith("Newer.")   # newest entry on top within the day
    assert lines[1].endswith("Older.")


def test_append_newest_day_on_top(tmp_path: Path):
    root = tmp_path / "wiki"
    wiki_memory.append_log_entry(root, datetime(2026, 6, 8, 9, 0), "Alice", "Yesterday.")
    wiki_memory.append_log_entry(root, datetime(2026, 6, 9, 9, 0), "Alice", "Today.")
    text = (root / "log.md").read_text(encoding="utf-8")
    assert text.index("## 2026-06-09") < text.index("## 2026-06-08")


def test_build_draft_prompt_lists_existing_notes():
    p = wiki_memory.build_draft_prompt([
        {"path": "auth/login.md", "content": "# Login\nUses OAuth.\n"},
        {"path": "log.md", "content": "# Project log\n"},
    ])
    assert "auth/login.md" in p
    assert "log.md" not in p            # the running log is not a reference note
    assert "json" in p.lower()          # instructs JSON output


def test_parse_note_draft_fenced_json():
    raw = 'Sure!\n```json\n{"title":"Caching","path":"perf/caching.md","body":"# Caching\\nUse Redis.","related":["perf/index.md"],"conflicts":["note says memcached"],"action":"new"}\n```\n'
    d = wiki_memory.parse_note_draft(raw)
    assert d["title"] == "Caching"
    assert d["path"] == "perf/caching.md"
    assert d["body"].startswith("# Caching")
    assert d["related"] == ["perf/index.md"]
    assert d["conflicts"] == ["note says memcached"]
    assert d["action"] == "new"
    assert d["unparsed"] is False


def test_parse_note_draft_fallback_on_garbage():
    d = wiki_memory.parse_note_draft("just some prose, no json here")
    assert d["unparsed"] is True
    assert d["body"] == "just some prose, no json here"
    assert d["action"] == "new"
    assert d["related"] == [] and d["conflicts"] == []


def test_rebuild_index_lists_notes_with_titles_and_summaries(tmp_path: Path):
    root = tmp_path / "wiki"
    (root / "auth").mkdir(parents=True)
    (root / "auth" / "jwt.md").write_text("# JWT migration\n\nMoved auth to JWT tokens.\n", encoding="utf-8")
    (root / "perf.md").write_text(
        '---\ndescription: Caching strategy for the API\n---\n# Perf\n\nbody\n', encoding="utf-8")
    wiki_memory.rebuild_index(root)
    idx = (root / "index.md").read_text(encoding="utf-8")
    assert "[JWT migration](auth/jwt.md)" in idx
    assert "Moved auth to JWT tokens." in idx
    assert "[Perf](perf.md)" in idx
    assert "Caching strategy for the API" in idx   # frontmatter description wins


def test_rebuild_index_excludes_log_index_and_graphify(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "log.md").write_text("# Project log\n", encoding="utf-8")
    (root / "index.md").write_text("# Wiki index\n", encoding="utf-8")
    (root / "note.md").write_text("# Note\n\nKeep me.\n", encoding="utf-8")
    (root / "graphify-out").mkdir()
    (root / "graphify-out" / "GRAPH_REPORT.md").write_text("# Report\n\nnoise\n", encoding="utf-8")
    wiki_memory.rebuild_index(root)
    idx = (root / "index.md").read_text(encoding="utf-8")
    assert "note.md" in idx
    assert "log.md" not in idx
    assert "GRAPH_REPORT" not in idx
    assert "graphify-out" not in idx


def test_rebuild_index_title_falls_back_to_filename(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "run-worker.md").write_text("no heading here, just prose.\n", encoding="utf-8")
    wiki_memory.rebuild_index(root)
    idx = (root / "index.md").read_text(encoding="utf-8")
    assert "[run worker](run-worker.md)" in idx          # slug humanized
    assert "no heading here, just prose." in idx


def test_rebuild_index_empty_wiki_writes_placeholder(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    wiki_memory.rebuild_index(root)
    assert "_No notes yet._" in (root / "index.md").read_text(encoding="utf-8")


def test_rebuild_index_noop_when_dir_missing(tmp_path: Path):
    root = tmp_path / "nope"
    wiki_memory.rebuild_index(root)        # must not raise
    assert not (root / "index.md").exists()


def test_rebuild_index_handles_crlf_frontmatter(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "win.md").write_text("---\r\ndescription: CRLF works\r\n---\r\n# Win\r\n", encoding="utf-8")
    wiki_memory.rebuild_index(root)
    idx = (root / "index.md").read_text(encoding="utf-8")
    assert "CRLF works" in idx


def test_preamble_without_project_still_gives_proxima_context():
    # Even a project-less chat gets a Proxima context block (runner-agnostic),
    # just without the project/wiki guidance.
    p = wiki_memory.build_run_preamble(None, None, None)
    assert p.startswith("[Proxima context]")
    assert "running inside Proxima" in p
    assert "Project:" not in p


def test_preamble_identity_and_index_guidance(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# Wiki index\n", encoding="utf-8")
    p = wiki_memory.build_run_preamble("Proxima", "acme", root)
    assert p.startswith("[Proxima context]")
    assert 'Project: "Proxima" (slug: acme)' in p
    assert "wiki/index.md" in p
    assert "Save to wiki" in p
    assert "graphify" not in p              # no graph present -> no graphify line


def test_preamble_points_at_folder_when_no_index(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "note.md").write_text("# N\n", encoding="utf-8")   # wiki exists, no index.md yet
    p = wiki_memory.build_run_preamble("P", "p", root)
    assert "durable memory as markdown notes in wiki/" in p
    assert "wiki/index.md" not in p


def test_preamble_fresh_project_without_wiki_dir(tmp_path: Path):
    p = wiki_memory.build_run_preamble("P", "p", tmp_path / "wiki")   # dir absent
    assert "[Proxima context]" in p
    assert "will hold this project's durable memory" in p
    assert "index.md" not in p


def test_preamble_includes_graphify_line_when_graph_and_binary_present(tmp_path: Path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "graphify-out").mkdir(parents=True)
    (root / "index.md").write_text("# Wiki index\n", encoding="utf-8")
    (root / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(wiki_memory.shutil, "which", lambda name: "/usr/bin/graphify")
    p = wiki_memory.build_run_preamble("P", "p", root)
    assert "graphify query" in p
    assert "wiki/graphify-out/graph.json" in p


def test_preamble_omits_graphify_when_binary_absent(tmp_path: Path, monkeypatch):
    root = tmp_path / "wiki"
    (root / "graphify-out").mkdir(parents=True)
    (root / "index.md").write_text("# Wiki index\n", encoding="utf-8")
    (root / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(wiki_memory.shutil, "which", lambda name: None)
    p = wiki_memory.build_run_preamble("P", "p", root)
    assert "graphify" not in p


def test_note_summary_skips_frontmatter_without_description():
    # frontmatter present but no description -> summary must be the body sentence,
    # NOT a leaked YAML line like "tags: [...]"
    note = "---\ntags: [infra, db]\nstatus: draft\n---\nWe run Postgres 16 in Docker.\n"
    assert wiki_memory._note_summary(note) == "We run Postgres 16 in Docker."
    # description, when present, still wins
    note2 = "---\ndescription: The DB setup\ntags: [x]\n---\nbody text\n"
    assert wiki_memory._note_summary(note2) == "The DB setup"
    # no frontmatter -> first real line, headings skipped
    note3 = "# Title\n\nFirst real line.\n"
    assert wiki_memory._note_summary(note3) == "First real line."


def test_read_design_guidelines_reads_project_design_md(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    assert wiki_memory.read_design_guidelines(root) is None  # no file yet
    (root / "design.md").write_text("# Brand\n- Primary #FF6A00\n- Font Space Grotesk")
    got = wiki_memory.read_design_guidelines(root)
    assert got is not None and "#FF6A00" in got


def test_build_run_preamble_injects_design_guidelines_only_with_design_studio(tmp_path: Path):
    g = "- Primary #FF6A00\n- Font Space Grotesk"
    with_ds = wiki_memory.build_run_preamble("Demo", "demo", tmp_path / "wiki", include_design_studio=True, design_guidelines=g)
    assert "design.md" in with_ds and "#FF6A00" in with_ds
    # Guidelines are design-scoped: a non-design run must not carry them.
    without_ds = wiki_memory.build_run_preamble("Demo", "demo", tmp_path / "wiki", include_design_studio=False, design_guidelines=g)
    assert "#FF6A00" not in (without_ds or "")


def test_preamble_injects_the_script_library_catalog(tmp_path: Path):
    # The T6 reuse-awareness surface: alongside the wiki catalog, a project
    # session's preamble inlines the scripts/ catalog + the prefer-reuse rule.
    root = tmp_path / "wiki"
    root.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "fetch.sh").write_text("# Description: fetch the sitemap\n", encoding="utf-8")

    p = wiki_memory.build_run_preamble("P", "p", root)
    assert "## Script library (scripts/)" in p
    assert "scripts/fetch.sh — fetch the sitemap" in p
    assert "REUSING" in p


def test_preamble_teaches_the_script_convention_when_library_is_empty(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    p = wiki_memory.build_run_preamble("P", "p", root)
    assert "## Script library (scripts/)" in p
    assert "none yet" in p
    # No project -> no scripts block at all.
    assert "Script library" not in (wiki_memory.build_run_preamble(None, None, None) or "")

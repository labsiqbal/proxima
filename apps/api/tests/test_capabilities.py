"""Per-runtime skill/MCP detection + activation."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api import capabilities as cap


def _app(tmp_path):
    return create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"),
                       "projectctl_path": "/usr/bin/true", "start_worker": False})


class _Spec:
    def __init__(self, rid, src):
        self.id = rid
        self.source_dir = src


# ── detection (unit, against a synthetic host dir) ───────────────────────────

def test_detect_flat_and_grouped_skills(tmp_path):
    src = tmp_path / "runnerhome"
    (src / "skills" / "alpha").mkdir(parents=True)
    (src / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: A flat skill\n---\nbody")
    (src / "skills" / "cat" / "nested").mkdir(parents=True)
    (src / "skills" / "cat" / "nested" / "SKILL.md").write_text("---\nname: nested\ndescription: |\n  Block scalar desc\n---\n")
    d = cap.detect_for_runner(_Spec("claude-code", str(src)))
    ids = {s["id"] for s in d["skills"]}
    assert "alpha" in ids and "cat/nested" in ids
    nested = next(s for s in d["skills"] if s["id"] == "cat/nested")
    assert nested["description"].startswith("Block scalar")  # block-scalar handled


def test_detect_mcp_from_claude_json(tmp_path):
    src = tmp_path / ".claude"
    src.mkdir()
    (src.parent / ".claude.json").write_text(json.dumps({"mcpServers": {"zread": {"type": "http", "url": "https://x/mcp"}}}))
    d = cap.detect_for_runner(_Spec("claude-code", str(src)))
    assert d["mcp"] == [{"name": "zread", "kind": "http", "detail": "https://x/mcp"}]


def test_detect_missing_dir_is_empty():
    d = cap.detect_for_runner(_Spec("claude-code", "/nonexistent/path"))
    assert d == {"skills": [], "mcp": []}


def test_per_runner_skill_subpath_and_hidden_filter(tmp_path):
    # pi reads skills from <home>/agent/skills, not <home>/skills; codex from <home>/skills.
    pi = tmp_path / "pi"
    (pi / "agent" / "skills" / "masterplan").mkdir(parents=True)
    (pi / "agent" / "skills" / "masterplan" / "SKILL.md").write_text("---\nname: masterplan\n---\n")
    (pi / "skills" / "wrong").mkdir(parents=True)  # wrong location — must be ignored
    (pi / "skills" / "wrong" / "SKILL.md").write_text("---\nname: wrong\n---\n")
    assert [s["id"] for s in cap.detect_for_runner(_Spec("pi", str(pi)))["skills"]] == ["masterplan"]

    # hidden/internal groups (e.g. codex .system) are skipped
    cx = tmp_path / "codex"
    (cx / "skills" / ".system" / "imagegen").mkdir(parents=True)
    (cx / "skills" / ".system" / "imagegen" / "SKILL.md").write_text("---\nname: imagegen\n---\n")
    (cx / "skills" / "grill-me").mkdir(parents=True)
    (cx / "skills" / "grill-me" / "SKILL.md").write_text("---\nname: grill-me\n---\n")
    assert [s["id"] for s in cap.detect_for_runner(_Spec("codex", str(cx)))["skills"]] == ["grill-me"]


# ── activation (unit) ────────────────────────────────────────────────────────

def test_apply_symlinks_selected_and_prunes(tmp_path):
    src = tmp_path / ".claude"
    for name in ("keep", "drop"):
        (src / "skills" / name).mkdir(parents=True)
        (src / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (src.parent / ".claude.json").write_text(json.dumps({"mcpServers": {"m1": {"command": "x"}, "m2": {"command": "y"}}}))
    home = tmp_path / "home"
    spec = _Spec("claude-code", str(src))

    cap.apply_capabilities(spec, home, {"skills": ["keep", "drop"], "mcp": ["m1"]})
    assert (home / "skills" / "keep").is_symlink() and (home / "skills" / "drop").is_symlink()
    assert list(json.loads((home / ".claude.json").read_text())["mcpServers"]) == ["m1"]

    # reselect → drop pruned, m2 added
    cap.apply_capabilities(spec, home, {"skills": ["keep"], "mcp": ["m1", "m2"]})
    assert (home / "skills" / "keep").is_symlink()
    assert not (home / "skills" / "drop").exists()
    assert set(json.loads((home / ".claude.json").read_text())["mcpServers"]) == {"m1", "m2"}


def test_apply_codex_mcp_filters_profile_config_and_preserves_other_settings(tmp_path):
    src = tmp_path / ".codex"
    src.mkdir()
    (src / "config.toml").write_text(
        'model = "gpt-5"\n[mcp_servers.keep]\ncommand = "keep"\n[mcp_servers.drop]\ncommand = "drop"\n'
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text((src / "config.toml").read_text())

    applied = cap.apply_capabilities(_Spec("codex", str(src)), home, {"skills": [], "mcp": ["keep"]})
    rendered = (home / "config.toml").read_text()

    assert applied["mcp"] == ["keep"]
    assert 'model = "gpt-5"' in rendered
    assert "mcp_servers.keep" in rendered
    assert "mcp_servers.drop" not in rendered


def test_apply_hermes_mcp_filters_profile_config_and_preserves_other_settings(tmp_path):
    src = tmp_path / ".hermes"
    src.mkdir()
    source = "model: test\nmcp_servers:\n  keep:\n    command: keep\n  drop:\n    command: drop\n"
    (src / "config.yaml").write_text(source)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(source)

    applied = cap.apply_capabilities(_Spec("hermes", str(src)), home, {"skills": [], "mcp": ["keep"]})
    rendered = (home / "config.yaml").read_text()

    assert applied["mcp"] == ["keep"]
    assert "model: test" in rendered
    assert "keep:" in rendered
    assert "drop:" not in rendered


# ── API (integration) ────────────────────────────────────────────────────────

def test_capabilities_endpoint_and_patch(tmp_path):
    c = TestClient(_app(tmp_path))
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    prof = c.get("/api/profiles", headers=h).json()["profiles"][0]
    rid = prof["runner_id"]

    # detection endpoint responds with the shape (contents depend on host)
    r = c.get(f"/api/runners/{rid}/capabilities", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["runner_id"] == rid and "skills" in body and "mcp" in body

    # PATCH a selection persists and round-trips in the payload
    sel = {"skills": ["x"], "mcp": []}
    r2 = c.patch(f"/api/profiles/{prof['id']}", json={"capabilities": sel}, headers=h)
    assert r2.status_code == 200
    assert r2.json()["capabilities"] == sel

    r3 = c.get("/api/runners/does-not-exist/capabilities", headers=h)
    assert r3.status_code == 400


# ── bundled skills (the Proxima capability bundle, T8) ───────────────────────

def _bundle(tmp_path, *names):
    """A synthetic bundle dir: each name becomes a folder+SKILL.md skill."""
    bundle = tmp_path / "bundle"
    for name in names:
        (bundle / name).mkdir(parents=True)
        (bundle / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} desc\n---\n")
    bundle.mkdir(exist_ok=True)
    return bundle


def test_detect_bundled_skills_is_content_pluggable(tmp_path):
    # Whatever folders exist ARE the bundle - no skill list in code. Dropping a
    # new folder in is the whole change.
    bundle = _bundle(tmp_path, "masterplan")
    assert [s["id"] for s in cap.detect_bundled_skills(bundle)] == ["bundled/masterplan"]
    (bundle / "extra").mkdir()
    (bundle / "extra" / "SKILL.md").write_text("---\nname: extra\ndescription: fixture\n---\n")
    ids = [s["id"] for s in cap.detect_bundled_skills(bundle)]
    assert ids == ["bundled/extra", "bundled/masterplan"]
    extra = next(s for s in cap.detect_bundled_skills(bundle) if s["id"] == "bundled/extra")
    assert extra["bundled"] is True and extra["group"] == cap.BUNDLED_GROUP
    assert extra["description"] == "fixture"


def test_detect_bundled_skills_ignores_files_hidden_and_skill_less_dirs(tmp_path):
    bundle = _bundle(tmp_path, "real")
    (bundle / "README.md").write_text("docs, not a skill")
    (bundle / "recommended-tools.json").write_text("{}")
    (bundle / ".hidden").mkdir()
    (bundle / ".hidden" / "SKILL.md").write_text("---\nname: nope\n---\n")
    (bundle / "no-skill-md").mkdir()
    assert [s["id"] for s in cap.detect_bundled_skills(bundle)] == ["bundled/real"]


def test_detect_bundled_skills_missing_or_none_dir_is_empty(tmp_path):
    assert cap.detect_bundled_skills(None) == []
    assert cap.detect_bundled_skills(tmp_path / "nope") == []


def test_detect_for_runner_merges_bundle_as_second_source(tmp_path):
    src = tmp_path / ".claude"
    (src / "skills" / "mine").mkdir(parents=True)
    (src / "skills" / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n")
    bundle = _bundle(tmp_path, "masterplan")
    d = cap.detect_for_runner(_Spec("claude-code", str(src)), bundle_dir=bundle)
    assert {s["id"] for s in d["skills"]} == {"mine", "bundled/masterplan"}
    # without a bundle dir the behavior is exactly the old one
    d2 = cap.detect_for_runner(_Spec("claude-code", str(src)))
    assert {s["id"] for s in d2["skills"]} == {"mine"}


def test_apply_symlinks_bundled_skills_and_optout_prunes(tmp_path):
    src = tmp_path / ".claude"
    src.mkdir()
    bundle = _bundle(tmp_path, "masterplan")
    home = tmp_path / "home"
    spec = _Spec("claude-code", str(src))

    # selection None -> inherit all, bundle included
    applied = cap.apply_capabilities(spec, home, None, bundle_dir=bundle)
    assert "bundled/masterplan" in applied["skills"]
    link = home / "skills" / "bundled" / "masterplan"
    assert link.is_symlink() and (link / "SKILL.md").is_file()

    # opt-out via the existing selection JSON prunes the symlink (and group dir)
    cap.apply_capabilities(spec, home, {"skills": [], "mcp": []}, bundle_dir=bundle)
    assert not link.exists()
    assert not (home / "skills" / "bundled").exists()

    # opting back in restores it
    cap.apply_capabilities(spec, home, {"skills": ["bundled/masterplan"], "mcp": []}, bundle_dir=bundle)
    assert link.is_symlink()


def test_bundled_skill_never_clobbers_same_named_host_skill(tmp_path):
    # A host skill named like a bundled one keeps its flat id; the bundled copy
    # lives under bundled/ - both coexist, nothing is overwritten.
    src = tmp_path / ".claude"
    (src / "skills" / "masterplan").mkdir(parents=True)
    (src / "skills" / "masterplan" / "SKILL.md").write_text("---\nname: masterplan\n---\n")
    bundle = _bundle(tmp_path, "masterplan")
    home = tmp_path / "home"
    cap.apply_capabilities(_Spec("claude-code", str(src)), home, None, bundle_dir=bundle)
    import os as _os
    assert _os.path.realpath(home / "skills" / "masterplan") == _os.path.realpath(src / "skills" / "masterplan")
    assert _os.path.realpath(home / "skills" / "bundled" / "masterplan") == _os.path.realpath(bundle / "masterplan")


def test_real_repo_bundle_ships_masterplan_with_provenance():
    # The shipped bundle: masterplan is present, vendored with provenance +
    # license, and detected purely from directory content.
    from proxima_api.settings import repo_root
    bundle = repo_root() / "bundled-skills"
    detected = {s["id"]: s for s in cap.detect_bundled_skills(bundle)}
    assert "bundled/masterplan" in detected
    assert detected["bundled/masterplan"]["name"] == "masterplan"
    assert (bundle / "masterplan" / "PROVENANCE.md").is_file()
    assert (bundle / "masterplan" / "LICENSE").is_file()


def test_profile_creation_applies_bundle_and_live_home_is_noop(tmp_path, monkeypatch):
    # End-to-end through the API: an isolated profile home gets the bundled
    # skills symlinked; live-home mode applies NOTHING (the user's own ~/.claude
    # already rules).
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    (tmp_path / "fakehome").mkdir()
    bundle = _bundle(tmp_path, "masterplan")

    c = TestClient(create_app({"database_path": str(tmp_path / "a.db"), "workspace_root": str(tmp_path / "ws"),
                               "projectctl_path": "/usr/bin/true", "start_worker": False,
                               "bundled_skills_dir": str(bundle)}))
    tok = c.post("/auth/auto").json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    prof = c.post("/api/profiles", json={"name": "Iso", "runner_id": "claude-code"}, headers=h).json()
    assert (Path(prof["hermes_home"]) / "skills" / "bundled" / "masterplan").is_symlink()

    live = TestClient(create_app({"database_path": str(tmp_path / "b.db"), "workspace_root": str(tmp_path / "ws2"),
                                  "projectctl_path": "/usr/bin/true", "start_worker": False,
                                  "claude_live_home": True, "bundled_skills_dir": str(bundle)}))
    tok2 = live.post("/auth/auto").json()["token"]
    h2 = {"Authorization": f"Bearer {tok2}"}
    prof2 = live.post("/api/profiles", json={"name": "Live", "runner_id": "claude-code"}, headers=h2).json()
    live_home = Path(prof2["hermes_home"])
    assert live_home == tmp_path / "fakehome" / ".claude"
    assert not (live_home / "skills").exists()  # nothing seeded or symlinked

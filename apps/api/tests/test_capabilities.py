"""Per-runtime skill/MCP detection + activation."""
import json

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

    r3 = c.get(f"/api/runners/does-not-exist/capabilities", headers=h)
    assert r3.status_code == 400

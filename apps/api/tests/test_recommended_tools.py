"""Recommended host tools: detect-and-advertise, never vendor (T8)."""
import json
import os
import stat

from fastapi.testclient import TestClient

from proxima_api import recommended_tools as rt
from proxima_api.main import create_app


def _bundle_with_tools(tmp_path, tools):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / rt.RECOMMENDED_TOOLS_FILENAME).write_text(json.dumps({"tools": tools}))
    return bundle


def _fake_bin(tmp_path, name):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def test_load_recommended_tools_is_data_driven_and_defensive(tmp_path):
    bundle = _bundle_with_tools(tmp_path, [
        {"bin": "markitdown", "use": "document conversion", "install": "pip install markitdown"},
        {"bin": "  "},          # blank bin -> dropped
        "not-a-dict",            # junk entry -> dropped
        {"use": "no bin"},      # missing bin -> dropped
    ])
    tools = rt.load_recommended_tools(bundle)
    assert tools == [{"bin": "markitdown", "use": "document conversion", "install": "pip install markitdown"}]

    assert rt.load_recommended_tools(None) == []
    assert rt.load_recommended_tools(tmp_path / "missing") == []
    (bundle / rt.RECOMMENDED_TOOLS_FILENAME).write_text("{ not json")
    assert rt.load_recommended_tools(bundle) == []


def test_probe_flags_present_tools_from_path(tmp_path, monkeypatch):
    bundle = _bundle_with_tools(tmp_path, [
        {"bin": "fm-fake-tool", "use": "testing", "install": "n/a"},
        {"bin": "fm-absent-tool", "use": "never installed", "install": "n/a"},
    ])
    monkeypatch.setenv("PATH", str(_fake_bin(tmp_path, "fm-fake-tool")))
    probed = {t["bin"]: t["present"] for t in rt.probe_recommended_tools(bundle)}
    assert probed == {"fm-fake-tool": True, "fm-absent-tool": False}
    assert [t["bin"] for t in rt.present_tools(bundle)] == ["fm-fake-tool"]


def test_tools_preamble_block_advertises_present_only():
    block = rt.tools_preamble_block([
        {"bin": "markitdown", "use": "document conversion", "present": True},
        {"bin": "bare", "use": "", "present": True},
    ])
    assert "### Host tools available" in block
    assert "`markitdown` - available for document conversion" in block
    assert "`bare` - available" in block
    assert rt.tools_preamble_block([]) is None


def test_real_repo_bundle_ships_a_tools_list():
    from proxima_api.settings import repo_root
    tools = rt.load_recommended_tools(repo_root() / "bundled-skills")
    assert {t["bin"] for t in tools} >= {"markitdown", "lavish-axi", "gh"}
    assert all(t["use"] for t in tools)  # every entry carries its one-liner


def test_recommended_tools_endpoint(tmp_path, monkeypatch):
    bundle = _bundle_with_tools(tmp_path, [{"bin": "fm-fake-tool", "use": "testing", "install": "n/a"}])
    monkeypatch.setenv("PATH", str(_fake_bin(tmp_path, "fm-fake-tool")) + os.pathsep + os.environ.get("PATH", ""))
    c = TestClient(create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "ws"),
                               "projectctl_path": "/usr/bin/true", "start_worker": False,
                               "bundled_skills_dir": str(bundle)}))
    tok = c.post("/auth/auto").json()["token"]
    r = c.get("/api/tools/recommended", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["tools"] == [{"bin": "fm-fake-tool", "use": "testing", "install": "n/a", "present": True}]

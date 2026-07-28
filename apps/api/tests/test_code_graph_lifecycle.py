"""Group 10: Code graph lifecycle - registration, merge, audit, MCP, isolation."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api.capabilities import (
    CODE_GRAPH_MCP_NAME,
    apply_fixed_code_graph_mcp,
    remove_fixed_code_graph_mcp,
)
from proxima_api.code_graph_lifecycle import (
    REASON_TASK_MERGED,
    CodeGraphLifecycle,
)
from proxima_api.graph_context import GRAPHIFY_GITIGNORE_LINE
from proxima_api.graphify_area_mcp import _query, main as mcp_main
from proxima_api.main import create_app


def _git(cwd: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_AUTHOR_NAME"] = "Proxima Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Proxima Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _api(tmp_path: Path, **config) -> tuple[TestClient, dict[str, str]]:
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "runtime"),
            "projectctl_path": "/usr/bin/true",
            "link_roots": [str(tmp_path)],
            "auto_provision": False,
            "start_worker": False,
            "update_check": False,
            "container_registry_refresh_seconds": 0,
            "feature_master_orchestrator": True,
            "code_graph_tick_seconds": 1,
            "code_graph_audit_seconds": 30,
            "code_graph_dirty_debounce_seconds": 1,
            **config,
        }
    )
    api = TestClient(app)
    token = api.post("/auth/auto").json()["token"]
    return api, {"Authorization": f"Bearer {token}"}


def _init_repo(path: Path, *, name: str = "app.py") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "checkout", "-b", "main")
    (path / name).write_text(
        "class BillingService:\n"
        "    def charge(self):\n"
        "        return 'paid'\n",
        encoding="utf-8",
    )
    _git(path, "add", name)
    _git(path, "commit", "-m", "init")


def _container_with_areas(
    api: TestClient,
    headers: dict[str, str],
    tmp_path: Path,
    *,
    slug: str = "life-one",
    area_count: int = 2,
) -> tuple[dict, list[int]]:
    response = api.post(
        "/api/projects",
        headers=headers,
        json={"slug": slug, "name": slug},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    root = Path(project["path"])
    area_ids: list[int] = []
    for index in range(area_count):
        rel = f"repo-{index}"
        area_root = root / rel
        _init_repo(area_root)
        added = api.post(
            f"/api/projects/{slug}/areas",
            headers=headers,
            json={"rel_path": rel},
        )
        assert added.status_code == 201, added.text
        area_ids.append(int(added.json()["id"]))
    return project, area_ids


def test_multiple_areas_receive_distinct_paths_state_and_generations(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    project, area_ids = _container_with_areas(api, headers, tmp_path)
    assert len(area_ids) == 2

    graphs = api.get(
        "/api/containers/life-one/graphs",
        headers=headers,
    ).json()["graphs"]
    code = [item for item in graphs if item["scope"]["kind"] == "code"]
    assert {item["scope"]["area_id"] for item in code} == set(area_ids)
    assert all(item["state"] == "queued" for item in code)

    for area_id in area_ids:
        rebuilt = api.post(
            "/api/containers/life-one/graphs/rebuild",
            headers=headers,
            json={"kind": "code", "area_id": area_id},
        )
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json()["state"] == "fresh"
        assert rebuilt.json()["generation"] == 1

    rows = api.app.state.db.execute(
        "SELECT area_id, graph_path, source_fingerprint, generation, repo_head "
        "FROM graph_states WHERE kind = 'code' ORDER BY area_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["graph_path"] != rows[1]["graph_path"]
    assert rows[0]["source_fingerprint"] != rows[1]["source_fingerprint"] or (
        Path(rows[0]["graph_path"]).parent.parent.name
        != Path(rows[1]["graph_path"]).parent.parent.name
    )
    assert all(int(row["generation"]) == 1 for row in rows)
    assert all(row["repo_head"] for row in rows)
    for row in rows:
        graph_path = Path(row["graph_path"])
        assert graph_path.is_file()
        exclude = graph_path.parent.parent / ".git" / "info" / "exclude"
        assert exclude.is_file()
        assert GRAPHIFY_GITIGNORE_LINE in exclude.read_text(encoding="utf-8")
    public = api.get(
        "/api/containers/life-one/graphs",
        headers=headers,
    ).json()
    serialized = json.dumps(public)
    assert project["path"] not in serialized
    assert "graphify-out" not in serialized


def test_cross_area_symlink_escape_fails_closed(tmp_path: Path):
    api, headers = _api(tmp_path)
    project, area_ids = _container_with_areas(
        api,
        headers,
        tmp_path,
        area_count=1,
    )
    outside = tmp_path / "outside-secret"
    outside.mkdir()
    (outside / ".git").mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    escape = Path(project["path"]) / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    container_id = int(
        api.app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'life-one'"
        ).fetchone()["id"]
    )
    api.app.state.db.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'code', 'escape', 'manual')",
        (container_id,),
    )

    listed = api.get("/api/containers/life-one/graphs", headers=headers)
    assert listed.status_code == 409, listed.text
    assert "escapes" in listed.text.lower() or "symlink" in listed.text.lower() or "Container" in listed.text
    assert str(outside) not in listed.text

    rebuild = api.post(
        "/api/containers/life-one/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_ids[0] + 999},
    )
    assert rebuild.status_code in {409, 422}


def test_task_merge_marks_only_target_stale_then_publishes_fresh(
    tmp_path: Path,
):
    api, headers = _api(tmp_path)
    _, area_ids = _container_with_areas(api, headers, tmp_path, area_count=2)
    target, other = area_ids
    for area_id in area_ids:
        body = api.post(
            "/api/containers/life-one/graphs/rebuild",
            headers=headers,
            json={"kind": "code", "area_id": area_id},
        ).json()
        assert body["state"] == "fresh"

    container_id = int(
        api.app.state.db.execute(
            "SELECT id FROM projects WHERE slug = 'life-one'"
        ).fetchone()["id"]
    )
    owner_id = int(
        api.app.state.db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    )
    root = Path(
        api.app.state.db.execute(
            "SELECT root_path FROM graph_states WHERE area_id = ?",
            (target,),
        ).fetchone()["root_path"]
    )
    before_head = api.app.state.graph_context.repo_head_sha(root)
    (root / "app.py").write_text(
        "class BillingService:\n"
        "    def refund(self):\n"
        "        return 'refunded'\n",
        encoding="utf-8",
    )
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "task land")
    after_head = api.app.state.graph_context.repo_head_sha(root)

    lifecycle: CodeGraphLifecycle = api.app.state.code_graph_lifecycle
    lifecycle.on_task_merged(
        owner_user_id=owner_id,
        container_id=container_id,
        area_id=target,
        base_commit=before_head,
        merge_commit=after_head,
    )

    states = {
        int(row["area_id"]): row
        for row in api.app.state.db.execute(
            "SELECT area_id, state, rebuild_reason, generation "
            "FROM graph_states WHERE kind = 'code'"
        ).fetchall()
    }
    assert states[target]["state"] == "queued"
    assert states[target]["rebuild_reason"] == REASON_TASK_MERGED
    assert int(states[target]["generation"]) == 1
    assert states[other]["state"] == "fresh"

    lifecycle.tick()
    after = api.app.state.db.execute(
        "SELECT state, generation, source_fingerprint, repo_head "
        "FROM graph_states WHERE area_id = ?",
        (target,),
    ).fetchone()
    assert after["state"] == "fresh"
    assert int(after["generation"]) == 2
    assert after["repo_head"] == after_head
    assert after["source_fingerprint"]


def test_failed_rebuild_preserves_last_good_bytes(
    tmp_path: Path,
    monkeypatch,
):
    api, headers = _api(tmp_path)
    _, area_ids = _container_with_areas(api, headers, tmp_path, area_count=1)
    area_id = area_ids[0]
    first = api.post(
        "/api/containers/life-one/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_id},
    ).json()
    graph_path = Path(
        api.app.state.db.execute(
            "SELECT graph_path FROM graph_states WHERE id = ?",
            (first["id"],),
        ).fetchone()["graph_path"]
    )
    before = graph_path.read_bytes()
    service = api.app.state.graph_context

    def boom(**_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(service, "_run_builder", boom)
    failed = api.post(
        "/api/containers/life-one/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_id},
    )
    assert failed.status_code == 409, failed.text
    assert graph_path.read_bytes() == before
    row = api.app.state.db.execute(
        "SELECT state, generation FROM graph_states WHERE id = ?",
        (first["id"],),
    ).fetchone()
    assert row["state"] == "failed"
    assert int(row["generation"]) == 1


def test_external_head_audit_only_touches_registered_areas(tmp_path: Path):
    api, headers = _api(tmp_path)
    _, area_ids = _container_with_areas(api, headers, tmp_path, area_count=1)
    area_id = area_ids[0]
    api.post(
        "/api/containers/life-one/graphs/rebuild",
        headers=headers,
        json={"kind": "code", "area_id": area_id},
    )
    # Unrelated container path that must not be scanned as a graph root.
    unrelated = tmp_path / "unrelated-container" / "repo"
    _init_repo(unrelated)

    root = Path(
        api.app.state.db.execute(
            "SELECT root_path FROM graph_states WHERE area_id = ?",
            (area_id,),
        ).fetchone()["root_path"]
    )
    (root / "app.py").write_text(
        "class BillingService:\n"
        "    def external(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "external")

    lifecycle: CodeGraphLifecycle = api.app.state.code_graph_lifecycle
    lifecycle._last_audit_at = 0.0
    lifecycle._audit_registered_code_graphs()
    row = api.app.state.db.execute(
        "SELECT state, rebuild_reason FROM graph_states WHERE area_id = ?",
        (area_id,),
    ).fetchone()
    assert row["state"] == "queued"
    assert row["rebuild_reason"] in {"external_head", "scheduled_audit"}
    # No graph_states row for the unrelated path.
    assert (
        api.app.state.db.execute(
            "SELECT COUNT(*) FROM graph_states WHERE root_path = ?",
            (str(unrelated),),
        ).fetchone()[0]
        == 0
    )


def test_fixed_code_graph_mcp_ignores_project_path(tmp_path: Path, monkeypatch):
    graph = tmp_path / "graphify-out" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "BillingService",
                        "label": "BillingService",
                        "source_file": "app.py",
                    }
                ],
                "links": [],
                "graph": {},
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "runner-home"
    home.mkdir()
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    applied = apply_fixed_code_graph_mcp("claude-code", home, graph)
    assert applied == CODE_GRAPH_MCP_NAME
    cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    entry = cfg["mcpServers"][CODE_GRAPH_MCP_NAME]
    assert entry["env"]["PROXIMA_CODE_GRAPH_PATH"] == str(graph.resolve())
    assert "project_path" not in entry.get("args", [])

    # Query helper never uses project_path; hostile alternate path is irrelevant.
    data = json.loads(graph.read_text(encoding="utf-8"))
    text = _query(data, "BillingService", depth=1, limit=5)
    assert "BillingService" in text

    remove_fixed_code_graph_mcp("claude-code", home)
    cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert CODE_GRAPH_MCP_NAME not in (cfg.get("mcpServers") or {})


def test_fixed_code_graph_mcp_live_home_uses_sibling_claude_json(tmp_path: Path):
    """Live CLAUDE_CONFIG_DIR=~/.claude must mutate sibling ~/.claude.json."""
    from proxima_api.acp import config_sig

    graph = tmp_path / "graphify-out" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text("{}", encoding="utf-8")
    fake_home = tmp_path / "owner"
    live_dir = fake_home / ".claude"
    live_dir.mkdir(parents=True)
    sibling = fake_home / ".claude.json"
    sibling.write_text(
        json.dumps({"mcpServers": {"personal": {"command": "keep-me"}}}),
        encoding="utf-8",
    )

    applied = apply_fixed_code_graph_mcp("claude-code", live_dir, graph)
    assert applied == CODE_GRAPH_MCP_NAME
    assert not (live_dir / ".claude.json").exists()
    cfg = json.loads(sibling.read_text(encoding="utf-8"))
    assert set(cfg["mcpServers"]) == {"personal", CODE_GRAPH_MCP_NAME}
    assert cfg["mcpServers"][CODE_GRAPH_MCP_NAME]["env"]["PROXIMA_CODE_GRAPH_PATH"] == str(
        graph.resolve()
    )
    # config_sig must watch the sibling host file Claude actually reads.
    assert config_sig(str(live_dir))[3] == round(sibling.stat().st_mtime, 3)

    remove_fixed_code_graph_mcp("claude-code", live_dir)
    cfg = json.loads(sibling.read_text(encoding="utf-8"))
    assert list(cfg["mcpServers"]) == ["personal"]
    assert CODE_GRAPH_MCP_NAME not in cfg["mcpServers"]
    assert config_sig(str(live_dir))[3] == round(sibling.stat().st_mtime, 3)


def test_worktree_path_cannot_become_canonical_scope(tmp_path: Path):
    from proxima_api.graph_context import _is_worktree_path

    api, headers = _api(tmp_path)
    project, area_ids = _container_with_areas(
        api,
        headers,
        tmp_path,
        area_count=1,
    )
    workspace = Path(api.app.state.config["workspace_root"])
    fake_wt = workspace / "worktrees" / "job-1"
    fake_wt.mkdir(parents=True)
    assert _is_worktree_path(fake_wt, workspace)
    assert not _is_worktree_path(Path(project["path"]) / "repo-0", workspace)

    # Canonical Area roots remain the registered path, never the job worktree.
    scope = api.app.state.graph_context.resolve_scope(
        owner_user_id=1,
        container_slug="life-one",
        kind="code",
        area_id=area_ids[0],
    )
    assert not _is_worktree_path(scope.root, workspace)
    assert scope.root == (Path(project["path"]) / "repo-0").resolve()


def test_graph_absence_does_not_block_jobs_or_live_state(tmp_path: Path):
    api, headers = _api(tmp_path)
    api.post(
        "/api/projects",
        headers=headers,
        json={"slug": "no-graph", "name": "No Graph"},
    )
    jobs = api.get("/api/jobs", headers=headers)
    assert jobs.status_code == 200
    fleet = api.get("/api/containers", headers=headers)
    assert fleet.status_code == 200
    assert fleet.json()["containers"][0]["live"] == {
        "running_tasks": 0,
        "queued_tasks": 0,
        "open_attention": 0,
    }


def test_mcp_main_requires_fixed_path(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PROXIMA_CODE_GRAPH_PATH", raising=False)
    with pytest.raises(SystemExit):
        mcp_main()


def test_incremental_sources_reject_untracked_detect_only_files(tmp_path: Path):
    api, headers = _api(tmp_path)
    project, area_ids = _container_with_areas(
        api,
        headers,
        tmp_path,
        area_count=1,
    )
    graphs = api.app.state.graph_context
    scope = graphs.resolve_scope(
        owner_user_id=1,
        container_slug="life-one",
        kind="code",
        area_id=area_ids[0],
    )
    head = graphs.repo_head_sha(scope.root)
    assert head
    assert graphs.incremental_sources_covered_by_commit(scope, head)

    # Untracked code is detected by Graphify but is outside the commit tree.
    (scope.root / "untracked_extra.py").write_text(
        "class Extra:\n    pass\n",
        encoding="utf-8",
    )
    assert not graphs.incremental_sources_covered_by_commit(scope, head)

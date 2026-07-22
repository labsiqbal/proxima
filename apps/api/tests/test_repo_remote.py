"""BYO repo-remote connector (Phase-1 slice 11, T9).

Covers the T9 contract end to end against scratch repos and SCRATCH BARE
remotes (never a real network remote): toggle appearance rules (remote vs
no-remote), the default-off regression, push-on-merge happy path through
approve, failure surfacing WITHOUT un-merging plus the retry action, the
never-push-unless-opted-in guardrail, the non-GitHub remote path (a plain
path remote - any `git push`-able URL works), and the GitHub URL parsing
that powers the gh-era enrichment (pure string work, no network).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import repo_remote
from proxima_api.main import create_app
from proxima_api.repo_remote import detect_remote, github_web_url


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert res.returncode == 0, f"git {args}: {res.stderr}"
    return res.stdout.strip()


def _scratch_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _bare_remote(repo: Path, bare: Path) -> Path:
    """Wire `repo` to a scratch BARE repo as its `origin` and push main up -
    the stand-in for GitHub/GitLab/self-hosted in every test here."""
    bare.mkdir(parents=True, exist_ok=True)
    _git(bare, "init", "-q", "--bare", "-b", "main")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    return bare


def _app(tmp_path: Path, **config):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(tmp_path)],
        "start_worker": False,
        "feature_repo_worktrees": True,
        **config,
    })


def _client(app) -> TestClient:
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _repo_job(c: TestClient, slug: str, folder: Path) -> tuple[dict, int]:
    """Link `folder` as a project; return (job targeting its first code area,
    that area's id)."""
    p = c.post("/api/projects/link", json={"path": str(folder), "slug": slug})
    assert p.status_code == 201, p.text
    area_id = p.json()["code_areas"][0]["id"]
    job = c.post("/api/jobs", json={"project_slug": slug, "input": {"brief": "change it"}, "target_area_id": area_id})
    assert job.status_code == 200, job.text
    return job.json(), area_id


def _merge_ready(c: TestClient, app, job: dict, edit: str = "patched\n") -> dict:
    """Start the repo job, make an edit in its worktree, park it at review."""
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    wt = c.get(f"/api/jobs/{job['id']}").json()["worktree"]
    Path(wt["worktree_path"], "agent-change.txt").write_text(edit, encoding="utf-8")
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))
    return wt


# ── remote detection + GitHub URL parsing (unit) ─────────────────────────


def test_github_web_url_parses_the_common_remote_shapes():
    for url in (
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo",
        "https://github.com/owner/repo/",
        "https://token@github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
        "git@github.com:owner/repo",
        "ssh://git@github.com/owner/repo.git",
    ):
        assert github_web_url(url) == "https://github.com/owner/repo", url


def test_github_web_url_rejects_non_github_remotes():
    for url in (
        "https://gitlab.com/owner/repo.git",
        "git@gitlab.example.com:owner/repo.git",
        "ssh://git@git.sr.ht/~owner/repo",
        "/tmp/scratch/bare.git",
        "https://github.com.evil.example/owner/repo.git",
        "",
    ):
        assert github_web_url(url) is None, url


def test_detect_remote_none_without_a_remote_or_repo(tmp_path: Path):
    assert detect_remote(_scratch_repo(tmp_path / "repo")) is None
    plain = tmp_path / "plain"
    plain.mkdir()
    assert detect_remote(plain) is None


def test_detect_remote_prefers_origin_and_reads_its_url(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "upstream", "/elsewhere/upstream.git")
    _git(repo, "remote", "add", "origin", "/elsewhere/origin.git")
    remote = detect_remote(repo)
    assert remote is not None
    assert remote["name"] == "origin" and remote["url"] == "/elsewhere/origin.git"
    # A plain-path remote is not GitHub: no web link, no gh enrichment key.
    assert remote["web_url"] is None
    assert "gh_authenticated" not in remote


def test_detect_remote_github_enrichment_never_requires_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _scratch_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "git@github.com:owner/repo.git")
    # gh absent entirely - the link still surfaces, gh is just reported false.
    monkeypatch.setattr(repo_remote, "gh_authenticated", lambda: False)
    remote = detect_remote(repo)
    assert remote is not None
    assert remote["web_url"] == "https://github.com/owner/repo"
    assert remote["gh_authenticated"] is False
    monkeypatch.setattr(repo_remote, "gh_authenticated", lambda: True)
    assert detect_remote(repo)["gh_authenticated"] is True


# ── toggle appearance + default-off (API) ────────────────────────────────


def test_areas_payload_offers_remote_only_where_one_exists(tmp_path: Path):
    container = tmp_path / "container"
    container.mkdir()
    connected = _scratch_repo(container / "connected")
    _bare_remote(connected, tmp_path / "bare.git")
    _scratch_repo(container / "local-only")
    c = _client(_app(tmp_path))
    assert c.post("/api/projects/link", json={"path": str(container), "slug": "c"}).status_code == 201

    areas = {a["rel_path"]: a for a in c.get("/api/projects/c/areas").json()["code_areas"]}
    assert areas["connected"]["remote"]["name"] == "origin"
    assert areas["connected"]["remote"]["url"] == str(tmp_path / "bare.git")
    assert areas["local-only"]["remote"] is None  # no remote -> no toggle offered
    # Default-off regression (the T9 guardrail): detection NEVER opts in.
    assert areas["connected"]["push_on_merge"] is False
    assert areas["local-only"]["push_on_merge"] is False
    # The re-detect payload carries the same pairing.
    redetected = c.post("/api/projects/c/areas/detect").json()["code_areas"]
    assert {a["rel_path"]: a["remote"] is not None for a in redetected} == {"connected": True, "local-only": False}


def test_toggle_on_requires_a_remote_off_always_works(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    c = _client(_app(tmp_path))
    assert c.post("/api/projects/link", json={"path": str(repo), "slug": "r"}).status_code == 201
    area_id = c.get("/api/projects/r/areas").json()["code_areas"][0]["id"]

    refused = c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True})
    assert refused.status_code == 409
    assert "no git remote" in refused.json()["detail"]
    assert c.get("/api/projects/r/areas").json()["code_areas"][0]["push_on_merge"] is False
    # Off is always accepted, remote or not.
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": False}).status_code == 200

    _bare_remote(repo, tmp_path / "bare.git")
    enabled = c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["push_on_merge"] is True
    assert enabled.json()["remote"]["name"] == "origin"
    assert c.get("/api/projects/r/areas").json()["code_areas"][0]["push_on_merge"] is True


def test_toggle_is_a_code_area_setting_only(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    c = _client(_app(tmp_path))
    p = c.post("/api/projects/link", json={"path": str(repo), "slug": "r"}).json()
    ops_id = p["ops_area"]["id"]
    assert c.patch(f"/api/projects/r/areas/{ops_id}", json={"push_on_merge": True}).status_code == 404
    assert c.patch("/api/projects/r/areas/99999", json={"push_on_merge": True}).status_code == 404


# ── push on merge: happy path, guardrail, failure, retry ─────────────────


def test_approve_pushes_merged_main_when_toggle_on(tmp_path: Path):
    """The lifecycle placement (T9 point 3): local merge first, then push of
    the merged main line to the (non-GitHub, plain-path) remote."""
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    _merge_ready(c, app, job)

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    wt = approved.json()["worktree"]
    assert wt["status"] == "merged"
    assert wt["push_status"] == "pushed"
    assert wt["push_error"] is None
    assert wt["push_remote"] == "origin"
    assert wt["push_web_url"] is None  # path remote: no GitHub enrichment
    # The remote's main IS the local merge commit.
    assert _git(bare, "rev-parse", "main") == wt["merge_commit"] == _git(repo, "rev-parse", "main")


def test_approve_never_pushes_by_default(tmp_path: Path):
    """The guardrail: a configured remote alone must never trigger a push -
    only the explicit per-area opt-in does."""
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    before = _git(bare, "rev-parse", "main")
    app = _app(tmp_path)
    c = _client(app)
    job, _area_id = _repo_job(c, "r", repo)
    _merge_ready(c, app, job)

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "done"
    wt = approved.json()["worktree"]
    assert wt["status"] == "merged"
    assert wt["push_status"] is None  # no attempt, not a recorded failure
    assert _git(bare, "rev-parse", "main") == before  # remote untouched
    # And the retry door refuses too: no opt-in, no push.
    refused = c.post(f"/api/jobs/{job['id']}/push")
    assert refused.status_code == 409
    assert "off for this code area" in refused.json()["detail"]


def test_failed_push_never_unmerges_and_surfaces_the_exact_command(tmp_path: Path):
    """T9 point 4: the remote diverges under us -> the job still lands as
    done-and-merged locally; the failure carries the concrete command +
    git's own message, and retry succeeds once the owner resolves."""
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    _merge_ready(c, app, job)
    # Someone else pushes to the remote after the job was cut: push must fail.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    (other / "elsewhere.txt").write_text("newer\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "raced ahead")
    _git(other, "push", "-q", "origin", "main")
    raced = _git(bare, "rev-parse", "main")

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200, approved.text  # approve NEVER fails on push
    body = approved.json()
    assert body["status"] == "done"  # done-and-merged locally, not un-merged
    wt = body["worktree"]
    assert wt["status"] == "merged" and wt["merge_commit"]
    assert (repo / "agent-change.txt").exists()  # the merge stands
    assert wt["push_status"] == "failed"
    # The exact command - including the F3 hardening flags that neutralize
    # agent-planted credential helpers and hooks.
    assert "$ git -c credential.helper= -c core.hooksPath=/dev/null push origin main" in wt["push_error"]
    assert "rejected" in wt["push_error"]  # + git's own output
    assert _git(bare, "rev-parse", "main") == raced  # never forced

    # Retry while still diverged: same blocker again, still merged.
    retried = c.post(f"/api/jobs/{job['id']}/push")
    assert retried.status_code == 200 and retried.json()["status"] == "failed"
    # The owner resolves (rewinds the remote here) and retries: push lands.
    _git(other, "push", "-q", "-f", "origin", f"{raced}~1:main")
    resolved = c.post(f"/api/jobs/{job['id']}/push")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "pushed"
    assert resolved.json()["worktree"]["push_status"] == "pushed"
    assert resolved.json()["worktree"]["push_error"] is None
    assert _git(bare, "rev-parse", "main") == wt["merge_commit"]


def test_push_retry_requires_a_locally_merged_job(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    # Not merged yet (worktree active) -> nothing to push.
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    res = c.post(f"/api/jobs/{job['id']}/push")
    assert res.status_code == 409
    assert "not merged" in res.json()["detail"]


def test_push_failure_when_remote_vanished_after_opt_in(tmp_path: Path):
    """Toggle on, then the remote goes away: the push surfaces the honest
    missing-remote blocker instead of pretending, and the merge stands."""
    repo = _scratch_repo(tmp_path / "repo")
    _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    _merge_ready(c, app, job)
    _git(repo, "remote", "remove", "origin")

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "done"
    wt = approved.json()["worktree"]
    assert wt["status"] == "merged"
    assert wt["push_status"] == "failed"
    assert "no git remote" in wt["push_error"]


# ── push target pinning + invocation hardening (audit F3) ────────────────


def test_enabling_the_toggle_pins_the_remote_url(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    c = _client(_app(tmp_path))
    assert c.post("/api/projects/link", json={"path": str(repo), "slug": "r"}).status_code == 201
    area_id = c.get("/api/projects/r/areas").json()["code_areas"][0]["id"]

    enabled = c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True})
    assert enabled.status_code == 200
    assert enabled.json()["push_remote_url"] == str(bare)
    assert c.get("/api/projects/r/areas").json()["code_areas"][0]["push_remote_url"] == str(bare)
    # Disabling clears the pin - a later re-enable re-reads and re-approves.
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": False}).status_code == 200
    assert c.get("/api/projects/r/areas").json()["code_areas"][0]["push_remote_url"] is None


def test_push_refuses_when_the_remote_url_changed_after_opt_in(tmp_path: Path):
    """The exfiltration vector (audit F3): a prompt-injected agent rewrites
    the repo's own .git/config to point somewhere else. The push must refuse
    instead of obeying the config - and the merge still stands."""
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    _merge_ready(c, app, job)
    # The agent's move: redirect origin to an attacker-controlled target.
    attacker = tmp_path / "attacker.git"
    _git(attacker.parent, "init", "-q", "--bare", "-b", "main", str(attacker))
    _git(repo, "remote", "set-url", "origin", str(attacker))

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "done"
    wt = approved.json()["worktree"]
    assert wt["status"] == "merged"  # never un-merged
    assert wt["push_status"] == "failed"
    assert "push refused" in wt["push_error"]
    assert str(bare) in wt["push_error"] and str(attacker) in wt["push_error"]
    # Nothing reached the attacker's remote.
    assert _git(attacker, "for-each-ref") == ""

    # The owner restores the approved remote and retries: push lands.
    _git(repo, "remote", "set-url", "origin", str(bare))
    resolved = c.post(f"/api/jobs/{job['id']}/push")
    assert resolved.status_code == 200 and resolved.json()["status"] == "pushed"
    assert _git(bare, "rev-parse", "main") == wt["merge_commit"]


def test_push_refuses_without_a_pinned_url(tmp_path: Path):
    """An opt-in with no pin (pre-pinning row, or the area was deleted) has no
    owner-approved target - the push refuses rather than trusting .git/config."""
    repo = _scratch_repo(tmp_path / "repo")
    _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    # Simulate a legacy opt-in: toggle on, but no pinned URL recorded.
    app.state.db.execute("UPDATE project_areas SET push_on_merge = 1 WHERE id = ?", (area_id,))
    _merge_ready(c, app, job)

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "done"
    wt = approved.json()["worktree"]
    assert wt["status"] == "merged"
    assert wt["push_status"] == "failed"
    assert "no pinned remote URL" in wt["push_error"]
    assert "re-enable" in wt["push_error"]

    # Re-enabling through the settings route pins the URL; retry then works.
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    resolved = c.post(f"/api/jobs/{job['id']}/push")
    assert resolved.status_code == 200 and resolved.json()["status"] == "pushed"


def test_planted_hooks_and_helpers_never_execute_at_push_time(tmp_path: Path):
    """The exec vector (audit F3): an agent plants a pre-push hook (both the
    default hooks dir and a config-redirected one). The hardened invocation
    must push successfully WITHOUT running any of it."""
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path)
    c = _client(app)
    job, area_id = _repo_job(c, "r", repo)
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    _merge_ready(c, app, job)

    marker = tmp_path / "hook-ran"
    hook_body = f"#!/bin/sh\ntouch {marker}\nexit 1\n"  # exit 1 would also block the push
    default_hook = repo / ".git" / "hooks" / "pre-push"
    default_hook.write_text(hook_body, encoding="utf-8")
    default_hook.chmod(0o755)
    planted_dir = tmp_path / "planted-hooks"  # outside the worktree: config
    planted_dir.mkdir()                       # redirection, not a dirty repo
    planted_hook = planted_dir / "pre-push"
    planted_hook.write_text(hook_body, encoding="utf-8")
    planted_hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(planted_dir))
    _git(repo, "config", "credential.helper", "!false")

    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200, approved.text
    wt = approved.json()["worktree"]
    assert wt["push_status"] == "pushed", wt["push_error"]
    assert not marker.exists()  # no planted hook ever ran
    assert _git(bare, "rev-parse", "main") == wt["merge_commit"]


def test_push_argv_always_carries_the_neutralizing_flags():
    assert repo_remote.PUSH_CONFIG_OVERRIDES == (
        "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
    )
    argv = repo_remote.push_argv("origin", "main")
    assert argv[:4] == list(repo_remote.PUSH_CONFIG_OVERRIDES)
    assert argv[4:] == ["push", "origin", "main"]


def test_graph_plan_approve_pushes_after_merge(tmp_path: Path):
    """Same lifecycle hook on the plan engine's approve door."""
    repo = _scratch_repo(tmp_path / "repo")
    bare = _bare_remote(repo, tmp_path / "bare.git")
    app = _app(tmp_path, feature_workflow_graph=True)
    c = _client(app)
    p = c.post("/api/projects/link", json={"path": str(repo), "slug": "r"})
    assert p.status_code == 201, p.text
    area_id = p.json()["code_areas"][0]["id"]
    assert c.patch(f"/api/projects/r/areas/{area_id}", json={"push_on_merge": True}).status_code == 200
    plan = c.post("/api/graph/jobs", json={
        "title": "Fix",
        "project_slug": "r",
        "graph": {"nodes": [{"id": "fix", "name": "Fix", "instruction": "fix it", "target": "."}]},
    })
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]
    assert c.post(f"/api/graph/jobs/{plan_id}/start").status_code == 200
    wt = c.get(f"/api/graph/jobs/{plan_id}").json()["worktree"]
    Path(wt["worktree_path"], "agent-change.txt").write_text("done\n", encoding="utf-8")
    # Park the plan at its review gate: node done, job in review.
    app.state.db.execute("UPDATE node_states SET status='done' WHERE job_id=?", (plan_id,))
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (plan_id,))

    approved = c.post(f"/api/graph/jobs/{plan_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    wt = approved.json()["worktree"]
    assert wt["status"] == "merged" and wt["push_status"] == "pushed"
    assert _git(bare, "rev-parse", "main") == wt["merge_commit"]
    # The plan-engine job shares the retry door (idempotent re-push: no-op).
    again = c.post(f"/api/jobs/{plan_id}/push")
    assert again.status_code == 200 and again.json()["status"] == "pushed"

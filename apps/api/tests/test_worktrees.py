"""Worktree machinery for repo jobs (Phase-1 slice 2, T1/T5).

Covers the lifecycle module against scratch repos (create/teardown for
root + subfolder repos, dirty-repo refusal, crash-leftover cleanup), the
diff endpoint contract, merge success + conflict paths through approve,
and the flag-off regression: with ``feature_repo_worktrees`` off (the
default) repo-targeted jobs behave exactly as today - including the
worker's cwd selection, proven end-to-end with a real ACP subprocess
that reports its own working directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxima_api import features, runner_specs
from proxima_api.main import create_app
from proxima_api.runner_specs import RunnerSpec
from proxima_api.worktrees import (
    MergeConflictError,
    WorktreeError,
    compute_diff,
    create_worktree,
    job_branch,
    merge_job_branch,
    remove_worktree,
    snapshot_worktree,
)


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert res.returncode == 0, f"git {args}: {res.stderr}"
    return res.stdout.strip()


def _scratch_repo(path: Path) -> Path:
    """A real git repo with one commit (README.md) on branch main."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


# ── lifecycle module (pure git, scratch repos) ───────────────────────────


def test_create_and_remove_worktree_root_repo(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    dest = tmp_path / "ws" / "worktrees" / "job-1"
    info = create_worktree(repo, dest, job_branch(1))
    assert dest.is_dir() and (dest / "README.md").read_text() == "hello\n"
    assert info["base_branch"] == "main"
    assert info["base_commit"] == _git(repo, "rev-parse", "HEAD")
    # The primary tree is untouched: still on main, still clean.
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert _git(repo, "status", "--porcelain") == ""
    assert "proxima/job-1" in _git(repo, "branch", "--list", "proxima/job-1")

    remove_worktree(repo, dest, job_branch(1))
    assert not dest.exists()
    assert _git(repo, "branch", "--list", "proxima/job-1") == ""
    assert _git(repo, "status", "--porcelain") == ""


def test_create_worktree_from_subfolder_repo(tmp_path: Path):
    container = tmp_path / "container"
    (container / "notes").mkdir(parents=True)
    repo = _scratch_repo(container / "app")
    dest = tmp_path / "ws" / "worktrees" / "job-2"
    info = create_worktree(repo, dest, job_branch(2))
    assert (dest / "README.md").is_file()
    assert info["base_branch"] == "main"
    # The worktree lives outside the container, and the container gained no files.
    assert not str(dest).startswith(str(container))
    assert sorted(p.name for p in container.iterdir()) == ["app", "notes"]


def test_create_refuses_dirty_repo(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="uncommitted changes.*README.md"):
        create_worktree(repo, tmp_path / "wt", job_branch(3))
    # Untracked files are in-flight owner work too.
    _git(repo, "checkout", "-q", "--", "README.md")
    (repo / "wip.txt").write_text("draft\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="uncommitted changes.*wip.txt"):
        create_worktree(repo, tmp_path / "wt", job_branch(3))


def test_create_refuses_no_commits_and_detached_head(tmp_path: Path):
    bare = tmp_path / "fresh"
    bare.mkdir()
    _git(bare, "init", "-q", "-b", "main")
    with pytest.raises(WorktreeError, match="no commits"):
        create_worktree(bare, tmp_path / "wt", job_branch(4))

    repo = _scratch_repo(tmp_path / "repo")
    _git(repo, "checkout", "-q", "--detach")
    with pytest.raises(WorktreeError, match="detached HEAD"):
        create_worktree(repo, tmp_path / "wt2", job_branch(4))


def test_create_refuses_non_repo_folder(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError, match="not a git repository"):
        create_worktree(plain, tmp_path / "wt", job_branch(5))


def test_recreate_after_crash_leftovers_is_idempotent(tmp_path: Path):
    """A crashed run can leave the worktree dir + branch behind; cutting the
    same job's worktree again must clean them up and succeed."""
    repo = _scratch_repo(tmp_path / "repo")
    dest = tmp_path / "ws" / "worktrees" / "job-6"
    create_worktree(repo, dest, job_branch(6))
    (dest / "half-done.txt").write_text("crash leftovers\n", encoding="utf-8")

    info = create_worktree(repo, dest, job_branch(6))
    assert info["base_branch"] == "main"
    assert not (dest / "half-done.txt").exists()
    # Cleanup also copes with a manually deleted dir, and is re-runnable.
    remove_worktree(repo, dest, job_branch(6))
    remove_worktree(repo, dest, job_branch(6))
    assert _git(repo, "branch", "--list", "proxima/job-6") == ""


def test_snapshot_and_compute_diff(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    dest = tmp_path / "wt"
    info = create_worktree(repo, dest, job_branch(7))
    (dest / "README.md").write_text("hello\nworld\n", encoding="utf-8")
    (dest / "new.py").write_text("print('hi')\n", encoding="utf-8")
    sha = snapshot_worktree(dest, "checkpoint")
    assert sha != info["base_commit"]
    # No outstanding changes ⇒ no new commit.
    assert snapshot_worktree(dest, "again") == sha

    diff = compute_diff(dest, info["base_commit"], "HEAD")
    assert {(f["path"], f["status"]) for f in diff["files"]} == {("README.md", "M"), ("new.py", "A")}
    assert "+world" in diff["patch"] and "+print('hi')" in diff["patch"]
    assert diff["head_commit"] == sha
    assert not diff["patch_truncated"]
    assert "2 files changed" in diff["summary"]


def test_merge_success_lands_on_base_branch(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    dest = tmp_path / "wt"
    create_worktree(repo, dest, job_branch(8))
    (dest / "feature.txt").write_text("done\n", encoding="utf-8")
    snapshot_worktree(dest, "work")

    sha = merge_job_branch(repo, job_branch(8), "main", "Merge Proxima job #8")
    assert (repo / "feature.txt").read_text() == "done\n"
    assert _git(repo, "rev-parse", "HEAD") == sha
    assert _git(repo, "log", "-1", "--format=%s") == "Merge Proxima job #8"
    assert _git(repo, "status", "--porcelain") == ""


def test_merge_conflict_aborts_cleanly(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    dest = tmp_path / "wt"
    create_worktree(repo, dest, job_branch(9))
    (dest / "README.md").write_text("job version\n", encoding="utf-8")
    snapshot_worktree(dest, "work")
    (repo / "README.md").write_text("owner version\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "owner change")

    with pytest.raises(MergeConflictError, match="README.md") as exc:
        merge_job_branch(repo, job_branch(9), "main", "Merge Proxima job #9")
    assert exc.value.files == ["README.md"]
    # Aborted, not half-merged: clean tree, owner's version intact.
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "README.md").read_text() == "owner version\n"


def test_merge_refuses_dirty_repo_and_switched_branch(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "repo")
    dest = tmp_path / "wt"
    create_worktree(repo, dest, job_branch(10))
    (repo / "README.md").write_text("uncommitted\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="uncommitted changes"):
        merge_job_branch(repo, job_branch(10), "main", "m")
    _git(repo, "checkout", "-q", "--", "README.md")
    _git(repo, "checkout", "-q", "-b", "other")
    with pytest.raises(WorktreeError, match="cut from 'main'"):
        merge_job_branch(repo, job_branch(10), "main", "m")


# ── API contract (flag ON) ───────────────────────────────────────────────


def _app(tmp_path: Path, **config):
    return create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(tmp_path)],
        "start_worker": False,
        **config,
    })


def _client(app) -> TestClient:
    c = TestClient(app)
    tok = c.post("/auth/auto").json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return c


def _repo_job(c: TestClient, slug: str, folder: Path, brief: str = "change the code") -> dict:
    """Link `folder` as a project and create a job targeting its first code area."""
    p = c.post("/api/projects/link", json={"path": str(folder), "slug": slug})
    assert p.status_code == 201, p.text
    area_id = p.json()["code_areas"][0]["id"]
    job = c.post("/api/jobs", json={"project_slug": slug, "input": {"brief": brief}, "target_area_id": area_id})
    assert job.status_code == 200, job.text
    return job.json()


def test_repo_job_start_diff_approve_merge_lifecycle(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)

    started = c.post(f"/api/jobs/{job['id']}/start")
    assert started.status_code == 200, started.text
    wt = started.json()["worktree"]
    assert wt["status"] == "active"
    assert wt["base_branch"] == "main" and wt["base_commit"] == base_sha
    assert wt["branch"] == f"proxima/job-{job['id']}"
    # Placement: inside the app-data workspace, never inside the project.
    assert wt["worktree_path"].startswith(str(tmp_path / "ws"))
    assert not wt["worktree_path"].startswith(str(repo))
    assert Path(wt["worktree_path"]).is_dir()
    # The primary tree is untouched and the worktree is not a new code area.
    assert _git(repo, "status", "--porcelain") == ""
    areas = c.post("/api/projects/myrepo/areas/detect").json()
    assert [a["rel_path"] for a in areas["code_areas"]] == ["."]

    # Simulate the agent working in the worktree (uncommitted, like a real run).
    Path(wt["worktree_path"], "README.md").write_text("hello\npatched\n", encoding="utf-8")
    Path(wt["worktree_path"], "feature.py").write_text("x = 1\n", encoding="utf-8")

    diff = c.get(f"/api/jobs/{job['id']}/diff")
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["job_id"] == job["id"]
    assert body["branch"] == wt["branch"] and body["base_branch"] == "main"
    assert body["base_commit"] == base_sha
    assert {(f["path"], f["status"]) for f in body["files"]} == {("README.md", "M"), ("feature.py", "A")}
    assert "+patched" in body["patch"] and "+x = 1" in body["patch"]

    # Approve = local merge onto main (T1 local-first), then the job closes.
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))
    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    merged = approved.json()["worktree"]
    assert merged["status"] == "merged" and merged["merge_commit"]
    assert (repo / "feature.py").read_text() == "x = 1\n"
    assert (repo / "README.md").read_text() == "hello\npatched\n"
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    # Torn down cleanly: worktree dir + job branch are gone.
    assert not Path(wt["worktree_path"]).exists()
    assert _git(repo, "branch", "--list", wt["branch"]) == ""
    # The diff stays readable off the base branch for the review record.
    after = c.get(f"/api/jobs/{job['id']}/diff").json()
    assert {(f["path"], f["status"]) for f in after["files"]} == {("README.md", "M"), ("feature.py", "A")}


def test_repo_job_worktree_cut_from_subfolder_area(tmp_path: Path):
    container = tmp_path / "container"
    (container / "reports").mkdir(parents=True)
    repo = _scratch_repo(container / "api")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    job = _repo_job(c, "container", container)

    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    wt = c.get(f"/api/jobs/{job['id']}").json()["worktree"]
    # Cut from the subfolder repo, not the container root.
    assert (Path(wt["worktree_path"]) / "README.md").is_file()
    row = app.state.db.execute("SELECT repo_path FROM job_worktrees WHERE job_id=?", (job["id"],)).fetchone()
    assert row["repo_path"] == str(repo)


def test_start_refuses_dirty_target_repo_and_leaves_job_queued(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    (repo / "README.md").write_text("owner wip\n", encoding="utf-8")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)

    res = c.post(f"/api/jobs/{job['id']}/start")
    assert res.status_code == 409
    assert "uncommitted changes" in res.json()["detail"]
    refreshed = c.get(f"/api/jobs/{job['id']}").json()
    assert refreshed["status"] == "queued"
    assert "worktree" not in refreshed
    assert app.state.db.execute("SELECT COUNT(*) FROM job_worktrees").fetchone()[0] == 0

    # Clean retry works: commit the owner's wip and start again.
    _git(repo, "commit", "-qam", "owner wip landed")
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    assert c.get(f"/api/jobs/{job['id']}").json()["worktree"]["status"] == "active"


def test_approve_merge_conflict_parks_job_in_review_then_retry_succeeds(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    wt = c.get(f"/api/jobs/{job['id']}").json()["worktree"]
    Path(wt["worktree_path"], "README.md").write_text("job version\n", encoding="utf-8")
    (repo / "README.md").write_text("owner version\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "owner change")
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))

    res = c.post(f"/api/jobs/{job['id']}/approve")
    assert res.status_code == 409
    assert "merge blocked" in res.json()["detail"] and "README.md" in res.json()["detail"]
    parked = c.get(f"/api/jobs/{job['id']}").json()
    assert parked["status"] == "review"  # parked, not failed, not done
    assert parked["worktree"]["status"] == "conflict"
    assert "README.md" in parked["worktree"]["error"]
    assert Path(wt["worktree_path"]).is_dir()  # kept for resolution
    # Never force: the owner's version is still on main, tree clean.
    assert (repo / "README.md").read_text() == "owner version\n"
    assert _git(repo, "status", "--porcelain") == ""

    # The owner resolves (here: drops their conflicting commit) and re-approves.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    retried = c.post(f"/api/jobs/{job['id']}/approve")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "done"
    assert retried.json()["worktree"]["status"] == "merged"
    assert (repo / "README.md").read_text() == "job version\n"


def test_create_job_validates_target_area(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    other = _scratch_repo(tmp_path / "other")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    assert c.post("/api/projects/link", json={"path": str(repo), "slug": "myrepo"}).status_code == 201
    p2 = c.post("/api/projects/link", json={"path": str(other), "slug": "other"}).json()

    bogus = c.post("/api/jobs", json={"project_slug": "myrepo", "input": {"brief": "x"}, "target_area_id": 99999})
    assert bogus.status_code == 422
    cross = c.post("/api/jobs", json={"project_slug": "myrepo", "input": {"brief": "x"}, "target_area_id": p2["code_areas"][0]["id"]})
    assert cross.status_code == 422
    projectless = c.post("/api/jobs", json={"input": {"brief": "x"}, "target_area_id": p2["code_areas"][0]["id"]})
    assert projectless.status_code == 422


def test_ops_targeted_job_gets_no_worktree_even_with_flag_on(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    p = c.post("/api/projects/link", json={"path": str(repo), "slug": "myrepo"}).json()
    ops_id = p["ops_area"]["id"]
    job = c.post("/api/jobs", json={"project_slug": "myrepo", "input": {"brief": "write a report"}, "target_area_id": ops_id}).json()
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    assert "worktree" not in c.get(f"/api/jobs/{job['id']}").json()
    assert app.state.db.execute("SELECT COUNT(*) FROM job_worktrees").fetchone()[0] == 0


def test_delete_job_tears_down_worktree_and_branch(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    wt = c.get(f"/api/jobs/{job['id']}").json()["worktree"]

    assert c.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert not Path(wt["worktree_path"]).exists()
    assert _git(repo, "branch", "--list", wt["branch"]) == ""
    assert app.state.db.execute("SELECT COUNT(*) FROM job_worktrees").fetchone()[0] == 0  # row died with the job


def test_start_recuts_worktree_when_dir_vanished(tmp_path: Path):
    """Crash-leftover healing keyed by job id: a worktree dir deleted out from
    under an active row is re-cut on the next start, not trusted blindly."""
    import shutil

    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path, feature_repo_worktrees=True)
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)
    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    wt = c.get(f"/api/jobs/{job['id']}").json()["worktree"]
    shutil.rmtree(wt["worktree_path"])
    app.state.db.execute("UPDATE jobs SET status='queued' WHERE id=?", (job["id"],))

    assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
    again = c.get(f"/api/jobs/{job['id']}").json()["worktree"]
    assert again["status"] == "active"
    assert Path(again["worktree_path"]).is_dir()


# ── flag OFF (the default): provably unchanged behavior ──────────────────


def test_flag_off_repo_targeted_job_runs_exactly_as_today(tmp_path: Path):
    repo = _scratch_repo(tmp_path / "myrepo")
    app = _app(tmp_path)  # feature_repo_worktrees defaults to OFF
    c = _client(app)
    job = _repo_job(c, "myrepo", repo)

    started = c.post(f"/api/jobs/{job['id']}/start")
    assert started.status_code == 200
    # No worktree machinery ran: no row, no dir, no branch, payload unchanged.
    assert "worktree" not in started.json()
    assert app.state.db.execute("SELECT COUNT(*) FROM job_worktrees").fetchone()[0] == 0
    assert not (tmp_path / "ws" / "worktrees").exists()
    assert _git(repo, "branch", "--list", "proxima/*") == ""
    assert _git(repo, "status", "--porcelain") == ""

    # The diff endpoint is feature-gated like every disabled feature.
    diff = c.get(f"/api/jobs/{job['id']}/diff")
    assert diff.status_code == 503
    assert diff.json() == {"detail": features.disabled_payload(features.REPO_WORKTREES)}

    # Approve runs the classic path: review -> done, repo untouched.
    app.state.db.execute("UPDATE jobs SET status='review' WHERE id=?", (job["id"],))
    approved = c.post(f"/api/jobs/{job['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "done"
    assert _git(repo, "log", "--oneline").count("\n") == 0  # still just the init commit


# ── worker cwd seam, end-to-end (real ACP subprocess reports its cwd) ────

# Minimal ACP agent (same JSON-RPC skeleton as test_worker_integration) whose
# one message chunk reports the subprocess's real working directory - the
# ground truth for where a run actually executes.
FAKE_CWD_ACP_SCRIPT = '''\
import sys, json, os
SID = "fake-session-1"
def send(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        continue
    mid = m.get("id"); method = m.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":1,
              "serverInfo":{"name":"fake-acp","version":"0"},"capabilities":{}}})
    elif method == "session/new":
        send({"jsonrpc":"2.0","id":mid,"result":{"sessionId":SID}})
    elif method == "session/load":
        send({"jsonrpc":"2.0","id":mid,"result":{}})
    elif method == "session/prompt":
        send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":SID,
              "update":{"sessionUpdate":"agent_message_chunk",
                        "content":{"type":"text","text":"ran-in:" + os.getcwd()}}}})
        send({"jsonrpc":"2.0","id":mid,"result":{"stopReason":"end_turn"}})
    else:
        if mid is not None:
            send({"jsonrpc":"2.0","id":mid,"result":{}})
'''


def _install_fake_runner(script: Path):
    saved_env = os.environ.get("PROXIMA_DEFAULT_RUNNER")
    saved_spec = runner_specs.RUNNER_SPECS.get("fake-acp")
    runner_specs.RUNNER_SPECS["fake-acp"] = RunnerSpec(
        id="fake-acp",
        spawn_argv=[sys.executable, str(script)],
        home_env="FAKE_ACP_HOME",
        binary="python",
        display_name="Fake ACP",
        has_adapter=True,
        detection_only=False,
        source_dir="",
        seed_files=(),
        refresh_files=(),
    )
    os.environ["PROXIMA_DEFAULT_RUNNER"] = "fake-acp"
    return saved_env, saved_spec


def _restore_fake_runner(saved_env, saved_spec):
    if saved_env is None:
        os.environ.pop("PROXIMA_DEFAULT_RUNNER", None)
    else:
        os.environ["PROXIMA_DEFAULT_RUNNER"] = saved_env
    if saved_spec is None:
        runner_specs.RUNNER_SPECS.pop("fake-acp", None)
    else:
        runner_specs.RUNNER_SPECS["fake-acp"] = saved_spec


def _run_repo_job_and_capture_cwd(tmp_path: Path, repo_worktrees: bool) -> tuple[str, dict, Path]:
    """Drive one repo-targeted job through the LIVE worker + a real ACP
    subprocess; return (the cwd the agent actually ran in, the job payload,
    the repo path)."""
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE_CWD_ACP_SCRIPT)
    repo = _scratch_repo(tmp_path / "myrepo")
    saved = _install_fake_runner(script)
    try:
        app = _app(
            tmp_path,
            start_worker=True,
            start_scheduler=False,
            run_worker_poll_interval_ms=20,
            feature_repo_worktrees=repo_worktrees,
        )
        with TestClient(app) as c:
            tok = c.post("/auth/auto").json()["token"]
            c.headers.update({"Authorization": f"Bearer {tok}"})
            job = _repo_job(c, "myrepo", repo)
            assert c.post(f"/api/jobs/{job['id']}/start").status_code == 200
            deadline = time.time() + 20
            status = "running"
            while time.time() < deadline:
                status = c.get(f"/api/jobs/{job['id']}").json()["status"]
                if status in ("review", "done", "failed"):
                    break
                time.sleep(0.05)
            assert status == "review", f"job did not reach review: {status}"
            msgs = c.get(f"/api/sessions/{job['session_id']}/messages").json()["messages"]
            reported = next(m["content"] for m in msgs if "ran-in:" in (m.get("content") or ""))
            payload = c.get(f"/api/jobs/{job['id']}").json()
            return reported.split("ran-in:", 1)[1].strip(), payload, repo
    finally:
        _restore_fake_runner(*saved)


def test_flag_on_run_executes_inside_the_worktree(tmp_path: Path):
    cwd, payload, repo = _run_repo_job_and_capture_cwd(tmp_path, repo_worktrees=True)
    wt_path = str(Path(payload["worktree"]["worktree_path"]).resolve())
    assert str(Path(cwd).resolve()) == wt_path
    assert not cwd.startswith(str(repo))


def test_flag_off_run_executes_in_the_project_path_as_today(tmp_path: Path):
    cwd, payload, repo = _run_repo_job_and_capture_cwd(tmp_path, repo_worktrees=False)
    assert str(Path(cwd).resolve()) == str(repo.resolve())
    assert "worktree" not in payload


def test_graph_repo_node_runs_in_worktree_and_ops_node_in_project(tmp_path: Path):
    """Slice 3's per-job binding, end-to-end on the LIVE worker: in one plan,
    the touches-repo node executes inside the plan's isolated worktree while
    its ops sibling executes at the project root (where its outputs belong).
    The fake ACP agent reports its real cwd as each node's output."""
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE_CWD_ACP_SCRIPT)
    repo = _scratch_repo(tmp_path / "myrepo")
    saved = _install_fake_runner(script)
    try:
        app = _app(
            tmp_path,
            start_worker=True,
            start_scheduler=False,
            run_worker_poll_interval_ms=20,
            feature_repo_worktrees=True,
            feature_workflow_graph=True,
        )
        with TestClient(app) as c:
            tok = c.post("/auth/auto").json()["token"]
            c.headers.update({"Authorization": f"Bearer {tok}"})
            p = c.post("/api/projects/link", json={"path": str(repo), "slug": "myrepo"})
            assert p.status_code == 201, p.text
            plan = c.post("/api/graph/jobs", json={
                "title": "Fix + report",
                "project_slug": "myrepo",
                "graph": {"nodes": [
                    {"id": "fix", "name": "Fix", "instruction": "fix it", "target": "."},
                    {"id": "report", "name": "Report", "instruction": "report it",
                     "target": "ops", "depends_on": ["fix"]},
                ]},
            })
            assert plan.status_code == 201, plan.text
            plan_id = plan.json()["id"]
            assert c.post(f"/api/graph/jobs/{plan_id}/start").status_code == 200
            deadline = time.time() + 20
            status = "running"
            while time.time() < deadline:
                status = c.get(f"/api/graph/jobs/{plan_id}").json()["status"]
                if status in ("review", "done", "failed"):
                    break
                time.sleep(0.05)
            assert status == "review", f"plan did not reach review: {status}"
            payload = c.get(f"/api/graph/jobs/{plan_id}").json()
            states = {n["node_id"]: n for n in payload["node_states"]}
            fix_cwd = str(states["fix"]["output"]).split("ran-in:", 1)[1].strip()
            report_cwd = str(states["report"]["output"]).split("ran-in:", 1)[1].strip()
            wt_path = str(Path(payload["worktree"]["worktree_path"]).resolve())
            assert str(Path(fix_cwd).resolve()) == wt_path
            assert str(Path(report_cwd).resolve()) == str(repo.resolve())
    finally:
        _restore_fake_runner(*saved)

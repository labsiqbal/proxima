"""Isolated git worktrees for repo jobs (Phase-1 slice 2, tickets T1/T5).

A **repo job** - a job whose ``target_area_id`` points at one of its project's
*code areas* (slice 1's container model) - never edits the primary tree.
Proxima cuts a dedicated worktree + branch from the code area's repo, the
agent works there, the owner reviews the before/after diff in-app, and on
approve Proxima merges the job branch back into the branch it was cut from -
**locally**, no remote involved (T1's local-first decision; push is T9).

Worktrees live OUTSIDE the project container, under the app-data workspace
(``<workspace_root>/worktrees/job-<id>``), deliberately:

- The container is scanned surface: artifact scanning and code-area detection
  must never see agent work-in-progress, and a worktree carries a ``.git``
  file so nesting it in the container would make it register as a code area
  of its own.
- The container may itself be the repo (rel_path ``.``); a worktree nested
  inside would show up in the owner's own ``git status``.
- Teardown is one directory keyed by job id, so leftovers from a crashed run
  are removable without touching the container (``remove_worktree`` is
  idempotent and safe to call on any partial state).

The worktree's branch history may carry "work snapshot" checkpoint commits:
diff and merge both operate on commits, so outstanding file edits are
committed (as Proxima, hooks/signing bypassed - checkpoints are internal
bookkeeping, and a failing owner hook would wedge the review flow with a
confusing error) before either. Partial work therefore also survives crashes
and future continuation turns (T5).

Everything here is gated behind ``feature_repo_worktrees`` (on by default
since slice 4 shipped the review UI; the flag stays as an escape hatch); with
the flag off no caller invokes this module and job execution is unchanged.
State lives in the ``job_worktrees`` table, one row
per job: status ``active`` (agent may work) -> ``merging`` (merge claimed) ->
``merged`` (landed on the base branch, worktree torn down) with ``conflict``
(merge refused or conflicted; job parks in review, worktree kept for
resolution) and ``discarded`` (torn down without merging) as the off-ramps.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT_SECONDS = 120
# Unified diffs can be huge; the review payload caps the patch so one giant
# vendored-file change cannot balloon an API response. File statuses are
# always complete - only the patch text truncates.
MAX_PATCH_BYTES = 1_000_000
# Commits Proxima itself makes (snapshots, merges) in repos that may have no
# git identity configured; signing off for determinism.
_GIT_IDENTITY = (
    "-c", "user.name=Proxima",
    "-c", "user.email=proxima@localhost",
    "-c", "commit.gpgsign=false",
)
# Runtime cache/bytecode agents often create while running code. These must
# never enter a review snapshot or pollute the owner's base branch on merge -
# even when the target repo has no .gitignore. Keep this list to clear junk
# only (not intentional build outputs like dist/).
_SNAPSHOT_NOISE_DIR_NAMES = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".eggs",
})
_SNAPSHOT_NOISE_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
_SNAPSHOT_NOISE_SUFFIXES = (".pyc", ".pyo", ".pyd")


class WorktreeError(RuntimeError):
    """A refused or failed worktree operation; the message is owner-facing."""


class MergeConflictError(WorktreeError):
    def __init__(self, message: str, files: list[str]):
        super().__init__(message)
        self.files = files


# ── git plumbing ─────────────────────────────────────────────────────────


def _git(cwd: Path | str, *args: str, check: bool = True, identity: bool = False) -> subprocess.CompletedProcess:
    # Scrub inherited GIT_* (GIT_DIR/GIT_INDEX_FILE would redirect commands at
    # the wrong repo) and never allow an interactive credential prompt.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    argv = ["git", *(_GIT_IDENTITY if identity else ()), *args]
    try:
        res = subprocess.run(
            argv, cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise WorktreeError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git {args[0]} timed out after {GIT_TIMEOUT_SECONDS}s") from exc
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()[-500:]
        raise WorktreeError(f"git {args[0]} failed: {detail}")
    return res


def _porcelain(repo: Path | str) -> list[str]:
    out = _git(repo, "status", "--porcelain").stdout
    return [line for line in out.splitlines() if line.strip()]


def require_clean_repo(repo: Path | str, doing: str) -> None:
    """Refuse loudly on ANY uncommitted state - tracked edits or untracked
    files are both in-flight owner work that a cut/merge must not race."""
    lines = _porcelain(repo)
    if lines:
        paths = ", ".join(line[3:] for line in lines[:5])
        more = f" (+{len(lines) - 5} more)" if len(lines) > 5 else ""
        raise WorktreeError(
            f"cannot {doing}: the repo has uncommitted changes ({paths}{more}) - commit or stash them first"
        )


def current_branch(repo: Path | str) -> str:
    res = _git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    name = res.stdout.strip()
    if res.returncode != 0 or not name:
        raise WorktreeError("the repo is on a detached HEAD - check out a branch first so the job has a merge target")
    return name


def job_branch(job_id: int) -> str:
    return f"proxima/job-{job_id}"


def worktrees_root(cfg: dict[str, Any]) -> Path:
    return Path(cfg["workspace_root"]) / "worktrees"


def create_worktree(repo: Path, dest: Path, branch: str) -> dict[str, str]:
    """Cut ``branch`` at the repo's current HEAD into a worktree at ``dest``.

    Refuses loudly on: not a repo, no commits yet, detached HEAD, dirty repo.
    Leftovers from a crashed prior attempt (same dest/branch) are cleaned
    first, so re-cutting a job's worktree is idempotent.
    """
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise WorktreeError(f"target code area is not a git repository: {repo}")
    head = _git(repo, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode != 0:
        raise WorktreeError("the repo has no commits yet - make an initial commit first")
    base_branch = current_branch(repo)
    require_clean_repo(repo, "cut a job worktree")
    remove_worktree(repo, dest, branch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(dest), "HEAD")
    return {"base_branch": base_branch, "base_commit": head.stdout.strip()}


def is_snapshot_noise(path: str | None) -> bool:
    """True for runtime cache/bytecode paths that must not enter a review commit."""
    if not path:
        return False
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    name = normalized.rsplit("/", 1)[-1]
    if name in _SNAPSHOT_NOISE_FILE_NAMES:
        return True
    if name.endswith(_SNAPSHOT_NOISE_SUFFIXES):
        return True
    return any(part in _SNAPSHOT_NOISE_DIR_NAMES for part in normalized.split("/"))


def _staged_paths(wt: Path | str) -> list[str]:
    out = _git(wt, "diff", "--cached", "--name-only", "-z", check=False).stdout
    return [p for p in out.split("\0") if p]


def _unstage_snapshot_noise(wt: Path | str) -> None:
    """Drop cache/bytecode from the index after ``git add -A``.

    Untracked noise becomes untracked again; already-tracked noise stays
    tracked but its staged edits are left out of the checkpoint commit so a
    recompiled ``.pyc`` cannot keep polluting the job branch.
    """
    noise = [p for p in _staged_paths(wt) if is_snapshot_noise(p)]
    if not noise:
        return
    # Batch to stay well under OS arg limits on huge trees.
    step = 100
    for i in range(0, len(noise), step):
        _git(wt, "reset", "-q", "HEAD", "--", *noise[i:i + step], check=False)


def _filter_noise_from_patch(patch: str) -> str:
    """Strip unified-diff file sections whose path is snapshot noise."""
    if not patch:
        return patch
    kept: list[str] = []
    section: list[str] = []
    section_noise = False

    def flush() -> None:
        nonlocal section, section_noise
        if section and not section_noise:
            kept.extend(section)
        section = []
        section_noise = False

    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            flush()
            section = [line]
            # "diff --git a/path b/path" - prefer the b/ side (new path).
            parts = line.rstrip("\n").split(" ")
            b_path = ""
            for part in parts:
                if part.startswith("b/"):
                    b_path = part[2:]
            section_noise = is_snapshot_noise(b_path)
            continue
        if section:
            section.append(line)
        else:
            # Preamble before the first file section (rare for git diff).
            kept.append(line)
    flush()
    return "".join(kept)


def _diff_summary(files: list[dict[str, Any]]) -> str:
    """Owner-facing shortstat that matches the filtered file list."""
    n = len(files)
    if n == 0:
        return ""
    return f"{n} file{'s' if n != 1 else ''} changed"


def snapshot_worktree(wt: Path | str, message: str) -> str:
    """Commit any outstanding work in the worktree; return the head sha.

    Diff and merge both deal in commits, so this runs before either - and it
    is what makes partial agent work durable (T5). No outstanding changes ⇒
    no commit. Runtime cache/bytecode is staged then dropped so a missing
    .gitignore cannot smuggle ``__pycache__`` into the review or merge.
    """
    if _porcelain(wt):
        _git(wt, "add", "-A")
        _unstage_snapshot_noise(wt)
        if _staged_paths(wt):
            commit = _git(wt, "commit", "--no-verify", "-m", message, check=False, identity=True)
            if commit.returncode != 0 and _staged_paths(wt):
                detail = (commit.stderr or commit.stdout or "").strip()[-500:]
                raise WorktreeError(f"could not snapshot worktree changes: {detail}")
    return _git(wt, "rev-parse", "HEAD").stdout.strip()


def compute_diff(cwd: Path | str, base: str, head: str = "HEAD") -> dict[str, Any]:
    """The job's before/after change, in the shape the review UI renders:
    per-file status (rename-aware) plus one unified patch.

    Cache/bytecode paths are omitted even when an older snapshot already
    committed them, so the review surface stays scannable.
    """
    files: list[dict[str, Any]] = []
    raw_file_count = 0
    tokens = _git(cwd, "diff", "--name-status", "-z", "-M", base, head).stdout.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status[0] in ("R", "C"):
            path, old_path = tokens[i + 2], tokens[i + 1]
            i += 3
        else:
            path, old_path = tokens[i + 1], None
            i += 2
        raw_file_count += 1
        if is_snapshot_noise(path) or is_snapshot_noise(old_path):
            continue
        files.append({"path": path, "old_path": old_path, "status": status[0]})
    patch = _filter_noise_from_patch(_git(cwd, "diff", "-M", "--no-color", base, head).stdout)
    truncated = len(patch.encode("utf-8", errors="replace")) > MAX_PATCH_BYTES
    if truncated:
        patch = patch.encode("utf-8", errors="replace")[:MAX_PATCH_BYTES].decode("utf-8", errors="replace")
    # Keep git's insert/delete shortstat when nothing was filtered; otherwise the
    # counts would still include hidden bytecode and mislead the owner.
    if raw_file_count == len(files):
        summary = _git(cwd, "diff", "--shortstat", "-M", base, head).stdout.strip()
    else:
        summary = _diff_summary(files)
    return {
        "base_commit": base,
        "head_commit": _git(cwd, "rev-parse", head).stdout.strip(),
        "files": files,
        "patch": patch,
        "patch_truncated": truncated,
        "summary": summary,
    }


def work_signature(wt: Path | str) -> str:
    """Cheap durable fingerprint of a worktree's work state, for the satpam's
    stall check (slice 12, T10): the branch head, the uncommitted status, the
    tracked content changes, and each untracked file's size+mtime. Read-only -
    three git calls plus stats, no snapshot commit - so the supervision loop
    can take it every sweep without touching the agent's work. Untracked files
    use stat rather than content on purpose: the bias is toward missing a
    stall over ever flagging a healthy, progressing job."""
    head = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
    status = _git(wt, "status", "--porcelain", "-uall", check=False).stdout
    tracked_diff = _git(wt, "diff", "HEAD", "--no-color", check=False).stdout
    untracked: list[str] = []
    for line in status.splitlines():
        if not line.startswith("?? "):
            continue
        target = Path(wt) / line[3:]
        try:
            st = target.stat()
            untracked.append(f"{line[3:]}\0{st.st_size}\0{st.st_mtime_ns}")
        except OSError:
            untracked.append(f"{line[3:]}\0gone")
    material = "\n".join([head, status, tracked_diff, *untracked])
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def fresh_signature(base_commit: str) -> str:
    """The signature a just-cut worktree has: at its base commit, nothing
    uncommitted, nothing untracked. The satpam's implicit baseline for a
    chain's first evaluation - a first turn that leaves the worktree in this
    state did no repo work at all."""
    return hashlib.sha256("\n".join([base_commit, "", ""]).encode("utf-8", errors="replace")).hexdigest()


def merge_job_branch(repo: Path | str, branch: str, base_branch: str, message: str) -> str:
    """Merge the job branch into the branch it was cut from, locally. Guarded:
    refuses a dirty repo or a switched-away base branch, aborts + surfaces
    conflicts, never forces. Returns the resulting head sha."""
    require_clean_repo(repo, "merge the job branch")
    on = current_branch(repo)
    if on != base_branch:
        raise WorktreeError(
            f"the repo is now on branch '{on}' but the job was cut from '{base_branch}' - check out '{base_branch}' to merge"
        )
    # --no-ff keeps an explicit merge commit recording the job on the main line.
    res = _git(repo, "merge", "--no-ff", "--no-verify", "-m", message, branch, check=False, identity=True)
    if res.returncode != 0:
        conflicts = [
            line for line in _git(repo, "diff", "--name-only", "--diff-filter=U", check=False).stdout.splitlines() if line
        ]
        _git(repo, "merge", "--abort", check=False)
        if conflicts:
            raise MergeConflictError(
                f"merge conflicts with the current {base_branch}: {', '.join(conflicts[:10])}", conflicts
            )
        detail = (res.stderr or res.stdout or "").strip()[-500:]
        raise WorktreeError(f"merge failed: {detail}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def remove_worktree(repo: Path | str | None, dest: Path | str, branch: str) -> None:
    """Tear down a job worktree + branch. Idempotent by design: safe on a
    half-created worktree, an already-removed dir, or a deleted repo - this is
    the crash-leftover cleanup path, keyed by the job's dest/branch."""
    dest = Path(dest)
    repo_ok = repo is not None and (Path(repo) / ".git").exists()
    if repo_ok:
        _git(repo, "worktree", "remove", "--force", str(dest), check=False)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    if repo_ok:
        _git(repo, "worktree", "prune", check=False)
        _git(repo, "branch", "-D", branch, check=False)


# ── job-level orchestration (DB glue) ────────────────────────────────────


def job_worktree_row(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM job_worktrees WHERE job_id = ?", (job_id,)).fetchone()


def repo_area_for_job(conn: sqlite3.Connection, job: sqlite3.Row | dict[str, Any]) -> tuple[sqlite3.Row, Path] | None:
    """The job's target CODE area and its absolute repo dir - or None when the
    job is not a repo job (no target, ops target, or excluded area). This is
    the single touches-repo test (T1: one target ⇒ one clean yes/no)."""
    if not job["target_area_id"] or not job["project_id"]:
        return None
    area = conn.execute(
        "SELECT * FROM project_areas WHERE id = ? AND project_id = ?",
        (job["target_area_id"], job["project_id"]),
    ).fetchone()
    if not area or area["kind"] != "code" or area["source"] == "excluded":
        return None
    project = conn.execute("SELECT path FROM projects WHERE id = ?", (job["project_id"],)).fetchone()
    if not project:
        return None
    root = Path(project["path"])
    return area, (root if area["rel_path"] == "." else root / area["rel_path"])


def bind_graph_job_repo_worktree(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    job: sqlite3.Row | dict[str, Any],
) -> sqlite3.Row | None:
    """Pin ``target_area_id`` and cut the isolated worktree for a graph job.

    Shared by ``POST /api/graph/jobs/{id}/start`` and the scheduler's graph
    spawn so a cron / Run-now recipe cannot drift from a manual start and write
    into the live code area. No-op when ``feature_repo_worktrees`` is off or the
    graph has no repo targets. Returns the ``job_worktrees`` row when one was
    cut, else None.

    Raises ``WorktreeError`` with an owner-facing message when the plan cannot
    start safely (unresolved area, multi-area graph, missing area, dirty repo).
    """
    from . import features
    from .graph import normalize_graph, repo_target_paths, unresolved_target_questions

    if not features.enabled(cfg, features.REPO_WORKTREES):
        return None
    job_id = int(job["id"])
    graph = normalize_graph(job["graph"] or "")
    # Ambiguous targets block start even when the plan has no project yet -
    # the owner must pick a work area before anything dispatches (T1). Checking
    # before the project_id early-return keeps a project-less ambiguous plan
    # from silently starting as a plain ops graph.
    questions = unresolved_target_questions(graph)
    if questions:
        raise WorktreeError(
            "this plan has an unresolved question - pick a work area first: "
            + "; ".join(questions)
        )
    project_id = job["project_id"]
    if not project_id:
        return None
    repo_targets = repo_target_paths(graph)
    if not repo_targets:
        return None
    if len(repo_targets) > 1:
        raise WorktreeError(
            "this plan's repo jobs target more than one code area "
            f"({', '.join(repo_targets)}) - a plan works one code area; "
            "split the others into their own plan"
        )
    area = conn.execute(
        "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'code' "
        "AND rel_path = ? AND source != 'excluded'",
        (project_id, repo_targets[0]),
    ).fetchone()
    if not area:
        raise WorktreeError(
            f"code area '{repo_targets[0]}' is no longer registered in this project"
        )
    # Pin the plan's one repo area on the job row: that is what the whole
    # slice-2 surface (worktree cut, diff, merge, teardown) keys off.
    conn.execute(
        "UPDATE jobs SET target_area_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (int(area["id"]), job_id),
    )
    refreshed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if refreshed is None:
        raise WorktreeError("job disappeared while binding its worktree")
    return ensure_job_worktree(conn, cfg, refreshed)


def ensure_job_worktree(conn: sqlite3.Connection, cfg: dict[str, Any], job: sqlite3.Row | dict[str, Any]) -> sqlite3.Row | None:
    """Get-or-create the job's isolated worktree. Returns its row, or None for
    a non-repo job. Raises WorktreeError with an owner-facing reason when the
    cut is refused (dirty repo, detached HEAD, no commits, missing folder)."""
    resolved = repo_area_for_job(conn, job)
    if resolved is None:
        return None
    area, repo = resolved
    job_id = int(job["id"])
    existing = job_worktree_row(conn, job_id)
    if existing and existing["status"] in ("merging", "merged", "discarded"):
        return existing
    if existing and Path(existing["worktree_path"]).is_dir():
        return existing
    # No worktree yet, or its directory vanished (crash / manual cleanup):
    # (re-)cut it. create_worktree clears any leftover registration first.
    dest = worktrees_root(cfg) / f"job-{job_id}"
    info = create_worktree(repo, dest, job_branch(job_id))
    conn.execute(
        """
        INSERT INTO job_worktrees(job_id, area_id, repo_path, worktree_path, branch, base_branch, base_commit, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(job_id) DO UPDATE SET
          area_id = excluded.area_id, repo_path = excluded.repo_path,
          worktree_path = excluded.worktree_path, branch = excluded.branch,
          base_branch = excluded.base_branch, base_commit = excluded.base_commit,
          status = 'active', merge_commit = NULL, error = NULL, updated_at = CURRENT_TIMESTAMP
        """,
        (job_id, area["id"], str(repo), str(dest), job_branch(job_id), info["base_branch"], info["base_commit"]),
    )
    return job_worktree_row(conn, job_id)


def job_diff(wt: sqlite3.Row) -> dict[str, Any]:
    """The job's current before/after diff. For a live worktree, outstanding
    edits are snapshotted first so the diff always reflects what would merge;
    after a merge the same change is read off the base branch."""
    if wt["status"] == "merged":
        return compute_diff(Path(wt["repo_path"]), wt["base_commit"], wt["merge_commit"])
    wt_path = Path(wt["worktree_path"])
    if not wt_path.is_dir():
        raise WorktreeError("the job worktree is missing on disk - restart the job to re-cut it, or delete the job")
    snapshot_worktree(wt_path, f"proxima: job #{wt['job_id']} work snapshot")
    return compute_diff(wt_path, wt["base_commit"], "HEAD")


def merge_job_worktree(conn: sqlite3.Connection, job: sqlite3.Row | dict[str, Any], wt: sqlite3.Row) -> sqlite3.Row:
    """The approve-time merge: snapshot outstanding work, merge the job branch
    into its base branch, record the result, tear the worktree down. On any
    refusal/conflict the row parks as 'conflict' (with the reason) and the
    caller keeps the job in review - never forced, never silent."""
    claimed = conn.execute(
        # The status row is the merge mutex. A stale 'merging' (a crash mid-
        # merge) becomes reclaimable after 10 minutes so a job can't wedge.
        "UPDATE job_worktrees SET status = 'merging', updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND (status IN ('active', 'conflict') "
        "OR (status = 'merging' AND updated_at <= datetime('now', '-10 minutes')))",
        (wt["id"],),
    )
    if claimed.rowcount == 0:
        raise WorktreeError("a merge for this job is already in progress")
    job_id = int(job["id"])
    try:
        wt_path = Path(wt["worktree_path"])
        if not wt_path.is_dir():
            raise WorktreeError("the job worktree is missing on disk - restart the job to re-cut it, or delete the job")
        snapshot_worktree(wt_path, f"proxima: job #{job_id} final work snapshot")
        title = (job["title"] or "").strip()
        merge_sha = merge_job_branch(
            Path(wt["repo_path"]), wt["branch"], wt["base_branch"],
            f"Merge Proxima job #{job_id}" + (f": {title}" if title else ""),
        )
    except WorktreeError as exc:
        conn.execute(
            "UPDATE job_worktrees SET status = 'conflict', error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(exc), wt["id"]),
        )
        raise
    remove_worktree(wt["repo_path"], wt["worktree_path"], wt["branch"])
    conn.execute(
        "UPDATE job_worktrees SET status = 'merged', merge_commit = ?, error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (merge_sha, wt["id"]),
    )
    refreshed = job_worktree_row(conn, job_id)
    assert refreshed is not None
    return refreshed


def discard_job_worktree(conn: sqlite3.Connection, job_id: int) -> None:
    """Tear down without merging (job deleted, or a future explicit discard).
    Idempotent; a merged/absent worktree is a no-op."""
    wt = job_worktree_row(conn, job_id)
    if not wt:
        return
    if wt["status"] not in ("merged", "discarded"):
        remove_worktree(wt["repo_path"], wt["worktree_path"], wt["branch"])
        conn.execute(
            "UPDATE job_worktrees SET status = 'discarded', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (wt["id"],),
        )


def recut_job_worktree(conn: sqlite3.Connection, cfg: dict[str, Any], job: sqlite3.Row | dict[str, Any]) -> sqlite3.Row | None:
    """Discard the job's worktree and cut a FRESH one from the repo's current
    HEAD - the satpam's approved restart-clean (slice 12, T10). Unlike
    ``ensure_job_worktree`` this deliberately does not reuse a discarded row:
    restart-clean means the agent's uncommitted/unmerged work is gone and the
    job re-runs from a clean base. Raises WorktreeError (dirty repo, detached
    HEAD, ...) with an owner-facing reason when the fresh cut is refused."""
    resolved = repo_area_for_job(conn, job)
    if resolved is None:
        return None
    area, repo = resolved
    job_id = int(job["id"])
    discard_job_worktree(conn, job_id)
    dest = worktrees_root(cfg) / f"job-{job_id}"
    info = create_worktree(repo, dest, job_branch(job_id))
    conn.execute(
        """
        INSERT INTO job_worktrees(job_id, area_id, repo_path, worktree_path, branch, base_branch, base_commit, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(job_id) DO UPDATE SET
          area_id = excluded.area_id, repo_path = excluded.repo_path,
          worktree_path = excluded.worktree_path, branch = excluded.branch,
          base_branch = excluded.base_branch, base_commit = excluded.base_commit,
          status = 'active', merge_commit = NULL, error = NULL,
          push_status = NULL, push_error = NULL, push_remote = NULL, push_remote_url = NULL,
          updated_at = CURRENT_TIMESTAMP
        """,
        (job_id, area["id"], str(repo), str(dest), job_branch(job_id), info["base_branch"], info["base_commit"]),
    )
    return job_worktree_row(conn, job_id)


def worktree_payload(wt: sqlite3.Row) -> dict[str, Any]:
    """The worktree state the job payload carries for the review UI. The
    push_* fields are the T9 push-after-merge outcome (NULL push_status until
    a push is attempted); push_web_url is the GitHub enrichment, parsed from
    the recorded remote URL so list payloads never shell out."""
    from .repo_remote import github_web_url

    return {
        "area_id": wt["area_id"],
        "branch": wt["branch"],
        "base_branch": wt["base_branch"],
        "base_commit": wt["base_commit"],
        "status": wt["status"],
        "merge_commit": wt["merge_commit"],
        "error": wt["error"],
        "worktree_path": wt["worktree_path"],
        "push_status": wt["push_status"],
        "push_error": wt["push_error"],
        "push_remote": wt["push_remote"],
        "push_web_url": github_web_url(wt["push_remote_url"]) if wt["push_remote_url"] else None,
    }

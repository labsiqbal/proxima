"""BYO repo-remote connector (Phase-1 slice 11, T9).

Graduates the local-first merge (slice 2/4): a code area with a detected git
remote can opt into **push after merge** - when a repo job's local merge
succeeds, Proxima pushes the merged main line to the remote. Everything here
is BYO to the letter (standing decision #9): all remote operations shell out
to the host's own ``git`` (and ``gh`` when present, for GitHub enrichment
only). Proxima never brokers auth, stores no tokens, ships no OAuth flow -
if ``git push`` cannot authenticate, the concrete failing command + its
output surface as the blocker, never an in-app credential prompt.

The contract, all four T9 points binding:

- **Per-area opt-in, auto-offered:** a code area with a detected remote gets
  a ``push_on_merge`` toggle (``project_areas.push_on_merge``), DEFAULT OFF.
  No remote detected -> no toggle offered. Local-only stays the T1 default.
- **Lifecycle placement:** push happens AFTER the local approve+merge, from
  the approve paths (work.py / graph.py). The review surface stays singular
  in-app; PR-as-review-surface is out of Phase 1.
- **Failure semantics:** a failed push (diverged remote, auth expiry,
  network) NEVER un-merges local work. The job stays done-and-merged; the
  failure is recorded on the job's worktree row (``push_status='failed'`` +
  the exact command and output in ``push_error``) and surfaces as a
  job-level blocker card with a retry action (``POST /api/jobs/{id}/push``).
- **GitHub-first, not GitHub-only:** plain ``git push`` covers any remote
  (GitLab, self-hosted, a bare path). When the remote is GitHub, the
  surfaced info is enriched with the repo web link (parsed from the remote
  URL - pure string work) and, when the host's ``gh`` is authenticated,
  that fact; there is no hard ``gh`` dependency anywhere.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

# Pushes cross the network; give them the same generous budget as the rest
# of the git plumbing (worktrees.GIT_TIMEOUT_SECONDS) rather than a new knob.
from .worktrees import GIT_TIMEOUT_SECONDS

# ``gh auth status`` shells out and may probe the network; cache the answer
# briefly so the areas payload (a settings read) stays snappy.
_GH_CACHE_TTL_SECONDS = 60.0
_gh_cache: tuple[float, bool] | None = None

_GITHUB_URL_RES = (
    # https://github.com/owner/repo(.git), with optional user@ and trailing /
    re.compile(r"^https?://(?:[^@/]+@)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    # git@github.com:owner/repo(.git) (scp-like) and ssh://git@github.com/owner/repo(.git)
    re.compile(r"^(?:ssh://)?git@github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
)


def _git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess:
    """Run the host's own git, never interactively. Same env hygiene as
    worktrees._git (scrub inherited GIT_*, no terminal prompt) plus ssh batch
    mode, so an unauthenticated push FAILS with a message instead of hanging
    on a prompt - the BYO stance: surface the blocker, never ask in-app."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def github_web_url(remote_url: str) -> str | None:
    """The repo's web link for a GitHub remote URL, else None. Parsed, not
    fetched - enrichment must not need auth or the network."""
    for pattern in _GITHUB_URL_RES:
        m = pattern.match(remote_url.strip())
        if m:
            return f"https://github.com/{m.group('owner')}/{m.group('repo')}"
    return None


def gh_authenticated() -> bool:
    """Whether the host's own ``gh`` exists and is logged in (cached). Purely
    additive enrichment - nothing depends on a yes."""
    global _gh_cache
    now = time.monotonic()
    if _gh_cache and now - _gh_cache[0] < _GH_CACHE_TTL_SECONDS:
        return _gh_cache[1]
    ok = False
    if shutil.which("gh"):
        try:
            ok = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, timeout=15
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    _gh_cache = (now, ok)
    return ok


def detect_remote(repo: Path | str) -> dict[str, Any] | None:
    """The code area's push target, read off its own repo config: prefer
    ``origin``, else the first configured remote; None when the repo has no
    remote (or is not a repo) - and then no toggle is ever offered."""
    try:
        listed = _git(repo, "remote")
    except (OSError, subprocess.TimeoutExpired):
        return None
    names = [n for n in listed.stdout.splitlines() if n.strip()]
    if listed.returncode != 0 or not names:
        return None
    name = "origin" if "origin" in names else names[0]
    url_res = _git(repo, "remote", "get-url", name)
    if url_res.returncode != 0:
        return None
    url = url_res.stdout.strip()
    web_url = github_web_url(url)
    info: dict[str, Any] = {"name": name, "url": url, "web_url": web_url}
    if web_url:
        # GitHub-first enrichment: surface whether the host's gh could act on
        # this repo. Informational only; push never depends on gh.
        info["gh_authenticated"] = gh_authenticated()
    return info


def push_toggle_on(conn: sqlite3.Connection, area_id: int | None) -> bool:
    if not area_id:
        return False  # area deleted since the job ran - nothing opted in
    row = conn.execute("SELECT push_on_merge FROM project_areas WHERE id = ?", (area_id,)).fetchone()
    return bool(row and row["push_on_merge"])


def _record(conn: sqlite3.Connection, wt_id: int, status: str, error: str | None,
            remote: str | None, remote_url: str | None) -> None:
    conn.execute(
        "UPDATE job_worktrees SET push_status = ?, push_error = ?, push_remote = ?, "
        "push_remote_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, error, remote, remote_url, wt_id),
    )


def push_merged_branch(conn: sqlite3.Connection, wt: sqlite3.Row) -> dict[str, Any]:
    """Push a merged job's base branch to its code area's remote and record
    the outcome on the worktree row. Never raises for a failed push - the
    failure IS the result (exact command + output), recorded for the blocker
    card. The caller has already checked the toggle/retry authorization."""
    repo = Path(wt["repo_path"])
    remote = detect_remote(repo)
    if remote is None:
        # The toggle was on but the remote is gone (or the repo moved) - an
        # honest blocker, same surface as a failed push.
        error = f"$ git remote get-url origin\nno git remote is configured for {repo}"
        _record(conn, wt["id"], "failed", error, None, None)
        return {"status": "failed", "error": error}
    command = f"git push {remote['name']} {wt['base_branch']}"
    try:
        res = _git(repo, "push", remote["name"], wt["base_branch"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = f"$ {command}\n{exc}"
        _record(conn, wt["id"], "failed", error, remote["name"], remote["url"])
        return {"status": "failed", "error": error}
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()[-2000:]
        error = f"$ {command}\n{detail}"
        _record(conn, wt["id"], "failed", error, remote["name"], remote["url"])
        return {"status": "failed", "error": error}
    _record(conn, wt["id"], "pushed", None, remote["name"], remote["url"])
    return {"status": "pushed", "remote": remote["name"], "remote_url": remote["url"]}


def push_after_merge(conn: sqlite3.Connection, wt: sqlite3.Row) -> dict[str, Any] | None:
    """The lifecycle hook the approve paths call right after a successful
    local merge. No-op unless the target code area's toggle is explicitly ON
    (default off - the T9 guardrail); a failure records the blocker and
    returns, never unwinding the merge or failing the approve."""
    if wt["status"] != "merged" or not push_toggle_on(conn, wt["area_id"]):
        return None
    return push_merged_branch(conn, wt)

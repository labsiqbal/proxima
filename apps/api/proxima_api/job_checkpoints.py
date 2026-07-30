"""Job-scoped checkpoints for Master-dispatched durable work.

A checkpoint contains only the owning job's state plus repository refs for that
job's project/target. It is intentionally not a database backup or filesystem
archive. Restores refuse while work in the same project is running.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .container_registry import container_root, resolve_area_root


class CheckpointError(RuntimeError):
    pass


RESTORABLE_JOB_FIELDS = (
    "status",
    "current_step_idx",
    "input",
    "steps_state",
    "engine",
    "graph",
    "target_area_id",
    "rejected_reason",
    "started_at",
    "finished_at",
)


def _git_sha(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_refs(conn, job: dict[str, Any]) -> list[dict[str, Any]]:
    if not job.get("project_id"):
        return []
    project = conn.execute(
        "SELECT id, path, path_identity FROM projects WHERE id = ?",
        (job["project_id"],),
    ).fetchone()
    if not project or not project["path"]:
        return []
    repo_path = container_root(project)
    if job.get("target_area_id"):
        repo_path = resolve_area_root(
            conn,
            project,
            int(job["target_area_id"]),
        )
    worktree = conn.execute(
        "SELECT id, worktree_path, branch, base_commit, status FROM job_worktrees WHERE job_id = ?",
        (job["id"],),
    ).fetchone()
    worktree_path = Path(worktree["worktree_path"]) if worktree and worktree["worktree_path"] else None
    # Only an existing job-owned worktree may be reset during restore. A main
    # checkout ref remains useful evidence, but is reference-only so unrelated
    # project work can never be silently rewound.
    capture_path = worktree_path if worktree_path and worktree_path.is_dir() else repo_path
    sha = _git_sha(capture_path)
    if not sha:
        return []
    ref: dict[str, Any] = {
        "project_id": project["id"],
        "repo_path": str(repo_path.resolve()),
        "sha": sha,
        "restore_strategy": "worktree_reset" if capture_path == worktree_path else "reference_only",
    }
    if worktree:
        ref.update(
            {
                "worktree_id": worktree["id"],
                "worktree_path": worktree["worktree_path"],
                "branch": worktree["branch"],
                "base_commit": worktree["base_commit"],
                "worktree_status": worktree["status"],
            }
        )
    return [ref]


def create_checkpoint(conn, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise CheckpointError("job not found")
    job = dict(row)
    node_states = [dict(item) for item in conn.execute(
        "SELECT * FROM node_states WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()]
    run_ids = [item["id"] for item in conn.execute(
        "SELECT r.id FROM runs r JOIN sessions s ON s.id = r.session_id "
        "WHERE s.job_id = ? ORDER BY r.id", (job_id,)
    ).fetchall()]
    payload = {
        "job": {field: job.get(field) for field in RESTORABLE_JOB_FIELDS},
        "node_states": node_states,
        "run_ids": run_ids,
    }
    refs = _git_refs(conn, job)
    cur = conn.execute(
        "INSERT INTO job_checkpoints(job_id, payload_json, git_refs_json) VALUES (?, ?, ?)",
        (job_id, json.dumps(payload), json.dumps(refs)),
    )
    # Product-wide FIFO of 30 unpinned checkpoints. Pinned rows never count
    # toward or participate in eviction.
    stale = conn.execute(
        "SELECT id FROM job_checkpoints WHERE pinned = 0 "
        "ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET 30"
    ).fetchall()
    if stale:
        conn.executemany(
            "DELETE FROM job_checkpoints WHERE id = ?", ((item["id"],) for item in stale)
        )
    return checkpoint_payload(
        conn.execute("SELECT * FROM job_checkpoints WHERE id = ?", (cur.lastrowid,)).fetchone()
    )


def checkpoint_payload(row) -> dict[str, Any]:
    data = dict(row)
    data["pinned"] = bool(data.get("pinned"))
    for source, target in (("payload_json", "payload"), ("git_refs_json", "git_refs")):
        try:
            data[target] = json.loads(data.pop(source) or ("{}" if target == "payload" else "[]"))
        except (TypeError, ValueError):
            data[target] = {} if target == "payload" else []
    return data


def list_checkpoints(
    conn,
    *,
    origin_master_session_id: int | None = None,
    job_id: int | None = None,
    alpha_session_id: int | None = None,
) -> list[dict[str, Any]]:
    """List checkpoints, accepting the Alpha keyword for one compatibility release."""
    if (
        origin_master_session_id is not None
        and alpha_session_id is not None
        and origin_master_session_id != alpha_session_id
    ):
        raise CheckpointError("conflicting Master and Alpha session ids")
    origin_session_id = (
        origin_master_session_id
        if origin_master_session_id is not None
        else alpha_session_id
    )
    if job_id is not None:
        rows = conn.execute(
            "SELECT * FROM job_checkpoints WHERE job_id = ? ORDER BY created_at DESC, id DESC",
            (job_id,),
        ).fetchall()
    elif origin_session_id is not None:
        rows = conn.execute(
            "SELECT cp.* FROM job_checkpoints cp JOIN jobs j ON j.id = cp.job_id "
            "WHERE j.origin_master_session_id = ? ORDER BY cp.created_at DESC, cp.id DESC",
            (origin_session_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM job_checkpoints ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [checkpoint_payload(row) for row in rows]


def restore_impact(conn, checkpoint_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT cp.*, j.title, j.project_id, j.status AS current_status "
        "FROM job_checkpoints cp JOIN jobs j ON j.id = cp.job_id WHERE cp.id = ?",
        (checkpoint_id,),
    ).fetchone()
    if not row:
        raise CheckpointError("checkpoint not found")
    payload = checkpoint_payload(row)
    snapshot = payload["payload"].get("job") or {}
    refs = payload["git_refs"]
    # A later job in the same project may depend on refs created after this
    # checkpoint. Refuse rather than rewinding shared git state underneath it.
    conflicts = [dict(item) for item in conn.execute(
        "SELECT id, title, status FROM jobs WHERE project_id IS ? AND ("
        "status = 'running' OR (id != ? AND started_at IS NOT NULL AND (started_at > ? OR id > ?))"
        ") ORDER BY id",
        (row["project_id"], row["job_id"], row["created_at"], row["job_id"]),
    ).fetchall()]
    return {
        "checkpoint_id": checkpoint_id,
        "job_id": row["job_id"],
        "job_title": row["title"],
        "current_status": row["current_status"],
        "restored_status": snapshot.get("status"),
        "database_scope": ["job", "node_states", "job runs created after checkpoint"],
        "git_refs": refs,
        "conflicts": conflicts,
        "can_restore": not conflicts,
    }


def _worktree_restore_target(ref: dict[str, Any]) -> tuple[Path, str] | None:
    if ref.get("restore_strategy") != "worktree_reset":
        return None
    path = Path(str(ref.get("worktree_path") or ""))
    sha = str(ref.get("sha") or "")
    return (path, sha) if path.is_dir() and sha else None


def _preflight_git(
    ref: dict[str, Any],
) -> tuple[Path, str, str] | None:
    target = _worktree_restore_target(ref)
    if not target:
        return None
    path, sha = target
    try:
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
        if dirty:
            raise CheckpointError(f"repository has uncommitted changes: {path}")
        subprocess.run(
            ["git", "-C", str(path), "cat-file", "-e", f"{sha}^{{commit}}"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
        original_sha = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
        if not original_sha:
            raise CheckpointError(f"repository has no restorable HEAD: {path}")
        return path, sha, original_sha
    except CheckpointError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckpointError(f"could not validate git ref for {path}: {exc}") from exc


def _verify_git_restore_plan(plan: tuple[Path, str, str]) -> None:
    path, _sha, original_sha = plan
    try:
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
        current_sha = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
        if dirty or current_sha != original_sha:
            raise CheckpointError(
                f"repository changed during checkpoint restore: {path}"
            )
    except CheckpointError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckpointError(f"could not revalidate git ref for {path}: {exc}") from exc


def _reset_git(plan: tuple[Path, str, str]) -> str:
    path, sha, _original_sha = plan
    try:
        subprocess.run(
            ["git", "-C", str(path), "reset", "--hard", sha],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        return str(path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckpointError(f"could not restore git ref for {path}: {exc}") from exc


def _compensate_git(plan: tuple[Path, str, str]) -> None:
    path, _sha, original_sha = plan
    try:
        subprocess.run(
            ["git", "-C", str(path), "reset", "--hard", original_sha],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckpointError(
            f"could not compensate checkpoint restore for {path}: {exc}"
        ) from exc


def restore_checkpoint(
    conn,
    checkpoint_id: int,
    *,
    confirmed: bool,
    actor: dict[str, Any] | None = None,
    on_restore: Callable[[Any, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    impact = restore_impact(conn, checkpoint_id)
    if not confirmed:
        raise CheckpointError("restore confirmation is required")
    if impact["conflicts"]:
        raise CheckpointError("conflicting jobs are running in this project")
    row = conn.execute(
        "SELECT * FROM job_checkpoints WHERE id = ?", (checkpoint_id,)
    ).fetchone()
    checkpoint = checkpoint_payload(row)
    snapshot = checkpoint["payload"]
    current_job = dict(
        conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (checkpoint["job_id"],)
        ).fetchone()
    )
    current_nodes = {
        str(item["node_id"]): dict(item)
        for item in conn.execute(
            "SELECT * FROM node_states WHERE job_id = ? ORDER BY id",
            (checkpoint["job_id"],),
        ).fetchall()
    }
    checkpoint_nodes = {
        str(item.get("node_id")): item
        for item in snapshot.get("node_states") or []
    }
    git_restore_plans = [
        plan
        for ref in checkpoint["git_refs"]
        if (plan := _preflight_git(ref)) is not None
    ]
    job = snapshot.get("job") or {}
    values = [job.get(field) for field in RESTORABLE_JOB_FIELDS]
    assignments = ", ".join(f"{field} = ?" for field in RESTORABLE_JOB_FIELDS)
    attempted_plans: list[tuple[Path, str, str]] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            f"UPDATE jobs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values, checkpoint["job_id"]),
        )
        checkpoint_run_ids = {int(value) for value in snapshot.get("run_ids") or []}
        current_runs = [dict(item) for item in conn.execute(
            "SELECT r.id, r.status FROM runs r JOIN sessions s ON s.id = r.session_id WHERE s.job_id = ?",
            (checkpoint["job_id"],),
        ).fetchall()]
        current_run_ids = [item["id"] for item in current_runs]
        remove_ids = [run_id for run_id in current_run_ids if run_id not in checkpoint_run_ids]
        discarded_progress = []
        if remove_ids:
            label = "run" if len(remove_ids) == 1 else "runs"
            discarded_progress.append(
                f"{len(remove_ids)} {label} created after the checkpoint"
            )
        if current_job.get("steps_state") != job.get("steps_state"):
            discarded_progress.append("Task step progress changed after the checkpoint")
        changed_nodes = 0
        for node_id in sorted(set(current_nodes) | set(checkpoint_nodes)):
            current_status = (current_nodes.get(node_id) or {}).get("status")
            checkpoint_status = (checkpoint_nodes.get(node_id) or {}).get("status")
            if current_status != checkpoint_status:
                changed_nodes += 1
        if changed_nodes:
            label = "record" if changed_nodes == 1 else "records"
            discarded_progress.append(
                f"{changed_nodes} Recipe node progress {label} "
                "changed after the checkpoint"
            )
        if remove_ids:
            conn.executemany("DELETE FROM runs WHERE id = ?", ((run_id,) for run_id in remove_ids))
        conn.execute("DELETE FROM node_states WHERE job_id = ?", (checkpoint["job_id"],))
        node_fields = (
            "job_id", "node_id", "status", "run_id", "inputs", "output_kind", "output",
            "checkpoint", "error", "version", "started_at", "finished_at", "question",
            "answer", "contract_failures", "created_at", "updated_at",
        )
        for node in snapshot.get("node_states") or []:
            conn.execute(
                f"INSERT INTO node_states({','.join(node_fields)}) VALUES ({','.join('?' for _ in node_fields)})",
                tuple(node.get(field) for field in node_fields),
            )
        git_restored = [str(plan[0]) for plan in git_restore_plans]
        if git_restored:
            label = "worktree" if len(git_restored) == 1 else "worktrees"
            discarded_progress.append(
                f"{len(git_restored)} Task {label} reset to the checkpoint"
            )
        recovery = {
            "job_id": checkpoint["job_id"],
            "checkpoint_id": checkpoint_id,
            "actor": actor or {"id": None, "username": "local owner"},
            "prior_status": impact["current_status"],
            "restored_status": impact["restored_status"],
            "discarded_progress": discarded_progress,
            "conflicting_progress": [
                f"Task #{item['id']} ({item['status']})"
                for item in impact["conflicts"]
            ],
        }
        if on_restore is not None:
            on_restore(conn, recovery)
        for plan in git_restore_plans:
            _verify_git_restore_plan(plan)
        for plan in git_restore_plans:
            attempted_plans.append(plan)
            _reset_git(plan)
        conn.execute("COMMIT")
    except Exception as exc:
        rollback_error: Exception | None = None
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except Exception as error:
                rollback_error = error
        compensation_errors: list[str] = []
        for plan in reversed(attempted_plans):
            try:
                _compensate_git(plan)
            except CheckpointError as compensation_error:
                compensation_errors.append(str(compensation_error))
        if compensation_errors:
            raise CheckpointError(
                "checkpoint restore failed and worktree compensation failed: "
                + "; ".join(compensation_errors)
            ) from exc
        if rollback_error is not None:
            raise CheckpointError(
                f"checkpoint restore database rollback failed: {rollback_error}"
            ) from exc
        raise
    return {
        "restored": impact["database_scope"],
        "git_restored": git_restored,
        "recovery": recovery,
        **impact,
    }

"""Session-scoped file journals for hands-on Chat turns."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

MAX_FILES = 400
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
SKIP_PARTS = {
    ".git", "node_modules", "dist", "build", ".next", ".cache", "coverage",
    "__pycache__", ".venv", "venv",
}
SKIP_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".zip", ".gz", ".tar", ".db", ".sqlite",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".woff", ".woff2",
}


class TurnRestoreError(RuntimeError):
    pass


def _eligible(root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return not any(part in SKIP_PARTS for part in rel.parts) and path.suffix.lower() not in SKIP_SUFFIXES


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _iter_files(root: Path):
    """Walk eligible trees without descending into dependency/cache forests."""
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        names[:] = sorted(
            name for name in names
            if name not in SKIP_PARTS and not (directory_path / name).is_symlink()
        )
        for name in sorted(files):
            path = directory_path / name
            if not path.is_symlink():
                yield path


def capture_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Capture bounded before-content for files that a normal chat turn may edit.

    The worker takes this at the turn boundary and only persists changed paths,
    so retained data remains a write journal rather than a project archive.
    """
    root = root.resolve()
    snapshot: dict[str, dict[str, Any]] = {}
    total = 0
    if not root.is_dir():
        return snapshot
    for path in _iter_files(root):
        if len(snapshot) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            break
        if not path.is_file() or not _eligible(root, path):
            continue
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        snapshot[rel] = {
            "hash": _hash(content),
            "content_b64": base64.b64encode(content).decode("ascii"),
        }
        total += len(content)
    return snapshot


def _current_files(root: Path) -> dict[str, str]:
    current: dict[str, str] = {}
    total = 0
    if not root.is_dir():
        return current
    for path in _iter_files(root):
        if len(current) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            break
        if not path.is_file() or not _eligible(root, path):
            continue
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        current[path.relative_to(root).as_posix()] = _hash(content)
        total += len(content)
    return current


def record_journal(
    conn,
    *,
    message_id: int,
    session_id: int,
    root: Path,
    before: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    after = _current_files(root.resolve())
    entries: list[dict[str, Any]] = []
    for rel in sorted(set(before) | set(after)):
        old = before.get(rel)
        after_hash = after.get(rel)
        before_hash = old.get("hash") if old else None
        if before_hash == after_hash:
            continue
        entries.append(
            {
                "path": rel,
                "before_hash": before_hash,
                "before_content_b64": old.get("content_b64") if old else None,
                "after_hash": after_hash,
            }
        )
    if not entries:
        return None
    conn.execute(
        "INSERT INTO turn_file_journals(message_id, session_id, entries_json) VALUES (?, ?, ?)",
        (message_id, session_id, json.dumps(entries)),
    )
    return {"paths": [entry["path"] for entry in entries], "count": len(entries)}


def _journal_for_message(conn, message_id: int):
    row = conn.execute(
        "SELECT j.*, s.project_id FROM turn_file_journals j "
        "JOIN messages m ON m.id = j.message_id "
        "JOIN sessions s ON s.id = j.session_id "
        "WHERE j.message_id = ?",
        (message_id,),
    ).fetchone()
    if not row:
        raise TurnRestoreError("this turn has no restorable file changes")
    try:
        entries = json.loads(row["entries_json"] or "[]")
    except (TypeError, ValueError) as exc:
        raise TurnRestoreError("turn journal is unreadable") from exc
    return row, entries


def preview(conn, message_id: int) -> dict[str, Any]:
    row, entries = _journal_for_message(conn, message_id)
    active = [dict(item) for item in conn.execute(
        "SELECT j.id, j.title FROM jobs j WHERE j.project_id IS ? "
        "AND j.alpha_session_id IS NOT NULL AND j.status = 'running' ORDER BY j.id",
        (row["project_id"],),
    ).fetchall()]
    return {
        "message_id": message_id,
        "paths": [entry["path"] for entry in entries],
        "warning": (
            "Alpha has active work in this project. Restoring may overwrite those workers' changes."
            if active else None
        ),
        "active_alpha_jobs": active,
    }


def restore(conn, message_id: int, *, root: Path, confirmed: bool, accept_active_alpha: bool) -> dict[str, Any]:
    impact = preview(conn, message_id)
    if not confirmed:
        raise TurnRestoreError("restore confirmation is required")
    if impact["active_alpha_jobs"] and not accept_active_alpha:
        raise TurnRestoreError("active Alpha work must be acknowledged before restore")
    _row, entries = _journal_for_message(conn, message_id)
    root = root.resolve()
    restored: list[str] = []
    for entry in entries:
        rel = str(entry.get("path") or "")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise TurnRestoreError(f"journal path leaves the project: {rel}")
        encoded = entry.get("before_content_b64")
        if encoded is None:
            if target.exists() and target.is_file():
                target.unlink()
        else:
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise TurnRestoreError(f"journal content is unreadable for {rel}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        restored.append(rel)
    # One restore per journal. Removing the row keeps repeated clicks from
    # overwriting later legitimate edits and it is deleted with the session.
    conn.execute("DELETE FROM turn_file_journals WHERE message_id = ?", (message_id,))
    return {"paths": restored, "restored": len(restored), "warning": impact["warning"]}

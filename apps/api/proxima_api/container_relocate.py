"""Relocate/rebind a moved or renamed project folder (prune C6, #141).

A project is the folder as it exists on disk (decision #121), and folders get
moved and renamed. Until now that was a dead end: the container root is pinned
to its filesystem identity at link time, so a moved, renamed, or
restored-from-backup folder turned every project operation into a
``ContainerBoundaryError`` with no way back (audit #120 part 2, item 6).

Rebinding re-pins the record to the folder's real location:

- **The owner points Proxima at the new path** through the same onboarding
  folder picker, so the realpath jail (configured link roots) applies exactly
  as it does at link time - the route resolves the target, this module never
  sees an unjailed path.
- **Identity is confirmed with the #137 machinery**: the identity docs the
  folder already has (``AGENTS.md`` / ``README.md`` / ``HANDOFF.md``, folder
  name as the fallback) are read AT THE NEW LOCATION and compared against the
  stored projection. A mismatch warns clearly and is refused - but it is
  overridable, because this is a single-owner product and the owner knows
  their own folders.
- **Everything else survives**: the project row keeps its id, so history,
  chats, tasks, deliverable records, approvals, the per-project Ops path, the
  layout map, and the memory-writes toggle are all untouched by construction.
  Only entries whose *paths* broke are re-detected at the new location.
- **Metadata only**: not one byte is written into either the old or the new
  folder. Validation is read-only and the realpath jail is unchanged; because
  rebinding moves no content it takes no deep symlink walk (prune C7, #142) -
  a symlinked Container or Ops root is still refused.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from . import layout_map
from .container_registry import (
    container_binding,
    container_mutation_lock,
    detect_ops_path,
    get_container,
    refresh_registry_projection,
    resolve_container_identity,
    validated_area_roots,
)
from .project_areas import sync_code_areas


class RebindRefused(Exception):
    """The rebind cannot proceed as asked; ``preview`` says why."""

    def __init__(self, message: str, preview: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.preview = dict(preview)


def _stored_identity(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT identity_label, summary, identity_source, source_hash "
        "FROM container_registry WHERE container_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return {"label": None, "summary": None, "source": None, "hash": None}
    return {
        "label": row["identity_label"],
        "summary": row["summary"],
        "source": row["identity_source"],
        "hash": row["source_hash"],
    }


def _normalized(label: Any) -> str:
    return str(label or "").strip().casefold()


def _is_real_directory(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _probe(
    container: Mapping[str, Any],
    target: Path,
    target_identity: str,
) -> dict[str, Any]:
    """The container row as it WOULD be after the rebind, for read-only
    resolution against the new location (nothing is persisted)."""
    data = dict(container)
    data["path"] = str(target)
    data["path_identity"] = target_identity
    return data


def _ops_rel(conn: sqlite3.Connection, project_id: int) -> str:
    row = conn.execute(
        "SELECT rel_path FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (project_id,),
    ).fetchone()
    return str(row["rel_path"] or ".") if row is not None else "."


def _code_area_rels(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, rel_path, source FROM project_areas "
        "WHERE project_id = ? AND kind = 'code' AND source != 'excluded' "
        "ORDER BY rel_path",
        (project_id,),
    ).fetchall()


def _resolves(root: Path, rel: str) -> bool:
    if rel in ("", "."):
        return True
    return _is_real_directory(root.joinpath(*PurePosixPath(rel).parts))


def preview_rebind(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    target: Path,
    target_identity: str,
) -> dict[str, Any]:
    """Read-only projection of what re-pinning this project at ``target``
    would mean: does the folder's own identity still match the stored one,
    and does the persisted layout still resolve there?"""
    data = get_container(conn, container)
    project_id = int(data["id"])
    probe = _probe(data, target, target_identity)
    label, summary, source_rel, source_hash = resolve_container_identity(conn, probe)
    stored = _stored_identity(conn, project_id)
    matches = bool(
        stored["hash"] is None
        or stored["hash"] == source_hash
        or (
            _normalized(stored["label"])
            and _normalized(stored["label"]) == _normalized(label)
        )
    )
    ops_rel = _ops_rel(conn, project_id)
    ops_resolves = _resolves(target, ops_rel)
    missing_areas = [
        str(row["rel_path"])
        for row in _code_area_rels(conn, project_id)
        if not _resolves(target, str(row["rel_path"]))
    ]
    return {
        "path": str(target),
        "previous_path": str(data.get("path") or ""),
        "identity": {
            "matches": matches,
            "stored": {
                "label": stored["label"],
                "summary": stored["summary"],
                "source": stored["source"],
            },
            "found": {
                "label": label,
                "summary": summary,
                "source": source_rel,
            },
        },
        "ops_path": {"path": ops_rel, "resolves": ops_resolves},
        "missing_code_areas": missing_areas,
        "ok": bool(matches and ops_resolves and not missing_areas),
    }


def rebind_container(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    target: Path,
    target_identity: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Re-pin one project to ``target``. Metadata only - zero writes on disk.

    Raises :class:`RebindRefused` when the new location does not confirm and
    the owner has not overridden it, and
    :class:`~.container_registry.ContainerBoundaryError` when the result would
    not be a valid Container (nothing is persisted in either case).

    Re-pinning the SAME folder a project is already correctly bound to is a
    no-op: the record already says the truth, so nothing is written.
    """
    data = get_container(conn, container)
    project_id = int(data["id"])
    with container_mutation_lock(conn, project_id):
        binding = container_binding(data)
        preview = preview_rebind(conn, data, target, target_identity)
        same_path = str(data.get("path") or "") == str(target)
        if same_path and binding["state"] == "bound" and preview["ok"]:
            # The record already says the truth AND the layout resolves there:
            # nothing to change. (A same-path rebind is NOT a no-op when the
            # Ops folder or an Area stopped resolving - that repair is the
            # point of asking.)
            return {
                "rebound": False,
                "path": str(target),
                "previous_path": str(target),
                "identity": preview["identity"],
                "repaired": {
                    "ops_path": None,
                    "layout": [],
                    "code_areas_dropped": [],
                },
            }
        if not preview["ok"] and not confirm:
            raise RebindRefused(
                _refusal_message(preview),
                preview,
            )
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE projects SET path = ?, path_identity = ? WHERE id = ?",
                (str(target), target_identity, project_id),
            )
            row = dict(
                conn.execute(
                    "SELECT * FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
            )
            repaired_ops: str | None = None
            if not preview["ops_path"]["resolves"]:
                # The persisted Ops folder does not exist here: fall back to
                # the same detection the link flow uses (prune C3) instead of
                # leaving the container boundary-invalid.
                repaired_ops = detect_ops_path(Path(target))
                conn.execute(
                    "UPDATE project_areas SET rel_path = ?, source = 'auto', "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
                    (repaired_ops, project_id),
                )
            # Auto rows follow the new tree; a manual row whose folder is not
            # here at all would keep the container permanently invalid, so it
            # is dropped (the owner can re-register it) and reported back.
            sync_code_areas(conn, project_id, target, validate=False)
            dropped = [
                str(area["rel_path"])
                for area in _code_area_rels(conn, project_id)
                if not _resolves(Path(target), str(area["rel_path"]))
            ]
            for rel in dropped:
                conn.execute(
                    "DELETE FROM project_areas WHERE project_id = ? AND kind = 'code' "
                    "AND rel_path = ?",
                    (project_id, rel),
                )
            rebased = layout_map.rebase_project_layout(conn, row)
            refresh_registry_projection(conn, row)
            # Rebinding moves no content, so it does not take the deep
            # descendant-symlink walk (prune C7); the layout checks and the
            # symlinked-Ops-root refusal still apply.
            validated_area_roots(conn, row)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return {
        "rebound": True,
        "path": str(target),
        "previous_path": preview["previous_path"],
        "identity": preview["identity"],
        "repaired": {
            "ops_path": repaired_ops,
            "layout": list(rebased),
            "code_areas_dropped": dropped,
        },
    }


def _refusal_message(preview: Mapping[str, Any]) -> str:
    identity = preview["identity"]
    if not identity["matches"]:
        found = identity["found"]["label"] or "an unnamed folder"
        stored = identity["stored"]["label"] or "this project"
        return (
            f"That folder identifies itself as “{found}”, not “{stored}”. "
            "Pick the right folder, or confirm to re-pin it anyway."
        )
    if not preview["ops_path"]["resolves"]:
        return (
            f"That folder has no “{preview['ops_path']['path']}” Ops folder. "
            "Confirm to re-pin it and re-detect the Ops folder there."
        )
    missing = ", ".join(preview["missing_code_areas"])
    return (
        f"That folder is missing registered code areas ({missing}). "
        "Confirm to re-pin it and drop what is not there."
    )


def location_payload(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """The actionable folder state for one project: where its folder should
    be, whether it is there, and what the owner can do about it."""
    data = get_container(conn, container)
    binding = container_binding(data)
    stored = _stored_identity(conn, int(data["id"]))
    bound = binding["state"] == "bound"
    return {
        **binding,
        "identity": {
            "label": stored["label"],
            "summary": stored["summary"],
            "source": stored["source"],
        },
        "actions": [] if bound else ["rebind", "unlink"],
    }


__all__ = (
    "RebindRefused",
    "location_payload",
    "preview_rebind",
    "rebind_container",
)

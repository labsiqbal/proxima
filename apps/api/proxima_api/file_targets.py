"""Canonical, server-validated identity for Container and Area files.

Display paths are compatibility/UI data. A :class:`FileLocator` is the durable
request identity: Container slug, authoritative Area identity, and a path
relative to that root. Every resolver revalidates the project/Area relationship
and applies the ordinary realpath jail before returning an absolute path.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from . import container_registry, fsapi

AREA_KINDS = frozenset(("container", "ops", "code"))


class FileTargetError(ValueError):
    """A serialized locator or its project/Area binding is invalid."""


@dataclass(frozen=True)
class FileArea:
    kind: str
    id: int | None

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True)
class FileLocator:
    project: str
    area: FileArea
    path: str

    def payload(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "area": self.area.payload(),
            "path": self.path,
        }

    def serialized(self) -> str:
        return json.dumps(self.payload(), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class ResolvedFile:
    locator: FileLocator
    root: Path
    path: Path


def normalize_relative_path(raw: str, *, allow_empty: bool = True) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    if "\x00" in text:
        raise FileTargetError("invalid file path")
    if not text:
        if allow_empty:
            return ""
        raise FileTargetError("file path is required")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise FileTargetError("path escapes file Area")
    normalized = candidate.as_posix()
    if normalized == ".":
        if allow_empty:
            return ""
        raise FileTargetError("file path is required")
    return normalized


def parse_locator(raw: str | Mapping[str, Any] | FileLocator) -> FileLocator:
    if isinstance(raw, FileLocator):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FileTargetError("invalid file target") from exc
    else:
        data = dict(raw)
    if not isinstance(data, dict) or set(data) != {"project", "area", "path"}:
        raise FileTargetError("invalid file target")
    project = data.get("project")
    area = data.get("area")
    if not isinstance(project, str) or not project.strip():
        raise FileTargetError("file target project is required")
    if not isinstance(area, dict) or set(area) != {"kind", "id"}:
        raise FileTargetError("invalid file target Area")
    kind = area.get("kind")
    area_id = area.get("id")
    if kind not in AREA_KINDS:
        raise FileTargetError("invalid file target Area kind")
    if kind == "container":
        if area_id is not None:
            raise FileTargetError("Container file target cannot carry an Area id")
    elif isinstance(area_id, bool) or not isinstance(area_id, int) or area_id <= 0:
        raise FileTargetError("file target Area id is required")
    return FileLocator(
        project=project,
        area=FileArea(kind=kind, id=area_id),
        path=normalize_relative_path(str(data.get("path") or "")),
    )


def container_locator(
    container: sqlite3.Row | Mapping[str, Any],
    path: str = "",
) -> FileLocator:
    data = dict(container)
    return FileLocator(
        project=str(data["slug"]),
        area=FileArea(kind="container", id=None),
        path=normalize_relative_path(path),
    )


def area_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    area_id: int,
    path: str = "",
) -> FileLocator:
    data = container_registry.get_container(conn, container)
    row = conn.execute(
        "SELECT id, kind FROM project_areas "
        "WHERE id = ? AND project_id = ? AND source != 'excluded'",
        (area_id, data["id"]),
    ).fetchone()
    if row is None or row["kind"] not in ("ops", "code"):
        raise FileTargetError("Area is not active in this Container")
    return FileLocator(
        project=str(data["slug"]),
        area=FileArea(kind=str(row["kind"]), id=int(row["id"])),
        path=normalize_relative_path(path),
    )


def ops_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    path: str = "",
) -> FileLocator:
    data = container_registry.get_container(conn, container)
    row = conn.execute(
        "SELECT id FROM project_areas "
        "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
        (data["id"],),
    ).fetchone()
    if row is None:
        raise FileTargetError("Container has no active Ops Area")
    return area_locator(conn, data, int(row["id"]), path)


def child_locator(parent: FileLocator, name: str) -> FileLocator:
    child = normalize_relative_path(
        f"{parent.path}/{name}" if parent.path else name,
        allow_empty=False,
    )
    return FileLocator(project=parent.project, area=parent.area, path=child)


def resolve_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    locator: FileLocator | Mapping[str, Any] | str,
) -> ResolvedFile:
    data = container_registry.get_container(conn, container)
    target = parse_locator(locator)
    if target.project != data.get("slug"):
        raise FileTargetError("file target belongs to another Container")
    if target.area.kind == "container":
        root = container_registry.container_root(data)
    else:
        assert target.area.id is not None
        row = conn.execute(
            "SELECT kind FROM project_areas "
            "WHERE id = ? AND project_id = ? AND source != 'excluded'",
            (target.area.id, data["id"]),
        ).fetchone()
        if row is None or row["kind"] != target.area.kind:
            raise FileTargetError("Area is not active in this Container")
        root = container_registry.resolve_area_root(conn, data, target.area.id)
    try:
        absolute = fsapi.resolve_in_project(root, target.path)
    except fsapi.FsError as exc:
        raise FileTargetError(str(exc)) from exc
    return ResolvedFile(locator=target, root=root, path=absolute)


def legacy_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    path: str,
) -> FileLocator:
    """Upgrade a documented path-only request without guessing from a basename.

    Reserved virtual Ops roots retain their historical mapping. An explicit
    ``ops/`` path upgrades to the Ops Area only for the physical ``ops`` layout.
    When the active legacy Ops Area is ``.``, the prefix remains a real
    Container child so a migration-collision folder cannot be mistaken for the
    legacy root.
    """
    data = container_registry.get_container(conn, container)
    normalized = normalize_relative_path(path)
    first = next(iter(PurePosixPath(normalized).parts), "")
    if first == container_registry.OPS_RELPATH:
        row = conn.execute(
            "SELECT id, rel_path FROM project_areas "
            "WHERE project_id = ? AND kind = 'ops' AND source != 'excluded'",
            (data["id"],),
        ).fetchone()
        if row is not None and row["rel_path"] == container_registry.OPS_RELPATH:
            relative = "/".join(PurePosixPath(normalized).parts[1:])
            return area_locator(conn, data, int(row["id"]), relative)
    if first in container_registry.OPS_VIRTUAL_NAMES:
        return ops_locator(conn, data, normalized)
    return container_locator(data, normalized)


def resolve_request(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    path: str = "",
    target: str | Mapping[str, Any] = "",
) -> ResolvedFile:
    locator = parse_locator(target) if target else legacy_locator(conn, container, path)
    return resolve_locator(conn, container, locator)


def add_targets(
    entries: list[dict[str, Any]],
    parent: FileLocator,
) -> list[dict[str, Any]]:
    return [{**entry, "target": child_locator(parent, str(entry["name"])).payload()} for entry in entries]


def add_ops_targets(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            **item,
            "target": ops_locator(conn, container, str(item.get("path") or "")).payload(),
        }
        for item in items
    ]

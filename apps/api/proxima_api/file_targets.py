"""Canonical, server-validated identity for Container and Area files.

Display paths are compatibility/UI data. A :class:`FileLocator` is the durable
request identity: Container slug, authoritative Area identity, and a path
relative to that root. Every resolver revalidates the project/Area relationship
and applies the ordinary realpath jail before returning an absolute path.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

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


@dataclass(frozen=True)
class _AreaBinding:
    area: FileArea
    root: Path


@dataclass(frozen=True)
class FileTargetContext:
    project_id: int
    project: str
    container_root: Path
    bindings: tuple[_AreaBinding, ...]

    def root_for(self, area: FileArea) -> Path:
        if area.kind == "container":
            return self.container_root
        binding = next(
            (item for item in self.bindings if item.area == area),
            None,
        )
        if binding is None:
            raise FileTargetError("Area is not active in this Container")
        return binding.root

    def ops_root(self) -> Path:
        binding = next(
            (item for item in self.bindings if item.area.kind == "ops"),
            None,
        )
        if binding is None:
            raise FileTargetError("Container has no active Ops Area")
        return binding.root


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
        if not isinstance(raw, Mapping):
            raise FileTargetError("invalid file target")
        try:
            data = dict(raw)
        except (TypeError, ValueError) as exc:
            raise FileTargetError("invalid file target") from exc
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


def _area_bindings(
    conn: sqlite3.Connection,
    container: sqlite3.Row | Mapping[str, Any],
) -> tuple[_AreaBinding, ...]:
    data = container_registry.get_container(conn, container)
    rows = conn.execute(
        "SELECT id, kind FROM project_areas "
        "WHERE project_id = ? AND source != 'excluded' ORDER BY id",
        (data["id"],),
    ).fetchall()
    roots = container_registry.validated_area_roots(conn, data)
    return tuple(
        _AreaBinding(
            area=FileArea(kind=str(row["kind"]), id=int(row["id"])),
            root=roots[int(row["id"])],
        )
        for row in rows
        if row["kind"] in ("ops", "code")
    )


def target_context(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
) -> FileTargetContext:
    data = container_registry.get_container(conn, container)
    return FileTargetContext(
        project_id=int(data["id"]),
        project=str(data["slug"]),
        container_root=container_registry.container_root(data),
        bindings=_area_bindings(conn, data),
    )


def _context_for(
    conn: sqlite3.Connection,
    data: Mapping[str, Any],
    context: FileTargetContext | None,
) -> FileTargetContext:
    resolved = context or target_context(conn, data)
    if (
        resolved.project_id != int(data["id"])
        or resolved.project != str(data["slug"])
    ):
        raise FileTargetError("file target context belongs to another Container")
    return resolved


def _authoritative_binding(
    bindings: tuple[_AreaBinding, ...],
    absolute: Path,
) -> _AreaBinding | None:
    matches = [
        binding
        for binding in bindings
        if absolute == binding.root or binding.root in absolute.parents
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda binding: (
            len(binding.root.parts),
            binding.area.kind == "ops",
        ),
    )


def _locator_for_absolute(
    context: FileTargetContext,
    absolute: Path,
) -> FileLocator:
    container_root = context.container_root
    if absolute != container_root and container_root not in absolute.parents:
        raise FileTargetError("path escapes Container")
    binding = _authoritative_binding(context.bindings, absolute)
    if binding is None:
        area = FileArea(kind="container", id=None)
        root = container_root
    else:
        area = binding.area
        root = binding.root
    relative = "" if absolute == root else absolute.relative_to(root).as_posix()
    return FileLocator(
        project=context.project,
        area=area,
        path=normalize_relative_path(relative),
    )


def canonical_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    path: str = "",
    *,
    context: FileTargetContext | None = None,
) -> FileLocator:
    data = container_registry.get_container(conn, container)
    resolved_context = _context_for(conn, data, context)
    normalized = normalize_relative_path(path)
    try:
        absolute = fsapi.resolve_in_project(
            resolved_context.container_root,
            normalized,
        )
    except fsapi.FsError as exc:
        raise FileTargetError(str(exc)) from exc
    return _locator_for_absolute(resolved_context, absolute)


def resolve_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    locator: FileLocator | Mapping[str, Any] | str,
    *,
    context: FileTargetContext | None = None,
) -> ResolvedFile:
    data = container_registry.get_container(conn, container)
    resolved_context = _context_for(conn, data, context)
    target = parse_locator(locator)
    if target.project != data.get("slug"):
        raise FileTargetError("file target belongs to another Container")
    if target.area.kind == "container":
        root = resolved_context.container_root
    else:
        assert target.area.id is not None
        binding = next(
            (
                item
                for item in resolved_context.bindings
                if item.area.id == target.area.id
                and item.area.kind == target.area.kind
            ),
            None,
        )
        if binding is None:
            raise FileTargetError("Area is not active in this Container")
        root = binding.root
    try:
        absolute = fsapi.resolve_in_project(root, target.path)
    except fsapi.FsError as exc:
        raise FileTargetError(str(exc)) from exc
    authoritative = _authoritative_binding(resolved_context.bindings, absolute)
    authoritative_area = (
        authoritative.area
        if authoritative is not None
        else FileArea(kind="container", id=None)
    )
    if target.area != authoritative_area:
        raise FileTargetError("file target does not match the authoritative Area")
    return ResolvedFile(locator=target, root=root, path=absolute)


def resolve_from_root(
    context: FileTargetContext,
    root: Path,
    path: str,
) -> ResolvedFile:
    if root != context.container_root and all(
        root != binding.root for binding in context.bindings
    ):
        raise FileTargetError("scan root is not active in this Container")
    normalized = normalize_relative_path(path)
    try:
        absolute = fsapi.resolve_in_project(root, normalized)
    except fsapi.FsError as exc:
        raise FileTargetError(str(exc)) from exc
    locator = _locator_for_absolute(context, absolute)
    authoritative_root = context.root_for(locator.area)
    return ResolvedFile(
        locator=locator,
        root=authoritative_root,
        path=absolute,
    )


def legacy_locator(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    path: str,
    *,
    context: FileTargetContext | None = None,
) -> FileLocator:
    """Upgrade a documented path-only request without guessing from a basename.

    Reserved virtual Ops roots retain their historical mapping. An explicit
    ``ops/`` path upgrades to the Ops Area only for the physical ``ops`` layout.
    When the active legacy Ops Area is ``.``, the prefix remains literal
    Area-relative input so it cannot be stripped to a same-name root file.
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
    return canonical_locator(conn, data, normalized, context=context)


def resolve_request(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    *,
    path: str = "",
    target: Any = "",
    context: FileTargetContext | None = None,
) -> ResolvedFile:
    has_target = target is not None and target != ""
    locator = (
        parse_locator(target)
        if has_target
        else legacy_locator(conn, container, path, context=context)
    )
    return resolve_locator(conn, container, locator, context=context)


def add_targets(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    entries: list[dict[str, Any]],
    directory: Path,
    *,
    root: Path,
    context: FileTargetContext | None = None,
) -> list[dict[str, Any]]:
    data = container_registry.get_container(conn, container)
    resolved_context = _context_for(conn, data, context)
    try:
        relative_directory = (
            ""
            if directory == root
            else directory.relative_to(root).as_posix()
        )
    except ValueError as exc:
        raise FileTargetError("tree directory is outside its active root") from exc
    enriched: list[dict[str, Any]] = []
    for entry in entries:
        try:
            relative_path = normalize_relative_path(
                "/".join(
                    part
                    for part in (relative_directory, str(entry["name"]))
                    if part
                ),
                allow_empty=False,
            )
            resolved = resolve_from_root(
                resolved_context,
                root,
                relative_path,
            )
        except FileTargetError:
            continue
        if not resolved.path.exists():
            continue
        enriched.append(
            {
                **entry,
                "target": resolved.locator.payload(),
            }
        )
    return enriched


def add_artifact_targets(
    conn: sqlite3.Connection,
    container: int | sqlite3.Row | Mapping[str, Any],
    items: list[dict[str, Any]],
    *,
    context: FileTargetContext | None = None,
) -> list[dict[str, Any]]:
    data = container_registry.get_container(conn, container)
    resolved_context = _context_for(conn, data, context)
    ops_root = resolved_context.ops_root()
    enriched: list[dict[str, Any]] = []
    for item in items:
        try:
            resolved = resolve_from_root(
                resolved_context,
                ops_root,
                str(item.get("path") or ""),
            )
        except FileTargetError:
            continue
        enriched.append(
            {
                **item,
                "target": resolved.locator.payload(),
            }
        )
    return enriched

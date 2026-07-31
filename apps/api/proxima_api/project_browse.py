from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class DirectoryBrowseUnavailable(Exception):
    pass


class PathResolutionUnavailable(Exception):
    pass


class PathOutsideRoots(Exception):
    pass


class DirectoryComponentInvalid(Exception):
    pass


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve(value: str | Path) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathResolutionUnavailable("path is not reachable") from exc


def split_directory_target(value: str | Path) -> tuple[Path, str]:
    try:
        target = Path(value).expanduser()
    except (RuntimeError, ValueError) as exc:
        raise PathResolutionUnavailable("path is not reachable") from exc
    return target.parent, target.name


@dataclass(frozen=True)
class ResolvedAllowedPath:
    path: Path
    root: Path


@dataclass(frozen=True)
class AllowedRoots:
    paths: tuple[Path, ...]

    @classmethod
    def from_configured(cls, configured_roots: Iterable[str | Path]) -> AllowedRoots:
        roots: list[Path] = []
        for configured in configured_roots:
            try:
                root = _resolve(configured)
            except PathResolutionUnavailable:
                continue
            if root not in roots:
                roots.append(root)
        if not roots:
            raise PathResolutionUnavailable("No allowed folder root can be resolved")
        return cls(tuple(roots))

    def resolve(self, value: str | Path) -> ResolvedAllowedPath:
        path = _resolve(value)
        root = next((candidate for candidate in self.paths if _inside(path, candidate)), None)
        if root is None:
            raise PathOutsideRoots("path is outside the allowed roots")
        return ResolvedAllowedPath(path=path, root=root)


def validate_directory_component(parent: Path, name: str) -> None:
    try:
        encoded = os.fsencode(name)
    except UnicodeError as exc:
        raise DirectoryComponentInvalid("folder name cannot be encoded for this filesystem") from exc
    try:
        limit = os.pathconf(parent, "PC_NAME_MAX")
    except (AttributeError, ValueError):
        return
    if limit >= 0 and len(encoded) > limit:
        raise DirectoryComponentInvalid(
            f"folder name is too long for this location (maximum {limit} bytes)"
        )


def _candidates(requested: str, roots: AllowedRoots) -> list[ResolvedAllowedPath]:
    if requested:
        try:
            current = Path(requested).expanduser()
        except (RuntimeError, ValueError):
            current = None
        while current is not None:
            try:
                selected = roots.resolve(current)
            except (PathResolutionUnavailable, PathOutsideRoots):
                parent = current.parent
                current = None if parent == current else parent
                continue
            candidates: list[ResolvedAllowedPath] = []
            path = selected.path
            while True:
                candidates.append(ResolvedAllowedPath(path=path, root=selected.root))
                if path == selected.root:
                    return candidates
                path = path.parent
    return [ResolvedAllowedPath(path=root, root=root) for root in roots.paths]


def browse_directory(requested: str, configured_roots: Iterable[str | Path]) -> dict[str, object]:
    try:
        roots = AllowedRoots.from_configured(configured_roots)
    except PathResolutionUnavailable as exc:
        raise DirectoryBrowseUnavailable(
            "No readable folder is available inside the allowed roots"
        ) from exc

    for resolved_candidate in _candidates(requested, roots):
        candidate = resolved_candidate.path
        root = resolved_candidate.root
        try:
            if not candidate.is_dir():
                continue
            entries = list(candidate.iterdir())
        except OSError:
            continue

        dirs: list[dict[str, str]] = []
        for child in sorted(entries, key=lambda entry: entry.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                resolved = roots.resolve(child)
                if child.is_dir() and _inside(resolved.path, root):
                    dirs.append({"name": child.name, "path": str(child)})
            except (OSError, RuntimeError, PathResolutionUnavailable, PathOutsideRoots):
                continue

        parent = (
            str(candidate.parent)
            if candidate != root and _inside(candidate.parent, root)
            else None
        )
        return {
            "path": str(candidate),
            "parent": parent,
            "dirs": dirs,
            "roots": [str(item) for item in roots.paths],
        }

    raise DirectoryBrowseUnavailable("No readable folder is available inside the allowed roots")

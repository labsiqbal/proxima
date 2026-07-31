from __future__ import annotations

import errno
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class DirectoryBrowseUnavailable(Exception):
    pass


class PathResolutionUnavailable(Exception):
    pass


class ConfiguredRootUnavailable(PathResolutionUnavailable):
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


def _lexical(value: str | Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
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
class AllowedRoot:
    configured: Path
    resolved: Path | None


@dataclass(frozen=True)
class AllowedRoots:
    roots: tuple[AllowedRoot, ...]

    @classmethod
    def from_configured(cls, configured_roots: Iterable[str | Path]) -> AllowedRoots:
        roots: list[AllowedRoot] = []
        for configured in configured_roots:
            identity = _lexical(configured)
            if any(root.configured == identity for root in roots):
                continue
            try:
                resolved = _resolve(configured)
            except PathResolutionUnavailable:
                resolved = None
            roots.append(AllowedRoot(configured=identity, resolved=resolved))
        if not roots:
            raise PathResolutionUnavailable("No allowed folder root is configured")
        return cls(tuple(roots))

    @property
    def configured_paths(self) -> tuple[Path, ...]:
        return tuple(root.configured for root in self.roots)

    @property
    def available(self) -> tuple[AllowedRoot, ...]:
        return tuple(root for root in self.roots if root.resolved is not None)

    def _lexical_owner(self, path: Path) -> AllowedRoot | None:
        owners = [root for root in self.roots if _inside(path, root.configured)]
        return max(owners, key=lambda root: len(root.configured.parts), default=None)

    def resolve(self, value: str | Path) -> ResolvedAllowedPath:
        lexical = _lexical(value)
        owner = self._lexical_owner(lexical)
        if owner is not None and owner.resolved is None:
            raise ConfiguredRootUnavailable("configured folder root is not reachable")
        path = _resolve(value)
        if owner is not None:
            assert owner.resolved is not None
            if not _inside(path, owner.resolved):
                raise PathOutsideRoots("path is outside the allowed roots")
            return ResolvedAllowedPath(path=path, root=owner.resolved)
        resolved_owner = next(
            (
                candidate
                for candidate in self.available
                if candidate.resolved is not None and _inside(path, candidate.resolved)
            ),
            None,
        )
        if resolved_owner is None or resolved_owner.resolved is None:
            raise PathOutsideRoots("path is outside the allowed roots")
        return ResolvedAllowedPath(path=path, root=resolved_owner.resolved)


def _validate_encoded_component(name: str, limit: int) -> None:
    try:
        encoded = os.fsencode(name)
    except UnicodeError as exc:
        raise DirectoryComponentInvalid("folder name cannot be encoded for this filesystem") from exc
    if limit >= 0 and len(encoded) > limit:
        raise DirectoryComponentInvalid(
            f"folder name is too long for this location (maximum {limit} bytes)"
        )


def create_directory_component(parent: Path, name: str, mode: int = 0o755) -> Path:
    flags = os.O_RDONLY
    for flag_name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, flag_name, 0)
    parent_fd = os.open(parent, flags)
    try:
        try:
            limit = os.fpathconf(parent_fd, "PC_NAME_MAX")
        except (AttributeError, OSError, ValueError):
            limit = -1
        _validate_encoded_component(name, limit)
        try:
            os.mkdir(name, mode=mode, dir_fd=parent_fd)
        except UnicodeError as exc:
            raise DirectoryComponentInvalid(
                "folder name cannot be encoded for this filesystem"
            ) from exc
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG:
                raise DirectoryComponentInvalid(
                    "folder name is too long for this location"
                ) from exc
            raise
    finally:
        os.close(parent_fd)
    return parent / name


def _candidates(requested: str, roots: AllowedRoots) -> list[ResolvedAllowedPath]:
    if requested:
        try:
            current = Path(requested).expanduser()
        except (RuntimeError, ValueError):
            current = None
        while current is not None:
            try:
                selected = roots.resolve(current)
            except ConfiguredRootUnavailable:
                raise
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
    return [
        ResolvedAllowedPath(path=root.resolved, root=root.resolved)
        for root in roots.available
        if root.resolved is not None
    ]


def browse_directory(requested: str, configured_roots: Iterable[str | Path]) -> dict[str, object]:
    try:
        roots = AllowedRoots.from_configured(configured_roots)
    except PathResolutionUnavailable as exc:
        raise DirectoryBrowseUnavailable(
            "No readable folder is available inside the allowed roots"
        ) from exc

    try:
        candidates = _candidates(requested, roots)
    except ConfiguredRootUnavailable as exc:
        raise DirectoryBrowseUnavailable(
            "Selected folder root is not reachable"
        ) from exc

    for resolved_candidate in candidates:
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
            "roots": [str(item) for item in roots.configured_paths],
        }

    raise DirectoryBrowseUnavailable("No readable folder is available inside the allowed roots")

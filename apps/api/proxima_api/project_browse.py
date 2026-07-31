from __future__ import annotations

import errno
import os
import stat
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


def _raw_path(value: str | Path) -> Path | None:
    try:
        return Path(os.path.normpath(os.fsdecode(os.fspath(value))))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


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
    raw: str
    raw_path: Path | None
    configured: Path | None
    resolved: Path | None

    @property
    def label(self) -> str:
        return str(self.configured) if self.configured is not None else self.raw


@dataclass(frozen=True)
class AllowedRoots:
    roots: tuple[AllowedRoot, ...]

    @classmethod
    def from_configured(cls, configured_roots: Iterable[str | Path]) -> AllowedRoots:
        roots: list[AllowedRoot] = []
        for configured in configured_roots:
            try:
                raw = os.fsdecode(os.fspath(configured))
            except (OSError, TypeError, ValueError):
                raw = str(configured)
            raw_path = _raw_path(raw)
            try:
                identity = _lexical(raw)
            except PathResolutionUnavailable:
                identity = None
            duplicate = any(
                (identity is not None and root.configured == identity)
                or (identity is None and root.configured is None and root.raw == raw)
                for root in roots
            )
            if duplicate:
                continue
            resolved = None
            if identity is not None:
                try:
                    resolved = _resolve(raw)
                except PathResolutionUnavailable:
                    pass
            roots.append(
                AllowedRoot(
                    raw=raw,
                    raw_path=raw_path,
                    configured=identity,
                    resolved=resolved,
                )
            )
        if not roots:
            raise PathResolutionUnavailable("No allowed folder root is configured")
        return cls(tuple(roots))

    @property
    def configured_paths(self) -> tuple[str, ...]:
        return tuple(root.label for root in self.roots)

    @property
    def available(self) -> tuple[AllowedRoot, ...]:
        return tuple(root for root in self.roots if root.resolved is not None)

    def owner(self, value: str | Path) -> AllowedRoot | None:
        raw_path = _raw_path(value)
        try:
            lexical = _lexical(value)
        except PathResolutionUnavailable:
            lexical = None
        owners: list[tuple[int, AllowedRoot]] = []
        for root in self.roots:
            depth = -1
            if (
                raw_path is not None
                and root.raw_path is not None
                and _inside(raw_path, root.raw_path)
            ):
                depth = max(depth, len(root.raw_path.parts))
            if (
                lexical is not None
                and root.configured is not None
                and _inside(lexical, root.configured)
            ):
                depth = max(depth, len(root.configured.parts))
            if depth >= 0:
                owners.append((depth, root))
        if not owners:
            return None
        return max(owners, key=lambda item: item[0])[1]

    def require_available(self, root: AllowedRoot) -> Path:
        if root.configured is None or root.resolved is None:
            raise ConfiguredRootUnavailable("configured folder root is not reachable")
        try:
            current = _resolve(root.configured)
        except PathResolutionUnavailable as exc:
            raise ConfiguredRootUnavailable(
                "configured folder root is not reachable"
            ) from exc
        if current != root.resolved:
            raise ConfiguredRootUnavailable("configured folder root changed")
        return root.resolved

    def resolve_for_owner(
        self,
        value: str | Path,
        owner: AllowedRoot,
    ) -> ResolvedAllowedPath:
        root = self.require_available(owner)
        path = _resolve(value)
        if not _inside(path, root):
            raise PathOutsideRoots("path is outside the allowed roots")
        return ResolvedAllowedPath(path=path, root=root)

    def resolve(self, value: str | Path) -> ResolvedAllowedPath:
        owner = self.owner(value)
        if owner is not None:
            return self.resolve_for_owner(value, owner)
        path = _resolve(value)
        resolved_owners = [
            candidate
            for candidate in self.available
            if candidate.resolved is not None and _inside(path, candidate.resolved)
        ]
        resolved_owner = max(
            resolved_owners,
            key=lambda candidate: len(candidate.resolved.parts)
            if candidate.resolved is not None
            else -1,
            default=None,
        )
        if resolved_owner is None:
            raise PathOutsideRoots("path is outside the allowed roots")
        root = self.require_available(resolved_owner)
        return ResolvedAllowedPath(path=path, root=root)


def _validate_encoded_component(name: str, limit: int) -> None:
    try:
        encoded = os.fsencode(name)
    except UnicodeError as exc:
        raise DirectoryComponentInvalid("folder name cannot be encoded for this filesystem") from exc
    if limit >= 0 and len(encoded) > limit:
        raise DirectoryComponentInvalid(
            f"folder name is too long for this location (maximum {limit} bytes)"
        )


def _directory_flags() -> int:
    flags = os.O_RDONLY
    for flag_name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, flag_name, 0)
    return flags


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise PathResolutionUnavailable("path is not absolute")
    flags = _directory_flags()
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_directory_under_root(root: Path, path: Path) -> int:
    if not _inside(path, root):
        raise PathOutsideRoots("path is outside the allowed roots")
    descriptor = _open_absolute_directory(root)
    try:
        for component in path.relative_to(root).parts:
            next_descriptor = os.open(
                component,
                _directory_flags(),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@dataclass
class CreatedDirectory:
    path: Path
    root: Path
    name: str
    parent_fd: int
    directory_fd: int
    closed: bool = False

    def _entry_is_owned(self) -> bool:
        try:
            entry = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
            directory = os.fstat(self.directory_fd)
        except OSError:
            return False
        return (entry.st_dev, entry.st_ino) == (directory.st_dev, directory.st_ino)

    def require_visible(self) -> Path:
        if self.closed or not self._entry_is_owned():
            raise PathResolutionUnavailable("created folder is not reachable")
        descriptor = _open_directory_under_root(self.root, self.path)
        try:
            visible = os.fstat(descriptor)
            created = os.fstat(self.directory_fd)
        finally:
            os.close(descriptor)
        if (visible.st_dev, visible.st_ino) != (created.st_dev, created.st_ino):
            raise PathResolutionUnavailable("created folder changed")
        return self.path

    def commit(self) -> None:
        self.require_visible()
        self._close()

    def rollback(self) -> None:
        if self.closed:
            return
        try:
            if self._entry_is_owned():
                try:
                    os.rmdir(self.name, dir_fd=self.parent_fd)
                except OSError:
                    pass
        finally:
            self._close()

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            os.close(self.directory_fd)
        finally:
            os.close(self.parent_fd)


def create_directory_component(
    parent: ResolvedAllowedPath,
    name: str,
    mode: int = 0o755,
) -> CreatedDirectory:
    parent_fd = _open_directory_under_root(parent.root, parent.path)
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
        try:
            directory_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except BaseException as exc:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                pass
            if isinstance(exc, UnicodeError):
                raise DirectoryComponentInvalid(
                    "folder name cannot be encoded for this filesystem"
                ) from exc
            if isinstance(exc, OSError) and exc.errno == errno.ENAMETOOLONG:
                raise DirectoryComponentInvalid(
                    "folder name is too long for this location"
                ) from exc
            raise
    except BaseException:
        os.close(parent_fd)
        raise
    return CreatedDirectory(
        path=parent.path / name,
        root=parent.root,
        name=name,
        parent_fd=parent_fd,
        directory_fd=directory_fd,
    )


def _candidates(requested: str, roots: AllowedRoots) -> list[ResolvedAllowedPath]:
    if requested:
        owner = roots.owner(requested)
        if owner is None:
            return _available_root_candidates(roots)
        root = roots.require_available(owner)
        try:
            current = _lexical(requested)
        except PathResolutionUnavailable as exc:
            raise ConfiguredRootUnavailable(
                "configured folder root is not reachable"
            ) from exc
        assert owner.configured is not None
        while _inside(current, owner.configured):
            try:
                selected = roots.resolve_for_owner(current, owner)
            except (PathResolutionUnavailable, PathOutsideRoots):
                parent = current.parent
                if parent == current:
                    break
                current = parent
                continue
            candidates: list[ResolvedAllowedPath] = []
            path = selected.path
            while True:
                candidates.append(ResolvedAllowedPath(path=path, root=root))
                if path == root:
                    return candidates
                path = path.parent
        return []
    return _available_root_candidates(roots)


def _available_root_candidates(roots: AllowedRoots) -> list[ResolvedAllowedPath]:
    candidates: list[ResolvedAllowedPath] = []
    for allowed_root in roots.available:
        try:
            root = roots.require_available(allowed_root)
        except ConfiguredRootUnavailable:
            continue
        candidates.append(ResolvedAllowedPath(path=root, root=root))
    return candidates


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
        descriptor = None
        try:
            descriptor = _open_directory_under_root(root, candidate)
            entries = os.listdir(descriptor)
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            continue

        dirs: list[dict[str, str]] = []
        try:
            for child_name in sorted(entries, key=str.lower):
                if child_name.startswith("."):
                    continue
                try:
                    child_stat = os.stat(
                        child_name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError:
                    continue
                if stat.S_ISDIR(child_stat.st_mode):
                    dirs.append(
                        {
                            "name": child_name,
                            "path": str(candidate / child_name),
                        }
                    )
        finally:
            os.close(descriptor)

        parent = (
            str(candidate.parent)
            if candidate != root and _inside(candidate.parent, root)
            else None
        )
        return {
            "path": str(candidate),
            "parent": parent,
            "dirs": dirs,
            "roots": list(roots.configured_paths),
        }

    raise DirectoryBrowseUnavailable("No readable folder is available inside the allowed roots")

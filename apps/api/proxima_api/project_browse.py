from __future__ import annotations

import errno
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .directory_handles import (
    DirectoryHandle,
    DirectoryNameError,
    directory_backend,
)


_backend = directory_backend()


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


class DirectoryPublishConflict(Exception):
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
    root_id: str
    root_identity: str


@dataclass(frozen=True)
class AllowedRoot:
    id: str
    raw: str
    raw_path: Path | None
    configured: Path | None
    resolved: Path | None
    resolved_identity: str | None

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
            resolved_identity = None
            if identity is not None:
                try:
                    resolved = _resolve(raw)
                    handle = _open_absolute_directory(resolved)
                    try:
                        resolved_identity = handle.identity
                    finally:
                        _backend.close(handle)
                except (OSError, PathResolutionUnavailable):
                    resolved = None
            roots.append(
                AllowedRoot(
                    id=hashlib.sha256(
                        raw.encode("utf-8", "surrogatepass")
                        + b"\0"
                        + (resolved_identity or "unavailable").encode("ascii")
                    ).hexdigest()[:24],
                    raw=raw,
                    raw_path=raw_path,
                    configured=identity,
                    resolved=resolved,
                    resolved_identity=resolved_identity,
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
        return tuple(
            root
            for root in self.roots
            if root.resolved is not None and root.resolved_identity is not None
        )

    def by_id(self, root_id: str) -> AllowedRoot:
        root = next((candidate for candidate in self.roots if candidate.id == root_id), None)
        if root is None:
            raise ConfiguredRootUnavailable("configured folder root identity is invalid")
        return root

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
        if (
            root.configured is None
            or root.resolved is None
            or root.resolved_identity is None
        ):
            raise ConfiguredRootUnavailable("configured folder root is not reachable")
        try:
            current = _resolve(root.configured)
        except PathResolutionUnavailable as exc:
            raise ConfiguredRootUnavailable(
                "configured folder root is not reachable"
            ) from exc
        if current != root.resolved:
            raise ConfiguredRootUnavailable("configured folder root changed")
        try:
            handle = _open_absolute_directory(current)
        except (OSError, PathResolutionUnavailable) as exc:
            raise ConfiguredRootUnavailable(
                "configured folder root is not reachable"
            ) from exc
        try:
            if handle.identity != root.resolved_identity:
                raise ConfiguredRootUnavailable("configured folder root changed")
        finally:
            _backend.close(handle)
        return root.resolved

    def select_owner(self, value: str | Path) -> AllowedRoot | None:
        lexical_owner = self.owner(value)
        if lexical_owner is not None:
            self.require_available(lexical_owner)
        try:
            lexical = _lexical(value)
        except PathResolutionUnavailable:
            return lexical_owner
        if (
            lexical_owner is not None
            and lexical_owner.configured is not None
            and lexical_owner.resolved is not None
            and lexical_owner.configured != lexical_owner.resolved
            and _inside(lexical, lexical_owner.configured)
        ):
            return lexical_owner
        resolved_owners = [
            candidate
            for candidate in self.available
            if candidate.resolved is not None
            and _inside(lexical, candidate.resolved)
        ]
        if not resolved_owners:
            return lexical_owner
        deepest_depth = max(
            len(candidate.resolved.parts)
            for candidate in resolved_owners
            if candidate.resolved is not None
        )
        deepest = [
            candidate
            for candidate in resolved_owners
            if candidate.resolved is not None
            and len(candidate.resolved.parts) == deepest_depth
        ]
        if len(deepest) == 1:
            return deepest[0]
        raise ConfiguredRootUnavailable(
            "configured folder root identity is required"
        )

    def resolve_for_owner(
        self,
        value: str | Path,
        owner: AllowedRoot,
    ) -> ResolvedAllowedPath:
        root = self.require_available(owner)
        path = _resolve(value)
        if not _inside(path, root):
            raise PathOutsideRoots("path is outside the allowed roots")
        assert owner.resolved_identity is not None
        return ResolvedAllowedPath(
            path=path,
            root=root,
            root_id=owner.id,
            root_identity=owner.resolved_identity,
        )

    def resolve(
        self,
        value: str | Path,
        root_id: str | None = None,
    ) -> ResolvedAllowedPath:
        if root_id:
            return self.resolve_for_owner(value, self.by_id(root_id))
        owner = self.select_owner(value)
        if owner is not None:
            return self.resolve_for_owner(value, owner)
        raise PathOutsideRoots("path is outside the allowed roots")


def _validate_encoded_component(name: str, limit: int) -> None:
    try:
        size = _backend.component_size(name)
    except UnicodeError as exc:
        raise DirectoryComponentInvalid("folder name cannot be encoded for this filesystem") from exc
    if limit >= 0 and size > limit:
        raise DirectoryComponentInvalid(
            f"folder name is too long for this location (maximum {limit} bytes)"
        )


def _open_absolute_directory(path: Path) -> DirectoryHandle:
    try:
        return _backend.open_absolute(path)
    except PermissionError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathResolutionUnavailable("path is not reachable") from exc


def _open_directory_under_root(
    root: Path,
    path: Path,
    root_identity: str,
) -> DirectoryHandle:
    if not _inside(path, root):
        raise PathOutsideRoots("path is outside the allowed roots")
    handle = _open_absolute_directory(root)
    if handle.identity != root_identity:
        _backend.close(handle)
        raise ConfiguredRootUnavailable("configured folder root changed")
    try:
        for component in path.relative_to(root).parts:
            next_handle = _backend.open_child(handle, component)
            _backend.close(handle)
            handle = next_handle
    except (FileNotFoundError, PermissionError):
        _backend.close(handle)
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        _backend.close(handle)
        raise PathResolutionUnavailable("path is not reachable") from exc
    return handle


def directory_identity(path: ResolvedAllowedPath) -> str:
    handle = _open_directory_under_root(
        path.root,
        path.path,
        path.root_identity,
    )
    try:
        return handle.identity
    finally:
        _backend.close(handle)


@dataclass
class CreatedDirectory:
    path: Path
    root: Path
    root_identity: str
    name: str
    staging_name: str
    parent_handle: DirectoryHandle
    directory_handle: DirectoryHandle
    published: bool = False
    closed: bool = False

    def _entry_is_owned(self) -> bool:
        return _backend.entry_is_owned(
            self.parent_handle,
            self.name if self.published else self.staging_name,
            self.directory_handle,
        )

    @property
    def identity(self) -> str:
        return self.directory_handle.identity

    def require_staged(self) -> Path:
        if self.closed or not self._entry_is_owned():
            raise PathResolutionUnavailable("created folder is not reachable")
        return self.path

    def commit(self) -> None:
        if self.closed:
            raise PathResolutionUnavailable("created folder is not reachable")
        try:
            _backend.publish(
                self.parent_handle,
                self.directory_handle,
                self.staging_name,
                self.name,
            )
        except FileExistsError as exc:
            raise DirectoryPublishConflict(
                "a folder with that name already exists"
            ) from exc
        except DirectoryNameError as exc:
            raise DirectoryComponentInvalid(
                "folder name is invalid for this location"
            ) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            if isinstance(exc, OSError) and exc.errno == errno.ENAMETOOLONG:
                raise DirectoryComponentInvalid(
                    "folder name is too long for this location"
                ) from exc
            raise PathResolutionUnavailable("created folder is not reachable") from exc
        self.published = True
        try:
            visible = _open_directory_under_root(
                self.root,
                self.path,
                self.root_identity,
            )
        except (OSError, PathOutsideRoots, PathResolutionUnavailable) as exc:
            raise PathResolutionUnavailable(
                "created folder is not reachable"
            ) from exc
        try:
            if visible.identity != self.identity:
                raise PathResolutionUnavailable(
                    "created folder identity changed"
                )
        finally:
            _backend.close(visible)

    def finish(self) -> None:
        self._close()

    def rollback(self) -> None:
        if self.closed:
            return
        try:
            try:
                _backend.remove_owned(
                    self.parent_handle,
                    self.name if self.published else self.staging_name,
                    self.directory_handle,
                )
            except OSError:
                pass
        finally:
            self._close()

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            _backend.close(self.directory_handle)
        finally:
            _backend.close(self.parent_handle)


def create_directory_component(
    parent: ResolvedAllowedPath,
    name: str,
    mode: int = 0o755,
) -> CreatedDirectory:
    parent_handle = _open_directory_under_root(
        parent.root,
        parent.path,
        parent.root_identity,
    )
    try:
        limit = _backend.component_limit(parent_handle)
        _validate_encoded_component(name, limit)
        try:
            staging_name, directory_handle = _backend.create_staging(
                parent_handle,
                mode,
            )
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
    except BaseException:
        _backend.close(parent_handle)
        raise
    return CreatedDirectory(
        path=parent.path / name,
        root=parent.root,
        root_identity=parent.root_identity,
        name=name,
        staging_name=staging_name,
        parent_handle=parent_handle,
        directory_handle=directory_handle,
    )


def _candidates(
    requested: str,
    roots: AllowedRoots,
    root_id: str | None,
) -> list[ResolvedAllowedPath]:
    if requested:
        if not root_id:
            raise ConfiguredRootUnavailable(
                "configured folder root identity is required"
            )
        owner = roots.by_id(root_id)
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
        boundary = root if _inside(current, root) else owner.configured
        while _inside(current, boundary):
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
                candidates.append(
                    ResolvedAllowedPath(
                        path=path,
                        root=root,
                        root_id=owner.id,
                        root_identity=selected.root_identity,
                    )
                )
                if path == root:
                    return candidates
                path = path.parent
        return []
    if root_id:
        owner = roots.by_id(root_id)
        root = roots.require_available(owner)
        assert owner.resolved_identity is not None
        return [
            ResolvedAllowedPath(
                path=root,
                root=root,
                root_id=owner.id,
                root_identity=owner.resolved_identity,
            )
        ]
    return _available_root_candidates(roots)


def _available_root_candidates(roots: AllowedRoots) -> list[ResolvedAllowedPath]:
    candidates: list[ResolvedAllowedPath] = []
    for allowed_root in roots.available:
        try:
            root = roots.require_available(allowed_root)
        except ConfiguredRootUnavailable:
            continue
        assert allowed_root.resolved_identity is not None
        candidates.append(
            ResolvedAllowedPath(
                path=root,
                root=root,
                root_id=allowed_root.id,
                root_identity=allowed_root.resolved_identity,
            )
        )
    return candidates


def browse_directory(
    requested: str,
    configured_roots: Iterable[str | Path],
    root_id: str | None = None,
) -> dict[str, object]:
    try:
        roots = AllowedRoots.from_configured(configured_roots)
    except PathResolutionUnavailable as exc:
        raise DirectoryBrowseUnavailable(
            "No readable folder is available inside the allowed roots"
        ) from exc

    try:
        candidates = _candidates(requested, roots, root_id)
    except ConfiguredRootUnavailable as exc:
        raise DirectoryBrowseUnavailable(
            "Selected folder root is not reachable"
        ) from exc

    for resolved_candidate in candidates:
        candidate = resolved_candidate.path
        root = resolved_candidate.root
        handle = None
        try:
            handle = _open_directory_under_root(
                root,
                candidate,
                resolved_candidate.root_identity,
            )
            entries = _backend.list_names(handle)
        except (OSError, PathResolutionUnavailable):
            if handle is not None:
                _backend.close(handle)
            continue

        dirs: list[dict[str, str]] = []
        try:
            for child_name in sorted(entries, key=str.lower):
                if child_name.startswith("."):
                    continue
                try:
                    child = _backend.open_child(handle, child_name)
                except OSError:
                    continue
                try:
                    dirs.append(
                        {
                            "name": child_name,
                            "path": str(candidate / child_name),
                        }
                    )
                finally:
                    _backend.close(child)
        finally:
            _backend.close(handle)

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
            "root_id": resolved_candidate.root_id,
        }

    raise DirectoryBrowseUnavailable("No readable folder is available inside the allowed roots")

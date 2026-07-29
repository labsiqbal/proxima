from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .durability import fsync_directory, write_all


class TreeError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedTree:
    release_id: str | None
    commit: str
    file_digests: tuple[tuple[str, str], ...]
    file_modes: tuple[tuple[str, int], ...] = ()
    symlink_targets: tuple[tuple[str, str], ...] = ()

    def files(self) -> dict[str, str]:
        return dict(self.file_digests)

    def modes(self) -> dict[str, int]:
        if self.file_modes:
            return dict(self.file_modes)
        return {
            relpath: release_file_mode(relpath)
            for relpath, _digest in self.file_digests
        }

    def symlinks(self) -> dict[str, str]:
        return dict(self.symlink_targets)


def _safe_component(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value


def _safe_relpath(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and all(_safe_component(part) for part in path.parts)
        and not path.is_absolute()
        and path.as_posix() == value
    )


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if (
        os.name != "posix"
        or not nofollow
        or not directory
        or os.open not in os.supports_dir_fd
        or os.scandir not in os.supports_fd
    ):
        raise TreeError("pinned candidate traversal is unavailable")
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def _same_entry(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat.S_IFMT(before.st_mode) == stat.S_IFMT(after.st_mode)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _open_pinned_directory(root: Path) -> int:
    flags = _directory_flags()
    absolute = Path(os.path.abspath(root))
    try:
        descriptor = os.open(absolute.anchor or os.sep, flags)
    except OSError as exc:
        raise TreeError("candidate tree ancestry cannot be pinned") from exc
    try:
        for component in absolute.parts[1:]:
            if not _safe_component(component):
                raise TreeError("candidate tree ancestry is invalid")
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise TreeError(
                    "candidate tree ancestry contains a symlink or substitution"
                ) from exc
            os.close(descriptor)
            descriptor = child
        value = os.fstat(descriptor)
        if not stat.S_ISDIR(value.st_mode):
            raise TreeError("candidate tree root must be a real directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _normalized_link(prefix: tuple[str, ...], target: str) -> tuple[str, ...]:
    if not target or "\0" in target or PurePosixPath(target).is_absolute():
        raise TreeError("candidate tree symlink target is unsafe")
    parts = list(prefix)
    for component in PurePosixPath(target).parts:
        if component in {"", "."}:
            continue
        if component == "..":
            if not parts:
                raise TreeError("candidate tree symlink escapes its root")
            parts.pop()
            continue
        if not _safe_component(component):
            raise TreeError("candidate tree symlink target is unsafe")
        parts.append(component)
    if not parts:
        raise TreeError("candidate tree symlink target is unsafe")
    return tuple(parts)


def _open_relative_regular(
    root_descriptor: int,
    parts: tuple[str, ...],
) -> tuple[int, os.stat_result]:
    directory_descriptor = os.dup(root_descriptor)
    try:
        for component in parts[:-1]:
            try:
                child = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise TreeError("candidate tree symlink target is unsafe") from exc
            os.close(directory_descriptor)
            directory_descriptor = child
        try:
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_BINARY", 0),
                dir_fd=directory_descriptor,
            )
        except OSError as exc:
            raise TreeError("candidate tree symlink target is unsafe") from exc
    finally:
        os.close(directory_descriptor)
    value = os.fstat(descriptor)
    if not stat.S_ISREG(value.st_mode):
        os.close(descriptor)
        raise TreeError("candidate tree symlink target must be a regular file")
    return descriptor, value


def _walk_files(
    root: Path,
    visit: Callable[[str, int, os.stat_result, str | None], None],
    *,
    allow_file_symlinks: bool = False,
    excluded: Callable[[str, bool], bool] | None = None,
) -> None:
    root_descriptor = _open_pinned_directory(root)
    root_stat = os.fstat(root_descriptor)

    def walk(directory_descriptor: int, prefix: tuple[str, ...]) -> None:
        try:
            with os.scandir(directory_descriptor) as iterator:
                entries = sorted(
                    (
                        entry.name,
                        entry.stat(follow_symlinks=False),
                    )
                    for entry in iterator
                )
        except OSError as exc:
            raise TreeError("candidate tree cannot be enumerated") from exc
        for name, entry_stat in entries:
            if not _safe_component(name):
                raise TreeError("candidate tree entry name is invalid")
            relpath = "/".join((*prefix, name))
            is_directory = stat.S_ISDIR(entry_stat.st_mode)
            if excluded is not None and excluded(relpath, is_directory):
                continue
            if stat.S_ISLNK(entry_stat.st_mode):
                if not allow_file_symlinks:
                    raise TreeError("candidate tree contains a symlink")
                try:
                    target = os.readlink(name, dir_fd=directory_descriptor)
                except OSError as exc:
                    raise TreeError("candidate tree symlink cannot be read") from exc
                target_parts = _normalized_link(prefix, target)
                target_descriptor, target_stat = _open_relative_regular(
                    root_descriptor,
                    target_parts,
                )
                try:
                    visit(relpath, target_descriptor, target_stat, target)
                    if not _same_entry(target_stat, os.fstat(target_descriptor)):
                        raise TreeError("candidate tree changed during verification")
                    current = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        not _same_entry(entry_stat, current)
                        or os.readlink(name, dir_fd=directory_descriptor) != target
                    ):
                        raise TreeError("candidate tree symlink was substituted")
                finally:
                    os.close(target_descriptor)
                continue
            if is_directory:
                try:
                    child_descriptor = os.open(
                        name,
                        _directory_flags(),
                        dir_fd=directory_descriptor,
                    )
                except OSError as exc:
                    raise TreeError(
                        "candidate tree directory was substituted"
                    ) from exc
                try:
                    opened = os.fstat(child_descriptor)
                    if not _same_entry(entry_stat, opened):
                        raise TreeError("candidate tree directory was substituted")
                    walk(child_descriptor, (*prefix, name))
                    if not _same_entry(opened, os.fstat(child_descriptor)):
                        raise TreeError("candidate tree changed during verification")
                finally:
                    os.close(child_descriptor)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise TreeError("candidate tree contains a non-regular file")
            try:
                file_descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_BINARY", 0),
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise TreeError("candidate tree file was substituted") from exc
            try:
                opened = os.fstat(file_descriptor)
                if not stat.S_ISREG(opened.st_mode) or not _same_entry(
                    entry_stat,
                    opened,
                ):
                    raise TreeError("candidate tree file was substituted")
                visit(relpath, file_descriptor, opened, None)
                if not _same_entry(opened, os.fstat(file_descriptor)):
                    raise TreeError("candidate tree changed during verification")
            finally:
                os.close(file_descriptor)

    try:
        walk(root_descriptor, ())
        if not _same_entry(root_stat, os.fstat(root_descriptor)):
            raise TreeError("candidate tree changed during verification")
    finally:
        os.close(root_descriptor)


def _walk_regular_files(
    root: Path,
    visit: Callable[[str, int, os.stat_result], None],
) -> None:
    _walk_files(
        root,
        lambda relpath, descriptor, value, _target: visit(
            relpath,
            descriptor,
            value,
        ),
    )


def regular_file_digests(root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}

    def hash_file(relpath: str, descriptor: int, _value: os.stat_result) -> None:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        digests[relpath] = digest.hexdigest()

    _walk_regular_files(root, hash_file)
    return dict(sorted(digests.items()))


def _normalized_file_mode(value: os.stat_result) -> int:
    return 0o555 if value.st_mode & 0o111 else 0o444


def regular_file_modes(root: Path) -> dict[str, int]:
    modes: dict[str, int] = {}

    def record(relpath: str, _descriptor: int, value: os.stat_result) -> None:
        modes[relpath] = _normalized_file_mode(value)

    _walk_regular_files(root, record)
    return dict(sorted(modes.items()))


_SOURCE_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)
_SOURCE_EXCLUDED_PATHS = frozenset({"apps/web/dist"})


def _source_excluded(relpath: str, _is_directory: bool) -> bool:
    path = PurePosixPath(relpath)
    return (
        relpath in _SOURCE_EXCLUDED_PATHS
        or any(part in _SOURCE_EXCLUDED_NAMES for part in path.parts)
    )


def source_tree_metadata(
    root: Path,
) -> tuple[dict[str, str], dict[str, int], dict[str, str]]:
    digests: dict[str, str] = {}
    modes: dict[str, int] = {}
    links: dict[str, str] = {}

    def record(
        relpath: str,
        descriptor: int,
        value: os.stat_result,
        target: str | None,
    ) -> None:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        digests[relpath] = digest.hexdigest()
        modes[relpath] = 0o444 if target is not None else _normalized_file_mode(value)
        if target is not None:
            links[relpath] = target

    _walk_files(
        root,
        record,
        allow_file_symlinks=True,
        excluded=_source_excluded,
    )
    return (
        dict(sorted(digests.items())),
        dict(sorted(modes.items())),
        dict(sorted(links.items())),
    )


def release_file_mode(relpath: str) -> int:
    path = PurePosixPath(relpath)
    executable = (
        path.parts[:4] == ("apps", "api", ".venv", "bin")
        and path.name.startswith("python")
    )
    return 0o555 if executable else 0o444


def materialize_build_symlinks(
    root: Path,
    *,
    external_executables: Sequence[Path] = (),
) -> None:
    root = root.resolve()
    external_roots = (Path("/usr/bin"), Path("/usr/local/bin"))
    allowed_external = {
        path.absolute()
        for path in external_executables
    }
    replacements = 0
    while True:
        directory_links: list[Path] = []
        file_links: list[Path] = []
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            directory_links.extend(
                current_path / name
                for name in directories
                if (current_path / name).is_symlink()
            )
            file_links.extend(
                current_path / name
                for name in files
                if (current_path / name).is_symlink()
            )
        if not directory_links and not file_links:
            return
        replacements += len(directory_links) + len(file_links)
        if replacements > 100_000:
            raise TreeError("candidate build contains too many symlinks")
        for path in directory_links:
            target = path.resolve(strict=True)
            if (
                root not in target.parents
                or not target.is_dir()
                or target == path.parent
                or target in path.parents
            ):
                raise TreeError("candidate build directory symlink target is unsafe")
            path.unlink()
            shutil.copytree(target, path, symlinks=True)
            fsync_directory(path.parent)
        for path in file_links:
            if not path.is_symlink():
                continue
            relpath = path.relative_to(root).as_posix()
            target = path.resolve(strict=True)
            internal = target == root or root in target.parents
            external_python = (
                (
                    PurePosixPath(relpath).parts[:4]
                    == ("apps", "api", ".venv", "bin")
                    and PurePosixPath(relpath).name.startswith("python")
                )
                or path.absolute() in allowed_external
            ) and (
                target.parent in external_roots
                and target.name.startswith("python")
            )
            if (
                (not internal and not external_python)
                or not target.is_file()
                or target.is_symlink()
            ):
                raise TreeError("candidate build symlink target is unsafe")
            payload = target.read_bytes()
            path.unlink()
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0),
                0o700 if target.stat().st_mode & 0o111 else 0o600,
            )
            try:
                write_all(descriptor, payload)
                os.fchmod(
                    descriptor,
                    0o700 if target.stat().st_mode & 0o111 else 0o600,
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            fsync_directory(path.parent)


def copy_regular_tree(
    source: Path,
    destination: Path,
    expected: Mapping[str, str],
    modes: Mapping[str, int] | None = None,
) -> None:
    expected_files = dict(expected)
    expected_modes = (
        dict(modes)
        if modes is not None
        else {
            relpath: release_file_mode(relpath)
            for relpath in expected_files
        }
    )
    if not expected_files or any(
        not _safe_relpath(path)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for path, digest in expected_files.items()
    ) or set(expected_modes) != set(expected_files) or any(
        mode not in {0o444, 0o555}
        for mode in expected_modes.values()
    ):
        raise TreeError("verified candidate digest map is invalid")

    destination_root = destination.absolute()
    binary = getattr(os, "O_BINARY", 0)
    directories = {destination_root}
    copied: set[str] = set()

    def copy_file(
        relpath: str,
        source_descriptor: int,
        _value: os.stat_result,
    ) -> None:
        expected_digest = expected_files.get(relpath)
        if expected_digest is None:
            raise TreeError("candidate tree contains an unverified file")
        destination_path = destination_root / relpath
        destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = destination_path.parent
        while current != destination_root.parent:
            directories.add(current)
            if current == destination_root:
                break
            current = current.parent
        try:
            destination_descriptor = os.open(
                destination_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary,
                0o700 if expected_modes[relpath] == 0o555 else 0o600,
            )
            try:
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    write_all(destination_descriptor, chunk)
                os.fchmod(
                    destination_descriptor,
                    0o700 if expected_modes[relpath] == 0o555 else 0o600,
                )
                os.fsync(destination_descriptor)
            finally:
                os.close(destination_descriptor)
        except OSError as exc:
            raise TreeError("candidate tree file cannot be copied") from exc
        if digest.hexdigest() != expected_digest:
            raise TreeError("candidate tree changed after verification")
        copied.add(relpath)

    _walk_regular_files(source, copy_file)
    if copied != set(expected_files):
        raise TreeError("candidate tree is missing a verified file")
    actual = regular_file_digests(destination_root)
    if (
        actual != expected_files
        or regular_file_modes(destination_root) != expected_modes
    ):
        raise TreeError("published tree verification failed")
    for directory in sorted(
        directories,
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        fsync_directory(directory)


def copy_verified_source(
    source: Path,
    destination: Path,
    verified: VerifiedTree,
) -> None:
    expected_files = verified.files()
    expected_modes = verified.modes()
    expected_links = verified.symlinks()
    if (
        not expected_files
        or set(expected_modes) != set(expected_files)
        or not set(expected_links).issubset(expected_files)
        or any(mode not in {0o444, 0o555} for mode in expected_modes.values())
    ):
        raise TreeError("verified source metadata is invalid")
    destination_root = destination.absolute()
    binary = getattr(os, "O_BINARY", 0)
    directories = {destination_root}
    copied: set[str] = set()

    def copy_file(
        relpath: str,
        source_descriptor: int,
        _value: os.stat_result,
        target: str | None,
    ) -> None:
        expected_digest = expected_files.get(relpath)
        if expected_digest is None or expected_links.get(relpath) != target:
            raise TreeError("candidate source metadata changed after verification")
        destination_path = destination_root / relpath
        destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = destination_path.parent
        while current != destination_root.parent:
            directories.add(current)
            if current == destination_root:
                break
            current = current.parent
        descriptor = os.open(
            destination_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary,
            0o700 if expected_modes[relpath] == 0o555 else 0o600,
        )
        try:
            digest = hashlib.sha256()
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                write_all(descriptor, chunk)
            os.fchmod(
                descriptor,
                0o700 if expected_modes[relpath] == 0o555 else 0o600,
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if digest.hexdigest() != expected_digest:
            raise TreeError("candidate source changed after verification")
        copied.add(relpath)

    _walk_files(
        source,
        copy_file,
        allow_file_symlinks=True,
        excluded=_source_excluded,
    )
    if copied != set(expected_files):
        raise TreeError("candidate source is missing a verified file")
    if (
        regular_file_digests(destination_root) != expected_files
        or regular_file_modes(destination_root) != expected_modes
    ):
        raise TreeError("candidate source materialization failed")
    for directory in sorted(
        directories,
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        fsync_directory(directory)

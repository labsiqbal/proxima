from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Mapping
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

    def files(self) -> dict[str, str]:
        return dict(self.file_digests)


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


def _walk_regular_files(
    root: Path,
    visit: Callable[[str, int, os.stat_result], None],
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
            if stat.S_ISLNK(entry_stat.st_mode):
                raise TreeError("candidate tree contains a symlink")
            if stat.S_ISDIR(entry_stat.st_mode):
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
                visit(relpath, file_descriptor, opened)
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


def copy_regular_tree(
    source: Path,
    destination: Path,
    expected: Mapping[str, str],
) -> None:
    expected_files = dict(expected)
    if not expected_files or any(
        not _safe_relpath(path)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for path, digest in expected_files.items()
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
                0o600,
            )
            try:
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    write_all(destination_descriptor, chunk)
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
    if actual != expected_files:
        raise TreeError("published tree verification failed")
    for directory in sorted(
        directories,
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        fsync_directory(directory)

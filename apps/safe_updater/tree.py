from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .durability import fsync_directory, write_all


class TreeError(ValueError):
    pass


def regular_file_digests(root: Path) -> dict[str, str]:
    absolute_root = root.absolute()
    try:
        root_stat = absolute_root.lstat()
    except OSError as exc:
        raise TreeError("candidate tree is unavailable") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise TreeError("candidate tree root must be a real directory")

    digests: dict[str, str] = {}
    pending = [absolute_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise TreeError("candidate tree cannot be enumerated") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise TreeError("candidate tree entry cannot be inspected") from exc
            if stat.S_ISLNK(entry_stat.st_mode):
                raise TreeError("candidate tree contains a symlink")
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise TreeError("candidate tree contains a non-regular file")
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                after = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise TreeError("candidate tree file cannot be read") from exc
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_dev != entry_stat.st_dev
                or after.st_ino != entry_stat.st_ino
                or after.st_size != entry_stat.st_size
                or after.st_mtime_ns != entry_stat.st_mtime_ns
            ):
                raise TreeError("candidate tree changed during verification")
            digests[path.relative_to(absolute_root).as_posix()] = digest
    return dict(sorted(digests.items()))


def copy_regular_tree(
    source: Path,
    destination: Path,
    expected: dict[str, str],
) -> None:
    source_root = source.absolute()
    destination_root = destination.absolute()
    binary = getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directories = {destination_root}

    for relpath, expected_digest in sorted(expected.items()):
        source_path = source_root / relpath
        destination_path = destination_root / relpath
        destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = destination_path.parent
        while current != destination_root.parent:
            directories.add(current)
            if current == destination_root:
                break
            current = current.parent

        try:
            source_descriptor = os.open(
                source_path,
                os.O_RDONLY | binary | nofollow,
            )
        except OSError as exc:
            raise TreeError("candidate tree file cannot be copied") from exc
        try:
            source_stat = os.fstat(source_descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                raise TreeError("candidate tree contains a non-regular file")
            destination_descriptor = os.open(
                destination_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary,
                0o400,
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
        finally:
            os.close(source_descriptor)
        if digest.hexdigest() != expected_digest:
            raise TreeError("candidate tree changed during publication")

    actual = regular_file_digests(destination_root)
    if actual != expected:
        raise TreeError("published tree verification failed")
    for directory in sorted(
        directories,
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        fsync_directory(directory)

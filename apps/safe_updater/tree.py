from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


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

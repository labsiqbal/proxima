"""Immutable updater layout and hostile identifier validation."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .tree import TreeError, regular_file_digests

RUN_ID = re.compile(r"^[a-f0-9]{32}$")
RELEASE_ID = re.compile(r"^sha256-[a-f0-9]{40}-[a-f0-9]{12}$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")


class LayoutError(ValueError):
    pass


def _validated(value: str, pattern: re.Pattern[str], kind: str) -> str:
    if not pattern.fullmatch(value) or "/" in value or ".." in value:
        raise LayoutError(f"invalid {kind}")
    return value


class ReleaseLayout:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def run_dir(self, run_id: str) -> Path:
        return self.root / "candidates" / _validated(run_id, RUN_ID, "run id")

    def release_dir(self, release_id: str) -> Path:
        return self.root / "releases" / _validated(release_id, RELEASE_ID, "release id")

    def pointer_target(self, release_id: str) -> str:
        self.release_dir(release_id)
        return f"../releases/{release_id}"

    def create_immutable_release(self, release_id: str, source: Path) -> Path:
        """Install an already-verified tree without replacing an existing release."""
        destination = self.release_dir(release_id)
        if os.path.lexists(destination):
            raise LayoutError("release id already exists")
        try:
            regular_file_digests(source)
        except TreeError as exc:
            raise LayoutError(str(exc)) from exc
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        parent_stat = destination.parent.lstat()
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
            raise LayoutError("release directory must be a real directory")
        os.rename(source, destination)
        for path in [destination, *destination.rglob("*")]:
            path.chmod(0o555 if path.is_dir() else 0o444)
        descriptor = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return destination

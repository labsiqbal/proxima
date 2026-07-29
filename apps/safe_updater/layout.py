"""Immutable updater layout and hostile identifier validation."""
from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from .durability import DurabilityError, ensure_durable_directory, fsync_directory
from .tree import (
    TreeError,
    VerifiedTree,
    copy_regular_tree,
    regular_file_digests,
    regular_file_modes,
)

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

    def set_pointer(self, name: str, release_id: str) -> None:
        """Atomically update a trusted relative release pointer and fsync it."""
        if name not in {"active", "last-good"}:
            raise LayoutError("invalid release pointer")
        target = self.pointer_target(release_id)
        pointers = self.root / "pointers"
        try:
            ensure_durable_directory(pointers, 0o755)
        except DurabilityError as exc:
            raise LayoutError(str(exc)) from exc
        pointer = pointers / name
        temporary = pointers / f".{name}.tmp"
        if os.path.lexists(temporary):
            raise LayoutError("temporary pointer already exists")
        os.symlink(target, temporary)
        try:
            os.replace(temporary, pointer)
            fsync_directory(pointers)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def pointer_release(self, name: str) -> str:
        if name not in {"active", "last-good"}:
            raise LayoutError("invalid release pointer")
        pointer = self.root / "pointers" / name
        if not pointer.is_symlink():
            raise LayoutError("release pointer is missing")
        target = os.readlink(pointer)
        expected_prefix = "../releases/"
        if not target.startswith(expected_prefix):
            raise LayoutError("release pointer target is invalid")
        release_id = target.removeprefix(expected_prefix)
        self.release_dir(release_id)
        return release_id

    def create_immutable_release(
        self,
        release_id: str,
        source: Path,
        verified: VerifiedTree,
    ) -> Path:
        """Install a verified tree without replacing an existing release."""
        destination = self.release_dir(release_id)
        if os.path.lexists(destination):
            raise LayoutError("release id already exists")
        if (
            (
                verified.release_id is not None
                and verified.release_id != release_id
            )
            or release_id.split("-")[1] != verified.commit
        ):
            raise LayoutError("verified tree release identity mismatch")
        expected = verified.files()
        modes = verified.modes()
        try:
            ensure_durable_directory(destination.parent, 0o755)
        except DurabilityError as exc:
            raise LayoutError(str(exc)) from exc
        parent_stat = destination.parent.lstat()
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
            raise LayoutError("release directory must be a real directory")
        fsync_directory(destination.parent)
        staging = Path(
            tempfile.mkdtemp(prefix=".incoming-", dir=destination.parent)
        )
        published = False
        renamed = False
        try:
            copy_regular_tree(source, staging, expected, modes)
            for path in sorted(
                staging.rglob("*"),
                key=lambda value: len(value.parts),
                reverse=True,
            ):
                path.chmod(
                    0o555
                    if path.is_dir()
                    else modes[path.relative_to(staging).as_posix()]
                )
            staging.chmod(0o555)
            if (
                regular_file_digests(staging) != expected
                or regular_file_modes(staging) != modes
            ):
                raise LayoutError("published tree verification failed")
            if os.path.lexists(destination):
                raise LayoutError("release id already exists")
            os.rename(staging, destination)
            renamed = True
            try:
                fsync_directory(destination.parent)
            except OSError as exc:
                try:
                    os.rename(destination, staging)
                    renamed = False
                    fsync_directory(destination.parent)
                except OSError as rollback_exc:
                    raise LayoutError(
                        "release publication durability and rollback failed"
                    ) from rollback_exc
                raise LayoutError("release publication durability failed") from exc
            published = True
            return destination
        except TreeError as exc:
            raise LayoutError(str(exc)) from exc
        finally:
            if not published and not renamed and os.path.lexists(staging):
                for path in [staging, *staging.rglob("*")]:
                    try:
                        path.chmod(0o700 if path.is_dir() else 0o600)
                    except OSError:
                        pass
                shutil.rmtree(staging)

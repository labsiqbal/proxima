"""Pinned Git worktree identity for local candidate provenance."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .sandbox import CandidateSandbox, SandboxError
from .tree import VerifiedTree


class GitSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitMetadata:
    read_only_roots: tuple[Path, ...]


def _read_small_file(path: Path) -> str:
    try:
        value = path.lstat()
        if not stat.S_ISREG(value.st_mode) or value.st_size > 4096:
            raise GitSourceError("Git metadata pointer is invalid")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            payload = os.read(descriptor, 4097)
            if len(payload) > 4096 or os.fstat(descriptor).st_ino != value.st_ino:
                raise GitSourceError("Git metadata pointer changed")
        finally:
            os.close(descriptor)
        return payload.decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise GitSourceError("Git metadata pointer cannot be read") from exc


def _real_directory(path: Path) -> Path:
    absolute = path.absolute()
    resolved = path.resolve(strict=True)
    if absolute != resolved or path.is_symlink() or not resolved.is_dir():
        raise GitSourceError("Git metadata path is unsafe")
    return resolved


def resolve_git_metadata(source: Path) -> GitMetadata:
    source = _real_directory(source)
    marker = source / ".git"
    try:
        marker_stat = marker.lstat()
    except OSError as exc:
        raise GitSourceError("candidate source is not a Git worktree") from exc
    if stat.S_ISDIR(marker_stat.st_mode):
        _real_directory(marker)
        return GitMetadata(())
    if not stat.S_ISREG(marker_stat.st_mode):
        raise GitSourceError("Git worktree marker is invalid")
    pointer = _read_small_file(marker)
    prefix = "gitdir: "
    if not pointer.startswith(prefix) or "\n" in pointer or "\0" in pointer:
        raise GitSourceError("Git worktree pointer is invalid")
    raw_git_dir = Path(pointer.removeprefix(prefix))
    git_dir = _real_directory(
        (
            raw_git_dir
            if raw_git_dir.is_absolute()
            else source / raw_git_dir
        ).resolve(strict=True)
    )
    roots = [git_dir]
    common_marker = git_dir / "commondir"
    if common_marker.exists():
        raw_common = Path(_read_small_file(common_marker))
        common = _real_directory(
            (
                raw_common
                if raw_common.is_absolute()
                else git_dir / raw_common
            ).resolve(strict=True)
        )
        roots.append(common)
    minimal: list[Path] = []
    for path in sorted(set(roots), key=lambda item: len(item.parts)):
        if not any(root == path or root in path.parents for root in minimal):
            minimal.append(path)
    return GitMetadata(tuple(minimal))


def _safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and "\\" not in value
        and "\0" not in value
        and "\n" not in value
        and "\r" not in value
        and "\t" not in value
        and "." not in path.parts
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _tracked_modes(raw: bytes) -> dict[str, int]:
    result: dict[str, int] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, encoded_path = record.split(b"\t", 1)
            mode, _object_id, stage = header.split(b" ", 2)
            relpath = encoded_path.decode("utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise GitSourceError("Git tracked-file report is invalid") from exc
        if stage != b"0" or not _safe_path(relpath) or relpath in result:
            raise GitSourceError("Git tracked-file report is invalid")
        if mode == b"100644":
            result[relpath] = 0o444
        elif mode == b"100755":
            result[relpath] = 0o555
        elif mode == b"120000":
            result[relpath] = 0o444
        else:
            raise GitSourceError("candidate Git tree contains an unsupported entry")
    if not result:
        raise GitSourceError("candidate Git tree is empty")
    return result


def verify_git_source(
    source: Path,
    commit: str,
    verified: VerifiedTree,
    sandbox: CandidateSandbox,
) -> None:
    metadata = resolve_git_metadata(source)
    prefix = (
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "credential.helper=",
        "-C",
        str(source),
    )
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    read_only = (source, *metadata.read_only_roots)
    try:
        status = sandbox.run(
            (*prefix, "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=source,
            writable_paths=(sandbox.runner_home,),
            read_only_paths=read_only,
            environment=environment,
            namespace_root=True,
            timeout=30,
        )
        head = sandbox.run(
            (*prefix, "rev-parse", "--verify", "HEAD"),
            cwd=source,
            writable_paths=(sandbox.runner_home,),
            read_only_paths=read_only,
            environment=environment,
            namespace_root=True,
            timeout=30,
        )
        tracked = sandbox.run(
            (*prefix, "ls-files", "--stage", "-z"),
            cwd=source,
            writable_paths=(sandbox.runner_home,),
            read_only_paths=read_only,
            environment=environment,
            namespace_root=True,
            timeout=30,
        )
    except SandboxError as exc:
        raise GitSourceError(str(exc)) from exc
    if (
        status.returncode
        or (status.stdout or b"").strip()
        or head.returncode
        or (head.stdout or b"").decode(errors="replace").strip() != commit
        or tracked.returncode
    ):
        raise GitSourceError("candidate source is dirty or commit identity is unavailable")
    modes = _tracked_modes(tracked.stdout or b"")
    if modes != verified.modes():
        raise GitSourceError("local provenance is not the complete tracked Git tree")
    symlink_paths = {
        relpath
        for relpath, mode in modes.items()
        if relpath in verified.symlinks() and mode == 0o444
    }
    if symlink_paths != set(verified.symlinks()):
        raise GitSourceError("local provenance symlink metadata differs from Git")

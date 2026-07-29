from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .durability import write_all


class PrivilegeBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateIdentity:
    uid: int
    gid: int
    supplementary_gids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class _AccessRequest:
    path: Path
    mode: int


def _effective_access(
    identity: CandidateIdentity,
    requests: tuple[_AccessRequest, ...],
) -> tuple[bool, ...]:
    if os.geteuid() != 0:
        raise PrivilegeBoundaryError(
            "candidate permission proof requires a privileged controller"
        )
    read_descriptor, write_descriptor = os.pipe()
    try:
        process_id = os.fork()
    except OSError as exc:
        os.close(read_descriptor)
        os.close(write_descriptor)
        raise PrivilegeBoundaryError("candidate permission probe failed") from exc
    if process_id == 0:
        os.close(read_descriptor)
        try:
            os.setgroups(sorted(identity.supplementary_gids))
            os.setgid(identity.gid)
            os.setuid(identity.uid)
            result = [
                os.access(request.path, request.mode)
                for request in requests
            ]
            payload = json.dumps({"result": result}).encode("utf-8")
        except BaseException:
            payload = b'{"error":"candidate permission probe failed"}'
        try:
            write_all(write_descriptor, payload)
        finally:
            os.close(write_descriptor)
            os._exit(0)

    os.close(write_descriptor)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(read_descriptor, 4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_descriptor)
    _, status = os.waitpid(process_id, 0)
    if status != 0:
        raise PrivilegeBoundaryError("candidate permission probe failed")
    try:
        value = json.loads(b"".join(chunks))
        result = value["result"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PrivilegeBoundaryError("candidate permission probe failed") from exc
    if (
        not isinstance(result, list)
        or len(result) != len(requests)
        or any(not isinstance(item, bool) for item in result)
    ):
        raise PrivilegeBoundaryError("candidate permission probe failed")
    return tuple(result)


def _ancestry(path: Path) -> tuple[tuple[Path, os.stat_result], ...]:
    result: list[tuple[Path, os.stat_result]] = []
    current = path.absolute()
    while True:
        try:
            value = current.lstat()
        except OSError as exc:
            raise PrivilegeBoundaryError("trusted path is unavailable") from exc
        if stat.S_ISLNK(value.st_mode):
            raise PrivilegeBoundaryError("trusted ancestry must not contain symlinks")
        result.append((current, value))
        if current.parent == current:
            return tuple(result)
        current = current.parent


def assert_candidate_cannot_write(
    identity: CandidateIdentity,
    trusted_paths: Iterable[Path],
) -> None:
    if os.name != "posix" or not hasattr(os, "fork"):
        raise PrivilegeBoundaryError("candidate permission proof unavailable")
    if (
        identity.uid <= 0
        or identity.gid < 0
        or any(group < 0 for group in identity.supplementary_gids)
    ):
        raise PrivilegeBoundaryError("candidate identity must be unprivileged")

    path_ancestries: list[tuple[tuple[Path, os.stat_result], ...]] = []
    seen: dict[Path, os.stat_result] = {}
    for path in trusted_paths:
        ancestry = _ancestry(path)
        path_ancestries.append(ancestry)
        for component, value in ancestry:
            seen[component] = value
    if not path_ancestries:
        raise PrivilegeBoundaryError("trusted path set is empty")
    if any(value.st_uid == identity.uid for value in seen.values()):
        raise PrivilegeBoundaryError("candidate owns trusted ancestry")

    requests: list[_AccessRequest] = []
    request_roles: list[tuple[str, os.stat_result | None]] = []
    for ancestry in path_ancestries:
        leaf, leaf_stat = ancestry[0]
        leaf_mode = os.W_OK | os.X_OK if stat.S_ISDIR(leaf_stat.st_mode) else os.W_OK
        requests.append(_AccessRequest(leaf, leaf_mode))
        request_roles.append(("leaf", None))
        for index in range(1, len(ancestry)):
            parent, parent_stat = ancestry[index]
            _, child_stat = ancestry[index - 1]
            requests.append(_AccessRequest(parent, os.W_OK | os.X_OK))
            request_roles.append(("parent", child_stat))

    access = _effective_access(identity, tuple(requests))
    for allowed, request, (role, child_stat) in zip(
        access,
        requests,
        request_roles,
        strict=True,
    ):
        if not allowed:
            continue
        if role == "leaf":
            raise PrivilegeBoundaryError("candidate can write trusted state")
        parent_stat = seen[request.path]
        sticky_protected = (
            stat.S_ISDIR(parent_stat.st_mode)
            and bool(parent_stat.st_mode & stat.S_ISVTX)
            and parent_stat.st_uid != identity.uid
            and child_stat is not None
            and child_stat.st_uid != identity.uid
        )
        if not sticky_protected:
            raise PrivilegeBoundaryError("candidate can replace trusted ancestry")

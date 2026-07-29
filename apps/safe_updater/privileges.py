from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PrivilegeBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateIdentity:
    uid: int
    gids: frozenset[int]


def assert_candidate_cannot_write(
    identity: CandidateIdentity,
    trusted_paths: Iterable[Path],
) -> None:
    if os.name != "posix":
        raise PrivilegeBoundaryError("candidate permission proof unavailable")
    checked = False
    for path in trusted_paths:
        checked = True
        try:
            value = path.lstat()
        except OSError as exc:
            raise PrivilegeBoundaryError("trusted path is unavailable") from exc
        if stat.S_ISLNK(value.st_mode):
            raise PrivilegeBoundaryError("trusted path must not be a symlink")
        if value.st_uid == identity.uid:
            raise PrivilegeBoundaryError("candidate owns trusted state")
        if value.st_mode & stat.S_IWOTH:
            raise PrivilegeBoundaryError("candidate can write trusted state")
        if value.st_gid in identity.gids and value.st_mode & stat.S_IWGRP:
            raise PrivilegeBoundaryError("candidate can write trusted state")
    if not checked:
        raise PrivilegeBoundaryError("trusted path set is empty")

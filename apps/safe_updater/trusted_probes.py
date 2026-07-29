"""Hash-pinned controller probe bundle contract.

The installed updater supplies the expected digest from its root-owned policy.
This module never accepts a digest declared by the candidate release itself.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .tree import regular_file_digests


class TrustedProbeError(ValueError):
    pass


def _tree_digest(root: Path) -> str:
    files = regular_file_digests(root)
    if not files:
        raise TrustedProbeError("trusted probe bundle is empty")
    digest = hashlib.sha256()
    for name, value in files.items():
        digest.update(name.encode() + b"\0" + value.encode() + b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class TrustedProbeBundle:
    root: Path
    digest: str

    @classmethod
    def load(cls, root: Path, expected_digest: str) -> "TrustedProbeBundle":
        if len(expected_digest) != 64 or set(expected_digest) - set("0123456789abcdef"):
            raise TrustedProbeError("trusted probe policy digest is invalid")
        if not root.is_dir() or root.is_symlink():
            raise TrustedProbeError("trusted probe bundle is unavailable")
        actual = _tree_digest(root)
        if actual != expected_digest:
            raise TrustedProbeError("trusted probe bundle was replaced or weakened")
        return cls(root.resolve(), actual)

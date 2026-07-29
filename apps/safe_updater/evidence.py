"""Controller-owned, append-only evidence for candidate qualification.

Evidence is intentionally stored outside a release.  Candidate code may emit
output, but it never selects the evidence names, locations, or hashes.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .durability import ensure_durable_directory, fsync_directory, write_all
from .layout import RUN_ID


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceBundle:
    path: Path
    digest: str
    files: dict[str, str]


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class EvidenceStore:
    """Writes a single immutable, controller-selected bundle for each run."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def persist(self, run_id: str, records: Mapping[str, bytes | str]) -> EvidenceBundle:
        if not RUN_ID.fullmatch(run_id):
            raise EvidenceError("invalid evidence run id")
        if not records or any(not name or "/" in name or ".." in name for name in records):
            raise EvidenceError("invalid evidence record name")
        directory = self.root / "evidence" / run_id
        ensure_durable_directory(directory.parent, 0o700)
        if os.path.lexists(directory):
            raise EvidenceError("candidate evidence already exists")
        directory.mkdir(mode=0o700)
        fsync_directory(directory.parent)
        digests: dict[str, str] = {}
        try:
            for name, value in sorted(records.items()):
                payload = value.encode() if isinstance(value, str) else value
                if not isinstance(payload, bytes):
                    raise EvidenceError("invalid evidence payload")
                destination = directory / name
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
                try:
                    write_all(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                digests[name] = _digest(payload)
            index = json.dumps(digests, sort_keys=True, separators=(",", ":")).encode()
            descriptor = os.open(directory / "index.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
            try:
                write_all(descriptor, index)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            fsync_directory(directory)
            return EvidenceBundle(directory, _digest(index), digests)
        except BaseException:
            # Do not attempt to erase partial evidence: it is useful forensic input.
            fsync_directory(directory)
            raise

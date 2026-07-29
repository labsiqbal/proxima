"""Durable, frozen and replay-verifiable qualification evidence."""
from __future__ import annotations

import hashlib
import json
import os
import stat
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


def _frozen(path: Path, *, directory: bool) -> bool:
    value = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return expected(value.st_mode) and not stat.S_ISLNK(value.st_mode) and value.st_mode & 0o222 == 0


class EvidenceStore:
    """Publishes one controller-selected bundle for each run."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def directory(self) -> Path:
        return self.root / "evidence"

    def persist(self, run_id: str, records: Mapping[str, bytes | str]) -> EvidenceBundle:
        if not RUN_ID.fullmatch(run_id):
            raise EvidenceError("invalid evidence run id")
        if (
            not records
            or "index.json" in records
            or any(not name or "/" in name or ".." in name for name in records)
        ):
            raise EvidenceError("invalid evidence record name")
        ensure_durable_directory(self.directory, 0o700)
        directory = self.directory / run_id
        if os.path.lexists(directory):
            self.directory.chmod(0o500)
            fsync_directory(self.directory.parent)
            raise EvidenceError("candidate evidence already exists")
        directory.mkdir(mode=0o700)
        fsync_directory(self.directory)
        digests: dict[str, str] = {}
        try:
            for name, value in sorted(records.items()):
                payload = value.encode() if isinstance(value, str) else value
                if not isinstance(payload, bytes):
                    raise EvidenceError("invalid evidence payload")
                destination = directory / name
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o400,
                )
                try:
                    write_all(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                digests[name] = _digest(payload)
            index = json.dumps(
                digests,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            descriptor = os.open(
                directory / "index.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
            )
            try:
                write_all(descriptor, index)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            directory.chmod(0o500)
            fsync_directory(directory)
            self.directory.chmod(0o500)
            fsync_directory(self.directory)
            fsync_directory(self.directory.parent)
            return EvidenceBundle(directory, _digest(index), digests)
        except BaseException:
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o400)
            directory.chmod(0o500)
            fsync_directory(directory)
            self.directory.chmod(0o500)
            fsync_directory(self.directory)
            fsync_directory(self.directory.parent)
            raise

    def load(self, run_id: str, expected_digest: str) -> EvidenceBundle:
        if (
            not RUN_ID.fullmatch(run_id)
            or len(expected_digest) != 64
            or set(expected_digest) - set("0123456789abcdef")
        ):
            raise EvidenceError("invalid evidence identity")
        directory = self.directory / run_id
        try:
            if not _frozen(self.directory, directory=True) or not _frozen(directory, directory=True):
                raise EvidenceError("candidate evidence directory is not frozen")
            index_path = directory / "index.json"
            if not _frozen(index_path, directory=False):
                raise EvidenceError("candidate evidence index is not frozen")
            index = index_path.read_bytes()
            if _digest(index) != expected_digest:
                raise EvidenceError("candidate evidence digest mismatch")
            value = json.loads(index)
            if not isinstance(value, dict) or not value:
                raise EvidenceError("candidate evidence index is invalid")
            files: dict[str, str] = {}
            for name, digest in value.items():
                if (
                    not isinstance(name, str)
                    or not name
                    or "/" in name
                    or ".." in name
                    or name == "index.json"
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or set(digest) - set("0123456789abcdef")
                ):
                    raise EvidenceError("candidate evidence index is invalid")
                path = directory / name
                if not _frozen(path, directory=False) or _digest(path.read_bytes()) != digest:
                    raise EvidenceError("candidate evidence file mismatch")
                files[name] = digest
            if json.dumps(files, sort_keys=True, separators=(",", ":")).encode() != index:
                raise EvidenceError("candidate evidence index is not canonical")
            actual = {path.name for path in directory.iterdir()}
            if actual != {*files, "index.json"}:
                raise EvidenceError("candidate evidence file set mismatch")
            return EvidenceBundle(directory, expected_digest, files)
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceError("candidate evidence is unreadable") from exc

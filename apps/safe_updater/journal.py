"""Hash-chained, fsynced journal owned by the external updater."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .durability import fsync_directory, write_all
from .layout import RUN_ID
from .state_machine import Phase, StateTransitionError, recovery_action, validate_transition


class JournalIntegrityError(ValueError):
    pass


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _ensure_durable_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    existing = current.lstat()
    if stat.S_ISLNK(existing.st_mode) or not stat.S_ISDIR(existing.st_mode):
        raise JournalIntegrityError("journal directory path is not a real directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        fsync_directory(directory)
        fsync_directory(directory.parent)


@dataclass(frozen=True)
class JournalRecord:
    sequence: int
    phase: Phase
    intent_digest: str
    evidence: dict[str, str]
    previous_hash: str | None
    record_hash: str
    recovery: str


class Journal:
    """One append-only jsonl journal.  Each append is durable before it returns."""

    def __init__(self, path: Path, intent_digest: str) -> None:
        if len(intent_digest) != 64 or any(char not in "0123456789abcdef" for char in intent_digest):
            raise JournalIntegrityError("invalid intent digest")
        self.path = path
        self.intent_digest = intent_digest

    @classmethod
    def create(cls, root: Path, run_id: str, intent_digest: str) -> "Journal":
        if not RUN_ID.fullmatch(run_id):
            raise JournalIntegrityError("invalid journal run id")
        journal_dir = root / "journal"
        _ensure_durable_directory(journal_dir)
        fsync_directory(journal_dir.parent)
        fsync_directory(journal_dir)
        path = journal_dir / f"{run_id}.jsonl"
        if os.path.lexists(path):
            raise FileExistsError("journal already exists")
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        fsync_directory(journal_dir)
        return cls(path, intent_digest)

    def records(self) -> list[JournalRecord]:
        if not os.path.lexists(self.path):
            return []
        path_stat = self.path.lstat()
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise JournalIntegrityError("journal path is not a regular file")
        raw_journal = self.path.read_bytes()
        if raw_journal and not raw_journal.endswith(b"\n"):
            raise JournalIntegrityError("journal has an unterminated record")
        records: list[JournalRecord] = []
        previous: JournalRecord | None = None
        for raw in raw_journal.splitlines():
            try:
                data = json.loads(raw)
                if not isinstance(data, dict) or set(data) != {
                    "sequence",
                    "phase",
                    "at",
                    "intent_digest",
                    "evidence",
                    "previous_hash",
                    "recovery",
                    "record_hash",
                }:
                    raise JournalIntegrityError("journal record fields invalid")
                stored = data.pop("record_hash")
                if (
                    not isinstance(stored, str)
                    or len(stored) != 64
                    or any(char not in "0123456789abcdef" for char in stored)
                    or hashlib.sha256(_canonical(data)).hexdigest() != stored
                ):
                    raise JournalIntegrityError("journal record hash mismatch")
                if (
                    not isinstance(data["sequence"], int)
                    or isinstance(data["sequence"], bool)
                    or not isinstance(data["at"], str)
                    or not isinstance(data["intent_digest"], str)
                    or not isinstance(data["evidence"], dict)
                    or data["previous_hash"] is not None
                    and not isinstance(data["previous_hash"], str)
                    or not isinstance(data["recovery"], str)
                ):
                    raise JournalIntegrityError("journal record types invalid")
                record = JournalRecord(
                    sequence=data["sequence"], phase=Phase(data["phase"]),
                    intent_digest=data["intent_digest"], evidence=dict(data["evidence"]),
                    previous_hash=data["previous_hash"], record_hash=stored, recovery=str(data["recovery"]),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise JournalIntegrityError("malformed journal record") from exc
            if record.intent_digest != self.intent_digest:
                raise JournalIntegrityError("journal intent substitution")
            if any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for key, value in record.evidence.items()
            ):
                raise JournalIntegrityError("journal evidence invalid")
            if record.sequence != len(records) + 1 or record.previous_hash != (previous.record_hash if previous else None):
                raise JournalIntegrityError("journal sequence or chain mismatch")
            try:
                validate_transition(previous.phase if previous else None, record.phase)
            except StateTransitionError as exc:
                raise JournalIntegrityError(str(exc)) from exc
            if record.recovery != recovery_action(record.phase):
                raise JournalIntegrityError("journal recovery action mismatch")
            records.append(record)
            previous = record
        return records

    def append(self, phase: Phase, evidence: dict[str, str] | None = None) -> JournalRecord:
        records = self.records()
        previous = records[-1] if records else None
        validate_transition(previous.phase if previous else None, phase)
        normalized = {str(k): str(v) for k, v in sorted((evidence or {}).items())}
        if any(len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in normalized.values()):
            raise JournalIntegrityError("evidence values must be sha256 digests")
        data: dict[str, Any] = {
            "sequence": len(records) + 1,
            "phase": phase.value,
            "at": datetime.now(timezone.utc).isoformat(),
            "intent_digest": self.intent_digest,
            "evidence": normalized,
            "previous_hash": previous.record_hash if previous else None,
            "recovery": recovery_action(phase),
        }
        digest_payload = dict(data)
        record_hash = hashlib.sha256(_canonical(digest_payload)).hexdigest()
        data["record_hash"] = record_hash
        line = _canonical(data) + b"\n"
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0),
        )
        try:
            write_all(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(self.path.parent)
        return JournalRecord(data["sequence"], phase, self.intent_digest, normalized, data["previous_hash"], record_hash, data["recovery"])

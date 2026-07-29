from __future__ import annotations

from dataclasses import dataclass
from .journal import Journal, JournalIntegrityError
from .state_machine import recovery_action


@dataclass(frozen=True)
class RecoveryStatus:
    safe: bool
    action: str
    journal_hash: str | None
    reason: str | None = None


def inspect(journal: Journal) -> RecoveryStatus:
    try:
        records = journal.records()
    except JournalIntegrityError as exc:
        return RecoveryStatus(False, "do_not_start_any_release", None, str(exc))
    if not records:
        return RecoveryStatus(True, "discard_candidate", None)
    record = records[-1]
    return RecoveryStatus(True, recovery_action(record.phase), record.record_hash)

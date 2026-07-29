from __future__ import annotations

import json
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
    if not journal.path.exists():
        return RecoveryStatus(
            False,
            "do_not_start_any_release",
            None,
            "accepted-run journal is missing",
        )
    try:
        records = journal.records()
    except JournalIntegrityError as exc:
        return RecoveryStatus(False, "do_not_start_any_release", None, str(exc))
    if not records:
        return RecoveryStatus(
            False,
            "do_not_start_any_release",
            None,
            "accepted-run journal is empty",
        )
    record = records[-1]
    return RecoveryStatus(True, recovery_action(record.phase), record.record_hash)


def format_status(run_id: str, value: RecoveryStatus) -> str:
    return json.dumps(
        {
            "action": value.action,
            "journal_hash": value.journal_hash,
            "reason": value.reason,
            "run_id": run_id,
            "safe": value.safe,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

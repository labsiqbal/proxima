from __future__ import annotations

import json
from dataclasses import dataclass

from .evidence import EvidenceError, EvidenceStore
from .journal import Journal, JournalIntegrityError
from .state_machine import Phase
from .state_machine import recovery_action


@dataclass(frozen=True)
class RecoveryStatus:
    safe: bool
    action: str
    journal_hash: str | None
    reason: str | None = None


def inspect(
    journal: Journal,
    *,
    evidence_store: EvidenceStore | None = None,
    run_id: str | None = None,
) -> RecoveryStatus:
    try:
        if not journal.path.exists():
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                None,
                "accepted-run journal is missing",
            )
        records = journal.records()
    except JournalIntegrityError as exc:
        return RecoveryStatus(False, "do_not_start_any_release", None, str(exc))
    except OSError:
        return RecoveryStatus(
            False,
            "do_not_start_any_release",
            None,
            "accepted-run journal is unreadable",
        )
    if not records:
        return RecoveryStatus(
            False,
            "do_not_start_any_release",
            None,
            "accepted-run journal is empty",
        )
    if evidence_store is not None:
        staged = [
            value for value in records if value.phase is Phase.CANDIDATE_STAGED
        ]
        if staged:
            expected = staged[-1].evidence.get("candidate_evidence")
            if not expected or run_id is None:
                return RecoveryStatus(
                    False,
                    "do_not_start_any_release",
                    None,
                    "candidate evidence identity is missing",
                )
            try:
                evidence_store.load(run_id, expected)
            except EvidenceError as exc:
                return RecoveryStatus(
                    False,
                    "do_not_start_any_release",
                    None,
                    str(exc),
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

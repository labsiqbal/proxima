"""Inert trusted controller facade for the group-14 updater foundation."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .journal import Journal
from .layout import RUN_ID
from .locks import SingleFlightLock
from .recovery import RecoveryStatus, inspect
from .state_machine import Phase


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    run_id: str
    reason: str | None = None


class SafeUpdateController:
    """Owns lock and journal only. Its activation methods intentionally do not exist."""
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = SingleFlightLock(root / "controller.lock")

    def _active_run(self) -> str | None:
        journal_dir = self.root / "journal"
        if not journal_dir.exists():
            return None
        if journal_dir.is_symlink() or not journal_dir.is_dir():
            return "unknown"
        for path in sorted(journal_dir.iterdir()):
            match = RUN_ID.fullmatch(path.name.removesuffix(".jsonl"))
            if path.suffix != ".jsonl" or match is None or path.is_symlink() or not path.is_file():
                return "unknown"
            try:
                first = path.read_bytes().splitlines()[0]
                intent_digest = str(json.loads(first)["intent_digest"])
                records = Journal(path, intent_digest).records()
            except (IndexError, KeyError, OSError, ValueError, json.JSONDecodeError):
                return match.group(0)
            if not records or records[-1].phase is not Phase.COMPLETED:
                return match.group(0)
        return None

    def submit(self, intent: dict[str, Any]) -> SubmitResult:
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        run_id = secrets.token_hex(16)
        acquired = self.lock.acquire(run_id, publish_owner=False)
        if not acquired.acquired:
            return SubmitResult(False, acquired.run_id or "unknown", "safe_update_in_progress")
        try:
            active_run = self._active_run()
            if active_run is not None:
                self.lock.set_owner(active_run)
                return SubmitResult(False, active_run, "safe_update_in_progress")
            self.lock.set_owner(run_id)
            journal = Journal.create(self.root, run_id, digest)
            journal.append(Phase.PREFLIGHT)
            # Group 14 stops here: no candidate build, pointer, data, or service side effect.
            return SubmitResult(True, run_id)
        finally:
            self.lock.release()

    def recovery_status(self, run_id: str, intent: dict[str, Any]) -> RecoveryStatus:
        if not RUN_ID.fullmatch(run_id):
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                None,
                "invalid journal run id",
            )
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        return inspect(Journal(self.root / "journal" / f"{run_id}.jsonl", digest))

    def qualify_candidate(self, run_id: str, intent: dict[str, Any], gate: Any, **kwargs: Any) -> Any:
        """Run only the pre-switch candidate gate for an accepted journal.

        This deliberately has no access to active pointers, fences, backups or a
        service adapter.  Later groups own every transition after
        ``candidate_staged``.
        """
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid journal run id")
        digest = hashlib.sha256(
            json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        journal = Journal(self.root / "journal" / f"{run_id}.jsonl", digest)
        records = journal.records()
        if not records or records[-1].phase is not Phase.PREFLIGHT:
            raise RuntimeError("candidate qualification requires an accepted preflight run")
        result = gate.prepare(run_id, **kwargs)
        journal.append(Phase.CANDIDATE_STAGED, {"candidate_evidence": result.evidence.digest})
        return result

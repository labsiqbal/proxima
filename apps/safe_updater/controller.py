"""Inert trusted controller facade for the group-14 updater foundation."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .journal import Journal
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

    def submit(self, intent: dict[str, Any]) -> SubmitResult:
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        run_id = secrets.token_hex(16)
        acquired = self.lock.acquire(run_id)
        if not acquired.acquired:
            return SubmitResult(False, acquired.run_id or "unknown", "safe_update_in_progress")
        try:
            journal = Journal.create(self.root, run_id, digest)
            journal.append(Phase.PREFLIGHT)
            # Group 14 stops here: no candidate build, pointer, data, or service side effect.
            return SubmitResult(True, run_id)
        finally:
            self.lock.release()

    def recovery_status(self, run_id: str, intent: dict[str, Any]) -> RecoveryStatus:
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        return inspect(Journal(self.root / "journal" / f"{run_id}.jsonl", digest))

"""App-visible mirror for an external safe updater.  It has no promotion authority."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


class SafeUpdateError(Exception):
    code = "safe_update_unavailable"


class SafeUpdateUnmanaged(SafeUpdateError):
    code = "safe_update_unmanaged"


class SafeUpdateInProgress(SafeUpdateError):
    code = "safe_update_in_progress"
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


class ExternalUpdater(Protocol):
    def capability(self) -> dict[str, Any]: ...
    def submit(self, request: dict[str, str]) -> dict[str, str]: ...
    def recovery_status(self, run_id: str) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SafeUpdateCoordinator:
    conn: sqlite3.Connection
    external: ExternalUpdater | None = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def capability(self) -> dict[str, Any]:
        if self.external is None:
            return {"managed": False, "reason": "safe_update_unmanaged", "activation": "disabled"}
        result = dict(self.external.capability())
        result["activation"] = "disabled"
        return result

    def submit(self, request: dict[str, str]) -> dict[str, Any]:
        if self.external is None:
            raise SafeUpdateUnmanaged()
        # The external controller, not this SQLite projection, owns single-flight.
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM self_update_runs WHERE status IN ('requested', 'in_progress') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if existing:
                raise SafeUpdateInProgress(str(existing[0]))
            accepted = self.external.submit(request)
            run_id = str(accepted["run_id"])
            self.conn.execute(
                "INSERT INTO self_update_runs(id, origin_job_id, base_commit, candidate_commit, phase, status, evidence_summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, request.get("origin_job_id"), request["base_commit"], request["candidate_commit"], "preflight", "requested", "{}", _now(), _now()),
            )
            self.conn.commit()
            return self.get(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM self_update_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["evidence_summary"] = json.loads(result["evidence_summary"] or "{}")
        return result

    def recovery_status(self, run_id: str) -> dict[str, Any]:
        record = self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        if self.external is None:
            return {"safe": False, "reason": "safe_update_unmanaged", "run_id": run_id}
        # Candidate-supplied text is neither read nor used as evidence.
        return dict(self.external.recovery_status(run_id))

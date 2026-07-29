"""Durable fixture-only circuit breaker for failed safe-update transactions."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .durability import ensure_durable_directory, fsync_directory, write_all


@dataclass(frozen=True)
class BreakerStatus:
    latched: bool
    failures: int
    reason: str | None


class CircuitBreaker:
    def __init__(self, root: Path) -> None:
        self.path = root / "breaker.json"
        self.temporary = self.path.with_name(f".{self.path.name}.tmp")

    def status(self) -> BreakerStatus:
        if os.path.lexists(self.temporary):
            return BreakerStatus(True, 0, "breaker_update_interrupted")
        try:
            path_stat = self.path.lstat()
            if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
                return BreakerStatus(True, 0, "breaker_state_unreadable")
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BreakerStatus(False, 0, None)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return BreakerStatus(True, 0, "breaker_state_unreadable")
        if (
            not isinstance(data, dict)
            or set(data) != {"failures", "latched", "reason"}
            or not isinstance(data["failures"], int)
            or isinstance(data["failures"], bool)
            or data["failures"] < 0
            or not isinstance(data["latched"], bool)
            or data["reason"] is not None
            and not isinstance(data["reason"], str)
            or data["latched"] != bool(data["reason"])
        ):
            return BreakerStatus(True, 0, "breaker_state_unreadable")
        return BreakerStatus(data["latched"], data["failures"], data["reason"])

    def record_failure(self, reason: str, *, rollback_failed: bool = False) -> BreakerStatus:
        current = self.status()
        failures = current.failures + 1
        latched = current.latched or rollback_failed or failures >= 2
        self._write({"failures": failures, "latched": latched, "reason": reason if latched else None})
        return self.status()

    def _write(self, value: dict[str, object]) -> None:
        ensure_durable_directory(self.path.parent, 0o700)
        descriptor = os.open(
            self.temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        replaced = False
        try:
            try:
                write_all(
                    descriptor,
                    json.dumps(
                        value,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(self.temporary, self.path)
            replaced = True
            fsync_directory(self.path.parent)
        finally:
            if not replaced:
                try:
                    self.temporary.unlink()
                except FileNotFoundError:
                    pass

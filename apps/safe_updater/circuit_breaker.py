"""Durable fixture-only circuit breaker for failed safe-update transactions."""
from __future__ import annotations

import json
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

    def status(self) -> BreakerStatus:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BreakerStatus(False, 0, None)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return BreakerStatus(True, 0, "breaker_state_unreadable")
        return BreakerStatus(bool(data.get("latched")), int(data.get("failures", 0)), data.get("reason"))

    def record_failure(self, reason: str, *, rollback_failed: bool = False) -> BreakerStatus:
        current = self.status()
        failures = current.failures + 1
        latched = current.latched or rollback_failed or failures >= 2
        self._write({"failures": failures, "latched": latched, "reason": reason if latched else None})
        return self.status()

    def _write(self, value: dict[str, object]) -> None:
        ensure_durable_directory(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = __import__("os").open(temporary, __import__("os").O_CREAT | __import__("os").O_EXCL | __import__("os").O_WRONLY, 0o600)
        try:
            write_all(descriptor, json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
            __import__("os").fsync(descriptor)
        finally:
            __import__("os").close(descriptor)
        __import__("os").replace(temporary, self.path)
        fsync_directory(self.path.parent)

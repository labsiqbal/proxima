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
        self.pending = self.path.with_name(f".{self.path.name}.pending")

    def status(self) -> BreakerStatus:
        if os.path.lexists(self.pending) or os.path.lexists(self.temporary):
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

    def begin_rollback(self, reason: str) -> BreakerStatus:
        current = self.status()
        if current.latched:
            return current
        self._write(
            {
                "failures": current.failures + 1,
                "latched": True,
                "reason": f"rollback_required:{reason}",
            }
        )
        return self.status()

    def finish_rollback(self, reason: str, *, latch: bool) -> BreakerStatus:
        current = self.status()
        if (
            not current.latched
            or current.reason is None
            or not current.reason.startswith("rollback_required:")
        ):
            raise RuntimeError("rollback verdict is unavailable")
        latched = latch or current.failures >= 2
        self._write(
            {
                "failures": current.failures,
                "latched": latched,
                "reason": reason if latched else None,
            }
        )
        return self.status()

    def _preserve_pending(self) -> None:
        try:
            descriptor = os.open(
                self.pending,
                os.O_CREAT
                | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.ftruncate(descriptor, 0)
                write_all(descriptor, b"pending\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            fsync_directory(self.path.parent)
        except Exception:
            pass

    def _write(self, value: dict[str, object]) -> None:
        ensure_durable_directory(self.path.parent, 0o700)
        pending_descriptor = os.open(
            self.pending,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            write_all(pending_descriptor, b"pending\n")
            os.fsync(pending_descriptor)
        finally:
            os.close(pending_descriptor)
        fsync_directory(self.path.parent)
        descriptor = os.open(
            self.temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
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
            fsync_directory(self.path.parent)
            self.pending.unlink()
            try:
                fsync_directory(self.path.parent)
            except Exception:
                self._preserve_pending()
                raise
        except Exception:
            self._preserve_pending()
            raise

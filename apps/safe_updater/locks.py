"""Kernel-backed single-flight lock with durable owner metadata."""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LockResult:
    acquired: bool
    run_id: str | None


class SingleFlightLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def acquire(self, run_id: str) -> LockResult:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.seek(0)
            try:
                owner = json.loads(stream.read() or "{}")
            except json.JSONDecodeError:
                owner = {}
            stream.close()
            return LockResult(False, owner.get("run_id"))
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"run_id": run_id, "pid": os.getpid()}))
        stream.flush()
        os.fsync(stream.fileno())
        self._file = stream
        return LockResult(True, run_id)

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None

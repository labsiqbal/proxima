"""Platform-selected single-flight lock with durable owner metadata."""
from __future__ import annotations

import errno
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import IO


@dataclass
class LockResult:
    acquired: bool
    run_id: str | None


class SingleFlightLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    @staticmethod
    def _try_lock(stream: IO[str]) -> bool:
        if os.name == "posix":
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write("\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    return False
                raise
            return True
        raise RuntimeError("safe_update_lock_platform_unsupported")

    @staticmethod
    def _unlock(stream: IO[str]) -> None:
        if os.name == "posix":
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            return
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        raise RuntimeError("safe_update_lock_platform_unsupported")

    def set_owner(self, run_id: str) -> None:
        if self._file is None:
            raise RuntimeError("safe_update_lock_not_acquired")
        self._file.seek(0)
        self._file.truncate()
        self._file.write(json.dumps({"run_id": run_id, "pid": os.getpid()}))
        self._file.flush()
        os.fsync(self._file.fileno())

    def acquire(self, run_id: str, *, publish_owner: bool = True) -> LockResult:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            acquired = self._try_lock(stream)
        except BaseException:
            stream.close()
            raise
        if not acquired:
            try:
                stream.seek(0)
                try:
                    owner = json.loads(stream.read() or "{}")
                except json.JSONDecodeError:
                    owner = {}
                owner_run_id = (
                    owner.get("run_id") if isinstance(owner, dict) else None
                )
            finally:
                stream.close()
            return LockResult(False, owner_run_id)
        self._file = stream
        if publish_owner:
            self.set_owner(run_id)
        return LockResult(True, run_id)

    def release(self) -> None:
        if self._file is not None:
            self._unlock(self._file)
            self._file.close()
            self._file = None

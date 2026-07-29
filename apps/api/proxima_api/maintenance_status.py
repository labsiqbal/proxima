from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Callable


def ingress_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.lock")


def ingress_pending_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.pending")


def _configured_fence(config: dict[str, object]) -> Path | None:
    raw_path = config.get("safe_update_fence_path")
    return Path(str(raw_path)) if raw_path else None


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class IngressLease:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.descriptor: int | None = None
        self.acquired = path is None

    def acquire(self) -> "IngressLease":
        if self.path is None:
            return self
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError:
            return self
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return self
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_SH | fcntl.LOCK_NB,
                )
            elif os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                os.close(descriptor)
                return self
        except OSError:
            os.close(descriptor)
            return self
        self.descriptor = descriptor
        self.acquired = True
        return self

    def release(self) -> None:
        if self.descriptor is None:
            return
        if os.name == "posix":
            import fcntl

            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        elif os.name == "nt":
            import msvcrt

            os.lseek(self.descriptor, 0, os.SEEK_SET)
            msvcrt.locking(self.descriptor, msvcrt.LK_UNLCK, 1)
        os.close(self.descriptor)
        self.descriptor = None
        self.acquired = False


class MaintenanceBoundary:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.fence_path = _configured_fence(config)
        self.lock_path = (
            ingress_lock_path(self.fence_path)
            if self.fence_path is not None
            else None
        )

    def prepare(self) -> None:
        if self.lock_path is None or active_maintenance(self.config) is not None:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            if self.lock_path.is_symlink() or not self.lock_path.is_file():
                raise RuntimeError("maintenance ingress lock is invalid")
            return
        try:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(self.lock_path.parent)

    def acquire(self) -> IngressLease:
        return IngressLease(self.lock_path).acquire()

    def status(self) -> dict[str, str] | None:
        status = active_maintenance(self.config)
        if status is not None:
            return status
        if self.lock_path is not None:
            try:
                lock_stat = self.lock_path.lstat()
            except OSError:
                lock_stat = None
            if lock_stat is None or not stat.S_ISREG(lock_stat.st_mode):
                return {
                    "phase": "unknown",
                    "reason": "maintenance_ingress_unavailable",
                }
        return None

    def fenced(self) -> bool:
        return self.status() is not None

    def database_write_check(self) -> Callable[[], bool] | None:
        if self.fence_path is None:
            return None
        return self.fenced


def read_external_fence(path: Path) -> dict[str, str] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"phase": "unknown", "reason": "maintenance_state_unreadable"}
    if not isinstance(value, dict) or not isinstance(value.get("phase"), str):
        return {"phase": "unknown", "reason": "maintenance_state_invalid"}
    return {"phase": value["phase"], "run_id": str(value.get("run_id") or "")}


def active_external_fence(config: dict[str, object]) -> dict[str, str] | None:
    path = _configured_fence(config)
    if path is None:
        return None
    pending = ingress_pending_path(path)
    if os.path.lexists(pending):
        if pending.is_symlink() or not pending.is_file():
            return {
                "phase": "unknown",
                "reason": "maintenance_ingress_invalid",
            }
        return {"phase": "write_fence_pending", "run_id": ""}
    return read_external_fence(path)


def active_maintenance(config: dict[str, object]) -> dict[str, str] | None:
    fence = active_external_fence(config)
    if fence is not None:
        return fence
    if (
        config.get("safe_update_maintenance_mode")
        or config.get("_safe_update_startup_read_only")
    ):
        return {"phase": "maintenance_readonly", "run_id": ""}
    return None


def writes_fenced(config: dict[str, object]) -> bool:
    """Fail closed for unreadable controller state and never cache the answer."""
    return MaintenanceBoundary(config).fenced()

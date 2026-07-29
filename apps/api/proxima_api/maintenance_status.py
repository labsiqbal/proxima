from __future__ import annotations

import asyncio
import json
import os
import secrets
import stat
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable


_context_admissions: ContextVar[frozenset[str]] = ContextVar(
    "maintenance_context_admissions",
    default=frozenset(),
)
_active_admissions: dict[str, str] = {}
_process_admissions: dict[str, int] = {}
_process_lock = threading.Lock()


def ingress_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.lock")


def ingress_pending_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.pending")


def _configured_fence(config: dict[str, object]) -> Path | None:
    raw_path = config.get("safe_update_fence_path")
    return Path(str(raw_path)) if raw_path else None


class IngressLease:
    def __init__(
        self,
        boundary: "MaintenanceBoundary",
        *,
        process_wide: bool = False,
    ) -> None:
        self.boundary = boundary
        self.path = boundary.lock_path
        self.process_wide = process_wide
        self.descriptor: int | None = None
        self.acquired = self.path is None
        self.token = secrets.token_hex(16)
        self.registered = False
        self.context_active = False

    def acquire(self) -> "IngressLease":
        if self.path is None:
            return self
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
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
        self.boundary._register(
            self.token,
            process_wide=self.process_wide,
        )
        self.registered = True
        if not self.process_wide:
            self.boundary._activate(self.token)
            self.context_active = True
        return self

    def suspend_admission(self) -> None:
        if self.process_wide:
            if self.registered:
                self.boundary._unregister(
                    self.token,
                    process_wide=True,
                )
                self.registered = False
            return
        if self.context_active:
            self.boundary._deactivate(self.token)
            self.context_active = False

    def activate_admission(self) -> None:
        if (
            self.process_wide
            or not self.registered
            or self.context_active
        ):
            return
        self.boundary._activate(self.token)
        self.context_active = True

    def release(self) -> None:
        if self.descriptor is None:
            return
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt

                os.lseek(self.descriptor, 0, os.SEEK_SET)
                msvcrt.locking(self.descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self.descriptor)
            self.descriptor = None
            self.acquired = False
            if self.context_active:
                self.boundary._deactivate(self.token)
                self.context_active = False
            if self.registered:
                self.boundary._unregister(
                    self.token,
                    process_wide=self.process_wide,
                )
                self.registered = False


class MaintenanceBoundary:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.fence_path = _configured_fence(config)
        self.lock_path = (
            ingress_lock_path(self.fence_path)
            if self.fence_path is not None
            else None
        )
        self._admission_key = (
            os.path.abspath(os.fspath(self.lock_path))
            if self.lock_path is not None
            else None
        )
        self._retained_leases: list[IngressLease] = []

    def _register(self, token: str, *, process_wide: bool) -> None:
        if self._admission_key is None:
            return
        with _process_lock:
            if process_wide:
                _process_admissions[self._admission_key] = (
                    _process_admissions.get(self._admission_key, 0) + 1
                )
            else:
                _active_admissions[token] = self._admission_key

    def _unregister(self, token: str, *, process_wide: bool) -> None:
        if self._admission_key is None:
            return
        with _process_lock:
            if process_wide:
                remaining = max(
                    0,
                    _process_admissions.get(self._admission_key, 0) - 1,
                )
                if remaining:
                    _process_admissions[self._admission_key] = remaining
                else:
                    _process_admissions.pop(self._admission_key, None)
            elif _active_admissions.get(token) == self._admission_key:
                _active_admissions.pop(token, None)

    def _activate(self, token: str) -> None:
        _context_admissions.set(
            _context_admissions.get() | {token}
        )

    def _deactivate(self, token: str) -> None:
        _context_admissions.set(
            _context_admissions.get() - {token}
        )

    def acquire(self, *, process_wide: bool = False) -> IngressLease:
        return IngressLease(
            self,
            process_wide=process_wide,
        ).acquire()

    def admitted(self) -> bool:
        if self._admission_key is None:
            return False
        with _process_lock:
            if _process_admissions.get(self._admission_key, 0) > 0:
                return True
            return any(
                _active_admissions.get(token) == self._admission_key
                for token in _context_admissions.get()
            )

    def retain(self, lease: IngressLease) -> None:
        self._retained_leases.append(lease)

    def background_lease(self) -> IngressLease:
        if self.lock_path is not None and not self.admitted():
            raise RuntimeError("background effect lacks ingress admission")
        lease = self.acquire()
        if not lease.acquired:
            raise RuntimeError("background effect ingress is unavailable")
        lease.suspend_admission()
        return lease

    def claim_effect(self) -> IngressLease:
        lease = self.acquire()
        if not lease.acquired or self.status() is not None:
            lease.release()
            raise RuntimeError("background effect ingress is unavailable")
        return lease

    def start_thread(
        self,
        target: Callable[..., Any],
        *,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        name: str | None = None,
        daemon: bool = True,
    ) -> threading.Thread:
        lease = self.background_lease()

        def run() -> None:
            lease.activate_admission()
            try:
                target(*args, **(kwargs or {}))
            finally:
                lease.release()

        thread = threading.Thread(
            target=run,
            daemon=daemon,
            name=name,
        )
        try:
            thread.start()
        except BaseException:
            lease.release()
            raise
        return thread

    def create_task(
        self,
        awaitable,
        *,
        name: str | None = None,
    ) -> asyncio.Task:
        lease = self.background_lease()

        async def run():
            lease.activate_admission()
            try:
                return await awaitable
            finally:
                lease.release()

        try:
            return asyncio.create_task(run(), name=name)
        except BaseException:
            lease.release()
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            raise

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

    def write_fenced(self) -> bool:
        return not self.admitted() and self.fenced()

    @property
    def process_containment_required(self) -> bool:
        return self.lock_path is not None

    def database_write_check(self) -> Callable[[], bool] | None:
        if self.fence_path is None:
            return None
        return self.write_fenced


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

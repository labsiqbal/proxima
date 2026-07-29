"""External maintenance-fence file contract.  Candidate releases never own it."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .durability import ensure_durable_directory, fsync_directory, write_all

FENCE_DIRECTORY_MODE = 0o755
FENCE_FILE_MODE = 0o644
INGRESS_DRAIN_TIMEOUT_SECONDS = 10.0


class IngressActivationError(RuntimeError):
    def __init__(self, message: str, *, pending_owned: bool) -> None:
        super().__init__(message)
        self.pending_owned = pending_owned


class IngressActivationPending(IngressActivationError):
    def __init__(self, message: str, *, pending_owned: bool = False) -> None:
        super().__init__(message, pending_owned=pending_owned)


class IngressDrainTimeout(IngressActivationError, TimeoutError):
    def __init__(self, message: str, *, pending_owned: bool = True) -> None:
        super().__init__(message, pending_owned=pending_owned)


@dataclass(frozen=True)
class IngressActivationState:
    run_id: str
    active: bool
    pending: bool


def ingress_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.lock")


def ingress_pending_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ingress.pending")


def prepare_ingress_lock(path: Path) -> Path:
    ensure_durable_directory(path.parent, FENCE_DIRECTORY_MODE)
    lock_path = ingress_lock_path(path)
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            FENCE_FILE_MODE,
        )
    except FileExistsError:
        if lock_path.is_symlink() or not lock_path.is_file():
            raise RuntimeError("maintenance ingress lock is invalid")
        return lock_path
    try:
        write_all(descriptor, b"\0")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)
    return lock_path


@contextmanager
def _exclusive_ingress(
    path: Path,
    *,
    timeout_seconds: float = INGRESS_DRAIN_TIMEOUT_SECONDS,
) -> Iterator[None]:
    lock_path = prepare_ingress_lock(path)
    descriptor = os.open(lock_path, os.O_RDWR)
    acquired = False
    try:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not acquired:
            try:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(
                        descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                elif os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    raise RuntimeError(
                        "maintenance ingress lock platform unsupported"
                    )
                acquired = True
            except OSError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise IngressDrainTimeout(
                        "maintenance ingress drain timed out",
                        pending_owned=True,
                    ) from exc
                time.sleep(min(0.01, remaining))
        yield
    finally:
        if acquired and os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif acquired and os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def _write_pending(path: Path, run_id: str) -> Path:
    pending = ingress_pending_path(path)
    try:
        descriptor = os.open(
            pending,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            FENCE_FILE_MODE,
        )
    except FileExistsError as exc:
        raise IngressActivationPending(
            "maintenance ingress activation is already pending",
            pending_owned=False,
        ) from exc
    try:
        try:
            payload = json.dumps(
                {"run_id": run_id},
                sort_keys=True,
            ).encode("utf-8")
            write_all(descriptor, payload)
            os.fsync(descriptor)
        except Exception as exc:
            raise IngressActivationError(
                "maintenance ingress pending state is ambiguous",
                pending_owned=True,
            ) from exc
    finally:
        os.close(descriptor)
    try:
        fsync_directory(path.parent)
    except Exception as exc:
        raise IngressActivationError(
            "maintenance ingress pending state is ambiguous",
            pending_owned=True,
        ) from exc
    return pending


def write(
    path: Path,
    run_id: str,
    phase: str,
    *,
    drain_timeout_seconds: float = INGRESS_DRAIN_TIMEOUT_SECONDS,
) -> None:
    prepare_ingress_lock(path)
    path.parent.chmod(FENCE_DIRECTORY_MODE)
    pending = _write_pending(path, run_id)
    payload = json.dumps(
        {"run_id": run_id, "phase": phase},
        sort_keys=True,
    ).encode("utf-8")
    try:
        with _exclusive_ingress(
            path,
            timeout_seconds=drain_timeout_seconds,
        ):
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                dir=path.parent,
            )
            temporary = Path(temporary_name)
            try:
                try:
                    if hasattr(os, "fchmod"):
                        os.fchmod(descriptor, FENCE_FILE_MODE)
                    else:
                        temporary.chmod(FENCE_FILE_MODE)
                    write_all(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary, path)
                fsync_directory(path.parent)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            pending.unlink()
            fsync_directory(path.parent)
    except IngressActivationError:
        raise
    except Exception as exc:
        raise IngressActivationError(
            "maintenance ingress activation is ambiguous",
            pending_owned=True,
        ) from exc


def _state_run_id(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable") from exc
    if not isinstance(value, dict) or not isinstance(value.get("run_id"), str):
        raise RuntimeError(f"{label} has no owner")
    return value["run_id"]


def read_activation_state(path: Path) -> IngressActivationState | None:
    owners: list[str] = []
    active = path.exists() or path.is_symlink()
    if active:
        owners.append(_state_run_id(path, "maintenance fence"))
    pending_path = ingress_pending_path(path)
    pending = pending_path.exists() or pending_path.is_symlink()
    if pending:
        owners.append(
            _state_run_id(
                pending_path,
                "maintenance ingress pending state",
            )
        )
    if not owners:
        return None
    if len(set(owners)) != 1:
        raise RuntimeError("maintenance activation owners diverge")
    return IngressActivationState(
        run_id=owners[0],
        active=active,
        pending=pending,
    )


def remove(
    path: Path,
    run_id: str,
    *,
    drain_timeout_seconds: float = INGRESS_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Durably remove a controller-owned fence after a committed outcome only."""
    with _exclusive_ingress(
        path,
        timeout_seconds=drain_timeout_seconds,
    ):
        changed = False
        if path.exists() or path.is_symlink():
            if _state_run_id(path, "maintenance fence") != run_id:
                raise RuntimeError("maintenance fence owner does not match")
            path.unlink()
            changed = True
        pending = ingress_pending_path(path)
        if pending.exists() or pending.is_symlink():
            if _state_run_id(
                pending,
                "maintenance ingress pending state",
            ) != run_id:
                raise RuntimeError("maintenance ingress pending owner does not match")
            pending.unlink()
            changed = True
        if changed:
            fsync_directory(path.parent)

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _adopt_lock(value: str) -> int:
    if os.name == "nt":
        import msvcrt

        from .container_registry import _windows_lock_file

        if not value.startswith("handle:"):
            raise ValueError("missing inherited activity handle")
        handle = int(value.removeprefix("handle:"))
        os.set_handle_inheritable(handle, False)
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR)
        _windows_lock_file(msvcrt.get_osfhandle(fd), shared=True)
        return fd
    fd = int(value)
    os.set_inheritable(fd, False)
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_SH)
    return fd


def _enable_subreaper() -> None:
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _descendants(parent: int) -> set[int]:
    proc = Path("/proc")
    if not proc.is_dir():
        return set()
    children: dict[int, set[int]] = {}
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        try:
            status = (item / "status").read_text(encoding="utf-8")
            line = next(value for value in status.splitlines() if value.startswith("PPid:"))
            ppid = int(line.split(":", 1)[1])
        except (OSError, StopIteration, ValueError):
            continue
        children.setdefault(ppid, set()).add(int(item.name))
    found: set[int] = set()
    pending = list(children.get(parent, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return found


def _signal_descendants(value: int) -> None:
    for pid in sorted(_descendants(os.getpid()), reverse=True):
        try:
            os.kill(pid, value)
        except OSError:
            pass


def _wait_for_children() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            time.sleep(0.02)


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        return 64
    activity_fd = _adopt_lock(sys.argv[1])
    command = sys.argv[3:]
    _enable_subreaper()
    signals = [
        value
        for value in (
            signal.SIGTERM,
            signal.SIGINT,
            getattr(signal, "SIGHUP", None),
        )
        if value is not None
    ]
    for value in signals:
        signal.signal(value, lambda _signum, _frame, sent=value: _signal_descendants(sent))
    process = subprocess.Popen(command, close_fds=True)
    result = process.wait()
    _wait_for_children()
    os.close(activity_fd)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
import time
from dataclasses import dataclass
from typing import Any

from .container_activity import process_start_identity
from .process_containment import pid_namespace_argv

logger = logging.getLogger("proxima.terminal")


@dataclass(frozen=True)
class CloseResult:
    """Outcome of shutting down a PTY session.

    ``session_stopped`` is True only when the full session membership could be
    verified empty. ``child_reaped`` is True once the direct child is proven
    reaped (or was never started). Callers may release writer-activity leases
    only when ``child_reaped`` is True; otherwise they must retain the lease
    behind an identity-bound lifecycle monitor.
    """

    session_stopped: bool
    child_reaped: bool
    pid: int | None = None
    start_identity: str | None = None

    def __bool__(self) -> bool:
        return self.session_stopped


def _reap(pid: int, attempts: int = 10, delay: float = 0.005) -> bool:
    """Non-blocking best-effort reap: poll waitpid(WNOHANG) a few times. Returns
    True if the child was reaped or is already gone, False if still alive."""
    for _ in range(attempts):
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except OSError:
            return True  # no such child — already reaped
        if done == pid:
            return True
        time.sleep(delay)
    return False


def _signal_process(pid: int, value: int) -> None:
    try:
        os.kill(pid, value)
    except OSError:
        pass


def _session_members(sid: int) -> set[int] | None:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,sid="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    members: set[int] = set()
    for line in result.stdout.splitlines():
        try:
            pid_raw, sid_raw = line.split()
            if int(sid_raw) == sid:
                members.add(int(pid_raw))
        except (TypeError, ValueError):
            continue
    return members


def _stop_session(sid: int, leader: int) -> bool:
    members = _session_members(sid)
    if members is None:
        return False
    for pid in members:
        _signal_process(pid, signal.SIGHUP)
    for _ in range(50):
        members = _session_members(sid)
        if members is None:
            return False
        if not members - {leader}:
            return True
        for pid in members:
            _signal_process(pid, signal.SIGKILL)
        time.sleep(0.01)
    return False


class TerminalSession:
    """A PTY-backed login shell. Spawns the shell in `cwd`; the master fd carries
    bidirectional I/O. Child inherits the server's environment (so PATH, etc. are
    already correct) plus TERM for proper rendering in xterm.js."""

    def __init__(
        self,
        cwd: str,
        shell: str = "bash",
        *,
        contained: bool = False,
        activity_lease: Any = None,
    ) -> None:
        self.cwd = cwd
        self.shell = shell
        self.contained = contained
        self.activity_lease = activity_lease
        self.pid: int | None = None
        self.sid: int | None = None
        self.fd: int | None = None
        self.start_identity: str | None = None

    def _argv(self) -> list[str]:
        if not self.contained:
            return [self.shell, "-l"]
        return pid_namespace_argv(
            [self.shell, "-l"],
            cwd=self.cwd,
            label="terminal",
        )

    def start(self) -> None:
        argv = self._argv()
        if self.activity_lease is not None:
            argv, _ = self.activity_lease.guard_process(argv)
        pid, fd = pty.fork()
        if pid == 0:
            # ── child ──
            try:
                os.chdir(self.cwd)
            except Exception:
                pass
            os.environ["TERM"] = "xterm-256color"
            try:
                os.execvp(argv[0], argv)
            except Exception:
                os._exit(1)
        # ── parent ──
        self.pid = pid
        self.fd = fd
        self.start_identity = process_start_identity(pid)
        if self.activity_lease is not None:
            self.activity_lease.mark_process_started()
        for _ in range(50):
            try:
                if os.getsid(pid) == pid:
                    self.sid = pid
                    break
            except OSError:
                break
            time.sleep(0.001)

    def write(self, data: bytes) -> None:
        if self.fd is not None:
            try:
                os.write(self.fd, data)
            except OSError:
                pass

    def resize(self, rows: int, cols: int) -> None:
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            except OSError:
                pass

    def read(self, size: int = 65536) -> bytes:
        """Blocking read (run in an executor thread). Returns b'' on EOF/error."""
        if self.fd is None:
            return b""
        try:
            return os.read(self.fd, size)
        except OSError:
            return b""

    def close(self) -> CloseResult:
        fd, pid, sid = self.fd, self.pid, self.sid
        start_identity = self.start_identity
        self.fd = None
        self.pid = None
        self.sid = None
        self.start_identity = None
        # Close the PTY master first so the shell sees EOF on its controlling tty.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not pid:
            return CloseResult(session_stopped=True, child_reaped=True)
        stopped = False
        if sid is None:
            _signal_process(pid, signal.SIGHUP)
        else:
            stopped = _stop_session(sid, pid)
            if not stopped:
                _signal_process(pid, signal.SIGKILL)
        reaped = _reap(pid, attempts=20, delay=0.005)
        if not reaped:
            _signal_process(pid, signal.SIGKILL)
            reaped = _reap(pid, attempts=20, delay=0.005)
        if not reaped:
            try:
                os.waitpid(pid, 0)
                reaped = True
            except OSError as exc:
                reaped = exc.errno == errno.ECHILD
        return CloseResult(
            session_stopped=sid is not None and stopped,
            child_reaped=reaped,
            pid=pid,
            start_identity=start_identity,
        )

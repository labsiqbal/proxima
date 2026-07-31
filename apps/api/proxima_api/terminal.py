from __future__ import annotations

import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import time
from dataclasses import dataclass
from typing import Any

from .container_activity import process_start_identity
from .process_containment import (
    pid_namespace_argv,
    process_tree_pids,
    terminate_process_tree,
)

logger = logging.getLogger("proxima.terminal")


@dataclass(frozen=True)
class CloseResult:
    """Outcome of shutting down a PTY session.

    ``session_stopped`` is True only when the full identity-bound writer tree
    could be verified empty. ``child_reaped`` is True once that tree is proven
    exited and the direct child is reaped (or was never started). Callers may
    release writer-activity and maintenance-ingress leases only when the
    corresponding flag is True; otherwise they must retain the lease behind an
    identity-bound lifecycle monitor.
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


def _tree_exited(root_pid: int) -> bool | None:
    """True when the tracked tree is gone, False if live, None if unprovable."""
    tree = process_tree_pids(root_pid)
    if tree is None:
        return None
    if not tree:
        return True
    for pid in tree:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            state = stat.rsplit(") ", 1)[1].split()[0]
        except (OSError, IndexError, ValueError):
            continue
        if state != "Z":
            return False
    return True


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
        fd, pid, _sid = self.fd, self.pid, self.sid
        start_identity = self.start_identity
        self.fd = None
        self.pid = None
        self.sid = None
        self.start_identity = None
        if not pid:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            return CloseResult(session_stopped=True, child_reaped=True)
        # Snapshot the writer tree before closing the PTY master. Closing first
        # can kill the shell and reparent background jobs to init, which would
        # hide them from a root-only process-tree walk.
        known = process_tree_pids(pid)
        seed = set(known) if known is not None else {pid}
        # Close the PTY master so the shell sees EOF on its controlling tty.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        # Stop through the identity-bound writer tree (guardian sentinel / shell
        # descendants), never launcher session membership alone. PTY-attached
        # guardians stay in-session, but writers may still setsid() themselves.
        tree_stopped = terminate_process_tree(
            pid,
            grace_seconds=0.5,
            kill_seconds=0.5,
            initial_signal=signal.SIGHUP,
            known_pids=seed,
        )
        if not tree_stopped:
            # One more aggressive pass if the first sweep could not prove exit.
            tree_stopped = terminate_process_tree(
                pid,
                grace_seconds=0.2,
                kill_seconds=0.5,
                known_pids=seed,
            )
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
        proven = tree_stopped
        if proven is not True:
            leftover = _tree_exited(pid)
            if leftover is True:
                proven = True
            elif leftover is False:
                proven = False
            else:
                proven = False
        return CloseResult(
            session_stopped=bool(proven and reaped),
            child_reaped=bool(proven and reaped),
            pid=pid,
            start_identity=start_identity,
        )

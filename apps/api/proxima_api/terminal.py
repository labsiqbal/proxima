from __future__ import annotations

import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import time

logger = logging.getLogger("proxima.terminal")


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


class TerminalSession:
    """A PTY-backed login shell. Spawns the shell in `cwd`; the master fd carries
    bidirectional I/O. Child inherits the server's environment (so PATH, etc. are
    already correct) plus TERM for proper rendering in xterm.js."""

    def __init__(self, cwd: str, shell: str = "bash") -> None:
        self.cwd = cwd
        self.shell = shell
        self.pid: int | None = None
        self.fd: int | None = None

    def start(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:
            # ── child ──
            try:
                os.chdir(self.cwd)
            except Exception:
                pass
            os.environ["TERM"] = "xterm-256color"
            try:
                os.execvp(self.shell, [self.shell, "-l"])
            except Exception:
                os._exit(1)
        # ── parent ──
        self.pid = pid
        self.fd = fd

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

    def close(self) -> None:
        fd, pid = self.fd, self.pid
        self.fd = None
        self.pid = None
        # Close the PTY master first so the shell sees EOF on its controlling tty.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if pid:
            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                pass
            # Actually REAP the child, else it lingers as a zombie (a single
            # WNOHANG almost never catches it mid-exit, and nothing else reaps a
            # raw pty.fork() child). Brief grace for a clean exit, then SIGKILL.
            if not _reap(pid, attempts=10, delay=0.005):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    os.waitpid(pid, 0)  # SIGKILL'd child is reaped near-instantly
                except OSError:
                    pass

from __future__ import annotations

import fcntl
import logging
import os
import pty
import signal
import struct
import subprocess
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

    def __init__(self, cwd: str, shell: str = "bash") -> None:
        self.cwd = cwd
        self.shell = shell
        self.pid: int | None = None
        self.sid: int | None = None
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

    def close(self) -> bool:
        fd, pid, sid = self.fd, self.pid, self.sid
        self.fd = None
        self.pid = None
        self.sid = None
        # Close the PTY master first so the shell sees EOF on its controlling tty.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not pid:
            return True
        if sid is None:
            _signal_process(pid, signal.SIGHUP)
        else:
            stopped = _stop_session(sid, pid)
            if not stopped:
                _signal_process(pid, signal.SIGKILL)
        reaped = _reap(pid, attempts=20, delay=0.005)
        if sid is None and not reaped:
            _signal_process(pid, signal.SIGKILL)
        if not reaped:
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
        return sid is not None and stopped

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any


MAX_PENDING_LINE_BYTES = 16 * 1024

_DISCARD_PROGRAM = """
import os
import select
import sys

fd = int(sys.argv[1])
while True:
    try:
        chunk = os.read(fd, 65536)
    except BlockingIOError:
        try:
            select.select([fd], [], [])
        except InterruptedError:
            continue
        continue
    except InterruptedError:
        continue
    except OSError:
        break
    if not chunk:
        break
os.close(fd)
"""


class BoundedLineBuffer:
    def __init__(self, limit: int = MAX_PENDING_LINE_BYTES) -> None:
        self._limit = limit
        self._pending = bytearray()

    def _append_tail(self, chunk: bytes) -> None:
        if len(chunk) >= self._limit:
            self._pending[:] = chunk[-self._limit:]
            return
        excess = len(self._pending) + len(chunk) - self._limit
        if excess > 0:
            del self._pending[:excess]
        self._pending.extend(chunk)

    def feed(self, chunk: bytes) -> list[bytes]:
        lines: list[bytes] = []
        offset = 0
        while True:
            newline = chunk.find(b"\n", offset)
            if newline < 0:
                self._append_tail(chunk[offset:])
                return lines
            self._append_tail(chunk[offset:newline])
            lines.append(bytes(self._pending))
            self._pending.clear()
            offset = newline + 1

    def finish(self) -> bytes:
        pending = bytes(self._pending)
        self._pending.clear()
        return pending


class DetachedOutputSinks:
    def __init__(self) -> None:
        self._helpers: dict[int, subprocess.Popen[bytes]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _pipe_fd(stdout: Any) -> tuple[int, Any]:
        transport = getattr(stdout, "_transport", None)
        if transport is None:
            raise RuntimeError("Managed output transport is unavailable")
        pipe = transport.get_extra_info("pipe")
        if pipe is None:
            raise RuntimeError("Managed output pipe is unavailable")
        return int(pipe.fileno()), transport

    def handoff(self, stdout: Any) -> int:
        if os.name != "posix":
            raise RuntimeError(
                "Detached output handoff requires a POSIX host"
            )
        fd, transport = self._pipe_fd(stdout)
        helper = subprocess.Popen(
            [sys.executable, "-c", _DISCARD_PROGRAM, str(fd)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath},
            pass_fds=(fd,),
            start_new_session=True,
        )
        with self._lock:
            self._helpers[helper.pid] = helper
        thread = threading.Thread(
            target=self._reap,
            args=(helper,),
            daemon=True,
            name=f"preview-output-{helper.pid}",
        )
        thread.start()
        transport.close()
        return helper.pid

    def _reap(self, helper: subprocess.Popen[bytes]) -> None:
        helper.wait()
        with self._lock:
            self._helpers.pop(helper.pid, None)

    def active_pids(self) -> set[int]:
        with self._lock:
            return set(self._helpers)

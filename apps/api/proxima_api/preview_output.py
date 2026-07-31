from __future__ import annotations

import array
import asyncio
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any


MAX_PENDING_LINE_BYTES = 16 * 1024
MAX_LOG_LINES = 200
BROKER_SOCKET_ENV = "PROXIMA_OUTPUT_BROKER_SOCKET"


class OutputBrokerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputSnapshot:
    lines: list[str]
    eof: bool


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

    def snapshot(self) -> bytes:
        return bytes(self._pending)


class BrokerLog:
    def __init__(self) -> None:
        self._lines: deque[bytes] = deque(maxlen=MAX_LOG_LINES)
        self._pending = BoundedLineBuffer()

    def feed(self, chunk: bytes) -> None:
        self._lines.extend(self._pending.feed(chunk))

    def snapshot(self) -> list[str]:
        raw = list(self._lines)
        pending = self._pending.snapshot()
        if pending:
            raw.append(pending)
        return [
            line.decode("utf-8", "replace").rstrip()
            for line in raw[-MAX_LOG_LINES:]
        ]


class OutputBroker:
    def __init__(
        self,
        control: socket.socket,
        child_output_fd: int,
        *,
        process: subprocess.Popen[bytes] | None,
        supervisor: str,
        pid: int | None,
    ) -> None:
        self._control = control
        self._child_output_fd = child_output_fd
        self._process = process
        self._supervisor = supervisor
        self._pid = pid
        self._lock = threading.Lock()
        self._fd_lock = threading.Lock()
        self._buffer = bytearray()
        self._closed = False

    @property
    def child_output_fd(self) -> int:
        with self._fd_lock:
            if self._child_output_fd < 0:
                raise OutputBrokerUnavailable("Preview output pipe is closed")
            return self._child_output_fd

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def supervisor(self) -> str:
        return self._supervisor

    @classmethod
    async def open(cls) -> OutputBroker:
        try:
            return await asyncio.to_thread(cls._open_sync)
        except OutputBrokerUnavailable:
            raise
        except Exception as exc:
            raise OutputBrokerUnavailable(
                f"Preview output broker could not start: {exc}"
            ) from exc

    @classmethod
    def _open_sync(cls) -> OutputBroker:
        configured = os.environ.get(BROKER_SOCKET_ENV, "").strip()
        if configured:
            return cls._connect_supervised(configured)
        if (
            os.name == "posix"
            and os.environ.get("INVOCATION_ID")
            and os.environ.get("SYSTEMD_EXEC_PID") == str(os.getpid())
        ):
            runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
            if runtime:
                expected = str(
                    Path(runtime) / "proxima" / "preview-output.sock"
                )
            else:
                expected = "the configured systemd broker socket"
            raise OutputBrokerUnavailable(
                f"Preview output broker is unavailable at {expected}. "
                "Reinstall or restart the Proxima service, then retry."
            )
        if os.name == "nt":
            return cls._open_windows_direct()
        return cls._open_posix_direct()

    @classmethod
    def _connect_supervised(cls, path: str) -> OutputBroker:
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        control.settimeout(5)
        received_fds: list[int] = []
        try:
            control.connect(path)
            payload, ancillary, _flags, _address = control.recvmsg(
                4096,
                socket.CMSG_SPACE(array.array("i").itemsize),
            )
            descriptors = array.array("i")
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors.frombytes(
                        data[: len(data) - (len(data) % descriptors.itemsize)]
                    )
            received_fds = [int(fd) for fd in descriptors]
            if not descriptors:
                raise OutputBrokerUnavailable(
                    "Supervisor broker did not provide an output descriptor"
                )
            ready = cls._receive_json(control, bytearray(payload))
            broker = cls(
                control,
                received_fds.pop(0),
                process=None,
                supervisor="systemd",
                pid=(
                    int(ready["pid"])
                    if isinstance(ready, dict)
                    and isinstance(ready.get("pid"), int)
                    else None
                ),
            )
            for fd in received_fds:
                cls._close_fd(fd)
            return broker
        except Exception:
            control.close()
            for fd in received_fds:
                cls._close_fd(fd)
            raise

    @staticmethod
    def _broker_env() -> dict[str, str]:
        environment = {
            "PATH": os.defpath,
            "PYTHONPATH": os.pathsep.join(sys.path),
        }
        for key in ("SYSTEMROOT", "WINDIR"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
        return environment

    @classmethod
    def _open_posix_direct(cls) -> OutputBroker:
        read_fd, write_fd = os.pipe()
        parent, child = socket.socketpair()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "proxima_api.preview_output_broker",
                    "--control-fd",
                    str(child.fileno()),
                    "--read-fd",
                    str(read_fd),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=cls._broker_env(),
                pass_fds=(child.fileno(), read_fd),
                start_new_session=True,
            )
            child.close()
            os.close(read_fd)
            parent.settimeout(5)
            ready = cls._receive_json(parent, bytearray())
            broker = cls(
                parent,
                write_fd,
                process=process,
                supervisor="process",
                pid=(
                    int(ready["pid"])
                    if isinstance(ready, dict)
                    and isinstance(ready.get("pid"), int)
                    else process.pid
                ),
            )
            broker._start_reaper()
            return broker
        except Exception:
            parent.close()
            child.close()
            cls._close_fd(read_fd)
            cls._close_fd(write_fd)
            if process is not None and process.poll() is None:
                process.terminate()
            raise

    @classmethod
    def _open_windows_direct(cls) -> OutputBroker:
        breakaway = getattr(
            subprocess,
            "CREATE_BREAKAWAY_FROM_JOB",
            0,
        )
        if not breakaway:
            raise OutputBrokerUnavailable(
                "Windows breakaway preview output is unsupported on this host. "
                "The app was not launched; retry on a supported service host."
            )
        import msvcrt

        read_fd, write_fd = os.pipe()
        flags = (
            breakaway
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        process: subprocess.Popen[bytes] | None = None
        control: socket.socket | None = None
        probe: socket.socket | None = None
        try:
            read_handle = msvcrt.get_osfhandle(read_fd)
            os.set_handle_inheritable(read_handle, True)
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
            probe.close()
            probe = None
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "proxima_api.preview_output_broker",
                    "--read-handle",
                    str(read_handle),
                    "--listen-port",
                    str(port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=cls._broker_env(),
                close_fds=False,
                creationflags=flags,
            )
            os.close(read_fd)
            deadline = time.monotonic() + 5
            while True:
                try:
                    control = socket.create_connection(
                        ("127.0.0.1", port),
                        timeout=0.25,
                    )
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise OutputBrokerUnavailable(
                            "Windows cannot start a breakaway preview output "
                            "broker on this host. Stop completed without "
                            "launching the app; retry after enabling job "
                            "breakaway support."
                        )
                    time.sleep(0.02)
            ready = cls._receive_json(control, bytearray())
            broker = cls(
                control,
                write_fd,
                process=process,
                supervisor="windows-breakaway",
                pid=(
                    int(ready["pid"])
                    if isinstance(ready, dict)
                    and isinstance(ready.get("pid"), int)
                    else process.pid
                ),
            )
            broker._start_reaper()
            return broker
        except Exception:
            if probe is not None:
                probe.close()
            if control is not None:
                control.close()
            cls._close_fd(read_fd)
            cls._close_fd(write_fd)
            if process is not None and process.poll() is None:
                process.terminate()
            raise

    def _start_reaper(self) -> None:
        if self._process is None:
            return
        thread = threading.Thread(
            target=self._process.wait,
            daemon=True,
            name=f"preview-output-{self._process.pid}",
        )
        thread.start()

    @staticmethod
    def _close_fd(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def close_child_output(self) -> None:
        with self._fd_lock:
            if self._child_output_fd < 0:
                return
            self._close_fd(self._child_output_fd)
            self._child_output_fd = -1

    async def snapshot(self) -> OutputSnapshot:
        return await asyncio.to_thread(self._snapshot_sync)

    def _snapshot_sync(self) -> OutputSnapshot:
        with self._lock:
            if self._closed:
                raise OutputBrokerUnavailable(
                    "Preview output broker disconnected"
                )
            try:
                self._control.sendall(b'{"op":"snapshot"}\n')
                payload = self._receive_json(self._control, self._buffer)
            except Exception as exc:
                raise OutputBrokerUnavailable(
                    f"Preview output broker disconnected: {exc}"
                ) from exc
        if not isinstance(payload, dict):
            raise OutputBrokerUnavailable(
                "Preview output broker returned an invalid snapshot"
            )
        lines = payload.get("lines")
        if not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines
        ):
            raise OutputBrokerUnavailable(
                "Preview output broker returned invalid log lines"
            )
        return OutputSnapshot(
            lines=list(lines[-MAX_LOG_LINES:]),
            eof=payload.get("eof") is True,
        )

    @staticmethod
    def _receive_json(
        control: socket.socket,
        buffer: bytearray,
    ) -> Any:
        while b"\n" not in buffer:
            chunk = control.recv(65536)
            if not chunk:
                raise OutputBrokerUnavailable(
                    "Preview output broker closed its control channel"
                )
            buffer.extend(chunk)
        raw, remainder = bytes(buffer).split(b"\n", 1)
        buffer[:] = remainder
        return json.loads(raw)

    async def disconnect(self) -> None:
        await asyncio.to_thread(self._disconnect_sync)

    def _disconnect_sync(self) -> None:
        self.close_child_output()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._control.close()

from __future__ import annotations

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
BROKER_PROTOCOL_ENV = "PROXIMA_OUTPUT_BROKER_PROTOCOL"
BROKER_PROFILE_ENV = "PROXIMA_PREVIEW_PROFILE"
BROKER_STATE_ROOT_ENV = "PROXIMA_PREVIEW_SCOPE_STATE_ROOT"
BROKER_PROTOCOL = "proxima-preview-supervisor-v1"


class OutputBrokerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputSnapshot:
    lines: list[str]
    eof: bool
    version: int = 0
    line_cursor: int = 0


@dataclass(frozen=True)
class OutputDelta:
    lines: list[str]
    pending: str
    eof: bool
    version: int
    line_cursor: int
    reset: bool
    changed: bool = True


def process_start_time(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(") ", 1)[1].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


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
        self._lines: deque[tuple[int, bytes]] = deque(maxlen=MAX_LOG_LINES)
        self._pending = BoundedLineBuffer()
        self._version = 0
        self._line_cursor = 0

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._version += 1
        for line in self._pending.feed(chunk):
            self._line_cursor += 1
            self._lines.append((self._line_cursor, line))

    @staticmethod
    def _decode(line: bytes) -> str:
        return line.decode("utf-8", "replace").rstrip()

    def snapshot(self) -> list[str]:
        raw = [line for _cursor, line in self._lines]
        pending = self._pending.snapshot()
        if pending:
            raw.append(pending)
        return [self._decode(line) for line in raw[-MAX_LOG_LINES:]]

    def state(
        self,
        *,
        since_version: int | None = None,
        after_line: int | None = None,
    ) -> dict[str, object]:
        if since_version == self._version:
            return {
                "changed": False,
                "version": self._version,
                "line_cursor": self._line_cursor,
            }
        earliest = (
            self._lines[0][0]
            if self._lines
            else self._line_cursor + 1
        )
        reset = (
            after_line is None
            or after_line < earliest - 1
            or after_line > self._line_cursor
        )
        selected = (
            list(self._lines)
            if reset
            else [
                item
                for item in self._lines
                if item[0] > int(after_line)
            ]
        )
        return {
            "changed": True,
            "reset": reset,
            "lines": [self._decode(line) for _cursor, line in selected],
            "pending": self._decode(self._pending.snapshot()),
            "version": self._version,
            "line_cursor": self._line_cursor,
        }


class BrokerManagedProcess:
    def __init__(
        self,
        broker: OutputBroker,
        *,
        pid: int,
        start_time: int | None,
        returncode: int | None,
        containment_pid_namespace: int | None,
    ) -> None:
        self._broker = broker
        self.pid = int(pid)
        self.start_time = start_time
        self.returncode = returncode
        self.containment_pid_namespace = containment_pid_namespace

    def _apply(self, payload: dict[str, Any]) -> None:
        if int(payload.get("pid") or 0) != self.pid:
            raise OutputBrokerUnavailable(
                "Preview supervisor process identity changed"
            )
        start_time = payload.get("start_time")
        if (
            self.start_time is not None
            and start_time is not None
            and int(start_time) != self.start_time
        ):
            raise OutputBrokerUnavailable(
                "Preview supervisor process generation changed"
            )
        self.returncode = (
            int(payload["returncode"])
            if isinstance(payload.get("returncode"), int)
            else None
        )
        namespace = payload.get("containment_pid_namespace")
        self.containment_pid_namespace = (
            int(namespace)
            if isinstance(namespace, int) and namespace > 0
            else None
        )

    async def refresh(self) -> int | None:
        payload = await self._broker.process_status()
        self._apply(payload)
        return self.returncode

    async def wait(self) -> int:
        while await self.refresh() is None:
            await asyncio.sleep(0.05)
        return int(self.returncode)

    async def terminate(self) -> None:
        await self._broker.signal_process("term")
        await self.refresh()

    async def kill(self) -> None:
        await self._broker.signal_process("kill")
        await self.refresh()


class OutputBroker:
    def __init__(
        self,
        control: socket.socket,
        *,
        process: subprocess.Popen[bytes] | None,
        supervisor: str,
        identity: dict[str, Any],
    ) -> None:
        self._control = control
        self._process = process
        self._supervisor = supervisor
        self._identity = dict(identity)
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._closed = False

    @property
    def pid(self) -> int | None:
        value = self._identity.get("pid")
        return int(value) if isinstance(value, int) else None

    @property
    def supervisor(self) -> str:
        return self._supervisor

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "supervisor": self._supervisor,
            **self._identity,
        }

    @classmethod
    async def open(cls) -> OutputBroker:
        try:
            return await asyncio.to_thread(cls._open_sync)
        except OutputBrokerUnavailable:
            raise
        except Exception as exc:
            raise OutputBrokerUnavailable(
                f"Preview supervisor could not start: {exc}"
            ) from exc

    @classmethod
    async def reconnect(
        cls,
        metadata: dict[str, Any],
    ) -> OutputBroker:
        try:
            return await asyncio.to_thread(
                cls._reconnect_sync,
                metadata,
            )
        except OutputBrokerUnavailable:
            raise
        except Exception as exc:
            raise OutputBrokerUnavailable(
                f"Preview supervisor could not be adopted: {exc}"
            ) from exc

    @classmethod
    def _expected_protocol(cls) -> str:
        return (
            os.environ.get(BROKER_PROTOCOL_ENV, "").strip()
            or BROKER_PROTOCOL
        )

    @classmethod
    def _expected_profile(cls) -> str:
        return (
            os.environ.get(BROKER_PROFILE_ENV, "").strip()
            or "direct"
        )

    @classmethod
    def _validate_identity(
        cls,
        identity: dict[str, Any],
        *,
        expected: dict[str, Any] | None = None,
    ) -> None:
        required = (
            "protocol",
            "profile",
            "session_id",
            "token",
            "endpoint",
            "pid",
            "start_time",
            "cgroup",
            "controller_cgroup",
        )
        if any(key not in identity for key in required):
            raise OutputBrokerUnavailable(
                "Preview supervisor returned incomplete identity"
            )
        if identity["protocol"] != cls._expected_protocol():
            raise OutputBrokerUnavailable(
                "Preview supervisor protocol does not match this profile"
            )
        if identity["profile"] != cls._expected_profile():
            raise OutputBrokerUnavailable(
                "Preview supervisor profile does not match this service"
            )
        if expected is not None:
            for key in required:
                if identity.get(key) != expected.get(key):
                    raise OutputBrokerUnavailable(
                        "Preview supervisor durable identity changed"
                    )

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
            raise OutputBrokerUnavailable(
                "Preview supervisor is unavailable for this systemd profile. "
                "Install and start its profile-specific socket, then retry."
            )
        if os.name == "nt":
            return cls._open_windows_direct()
        return cls._open_posix_direct()

    @classmethod
    def _connect_supervised(cls, path: str) -> OutputBroker:
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        control.settimeout(5)
        try:
            control.connect(path)
            identity = cls._receive_json(control, bytearray())
            if not isinstance(identity, dict):
                raise OutputBrokerUnavailable(
                    "Preview supervisor returned an invalid identity"
                )
            cls._validate_identity(identity)
            return cls(
                control,
                process=None,
                supervisor="systemd",
                identity=identity,
            )
        except Exception:
            control.close()
            raise

    @classmethod
    def _connect_endpoint(
        cls,
        endpoint: dict[str, Any],
    ) -> socket.socket:
        kind = endpoint.get("kind")
        if kind == "unix" and isinstance(endpoint.get("path"), str):
            control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            control.settimeout(5)
            control.connect(endpoint["path"])
            return control
        if (
            kind == "abstract"
            and os.name == "posix"
            and isinstance(endpoint.get("name"), str)
        ):
            control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            control.settimeout(5)
            control.connect("\0" + endpoint["name"])
            return control
        if (
            kind == "tcp"
            and isinstance(endpoint.get("host"), str)
            and isinstance(endpoint.get("port"), int)
        ):
            return socket.create_connection(
                (endpoint["host"], int(endpoint["port"])),
                timeout=5,
            )
        raise OutputBrokerUnavailable(
            "Preview supervisor reconnect endpoint is invalid"
        )

    @classmethod
    def _reconnect_sync(
        cls,
        metadata: dict[str, Any],
    ) -> OutputBroker:
        endpoint = metadata.get("endpoint")
        if not isinstance(endpoint, dict):
            raise OutputBrokerUnavailable(
                "Preview supervisor reconnect endpoint is missing"
            )
        control = cls._connect_endpoint(endpoint)
        try:
            control.sendall(
                json.dumps(
                    {
                        "op": "attach",
                        "protocol": metadata.get("protocol"),
                        "profile": metadata.get("profile"),
                        "session_id": metadata.get("session_id"),
                        "token": metadata.get("token"),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            identity = cls._receive_json(control, bytearray())
            if not isinstance(identity, dict):
                raise OutputBrokerUnavailable(
                    "Preview supervisor adoption response is invalid"
                )
            cls._validate_identity(identity, expected=metadata)
            return cls(
                control,
                process=None,
                supervisor=str(metadata.get("supervisor") or "adopted"),
                identity=identity,
            )
        except Exception:
            control.close()
            raise

    @staticmethod
    def _broker_env() -> dict[str, str]:
        environment = {
            "PATH": os.defpath,
            "PYTHONPATH": os.pathsep.join(sys.path),
        }
        for key in (
            "SYSTEMROOT",
            "WINDIR",
            BROKER_PROTOCOL_ENV,
            BROKER_PROFILE_ENV,
            BROKER_STATE_ROOT_ENV,
        ):
            value = os.environ.get(key)
            if value:
                environment[key] = value
        return environment

    @staticmethod
    def _terminate_and_reap(
        process: subprocess.Popen[bytes],
        *,
        timeout: float = 1.0,
    ) -> None:
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise OutputBrokerUnavailable(
                "Preview supervisor did not exit after launch failure"
            ) from exc

    @classmethod
    def _open_posix_direct(cls) -> OutputBroker:
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
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=cls._broker_env(),
                pass_fds=(child.fileno(),),
                start_new_session=True,
            )
            child.close()
            parent.settimeout(5)
            identity = cls._receive_json(parent, bytearray())
            if not isinstance(identity, dict):
                raise OutputBrokerUnavailable(
                    "Preview supervisor returned an invalid identity"
                )
            cls._validate_identity(identity)
            broker = cls(
                parent,
                process=process,
                supervisor="process",
                identity=identity,
            )
            broker._start_reaper()
            return broker
        except Exception:
            parent.close()
            child.close()
            if process is not None:
                cls._terminate_and_reap(process)
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
                "Windows breakaway preview supervision is unsupported on "
                "this host. The app was not launched; retry on a supported "
                "service host."
            )
        flags = (
            breakaway
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        process: subprocess.Popen[bytes] | None = None
        control: socket.socket | None = None
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
            probe.close()
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "proxima_api.preview_output_broker",
                    "--listen-port",
                    str(port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=cls._broker_env(),
                creationflags=flags,
            )
            deadline = time.monotonic() + 5
            while True:
                try:
                    control = socket.create_connection(
                        ("127.0.0.1", port),
                        timeout=0.25,
                    )
                    break
                except OSError:
                    if (
                        process.poll() is not None
                        or time.monotonic() >= deadline
                    ):
                        raise OutputBrokerUnavailable(
                            "Windows cannot start a breakaway preview "
                            "supervisor on this host."
                        )
                    time.sleep(0.02)
            identity = cls._receive_json(control, bytearray())
            if not isinstance(identity, dict):
                raise OutputBrokerUnavailable(
                    "Preview supervisor returned an invalid identity"
                )
            cls._validate_identity(identity)
            broker = cls(
                control,
                process=process,
                supervisor="windows-breakaway",
                identity=identity,
            )
            broker._start_reaper()
            return broker
        except Exception:
            probe.close()
            if control is not None:
                control.close()
            if process is not None:
                cls._terminate_and_reap(process)
            raise

    def _start_reaper(self) -> None:
        if self._process is None:
            return
        thread = threading.Thread(
            target=self._process.wait,
            daemon=True,
            name=f"preview-supervisor-{self._process.pid}",
        )
        thread.start()

    def _request_sync(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise OutputBrokerUnavailable(
                    "Preview supervisor disconnected"
                )
            try:
                self._control.sendall(
                    json.dumps(
                        payload,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                response = self._receive_json(
                    self._control,
                    self._buffer,
                )
            except Exception as exc:
                raise OutputBrokerUnavailable(
                    f"Preview supervisor disconnected: {exc}"
                ) from exc
        if not isinstance(response, dict):
            raise OutputBrokerUnavailable(
                "Preview supervisor returned an invalid response"
            )
        if isinstance(response.get("error"), str):
            raise OutputBrokerUnavailable(response["error"])
        return response

    async def spawn(
        self,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        contained: bool,
    ) -> BrokerManagedProcess:
        payload = await asyncio.to_thread(
            self._request_sync,
            {
                "op": "spawn",
                "argv": list(argv),
                "cwd": cwd,
                "env": env,
                "contained": bool(contained),
            },
        )
        return self._process_from_payload(payload)

    def _process_from_payload(
        self,
        payload: dict[str, Any],
    ) -> BrokerManagedProcess:
        pid = payload.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise OutputBrokerUnavailable(
                "Preview supervisor returned an invalid process identity"
            )
        return BrokerManagedProcess(
            self,
            pid=pid,
            start_time=(
                int(payload["start_time"])
                if isinstance(payload.get("start_time"), int)
                else None
            ),
            returncode=(
                int(payload["returncode"])
                if isinstance(payload.get("returncode"), int)
                else None
            ),
            containment_pid_namespace=(
                int(payload["containment_pid_namespace"])
                if isinstance(
                    payload.get("containment_pid_namespace"),
                    int,
                )
                else None
            ),
        )

    async def managed_process(self) -> BrokerManagedProcess:
        return self._process_from_payload(await self.process_status())

    async def process_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request_sync,
            {"op": "process_status"},
        )

    async def signal_process(self, kind: str) -> None:
        await asyncio.to_thread(
            self._request_sync,
            {"op": "signal", "kind": kind},
        )

    async def changes(
        self,
        *,
        since_version: int,
        after_line: int,
    ) -> OutputDelta:
        payload = await asyncio.to_thread(
            self._request_sync,
            {
                "op": "changes",
                "since_version": int(since_version),
                "after_line": int(after_line),
            },
        )
        lines = payload.get("lines", [])
        if not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines
        ):
            raise OutputBrokerUnavailable(
                "Preview supervisor returned invalid log changes"
            )
        pending = payload.get("pending", "")
        if not isinstance(pending, str):
            raise OutputBrokerUnavailable(
                "Preview supervisor returned an invalid partial line"
            )
        return OutputDelta(
            lines=list(lines[-MAX_LOG_LINES:]),
            pending=pending,
            eof=payload.get("eof") is True,
            version=int(payload.get("version") or 0),
            line_cursor=int(payload.get("line_cursor") or 0),
            reset=payload.get("reset") is True,
            changed=payload.get("changed") is not False,
        )

    async def snapshot(self) -> OutputSnapshot:
        payload = await asyncio.to_thread(
            self._request_sync,
            {"op": "snapshot"},
        )
        lines = payload.get("lines")
        if not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines
        ):
            raise OutputBrokerUnavailable(
                "Preview supervisor returned invalid log lines"
            )
        return OutputSnapshot(
            lines=list(lines[-MAX_LOG_LINES:]),
            eof=payload.get("eof") is True,
            version=int(payload.get("version") or 0),
            line_cursor=int(payload.get("line_cursor") or 0),
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
                    "Preview supervisor closed its control channel"
                )
            buffer.extend(chunk)
        raw, remainder = bytes(buffer).split(b"\n", 1)
        buffer[:] = remainder
        return json.loads(raw)

    async def disconnect(self) -> None:
        await asyncio.to_thread(self._disconnect_sync)

    def _disconnect_sync(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._control.close()


async def _check() -> int:
    broker = await OutputBroker.open()
    try:
        print(
            json.dumps(
                {
                    "protocol": broker.metadata["protocol"],
                    "profile": broker.metadata["profile"],
                    "supervisor": broker.supervisor,
                },
                sort_keys=True,
            )
        )
    finally:
        await broker.disconnect()
    return 0


def main() -> int:
    return asyncio.run(_check())


if __name__ == "__main__":
    raise SystemExit(main())

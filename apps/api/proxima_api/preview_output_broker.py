from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import struct
import subprocess
import sys
from typing import Any

from .preview_output import (
    BROKER_PROFILE_ENV,
    BROKER_PROTOCOL,
    BROKER_PROTOCOL_ENV,
    BROKER_STATE_ROOT_ENV,
    BrokerLog,
    cgroup_is_within,
    process_start_time,
)
from .process_containment import pid_namespace_argv


def _pipe_available(read_fd: int) -> int:
    if os.name == "nt":
        import ctypes
        import msvcrt

        available = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.PeekNamedPipe(
            msvcrt.get_osfhandle(read_fd),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        )
        return int(available.value) if ok else 0
    import array
    import fcntl
    import termios

    pending = array.array("i", [0])
    fcntl.ioctl(read_fd, termios.FIONREAD, pending, True)
    return max(0, int(pending[0]))


def _drain_pipe(
    read_fd: int,
    log: BrokerLog,
    *,
    limit: int,
) -> tuple[bool, int]:
    drained = 0
    while drained < limit:
        try:
            chunk = os.read(read_fd, min(65536, limit - drained))
        except BlockingIOError:
            return False, drained
        except InterruptedError:
            continue
        except OSError:
            return True, drained
        if not chunk:
            return True, drained
        log.feed(chunk)
        drained += len(chunk)
    return False, drained


def _send_json(
    control: socket.socket,
    payload: dict[str, object],
) -> bool:
    previous_timeout = control.gettimeout()
    try:
        control.settimeout(5)
        control.sendall(
            json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        return True
    except OSError:
        return False
    finally:
        try:
            control.settimeout(previous_timeout)
        except OSError:
            pass


def _cgroup_identity(pid: int) -> str | None:
    try:
        return Path(f"/proc/{int(pid)}/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None


def _pidfd_open(pid: int) -> int:
    native = getattr(os, "pidfd_open", None)
    if native is not None:
        return int(native(pid))
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "pidfd_open", None)
    if function is None:
        raise OSError("pidfd_open is unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = int(function(pid, 0))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def _pidfd_send_signal(pidfd: int, signal_number: int) -> None:
    native = getattr(signal, "pidfd_send_signal", None)
    if native is not None:
        native(pidfd, signal_number)
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "pidfd_send_signal", None)
    if function is None:
        raise OSError("pidfd_send_signal is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = int(function(pidfd, signal_number, None, 0))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


class PreviewSupervisor:
    def __init__(self, initial_control: socket.socket) -> None:
        self.protocol = (
            os.environ.get(BROKER_PROTOCOL_ENV, "").strip() or BROKER_PROTOCOL
        )
        self.profile = os.environ.get(BROKER_PROFILE_ENV, "").strip() or "direct"
        self.session_id = secrets.token_urlsafe(18)
        self.token = secrets.token_urlsafe(32)
        self.initial_control = initial_control
        self.controller_cgroup = self._peer_cgroup(initial_control)
        self.listener, self.endpoint, self.socket_path = self._reconnect_listener()
        self.process: subprocess.Popen[bytes] | None = None
        self.process_start_time: int | None = None
        self.read_fd: int | None = None
        self.write_fd: int | None = None
        self.info_fd: int | None = None
        self.info_buffer = bytearray()
        self.containment_pid_namespace: int | None = None
        self.app_cgroup: Path | None = None
        self.app_cgroup_identity: str | None = None
        self.log = BrokerLog()
        self.eof = False

    @staticmethod
    def _peer_cgroup(control: socket.socket) -> str | None:
        if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
            return None
        try:
            credentials = control.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            )
            pid, _uid, _gid = struct.unpack("3i", credentials)
        except OSError:
            return None
        return _cgroup_identity(pid)

    def _reconnect_listener(
        self,
    ) -> tuple[socket.socket, dict[str, object], Path | None]:
        state_root = os.environ.get(BROKER_STATE_ROOT_ENV, "").strip()
        if os.name == "posix" and state_root:
            root = Path(state_root) / "control"
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(root, 0o700)
            path = root / f"{self.session_id}.sock"
            if len(os.fsencode(path)) < 100:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(str(path))
                os.chmod(path, 0o600)
                listener.listen(4)
                listener.setblocking(False)
                return listener, {"kind": "unix", "path": str(path)}, path
            if sys.platform.startswith("linux"):
                name = f"proxima-preview-{self.session_id}"
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind("\0" + name)
                listener.listen(4)
                listener.setblocking(False)
                return listener, {"kind": "abstract", "name": name}, None
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        listener.setblocking(False)
        return (
            listener,
            {
                "kind": "tcp",
                "host": "127.0.0.1",
                "port": int(listener.getsockname()[1]),
            },
            None,
        )

    def identity(self) -> dict[str, object]:
        pid = os.getpid()
        return {
            "protocol": self.protocol,
            "profile": self.profile,
            "session_id": self.session_id,
            "token": self.token,
            "endpoint": self.endpoint,
            "pid": pid,
            "start_time": process_start_time(pid),
            "cgroup": _cgroup_identity(pid),
            "controller_cgroup": self.controller_cgroup,
        }

    def process_status(self) -> dict[str, object]:
        if self.process is None:
            return {"error": "Preview process has not been launched"}
        return {
            "pid": self.process.pid,
            "start_time": self.process_start_time,
            "returncode": self.process.poll(),
            "containment_pid_namespace": self.containment_pid_namespace,
            "cgroup": _cgroup_identity(self.process.pid),
            "app_cgroup": (
                str(self.app_cgroup) if self.app_cgroup is not None else None
            ),
            "scope_live": bool(self._app_cgroup_pids()),
            "managed_cgroup": self.app_cgroup_identity,
        }

    def _app_cgroup_pids(self) -> set[int]:
        if self.app_cgroup is None:
            return set()
        pending = [self.app_cgroup]
        pids: set[int] = set()
        while pending:
            current = pending.pop()
            try:
                pending.extend(path for path in current.iterdir() if path.is_dir())
                pids.update(
                    int(raw_pid)
                    for raw_pid in current.joinpath("cgroup.procs")
                    .read_text(encoding="ascii")
                    .split()
                )
            except (OSError, ValueError):
                continue
        return pids

    def _remove_app_cgroup(self) -> None:
        if self.app_cgroup is None:
            return
        try:
            descendants = sorted(
                (path for path in self.app_cgroup.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
        except OSError:
            descendants = []
        for path in [*descendants, self.app_cgroup]:
            try:
                path.rmdir()
            except OSError:
                pass

    def _prepare_app_cgroup(self) -> Path | None:
        if (
            not sys.platform.startswith("linux")
            or not os.environ.get("INVOCATION_ID")
        ):
            return None
        identity = _cgroup_identity(os.getpid())
        if not isinstance(identity, str):
            return None
        unified = next(
            (line[3:] for line in identity.splitlines() if line.startswith("0::/")),
            None,
        )
        if unified is None:
            return None
        root = Path("/sys/fs/cgroup") / unified.lstrip("/")
        target = root / f"preview-app-{self.session_id}"
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            return None
        except OSError:
            return None
        return target

    def _spawn(self, command: dict[str, Any]) -> dict[str, object]:
        if self.process is not None:
            return {"error": "Preview process has already been launched"}
        argv = command.get("argv")
        cwd = command.get("cwd")
        env = command.get("env")
        contained = command.get("contained") is True
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) for item in argv)
            or not isinstance(cwd, str)
            or not isinstance(env, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in env.items()
            )
        ):
            return {"error": "Preview launch request is invalid"}
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.read_fd = read_fd
        self.write_fd = write_fd
        extra: dict[str, Any]
        launch_argv = list(argv)
        containment_write_fd: int | None = None
        cgroup_ready_read_fd: int | None = None
        cgroup_ready_write_fd: int | None = None
        cgroup_release_read_fd: int | None = None
        cgroup_release_write_fd: int | None = None
        if os.name == "nt":
            extra = {
                "creationflags": getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0,
                )
            }
        else:
            extra = {"start_new_session": True}
        try:
            self.app_cgroup = self._prepare_app_cgroup()
            if os.environ.get("INVOCATION_ID") and self.app_cgroup is None:
                return {"error": "Launch-specific preview cgroup is unavailable"}
            if contained:
                if os.name != "posix":
                    return {"error": "Preview containment is unavailable"}
                self.info_fd, containment_write_fd = os.pipe()
                os.set_blocking(self.info_fd, False)
                launch_argv = pid_namespace_argv(
                    launch_argv,
                    cwd=cwd,
                    label="project app",
                    info_fd=containment_write_fd,
                )
            if self.app_cgroup is not None:
                cgroup_ready_read_fd, cgroup_ready_write_fd = os.pipe()
                cgroup_release_read_fd, cgroup_release_write_fd = os.pipe()
                launch_argv = [
                    sys.executable,
                    "-I",
                    "-S",
                    str(Path(__file__).with_name("preview_app_launcher.py")),
                    str(self.app_cgroup / "cgroup.procs"),
                    str(cgroup_ready_write_fd),
                    str(cgroup_release_read_fd),
                    *launch_argv,
                ]
            pass_fds = tuple(
                fd
                for fd in (
                    containment_write_fd,
                    cgroup_ready_write_fd,
                    cgroup_release_read_fd,
                )
                if fd is not None
            )
            if pass_fds:
                extra["pass_fds"] = pass_fds
            self.process = subprocess.Popen(
                launch_argv,
                cwd=cwd,
                env=env,
                stdout=write_fd,
                stderr=subprocess.STDOUT,
                **extra,
            )
            self.process_start_time = process_start_time(self.process.pid)
            if cgroup_ready_read_fd is not None:
                readable, _, _ = select.select(
                    [cgroup_ready_read_fd],
                    [],
                    [],
                    2,
                )
                if not readable or os.read(cgroup_ready_read_fd, 1) != b"1":
                    raise RuntimeError(
                        "Preview process did not enter its managed cgroup"
                    )
                observed_cgroup = _cgroup_identity(self.process.pid)
                expected_cgroup = (
                    "0::/"
                    + self.app_cgroup.relative_to("/sys/fs/cgroup").as_posix()
                    + "\n"
                )
                if not (
                    cgroup_is_within(observed_cgroup, expected_cgroup)
                    and cgroup_is_within(expected_cgroup, observed_cgroup)
                ):
                    raise RuntimeError(
                        "Preview process escaped its managed cgroup before launch"
                    )
                self.app_cgroup_identity = observed_cgroup
                os.write(cgroup_release_write_fd, b"1")
        except Exception as exc:
            if self.process is not None and self.process.poll() is None:
                try:
                    os.killpg(
                        os.getpgid(self.process.pid),
                        signal.SIGKILL,
                    )
                except OSError:
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            return {"error": f"Preview process could not start: {exc}"}
        finally:
            for fd in (
                containment_write_fd,
                cgroup_ready_read_fd,
                cgroup_ready_write_fd,
                cgroup_release_read_fd,
                cgroup_release_write_fd,
            ):
                if fd is None:
                    continue
                try:
                    os.close(fd)
                except OSError:
                    pass
            if self.write_fd is not None:
                try:
                    os.close(self.write_fd)
                except OSError:
                    pass
                self.write_fd = None
        return self.process_status()

    def _read_containment(self) -> None:
        if self.info_fd is None:
            return
        while True:
            try:
                chunk = os.read(self.info_fd, 4096)
            except BlockingIOError:
                return
            except OSError:
                chunk = b""
            if not chunk:
                try:
                    os.close(self.info_fd)
                except OSError:
                    pass
                self.info_fd = None
                return
            self.info_buffer.extend(chunk)
            try:
                payload = json.loads(self.info_buffer)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            namespace = payload.get("pid-namespace")
            if isinstance(namespace, int) and namespace > 0:
                self.containment_pid_namespace = namespace
            try:
                os.close(self.info_fd)
            except OSError:
                pass
            self.info_fd = None
            return

    def _signal(self, kind: object) -> dict[str, object]:
        if self.process is None:
            return {"error": "Preview process has not been launched"}
        if kind not in ("term", "kill"):
            return {"error": "Preview signal is invalid"}
        if self.process.poll() is not None and not self._app_cgroup_pids():
            return self.process_status()
        try:
            if os.name == "nt":
                if kind == "kill":
                    subprocess.run(
                        [
                            "taskkill",
                            "/F",
                            "/T",
                            "/PID",
                            str(self.process.pid),
                        ],
                        capture_output=True,
                        check=False,
                    )
                else:
                    self.process.terminate()
            elif self.app_cgroup is not None:
                signal_number = signal.SIGKILL if kind == "kill" else signal.SIGTERM
                if kind == "kill":
                    try:
                        self.app_cgroup.joinpath("cgroup.kill").write_text(
                            "1",
                            encoding="ascii",
                        )
                    except OSError as exc:
                        return {
                            "error": (
                                f"Preview managed cgroup could not be terminated: {exc}"
                            )
                        }
                    return self.process_status()
                pending = [self.app_cgroup]
                seen: set[int] = set()
                while pending:
                    current = pending.pop()
                    try:
                        pending.extend(
                            path for path in current.iterdir() if path.is_dir()
                        )
                        pids = (
                            current.joinpath("cgroup.procs")
                            .read_text(encoding="ascii")
                            .split()
                        )
                    except OSError:
                        continue
                    for raw_pid in pids:
                        pid = int(raw_pid)
                        if pid in seen:
                            continue
                        seen.add(pid)
                        pidfd: int | None = None
                        try:
                            pidfd = _pidfd_open(pid)
                            if not cgroup_is_within(
                                _cgroup_identity(pid),
                                self.app_cgroup_identity,
                            ):
                                continue
                            _pidfd_send_signal(pidfd, signal_number)
                        except (OSError, ProcessLookupError):
                            continue
                        finally:
                            if pidfd is not None:
                                try:
                                    os.close(pidfd)
                                except OSError:
                                    pass
            else:
                os.killpg(
                    os.getpgid(self.process.pid),
                    signal.SIGKILL if kind == "kill" else signal.SIGTERM,
                )
        except (OSError, ProcessLookupError):
            pass
        return self.process_status()

    def _flush_available(self) -> None:
        if self.read_fd is None or self.eof:
            return
        pending = _pipe_available(self.read_fd)
        if pending:
            self.eof, _drained = _drain_pipe(
                self.read_fd,
                self.log,
                limit=pending,
            )
        if not self.eof:
            self.eof, _drained = _drain_pipe(
                self.read_fd,
                self.log,
                limit=1,
            )

    def command(self, command: object) -> dict[str, object]:
        if not isinstance(command, dict):
            return {"error": "Preview supervisor command is invalid"}
        operation = command.get("op")
        if operation == "spawn":
            return self._spawn(command)
        if operation == "process_status":
            self._read_containment()
            return self.process_status()
        if operation == "has_process":
            return {"has_process": self.process is not None}
        if operation == "signal":
            return self._signal(command.get("kind"))
        if operation == "changes":
            self._flush_available()
            state = self.log.state(
                since_version=(
                    int(command["since_version"])
                    if isinstance(command.get("since_version"), int)
                    else None
                ),
                after_line=(
                    int(command["after_line"])
                    if isinstance(command.get("after_line"), int)
                    else None
                ),
            )
            return {**state, "eof": self.eof}
        if operation == "snapshot":
            self._flush_available()
            state = self.log.state()
            return {
                "lines": self.log.snapshot(),
                "eof": self.eof,
                "version": state["version"],
                "line_cursor": state["line_cursor"],
            }
        return {"error": "Preview supervisor command is unsupported"}

    def _accept(self) -> socket.socket | None:
        try:
            control, _address = self.listener.accept()
        except BlockingIOError:
            return None
        control.settimeout(2)
        buffer = bytearray()
        try:
            if (
                self.controller_cgroup is not None
                and self._peer_cgroup(control) != self.controller_cgroup
            ):
                raise OSError("controller cgroup changed")
            while b"\n" not in buffer:
                chunk = control.recv(65536)
                if not chunk:
                    raise OSError("closed")
                buffer.extend(chunk)
            raw, _remainder = bytes(buffer).split(b"\n", 1)
            request = json.loads(raw)
            expected = {
                "op": "attach",
                "protocol": self.protocol,
                "profile": self.profile,
                "session_id": self.session_id,
                "token": self.token,
            }
            if request != expected:
                _send_json(control, {"error": "Preview adoption denied"})
                control.close()
                return None
            if not _send_json(control, self.identity()):
                control.close()
                return None
            control.setblocking(False)
            return control
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            control.close()
            return None

    def run(self) -> int:
        control: socket.socket | None = self.initial_control
        control.setblocking(False)
        buffers: dict[int, bytearray] = {control.fileno(): bytearray()}
        _send_json(control, self.identity())

        while True:
            self._read_containment()
            if self.read_fd is not None and not self.eof:
                self.eof, _drained = _drain_pipe(
                    self.read_fd,
                    self.log,
                    limit=1024 * 1024,
                )
            returncode = self.process.poll() if self.process is not None else None
            if control is None and self.process is None:
                break
            if (
                control is None
                and returncode is not None
                and self.eof
                and not self._app_cgroup_pids()
            ):
                break

            readers: list[Any] = [self.listener]
            if control is not None:
                readers.append(control)
            try:
                readable, _, _ = select.select(
                    readers,
                    [],
                    [],
                    0.01,
                )
            except (OSError, ValueError):
                readable = []
            if self.listener in readable:
                adopted = self._accept()
                if adopted is not None:
                    if control is not None:
                        try:
                            control.close()
                        except OSError:
                            pass
                    control = adopted
                    buffers = {control.fileno(): bytearray()}
            if control is not None and control in readable:
                try:
                    chunk = control.recv(65536)
                except BlockingIOError:
                    chunk = None
                except OSError:
                    chunk = b""
                if chunk == b"":
                    buffers.pop(control.fileno(), None)
                    control.close()
                    control = None
                elif chunk:
                    commands = buffers.setdefault(
                        control.fileno(),
                        bytearray(),
                    )
                    commands.extend(chunk)
                    while b"\n" in commands:
                        raw, remainder = bytes(commands).split(b"\n", 1)
                        commands[:] = remainder
                        try:
                            command = json.loads(raw)
                        except (
                            json.JSONDecodeError,
                            UnicodeDecodeError,
                        ):
                            response = {
                                "error": "Preview supervisor command is invalid"
                            }
                        else:
                            response = self.command(command)
                        if not _send_json(control, response):
                            control.close()
                            control = None
                            break

        if control is not None:
            control.close()
        self.listener.close()
        if self.socket_path is not None:
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
        for fd in (self.read_fd, self.write_fd, self.info_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._remove_app_cgroup()
        return 0


def _systemd_broker() -> int:
    return PreviewSupervisor(socket.socket(fileno=0)).run()


def _inherited_broker(control_fd: int) -> int:
    return PreviewSupervisor(socket.socket(fileno=control_fd)).run()


def _windows_broker(port: int) -> int:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    control, _address = listener.accept()
    listener.close()
    return PreviewSupervisor(control).run()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systemd-socket", action="store_true")
    parser.add_argument("--control-fd", type=int)
    parser.add_argument("--listen-port", type=int)
    args = parser.parse_args()
    if args.systemd_socket:
        return _systemd_broker()
    if args.control_fd is not None:
        return _inherited_broker(args.control_fd)
    if args.listen_port is not None:
        return _windows_broker(args.listen_port)
    parser.error("supervisor transport is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import array
import json
import os
import select
import socket
import time

from .preview_output import BrokerLog


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


def _send_json(control: socket.socket, payload: dict[str, object]) -> bool:
    try:
        control.sendall(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        return True
    except OSError:
        return False


def _send_descriptor(
    control: socket.socket,
    write_fd: int,
) -> None:
    descriptors = array.array("i", [write_fd])
    control.sendmsg(
        [json.dumps({"pid": os.getpid()}).encode("utf-8") + b"\n"],
        [
            (
                socket.SOL_SOCKET,
                socket.SCM_RIGHTS,
                descriptors.tobytes(),
            )
        ],
    )


def _run(control: socket.socket, read_fd: int) -> int:
    os.set_blocking(read_fd, False)
    control.setblocking(False)
    log = BrokerLog()
    commands = bytearray()
    control_open = True
    eof = False

    while control_open or not eof:
        if not eof:
            eof, _drained = _drain_pipe(
                read_fd,
                log,
                limit=1024 * 1024,
            )

        if control_open:
            try:
                readable, _, _ = select.select([control], [], [], 0.01)
            except (OSError, ValueError):
                readable = []
                control_open = False
            if readable:
                try:
                    chunk = control.recv(65536)
                except BlockingIOError:
                    chunk = None
                except OSError:
                    chunk = b""
                if chunk == b"":
                    control_open = False
                elif chunk:
                    commands.extend(chunk)
                    while b"\n" in commands:
                        raw, remainder = bytes(commands).split(b"\n", 1)
                        commands[:] = remainder
                        try:
                            command = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            control_open = _send_json(
                                control,
                                {"error": "invalid command"},
                            )
                            continue
                        if command == {"op": "snapshot"}:
                            pending = _pipe_available(read_fd)
                            if pending:
                                eof, _drained = _drain_pipe(
                                    read_fd,
                                    log,
                                    limit=pending,
                                )
                            if not eof:
                                eof, _drained = _drain_pipe(
                                    read_fd,
                                    log,
                                    limit=1,
                                )
                            control_open = _send_json(
                                control,
                                {
                                    "lines": log.snapshot(),
                                    "eof": eof,
                                },
                            )
        else:
            time.sleep(0.01)

    try:
        control.close()
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    return 0


def _systemd_broker() -> int:
    control = socket.socket(fileno=0)
    read_fd, write_fd = os.pipe()
    try:
        _send_descriptor(control, write_fd)
    finally:
        os.close(write_fd)
    return _run(control, read_fd)


def _inherited_broker(control_fd: int, read_fd: int) -> int:
    control = socket.socket(fileno=control_fd)
    _send_json(control, {"pid": os.getpid()})
    return _run(control, read_fd)


def _windows_broker(read_handle: int, port: int) -> int:
    import msvcrt

    read_fd = msvcrt.open_osfhandle(read_handle, os.O_RDONLY)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    control, _address = listener.accept()
    listener.close()
    _send_json(control, {"pid": os.getpid()})
    return _run(control, read_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systemd-socket", action="store_true")
    parser.add_argument("--control-fd", type=int)
    parser.add_argument("--read-fd", type=int)
    parser.add_argument("--read-handle", type=int)
    parser.add_argument("--listen-port", type=int)
    args = parser.parse_args()
    if args.systemd_socket:
        return _systemd_broker()
    if args.control_fd is not None and args.read_fd is not None:
        return _inherited_broker(args.control_fd, args.read_fd)
    if args.read_handle is not None and args.listen_port is not None:
        return _windows_broker(args.read_handle, args.listen_port)
    parser.error("broker transport is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

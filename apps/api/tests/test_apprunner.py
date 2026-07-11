from __future__ import annotations

import asyncio
import socket
import time

import pytest

from proxima_api.apprunner import AppManager


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProcess:
    returncode = None

    def __init__(self, lines: list[bytes]) -> None:
        self.stdout = _FakeStdout(lines)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_app_runner_does_not_report_ready_without_open_port(tmp_path):
    manager = AppManager()

    async def run_case():
        try:
            await manager.start("demo", str(tmp_path), "sleep 60", 59999)
            manager._apps["demo"]["started_at"] = time.time() - 20
            status = manager.status("demo")
            assert status["running"] is True
            assert status["ready"] is False
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


def test_app_runner_ignores_stale_drain_from_replaced_process():
    manager = AppManager()
    old_proc = _FakeProcess([b"http://localhost:49999\n"])
    new_proc = _FakeProcess([])
    manager._apps["demo"] = {"proc": new_proc, "port": 5180, "command": "new", "started_at": time.time(), "log": []}

    asyncio.run(manager._drain("demo", old_proc))  # type: ignore[arg-type]

    assert manager._apps["demo"]["log"] == []
    assert "detected_port" not in manager._apps["demo"]


def test_app_runner_reports_ready_when_port_accepts_connections():
    manager = AppManager()
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    async def run_case():
        try:
            await manager.start("demo", ".", f"python3 -m http.server {port} --bind 127.0.0.1", port)
            for _ in range(40):
                status = manager.status("demo")
                if status.get("ready"):
                    break
                await asyncio.sleep(0.05)
            assert status["running"] is True
            assert status["ready"] is True
            assert status["port"] == port
        finally:
            await manager.shutdown()

    asyncio.run(run_case())

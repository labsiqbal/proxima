from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

import pytest

from proxima_api import preview_output
from proxima_api.preview_output import (
    BROKER_SOCKET_ENV,
    BrokerLog,
    MAX_PENDING_LINE_BYTES,
    OutputBroker,
    OutputBrokerUnavailable,
)


ROOT = Path(__file__).resolve().parents[3]


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_broker_bounds_newline_free_output() -> None:
    log = BrokerLog()
    log.feed(b"x" * (MAX_PENDING_LINE_BYTES * 8))

    snapshot = log.snapshot()

    assert len(snapshot) == 1
    assert len(snapshot[0].encode()) == MAX_PENDING_LINE_BYTES


def test_api_systemd_process_fails_closed_without_supervised_broker(
    monkeypatch,
) -> None:
    monkeypatch.delenv(BROKER_SOCKET_ENV, raising=False)
    monkeypatch.setenv("INVOCATION_ID", "test-invocation")
    monkeypatch.setenv("SYSTEMD_EXEC_PID", "12345")
    monkeypatch.setattr(preview_output.os, "getpid", lambda: 12345)

    with pytest.raises(OutputBrokerUnavailable, match="unavailable"):
        OutputBroker._open_sync()


def test_windows_adapter_fails_before_launch_without_breakaway_support(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "CREATE_BREAKAWAY_FROM_JOB",
        0,
        raising=False,
    )
    with pytest.raises(OutputBrokerUnavailable, match="not launched"):
        OutputBroker._open_windows_direct()


@pytest.mark.skipif(os.name != "posix", reason="descriptor broker requires POSIX")
def test_broker_owns_output_from_launch_and_snapshots_atomically() -> None:
    async def run_case() -> None:
        broker = await OutputBroker.open()
        broker_pid = broker.pid
        assert broker_pid is not None
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os; os.write(1, b'transport-buffer-tail')",
            ],
            stdout=broker.child_output_fd,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        broker.close_child_output()
        process.wait(timeout=5)

        snapshot = await broker.snapshot()

        assert snapshot.lines == ["transport-buffer-tail"]
        assert snapshot.eof is True
        assert process.stdout is None
        await broker.disconnect()
        deadline = time.monotonic() + 5
        while _pid_exists(broker_pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not _pid_exists(broker_pid)

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="descriptor broker requires POSIX")
def test_broker_serializes_polling_with_final_snapshot() -> None:
    async def run_case() -> None:
        broker = await OutputBroker.open()
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os, time; time.sleep(0.02); "
                "os.write(1, b'broker-api-race-tail')",
            ],
            stdout=broker.child_output_fd,
            stderr=subprocess.STDOUT,
        )
        broker.close_child_output()

        polls = [
            asyncio.create_task(broker.snapshot())
            for _ in range(12)
        ]
        process.wait(timeout=5)
        final = await broker.snapshot()
        await asyncio.gather(*polls)

        assert "broker-api-race-tail" in final.lines
        assert final.eof is True
        await broker.disconnect()

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="descriptor broker requires POSIX")
def test_broker_survives_api_disconnect_until_detached_writer_eof(
    tmp_path: Path,
) -> None:
    trigger = tmp_path / "write.trigger"
    result = tmp_path / "write.result"
    worker = (
        "import os, pathlib, time\n"
        f"trigger = pathlib.Path({str(trigger)!r})\n"
        f"result = pathlib.Path({str(result)!r})\n"
        "while not trigger.exists():\n"
        "    time.sleep(0.01)\n"
        "try:\n"
        "    for _ in range(64):\n"
        "        os.write(1, b'after-api-disconnect\\n')\n"
        "except OSError as exc:\n"
        "    result.write_text(f'error:{exc.errno}')\n"
        "else:\n"
        "    result.write_text('success')\n"
    )

    async def run_case() -> None:
        broker = await OutputBroker.open()
        broker_pid = broker.pid
        assert broker_pid is not None
        process = subprocess.Popen(
            [sys.executable, "-c", worker],
            stdout=broker.child_output_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        broker.close_child_output()
        await broker.disconnect()

        trigger.write_text("write", encoding="utf-8")
        process.wait(timeout=5)
        assert result.read_text(encoding="utf-8") == "success"
        deadline = time.monotonic() + 5
        while _pid_exists(broker_pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not _pid_exists(broker_pid)

    asyncio.run(run_case())


def test_packaged_systemd_units_keep_broker_outside_api_cgroup() -> None:
    production = (
        ROOT / "infra" / "systemd" / "proxima.service.example"
    ).read_text(encoding="utf-8")
    staging = (
        ROOT / "infra" / "systemd" / "proxima-staging.service.example"
    ).read_text(encoding="utf-8")
    broker = (
        ROOT
        / "infra"
        / "systemd"
        / "proxima-preview-output@.service.example"
    ).read_text(encoding="utf-8")
    broker_socket = (
        ROOT / "infra" / "systemd" / "proxima-preview-output.socket"
    ).read_text(encoding="utf-8")
    user_installer = (ROOT / "scripts" / "install-user").read_text(
        encoding="utf-8"
    )

    for service in (production, staging):
        assert "Requires=proxima-preview-output.socket" in service
        assert "PROXIMA_OUTPUT_BROKER_SOCKET=" in service
        assert "KillMode=process" in service
        assert "output-broker" not in service
    assert "ExecStart=/usr/local/bin/proxima output-broker" in broker
    assert "StandardInput=socket" in broker
    assert "Accept=yes" in broker_socket
    assert "proxima-preview-output@.service" in user_installer
    assert "PROXIMA_OUTPUT_BROKER_SOCKET=%t/" in user_installer
    assert "KillMode=process" in user_installer


@pytest.mark.skipif(
    os.environ.get("PROXIMA_TEST_SYSTEMD_BROKER") != "1"
    or shutil.which("systemd-run") is None,
    reason="real user-systemd broker integration is opt-in",
)
def test_systemd_broker_survives_real_api_restart_and_stop(
    tmp_path: Path,
) -> None:
    socket_path = os.environ["PROXIMA_TEST_SYSTEMD_BROKER_SOCKET"]
    unit = f"proxima-preview-broker-test-{uuid.uuid4().hex}"
    script = tmp_path / "api_process.py"
    generation = tmp_path / "generation"
    trigger_prefix = tmp_path / "trigger"
    result_prefix = tmp_path / "result"
    state_prefix = tmp_path / "state"
    script.write_text(
        "\n".join(
            [
                "import asyncio, json, os, pathlib, subprocess, sys, time",
                "from proxima_api.preview_output import OutputBroker",
                f"generation_path = pathlib.Path({str(generation)!r})",
                "generation = int(generation_path.read_text()) + 1 if generation_path.exists() else 1",
                "generation_path.write_text(str(generation))",
                f"trigger = pathlib.Path({str(trigger_prefix)!r} + '-' + str(generation))",
                f"result = pathlib.Path({str(result_prefix)!r} + '-' + str(generation))",
                "worker = (",
                "    'import os, pathlib, time\\n'",
                "    + f'trigger = pathlib.Path({str(trigger)!r})\\n'",
                "    + f'result = pathlib.Path({str(result)!r})\\n'",
                "    + 'while not trigger.exists():\\n    time.sleep(0.01)\\n'",
                "    + \"try:\\n    os.write(1, b'after-systemd-stop\\\\n')\\n\"",
                "    + \"except OSError as exc:\\n    result.write_text(f'error:{exc.errno}')\\n\"",
                "    + \"else:\\n    result.write_text('success')\\n\"",
                ")",
                "broker = asyncio.run(OutputBroker.open())",
                "child = subprocess.Popen([sys.executable, '-c', worker], stdout=broker.child_output_fd, start_new_session=True)",
                "broker.close_child_output()",
                f"state = pathlib.Path({str(state_prefix)!r} + '-' + str(generation))",
                "state.write_text(json.dumps({'api': os.getpid(), 'broker': broker.pid, 'child': child.pid}))",
                "time.sleep(60)",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    api_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = (
        api_root
        + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    )
    env[BROKER_SOCKET_ENV] = socket_path

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["systemctl", "--user", *args],
            text=True,
            capture_output=True,
            check=False,
        )

    subprocess.run(
        [
            "systemd-run",
            "--user",
            f"--unit={unit}",
            "--property=Type=exec",
            "--property=KillMode=process",
            f"--setenv=PYTHONPATH={env['PYTHONPATH']}",
            f"--setenv={BROKER_SOCKET_ENV}={socket_path}",
            sys.executable,
            str(script),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    states: list[dict[str, int]] = []
    try:
        for expected in (1, 2):
            state_path = Path(f"{state_prefix}-{expected}")
            deadline = time.monotonic() + 10
            while not state_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            states.append(json.loads(state_path.read_text(encoding="utf-8")))
            api_cgroup = Path(
                f"/proc/{states[-1]['api']}/cgroup"
            ).read_text(encoding="utf-8")
            broker_cgroup = Path(
                f"/proc/{states[-1]['broker']}/cgroup"
            ).read_text(encoding="utf-8")
            assert api_cgroup != broker_cgroup
            if expected == 1:
                assert systemctl("restart", unit).returncode == 0

        assert systemctl("stop", unit).returncode == 0
        for index, state in enumerate(states, start=1):
            Path(f"{trigger_prefix}-{index}").write_text(
                "write",
                encoding="utf-8",
            )
            result = Path(f"{result_prefix}-{index}")
            deadline = time.monotonic() + 5
            while not result.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert result.read_text(encoding="utf-8") == "success"
            deadline = time.monotonic() + 5
            while _pid_exists(state["broker"]) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not _pid_exists(state["broker"])
    finally:
        systemctl("stop", unit)
        systemctl("reset-failed", unit)
        for state in states:
            for pid in (state["child"], state["broker"]):
                if _pid_exists(pid):
                    os.kill(pid, signal.SIGKILL)

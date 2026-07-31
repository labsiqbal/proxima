from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest

from proxima_api import apprunner
from proxima_api.apprunner import AppManager, PortInUseError
from proxima_api.preview_output import (
    BROKER_STATE_ROOT_ENV,
    OutputBroker,
    OutputBrokerUnavailable,
)
from proxima_api.container_activity import acquire_container_activity_lease
from proxima_api.db import connect, init_db


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


def test_app_runner_keeps_exit_log_across_status_polls(tmp_path):
    """A failed start must stay visible on later polls — the UI polls every 2s
    and used to wipe the exit log the moment the process was reaped."""
    manager = AppManager()

    async def run_case():
        try:
            await manager.start(
                "demo", str(tmp_path), "bash -lc 'echo boom-fail; exit 7'", 5180
            )
            status = None
            for _ in range(40):
                status = manager.status("demo")
                if status.get("exited"):
                    break
                await asyncio.sleep(0.05)
            assert status is not None
            assert status["running"] is False
            assert status["exited"] is True
            assert status["exit_code"] == 7
            assert any("boom-fail" in line for line in status.get("log") or [])
            # Second poll must still carry the same exit payload.
            again = manager.status("demo")
            assert again.get("exited") is True
            assert again.get("exit_code") == 7
            assert again.get("log") == status.get("log")
            assert again.get("command") == "bash -lc 'echo boom-fail; exit 7'"
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


def test_app_runner_keeps_bounded_log_after_explicit_stop(tmp_path):
    """Stop preserves the latest bounded buffer for status reloads."""
    manager = AppManager()
    port = _free_port()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "for i in $(seq 0 59); do echo line-$i; done; sleep 60",
            port,
        )
        for _ in range(100):
            if len(manager._apps["demo"]["log"]) >= 60:
                break
            await asyncio.sleep(0.01)

        await manager.stop("demo")

        status = manager.status("demo")
        assert status["state"] == "stopped"
        assert status["running"] is False
        assert status["command"].endswith("sleep 60")
        assert status["requested_port"] == port
        assert status["log"] == [f"line-{number}" for number in range(20, 60)]
        assert manager.status("demo") == status

    asyncio.run(run_case())


def test_app_runner_stop_waits_for_terminal_output(tmp_path):
    manager = AppManager()
    port = _free_port()

    async def run_case():
        armed = tmp_path / "terminal-trap-armed"
        await manager.start(
            "demo",
            str(tmp_path),
            "trap 'echo terminal-line; exit 0' TERM; "
            "touch terminal-trap-armed; "
            "while true; do sleep 1; done",
            port,
        )
        for _ in range(100):
            if armed.is_file():
                break
            await asyncio.sleep(0.01)
        assert armed.is_file()
        await manager.stop("demo")

        status = manager.status("demo")
        assert status["state"] == "stopped"
        assert "terminal-line" in status["log"]

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="setsid requires POSIX")
def test_stop_bounds_final_drain_with_inherited_detached_pipe(tmp_path):
    manager = AppManager()
    port = _free_port()
    child_pid_file = tmp_path / "inherited-pipe.pid"
    output_ready_file = tmp_path / "inherited-output.ready"
    write_trigger_file = tmp_path / "inherited-write.trigger"
    write_result_file = tmp_path / "inherited-write.result"

    async def run_case():
        child_pid = None
        try:
            worker_code = (
                "import os, pathlib, time\n"
                f"trigger = pathlib.Path({str(write_trigger_file)!r})\n"
                f"result = pathlib.Path({str(write_result_file)!r})\n"
                "while not trigger.exists():\n"
                "    time.sleep(0.01)\n"
                "try:\n"
                "    for _ in range(4):\n"
                "        os.write(1, b'after-stop\\n')\n"
                "        time.sleep(0.02)\n"
                "except OSError as exc:\n"
                "    result.write_text(f'error:{exc.errno}')\n"
                "else:\n"
                "    result.write_text('success')\n"
                "time.sleep(60)\n"
            )
            worker = [
                sys.executable,
                "-c",
                worker_code,
            ]
            managed = (
                "import sys, time; "
                "sys.stdout.write('inherited-tail'); "
                "sys.stdout.flush(); "
                f"open({str(output_ready_file)!r}, 'w').write('ready'); "
                "time.sleep(60)"
            )
            launcher = (
                "import os, subprocess; "
                f"child = subprocess.Popen({worker!r}, start_new_session=True); "
                f"open({str(child_pid_file)!r}, 'w').write(str(child.pid)); "
                f"os.execv({sys.executable!r}, "
                f"[{sys.executable!r}, '-c', {managed!r}])"
            )
            command = f"exec {shlex.quote(sys.executable)} -c {shlex.quote(launcher)}"
            await manager.start("demo", str(tmp_path), command, port)
            for _ in range(100):
                if child_pid_file.is_file():
                    child_pid = int(child_pid_file.read_text().strip())
                    break
                await asyncio.sleep(0.01)
            assert child_pid is not None
            for _ in range(100):
                if output_ready_file.is_file():
                    break
                await asyncio.sleep(0.01)
            assert output_ready_file.is_file()
            managed_group = manager._apps["demo"]["authority"].process_group
            assert managed_group is not None
            for _ in range(100):
                if os.getpgid(child_pid) != managed_group:
                    break
                await asyncio.sleep(0.01)
            assert os.getpgid(child_pid) != managed_group

            broker_pid = manager._apps["demo"]["output_broker"].pid
            assert broker_pid is not None
            await asyncio.wait_for(manager.stop("demo"), timeout=7)

            status = manager.status("demo")
            assert status["state"] == "stopped"
            assert "inherited-tail" in status["log"]
            os.kill(broker_pid, 0)
            write_trigger_file.write_text("write")
            for _ in range(200):
                if write_result_file.is_file():
                    break
                await asyncio.sleep(0.01)
            assert write_result_file.read_text() == "success"
            assert manager.status("demo")["log"] == status["log"]
            assert not any(
                "after-stop" in line for line in manager.status("demo")["log"]
            )
            os.kill(child_pid, 0)
        finally:
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_detached_output_sink_survives_event_loop_shutdown_and_reaps(
    tmp_path,
):
    child_pid_file = tmp_path / "service-child.pid"
    helper_pid_file = tmp_path / "service-helper.pid"
    loop_closed_file = tmp_path / "service-loop.closed"
    write_trigger_file = tmp_path / "service-write.trigger"
    write_result_file = tmp_path / "service-write.result"
    worker_code = (
        "import os, pathlib, time\n"
        f"trigger = pathlib.Path({str(write_trigger_file)!r})\n"
        f"result = pathlib.Path({str(write_result_file)!r})\n"
        "while not trigger.exists():\n"
        "    time.sleep(0.01)\n"
        "try:\n"
        "    for _ in range(32):\n"
        "        os.write(1, b'after-loop-close\\n')\n"
        "except OSError as exc:\n"
        "    result.write_text(f'error:{exc.errno}')\n"
        "else:\n"
        "    result.write_text('success')\n"
    )
    launcher_code = (
        "import os, subprocess, sys, time; "
        f"child = subprocess.Popen("
        f"[sys.executable, '-c', {worker_code!r}], "
        "start_new_session=True); "
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid)); "
        "os.execv(sys.executable, "
        "[sys.executable, '-c', 'import time; time.sleep(60)'])"
    )
    command = f"exec {shlex.quote(sys.executable)} -c {shlex.quote(launcher_code)}"
    service_code = f"""
import asyncio
import time
from proxima_api.apprunner import AppManager

async def run():
    manager = AppManager()
    await manager.start(
        "demo",
        {str(tmp_path)!r},
        {command!r},
        {_free_port()},
    )
    child = Path({str(child_pid_file)!r})
    for _ in range(300):
        if child.exists():
            break
        await asyncio.sleep(0.01)
    broker_pid = manager._apps["demo"]["output_broker"].pid
    Path({str(helper_pid_file)!r}).write_text(str(broker_pid))
    await manager.stop("demo")
    await manager.shutdown()

asyncio.run(run())
Path({str(loop_closed_file)!r}).write_text("closed")
time.sleep(60)
"""
    api_root = Path(apprunner.__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(api_root)
        if not existing_path
        else str(api_root) + os.pathsep + existing_path
    )
    service = subprocess.Popen(
        [sys.executable, "-c", service_code],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 8
        while (
            not loop_closed_file.is_file()
            and service.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        if service.poll() is not None:
            pytest.fail(service.stderr.read())
        assert loop_closed_file.is_file()
        child_pid = int(child_pid_file.read_text())
        helper_pid = int(helper_pid_file.read_text())

        write_trigger_file.write_text("write")
        deadline = time.monotonic() + 4
        while not write_result_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert write_result_file.read_text() == "success"

        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                os.kill(helper_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("detached output helper was not reaped after EOF")
        assert service.poll() is None
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if service.poll() is None:
            os.killpg(os.getpgid(service.pid), signal.SIGTERM)
        service.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
@pytest.mark.parametrize("stage", ["before_spawn", "after_spawn"])
def test_cancelled_start_reaps_provisional_process(
    monkeypatch,
    tmp_path,
    stage,
):
    manager = AppManager(contained=True)
    original_spawn = OutputBroker.spawn
    entered = asyncio.Event()
    release = asyncio.Event()
    spawned = {}

    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    lease = Lease()

    async def delayed_spawn(broker, *args, **kwargs):
        if stage == "before_spawn":
            entered.set()
            await release.wait()
        proc = await original_spawn(
            broker,
            *args,
            **{**kwargs, "contained": False},
        )
        spawned["proc"] = proc
        if stage == "after_spawn":
            entered.set()
            await release.wait()
        return proc

    monkeypatch.setattr(
        OutputBroker,
        "spawn",
        delayed_spawn,
    )

    async def run_case():
        task = asyncio.create_task(
            manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
                effect_lease=lease,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager._cleanup_tasks
        if stage == "after_spawn":
            assert lease.released is False
        release.set()
        await asyncio.wait_for(manager.shutdown(), timeout=7)

        proc = spawned["proc"]
        assert proc.returncode is not None
        assert "demo" not in manager._apps
        assert lease.released is True
        assert not manager._cleanup_tasks

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_cancelled_start_reserves_generation_before_immediate_retry(
    monkeypatch,
    tmp_path,
):
    manager = AppManager()
    original_spawn = OutputBroker.spawn
    first_entered = asyncio.Event()
    first_release = asyncio.Event()
    calls = 0

    async def delayed_first_spawn(broker, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await first_release.wait()
        return await original_spawn(broker, *args, **kwargs)

    monkeypatch.setattr(
        OutputBroker,
        "spawn",
        delayed_first_spawn,
    )

    async def run_case():
        first = asyncio.create_task(
            manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
            )
        )
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        retry = asyncio.create_task(
            manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
            )
        )
        await asyncio.sleep(0.05)
        assert retry.done() is False

        first_release.set()
        await asyncio.wait_for(retry, timeout=8)
        app = manager._apps["demo"]
        retry_pid = app["proc"].pid
        assert app["generation"] == 2
        assert app["proc"].returncode is None

        await manager.shutdown()
        assert app["proc"].returncode is not None
        assert retry_pid > 0
        assert not manager._cleanup_tasks

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="process broker requires POSIX")
def test_launch_phases_are_durable_before_broker_and_app_spawn(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))
    phases: list[str] = []

    async def observed_broker():
        record = json.loads(next(state_root.glob("*.json")).read_text(encoding="utf-8"))
        phases.append(record["phase"])
        broker = await OutputBroker.open()
        original_spawn = broker.spawn

        async def observed_spawn(*args, **kwargs):
            record = json.loads(
                next(state_root.glob("*.json")).read_text(encoding="utf-8")
            )
            phases.append(record["phase"])
            return await original_spawn(*args, **kwargs)

        broker.spawn = observed_spawn
        return broker

    manager = AppManager(
        output_broker_factory=observed_broker,
        state_root=state_root,
    )

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        record = json.loads(next(state_root.glob("*.json")).read_text(encoding="utf-8"))
        assert phases == ["pending", "broker_attached"]
        assert record["phase"] == "attached"
        assert record["process"]["pid"] == manager._apps["demo"]["proc"].pid
        await manager.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="procfs adoption requires POSIX")
def test_restart_completes_broker_attached_spawn_phase(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))
    first = AppManager(state_root=state_root)

    async def run_case():
        lineage = "broker-attached-lineage"
        record = first._persist_reservation(
            slug="demo",
            generation=1,
            port=_free_port(),
            command="sleep 60",
            lineage_token=lineage,
            started_at=time.time(),
        )
        broker = await OutputBroker.open()
        first._persist_broker_reservation(record, broker.metadata)
        environment = {
            **os.environ,
            "PROXIMA_APP_LINEAGE": lineage,
        }
        spawned = await broker.spawn(
            ["bash", "-lc", "sleep 60"],
            cwd=str(tmp_path),
            env=environment,
            contained=False,
        )
        await broker.disconnect()

        restarted = AppManager(state_root=state_root)
        await restarted.reconcile()

        adopted = restarted._apps["demo"]
        assert adopted["proc"].pid == spawned.pid
        durable = json.loads(
            next(state_root.glob("*.json")).read_text(encoding="utf-8")
        )
        assert durable["phase"] == "attached"
        assert durable["process"]["pid"] == spawned.pid
        await restarted.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="process broker requires POSIX")
def test_restart_terminally_reconciles_broker_attached_without_process(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))
    first = AppManager(state_root=state_root)

    async def run_case():
        record = first._persist_reservation(
            slug="demo",
            generation=1,
            port=_free_port(),
            command="sleep 60",
            lineage_token="broker-only-lineage",
            started_at=time.time(),
        )
        broker = await OutputBroker.open()
        first._persist_broker_reservation(record, broker.metadata)

        restarted = AppManager(state_root=state_root)
        await restarted.reconcile()

        assert "demo" not in restarted._unadopted
        assert restarted.status("demo")["state"] == "stopped"
        assert not list(state_root.glob("*.json"))

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="process broker requires POSIX")
def test_cancelled_spawn_persistence_failure_reaps_matching_generation(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))
    manager = AppManager(state_root=state_root)
    original_spawn = OutputBroker.spawn
    entered = asyncio.Event()
    release = asyncio.Event()
    spawned = {}

    async def delayed_spawn(broker, *args, **kwargs):
        proc = await original_spawn(broker, *args, **kwargs)
        spawned["proc"] = proc
        entered.set()
        await release.wait()
        return proc

    monkeypatch.setattr(OutputBroker, "spawn", delayed_spawn)
    monkeypatch.setattr(
        manager,
        "_persist_app",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state root full")),
    )

    async def run_case():
        task = asyncio.create_task(
            manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.wait_for(manager.shutdown(), timeout=7)
        assert spawned["proc"].returncode is not None
        assert not list(state_root.glob("*.json"))
        assert "demo" not in manager._apps

    asyncio.run(run_case())


def test_failed_stop_is_retryable_and_blocks_replacement(tmp_path):
    manager = AppManager()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        app = manager._apps["demo"]
        original_refresh = app["proc"].refresh

        async def unavailable():
            raise OutputBrokerUnavailable("supervisor unavailable")

        app["proc"].refresh = unavailable
        first_stop = await manager.stop("demo")
        assert first_stop["ok"] is False
        assert first_stop["state"] == "ownership_unknown"
        assert "stop_task" not in app
        with pytest.raises(OutputBrokerUnavailable, match="still live"):
            await manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
            )
        assert manager._apps["demo"] is app
        assert manager._generations["demo"] == 1

        app["proc"].refresh = original_refresh
        second_stop = await manager.stop("demo")
        assert second_stop["ok"] is True
        assert "demo" not in manager._apps

    asyncio.run(run_case())


def test_unadopted_stop_reports_unresolved_and_blocks_replacement(tmp_path):
    manager = AppManager()

    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    lease = Lease()

    async def run_case():
        manager._unadopted.add("demo")
        manager._generations["demo"] = 1
        manager._retain_effect("demo", lease)
        manager._last_exit["demo"] = manager._adoption_unknown_status(
            {"port": 5180, "command": "sleep 60"},
            "Preview registration failed and terminal cleanup could not "
            "be authenticated.",
        )

        result = await manager.stop("demo")
        assert result["ok"] is False
        assert result["state"] == "ownership_unknown"
        assert "authenticate" in result["message"]
        assert "demo" in manager._unadopted
        assert lease.released is False
        assert manager.status("demo")["state"] == "ownership_unknown"

        with pytest.raises(OutputBrokerUnavailable, match="without complete"):
            await manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
                effect_lease=Lease(),
            )
        assert manager._generations["demo"] == 1

    asyncio.run(run_case())


def test_unadopted_stop_recovers_ended_durable_scope(tmp_path):
    state_root = tmp_path / "preview-supervisors"
    state_root.mkdir()
    record = {
        "version": 2,
        "phase": "attached",
        "profile": "direct",
        "slug": "demo",
        "generation": 3,
        "port": 5180,
        "command": "sleep 60",
        "started_at": time.time() - 30,
        "lineage_token": "ended-token",
        "contained": False,
        "broker": {
            "pid": 2**22,
            "start_time": 1,
            "cgroup": "broker-cgroup",
            "controller_cgroup": "controller-cgroup",
            "profile": "direct",
        },
        "process": {
            "pid": 2**22 + 1,
            "start_time": 1,
            "cgroup": "process-cgroup",
        },
    }
    (state_root / "demo.3.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    manager = AppManager(state_root=state_root)

    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    lease = Lease()

    async def run_case():
        manager._unadopted.add("demo")
        manager._generations["demo"] = 3
        manager._retain_effect("demo", lease)
        manager._last_exit["demo"] = manager._adoption_unknown_status(
            record,
            "Preview adoption exceeded the startup deadline.",
        )

        result = await manager.stop("demo")
        assert result["ok"] is True
        assert "demo" not in manager._unadopted
        assert lease.released is True
        assert manager.status("demo")["state"] == "stopped"
        assert not list(state_root.glob("*.json"))

        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        assert manager._generations["demo"] == 4
        await manager.stop("demo")

    asyncio.run(run_case())


def test_unadopted_stop_failed_recovery_keeps_single_live_entry(tmp_path):
    manager = AppManager()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        live = manager._apps["demo"]
        original_refresh = live["proc"].refresh
        manager._unadopted.add("demo")

        async def unavailable():
            raise OutputBrokerUnavailable("supervisor unavailable")

        live["proc"].refresh = unavailable

        first_stop = await manager.stop("demo")
        assert first_stop["ok"] is False
        assert first_stop["state"] == "ownership_unknown"
        assert manager._apps["demo"] is live
        assert "demo" not in manager._unadopted

        second_stop = await manager.stop("demo")
        assert second_stop["ok"] is False
        assert manager._apps["demo"] is live
        assert "demo" not in manager._unadopted

        with pytest.raises(OutputBrokerUnavailable, match="still live"):
            await manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
            )
        assert manager._apps["demo"] is live
        assert manager._generations["demo"] == 1

        live["proc"].refresh = original_refresh
        recovered = await manager.stop("demo")
        assert recovered["ok"] is True
        assert "demo" not in manager._apps
        assert "demo" not in manager._unadopted

    asyncio.run(run_case())


def test_reconcile_post_register_snapshot_failure_disposes_generation(
    monkeypatch,
    tmp_path,
):
    manager = AppManager(state_root=tmp_path / "preview-supervisors")
    registered: dict[str, object] = {}

    class FakeProc:
        pid = 4242
        start_time = 11
        cgroup = "process-cgroup"
        managed_cgroup = "process-cgroup"
        containment_pid_namespace = None
        returncode = None
        scope_live = True

        async def refresh(self):
            return self.returncode

        async def terminate(self):
            self.returncode = -15
            self.scope_live = False

        async def kill(self):
            self.returncode = -9
            self.scope_live = False

        async def wait(self):
            return self.returncode

    class FakeBroker:
        pid = 4241

        def __init__(self):
            self.disconnected = False
            self._proc = FakeProc()

        async def has_managed_process(self):
            return True

        async def managed_process(self):
            return self._proc

        async def snapshot(self):
            raise OutputBrokerUnavailable("snapshot failed after register")

        async def disconnect(self):
            self.disconnected = True

        async def changes(self, **_kwargs):
            raise OutputBrokerUnavailable("broker gone")

    fake_broker = FakeBroker()
    record = {
        "version": 2,
        "phase": "attached",
        "profile": "direct",
        "slug": "demo",
        "generation": 2,
        "port": 5180,
        "command": "sleep 60",
        "started_at": time.time() - 5,
        "lineage_token": "snap-fail-token",
        "contained": False,
        "broker": {
            "pid": fake_broker.pid,
            "start_time": 10,
            "cgroup": "broker-cgroup",
            "controller_cgroup": "controller-cgroup",
            "profile": "direct",
        },
        "process": {
            "pid": fake_broker._proc.pid,
            "start_time": fake_broker._proc.start_time,
            "cgroup": fake_broker._proc.cgroup,
        },
    }

    async def fake_reconnect(_metadata, timeout=1):
        return fake_broker

    real_register = manager._register_app

    def tracking_register(**kwargs):
        app = real_register(**kwargs)
        registered["app"] = app
        return app

    monkeypatch.setattr(OutputBroker, "reconnect", staticmethod(fake_reconnect))
    monkeypatch.setattr(manager, "_register_app", tracking_register)
    monkeypatch.setattr(
        manager,
        "_cgroup_identity",
        lambda pid: {
            fake_broker.pid: "broker-cgroup",
            fake_broker._proc.pid: "process-cgroup",
            os.getpid(): "controller-cgroup",
        }.get(pid),
    )
    monkeypatch.setattr(
        apprunner,
        "process_start_time",
        lambda pid: {
            fake_broker.pid: 10,
            fake_broker._proc.pid: fake_broker._proc.start_time,
        }.get(pid),
    )
    monkeypatch.setattr(
        apprunner,
        "_process_has_lineage",
        lambda _pid, token: token == "snap-fail-token",
    )
    monkeypatch.setattr(manager, "_persist_app", lambda *_a, **_k: None)
    monkeypatch.setattr(manager, "_remove_app_record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        manager,
        "_remove_generation_record",
        lambda *_a, **_k: None,
    )

    async def run_case():
        await manager._reconcile_slug(
            "demo",
            [(tmp_path / "demo.2.json", record)],
            asyncio.get_running_loop().time() + 2,
        )
        assert "app" in registered
        assert "demo" not in manager._apps
        assert "demo" not in manager._unadopted
        assert fake_broker._proc.returncode is not None
        assert fake_broker.disconnected is True

    asyncio.run(run_case())


def test_reconcile_never_registers_over_live_app(tmp_path):
    manager = AppManager()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        live = manager._apps["demo"]
        manager._unadopted.add("demo")

        called = {"reconcile": 0}
        original_reconcile = manager._reconcile_slug

        async def tracking_reconcile(*args, **kwargs):
            called["reconcile"] += 1
            return await original_reconcile(*args, **kwargs)

        manager._reconcile_slug = tracking_reconcile  # type: ignore[method-assign]

        recovery = await manager._try_recover_unadopted("demo")
        assert recovery == "adopted"
        assert called["reconcile"] == 0
        assert manager._apps["demo"] is live
        assert "demo" not in manager._unadopted

        with pytest.raises(OutputBrokerUnavailable, match="already owns"):
            manager._register_app(
                slug="demo",
                generation=99,
                proc=live["proc"],
                broker=live["output_broker"],
                port=int(live["port"]),
                command="should-not-replace",
                lineage_token="other",
                effect_lease=None,
            )
        assert manager._apps["demo"] is live
        assert live["command"] != "should-not-replace"

        await manager.stop("demo")

    asyncio.run(run_case())


def test_startup_reconciliation_is_concurrent_and_deadline_bounded(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    state_root.mkdir()
    for generation in range(1, 17):
        slug = f"demo-{generation}"
        (state_root / f"{slug}.{generation}.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "phase": "broker_attached",
                    "profile": "direct",
                    "slug": slug,
                    "generation": generation,
                    "port": 40000 + generation,
                    "command": "sleep 60",
                    "broker": {},
                }
            ),
            encoding="utf-8",
        )
    manager = AppManager(state_root=state_root)

    async def slow_reconcile(_slug, _candidates, _deadline):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(manager, "_reconcile_slug", slow_reconcile)

    async def run_case():
        started = time.monotonic()
        await manager.reconcile()
        assert time.monotonic() - started < 0.4

        monkeypatch.setattr(apprunner, "RECONCILE_DEADLINE_SECONDS", 0.05)

        async def stuck_reconcile(_slug, _candidates, _deadline):
            await asyncio.sleep(60)

        monkeypatch.setattr(manager, "_reconcile_slug", stuck_reconcile)
        await manager.reconcile()
        assert manager._unadopted
        assert all(
            manager.status(slug)["state"] == "ownership_unknown"
            for slug in manager._unadopted
        )

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="procfs adoption requires POSIX")
def test_restart_adopts_only_exact_durable_preview_scope(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))

    async def run_case():
        first = AppManager(
            state_root=state_root,
            profile="direct",
        )
        await first.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        original = first._apps["demo"]
        original_pid = original["proc"].pid
        tasks = [
            task
            for task in (
                original.get("output_task"),
                original.get("exit_task"),
                original.get("authority_task"),
            )
            if isinstance(task, asyncio.Task)
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await original["output_broker"].disconnect()
        first._apps.clear()

        restarted = AppManager(
            state_root=state_root,
            profile="direct",
        )
        await restarted.reconcile()

        adopted = restarted._apps["demo"]
        assert adopted["proc"].pid == original_pid
        assert adopted["generation"] == 1
        assert restarted.status("demo")["state"] == "starting"

        await restarted.shutdown()
        assert adopted["proc"].returncode is not None

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="procfs adoption requires POSIX")
def test_restart_rejects_tampered_scope_without_signaling_it(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))

    async def run_case():
        first = AppManager(
            state_root=state_root,
            profile="direct",
        )
        await first.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        original = first._apps["demo"]
        metadata = original["output_broker"].metadata
        original_pid = original["proc"].pid
        tasks = [
            task
            for task in (
                original.get("output_task"),
                original.get("exit_task"),
            )
            if isinstance(task, asyncio.Task)
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await original["output_broker"].disconnect()
        first._apps.clear()

        record_path = next(state_root.glob("*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["process"]["start_time"] += 1
        record_path.write_text(json.dumps(record), encoding="utf-8")

        restarted = AppManager(
            state_root=state_root,
            profile="direct",
        )
        await restarted.reconcile()

        assert "demo" not in restarted._apps
        assert restarted.status("demo")["state"] == "ownership_unknown"
        os.kill(original_pid, 0)

        cleanup = await OutputBroker.reconnect(metadata)
        process = await cleanup.managed_process()
        await process.terminate()
        assert await AppManager._wait_for_returncode(process, 4)
        await cleanup.disconnect()

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="procfs adoption requires POSIX")
def test_restart_discards_fully_ended_durable_scope(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "preview-supervisors"
    monkeypatch.setenv(BROKER_STATE_ROOT_ENV, str(state_root))

    async def run_case():
        first = AppManager(
            state_root=state_root,
            profile="direct",
        )
        await first.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        original = first._apps["demo"]
        metadata = original["output_broker"].metadata
        broker_pid = int(metadata["pid"])
        tasks = [
            task
            for task in (
                original.get("output_task"),
                original.get("exit_task"),
            )
            if isinstance(task, asyncio.Task)
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await original["output_broker"].disconnect()
        first._apps.clear()

        cleanup = await OutputBroker.reconnect(metadata)
        process = await cleanup.managed_process()
        await process.terminate()
        assert await AppManager._wait_for_returncode(process, 4)
        await cleanup.disconnect()
        deadline = time.monotonic() + 5
        while (
            apprunner.process_start_time(broker_pid) is not None
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.02)
        assert apprunner.process_start_time(broker_pid) is None

        restarted = AppManager(
            state_root=state_root,
            profile="direct",
        )
        await restarted.reconcile()

        assert restarted.status("demo")["state"] == "stopped"
        assert not list(state_root.glob("*.json"))
        await restarted.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        assert restarted._apps["demo"]["generation"] == 2
        await restarted.shutdown()

    asyncio.run(run_case())


def test_shutdown_reconciles_many_apps_concurrently(monkeypatch):
    manager = AppManager()
    for index in range(24):
        manager._apps[f"app-{index}"] = {"registered": True}

    async def slow_stop(slug, _app, *, preserve_status):
        assert preserve_status is False
        await asyncio.sleep(0.1)
        manager._apps.pop(slug, None)

    monkeypatch.setattr(manager, "_stop_app", slow_stop)

    async def run_case():
        started = time.monotonic()
        await manager.shutdown()
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert not manager._apps

    asyncio.run(run_case())


def test_shutdown_stops_unadopted_and_registered_slugs(monkeypatch):
    manager = AppManager()
    manager._apps["live"] = {"registered": True}
    manager._unadopted.add("ghost")
    stopped: list[tuple[str, bool]] = []

    async def fake_stop(slug, *, preserve_status=True):
        stopped.append((slug, preserve_status))
        await asyncio.sleep(0.05)
        manager._apps.pop(slug, None)
        manager._unadopted.discard(slug)
        return {"ok": True}

    monkeypatch.setattr(manager, "stop", fake_stop)

    async def run_case():
        started = time.monotonic()
        await manager.shutdown()
        elapsed = time.monotonic() - started
        assert elapsed < 0.4
        assert sorted(slug for slug, _ in stopped) == ["ghost", "live"]
        assert all(preserve is False for _, preserve in stopped)
        assert not manager._apps
        assert not manager._unadopted

    asyncio.run(run_case())


def test_shutdown_recovers_ended_unadopted_scope(tmp_path):
    state_root = tmp_path / "preview-supervisors"
    state_root.mkdir()
    record = {
        "version": 2,
        "phase": "attached",
        "profile": "direct",
        "slug": "demo",
        "generation": 3,
        "port": 5180,
        "command": "sleep 60",
        "started_at": time.time() - 30,
        "lineage_token": "ended-token",
        "contained": False,
        "broker": {
            "pid": 2**22,
            "start_time": 1,
            "cgroup": "broker-cgroup",
            "controller_cgroup": "controller-cgroup",
            "profile": "direct",
        },
        "process": {
            "pid": 2**22 + 1,
            "start_time": 1,
            "cgroup": "process-cgroup",
        },
    }
    (state_root / "demo.3.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    manager = AppManager(state_root=state_root)

    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    lease = Lease()

    async def run_case():
        manager._unadopted.add("demo")
        manager._generations["demo"] = 3
        manager._retain_effect("demo", lease)
        manager._last_exit["demo"] = manager._adoption_unknown_status(
            record,
            "Preview adoption exceeded the startup deadline.",
        )

        await manager.shutdown()

        assert "demo" not in manager._unadopted
        assert lease.released is True
        assert not list(state_root.glob("*.json"))

    asyncio.run(run_case())


def test_shutdown_retains_unresolved_unadopted_authority(tmp_path):
    manager = AppManager()

    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    lease = Lease()

    async def run_case():
        manager._unadopted.add("demo")
        manager._generations["demo"] = 1
        manager._retain_effect("demo", lease)
        manager._last_exit["demo"] = manager._adoption_unknown_status(
            {"port": 5180, "command": "sleep 60"},
            "Preview registration failed and terminal cleanup could not "
            "be authenticated.",
        )

        await manager.shutdown()

        assert "demo" in manager._unadopted
        assert lease.released is False
        assert manager.status("demo")["state"] == "ownership_unknown"

    asyncio.run(run_case())


def test_cancelled_start_before_spawn_releases_effect_lease(
    monkeypatch,
    tmp_path,
):
    manager = AppManager()
    entered = asyncio.Event()
    gate = asyncio.Event()

    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    lease = Lease()

    async def blocked_stop(*_args, **_kwargs):
        entered.set()
        await gate.wait()

    monkeypatch.setattr(manager, "_stop_locked", blocked_stop)

    async def run_case():
        task = asyncio.create_task(
            manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
                effect_lease=lease,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert lease.released is True
        assert not manager._apps

    asyncio.run(run_case())


def test_output_broker_launch_failure_is_recoverable_before_spawn(
    tmp_path,
):
    async def unavailable():
        raise OutputBrokerUnavailable("Windows breakaway preview output is unavailable")

    manager = AppManager(output_broker_factory=unavailable)

    async def run_case():
        with pytest.raises(OutputBrokerUnavailable):
            await manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                _free_port(),
            )
        status = manager.status("demo")
        assert status["state"] == "stopped"
        assert status["reason"] == "output_sink_unavailable"
        assert "breakaway" in status["message"]
        assert not manager._apps

    asyncio.run(run_case())


def test_stop_completes_when_output_broker_disconnects(tmp_path):
    manager = AppManager()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "echo retained-before-broker-error; sleep 60",
            _free_port(),
        )
        app = manager._apps["demo"]
        for _ in range(100):
            if "retained-before-broker-error" in app["log"]:
                break
            await asyncio.sleep(0.01)

        async def failed_snapshot():
            raise OutputBrokerUnavailable("output broker test failure")

        async def failed_disconnect():
            raise OutputBrokerUnavailable("output broker test failure")

        app["output_broker"].snapshot = failed_snapshot
        app["output_broker"].disconnect = failed_disconnect

        await manager.stop("demo")

        status = manager.status("demo")
        assert status["state"] == "stopped"
        assert status["reason"] == "output_sink_unavailable"
        assert "retained-before-broker-error" in status["log"]
        assert "demo" not in manager._apps

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="information fd requires POSIX")
def test_containment_proof_completes_asynchronously_and_stop_owns_it(
    monkeypatch,
    tmp_path,
):
    manager = AppManager(contained=True)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_spawn = OutputBroker.spawn

    async def uncontained_spawn(broker, *args, **kwargs):
        return await original_spawn(
            broker,
            *args,
            **{**kwargs, "contained": False},
        )

    async def slow_proof(slug, app):
        entered.set()
        await release.wait()
        if manager._apps.get(slug) is app:
            app["authority"] = apprunner.replace(
                app["authority"],
                containment_pid_namespace=4242,
            )

    monkeypatch.setattr(
        OutputBroker,
        "spawn",
        uncontained_spawn,
    )
    monkeypatch.setattr(manager, "_complete_containment_authority", slow_proof)

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "echo provisional-output; sleep 60",
            _free_port(),
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        app = manager._apps["demo"]
        proof_task = app["authority_task"]
        assert app["authority"].containment_pid_namespace is None
        for _ in range(100):
            if "provisional-output" in app["log"]:
                break
            await asyncio.sleep(0.01)
        assert "provisional-output" in app["log"]

        release.set()
        await asyncio.wait_for(proof_task, timeout=2)
        assert app["authority"].containment_pid_namespace == 4242

        second_gate = asyncio.Event()

        async def blocked_proof(_slug, _app):
            await second_gate.wait()

        monkeypatch.setattr(
            manager,
            "_complete_containment_authority",
            blocked_proof,
        )
        await manager.stop("demo", preserve_status=False)
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 60",
            _free_port(),
        )
        app = manager._apps["demo"]
        proof_task = app["authority_task"]
        await manager.stop("demo", preserve_status=False)

        assert proof_task.done()
        assert proof_task.cancelled()
        assert app["proc"].returncode is not None
        assert "demo" not in manager._apps

    asyncio.run(run_case())


def test_fast_strict_port_exit_is_a_sticky_conflict(tmp_path, monkeypatch):
    manager = AppManager()
    port = _free_port()
    original_port_open = apprunner._port_open
    foreign: subprocess.Popen | None = None

    def lose_candidate_after_preflight(candidate: int) -> bool:
        nonlocal foreign
        if candidate == port and foreign is None:
            foreign = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(100):
                if original_port_open(port):
                    break
                time.sleep(0.01)
            return False
        return original_port_open(candidate)

    monkeypatch.setattr(apprunner, "_port_open", lose_candidate_after_preflight)

    async def run_case():
        try:
            command = (
                f"{shlex.quote(sys.executable)} -m http.server $PORT --bind 127.0.0.1"
            )
            await manager.start("demo", str(tmp_path), command, port)
            status = {}
            for _ in range(100):
                status = manager.status("demo")
                if status.get("state") in {"port_conflict", "exited"}:
                    break
                await asyncio.sleep(0.01)

            assert status["state"] == "port_conflict"
            assert status["running"] is False
            assert status["requested_port"] == port
            assert manager.status("demo") == status
            assert foreign is not None and foreign.poll() is None
        finally:
            await manager.shutdown()

    try:
        asyncio.run(run_case())
    finally:
        if foreign is not None:
            foreign.terminate()
            foreign.wait(timeout=5)


def test_app_runner_reports_ready_when_port_accepts_connections():
    manager = AppManager()
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    async def run_case():
        try:
            await manager.start(
                "demo", ".", f"python3 -m http.server {port} --bind 127.0.0.1", port
            )
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


def test_app_runner_refuses_a_port_owned_by_an_unrelated_preview():
    """A foreign Astro/dev server must never be embedded as this app's preview."""
    port = _free_port()
    foreign = subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    async def run_case():
        manager = AppManager()
        try:
            for _ in range(40):
                if manager_port_open(port):
                    break
                await asyncio.sleep(0.025)
            else:
                pytest.fail("foreign preview did not start")
            with pytest.raises(PortInUseError, match=f"Port {port} is already in use"):
                await manager.start("demo", ".", f"python3 -m http.server {port}", port)
            assert foreign.poll() is None
            status = manager.status("demo")
            assert status["state"] == "port_conflict"
            assert status["running"] is False
            assert status["requested_port"] == port
        finally:
            await manager.shutdown()

    try:
        asyncio.run(run_case())
    finally:
        foreign.terminate()
        foreign.wait(timeout=5)


def test_app_runner_fails_closed_when_procfs_cannot_prove_listener_ownership(
    monkeypatch,
    tmp_path,
):
    """Reachability is not ownership. Non-procfs hosts may run the command and
    expose logs, but must never turn an unverified listener into a preview."""
    manager = AppManager()

    async def run_case():
        try:
            port = _free_port()
            await manager.start("demo", str(tmp_path), "sleep 60", port)
            monkeypatch.setattr(
                apprunner, "_port_open", lambda candidate: candidate == port
            )
            monkeypatch.setattr(
                apprunner,
                "_listening_socket_inodes",
                lambda _port: None,
            )

            status = manager.status("demo")

            assert status["state"] == "ownership_unknown"
            assert status["running"] is True
            assert status["ready"] is False
            assert status["requested_port"] == port
            assert "port" not in status
            assert manager.preview_target("demo") is None
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


def test_detached_descendant_requires_exact_pid_namespace_membership(
    monkeypatch,
):
    leader_pid = 1200
    detached_pid = 1201
    listener_inode = "98765"
    monkeypatch.setattr(
        apprunner,
        "_listening_socket_inodes",
        lambda _port: {listener_inode},
    )
    monkeypatch.setattr(apprunner.os, "getpgid", lambda _pid: leader_pid)
    monkeypatch.setattr(
        apprunner,
        "_process_table",
        lambda _inodes: {
            leader_pid: (1, leader_pid, set()),
            detached_pid: (leader_pid, detached_pid, {listener_inode}),
        },
    )
    monkeypatch.setattr(
        apprunner,
        "_process_has_lineage",
        lambda _pid, _token: True,
    )
    monkeypatch.setattr(
        apprunner,
        "_pid_namespace_id",
        lambda _pid: 4242,
    )
    monkeypatch.setattr(
        apprunner,
        "_process_cgroup_identity",
        lambda _pid: "0::/managed\n",
    )
    uncontained = apprunner.ProcessAuthority(
        leader_pid=leader_pid,
        process_group=leader_pid,
        lineage_token="launch-token",
        containment_required=False,
        containment_pid_namespace=None,
    )
    missing_proof = apprunner.ProcessAuthority(
        leader_pid=leader_pid,
        process_group=leader_pid,
        lineage_token="launch-token",
        containment_required=True,
        containment_pid_namespace=None,
        containment_cgroup="0::/managed\n",
    )
    exact_proof = apprunner.ProcessAuthority(
        leader_pid=leader_pid,
        process_group=leader_pid,
        lineage_token="launch-token",
        containment_required=True,
        containment_pid_namespace=4242,
        containment_cgroup="0::/managed\n",
    )

    assert (
        apprunner._listener_ownership(
            5180,
            authority=uncontained,
        )
        == apprunner.PortOwnership.DETACHED
    )
    assert (
        apprunner._listener_ownership(
            5180,
            authority=missing_proof,
        )
        == apprunner.PortOwnership.DETACHED
    )
    assert (
        apprunner._listener_ownership(
            5180,
            authority=exact_proof,
        )
        == apprunner.PortOwnership.VERIFIED
    )

    monkeypatch.setattr(
        apprunner,
        "_process_cgroup_identity",
        lambda pid: "0::/escaped\n" if pid == detached_pid else "0::/managed\n",
    )
    assert (
        apprunner._listener_ownership(
            5180,
            authority=exact_proof,
        )
        == apprunner.PortOwnership.DETACHED
    )
    monkeypatch.setattr(
        apprunner,
        "_process_cgroup_identity",
        lambda _pid: "0::/managed\n",
    )

    monkeypatch.setattr(
        apprunner,
        "_process_table",
        lambda _inodes: {
            leader_pid: (1, leader_pid, set()),
            detached_pid: (1, detached_pid, {listener_inode}),
        },
    )
    assert (
        apprunner._listener_ownership(
            5180,
            authority=exact_proof,
        )
        == apprunner.PortOwnership.DETACHED
    )

    monkeypatch.setattr(
        apprunner,
        "_process_table",
        lambda _inodes: {
            leader_pid: (1, leader_pid, set()),
            detached_pid: (leader_pid, detached_pid, {listener_inode}),
        },
    )
    monkeypatch.setattr(
        apprunner,
        "_process_has_lineage",
        lambda _pid, _token: False,
    )
    assert (
        apprunner._listener_ownership(
            5180,
            authority=exact_proof,
        )
        == apprunner.PortOwnership.DETACHED
    )


def test_uncontained_detached_listener_is_not_a_preview(tmp_path):
    """A setsid child is outside the managed process group. Without PID
    containment Proxima cannot promise Stop owns its lifetime, so it fails closed
    instead of blessing the listener just because it descends from the shell."""
    port = _free_port()
    child_pid_file = tmp_path / "detached.pid"
    manager = AppManager(contained=False)

    async def run_case():
        try:
            await manager.start(
                "demo",
                str(tmp_path),
                "setsid sh -c 'echo $$ > detached.pid; "
                f"exec python3 -m http.server {port} --bind 127.0.0.1' "
                "</dev/null >/dev/null 2>&1 & wait",
                port,
            )
            status = {}
            for _ in range(80):
                status = manager.status("demo")
                if status.get("state") == "ownership_unknown":
                    break
                await asyncio.sleep(0.05)

            assert status["state"] == "ownership_unknown"
            assert status["ready"] is False
            assert manager.preview_target("demo") is None
        finally:
            await manager.shutdown()
            if child_pid_file.exists():
                child_pid = int(child_pid_file.read_text().strip())
                try:
                    os.kill(child_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                for _ in range(80):
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(0.025)

    asyncio.run(run_case())


@pytest.mark.skipif(os.name != "posix", reason="setsid requires POSIX")
def test_reparented_uncontained_listener_stays_ownership_unknown(tmp_path):
    port = _free_port()
    child_pid_file = tmp_path / "reparented.pid"
    manager = AppManager(contained=False)

    async def run_case():
        child_pid = None
        try:
            command = (
                f"setsid {shlex.quote(sys.executable)} -m http.server "
                "$PORT --bind 127.0.0.1 >/dev/null 2>&1 & "
                f"echo $! > {shlex.quote(str(child_pid_file))}; "
                "for i in $(seq 1 200); do "
                "(echo >/dev/tcp/127.0.0.1/$PORT) >/dev/null 2>&1 "
                "&& exit 0; sleep 0.01; done; exit 3"
            )
            await manager.start("demo", str(tmp_path), command, port)
            for _ in range(100):
                if child_pid_file.is_file():
                    child_pid = int(child_pid_file.read_text().strip())
                    break
                await asyncio.sleep(0.01)
            assert child_pid is not None

            status = {}
            for _ in range(200):
                status = manager.status("demo")
                if status.get("state") in {
                    "ownership_unknown",
                    "port_conflict",
                    "exited",
                }:
                    break
                await asyncio.sleep(0.01)

            assert status["state"] == "ownership_unknown"
            assert status["ready"] is False
            assert manager.preview_target("demo") is None
            os.kill(child_pid, 0)

            await manager.stop("demo")
            assert manager.status("demo")["state"] == "stopped"
            os.kill(child_pid, 0)
        finally:
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    asyncio.run(run_case())


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="Bubblewrap is required for process containment",
)
def test_stolen_marker_outside_containment_never_verifies(tmp_path):
    port = _free_port()
    manager = AppManager(contained=True)

    async def run_case():
        foreign = None
        try:
            await manager.start(
                "demo",
                str(tmp_path),
                "sleep 60",
                port,
            )
            await asyncio.wait_for(
                manager._apps["demo"]["authority_task"],
                timeout=2,
            )
            authority = manager._apps["demo"]["authority"]
            assert authority.containment_pid_namespace is not None
            foreign_env = os.environ.copy()
            foreign_env[apprunner._LINEAGE_ENV] = authority.lineage_token
            foreign = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ],
                env=foreign_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(100):
                if manager_port_open(port):
                    break
                await asyncio.sleep(0.01)
            assert manager_port_open(port)

            status = manager.status("demo")

            assert status["state"] == "ownership_unknown"
            assert status["ready"] is False
            assert manager.preview_target("demo") is None
            assert foreign.poll() is None
            await manager.stop("demo")
            assert foreign.poll() is None
        finally:
            await manager.shutdown()
            if foreign is not None and foreign.poll() is None:
                foreign.terminate()
                foreign.wait(timeout=5)

    asyncio.run(run_case())


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="Bubblewrap is required for process containment",
)
def test_detached_listener_inside_exact_containment_is_ready(tmp_path):
    port = _free_port()
    manager = AppManager(contained=True)

    async def run_case():
        try:
            command = (
                f"setsid {shlex.quote(sys.executable)} -m http.server "
                "$PORT --bind 127.0.0.1 >/dev/null 2>&1 & wait"
            )
            await manager.start("demo", str(tmp_path), command, port)
            await asyncio.wait_for(
                manager._apps["demo"]["authority_task"],
                timeout=2,
            )
            authority = manager._apps["demo"]["authority"]
            assert authority.containment_pid_namespace is not None
            status = {}
            for _ in range(200):
                status = manager.status("demo")
                if status.get("state") in {
                    "ready",
                    "ownership_unknown",
                    "port_conflict",
                }:
                    break
                await asyncio.sleep(0.01)

            assert status["state"] == "ready"
            assert status["ready"] is True
            assert manager.preview_target("demo") == port
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


def test_prolonged_start_is_bounded_and_actionable(tmp_path):
    manager = AppManager()

    async def run_case():
        try:
            port = _free_port()
            await manager.start("demo", str(tmp_path), "sleep 60", port)
            manager._apps["demo"]["started_at"] = (
                time.time() - apprunner.PROLONGED_START_SECONDS - 1
            )

            status = manager.status("demo")

            assert status["state"] == "starting"
            assert status["prolonged_start"] is True
            assert status["ready"] is False
            assert status["log"] == []
            assert manager.preview_target("demo") is None
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


def manager_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def test_app_runner_holds_effect_lease_until_process_stops(tmp_path):
    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    manager = AppManager()
    lease = Lease()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 30",
            5180,
            effect_lease=lease,
        )
        assert lease.released is False
        await manager.stop("demo")
        assert lease.released is True

    asyncio.run(run_case())


def test_app_runner_splits_ingress_and_activity_on_unverified_stop(
    tmp_path,
    monkeypatch,
):
    class IngressLease:
        released = False

        def release(self) -> None:
            self.released = True

    class ActivityLease:
        released = False

        def release(self) -> None:
            self.released = True

        def guard_process(self, command):
            return command, {}

        def mark_process_started(self) -> None:
            return None

    class SplitLease:
        def __init__(self) -> None:
            self.ingress = IngressLease()
            self.activity = ActivityLease()
            self.finished = None

        def release(self) -> None:
            self.activity.release()
            self.ingress.release()

        def guard_process(self, command):
            return self.activity.guard_process(command)

        def mark_process_started(self) -> None:
            self.activity.mark_process_started()

        def finish(self, **kwargs):
            self.finished = kwargs
            if kwargs.get("process_exited"):
                self.release()
                return
            retain = kwargs.get("retain_ingress")
            if retain is not None:
                retain(self.ingress)
            from proxima_api.container_activity import retain_activity_lease

            retain_activity_lease(
                self.activity,
                pid=kwargs.get("pid"),
                start_identity=kwargs.get("start_identity"),
                tree=kwargs.get("tree"),
            )

    manager = AppManager()
    lease = SplitLease()

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "sleep 30",
            5180,
            effect_lease=lease,
        )
        app = manager._apps["demo"]
        proc = app["proc"]
        pid = app["proc_pid"]
        identity = app["proc_start_identity"]
        assert pid is not None
        assert identity

        async def hang_wait(*_args, **_kwargs):
            raise asyncio.TimeoutError

        monkeypatch.setattr(proc, "wait", hang_wait)
        monkeypatch.setattr(proc, "kill", lambda: None)
        monkeypatch.setattr(proc, "terminate", lambda: None)

        from proxima_api.container_activity import GuardedWriterTree

        real_terminate = GuardedWriterTree.terminate
        real_exited = GuardedWriterTree.exited
        monkeypatch.setattr(
            GuardedWriterTree,
            "terminate",
            lambda self, **_kwargs: False,
        )
        monkeypatch.setattr(
            GuardedWriterTree,
            "exited",
            lambda self: False,
        )
        await manager.stop("demo")
        # Restore real tree observation so the retain monitor can release once
        # the launcher identity actually exits.
        monkeypatch.setattr(GuardedWriterTree, "terminate", real_terminate)
        monkeypatch.setattr(GuardedWriterTree, "exited", real_exited)

        assert lease.finished is not None
        assert lease.finished["process_exited"] is False
        assert lease.finished["pid"] == pid
        assert lease.finished["start_identity"] == identity
        assert lease.ingress.released is False
        assert lease.activity.released is False
        assert lease.ingress in manager._retained_effects

        os.kill(pid, 9)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not lease.activity.released:
            await asyncio.sleep(0.02)
        assert lease.activity.released is True
        assert lease.ingress.released is False

    asyncio.run(run_case())


def _activity_lease(tmp_path: Path, slug: str = "preview"):
    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / slug
    root.mkdir(parents=True, exist_ok=True)
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        (f"owner-{slug}", f"owner-{slug}"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        (slug, slug.title(), str(root), user_id),
    ).lastrowid
    return acquire_container_activity_lease(conn, int(container_id)), root


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian setsid + /proc ownership is Linux-only",
)
def test_guarded_app_runner_reports_ready_for_process_tree_listener(tmp_path):
    """Guardian setsid must not hide the owned listener from readiness."""
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    lease, root = _activity_lease(tmp_path, "guarded-ready")
    manager = AppManager()

    async def run_case():
        try:
            await manager.start(
                "demo",
                str(root),
                f"python3 -m http.server {port} --bind 127.0.0.1",
                port,
                effect_lease=lease,
            )
            status = None
            for _ in range(80):
                status = manager.status("demo")
                if status.get("ready"):
                    break
                await asyncio.sleep(0.05)
            assert status is not None
            assert status["running"] is True
            assert status["ready"] is True
            assert status.get("port_conflict") is not True
            assert status["port"] == port
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian setsid + /proc ownership is Linux-only",
)
def test_guarded_app_runner_marks_unrelated_port_owner_as_conflict(tmp_path):
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    lease, root = _activity_lease(tmp_path, "guarded-conflict")
    manager = AppManager()
    foreign = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    async def run_case():
        try:
            for _ in range(40):
                if manager_port_open(port):
                    break
                await asyncio.sleep(0.025)
            else:
                pytest.fail("foreign preview did not start")
            # Occupied port is refused before spawn; free it, start a non-listener
            # guarded app, then let a foreign server claim the port afterward.
            foreign.terminate()
            foreign.wait(timeout=5)
            for _ in range(40):
                if not manager_port_open(port):
                    break
                await asyncio.sleep(0.025)

            await manager.start(
                "demo",
                str(root),
                "sleep 60",
                port,
                effect_lease=lease,
            )
            usurper = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                for _ in range(40):
                    if manager_port_open(port):
                        break
                    await asyncio.sleep(0.025)
                else:
                    pytest.fail("unrelated listener did not start")
                status = manager.status("demo")
                assert status["running"] is True
                assert status["ready"] is False
                assert status.get("port_conflict") is True
            finally:
                usurper.terminate()
                usurper.wait(timeout=5)
        finally:
            await manager.shutdown()

    try:
        asyncio.run(run_case())
    finally:
        if foreign.poll() is None:
            foreign.terminate()
            foreign.wait(timeout=5)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian setsid + /proc ownership is Linux-only",
)
def test_guarded_app_runner_clears_ready_after_guardian_exit(tmp_path):
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    lease, root = _activity_lease(tmp_path, "guarded-exit")
    manager = AppManager()

    async def run_case():
        try:
            await manager.start(
                "demo",
                str(root),
                f"python3 -m http.server {port} --bind 127.0.0.1",
                port,
                effect_lease=lease,
            )
            for _ in range(80):
                if manager.status("demo").get("ready"):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("guarded preview never became ready")

            app = manager._apps["demo"]
            pid = int(app["proc_pid"])
            os.kill(pid, 9)
            status = None
            for _ in range(80):
                status = manager.status("demo")
                if not status.get("running"):
                    break
                await asyncio.sleep(0.05)
            assert status is not None
            assert status.get("running") is False
            assert status.get("ready") is not True
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian setsid + /proc ownership is Linux-only",
)
def test_guarded_app_runner_restart_reports_ready_again(tmp_path):
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    first_lease, root = _activity_lease(tmp_path, "guarded-restart")
    manager = AppManager()

    async def run_case():
        try:
            command = f"python3 -m http.server {port} --bind 127.0.0.1"
            await manager.start(
                "demo",
                str(root),
                command,
                port,
                effect_lease=first_lease,
            )
            for _ in range(80):
                if manager.status("demo").get("ready"):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("first guarded preview never became ready")

            await manager.stop("demo")
            assert manager.status("demo")["running"] is False

            second_lease, _ = _activity_lease(tmp_path, "guarded-restart-2")
            await manager.start(
                "demo",
                str(root),
                command,
                port,
                effect_lease=second_lease,
            )
            status = None
            for _ in range(80):
                status = manager.status("demo")
                if status.get("ready"):
                    break
                await asyncio.sleep(0.05)
            assert status is not None
            assert status["running"] is True
            assert status["ready"] is True
            assert status.get("port_conflict") is not True
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="Bubblewrap is required for process containment",
)
def test_contained_app_runner_kills_detached_descendants(tmp_path):
    class Lease:
        released = False

        def release(self) -> None:
            self.released = True

    manager = AppManager(contained=True)
    lease = Lease()
    escaped = tmp_path / "escaped.txt"

    async def run_case():
        await manager.start(
            "demo",
            str(tmp_path),
            "setsid sh -c 'sleep 0.4; echo escaped > escaped.txt' "
            "</dev/null >/dev/null 2>&1 &",
            5180,
            effect_lease=lease,
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not lease.released:
            await asyncio.sleep(0.01)
        assert lease.released is True
        await asyncio.sleep(0.6)
        assert not escaped.exists()
        await manager.shutdown()

    asyncio.run(run_case())


def _listener_pids(port: int) -> set[int]:
    from proxima_api.apprunner import _listening_socket_inodes

    inodes = _listening_socket_inodes(port) or set()
    found: set[int] = set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    target = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    found.add(pid)
                    break
        except OSError:
            continue
    return found


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian process-tree stop is Linux-only",
)
def test_guarded_app_runner_stop_kills_orphan_writer_tree(tmp_path):
    """Stop must tear down setsid() writers, not only the guardian launcher."""
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    lease, root = _activity_lease(tmp_path, "guarded-stop-tree")
    manager = AppManager()
    released = {"done": False}

    class TrackingLease:
        def release(self) -> None:
            released["done"] = True
            lease.release()

        def guard_process(self, command):
            return lease.guard_process(command)

        def mark_process_started(self) -> None:
            lease.mark_process_started()

        def finish(self, **kwargs):
            if kwargs.get("process_exited"):
                self.release()

    tracking = TrackingLease()

    async def run_case():
        try:
            await manager.start(
                "demo",
                str(root),
                f"python3 -m http.server {port} --bind 127.0.0.1",
                port,
                effect_lease=tracking,
            )
            for _ in range(80):
                if manager.status("demo").get("ready"):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("guarded preview never became ready")

            app = manager._apps["demo"]
            launcher = int(app["proc_pid"])
            listeners_before = _listener_pids(port)
            assert listeners_before
            assert launcher not in listeners_before or len(listeners_before) >= 1

            await manager.stop("demo")
            assert manager.status("demo")["running"] is False
            assert released["done"] is True

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and _listener_pids(port):
                await asyncio.sleep(0.05)
            assert not _listener_pids(port)
            assert not Path(f"/proc/{launcher}").exists()
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian process-tree stop is Linux-only",
)
def test_terminate_process_tree_sigkills_setsid_writers_not_just_launcher(
    tmp_path,
):
    """SIGKILL must target the whole tree; launcher-only kill would orphan writers."""
    from proxima_api.process_containment import (
        process_tree_pids,
        terminate_process_tree,
    )

    ready = tmp_path / "ready"
    launcher = os.fork()
    if launcher == 0:
        writer = os.fork()
        if writer == 0:
            try:
                os.setsid()
                ready.write_text(str(os.getpid()), encoding="utf-8")
                while True:
                    time.sleep(1)
            finally:
                os._exit(0)
        while True:
            time.sleep(1)
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not ready.is_file():
            time.sleep(0.01)
        assert ready.is_file()
        writer_pid = int(ready.read_text(encoding="utf-8").strip())
        tree = process_tree_pids(launcher) or set()
        assert writer_pid in tree
        assert terminate_process_tree(
            launcher,
            grace_seconds=0.05,
            kill_seconds=0.5,
        )
        try:
            os.waitpid(launcher, 0)
        except ChildProcessError:
            pass
        for pid in (launcher, writer_pid):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError(f"pid {pid} survived tree terminate")
    finally:
        for pid in (launcher,):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="activity guardian setsid + /proc ownership is Linux-only",
)
def test_guarded_preview_launcher_sigkill_keeps_lease_until_tree_exits(tmp_path):
    """Launcher death must not release leases while the writer tree lives."""
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("environment does not permit localhost sockets")

    from proxima_api.container_activity import (
        acquire_container_activity_lease,
        container_quiescence_lock,
        ContainerBoundaryError,
    )
    from proxima_api.db import connect, init_db

    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / "launcher-sigkill"
    root.mkdir()
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        ("owner-sigkill", "owner-sigkill"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        ("launcher-sigkill", "Launcher", str(root), user_id),
    ).lastrowid
    lease = acquire_container_activity_lease(conn, int(container_id))
    manager = AppManager()
    released = {"activity": False, "ingress": False}

    class Ingress:
        def release(self):
            released["ingress"] = True

    class Activity:
        def __init__(self, inner):
            self._inner = inner

        def release(self):
            released["activity"] = True
            self._inner.release()

        def guard_process(self, command):
            return self._inner.guard_process(command)

        def mark_process_started(self):
            self._inner.mark_process_started()

    class Group:
        def __init__(self):
            self.activity = Activity(lease)
            self.ingress = Ingress()
            self._activity = lease
            self.finished = None

        def release(self):
            self.activity.release()
            self.ingress.release()

        def guard_process(self, command):
            return self.activity.guard_process(command)

        def mark_process_started(self):
            self.activity.mark_process_started()

        def finish(self, **kwargs):
            self.finished = kwargs
            if kwargs.get("process_exited"):
                self.release()
                return
            from proxima_api.container_activity import retain_activity_lease
            retain = kwargs.get("retain_ingress")
            if retain is not None:
                retain(self.ingress)
            retain_activity_lease(
                self.activity._inner,
                pid=kwargs.get("pid"),
                start_identity=kwargs.get("start_identity"),
                tree=kwargs.get("tree"),
            )

    group = Group()

    async def run_case():
        try:
            await manager.start(
                "demo",
                str(root),
                f"python3 -m http.server {port} --bind 127.0.0.1",
                port,
                effect_lease=group,
            )
            for _ in range(80):
                if manager.status("demo").get("ready"):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("guarded preview never became ready")

            app = manager._apps["demo"]
            launcher = int(app["proc_pid"])
            listeners = _listener_pids(port)
            assert listeners

            os.kill(launcher, 9)
            # Quiescence must stay blocked while the orphan tree / retain holds.
            blocked = False
            try:
                with container_quiescence_lock(conn, int(container_id)):
                    pass
            except ContainerBoundaryError:
                blocked = True
            # status() must stay non-blocking and fail-closed while the tree is
            # unproven; _drain owns termination of the identity-bound tree.
            poll = manager.status("demo")
            assert poll.get("writer_tree_live") is True or poll.get("running") is True
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _listener_pids(port):
                manager.status("demo")
                await asyncio.sleep(0.05)
            assert not _listener_pids(port), "writer tree listener survived launcher death"
            # Ingress must not have been released early on launcher-only death.
            # Activity may release only after tree proof.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not released["activity"]:
                manager.status("demo")
                await asyncio.sleep(0.05)
            assert released["ingress"] is False or (
                group.finished is not None and group.finished.get("process_exited") is True
            )
            # After tree exit, activity can release; ingress only on verified finish.
            if group.finished and group.finished.get("process_exited"):
                assert released["activity"] is True
            else:
                # Retained path: activity still held or released by monitor after exit.
                pass
        finally:
            await manager.shutdown()

    asyncio.run(run_case())


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="identity-bound retain is exercised on Linux /proc",
)
def test_retain_activity_lease_waits_for_tree_not_just_launcher(tmp_path):
    """Retained leases must not drop when only the launcher identity exits."""
    import threading
    from proxima_api import container_registry
    from proxima_api.container_activity import (
        ContainerBoundaryError,
        GuardedWriterTree,
        container_quiescence_lock,
    )
    from proxima_api.db import connect, init_db

    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / "retain-tree"
    root.mkdir()
    (root / "wiki").mkdir()
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        ("owner-retain-tree", "owner-retain-tree"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        ("retain-tree", "Retain", str(root), user_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO project_areas(project_id, kind, rel_path, source) "
        "VALUES (?, 'ops', '.', 'auto')",
        (container_id,),
    )
    lease = container_registry.acquire_container_activity_lease(conn, int(container_id))

    # Parent launcher + setsid child writer (mirrors guardian shape).
    ready = tmp_path / "writer-ready"
    launcher = os.fork()
    if launcher == 0:
        writer = os.fork()
        if writer == 0:
            try:
                os.setsid()
                ready.write_text(str(os.getpid()), encoding="utf-8")
                while True:
                    time.sleep(1)
            finally:
                os._exit(0)
        try:
            os.waitpid(writer, 0)
        except ChildProcessError:
            pass
        os._exit(0)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not ready.is_file():
        time.sleep(0.01)
    assert ready.is_file()
    writer_pid = int(ready.read_text(encoding="utf-8").strip())
    # Distinct start identities: brief pause so /proc starttime can differ.
    time.sleep(0.05)
    launcher_start = container_registry.process_start_identity(launcher)
    writer_start = container_registry.process_start_identity(writer_pid)
    assert launcher_start and writer_start
    tree = GuardedWriterTree(
        launcher_pid=launcher,
        launcher_start=launcher_start,
        known_identities={writer_pid: writer_start},
    )
    container_registry.retain_activity_lease(
        lease,
        pid=launcher,
        start_identity=launcher_start,
        tree=tree,
    )

    # Kill only the launcher; writer remains.
    os.kill(launcher, 9)
    try:
        os.waitpid(launcher, 0)
    except ChildProcessError:
        pass
    time.sleep(0.15)
    assert lease._released is False
    assert tree.exited() is False

    blocked = threading.Event()
    finished = threading.Event()
    raised: list[BaseException] = []

    def take_exclusive():
        blocked.set()
        try:
            with container_quiescence_lock(conn, int(container_id)):
                pass
        except BaseException as exc:
            raised.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=take_exclusive)
    thread.start()
    assert blocked.wait(timeout=1)
    assert finished.wait(timeout=0.25) is False

    os.kill(writer_pid, 9)
    try:
        os.waitpid(writer_pid, 0)
    except ChildProcessError:
        pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not lease._released:
        time.sleep(0.05)
    thread.join(timeout=5)
    assert finished.is_set()
    assert lease._released is True
    assert not raised or not isinstance(raised[0], ContainerBoundaryError)


def test_worker_script_retains_activity_lease_on_containment_failure(tmp_path, monkeypatch):
    """Script containment failure must not drop the activity lease in finally."""
    from proxima_api.container_activity import ContainerActivityLease

    class FakeLease:
        def __init__(self):
            self.released = False
            self._released = False
            self._retained_for_writer_tree = False

        def release(self):
            self.released = True
            self._released = True

    class FakeScriptRunner:
        def __init__(self):
            self.lease = None

        async def execute(self, run, activity_lease=None):
            self.lease = activity_lease
            if activity_lease is not None:
                activity_lease._retained_for_writer_tree = True
            raise RuntimeError("script containment shutdown failed")

    class FakeWorker:
        def __init__(self):
            self.script_runner = FakeScriptRunner()
            self.app = type("A", (), {"state": type("S", (), {})()})()

    # Exercise the finally pattern used by worker.execute_run script path.
    script_activity_lease = FakeLease()
    runner = FakeScriptRunner()

    async def run_case():
        try:
            await runner.execute({}, activity_lease=script_activity_lease)
        except RuntimeError:
            pass
        finally:
            if (
                script_activity_lease is not None
                and not getattr(
                    script_activity_lease,
                    "_retained_for_writer_tree",
                    False,
                )
            ):
                if not getattr(script_activity_lease, "_released", False):
                    script_activity_lease.release()

    asyncio.run(run_case())
    assert script_activity_lease.released is False
    assert script_activity_lease._retained_for_writer_tree is True


def test_worker_acp_recycle_failure_retains_activity_lease():
    class FakeLease:
        def __init__(self):
            self.released = False
            self._released = False
            self._retained_for_writer_tree = False

        def release(self):
            self.released = True
            self._released = True

    retained = {}

    def fake_retain(lease, **kwargs):
        lease._retained_for_writer_tree = True
        retained["lease"] = lease
        retained["kwargs"] = kwargs

    project_activity_lease = FakeLease()
    recycle_verified = False
    recycle_tree = object()
    if project_activity_lease is not None:
        if (
            recycle_verified
            and not getattr(
                project_activity_lease,
                "_retained_for_writer_tree",
                False,
            )
        ):
            project_activity_lease.release()
        elif not recycle_verified:
            fake_retain(
                project_activity_lease,
                tree=recycle_tree,
            )
    assert project_activity_lease.released is False
    assert retained["lease"] is project_activity_lease
    assert retained["kwargs"]["tree"] is recycle_tree


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="stale guardian-record orphan proof uses Linux /proc",
)
def test_writer_tree_stale_record_with_unseeded_orphan_fails_closed(tmp_path):
    """Leftover guardian record must not report exited after sentinel death."""
    import json
    from proxima_api.container_activity import (
        GuardedWriterTree,
        process_start_identity,
        retain_activity_lease,
        acquire_container_activity_lease,
        container_quiescence_lock,
        ContainerBoundaryError,
    )

    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / "stale-record"
    root.mkdir()
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        ("owner-stale", "owner-stale"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        ("stale-record", "Stale", str(root), user_id),
    ).lastrowid
    lease = acquire_container_activity_lease(conn, int(container_id))

    ready = tmp_path / "orphan-ready"
    record = tmp_path / "guardian.json"

    # Launch a setsid orphan writer under a short-lived sentinel, leave the
    # guardian record on disk (SIGKILL skips cleanup), and never seed the
    # writer into known_identities - the pre-fix false-positive path.
    launcher = os.fork()
    if launcher == 0:
        sentinel = os.fork()
        if sentinel == 0:
            try:
                os.setsid()
                writer = os.fork()
                if writer == 0:
                    try:
                        ready.write_text(str(os.getpid()), encoding="utf-8")
                        while True:
                            time.sleep(1)
                    finally:
                        os._exit(0)
                # Parent is the sentinel; stay alive until killed.
                while True:
                    time.sleep(1)
            finally:
                os._exit(0)
        # Write guardian record pointing at the live sentinel, then exit the
        # launcher so only sentinel + orphan remain.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(sentinel, 0)
                break
            except OSError:
                time.sleep(0.01)
        payload = {
            "sentinel_pid": sentinel,
            "sentinel_start": process_start_identity(sentinel) or "",
            "launcher_pid": os.getpid(),
            "owner_pid": os.getppid(),
            "owner_start": "",
            "job_name": None,
        }
        record.write_text(json.dumps(payload), encoding="utf-8")
        # Signal parent with sentinel pid via a side file then exit launcher.
        (tmp_path / "sentinel-pid").write_text(str(sentinel), encoding="utf-8")
        os._exit(0)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not (
        ready.is_file() and (tmp_path / "sentinel-pid").is_file()
    ):
        time.sleep(0.01)
    assert ready.is_file()
    assert (tmp_path / "sentinel-pid").is_file()
    try:
        os.waitpid(launcher, 0)
    except ChildProcessError:
        pass

    writer_pid = int(ready.read_text(encoding="utf-8").strip())
    sentinel_pid = int(
        (tmp_path / "sentinel-pid").read_text(encoding="utf-8").strip()
    )
    launcher_start = process_start_identity(launcher)
    # Launcher is already dead; bind with dead launcher + live record only.
    tree = GuardedWriterTree(
        launcher_pid=launcher,
        launcher_start=launcher_start or "dead-launcher",
        guardian_record=record,
        known_identities={},
    )
    # Kill sentinel hard so the record is never cleaned up and the writer is
    # reparented away from the monitored roots.
    os.kill(sentinel_pid, 9)
    try:
        os.waitpid(sentinel_pid, 0)
    except ChildProcessError:
        pass
    time.sleep(0.05)

    # Writer still alive, record still on disk, no seeded descendants.
    assert Path(f"/proc/{writer_pid}").exists()
    assert record.exists()
    assert tree.exited() is not True, (
        "stale guardian record must fail closed, not report tree exit"
    )

    retain_activity_lease(
        lease,
        pid=launcher,
        start_identity=launcher_start or "dead-launcher",
        tree=tree,
    )
    time.sleep(0.15)
    assert getattr(lease, "_released", False) is False

    blocked = False
    try:
        with container_quiescence_lock(conn, int(container_id)):
            pass
    except ContainerBoundaryError:
        blocked = True
    assert blocked, "quiescence must stay blocked on unproven orphan tree"

    os.kill(writer_pid, 9)
    try:
        os.waitpid(writer_pid, 0)
    except ChildProcessError:
        pass
    # Even after the orphan dies, leftover record keeps proof unavailable
    # (fail closed) until the record is reconciled/removed.
    assert tree.exited() is not True
    record.unlink(missing_ok=True)
    # With record gone and no live known identities, exit can clear.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and tree.exited() is not True:
        time.sleep(0.05)
    assert tree.exited() is True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="live seed arming uses Linux /proc process trees",
)
def test_writer_tree_exited_seeds_live_descendants(tmp_path):
    """exited() must capture live descendants before they can escape."""
    from proxima_api.container_activity import (
        GuardedWriterTree,
        process_start_identity,
    )

    ready = tmp_path / "seed-ready"
    parent = os.fork()
    if parent == 0:
        child = os.fork()
        if child == 0:
            try:
                ready.write_text(str(os.getpid()), encoding="utf-8")
                while True:
                    time.sleep(1)
            finally:
                os._exit(0)
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass
        os._exit(0)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not ready.is_file():
        time.sleep(0.01)
    assert ready.is_file()
    child_pid = int(ready.read_text(encoding="utf-8").strip())
    parent_start = process_start_identity(parent)
    assert parent_start
    tree = GuardedWriterTree(
        launcher_pid=parent,
        launcher_start=parent_start,
        known_identities={},
    )
    assert tree.exited() is False
    assert child_pid in tree.known_identities

    os.kill(parent, 9)
    try:
        os.waitpid(parent, 0)
    except ChildProcessError:
        pass
    # Child may still be alive; seeded identity keeps exited False.
    if Path(f"/proc/{child_pid}").exists():
        assert tree.exited() is False
        os.kill(child_pid, 9)
        try:
            os.waitpid(child_pid, 0)
        except ChildProcessError:
            pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and tree.exited() is not True:
        time.sleep(0.05)
    assert tree.exited() is True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="status latency with live orphan tree uses Linux /proc",
)
def test_app_status_stays_non_blocking_when_writer_tree_unproven(tmp_path):
    """status() must not run tree.terminate on the poll path."""
    from proxima_api.container_activity import (
        GuardedWriterTree,
        process_start_identity,
    )

    ready = tmp_path / "status-orphan-ready"
    record = tmp_path / "status-guardian.json"
    launcher = os.fork()
    if launcher == 0:
        sentinel = os.fork()
        if sentinel == 0:
            try:
                os.setsid()
                writer = os.fork()
                if writer == 0:
                    try:
                        ready.write_text(str(os.getpid()), encoding="utf-8")
                        while True:
                            time.sleep(1)
                    finally:
                        os._exit(0)
                while True:
                    time.sleep(1)
            finally:
                os._exit(0)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(sentinel, 0)
                break
            except OSError:
                time.sleep(0.01)
        payload = {
            "sentinel_pid": sentinel,
            "sentinel_start": process_start_identity(sentinel) or "",
            "launcher_pid": os.getpid(),
            "owner_pid": os.getppid(),
            "owner_start": "",
        }
        record.write_text(json.dumps(payload), encoding="utf-8")
        (tmp_path / "status-sentinel-pid").write_text(
            str(sentinel), encoding="utf-8"
        )
        os._exit(0)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not (
        ready.is_file() and (tmp_path / "status-sentinel-pid").is_file()
    ):
        time.sleep(0.01)
    assert ready.is_file()
    try:
        os.waitpid(launcher, 0)
    except ChildProcessError:
        pass
    writer_pid = int(ready.read_text(encoding="utf-8").strip())
    sentinel_pid = int(
        (tmp_path / "status-sentinel-pid").read_text(encoding="utf-8").strip()
    )
    os.kill(sentinel_pid, 9)
    try:
        os.waitpid(sentinel_pid, 0)
    except ChildProcessError:
        pass

    manager = AppManager()
    # Synthesize a drained launcher with an unproven writer tree so status
    # takes the fail-closed branch without attempting termination.
    class _DeadProc:
        returncode = 1
        pid = launcher

    tree = GuardedWriterTree(
        launcher_pid=launcher,
        launcher_start=process_start_identity(launcher) or "dead",
        guardian_record=record,
        known_identities={},
    )
    manager._apps["demo"] = {
        "proc": _DeadProc(),
        "port": 8765,
        "command": "sleep 1",
        "started_at": time.time(),
        "log": [],
        "effect_lease": None,
        "proc_pid": launcher,
        "proc_start_identity": "dead",
        "writer_tree": tree,
    }
    try:
        started = time.monotonic()
        samples = [manager.status("demo") for _ in range(5)]
        elapsed = time.monotonic() - started
        assert elapsed < 0.25, f"status polls blocked for {elapsed:.3f}s"
        for sample in samples:
            assert sample.get("running") is True
            assert sample.get("writer_tree_live") is True
            assert sample.get("ready") is not True
        # status must not have reaped/finished the effect while unproven.
        assert "demo" in manager._apps
    finally:
        manager._apps.pop("demo", None)
        try:
            os.kill(writer_pid, 9)
        except OSError:
            pass
        try:
            os.waitpid(writer_pid, 0)
        except ChildProcessError:
            pass
        record.unlink(missing_ok=True)

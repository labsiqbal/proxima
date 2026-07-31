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
            await manager.start("demo", str(tmp_path), "bash -lc 'echo boom-fail; exit 7'", 5180)
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
        assert status["log"] == [
            f"line-{number}" for number in range(20, 60)
        ]
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
            command = (
                f"exec {shlex.quote(sys.executable)} "
                f"-c {shlex.quote(launcher)}"
            )
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
            managed_group = (
                manager._apps["demo"]["authority"].process_group
            )
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
                "after-stop" in line
                for line in manager.status("demo")["log"]
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
    command = (
        f"exec {shlex.quote(sys.executable)} "
        f"-c {shlex.quote(launcher_code)}"
    )
    service_code = f"""
import asyncio
from pathlib import Path
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
        while (
            not write_result_file.is_file()
            and time.monotonic() < deadline
        ):
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
        raise OutputBrokerUnavailable(
            "Windows breakaway preview output is unavailable"
        )

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
                f"{shlex.quote(sys.executable)} -m http.server "
                "$PORT --bind 127.0.0.1"
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
            monkeypatch.setattr(apprunner, "_port_open", lambda candidate: candidate == port)
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
    )
    exact_proof = apprunner.ProcessAuthority(
        leader_pid=leader_pid,
        process_group=leader_pid,
        lineage_token="launch-token",
        containment_required=True,
        containment_pid_namespace=4242,
    )

    assert apprunner._listener_ownership(
        5180,
        authority=uncontained,
    ) == apprunner.PortOwnership.DETACHED
    assert apprunner._listener_ownership(
        5180,
        authority=missing_proof,
    ) == apprunner.PortOwnership.DETACHED
    assert apprunner._listener_ownership(
        5180,
        authority=exact_proof,
    ) == apprunner.PortOwnership.VERIFIED

    monkeypatch.setattr(
        apprunner,
        "_process_table",
        lambda _inodes: {
            leader_pid: (1, leader_pid, set()),
            detached_pid: (1, detached_pid, {listener_inode}),
        },
    )
    assert apprunner._listener_ownership(
        5180,
        authority=exact_proof,
    ) == apprunner.PortOwnership.DETACHED

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
    assert apprunner._listener_ownership(
        5180,
        authority=exact_proof,
    ) == apprunner.PortOwnership.DETACHED


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

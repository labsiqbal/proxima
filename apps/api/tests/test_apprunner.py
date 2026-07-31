from __future__ import annotations

import asyncio
import os
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


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    async def read(self, _size: int) -> bytes:
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
        await manager.start("demo", str(tmp_path), "sleep 60", port)
        manager._apps["demo"]["log"].extend(
            f"line-{number}" for number in range(60)
        )

        await manager.stop("demo")

        status = manager.status("demo")
        assert status["state"] == "stopped"
        assert status["running"] is False
        assert status["command"] == "sleep 60"
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
        gate = asyncio.Event()
        drained = asyncio.Event()
        armed = tmp_path / "terminal-trap-armed"
        original_drain = manager._drain

        async def delayed_drain(slug, proc):
            await gate.wait()
            try:
                await original_drain(slug, proc)
            finally:
                drained.set()

        manager._drain = delayed_drain
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

        async def release_drain():
            await asyncio.sleep(0.1)
            gate.set()

        release_task = asyncio.create_task(release_drain())
        await manager.stop("demo")
        await release_task
        await asyncio.wait_for(drained.wait(), timeout=2)

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

            await asyncio.wait_for(
                manager.stop("demo"),
                timeout=apprunner.FINAL_DRAIN_SECONDS + 2,
            )

            status = manager.status("demo")
            assert status["state"] == "stopped"
            assert "inherited-tail" in status["log"]
            assert manager._discard_tasks
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
            for _ in range(200):
                if not manager._discard_tasks:
                    break
                await asyncio.sleep(0.01)
            assert not manager._discard_tasks

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

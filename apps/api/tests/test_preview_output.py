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

from proxima_api import preview_output, preview_output_broker
from proxima_api.apprunner import AppManager
from proxima_api.preview_output import (
    BROKER_PROFILE_ENV,
    BROKER_PROTOCOL_ENV,
    BROKER_SOCKET_ENV,
    BROKER_STATE_ROOT_ENV,
    BrokerLog,
    MAX_PENDING_LINE_BYTES,
    OutputBroker,
    OutputBrokerUnavailable,
)
from proxima_api.preview_output_broker import PreviewSupervisor


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


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="launch cgroups require Linux",
)
def test_direct_supervisor_does_not_create_packaged_app_cgroup(
    monkeypatch,
) -> None:
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(
        preview_output_broker,
        "_cgroup_identity",
        lambda _pid: pytest.fail("direct mode must not inspect cgroup delegation"),
    )
    supervisor = PreviewSupervisor.__new__(PreviewSupervisor)

    assert supervisor._prepare_app_cgroup() is None


@pytest.mark.skipif(os.name != "posix", reason="descriptor broker requires POSIX")
def test_broker_owns_output_from_launch_and_snapshots_atomically() -> None:
    async def run_case() -> None:
        broker = await OutputBroker.open()
        broker_pid = broker.pid
        assert broker_pid is not None
        process = await broker.spawn(
            [
                sys.executable,
                "-c",
                "import os; os.write(1, b'transport-buffer-tail')",
            ],
            cwd=str(ROOT),
            env=os.environ.copy(),
            contained=False,
        )
        await asyncio.wait_for(process.wait(), timeout=5)

        snapshot = await broker.snapshot()

        assert snapshot.lines == ["transport-buffer-tail"]
        assert snapshot.eof is True
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
        process = await broker.spawn(
            [
                sys.executable,
                "-c",
                "import os, time; time.sleep(0.02); "
                "os.write(1, b'broker-api-race-tail')",
            ],
            cwd=str(ROOT),
            env=os.environ.copy(),
            contained=False,
        )

        polls = [asyncio.create_task(broker.snapshot()) for _ in range(12)]
        await asyncio.wait_for(process.wait(), timeout=5)
        final = await broker.snapshot()
        await asyncio.gather(*polls)

        assert "broker-api-race-tail" in final.lines
        assert final.eof is True
        await broker.disconnect()

    asyncio.run(run_case())


def test_broker_transfers_full_bounded_snapshot() -> None:
    async def run_case() -> None:
        broker = await OutputBroker.open()
        process = await broker.spawn(
            [
                sys.executable,
                "-c",
                (
                    "import os\n"
                    "for index in range(200):\n"
                    " os.write(1, (f'{index:03d}-' + 'x' * 16000 + "
                    "'\\n').encode())\n"
                ),
            ],
            cwd=str(ROOT),
            env=os.environ.copy(),
            contained=False,
        )
        await process.wait()
        snapshot = await broker.snapshot()
        assert len(snapshot.lines) == 200
        assert snapshot.lines[-1].startswith("199-")
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
        process = await broker.spawn(
            [sys.executable, "-c", worker],
            cwd=str(tmp_path),
            env=os.environ.copy(),
            contained=False,
        )
        await broker.disconnect()

        trigger.write_text("write", encoding="utf-8")
        deadline = time.monotonic() + 5
        while _pid_exists(process.pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert result.read_text(encoding="utf-8") == "success"
        deadline = time.monotonic() + 5
        while _pid_exists(broker_pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not _pid_exists(broker_pid)

    asyncio.run(run_case())


def test_packaged_systemd_profiles_isolate_preview_supervisors() -> None:
    production = (ROOT / "infra" / "systemd" / "proxima.service.example").read_text(
        encoding="utf-8"
    )
    staging = (
        ROOT / "infra" / "systemd" / "proxima-staging.service.example"
    ).read_text(encoding="utf-8")
    broker = (
        ROOT / "infra" / "systemd" / "proxima-preview-output@.service.example"
    ).read_text(encoding="utf-8")
    broker_socket = (
        ROOT / "infra" / "systemd" / "proxima-preview-output.socket"
    ).read_text(encoding="utf-8")
    staging_broker = (
        ROOT / "infra" / "systemd" / "proxima-staging-preview-output@.service.example"
    ).read_text(encoding="utf-8")
    staging_socket = (
        ROOT / "infra" / "systemd" / "proxima-staging-preview-output.socket"
    ).read_text(encoding="utf-8")
    user_installer = (ROOT / "scripts" / "install-user").read_text(encoding="utf-8")

    assert "Requires=proxima-preview-output.socket" in production
    assert "Requires=proxima-staging-preview-output.socket" in staging
    for service in (production, staging):
        assert "PROXIMA_OUTPUT_BROKER_SOCKET=" in service
        assert "KillMode=process" in service
        assert "TimeoutStopSec=20s" in service
        assert "output-broker" not in service
    assert "/run/proxima-preview-output.sock" in production
    assert "/run/proxima-staging-preview-output.sock" in staging
    assert "supervisor-v2:production" in production
    assert "supervisor-v2:staging" in staging
    assert "/var/lib/proxima/preview-supervisors" in production
    assert "/var/lib/proxima-staging/preview-supervisors" in staging
    assert "ExecStart=/opt/proxima/scripts/proxima output-broker" in broker
    assert (
        "ExecStart=/opt/proxima-staging/scripts/proxima output-broker" in staging_broker
    )
    assert "StandardInput=socket" in broker
    assert "Delegate=yes" in broker
    assert "Delegate=yes" in staging_broker
    assert "KillMode=process" in broker
    assert "KillMode=process" in staging_broker
    assert "Accept=yes" in broker_socket
    assert "Accept=yes" in staging_socket
    assert "proxima-preview-output@.service" in user_installer
    assert "PROXIMA_OUTPUT_BROKER_SOCKET=%t/" in user_installer
    assert "PROXIMA_OUTPUT_BROKER_PROTOCOL=" in user_installer
    assert "PROXIMA_PREVIEW_SCOPE_STATE_ROOT=" in user_installer
    assert "check-preview-drained" in user_installer
    assert "KillMode=process" in user_installer


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="launch cgroups require Linux",
)
def test_supervisor_signals_only_launch_specific_app_cgroup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_cgroup = tmp_path / "app"
    nested = app_cgroup / "nested"
    nested.mkdir(parents=True)
    app_cgroup.joinpath("cgroup.procs").write_text(
        "101\n",
        encoding="ascii",
    )
    app_cgroup.joinpath("cgroup.kill").write_text("", encoding="ascii")
    nested.joinpath("cgroup.procs").write_text(
        "102\n",
        encoding="ascii",
    )
    signaled: list[tuple[int, int]] = []

    class Process:
        pid = 101

        @staticmethod
        def poll():
            return None

    supervisor = PreviewSupervisor.__new__(PreviewSupervisor)
    supervisor.process = Process()
    supervisor.app_cgroup = app_cgroup
    supervisor.app_cgroup_identity = "0::/managed\n"
    supervisor.process_status = lambda: {"pid": 101}
    monkeypatch.setattr(
        preview_output_broker,
        "_cgroup_identity",
        lambda pid: (
            "0::/managed\n"
            if pid == 101
            else ("0::/managed/nested\n" if pid == 102 else "0::/escaped\n")
        ),
    )
    monkeypatch.setattr(
        preview_output_broker,
        "_pidfd_open",
        lambda pid: pid + 1000,
    )
    monkeypatch.setattr(
        preview_output_broker,
        "_pidfd_send_signal",
        lambda pidfd, sig: signaled.append((pidfd - 1000, sig)),
    )

    assert supervisor._signal("term") == {"pid": 101}
    assert signaled == [(101, signal.SIGTERM), (102, signal.SIGTERM)]
    assert all(pid != 999 for pid, _sig in signaled)
    assert supervisor._signal("kill") == {"pid": 101}
    assert app_cgroup.joinpath("cgroup.kill").read_text(encoding="ascii") == "1"


@pytest.mark.skipif(os.name != "posix", reason="launcher handshake requires POSIX")
def test_app_launcher_waits_for_verified_cgroup_release(tmp_path: Path) -> None:
    cgroup_procs = tmp_path / "cgroup.procs"
    result = tmp_path / "owner-code-ran"
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    launcher = ROOT / "apps" / "api" / "proxima_api" / "preview_app_launcher.py"
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            str(launcher),
            str(cgroup_procs),
            str(ready_write),
            str(release_read),
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(result)!r}).write_text('ran')",
        ],
        pass_fds=(ready_write, release_read),
    )
    os.close(ready_write)
    os.close(release_read)
    try:
        assert os.read(ready_read, 1) == b"1"
        assert cgroup_procs.read_text(encoding="ascii") == "0"
        assert not result.exists()
        os.write(release_write, b"1")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert result.read_text(encoding="utf-8") == "ran"
    finally:
        os.close(ready_read)
        os.close(release_write)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_delta_polling_is_constant_size_when_log_is_unchanged() -> None:
    log = BrokerLog()
    for index in range(200):
        log.feed((f"{index:03d}-" + ("x" * 16000) + "\n").encode())
    initial = json.dumps(log.state(), separators=(",", ":")).encode()
    state = log.state()
    cursor = int(state["line_cursor"])
    version = int(state["version"])

    unchanged = sum(
        len(
            json.dumps(
                log.state(
                    since_version=version,
                    after_line=cursor,
                ),
                separators=(",", ":"),
            ).encode()
        )
        for _ in range(100)
    )

    assert len(initial) > 3_000_000
    assert unchanged < 10_000


def test_unchanged_delta_preserves_partial_log_line() -> None:
    app = {
        "log": ["complete", "partial"],
        "log_complete": ["complete"],
        "log_pending": "partial",
        "output_version": 2,
        "output_line_cursor": 1,
    }

    AppManager._apply_output_delta(
        app,
        preview_output.OutputDelta(
            lines=[],
            pending="",
            eof=False,
            version=2,
            line_cursor=1,
            reset=False,
            changed=False,
        ),
    )

    assert app["log"] == ["complete", "partial"]
    assert app["log_pending"] == "partial"


def test_direct_broker_handshake_failure_terminates_and_reaps(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FailedBroker:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            events.append(f"wait:{timeout}")
            if len([item for item in events if item.startswith("wait:")]) == 1:
                raise subprocess.TimeoutExpired("broker", timeout)
            return -9

    monkeypatch.setattr(
        preview_output.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FailedBroker(),
    )
    monkeypatch.setattr(
        OutputBroker,
        "_receive_json",
        staticmethod(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OutputBrokerUnavailable("bad handshake")
            )
        ),
    )

    with pytest.raises(OutputBrokerUnavailable, match="bad handshake"):
        OutputBroker._open_posix_direct()

    assert events == ["terminate", "wait:1.0", "kill", "wait:1.0"]


def test_supervisor_profile_mismatch_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv(BROKER_PROTOCOL_ENV, "expected-protocol")
    monkeypatch.setenv(BROKER_PROFILE_ENV, "production")
    with pytest.raises(OutputBrokerUnavailable, match="protocol"):
        OutputBroker._validate_identity(
            {
                "protocol": "stale-protocol",
                "profile": "production",
                "session_id": "session",
                "token": "token",
                "endpoint": {"kind": "tcp", "host": "127.0.0.1", "port": 1},
                "pid": 1,
                "start_time": 1,
                "cgroup": None,
                "controller_cgroup": None,
            }
        )


def test_system_wide_upgrade_migrates_and_probes_supervisor_first() -> None:
    guide = (ROOT / "infra" / "systemd" / "README.md").read_text(encoding="utf-8")
    update = guide.split("## Updates and ownership", 1)[1]

    assert update.index("proxima-preview-output.socket") < update.index(
        "systemctl restart proxima.service"
    )
    assert update.index("systemctl daemon-reload") < update.index(
        "systemctl restart proxima.service"
    )
    assert update.index("preview-broker-check") < update.index(
        "systemctl restart proxima.service"
    )
    assert "proxima-staging-preview-output" in update
    assert "supervisor-v2:staging" in update
    assert update.index("systemctl stop proxima.service") < update.index(
        "check-preview-drained"
    )
    assert update.index("check-preview-drained") < update.index(
        "systemctl daemon-reload"
    )


@pytest.mark.skipif(os.name != "posix", reason="procfs check requires POSIX")
def test_upgrade_preflight_refuses_legacy_preview_process(
    tmp_path: Path,
) -> None:
    helper = ROOT / "scripts" / "check-preview-drained"
    environment = {
        **os.environ,
        "PROXIMA_APP_LINEAGE": "legacy",
        "PROXIMA_PREVIEW_AUTHORITY_PROTOCOL": ("proxima-preview-supervisor-v1:user"),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env=environment,
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--protocol",
                "proxima-preview-supervisor-v2:user",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert str(process.pid) in result.stderr
        assert "No service units were replaced" in result.stderr
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="procfs check requires POSIX")
def test_upgrade_preflight_refuses_unmarked_legacy_preview_child(
    tmp_path: Path,
) -> None:
    helper = ROOT / "scripts" / "check-preview-drained"
    child_pid_path = tmp_path / "child.pid"
    api_code = (
        "import os, pathlib, subprocess, sys, time\n"
        "environment = dict(os.environ)\n"
        "environment['PORT'] = '4321'\n"
        "environment['HOST'] = '127.0.0.1'\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], env=environment)\n"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            api_code,
            "/opt/proxima/apps/api/scripts/serve.py",
        ],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        result = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--protocol",
                "proxima-preview-supervisor-v2:production",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert str(child_pid) in result.stderr
        assert "No service units were replaced" in result.stderr
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)


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
                "child = asyncio.run(broker.spawn([sys.executable, '-c', worker], cwd=str(pathlib.Path.cwd()), env=os.environ.copy(), contained=False))",
                f"state = pathlib.Path({str(state_prefix)!r} + '-' + str(generation))",
                "state.write_text(json.dumps({'api': os.getpid(), 'broker': broker.pid, 'child': child.pid}))",
                "time.sleep(60)",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    api_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = api_root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env[BROKER_SOCKET_ENV] = socket_path
    env[BROKER_PROTOCOL_ENV] = os.environ.get(
        "PROXIMA_TEST_SYSTEMD_BROKER_PROTOCOL",
        "proxima-preview-supervisor-v2:user",
    )
    env[BROKER_PROFILE_ENV] = os.environ.get(
        "PROXIMA_TEST_SYSTEMD_BROKER_PROFILE",
        "user",
    )

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
            f"--setenv={BROKER_PROTOCOL_ENV}={env[BROKER_PROTOCOL_ENV]}",
            f"--setenv={BROKER_PROFILE_ENV}={env[BROKER_PROFILE_ENV]}",
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
            api_cgroup = Path(f"/proc/{states[-1]['api']}/cgroup").read_text(
                encoding="utf-8"
            )
            broker_cgroup = Path(f"/proc/{states[-1]['broker']}/cgroup").read_text(
                encoding="utf-8"
            )
            app_cgroup = Path(f"/proc/{states[-1]['child']}/cgroup").read_text(
                encoding="utf-8"
            )
            assert api_cgroup != broker_cgroup
            assert app_cgroup != broker_cgroup
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


@pytest.mark.skipif(
    os.environ.get("PROXIMA_TEST_SYSTEMD_BROKER") != "1"
    or shutil.which("systemd-run") is None,
    reason="real user-systemd supervisor integration is opt-in",
)
def test_systemd_restart_adopts_exact_supervised_app(
    tmp_path: Path,
) -> None:
    socket_path = os.environ["PROXIMA_TEST_SYSTEMD_BROKER_SOCKET"]
    protocol = os.environ.get(
        "PROXIMA_TEST_SYSTEMD_BROKER_PROTOCOL",
        "proxima-preview-supervisor-v2:user",
    )
    profile = os.environ.get(
        "PROXIMA_TEST_SYSTEMD_BROKER_PROFILE",
        "user",
    )
    unit = f"proxima-preview-adoption-test-{uuid.uuid4().hex}"
    script = tmp_path / "api_adoption.py"
    generation_path = tmp_path / "generation"
    first_state = tmp_path / "first.json"
    adoption_result = tmp_path / "adopted.json"
    durable_state = tmp_path / "durable"
    script.write_text(
        "\n".join(
            [
                "import asyncio, json, pathlib",
                "from proxima_api.apprunner import AppManager",
                f"generation_path = pathlib.Path({str(generation_path)!r})",
                "generation = int(generation_path.read_text()) + 1 if generation_path.exists() else 1",
                "generation_path.write_text(str(generation))",
                "async def run():",
                f"    manager = AppManager(state_root={str(durable_state)!r}, profile={profile!r})",
                "    if generation == 1:",
                f"        await manager.start('demo', {str(tmp_path)!r}, 'sleep 60', 49231)",
                "        app = manager._apps['demo']",
                f"        pathlib.Path({str(first_state)!r}).write_text(json.dumps({{'pid': app['proc'].pid, 'generation': app['generation']}}))",
                "        await asyncio.sleep(60)",
                "    else:",
                "        await manager.reconcile()",
                "        app = manager._apps['demo']",
                f"        first = json.loads(pathlib.Path({str(first_state)!r}).read_text())",
                "        assert app['proc'].pid == first['pid']",
                "        assert app['generation'] == first['generation']",
                f"        pathlib.Path({str(adoption_result)!r}).write_text(json.dumps({{'pid': app['proc'].pid}}))",
                "        await manager.shutdown()",
                "asyncio.run(run())",
            ]
        ),
        encoding="utf-8",
    )
    api_root = str(Path(__file__).resolve().parents[1])

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
            f"--setenv=PYTHONPATH={api_root}",
            f"--setenv={BROKER_SOCKET_ENV}={socket_path}",
            f"--setenv={BROKER_PROTOCOL_ENV}={protocol}",
            f"--setenv={BROKER_PROFILE_ENV}={profile}",
            f"--setenv={BROKER_STATE_ROOT_ENV}={durable_state}",
            sys.executable,
            str(script),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not first_state.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert first_state.exists()
        assert systemctl("restart", unit).returncode == 0
        deadline = time.monotonic() + 10
        while not adoption_result.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert adoption_result.exists()
        assert (
            json.loads(adoption_result.read_text())["pid"]
            == json.loads(first_state.read_text())["pid"]
        )
    finally:
        systemctl("stop", unit)
        systemctl("reset-failed", unit)

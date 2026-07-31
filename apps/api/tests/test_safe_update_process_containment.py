from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.safe_updater import controller as controller_module
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.evidence import EvidenceStore
from apps.safe_updater.journal import Journal
from apps.safe_updater.state_machine import Phase
from apps.safe_updater.write_fence import (
    ingress_pending_path,
    prepare_ingress_lock,
    write as write_fence,
)
from proxima_api import acp as acp_module
from proxima_api.acp import AcpManager
from proxima_api.main import create_app
from proxima_api.process_containment import terminate_and_verify


def _digest(intent: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            intent,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@pytest.mark.parametrize("state", ["active", "pending"])
def test_recovery_rejects_unacknowledged_maintenance_activation(
    tmp_path: Path,
    state: str,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller = SafeUpdateController.create_disposable_fixture(root)
    intent = {"candidate_commit": "c" * 40}
    accepted = controller.submit(intent)
    evidence = EvidenceStore(root).persist(
        accepted.run_id,
        {"qualification.json": "{}"},
    )
    journal = Journal(
        root / "journal" / f"{accepted.run_id}.jsonl",
        _digest(intent),
    )
    journal.append(
        Phase.CANDIDATE_STAGED,
        {"candidate_evidence": evidence.digest},
    )
    fence = root / "status" / "fence.json"
    prepare_ingress_lock(fence)
    if state == "active":
        write_fence(
            fence,
            accepted.run_id,
            Phase.WRITE_FENCED.value,
        )
    else:
        ingress_pending_path(fence).write_text(
            json.dumps({"run_id": accepted.run_id}),
            encoding="utf-8",
        )

    recovered = controller.recovery_status(accepted.run_id, intent)

    assert recovered.safe is False
    assert recovered.action == "do_not_start_any_release"
    assert recovered.reason == (
        "maintenance activation was not acknowledged by the journal"
    )


def test_recovery_fails_closed_while_controller_lock_is_busy(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    controller = SafeUpdateController.create_disposable_fixture(root)
    intent = {"candidate_commit": "c" * 40}
    accepted = controller.submit(intent)
    holder = SafeUpdateController(root)
    acquired = holder.lock.acquire("d" * 32)
    assert acquired.acquired

    try:
        recovered = controller.recovery_status(accepted.run_id, intent)
    finally:
        holder.lock.release()

    assert recovered.safe is False
    assert recovered.action == "do_not_start_any_release"
    assert recovered.reason == "safe_update_in_progress"


def test_recovery_holds_controller_lock_across_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller = SafeUpdateController.create_disposable_fixture(root)
    intent = {"candidate_commit": "c" * 40}
    accepted = controller.submit(intent)
    entered = threading.Event()
    proceed = threading.Event()
    original_read_activation_state = controller_module.read_activation_state

    def read_activation_state(path: Path):
        entered.set()
        if not proceed.wait(2):
            raise RuntimeError("timed out waiting to inspect activation")
        return original_read_activation_state(path)

    monkeypatch.setattr(
        controller_module,
        "read_activation_state",
        read_activation_state,
    )
    results = []
    errors = []

    def recover() -> None:
        try:
            results.append(controller.recovery_status(accepted.run_id, intent))
        except BaseException as exc:
            errors.append(exc)

    recovery_thread = threading.Thread(target=recover)
    recovery_thread.start()
    contender = SafeUpdateController(root)
    try:
        assert entered.wait(2)
        acquired = contender.lock.acquire("e" * 32, publish_owner=False)
        if acquired.acquired:
            contender.lock.release()
        assert acquired.acquired is False
    finally:
        proceed.set()
        recovery_thread.join(2)

    assert recovery_thread.is_alive() is False
    assert errors == []
    assert len(results) == 1
    assert results[0].safe is True
    assert results[0].action == "discard_candidate"


def test_fixture_fence_path_is_canonical_for_recovery(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    controller = SafeUpdateController.create_disposable_fixture(root)

    with pytest.raises(RuntimeError, match="status path must be canonical"):
        controller._disposable_fixture_paths(
            root / "status" / "alternate.json",
            root / "data" / "proxima.db",
            root / "candidate" / "staged.db",
        )


class _ManagedProcess:
    instances: list["_ManagedProcess"] = []

    def __init__(self, *_args, **_kwargs):
        self._started = False
        self.config_sig = ()
        self.stopped = asyncio.Event()
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self.stopped.set()

    def resolve_permission(self, *_args) -> bool:
        return False


def _wait_for_path(path: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def test_pending_activation_stops_cached_runner_before_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fence = tmp_path / "status" / "fence.json"
    prepare_ingress_lock(fence)
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "start_worker": False,
            "safe_update_fence_path": str(fence),
            "run_worker_poll_interval_ms": 20,
        }
    )
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _ManagedProcess,
    )
    _ManagedProcess.instances.clear()

    async def run_case() -> None:
        spec = SimpleNamespace(id="fake", protocol="acp")
        await app.state.acp_manager.get(
            spec,
            str(tmp_path / "home"),
            str(tmp_path / "workspace"),
        )
        process = _ManagedProcess.instances[-1]
        app.state.startup_lease.release()
        worker = app.state.worker
        worker.reap_stale_runs = lambda _seconds: None
        worker.reap_orphaned_jobs = lambda: None
        worker.satpam.maybe_tick = lambda _now: None
        worker.claim_run = lambda: None
        worker.start()
        errors: list[BaseException] = []

        def activate() -> None:
            try:
                write_fence(
                    fence,
                    "7" * 32,
                    Phase.WRITE_FENCED.value,
                    drain_timeout_seconds=2,
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=activate)
        thread.start()
        await asyncio.to_thread(
            _wait_for_path,
            ingress_pending_path(fence),
        )
        await asyncio.to_thread(thread.join, 5)
        await worker.stop()

        assert not thread.is_alive()
        assert errors == []
        assert process.stopped.is_set()
        assert fence.is_file()

    asyncio.run(run_case())


class _Lease:
    def __init__(self) -> None:
        self.released = False
        self.suspended = False

    def release(self) -> None:
        self.released = True

    def suspend_admission(self) -> None:
        self.suspended = True


class _Boundary:
    def __init__(self) -> None:
        self.lease = _Lease()
        self.retained: list[_Lease] = []

    def background_lease(self) -> _Lease:
        return self.lease

    def retain(self, lease: _Lease) -> None:
        self.retained.append(lease)


class _FailingStopProcess(_ManagedProcess):
    async def stop(self) -> None:
        raise RuntimeError("still alive")


def test_guarded_process_scopes_recycle_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manager = AcpManager()
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _ManagedProcess,
    )
    _ManagedProcess.instances.clear()
    spec = SimpleNamespace(id="fake", protocol="acp")

    async def run_case() -> None:
        first = await manager.get(
            spec,
            str(tmp_path / "home"),
            str(tmp_path),
            cache_scope="run-1",
        )
        second = await manager.get(
            spec,
            str(tmp_path / "home"),
            str(tmp_path),
            cache_scope="run-2",
        )
        assert first is not second

        await manager.recycle(
            spec,
            str(tmp_path / "home"),
            str(tmp_path),
            cache_scope="run-1",
        )

        assert first.stopped.is_set()
        assert second.stopped.is_set() is False

        await manager.recycle(
            spec,
            str(tmp_path / "home"),
            str(tmp_path),
            cache_scope="run-2",
        )
        assert second.stopped.is_set()

    asyncio.run(run_case())


def test_failed_runner_shutdown_retains_lifetime_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    boundary = _Boundary()
    manager = AcpManager(
        contained=True,
        maintenance=boundary,
    )
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _FailingStopProcess,
    )
    spec = SimpleNamespace(id="fake", protocol="acp")

    async def run_case() -> None:
        await manager.get(spec, str(tmp_path / "home"), str(tmp_path))
        with pytest.raises(
            RuntimeError,
            match="runner containment shutdown failed",
        ):
            await manager.shutdown()

    asyncio.run(run_case())

    assert boundary.lease.released is False
    assert boundary.lease.suspended is True
    assert boundary.retained == [boundary.lease]


class _UnstoppableProcess:
    returncode = None

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        raise asyncio.TimeoutError


def test_process_exit_must_be_observed_after_kill():
    process = _UnstoppableProcess()

    async def run_case() -> None:
        with pytest.raises(RuntimeError, match="did not exit after kill"):
            await terminate_and_verify(
                process,
                label="runner",
                timeout=0,
            )

    asyncio.run(run_case())

    assert process.terminated is True
    assert process.killed is True

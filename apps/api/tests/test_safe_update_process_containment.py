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


class _SlowThenDeadProcess:
    """Launcher that only exits after kill(); first wait times out."""

    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._wait_calls = 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self._wait_calls += 1
        if not self.killed:
            raise asyncio.TimeoutError
        self.returncode = -9
        return -9


class _ClearTree:
    def __init__(self) -> None:
        self.seeded = 0
        self.terminated = False

    def seed_live_members(self) -> None:
        self.seeded += 1

    def terminate(self, **_kwargs) -> bool:
        self.terminated = True
        return False

    def exited(self) -> bool | None:
        return True


def test_terminate_and_verify_continues_after_successful_post_kill_wait():
    """A successful post-kill wait must fall through to tree exit proof."""
    process = _SlowThenDeadProcess()
    tree = _ClearTree()

    async def run_case() -> None:
        await terminate_and_verify(
            process,
            label="runner",
            timeout=0.2,
            tree=tree,
        )

    asyncio.run(run_case())
    assert process.killed is True
    assert process.returncode == -9
    assert tree.terminated is True
    assert tree.seeded >= 1


class _UnprovenTree:
    def seed_live_members(self) -> None:
        return None

    def exited(self) -> bool | None:
        return None


class _StartFailsUnprovenProcess:
    instances: list["_StartFailsUnprovenProcess"] = []

    def __init__(self, *_args, **kwargs):
        self.proc = SimpleNamespace(returncode=0, pid=4242)
        self.writer_tree = _UnprovenTree()
        self.activity_lease = kwargs.get("activity_lease")
        self.config_sig = ()
        self.retained_calls = 0
        self.__class__.instances.append(self)

    def _retain_activity_for_unproven_tree(self) -> None:
        self.retained_calls += 1
        lease = self.activity_lease
        if lease is None:
            return
        lease._retained_for_writer_tree = True
        retain = getattr(lease, "_on_retain", None)
        if callable(retain):
            retain(self.writer_tree)

    async def start(self) -> None:
        # Simulate initialize failure after the writer tree was bound.
        raise RuntimeError("initialize failed")

    async def stop(self) -> None:
        self._retain_activity_for_unproven_tree()
        raise RuntimeError("ACP runner process tree did not exit after kill")

    def resolve_permission(self, *_args) -> bool:
        return False


class _StartFailsStopUnprovenProcess(_StartFailsUnprovenProcess):
    """start() binds a tree then stop() fails to prove exit."""

    async def start(self) -> None:
        try:
            raise RuntimeError("initialize failed")
        except BaseException as start_exc:
            try:
                await self.stop()
            except BaseException as stop_exc:
                raise start_exc from stop_exc
            raise


def test_acp_start_failure_retains_ingress_when_tree_unproven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Start-failure cleanup must not release ingress on launcher returncode."""
    boundary = _Boundary()
    manager = AcpManager(contained=True, maintenance=boundary)
    _StartFailsUnprovenProcess.instances.clear()
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _StartFailsUnprovenProcess,
    )
    spec = SimpleNamespace(id="fake", protocol="acp")

    async def run_case() -> None:
        with pytest.raises(RuntimeError, match="initialize failed"):
            await manager.get(spec, str(tmp_path / "home"), str(tmp_path))

    asyncio.run(run_case())

    assert boundary.lease.released is False
    assert boundary.lease.suspended is True
    assert boundary.retained == [boundary.lease]


def test_acp_start_failure_retains_activity_lease_when_tree_unproven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Uncached initialize failure must transfer the activity lease to the tree."""
    boundary = _Boundary()
    manager = AcpManager(contained=True, maintenance=boundary)
    _StartFailsUnprovenProcess.instances.clear()
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _StartFailsUnprovenProcess,
    )
    retained: dict[str, object] = {}

    class ActivityLease:
        def __init__(self) -> None:
            self._retained_for_writer_tree = False
            self._released = False
            self.released = False

        def _on_retain(self, tree) -> None:
            retained["tree"] = tree

        def release(self) -> None:
            self.released = True
            self._released = True

        def guard_process(self, command):
            return command, {}

        def mark_process_started(self) -> None:
            return None

    activity = ActivityLease()
    spec = SimpleNamespace(id="fake", protocol="acp")

    async def run_case() -> None:
        with pytest.raises(RuntimeError, match="initialize failed"):
            await manager.get(
                spec,
                str(tmp_path / "home"),
                str(tmp_path),
                activity_lease=activity,
                cache_scope="run-1",
            )

    asyncio.run(run_case())

    assert len(_StartFailsUnprovenProcess.instances) == 1
    proc = _StartFailsUnprovenProcess.instances[0]
    assert proc.retained_calls >= 1
    assert activity._retained_for_writer_tree is True
    assert activity.released is False
    assert retained["tree"] is proc.writer_tree
    assert boundary.lease.released is False
    assert manager._procs == {}

    # Worker finally: recycle is a no-op for uncached start failure, but the
    # transferred retention flag must keep the shared activity blocker.
    recycle_verified = True

    async def recycle_noop() -> None:
        await manager.recycle(
            spec,
            str(tmp_path / "home"),
            str(tmp_path),
            cache_scope="run-1",
        )

    asyncio.run(recycle_noop())
    if (
        recycle_verified
        and not getattr(activity, "_retained_for_writer_tree", False)
    ):
        activity.release()
    assert activity.released is False


def test_acp_start_failure_stop_path_retains_activity_before_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """stop() during initialize failure retains activity before propagating."""
    boundary = _Boundary()
    manager = AcpManager(contained=True, maintenance=boundary)
    _StartFailsUnprovenProcess.instances.clear()
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _StartFailsStopUnprovenProcess,
    )

    class ActivityLease:
        def __init__(self) -> None:
            self._retained_for_writer_tree = False
            self._released = False
            self.released = False

        def release(self) -> None:
            self.released = True
            self._released = True

        def guard_process(self, command):
            return command, {}

        def mark_process_started(self) -> None:
            return None

    activity = ActivityLease()
    spec = SimpleNamespace(id="fake", protocol="acp")

    async def run_case() -> None:
        with pytest.raises(
            RuntimeError,
            match="initialize failed|process tree did not exit",
        ):
            await manager.get(
                spec,
                str(tmp_path / "home"),
                str(tmp_path),
                activity_lease=activity,
            )

    asyncio.run(run_case())
    proc = _StartFailsUnprovenProcess.instances[0]
    assert proc.retained_calls >= 1
    assert activity._retained_for_writer_tree is True
    assert activity.released is False


class _LiveDescendantTree:
    """Tree that stays unproven while a real descendant pid is alive."""

    def __init__(self, pid: int, start: str) -> None:
        self.launcher_pid = pid
        self.launcher_start = start
        self.known_identities = {pid: start}
        self.members_observed = True

    def seed_live_members(self) -> None:
        return None

    def has_binding(self) -> bool:
        return True

    def monitor_roots(self) -> list[tuple[int, str]]:
        return [(self.launcher_pid, self.launcher_start)]

    def exited(self) -> bool | None:
        from proxima_api.container_activity import process_start_identity

        current = process_start_identity(self.launcher_pid)
        if current is None:
            return True
        if current != self.launcher_start:
            return True
        return False

    def terminate(self, **_kwargs) -> bool:
        return self.exited() is True


class _InitializeFailsLiveTreeProcess:
    instances: list["_InitializeFailsLiveTreeProcess"] = []

    def __init__(self, *_args, **kwargs):
        self.activity_lease = kwargs.get("activity_lease")
        self.proc = SimpleNamespace(returncode=None, pid=None)
        self.writer_tree = None
        self.config_sig = ()
        self._descendant = None
        self.__class__.instances.append(self)

    async def start(self) -> None:
        import os
        import time as time_mod
        from proxima_api.container_activity import process_start_identity

        ready = os.environ.get("PROXIMA_TEST_DESCENDANT_READY")
        assert ready
        ready_path = Path(ready)
        child = os.fork()
        if child == 0:
            try:
                ready_path.write_text(str(os.getpid()), encoding="utf-8")
                while True:
                    time_mod.sleep(1)
            finally:
                os._exit(0)
        self._descendant = child
        deadline = time_mod.monotonic() + 2
        while time_mod.monotonic() < deadline and not ready_path.is_file():
            time_mod.sleep(0.01)
        start = process_start_identity(child)
        assert start
        self.proc = SimpleNamespace(returncode=None, pid=child)
        self.writer_tree = _LiveDescendantTree(child, start)
        if self.activity_lease is not None:
            self.activity_lease.mark_process_started()
        try:
            raise RuntimeError("initialize failed")
        except BaseException as start_exc:
            try:
                await self.stop()
            except BaseException as stop_exc:
                raise start_exc from stop_exc
            raise

    def _retain_activity_for_unproven_tree(self) -> None:
        from proxima_api.container_activity import retain_activity_lease

        if self.activity_lease is None:
            return
        tree = self.writer_tree
        if tree is not None and tree.exited() is True:
            return
        retain_activity_lease(
            self.activity_lease,
            tree=tree,
            pid=tree.launcher_pid if tree is not None else None,
            start_identity=tree.launcher_start if tree is not None else None,
        )

    async def stop(self) -> None:
        # Refuse to prove tree exit while the descendant lives - mirrors
        # terminate_and_verify raising on unproven guarded writers.
        if self.writer_tree is not None and self.writer_tree.exited() is not True:
            self._retain_activity_for_unproven_tree()
            raise RuntimeError("ACP runner process tree did not exit after kill")
        self._retain_activity_for_unproven_tree()

    def resolve_permission(self, *_args) -> bool:
        return False


@pytest.mark.skipif(
    __import__("sys").platform.startswith("win"),
    reason="fork-based live descendant fixture is POSIX-only",
)
def test_acp_initialize_failure_retains_activity_and_blocks_quiescence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Live descendant after initialize failure keeps migration quiescence blocked."""
    import os
    import time as time_mod

    from proxima_api.container_activity import (
        ContainerBoundaryError,
        acquire_container_activity_lease,
        container_quiescence_lock,
    )
    from proxima_api.db import connect, init_db

    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / "proj"
    root.mkdir()
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        ("owner-acp-init", "owner-acp-init"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        ("acp-init", "ACP Init", str(root), user_id),
    ).lastrowid
    activity = acquire_container_activity_lease(conn, int(container_id))

    ready = tmp_path / "descendant-ready"
    monkeypatch.setenv("PROXIMA_TEST_DESCENDANT_READY", str(ready))

    boundary = _Boundary()
    manager = AcpManager(contained=True, maintenance=boundary)
    _InitializeFailsLiveTreeProcess.instances.clear()
    monkeypatch.setattr(
        acp_module,
        "_process_class",
        lambda _spec: _InitializeFailsLiveTreeProcess,
    )
    spec = SimpleNamespace(id="fake", protocol="acp")

    async def run_case() -> None:
        with pytest.raises(
            RuntimeError,
            match="initialize failed|process tree did not exit",
        ):
            await manager.get(
                spec,
                str(tmp_path / "home"),
                str(tmp_path / "cwd"),
                activity_lease=activity,
                cache_scope="run-live",
            )

    asyncio.run(run_case())

    proc = _InitializeFailsLiveTreeProcess.instances[0]
    assert getattr(activity, "_retained_for_writer_tree", False) is True
    assert getattr(activity, "_released", False) is False
    assert manager._procs == {}

    # Worker finally pattern: recycle no-op must not drop the transferred lease.
    recycle_verified = True

    async def recycle_noop() -> None:
        await manager.recycle(
            spec,
            str(tmp_path / "home"),
            str(tmp_path / "cwd"),
            cache_scope="run-live",
        )

    asyncio.run(recycle_noop())
    if (
        recycle_verified
        and not getattr(activity, "_retained_for_writer_tree", False)
    ):
        activity.release()
    assert getattr(activity, "_released", False) is False

    blocked = False
    try:
        exclusive = acquire_container_activity_lease(
            conn,
            int(container_id),
            shared=False,
            timeout=0.2,
        )
    except ContainerBoundaryError:
        blocked = True
    else:
        exclusive.release()
    assert blocked, "retained activity lease must block exclusive quiescence"
    assert getattr(activity, "_released", False) is False

    # Delayed exact-tree exit: once the descendant dies, retain monitor may
    # release; quiescence should eventually clear the in-process hold.
    child = proc._descendant
    assert child is not None
    os.kill(child, 9)
    try:
        os.waitpid(child, 0)
    except ChildProcessError:
        pass
    deadline = time_mod.monotonic() + 3
    while (
        time_mod.monotonic() < deadline
        and not getattr(activity, "_released", False)
    ):
        time_mod.sleep(0.05)
    assert getattr(activity, "_released", False) is True

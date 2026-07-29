from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.safe_updater.circuit_breaker import CircuitBreaker
from apps.safe_updater import circuit_breaker as circuit_breaker_module
from apps.safe_updater import controller as controller_module
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.journal import Journal, JournalIntegrityError
from apps.safe_updater.layout import ReleaseLayout
from apps.safe_updater.service_adapter import DisposableServiceAdapter
from apps.safe_updater.state_machine import Phase
from apps.safe_updater.write_fence import (
    ingress_pending_path,
    prepare_ingress_lock,
    write as write_fence,
)
from proxima_api.db import connect, init_db
from proxima_api import main as main_module
from proxima_api.main import create_app
from proxima_api.maintenance_status import MaintenanceBoundary
from proxima_api.migrations import run_migrations


PREVIOUS = f"sha256-{'a' * 40}-{'b' * 12}"
CANDIDATE = f"sha256-{'c' * 40}-{'d' * 12}"


def _database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE fixture_values(value TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO fixture_values(value) VALUES (?)",
            (value,),
        )
        connection.commit()
    finally:
        connection.close()


def _wal_database(path: Path, value: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute("CREATE TABLE fixture_values(value TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO fixture_values(value) VALUES (?)",
        (value,),
    )
    connection.commit()
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    assert path.with_name(path.name + "-shm").is_file()
    return connection


def _database_value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(
            connection.execute(
                "SELECT value FROM fixture_values"
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _digest(intent: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            intent,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _wait_for_path(path: Path, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def _provision_ingress(fence: Path) -> None:
    prepare_ingress_lock(fence)


def _journal(root: Path, run_id: str, intent: dict[str, str]) -> Journal:
    return Journal(root / "journal" / f"{run_id}.jsonl", _digest(intent))


def _staged_run(
    root: Path,
) -> tuple[SafeUpdateController, str, dict[str, str]]:
    controller = SafeUpdateController.create_disposable_fixture(root)
    intent = {"base_commit": "a" * 40, "candidate_commit": "c" * 40}
    submitted = controller.submit(intent)
    _journal(root, submitted.run_id, intent).append(
        Phase.CANDIDATE_STAGED,
        {"candidate_evidence": "e" * 64},
    )
    layout = ReleaseLayout(root)
    layout.release_dir(PREVIOUS).mkdir(parents=True)
    layout.release_dir(CANDIDATE).mkdir(parents=True)
    layout.set_pointer("active", PREVIOUS)
    layout.set_pointer("last-good", PREVIOUS)
    return controller, submitted.run_id, intent


def _fixture_paths(root: Path) -> tuple[Path, Path, Path]:
    fence = root / "status" / "fence.json"
    live = root / "data" / "proxima.db"
    live.parent.mkdir()
    staged = root / "candidate" / "staged.db"
    staged.parent.mkdir()
    return fence, live, staged


def _promote(
    controller: SafeUpdateController,
    run_id: str,
    intent: dict[str, str],
    adapter: DisposableServiceAdapter,
    fence: Path,
    live: Path,
    staged: Path,
    probe: Callable[[str, str], None] | None = None,
) -> str:
    return controller.promote_disposable_fixture(
        run_id,
        intent,
        adapter=adapter,
        fence_path=fence,
        live_database=live,
        staged_database=staged,
        previous_release_id=PREVIOUS,
        candidate_release_id=CANDIDATE,
        probe=probe or (lambda _mode, _release: None),
    )


def _interrupt_append_once(
    monkeypatch: pytest.MonkeyPatch,
    phase: Phase,
    *,
    after_durable_append: bool,
) -> None:
    original = Journal.append
    interrupted = False

    def append(
        journal: Journal,
        current: Phase,
        evidence: dict[str, str] | None = None,
    ):
        nonlocal interrupted
        if current is phase and not interrupted:
            interrupted = True
            if after_durable_append:
                original(journal, current, evidence)
            raise RuntimeError(f"injected {phase.value} interruption")
        return original(journal, current, evidence)

    monkeypatch.setattr(Journal, "append", append)


def test_disposable_switch_quarantines_real_wal_and_shm_before_commit(
    tmp_path: Path,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    live_connection = _wal_database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)
    observed: list[tuple[str, str]] = []

    try:
        result = _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
            lambda mode, release: observed.append((mode, release)),
        )

        sidecars = root / "backups" / run_id / "sidecars"
        assert (sidecars / "proxima.db-wal").is_file()
        assert (sidecars / "proxima.db-shm").is_file()
        assert result == "candidate_good"
        assert observed == [
            ("readonly", CANDIDATE),
            ("writable", CANDIDATE),
        ]
        assert _database_value(live) == "candidate"
        assert ReleaseLayout(root).pointer_release("active") == CANDIDATE
        assert ReleaseLayout(root).pointer_release("last-good") == CANDIDATE
        assert not fence.exists()
        assert adapter.autonomous_writers_paused is False
        assert adapter.calls[:3] == [
            "pause_autonomous_writers",
            "drain",
            "stop_and_verify",
        ]
        phases = [
            record.phase
            for record in _journal(root, run_id, intent).records()
        ]
        assert phases[-1] is Phase.COMPLETED
        assert Phase.WAL_CHECKPOINTED in phases
        assert Phase.SIDECARS_QUARANTINED in phases
        assert Phase.IMAGE_SEALED in phases
        assert Phase.WRITABLE_PROBED in phases
    finally:
        with contextlib.suppress(sqlite3.Error):
            live_connection.close()


@pytest.mark.parametrize(
    "phase",
    [
        Phase.DB_SWAPPED,
        Phase.RELEASE_SWITCHED,
        Phase.LAST_GOOD_COMMITTED,
    ],
)
def test_unacknowledged_precommit_append_restores_state_and_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: Phase,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)
    _interrupt_append_once(
        monkeypatch,
        phase,
        after_durable_append=False,
    )

    with pytest.raises(RuntimeError, match="safe_update_breaker_latched"):
        _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
        )

    assert _database_value(live) == "previous"
    assert ReleaseLayout(root).pointer_release("active") == PREVIOUS
    assert ReleaseLayout(root).pointer_release("last-good") == PREVIOUS
    assert adapter.running_release == PREVIOUS
    assert adapter.autonomous_writers_paused is False
    assert not fence.exists()
    breaker = CircuitBreaker(root).status()
    assert breaker.failures == 1
    assert breaker.latched is True


@pytest.mark.parametrize(
    ("phase", "database_value", "release"),
    [
        (Phase.LAST_GOOD_COMMITTED, "previous", PREVIOUS),
        (Phase.COMPLETED, "candidate", CANDIDATE),
    ],
)
def test_unacknowledged_full_journal_write_latches_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: Phase,
    database_value: str,
    release: str,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)
    _interrupt_append_once(
        monkeypatch,
        phase,
        after_durable_append=True,
    )

    with pytest.raises(RuntimeError, match="safe_update_breaker_latched"):
        _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
        )

    assert _database_value(live) == database_value
    assert ReleaseLayout(root).pointer_release("active") == release
    assert ReleaseLayout(root).pointer_release("last-good") == release
    assert adapter.running_release == release
    assert not fence.exists()
    assert CircuitBreaker(root).status().latched is True
    assert _journal(root, run_id, intent).records()[-1].phase is phase
    recovery = controller.recovery_status(run_id, intent)
    assert recovery.safe is False
    assert recovery.action == "do_not_start_any_release"
    assert recovery.reason is not None


def test_rollback_verdict_precedes_physical_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class SimulatedCrash(BaseException):
        pass

    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)
    _interrupt_append_once(
        monkeypatch,
        Phase.LAST_GOOD_COMMITTED,
        after_durable_append=True,
    )
    original_begin = CircuitBreaker.begin_rollback

    def crash_after_verdict(
        breaker: CircuitBreaker,
        reason: str,
    ):
        original_begin(breaker, reason)
        raise SimulatedCrash

    monkeypatch.setattr(
        CircuitBreaker,
        "begin_rollback",
        crash_after_verdict,
    )

    with pytest.raises(SimulatedCrash):
        _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
        )

    assert _database_value(live) == "candidate"
    assert ReleaseLayout(root).pointer_release("active") == CANDIDATE
    assert ReleaseLayout(root).pointer_release("last-good") == CANDIDATE
    assert fence.is_file()
    breaker = CircuitBreaker(root).status()
    assert breaker.latched is True
    assert breaker.reason is not None
    assert breaker.reason.startswith("rollback_required:")
    recovery = controller.recovery_status(run_id, intent)
    assert recovery.safe is False
    assert recovery.action == "do_not_start_any_release"


def test_acknowledged_last_good_recovers_candidate_after_finalize_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)
    original_remove = controller_module.remove_fence
    attempts = 0

    def remove_once(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected finalize interruption")
        original_remove(path)

    monkeypatch.setattr(controller_module, "remove_fence", remove_once)

    assert (
        _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
        )
        == "candidate_good"
    )
    assert attempts == 2
    assert _database_value(live) == "candidate"
    assert ReleaseLayout(root).pointer_release("active") == CANDIDATE
    assert ReleaseLayout(root).pointer_release("last-good") == CANDIDATE
    assert adapter.running_release == CANDIDATE
    assert not fence.exists()
    assert CircuitBreaker(root).status().failures == 0
    assert _journal(root, run_id, intent).records()[-1].phase is Phase.COMPLETED


def test_partial_journal_tail_cannot_skip_database_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)
    original = Journal.append
    interrupted = False

    def append(
        journal: Journal,
        phase: Phase,
        evidence: dict[str, str] | None = None,
    ):
        nonlocal interrupted
        if phase is Phase.DB_SWAPPED and not interrupted:
            interrupted = True
            descriptor = os.open(journal.path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(descriptor, b'{"phase":"db_swapped"')
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            raise RuntimeError("injected partial journal append")
        return original(journal, phase, evidence)

    monkeypatch.setattr(Journal, "append", append)

    with pytest.raises(RuntimeError, match="safe_update_breaker_latched"):
        _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
        )

    assert _database_value(live) == "previous"
    assert ReleaseLayout(root).pointer_release("active") == PREVIOUS
    assert ReleaseLayout(root).pointer_release("last-good") == PREVIOUS
    assert adapter.running_release == PREVIOUS
    assert not fence.exists()
    assert CircuitBreaker(root).status().latched is True
    with pytest.raises(JournalIntegrityError, match="unterminated"):
        _journal(root, run_id, intent).records()


def test_disposable_probe_failure_restores_previous_state(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    adapter = DisposableServiceAdapter(PREVIOUS)

    def probe(mode: str, _release: str) -> None:
        if mode == "writable":
            raise RuntimeError("injected writable probe failure")

    with pytest.raises(RuntimeError, match="injected writable"):
        _promote(
            controller,
            run_id,
            intent,
            adapter,
            fence,
            live,
            staged,
            probe,
        )

    assert _database_value(live) == "previous"
    assert ReleaseLayout(root).pointer_release("active") == PREVIOUS
    assert ReleaseLayout(root).pointer_release("last-good") == PREVIOUS
    assert not fence.exists()
    assert adapter.running_release == PREVIOUS


def test_fixture_promotion_holds_single_flight_lock(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    holder = SafeUpdateController(root)
    assert holder.lock.acquire("f" * 32).acquired
    try:
        with pytest.raises(RuntimeError, match="safe_update_in_progress"):
            _promote(
                controller,
                run_id,
                intent,
                DisposableServiceAdapter(PREVIOUS),
                fence,
                live,
                staged,
            )
    finally:
        holder.lock.release()

    assert _journal(root, run_id, intent).records()[-1].phase is Phase.CANDIDATE_STAGED
    assert _database_value(live) == "previous"
    assert not fence.exists()


def test_fixture_promotion_rejects_hostile_run_id_before_path_use(
    tmp_path: Path,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller = SafeUpdateController(root)

    with pytest.raises(ValueError, match="invalid journal run id"):
        controller.promote_disposable_fixture(
            "../escaped",
            {},
            adapter=DisposableServiceAdapter(PREVIOUS),
            fence_path=root / "status" / "fence.json",
            live_database=root / "data" / "proxima.db",
            staged_database=root / "candidate" / "staged.db",
            previous_release_id=PREVIOUS,
            candidate_release_id=CANDIDATE,
            probe=lambda _mode, _release: None,
        )

    assert not (tmp_path / "escaped").exists()


def test_fixture_promotion_requires_initialized_disjoint_role_paths(
    tmp_path: Path,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    _fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")

    with pytest.raises(RuntimeError, match="outside its role root"):
        _promote(
            controller,
            run_id,
            intent,
            DisposableServiceAdapter(PREVIOUS),
            live,
            live,
            staged,
        )

    uninitialized = tmp_path / "uninitialized"
    uninitialized.mkdir()
    plain = SafeUpdateController(uninitialized)
    with pytest.raises(RuntimeError, match="initialized disposable fixture"):
        plain.promote_disposable_fixture(
            run_id,
            intent,
            adapter=DisposableServiceAdapter(PREVIOUS),
            fence_path=uninitialized / "status" / "fence.json",
            live_database=uninitialized / "data" / "proxima.db",
            staged_database=uninitialized / "candidate" / "staged.db",
            previous_release_id=PREVIOUS,
            candidate_release_id=CANDIDATE,
            probe=lambda _mode, _release: None,
        )


def test_orphan_breaker_update_latches_before_promotion(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    (root / ".breaker.json.tmp").write_text(
        '{"failures":1,"latched":false,"reason":null}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="safe_update_breaker_latched"):
        _promote(
            controller,
            run_id,
            intent,
            DisposableServiceAdapter(PREVIOUS),
            fence,
            live,
            staged,
        )

    assert CircuitBreaker(root).status().reason == "breaker_update_interrupted"
    assert _database_value(live) == "previous"
    assert not fence.exists()


def test_failed_breaker_payload_write_remains_latched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "controller"
    root.mkdir()
    controller, run_id, intent = _staged_run(root)
    fence, live, staged = _fixture_paths(root)
    _database(live, "previous")
    _database(staged, "candidate")
    original_write_all = circuit_breaker_module.write_all

    def fail_payload(descriptor: int, value: bytes) -> None:
        if value.startswith(b"{"):
            raise OSError("injected breaker payload failure")
        original_write_all(descriptor, value)

    monkeypatch.setattr(circuit_breaker_module, "write_all", fail_payload)

    with pytest.raises(OSError, match="injected breaker payload failure"):
        CircuitBreaker(root).record_failure("injected", rollback_failed=True)

    breaker = CircuitBreaker(root).status()
    assert breaker.latched is True
    assert breaker.reason == "breaker_update_interrupted"
    with pytest.raises(RuntimeError, match="safe_update_breaker_latched"):
        _promote(
            controller,
            run_id,
            intent,
            DisposableServiceAdapter(PREVIOUS),
            fence,
            live,
            staged,
        )
    assert _database_value(live) == "previous"
    assert not fence.exists()


def test_fence_blocks_mutating_http_terminal_and_dynamic_database_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "proxima.db"
    setup = connect(database)
    init_db(setup)
    run_migrations(setup, database)
    setup.close()
    fence = tmp_path / "status" / "fence.json"
    config = {
        "database_path": str(database),
        "workspace_root": str(tmp_path / "workspace"),
        "start_worker": False,
        "safe_update_fence_path": str(fence),
    }
    _provision_ingress(fence)
    app = create_app(config)

    with TestClient(app) as client:
        login = client.post("/auth/auto")
        assert login.status_code == 200
        token = login.json()["token"]
        cached_insert = "INSERT INTO fence_cache(value) VALUES (?)"
        app.state.db.execute("CREATE TABLE fence_cache(value TEXT NOT NULL)")
        app.state.db.execute(cached_insert, ("before",))
        app.state.db.execute("DELETE FROM profiles")
        wiki_root = (
            Path(config["workspace_root"])
            / "users"
            / "owner"
            / "wiki"
        )
        assert not wiki_root.exists()
        sessions_before = app.state.db.execute(
            "SELECT COUNT(*) FROM auth_sessions"
        ).fetchone()[0]
        write_fence(fence, "a" * 32, "write_fenced")

        def unexpected_shim(_path_env: str | None = None):
            raise AssertionError("maintenance readiness attempted shim creation")

        monkeypatch.setattr(
            "proxima_api.runners.ensure_python_compat_shim",
            unexpected_shim,
        )

        assert client.post("/api/update/check").status_code == 423
        assert client.post("/auth/auto").status_code == 423
        assert client.post("/auth/resume").status_code == 200
        assert client.get("/api/setup/status").status_code == 200
        assert client.get("/api/profiles").json() == {"profiles": []}
        assert client.get("/api/wiki/all").json() == {"notes": []}
        assert not wiki_root.exists()
        assert client.get("/api/appview/missing/").status_code == 423
        assert (
            app.state.db.execute(
                "SELECT COUNT(*) FROM auth_sessions"
            ).fetchone()[0]
            == sessions_before
        )
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                f"/api/ws/terminal?token={token}"
            ):
                pass
        assert rejected.value.code == 4423
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            app.state.db.execute("CREATE TABLE forbidden(value TEXT)")
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            app.state.db.execute(cached_insert, ("after",))
        assert app.state.db.execute(
            "SELECT COUNT(*) FROM fence_cache"
        ).fetchone()[0] == 1

    maintenance_app = create_app(
        {
            **config,
            "safe_update_maintenance_mode": True,
        }
    )
    with TestClient(maintenance_app) as client:
        response = client.post(
            "/auth/resume",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert client.get("/api/health").json()["maintenance_mode"] == "readonly"
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        maintenance_app.state.db.execute("CREATE TABLE forbidden(value TEXT)")


def test_normal_wiki_read_seeds_index_without_dynamic_database_fence(
    tmp_path: Path,
):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "start_worker": False,
        }
    )
    assert app.state.maintenance.database_write_check() is None

    with TestClient(app) as client:
        assert client.post("/auth/auto").status_code == 200
        response = client.get("/api/wiki/all")

    wiki_index = tmp_path / "workspace" / "users" / "owner" / "wiki" / "index.md"
    assert response.status_code == 200
    assert response.json()["notes"] == [
        {
            "path": "index.md",
            "content": "# owner's wiki\n\nYour personal notes.\n",
        }
    ]
    assert wiki_index.is_file()


def test_database_fence_callback_skips_read_operations(tmp_path: Path):
    database = tmp_path / "proxima.db"
    checks = 0

    def fenced() -> bool:
        nonlocal checks
        checks += 1
        return False

    connection = connect(database, writes_fenced=fenced)
    try:
        checks = 0
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert checks == 0
        connection.execute("CREATE TABLE callback_probe(value TEXT)")
        assert checks > 0
    finally:
        connection.close()


def test_application_uses_controller_owned_read_only_ingress_lock(
    tmp_path: Path,
):
    fence = tmp_path / "status" / "fence.json"
    lock = prepare_ingress_lock(fence)
    lock.chmod(0o444)
    lock.parent.chmod(0o555)
    try:
        app = create_app(
            {
                "database_path": str(tmp_path / "proxima.db"),
                "workspace_root": str(tmp_path / "workspace"),
                "start_worker": False,
                "safe_update_fence_path": str(fence),
            }
        )
        with TestClient(app) as client:
            assert client.post("/auth/auto").status_code == 200
            assert client.get("/api/maintenance").json() == {
                "active": False,
                "phase": None,
            }
        assert stat.S_IMODE(lock.stat().st_mode) == 0o444
    finally:
        lock.parent.chmod(0o755)
        lock.chmod(0o644)


def test_admission_is_shared_across_boundary_instances(tmp_path: Path):
    fence = tmp_path / "status" / "fence.json"
    _provision_ingress(fence)
    config = {"safe_update_fence_path": str(fence)}
    admission = MaintenanceBoundary(config).acquire()
    assert admission.acquired
    fence_errors: list[BaseException] = []

    def activate_fence() -> None:
        try:
            write_fence(fence, "d" * 32, "write_fenced")
        except BaseException as exc:
            fence_errors.append(exc)

    fence_thread = threading.Thread(target=activate_fence)
    fence_thread.start()
    try:
        _wait_for_path(ingress_pending_path(fence))
        other_boundary = MaintenanceBoundary(config)
        write_check = other_boundary.database_write_check()
        assert write_check is not None
        assert write_check() is False
        assert fence_thread.is_alive()
    finally:
        admission.release()

    fence_thread.join(timeout=5)
    assert not fence_thread.is_alive()
    assert fence_errors == []
    assert write_check() is True


def test_startup_admission_drains_before_fence_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fence = tmp_path / "status" / "fence.json"
    _provision_ingress(fence)
    init_started = threading.Event()
    finish_init = threading.Event()
    original_init_db = main_module.init_db

    def blocking_init(*args, **kwargs):
        init_started.set()
        finish_init.wait(timeout=5)
        return original_init_db(*args, **kwargs)

    monkeypatch.setattr(main_module, "init_db", blocking_init)
    apps: list[object] = []
    startup_errors: list[BaseException] = []

    def start_app() -> None:
        try:
            apps.append(
                create_app(
                    {
                        "database_path": str(tmp_path / "proxima.db"),
                        "workspace_root": str(tmp_path / "workspace"),
                        "start_worker": False,
                        "safe_update_fence_path": str(fence),
                    }
                )
            )
        except BaseException as exc:
            startup_errors.append(exc)

    startup_thread = threading.Thread(target=start_app)
    startup_thread.start()
    assert init_started.wait(timeout=2)
    fence_errors: list[BaseException] = []

    def activate_fence() -> None:
        try:
            write_fence(fence, "e" * 32, "write_fenced")
        except BaseException as exc:
            fence_errors.append(exc)

    fence_thread = threading.Thread(target=activate_fence)
    fence_thread.start()
    _wait_for_path(ingress_pending_path(fence))
    assert fence_thread.is_alive()
    assert not fence.exists()
    finish_init.set()
    startup_thread.join(timeout=5)
    assert not startup_thread.is_alive()
    assert startup_errors == []
    assert len(apps) == 1
    assert fence_thread.is_alive()

    with TestClient(apps[0]):
        _wait_for_path(fence)

    fence_thread.join(timeout=5)
    assert not fence_thread.is_alive()
    assert fence_errors == []


def test_admitted_wiki_write_finishes_audit_before_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fence = tmp_path / "status" / "fence.json"
    _provision_ingress(fence)
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "start_worker": False,
            "safe_update_fence_path": str(fence),
        }
    )
    file_written = threading.Event()
    finish_write = threading.Event()
    from proxima_api.routes import wiki as wiki_routes

    original_write_file = wiki_routes.fsapi.write_file

    def blocking_write_file(
        root: Path,
        path: str,
        content: str,
    ) -> None:
        original_write_file(root, path, content)
        file_written.set()
        finish_write.wait(timeout=5)

    monkeypatch.setattr(
        wiki_routes.fsapi,
        "write_file",
        blocking_write_file,
    )

    with TestClient(app) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        responses: list[object] = []
        request_errors: list[BaseException] = []

        def write_wiki() -> None:
            try:
                responses.append(
                    client.put(
                        "/api/wiki/file",
                        params={"path": "atomic.md"},
                        json={"content": "complete"},
                        headers=auth,
                    )
                )
            except BaseException as exc:
                request_errors.append(exc)

        request_thread = threading.Thread(target=write_wiki)
        request_thread.start()
        assert file_written.wait(timeout=2)
        fence_errors: list[BaseException] = []

        def activate_fence() -> None:
            try:
                write_fence(fence, "f" * 32, "write_fenced")
            except BaseException as exc:
                fence_errors.append(exc)

        fence_thread = threading.Thread(target=activate_fence)
        fence_thread.start()
        _wait_for_path(ingress_pending_path(fence))
        assert fence_thread.is_alive()
        finish_write.set()
        request_thread.join(timeout=5)
        fence_thread.join(timeout=5)

        assert not request_thread.is_alive()
        assert not fence_thread.is_alive()
        assert request_errors == []
        assert fence_errors == []
        assert responses[0].status_code == 200
        assert (
            tmp_path
            / "workspace"
            / "users"
            / "owner"
            / "wiki"
            / "atomic.md"
        ).read_text(encoding="utf-8") == "complete"
        audit = app.state.db.execute(
            "SELECT action FROM audit_log "
            "WHERE action = 'wiki.write' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        assert audit["action"] == "wiki.write"


def test_fenced_provider_reads_return_inert_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fence = tmp_path / "status" / "fence.json"
    _provision_ingress(fence)
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "start_worker": False,
            "safe_update_fence_path": str(fence),
        }
    )

    with TestClient(app) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/projects",
            headers=auth,
            json={"slug": "demo", "name": "Demo"},
        ).status_code == 201
        assert client.put(
            "/api/settings/image-gen",
            headers=auth,
            json={"provider": "higgsfield"},
        ).status_code == 200
        write_fence(fence, "1" * 32, "write_fenced")

        def unexpected_readiness(*_args, **_kwargs):
            raise AssertionError("fenced read launched provider readiness")

        monkeypatch.setattr(
            "proxima_api.image_providers.codex_ready",
            unexpected_readiness,
        )
        monkeypatch.setattr(
            "proxima_api.image_providers.xai_oauth_ready",
            unexpected_readiness,
        )
        monkeypatch.setattr(
            "proxima_api.higgsfield.status",
            unexpected_readiness,
        )

        image_settings = client.get(
            "/api/settings/image-gen",
            headers=auth,
        )
        higgsfield_settings = client.get(
            "/api/settings/higgsfield",
            headers=auth,
        )
        image_models = client.get(
            "/api/projects/demo/design/image-models",
            headers=auth,
        )

    assert image_settings.status_code == 200
    assert image_settings.json()["higgsfieldReady"]["ready"] is False
    assert higgsfield_settings.status_code == 200
    assert higgsfield_settings.json()["status"]["ready"] is False
    assert image_models.status_code == 200
    assert image_models.json()["configured"] is False


def test_appview_request_drains_before_fence_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "proxima.db"
    fence = tmp_path / "status" / "fence.json"
    _provision_ingress(fence)
    app = create_app(
        {
            "database_path": str(database),
            "workspace_root": str(tmp_path / "workspace"),
            "start_worker": False,
            "safe_update_fence_path": str(fence),
        }
    )
    outbound_started = threading.Event()
    finish_outbound = threading.Event()

    class UpstreamResponse:
        status_code = 200
        content = b"ok"
        headers: dict[str, str] = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def request(self, *_args, **_kwargs):
            outbound_started.set()
            await asyncio.get_running_loop().run_in_executor(
                None,
                finish_outbound.wait,
                5,
            )
            return UpstreamResponse()

    monkeypatch.setattr(
        "proxima_api.routes.files.httpx.AsyncClient",
        FakeAsyncClient,
    )

    with TestClient(app) as client:
        token = client.post("/auth/auto").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/projects",
            headers=auth,
            json={"slug": "demo", "name": "Demo"},
        ).status_code == 201
        monkeypatch.setattr(app.state.app_manager, "port", lambda _slug: 4567)
        responses: list[object] = []
        request_errors: list[BaseException] = []

        def request_appview() -> None:
            try:
                responses.append(
                    client.get("/api/appview/demo/", headers=auth)
                )
            except BaseException as exc:
                request_errors.append(exc)

        request_thread = threading.Thread(target=request_appview)
        request_thread.start()
        assert outbound_started.wait(timeout=2)
        fence_errors: list[BaseException] = []

        def activate_fence() -> None:
            try:
                write_fence(fence, "c" * 32, "write_fenced")
            except BaseException as exc:
                fence_errors.append(exc)

        fence_thread = threading.Thread(target=activate_fence)
        fence_thread.start()
        _wait_for_path(ingress_pending_path(fence))
        assert fence_thread.is_alive()
        assert not fence.exists()
        finish_outbound.set()
        request_thread.join(timeout=5)
        fence_thread.join(timeout=5)

        assert not request_thread.is_alive()
        assert not fence_thread.is_alive()
        assert request_errors == []
        assert fence_errors == []
        assert len(responses) == 1
        assert responses[0].status_code == 200
        assert responses[0].text == "ok"
        assert fence.is_file()
        assert client.get("/api/appview/demo/", headers=auth).status_code == 423


def test_established_terminal_drains_session_before_fence_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "proxima.db"
    setup = connect(database)
    init_db(setup)
    run_migrations(setup, database)
    setup.close()
    fence = tmp_path / "status" / "fence.json"
    _provision_ingress(fence)
    started = threading.Event()
    closed = threading.Event()
    input_started = threading.Event()
    close_started = threading.Event()
    finish_close = threading.Event()
    writes: list[bytes] = []

    class FakeTerminal:
        def __init__(self, _cwd: str) -> None:
            pass

        def start(self) -> None:
            started.set()

        def read(self, _size: int) -> bytes:
            closed.wait(timeout=5)
            return b""

        def write(self, value: bytes) -> None:
            writes.append(value)
            input_started.set()

        def resize(self, _rows: int, _cols: int) -> None:
            writes.append(b"resize")

        def close(self) -> bool:
            close_started.set()
            finish_close.wait(timeout=5)
            closed.set()
            return True

    monkeypatch.setattr("proxima_api.routes.chat.TerminalSession", FakeTerminal)
    app = create_app(
        {
            "database_path": str(database),
            "workspace_root": str(tmp_path / "workspace"),
            "start_worker": False,
            "safe_update_fence_path": str(fence),
        }
    )

    with TestClient(app) as client:
        token = client.post("/auth/auto").json()["token"]
        with client.websocket_connect(
            f"/api/ws/terminal?token={token}"
        ) as websocket:
            assert started.wait(timeout=2)
            websocket.send_bytes(b"admitted-input")
            assert input_started.wait(timeout=2)
            fence_errors: list[BaseException] = []

            def activate_fence() -> None:
                try:
                    write_fence(fence, "b" * 32, "write_fenced")
                except BaseException as exc:
                    fence_errors.append(exc)

            fence_thread = threading.Thread(target=activate_fence)
            fence_thread.start()
            try:
                _wait_for_path(ingress_pending_path(fence))
                assert close_started.wait(timeout=2)
                assert fence_thread.is_alive()
                assert not fence.exists()
            finally:
                finish_close.set()
            with pytest.raises(WebSocketDisconnect) as rejected:
                websocket.receive_bytes()
            assert rejected.value.code == 4423
            fence_thread.join(timeout=5)
            assert not fence_thread.is_alive()
            assert fence_errors == []
            assert fence.is_file()

    assert writes == [b"admitted-input"]

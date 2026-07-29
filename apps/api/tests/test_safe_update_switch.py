from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.safe_updater.circuit_breaker import CircuitBreaker
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.journal import Journal
from apps.safe_updater.layout import ReleaseLayout
from apps.safe_updater.service_adapter import DisposableServiceAdapter
from apps.safe_updater.state_machine import Phase
from apps.safe_updater.write_fence import write as write_fence
from proxima_api.db import connect, init_db
from proxima_api.main import create_app
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
def test_precommit_interruption_restores_database_and_both_pointers(
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

    with pytest.raises(RuntimeError, match=f"injected {phase.value}"):
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
    assert CircuitBreaker(root).status().failures == 1


@pytest.mark.parametrize(
    "phase",
    [Phase.LAST_GOOD_COMMITTED, Phase.COMPLETED],
)
def test_durable_commit_interruption_resumes_candidate(
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
        after_durable_append=True,
    )

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

    assert _database_value(live) == "candidate"
    assert ReleaseLayout(root).pointer_release("active") == CANDIDATE
    assert ReleaseLayout(root).pointer_release("last-good") == CANDIDATE
    assert adapter.running_release == CANDIDATE
    assert not fence.exists()
    assert _journal(root, run_id, intent).records()[-1].phase is Phase.COMPLETED


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


def test_fence_blocks_mutating_http_terminal_and_dynamic_database_writes(
    tmp_path: Path,
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
    app = create_app(config)

    with TestClient(app) as client:
        login = client.post("/auth/auto")
        assert login.status_code == 200
        token = login.json()["token"]
        sessions_before = app.state.db.execute(
            "SELECT COUNT(*) FROM auth_sessions"
        ).fetchone()[0]
        write_fence(fence, "a" * 32, "write_fenced")

        assert client.post("/api/update/check").status_code == 423
        assert client.post("/auth/auto").status_code == 423
        assert client.post("/auth/resume").status_code == 200
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

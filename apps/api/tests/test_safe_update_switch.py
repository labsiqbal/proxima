from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
        connection.execute("INSERT INTO fixture_values(value) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _staged_run(root: Path) -> tuple[SafeUpdateController, str, dict[str, str]]:
    controller = SafeUpdateController(root)
    intent = {"base_commit": "a" * 40, "candidate_commit": "c" * 40}
    submitted = controller.submit(intent)
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    journal = Journal(root / "journal" / f"{submitted.run_id}.jsonl", digest)
    journal.append(Phase.CANDIDATE_STAGED, {"candidate_evidence": "e" * 64})
    layout = ReleaseLayout(root)
    layout.release_dir(PREVIOUS).mkdir(parents=True)
    layout.release_dir(CANDIDATE).mkdir(parents=True)
    layout.set_pointer("active", PREVIOUS)
    layout.set_pointer("last-good", PREVIOUS)
    return controller, submitted.run_id, intent


def test_disposable_switch_commits_only_after_readonly_and_writable_proofs(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    live = root / "data" / "proxima.db"
    live.parent.mkdir()
    _database(live, "previous")
    staged = root / "candidate" / "staged.db"
    staged.parent.mkdir()
    _database(staged, "candidate")
    controller, run_id, intent = _staged_run(root)
    adapter = DisposableServiceAdapter(PREVIOUS)
    observed: list[tuple[str, str]] = []

    result = controller.promote_disposable_fixture(
        run_id,
        intent,
        adapter=adapter,
        fence_path=root / "status" / "fence.json",
        live_database=live,
        staged_database=staged,
        previous_release_id=PREVIOUS,
        candidate_release_id=CANDIDATE,
        probe=lambda mode, release: observed.append((mode, release)),
    )

    assert result == "candidate_good"
    assert observed == [("readonly", CANDIDATE), ("writable", CANDIDATE)]
    assert ReleaseLayout(root).pointer_release("active") == CANDIDATE
    assert ReleaseLayout(root).pointer_release("last-good") == CANDIDATE
    assert not (root / "status" / "fence.json").exists()
    assert adapter.autonomous_writers_paused is False
    phases = [record.phase for record in Journal(root / "journal" / f"{run_id}.jsonl", hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()).records()]
    assert phases[-1] is Phase.COMPLETED
    assert Phase.WAL_CHECKPOINTED in phases
    assert Phase.IMAGE_SEALED in phases
    assert Phase.WRITABLE_PROBED in phases


def test_disposable_probe_failure_restores_previous_database_and_release(tmp_path: Path):
    root = tmp_path / "controller"
    root.mkdir()
    live = root / "data" / "proxima.db"
    live.parent.mkdir()
    _database(live, "previous")
    staged = root / "candidate" / "staged.db"
    staged.parent.mkdir()
    _database(staged, "candidate")
    controller, run_id, intent = _staged_run(root)
    adapter = DisposableServiceAdapter(PREVIOUS)

    def probe(mode: str, _release: str) -> None:
        if mode == "writable":
            raise RuntimeError("injected writable probe failure")

    with pytest.raises(RuntimeError, match="injected writable"):
        controller.promote_disposable_fixture(
            run_id,
            intent,
            adapter=adapter,
            fence_path=root / "status" / "fence.json",
            live_database=live,
            staged_database=staged,
            previous_release_id=PREVIOUS,
            candidate_release_id=CANDIDATE,
            probe=probe,
        )

    assert ReleaseLayout(root).pointer_release("active") == PREVIOUS
    assert not (root / "status" / "fence.json").exists()
    restored = sqlite3.connect(live).execute("SELECT value FROM fixture_values").fetchone()[0]
    assert restored == "previous"
    assert adapter.running_release == PREVIOUS


def test_write_fence_returns_423_and_maintenance_connections_are_authorizer_denied(tmp_path: Path):
    database = tmp_path / "proxima.db"
    setup = connect(database)
    init_db(setup)
    run_migrations(setup, database)
    setup.close()
    fence = tmp_path / "status" / "fence.json"
    write_fence(fence, "a" * 32, "write_fenced")

    app = create_app({
        "database_path": str(database),
        "workspace_root": str(tmp_path / "workspace"),
        "start_worker": False,
        "safe_update_fence_path": str(fence),
        "safe_update_maintenance_mode": True,
    })
    with TestClient(app) as client:
        response = client.post("/api/update/check")
        assert response.status_code == 423
        assert client.get("/api/health").json()["maintenance_mode"] == "readonly"
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        app.state.db.execute("CREATE TABLE forbidden(value TEXT)")

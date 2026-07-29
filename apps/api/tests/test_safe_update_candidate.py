from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from apps.safe_updater.build import BuildResult
from apps.safe_updater.candidate import CandidateGate, CandidateGateError
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.manifest import local_provenance
from apps.safe_updater.trusted_probes import TrustedProbeBundle, _tree_digest
from proxima_api.db import connect, init_db
from proxima_api.main import create_app
from proxima_api.migrations import run_migrations


class _Builder:
    def build(self, _release, *, cache_root):
        assert cache_root.is_dir()
        return BuildResult({"fixed": b"ok"}, {})


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "apps/api").mkdir(parents=True)
    (source / "apps/web/dist/assets").mkdir(parents=True)
    (source / "apps/api/uv.lock").write_text("lock", encoding="utf-8")
    (source / "apps/web/package-lock.json").write_text("{}", encoding="utf-8")
    (source / "apps/web/dist/assets/main.js").write_text("fixture", encoding="utf-8")
    return source


def _live_database(path: Path) -> None:
    conn = connect(str(path))
    init_db(conn, [], lambda _u, _s: path.parent / "home")
    run_migrations(conn, str(path))
    conn.close()


def test_candidate_gate_clones_live_data_without_touching_it_and_records_immutable_evidence(tmp_path: Path):
    source = _source(tmp_path)
    live = tmp_path / "live.db"
    _live_database(live)
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    probes = tmp_path / "probes"
    probes.mkdir()
    (probes / "browser.json").write_text("{}", encoding="utf-8")
    trusted = TrustedProbeBundle.load(probes, _tree_digest(probes))
    cache = tmp_path / "cache"
    cache.mkdir()
    provenance = local_provenance("task", "a" * 40, "b" * 40, "local", source)
    controller = SafeUpdateController(tmp_path / "controller")
    accepted = controller.submit({"candidate_commit": "b" * 40})
    gate = CandidateGate(tmp_path / "controller", builder=_Builder(), source_is_clean=lambda _path, _commit: True)
    result = controller.qualify_candidate(
        accepted.run_id, {"candidate_commit": "b" * 40}, gate,
        candidate_source=source, local_provenance=provenance, live_database=live,
        cache_root=cache, migrate_clone=lambda _clone: None, trusted_probes=trusted,
    )
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
    assert result.release.is_dir()
    assert result.fixture.is_file()
    assert (result.evidence.path / "index.json").is_file()
    assert controller.recovery_status(accepted.run_id, {"candidate_commit": "b" * 40}).safe


def test_candidate_gate_refuses_a_missing_trusted_probe_bundle(tmp_path: Path):
    source = _source(tmp_path)
    live = tmp_path / "live.db"
    _live_database(live)
    cache = tmp_path / "cache"
    cache.mkdir()
    provenance = local_provenance("task", "a" * 40, "b" * 40, "local", source)
    controller = SafeUpdateController(tmp_path / "controller")
    accepted = controller.submit({"candidate_commit": "b" * 40})
    with __import__("pytest").raises(CandidateGateError, match="trusted probe"):
        controller.qualify_candidate(
            accepted.run_id, {"candidate_commit": "b" * 40}, CandidateGate(tmp_path / "controller", builder=_Builder(), source_is_clean=lambda _path, _commit: True),
            candidate_source=source, local_provenance=provenance, live_database=live,
            cache_root=cache, migrate_clone=lambda _clone: None,
        )


def test_candidate_mode_reports_identity_without_running_migrations(tmp_path: Path):
    database = tmp_path / "candidate.db"
    _live_database(database)
    app = create_app({
        "database_path": str(database), "workspace_root": str(tmp_path / "workspace"),
        "hermes_profiles_root": str(tmp_path / "runner"), "start_worker": False,
        "link_roots": ["/should-not-be-visible"],
        "candidate_mode": True, "candidate_release_id": f"sha256-{'a' * 40}-{'b' * 12}",
        "candidate_commit": "a" * 40, "candidate_asset_manifest_digest": "c" * 64,
    })
    with TestClient(app) as client:
        payload = client.get("/api/health").json()
    assert payload["release_id"] == f"sha256-{'a' * 40}-{'b' * 12}"
    assert payload["commit"] == "a" * 40
    assert payload["asset_manifest_digest"] == "c" * 64
    assert app.state.config["link_roots"] == [str(tmp_path / "workspace")]

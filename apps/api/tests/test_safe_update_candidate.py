from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import apps.safe_updater.candidate as candidate_module
from apps.safe_updater.build import BUILD_MANIFEST, BuildResult, OfflineBuilder
from apps.safe_updater.candidate import CandidateGate, CandidateGateError
from apps.safe_updater.candidate_data import (
    CandidateDataError,
    MigrationReport,
    validate_migrated_clone,
)
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.manifest import local_provenance
from apps.safe_updater.probe_runner import TrustedProbeResult
from apps.safe_updater.sandbox import CandidateSandbox
from apps.safe_updater.trusted_probes import TrustedProbeBundle, _tree_digest
from proxima_api.db import connect, init_db
from proxima_api.main import create_app
from proxima_api.migrations import MIGRATIONS, run_migrations


EXPECTED_MIGRATION_VERSION = max(entry[0] for entry in MIGRATIONS)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "apps/api").mkdir(parents=True)
    (source / "apps/web/dist/assets").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "apps/api/uv.lock").write_text("lock", encoding="utf-8")
    (source / "apps/web/package-lock.json").write_text("{}", encoding="utf-8")
    (source / "apps/web/dist/index.html").write_text(
        '<html><head><title>Proxima</title></head><body><div id="root"></div>'
        '<script src="/assets/main.js"></script></body></html>',
        encoding="utf-8",
    )
    (source / "apps/web/dist/assets/main.js").write_text("fixture", encoding="utf-8")
    (source / "VERSION").write_text("1.0.2\n", encoding="utf-8")
    return source


def _live_database(path: Path) -> None:
    conn = connect(str(path))
    init_db(conn, [], lambda _u, _s: path.parent / "home")
    run_migrations(conn, str(path))
    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES (?, ?)",
        ("image_gen", '{"apiKey":"live-secret"}'),
    )
    conn.execute(
        "INSERT INTO workflows(name, description, category, status, steps) "
        "VALUES (?, ?, ?, ?, ?)",
        ("private workflow", "", "other", "active", "[]"),
    )
    conn.execute(
        "INSERT INTO audit_log(action, target_type, target_id, metadata) "
        "VALUES (?, ?, ?, ?)",
        ("private", "settings", "image_gen", '{"path":"/live"}'),
    )
    conn.execute(
        "INSERT INTO events(seq, type, payload) VALUES (?, ?, ?)",
        (1, "private", '{"secret":true}'),
    )
    conn.commit()
    conn.close()


def _trusted(tmp_path: Path) -> TrustedProbeBundle:
    probes = tmp_path / "probes"
    probes.mkdir()
    (probes / "probe.py").write_text("trusted", encoding="utf-8")
    (probes / "browser-scenarios.json").write_text(
        '{"version":1,"scenarios":["shell"]}',
        encoding="utf-8",
    )
    return TrustedProbeBundle.load(probes, _tree_digest(probes))


def test_shipped_trusted_probe_bundle_is_executable_and_has_browser_scenarios():
    root = Path(__file__).resolve().parents[3] / "trusted-probes" / "safe-update"
    compile(
        (root / "probe.py").read_text(encoding="utf-8"),
        str(root / "probe.py"),
        "exec",
    )
    scenarios = json.loads(
        (root / "browser-scenarios.json").read_text(encoding="utf-8")
    )
    assert scenarios["version"] == 1
    assert scenarios["scenarios"]


def test_offline_manifest_uses_only_the_candidate_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    release = _source(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    calls: list[tuple[tuple[str, ...], Path]] = []

    class Sandbox:
        def __init__(self):
            self.runner_home = home
            self.release = release

        def run(self, argv, *, cwd, **_kwargs):
            calls.append((tuple(argv), cwd))
            return subprocess.CompletedProcess(argv, 0, b"ok")

    monkeypatch.setattr(OfflineBuilder, "_tools", staticmethod(lambda: {}))
    result = OfflineBuilder().build(release, cache_root=cache, sandbox=Sandbox())
    assert [call[0] for call in calls] == [step.argv for step in BUILD_MANIFEST]
    assert result.logs == {step.name: b"ok" for step in BUILD_MANIFEST}


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="bubblewrap sandbox contract",
)
def test_candidate_sandbox_hides_host_paths_drops_uid_and_denies_egress(tmp_path: Path):
    root = tmp_path / "sandbox"
    release = root / "release"
    workspace = root / "workspace"
    runner_home = root / "runner"
    release.mkdir(parents=True)
    workspace.mkdir()
    runner_home.mkdir()
    protected = tmp_path / "live-secret"
    protected.write_text("secret", encoding="utf-8")
    sandbox = CandidateSandbox(
        root,
        release,
        root / "candidate.db",
        workspace,
        runner_home,
        18765,
    )
    script = (
        "import os,socket,sys;"
        "sock=socket.socket();sock.settimeout(.2);"
        "print(os.getuid());"
        "print(os.path.exists(sys.argv[1]));"
        "print(sock.connect_ex(('1.1.1.1',53)))"
    )
    result = sandbox.run(
        ("/usr/bin/python3", "-c", script, str(protected)),
        cwd=release,
        writable_paths=(release, runner_home),
        timeout=10,
    )
    lines = (result.stdout or b"").decode().splitlines()
    assert result.returncode == 0
    assert lines[:2] == ["65534", "False"]
    assert int(lines[2]) != 0


def test_candidate_gate_builds_before_freezing_scrubs_fixture_and_revalidates_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = _source(tmp_path)
    live = tmp_path / "live.db"
    _live_database(live)
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "seed").write_text("offline", encoding="utf-8")
    provenance = local_provenance("task", "a" * 40, "b" * 40, "local", source)
    controller = SafeUpdateController(tmp_path / "controller")
    intent = {"candidate_commit": "b" * 40}
    accepted = controller.submit(intent)
    gate = CandidateGate(
        tmp_path / "controller",
        expected_migration_version=EXPECTED_MIGRATION_VERSION,
    )

    monkeypatch.setattr(
        CandidateGate,
        "_source_is_clean",
        staticmethod(lambda _source, _commit, _sandbox: True),
    )

    def build(release, *, cache_root, sandbox):
        assert sandbox.release == release
        assert cache_root.is_dir()
        (release / "post-build-output").write_text("built", encoding="utf-8")
        interpreter = release / "apps" / "api" / ".venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(Path("/usr/bin/python3"))
        return BuildResult({"fixed": b"ok"}, {"apps/api/uv.lock": "a" * 64})

    monkeypatch.setattr(gate.builder, "build", build)

    def migrate(clone, _sandbox, expected_version):
        report = validate_migrated_clone(clone, expected_version)
        return MigrationReport(report, b'{"applied":[]}')

    monkeypatch.setattr(candidate_module, "migrate_clone_in_sandbox", migrate)
    observed_probes: list[Path] = []

    def probe(*, sandbox, **_kwargs):
        observed_probes.append(sandbox.release)
        return TrustedProbeResult(
            b'{"ok":true,"results":{"browser":{"shell":"ok"}}}',
            {"browser": {"shell": "ok"}},
        )

    monkeypatch.setattr(gate.probes, "run", probe)
    result = controller.qualify_candidate(
        accepted.run_id,
        intent,
        gate,
        candidate_source=source,
        local_provenance=provenance,
        live_database=live,
        cache_root=cache,
        candidate_port=18765,
        trusted_probes=_trusted(tmp_path),
    )

    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
    assert observed_probes == [result.release]
    assert (result.release / "post-build-output").read_text(encoding="utf-8") == "built"
    interpreter = result.release / "apps" / "api" / ".venv" / "bin" / "python"
    assert interpreter.is_file() and not interpreter.is_symlink()
    assert interpreter.stat().st_mode & 0o111
    assert result.release.stat().st_mode & 0o222 == 0
    assert not (tmp_path / "controller" / "candidates" / accepted.run_id / "build").exists()
    fixture = sqlite3.connect(result.fixture)
    try:
        for table in ("app_settings", "workflows", "audit_log", "events"):
            assert fixture.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert fixture.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert fixture.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 1
        versions = [
            row[0]
            for row in fixture.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        assert versions == list(range(1, EXPECTED_MIGRATION_VERSION + 1))
    finally:
        fixture.close()
    assert result.evidence.path.stat().st_mode & 0o222 == 0
    assert result.evidence.path.parent.stat().st_mode & 0o222 == 0
    assert {
        "build-fixed.log",
        "migration.json",
        "probe-results.json",
        "trusted-probes.json",
    }.issubset(result.evidence.files)
    assert controller.recovery_status(accepted.run_id, intent).safe

    evidence_root = result.evidence.path.parent
    evidence_root.chmod(0o700)
    result.evidence.path.chmod(0o700)
    probe_evidence = result.evidence.path / "probe-results.json"
    probe_evidence.chmod(0o600)
    probe_evidence.write_text('{"replaced":true}', encoding="utf-8")
    probe_evidence.chmod(0o400)
    result.evidence.path.chmod(0o500)
    evidence_root.chmod(0o500)
    recovered = controller.recovery_status(accepted.run_id, intent)
    assert recovered.safe is False
    assert recovered.action == "do_not_start_any_release"


def test_migration_validation_rejects_a_noop_with_an_incomplete_ledger(tmp_path: Path):
    database = tmp_path / "candidate.db"
    _live_database(database)
    conn = sqlite3.connect(database)
    conn.execute(
        "DELETE FROM schema_migrations WHERE version = ?",
        (EXPECTED_MIGRATION_VERSION,),
    )
    conn.commit()
    conn.close()
    with pytest.raises(CandidateDataError, match="ledger is incomplete"):
        validate_migrated_clone(database, EXPECTED_MIGRATION_VERSION)


def test_candidate_gate_refuses_a_missing_trusted_probe_bundle(tmp_path: Path):
    source = _source(tmp_path)
    live = tmp_path / "live.db"
    _live_database(live)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "seed").write_text("offline", encoding="utf-8")
    provenance = local_provenance("task", "a" * 40, "b" * 40, "local", source)
    controller = SafeUpdateController(tmp_path / "controller")
    intent = {"candidate_commit": "b" * 40}
    accepted = controller.submit(intent)
    with pytest.raises(CandidateGateError, match="trusted probe"):
        controller.qualify_candidate(
            accepted.run_id,
            intent,
            CandidateGate(
                tmp_path / "controller",
                expected_migration_version=EXPECTED_MIGRATION_VERSION,
            ),
            candidate_source=source,
            local_provenance=provenance,
            live_database=live,
            cache_root=cache,
            candidate_port=18765,
        )


def test_candidate_mode_reports_identity_without_running_migrations(tmp_path: Path):
    database = tmp_path / "candidate.db"
    _live_database(database)
    app = create_app({
        "database_path": str(database),
        "workspace_root": str(tmp_path / "workspace"),
        "hermes_profiles_root": str(tmp_path / "runner"),
        "start_worker": False,
        "link_roots": ["/should-not-be-visible"],
        "candidate_mode": True,
        "candidate_release_id": f"sha256-{'a' * 40}-{'b' * 12}",
        "candidate_commit": "a" * 40,
        "candidate_asset_manifest_digest": "c" * 64,
    })
    with TestClient(app) as client:
        payload = client.get("/api/health").json()
    assert payload["release_id"] == f"sha256-{'a' * 40}-{'b' * 12}"
    assert payload["commit"] == "a" * 40
    assert payload["asset_manifest_digest"] == "c" * 64
    assert app.state.config["link_roots"] == [str(tmp_path / "workspace")]

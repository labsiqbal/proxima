from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import apps.safe_updater.candidate as candidate_module
from apps.safe_updater.build import BUILD_MANIFEST, BuildResult, OfflineBuilder
from apps.safe_updater.candidate import CandidateGate, CandidateGateError
from apps.safe_updater.candidate_data import (
    CandidateDataError,
    MigrationReport,
    migrate_clone_in_sandbox,
    validate_migrated_clone,
)
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.git_source import resolve_git_metadata
from apps.safe_updater.manifest import local_provenance, verify_local_provenance
from apps.safe_updater.probe_runner import (
    TRUSTED_BROWSER_ADDRESS_SPACE_BYTES,
    TRUSTED_BROWSER_PROCESS_LIMIT,
    CandidateIdentity,
    TrustedProbeResult,
    TrustedProbeRunner,
)
from apps.safe_updater.sandbox import CandidateSandbox, SandboxError
from apps.safe_updater.tree import copy_verified_source
from apps.safe_updater.trusted_probes import TrustedProbeBundle, _tree_digest
from proxima_api.db import connect, init_db
from proxima_api.main import create_app
from proxima_api.migrations import MIGRATIONS, run_migrations


EXPECTED_MIGRATION_VERSION = max(entry[0] for entry in MIGRATIONS)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "apps/api").mkdir(parents=True)
    (source / "apps/web").mkdir(parents=True)
    (source / "docs/reference").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "apps/api/uv.lock").write_text("lock", encoding="utf-8")
    (source / "apps/web/package-lock.json").write_text("{}", encoding="utf-8")
    (source / "docs/reference/api.md").write_text("api\n", encoding="utf-8")
    (source / "docs/reference/database.md").write_text("database\n", encoding="utf-8")
    (source / "AGENTS.md").write_text("candidate rules\n", encoding="utf-8")
    (source / "CLAUDE.md").symlink_to("AGENTS.md")
    launcher = source / "scripts/proxima"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
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
    (probes / "browser.py").write_text("trusted", encoding="utf-8")
    (probes / "browser-scenarios.json").write_text(
        '{"version":1,"scenarios":["shell"]}',
        encoding="utf-8",
    )
    fixture_codex = probes / "codex-fixture"
    shutil.copyfile(
        Path(__file__).resolve().parents[3]
        / "trusted-probes"
        / "safe-update"
        / "codex-fixture",
        fixture_codex,
        follow_symlinks=False,
    )
    fixture_codex.chmod(0o500)
    return TrustedProbeBundle.load(probes, _tree_digest(probes))


def test_shipped_trusted_probe_bundle_is_executable_and_has_browser_scenarios():
    root = Path(__file__).resolve().parents[3] / "trusted-probes" / "safe-update"
    for name in ("browser.py", "probe.py"):
        compile(
            (root / name).read_text(encoding="utf-8"),
            str(root / name),
            "exec",
        )
    fixture_codex = root / "codex-fixture"
    assert fixture_codex.is_file()
    assert os.access(fixture_codex, os.X_OK)
    assert "codex-cli 0.145.0" in fixture_codex.read_text(encoding="utf-8")
    scenarios = json.loads(
        (root / "browser-scenarios.json").read_text(encoding="utf-8")
    )
    assert scenarios["version"] == 3
    definitions = scenarios["scenarios"]
    assert {scenario["name"] for scenario in definitions} == {
        "focus",
        "graph-freshness",
        "login",
        "master-popup-home",
        "ops-task",
        "repo-task",
        "review",
        "update-status",
    }
    assert all(scenario["steps"] for scenario in definitions)
    master_steps = next(
        scenario["steps"]
        for scenario in definitions
        if scenario["name"] == "master-popup-home"
    )
    assert {
        step["action"]
        for step in master_steps
    } >= {"assert", "assert_absent", "click"}
    assert any(
        "data-master-eligible" in step["selector"]
        for step in master_steps
    )
    assert len(
        {
            json.dumps(scenario["steps"], sort_keys=True)
            for scenario in definitions
        }
    ) == len(definitions)


def test_trusted_probe_runner_pins_browser_resource_ceilings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    release = tmp_path / "release"
    workspace = tmp_path / "workspace"
    runner_home = tmp_path / "runner"
    database = tmp_path / "database" / "fixture.db"
    for path in (release, workspace, runner_home, database.parent):
        path.mkdir(parents=True, exist_ok=True)
    database.touch()
    observed: dict[str, object] = {}

    class Sandbox:
        root = tmp_path
        port = 18768

        def __init__(self):
            self.release = release
            self.workspace = workspace
            self.runner_home = runner_home
            self.database = database

        def run(self, argv, **kwargs):
            observed["memory_bytes"] = kwargs["memory_bytes"]
            observed["process_limit"] = kwargs["process_limit"]
            observed["auxiliary_tools"] = kwargs["auxiliary_tools"]
            return subprocess.CompletedProcess(
                argv,
                0,
                b'{"ok":true,"results":{"browser":{"shell":"ok"}}}',
            )

    monkeypatch.setattr(
        TrustedProbeRunner,
        "_browser",
        staticmethod(lambda: ("/usr/bin/true", {})),
    )
    trusted_bundle = _trusted(tmp_path)
    result = TrustedProbeRunner().run(
        sandbox=Sandbox(),
        trusted_bundle=trusted_bundle,
        identity=CandidateIdentity(
            f"sha256-{'a' * 40}-{'b' * 12}",
            "a" * 40,
            "c" * 64,
            "1.0.2",
        ),
        auth_token="candidate-token",
        session_id=1,
    )
    assert result.results == {"browser": {"shell": "ok"}}
    assert observed["memory_bytes"] == TRUSTED_BROWSER_ADDRESS_SPACE_BYTES
    assert observed["memory_bytes"] == 128 * 1024 * 1024 * 1024
    assert observed["process_limit"] == TRUSTED_BROWSER_PROCESS_LIMIT
    assert observed["process_limit"] == 256
    auxiliary_tools = observed["auxiliary_tools"]
    assert isinstance(auxiliary_tools, dict)
    codex = auxiliary_tools["codex"]
    assert isinstance(codex, Path)
    assert codex == trusted_bundle.root / "codex-fixture"
    assert "codex-cli 0.145.0" in codex.read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="bubblewrap sandbox contract",
)
def test_candidate_sandbox_mounts_read_only_auxiliary_tool(tmp_path: Path):
    root = tmp_path / "sandbox"
    release = root / "release"
    workspace = root / "workspace"
    runner_home = root / "runner"
    release.mkdir(parents=True)
    workspace.mkdir()
    runner_home.mkdir()
    fixture_codex = tmp_path / "codex-fixture"
    shutil.copyfile(
        Path(__file__).resolve().parents[3]
        / "trusted-probes"
        / "safe-update"
        / "codex-fixture",
        fixture_codex,
        follow_symlinks=False,
    )
    fixture_codex.chmod(0o777)
    sandbox = CandidateSandbox(
        root,
        release,
        root / "candidate.db",
        workspace,
        runner_home,
        18764,
        storage_bytes=32 * 1024 * 1024,
        reserve_bytes=1024 * 1024,
        tmpfs_bytes=8 * 1024 * 1024,
        file_bytes=16 * 1024 * 1024,
    )
    script = "\n".join(
        (
            "import shutil, subprocess",
            "from pathlib import Path",
            "path = shutil.which('codex')",
            "print(path)",
            "print(subprocess.check_output([path, '--version'], text=True).strip())",
            "print(subprocess.run([path, 'exec']).returncode)",
            "try:",
            "    Path(path).write_text('replaced', encoding='utf-8')",
            "except OSError:",
            "    print('read-only')",
            "else:",
            "    print('writable')",
        )
    )
    result = sandbox.run(
        ("/usr/bin/python3", "-c", script),
        cwd=release,
        writable_paths=(release, runner_home),
        auxiliary_tools={"codex": fixture_codex},
        timeout=10,
    )
    assert result.returncode == 0
    assert (result.stdout or b"").decode().splitlines() == [
        "/opt/proxima-tools/codex",
        "codex-cli 0.145.0",
        "1",
        "read-only",
    ]
    assert "codex-cli 0.145.0" in fixture_codex.read_text(encoding="utf-8")


def test_offline_manifest_uses_only_the_candidate_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    candidate = _source(tmp_path)
    verified = verify_local_provenance(
        local_provenance("task", "a" * 40, "b" * 40, "local", candidate),
        candidate,
    )
    release = tmp_path / "materialized-source"
    copy_verified_source(candidate, release, verified)
    cache = tmp_path / "cache"
    cache.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    calls: list[tuple[tuple[str, ...], Path]] = []

    class Sandbox:
        def __init__(self):
            self.root = tmp_path
            self.runner_home = home
            self.release = release

        def run(self, argv, *, cwd, **kwargs):
            calls.append((tuple(argv), cwd))
            assert release in kwargs["read_only_paths"]
            assert release not in kwargs["writable_paths"]
            if tuple(argv) == next(
                step.argv for step in BUILD_MANIFEST if step.name == "web-build"
            ):
                dist = next(
                    source
                    for source, target in kwargs["writable_overlays"].items()
                    if target == release / "apps/web/dist"
                )
                (dist / "assets").mkdir()
                (dist / "assets/main.js").write_text("built", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, b"ok")

    monkeypatch.setattr(OfflineBuilder, "_tools", staticmethod(lambda: {}))
    result = OfflineBuilder().build(release, cache_root=cache, sandbox=Sandbox())
    assert [call[0] for call in calls] == [step.argv for step in BUILD_MANIFEST]
    assert result.logs == {step.name: b"ok" for step in BUILD_MANIFEST}
    assert set(dict(result.artifacts)) == {
        "docs-reference",
        "python-environment",
        "web-dependencies",
        "web-dist",
    }


def test_local_source_materializes_safe_symlinks_and_executable_modes(tmp_path: Path):
    source = _source(tmp_path)
    provenance = local_provenance("task", "a" * 40, "b" * 40, "local", source)
    verified = verify_local_provenance(provenance, source)
    assert verified.symlinks() == {"CLAUDE.md": "AGENTS.md"}
    assert verified.modes()["scripts/proxima"] == 0o555
    destination = tmp_path / "materialized"
    copy_verified_source(source, destination, verified)
    assert (destination / "CLAUDE.md").is_file()
    assert not (destination / "CLAUDE.md").is_symlink()
    assert (destination / "scripts/proxima").stat().st_mode & 0o111

    escaping = source / "escape"
    escaping.symlink_to("../outside")
    with pytest.raises(ValueError, match="symlink"):
        local_provenance("task", "a" * 40, "b" * 40, "local", source)


def test_git_worktree_pointer_resolves_to_one_read_only_common_root(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    common = tmp_path / "git-common"
    git_dir = common / "worktrees" / "candidate"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (source / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    metadata = resolve_git_metadata(source)

    assert metadata.read_only_roots == (common,)


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
        storage_bytes=32 * 1024 * 1024,
        reserve_bytes=1024 * 1024,
        tmpfs_bytes=8 * 1024 * 1024,
        file_bytes=16 * 1024 * 1024,
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


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="bubblewrap sandbox contract",
)
def test_candidate_sandbox_enforces_aggregate_storage_quota(tmp_path: Path):
    root = tmp_path / "sandbox"
    release = root / "release"
    workspace = root / "workspace"
    runner_home = root / "runner"
    release.mkdir(parents=True)
    workspace.mkdir()
    runner_home.mkdir()
    sandbox = CandidateSandbox(
        root,
        release,
        root / "candidate.db",
        workspace,
        runner_home,
        18766,
        storage_bytes=2 * 1024 * 1024,
        reserve_bytes=1024 * 1024,
        tmpfs_bytes=8 * 1024 * 1024,
        file_bytes=1024 * 1024,
    )
    script = (
        "from pathlib import Path;"
        "root=Path('.');"
        "[(root/f'fill-{n}').write_bytes(b'x'*(900*1024)) for n in range(3)]"
    )
    with pytest.raises(SandboxError, match="storage quota"):
        sandbox.run(
            ("/usr/bin/python3", "-c", script),
            cwd=release,
            writable_paths=(release, runner_home),
            timeout=10,
        )


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="bubblewrap sandbox contract",
)
def test_loopback_probe_children_drop_all_namespace_capabilities(tmp_path: Path):
    root = tmp_path / "sandbox"
    release = root / "release"
    workspace = root / "workspace"
    runner_home = root / "runner"
    release.mkdir(parents=True)
    workspace.mkdir()
    runner_home.mkdir()
    sandbox = CandidateSandbox(
        root,
        release,
        root / "candidate.db",
        workspace,
        runner_home,
        18767,
        storage_bytes=32 * 1024 * 1024,
        reserve_bytes=1024 * 1024,
        tmpfs_bytes=8 * 1024 * 1024,
        file_bytes=16 * 1024 * 1024,
    )
    probe_root = (
        Path(__file__).resolve().parents[3] / "trusted-probes" / "safe-update"
    )
    spec = importlib.util.spec_from_file_location(
        "safe_update_trusted_probe",
        probe_root / "probe.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(probe_root))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(probe_root))
    script = (
        "from pathlib import Path;"
        "status=Path('/proc/self/status').read_text();"
        "wanted=('CapInh:', 'CapPrm:', 'CapEff:', 'CapBnd:', "
        "'CapAmb:', 'NoNewPrivs:');"
        "print(''.join(line for line in status.splitlines(True) "
        "if line.startswith(wanted)),end='')"
    )
    result = sandbox.run(
        (*module._drop_prefix(), "/usr/bin/python3", "-c", script),
        cwd=runner_home,
        writable_paths=(workspace, runner_home),
        read_only_paths=(release,),
        network_loopback=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert (result.stdout or b"").decode().splitlines() == [
        "CapInh:\t0000000000000000",
        "CapPrm:\t0000000000000000",
        "CapEff:\t0000000000000000",
        "CapBnd:\t0000000000000000",
        "CapAmb:\t0000000000000000",
        "NoNewPrivs:\t1",
    ]


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
        staticmethod(lambda _source, _commit, _verified, _sandbox: True),
    )

    def build(release, *, cache_root, sandbox):
        assert sandbox.release == release
        assert cache_root.is_dir()
        output_root = sandbox.root / "build-outputs"
        python = output_root / "python-environment"
        dependencies = output_root / "web-dependencies"
        dist = output_root / "web-dist"
        docs = output_root / "docs-reference"
        for path in (python, dependencies, dist / "assets", docs):
            path.mkdir(parents=True)
        interpreter = python / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(Path("/usr/bin/python3"))
        (dependencies / "package.js").write_text("dependency", encoding="utf-8")
        (dist / "index.html").write_text(
            '<html><head><title>Proxima</title></head><body><div id="root"></div>'
            '<script src="/assets/main.js"></script></body></html>',
            encoding="utf-8",
        )
        (dist / "assets/main.js").write_text("fixture", encoding="utf-8")
        return BuildResult(
            {"fixed": b"ok"},
            {"apps/api/uv.lock": "a" * 64},
            (
                ("docs-reference", docs),
                ("python-environment", python),
                ("web-dependencies", dependencies),
                ("web-dist", dist),
            ),
        )

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
    interpreter = result.release / "apps" / "api" / ".venv" / "bin" / "python"
    assert interpreter.is_file() and not interpreter.is_symlink()
    assert interpreter.stat().st_mode & 0o111
    launcher = result.release / "scripts/proxima"
    assert launcher.stat().st_mode & 0o111
    assert (result.release / "CLAUDE.md").is_file()
    assert not (result.release / "CLAUDE.md").is_symlink()
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


def test_migration_rejects_candidate_whose_maximum_is_older_than_policy(tmp_path: Path):
    database = tmp_path / "candidate.db"
    _live_database(database)

    class Sandbox:
        def __init__(self):
            self.database = database
            self.release = tmp_path / "release"
            self.runner_home = tmp_path / "runner"

        def run(self, *_args, **_kwargs):
            return subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "applied": [],
                        "candidate_expected_version": EXPECTED_MIGRATION_VERSION - 1,
                    }
                ).encode(),
            )

    sandbox = Sandbox()
    (sandbox.release / "apps/api").mkdir(parents=True)
    sandbox.runner_home.mkdir()
    with pytest.raises(CandidateDataError, match="differs from policy"):
        migrate_clone_in_sandbox(
            database,
            sandbox,
            EXPECTED_MIGRATION_VERSION,
        )


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

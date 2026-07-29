"""Mandatory pre-switch candidate qualification pipeline."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .build import OfflineBuilder
from .candidate_data import (
    MigrationReport,
    clone_live_database,
    migrate_clone_in_sandbox,
)
from .evidence import EvidenceBundle, EvidenceStore
from .fixture_assembler import FixtureResult, assemble_fixture
from .git_source import GitSourceError, verify_git_source
from .layout import ReleaseLayout
from .manifest import verify_local_provenance
from .probe_runner import (
    CandidateIdentity,
    TrustedProbeRunner,
    asset_manifest_digest,
)
from .sandbox import CandidateSandbox, SandboxError
from .tree import (
    VerifiedTree,
    copy_verified_source,
    copy_regular_tree,
    materialize_build_symlinks,
    regular_file_digests,
    regular_file_modes,
)
from .trusted_probes import TrustedProbeBundle


class CandidateGateError(RuntimeError):
    pass


def release_id_for(verified: VerifiedTree) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "files": verified.files(),
                "modes": verified.modes(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return f"sha256-{verified.commit}-{digest[:12]}"


def _overlaps(left: Path, right: Path) -> bool:
    first = left.resolve()
    second = right.resolve()
    return first == second or first in second.parents or second in first.parents


@dataclass(frozen=True)
class CandidateGateResult:
    release_id: str
    release: Path
    clone: Path
    fixture: Path
    evidence: EvidenceBundle


class CandidateGate:
    """Owns the complete build, clone, probe, publication and evidence gate."""

    def __init__(self, root: Path, *, expected_migration_version: int) -> None:
        if expected_migration_version < 1:
            raise CandidateGateError("candidate migration policy is invalid")
        self.root = root.resolve()
        self.layout = ReleaseLayout(root)
        self.expected_migration_version = expected_migration_version
        self.builder = OfflineBuilder()
        self.probes = TrustedProbeRunner()
        self.evidence = EvidenceStore(root)

    @staticmethod
    def _source_is_clean(
        source: Path,
        commit: str,
        verified: VerifiedTree,
        sandbox: CandidateSandbox,
    ) -> bool:
        try:
            verify_git_source(source, commit, verified, sandbox)
        except (GitSourceError, SandboxError):
            return False
        return True

    @staticmethod
    def _copy_cache(source: Path, destination: Path) -> None:
        if not source.is_dir() or source.is_symlink() or destination.exists():
            raise CandidateGateError("offline candidate cache is unavailable")
        files = regular_file_digests(source)
        if not files:
            raise CandidateGateError("offline candidate cache is empty")
        copy_regular_tree(source, destination, files)

    @staticmethod
    def _version(release: Path) -> str:
        path = release / "VERSION"
        if not path.is_file() or path.is_symlink():
            raise CandidateGateError("candidate version file is unavailable")
        value = path.read_text(encoding="utf-8").strip()
        if not value or len(value) > 64 or any(ord(character) < 32 for character in value):
            raise CandidateGateError("candidate version identity is invalid")
        return value

    @staticmethod
    def _copy_build_artifact(
        source: Path,
        destination: Path,
        *,
        python_environment: bool = False,
    ) -> None:
        if not source.is_dir() or source.is_symlink() or destination.exists():
            raise CandidateGateError("candidate build artifact is unavailable")
        external = (
            tuple(
                path
                for path in (source / "bin").glob("python*")
                if path.is_symlink()
            )
            if python_environment
            else ()
        )
        materialize_build_symlinks(
            source,
            external_executables=external,
        )
        files = regular_file_digests(source)
        modes = regular_file_modes(source)
        if not files:
            raise CandidateGateError("candidate build artifact is empty")
        copy_regular_tree(source, destination, files, modes)

    @staticmethod
    def _freeze_tree(root: Path) -> None:
        for path in sorted(
            [root, *root.rglob("*")],
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            if path.is_symlink():
                raise CandidateGateError("candidate build contains a symlink")
            if path.is_dir():
                path.chmod(0o555)
            else:
                path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)

    @staticmethod
    def _remove_tree(root: Path) -> None:
        for path in [root, *root.rglob("*")]:
            if path.is_symlink():
                continue
            path.chmod(0o700 if path.is_dir() else 0o600)
        shutil.rmtree(root)

    @staticmethod
    def _evidence_records(
        *,
        build,
        migration: MigrationReport,
        fixture: FixtureResult,
        assets: str,
        release_id: str,
        commit: str,
        version: str,
        trusted_probes: TrustedProbeBundle,
        probe_result,
    ) -> dict[str, bytes | str]:
        records: dict[str, bytes | str] = {
            "assets.json": json.dumps(
                {
                    "asset_manifest_digest": assets,
                    "commit": commit,
                    "release_id": release_id,
                    "version": version,
                },
                sort_keys=True,
            ),
            "build.json": json.dumps(
                {
                    "locks": build.lock_digests,
                    "steps": sorted(build.logs),
                },
                sort_keys=True,
            ),
            "fixture.json": json.dumps(
                {
                    "schema_version": fixture.schema_version,
                    "session_id": fixture.session_id,
                },
                sort_keys=True,
            ),
            "migration.json": json.dumps(
                {
                    "expected_version": migration.clone.schema_version,
                    "ledger": migration.clone.migration_versions,
                },
                sort_keys=True,
            ),
            "migration.log": migration.output,
            "probe-results.json": probe_result.raw,
            "trusted-probes.json": json.dumps(
                {
                    "bundle_digest": trusted_probes.digest,
                    "results": probe_result.results,
                },
                sort_keys=True,
            ),
        }
        for name, output in build.logs.items():
            records[f"build-{name}.log"] = output
        return records

    def prepare(
        self,
        run_id: str,
        *,
        candidate_source: Path,
        local_provenance: dict,
        live_database: Path,
        cache_root: Path,
        candidate_port: int,
        protected_paths: tuple[Path, ...] = (),
        trusted_probes: TrustedProbeBundle | None = None,
    ) -> CandidateGateResult:
        protected = (
            candidate_source,
            live_database,
            cache_root,
            *protected_paths,
        )
        if any(_overlaps(self.root, path) for path in protected):
            raise CandidateGateError("candidate controller root overlaps a protected path")
        verified = verify_local_provenance(local_provenance, candidate_source)
        if trusted_probes is None:
            raise CandidateGateError("trusted probe bundle is required")
        run_root = self.layout.run_dir(run_id)
        release: Path | None = None
        try:
            run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
            build_home = run_root / "build-home"
            build_workspace = run_root / "build-workspace"
            build_home.mkdir(mode=0o700)
            build_workspace.mkdir(mode=0o700)
            source_sandbox = CandidateSandbox(
                run_root,
                candidate_source,
                run_root / "source-check.db",
                build_workspace,
                build_home,
                candidate_port,
            )
            source_sandbox.validate(protected)
            if not self._source_is_clean(
                candidate_source,
                verified.commit,
                verified,
                source_sandbox,
            ):
                raise CandidateGateError("candidate source is dirty or commit identity is unavailable")

            authenticated_source = run_root / "authenticated-source"
            copy_verified_source(candidate_source, authenticated_source, verified)
            clone = run_root / "database" / "raw-clone.db"
            clone_live_database(live_database, clone)
            candidate_cache = run_root / "offline-cache"
            self._copy_cache(cache_root, candidate_cache)
            build_sandbox = CandidateSandbox(
                run_root,
                authenticated_source,
                clone,
                build_workspace,
                build_home,
                candidate_port,
            )
            build_sandbox.validate(protected)
            build = self.builder.build(
                authenticated_source,
                cache_root=candidate_cache,
                sandbox=build_sandbox,
            )
            build_root = run_root / "build"
            copy_regular_tree(
                authenticated_source,
                build_root,
                verified.files(),
                verified.modes(),
            )
            self._copy_build_artifact(
                build.artifact("python-environment"),
                build_root / "apps" / "api" / ".venv",
                python_environment=True,
            )
            self._copy_build_artifact(
                build.artifact("web-dependencies"),
                build_root / "apps" / "web" / "node_modules",
            )
            self._copy_build_artifact(
                build.artifact("web-dist"),
                build_root / "apps" / "web" / "dist",
            )
            materialize_build_symlinks(build_root)
            self._freeze_tree(build_root)
            migration_sandbox = CandidateSandbox(
                run_root,
                build_root,
                clone,
                build_workspace,
                build_home,
                candidate_port,
            )
            migration_sandbox.validate(protected)
            migration = migrate_clone_in_sandbox(
                clone,
                migration_sandbox,
                self.expected_migration_version,
            )

            built_tree = VerifiedTree(
                release_id=None,
                commit=verified.commit,
                file_digests=tuple(sorted(regular_file_digests(build_root).items())),
                file_modes=tuple(sorted(regular_file_modes(build_root).items())),
            )
            release_id = release_id_for(built_tree)
            release = self.layout.create_immutable_release(
                release_id,
                build_root,
                built_tree,
            )
            runtime_root = run_root / "runtime"
            fixture = assemble_fixture(
                clone,
                runtime_root / "database" / "runtime-fixture.db",
                workspace=runtime_root / "workspace",
                runner_home=runtime_root / "runner-home",
                expected_version=self.expected_migration_version,
            )
            assets = asset_manifest_digest(release / "apps" / "web" / "dist")
            version = self._version(release)
            probe_sandbox = CandidateSandbox(
                run_root,
                release,
                fixture.path,
                runtime_root / "workspace",
                runtime_root / "runner-home",
                candidate_port,
            )
            probe_sandbox.validate(protected)
            probe_result = self.probes.run(
                sandbox=probe_sandbox,
                trusted_bundle=trusted_probes,
                identity=CandidateIdentity(
                    release_id,
                    verified.commit,
                    assets,
                    version,
                ),
                auth_token=fixture.auth_token,
                session_id=fixture.session_id,
            )
            self._remove_tree(build_root)
            self._remove_tree(authenticated_source)
            shutil.rmtree(run_root / "build-outputs")
            shutil.rmtree(candidate_cache)
            bundle = self.evidence.persist(
                run_id,
                self._evidence_records(
                    build=build,
                    migration=migration,
                    fixture=fixture,
                    assets=assets,
                    release_id=release_id,
                    commit=verified.commit,
                    version=version,
                    trusted_probes=trusted_probes,
                    probe_result=probe_result,
                ),
            )
            return CandidateGateResult(
                release_id,
                release,
                clone,
                fixture.path,
                bundle,
            )
        except BaseException as exc:
            evidence_dir = self.root / "evidence" / run_id
            if not evidence_dir.exists():
                self.evidence.persist(
                    run_id,
                    {
                        "failure.json": json.dumps(
                            {
                                "error": type(exc).__name__,
                                "phase": "candidate_gate",
                                "published_release": release is not None,
                            },
                            sort_keys=True,
                        ),
                    },
                )
            raise

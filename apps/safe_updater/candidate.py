"""Pre-switch candidate gate.  This module has no pointer, fence or service API."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .build import OfflineBuilder
from .candidate_data import clone_live_database, validate_migrated_clone
from .evidence import EvidenceBundle, EvidenceStore
from .fixture_assembler import assemble_fixture
from .layout import ReleaseLayout
from .manifest import verify_local_provenance
from .probe_runner import asset_manifest_digest
from .tree import VerifiedTree
from .trusted_probes import TrustedProbeBundle


class CandidateGateError(RuntimeError):
    pass


def release_id_for(verified: VerifiedTree) -> str:
    digest = hashlib.sha256(json.dumps(verified.files(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"sha256-{verified.commit}-{digest[:12]}"


@dataclass(frozen=True)
class CandidateGateResult:
    release_id: str
    release: Path
    clone: Path
    fixture: Path
    evidence: EvidenceBundle


class CandidateGate:
    """Owns only candidate artifacts below the controller root.

    The caller supplies launch/probe hooks from a qualified external adapter.  No
    hook receives a live data or release-pointer path.
    """

    def __init__(
        self, root: Path, *, builder: OfflineBuilder | None = None,
        source_is_clean: Callable[[Path, str], bool] | None = None,
    ) -> None:
        self.root = root
        self.layout = ReleaseLayout(root)
        self.builder = builder or OfflineBuilder()
        self.evidence = EvidenceStore(root)
        self.source_is_clean = source_is_clean or self._source_is_clean

    @staticmethod
    def _source_is_clean(source: Path, commit: str) -> bool:
        try:
            status = subprocess.run(
                ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=False, text=True,
            )
            head = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=False, text=True,
            )
        except OSError:
            return False
        return status.returncode == 0 and not status.stdout.strip() and head.returncode == 0 and head.stdout.strip() == commit

    def prepare(
        self, run_id: str, *, candidate_source: Path, local_provenance: dict,
        live_database: Path, cache_root: Path, protected_paths: tuple[Path, ...] = (),
        migrate_clone: Callable[[Path], None] | None = None,
        trusted_probes: TrustedProbeBundle | None = None,
    ) -> CandidateGateResult:
        if candidate_source.resolve() in {path.resolve() for path in protected_paths}:
            raise CandidateGateError("candidate source overlaps a protected path")
        verified = verify_local_provenance(local_provenance, candidate_source)
        if not self.source_is_clean(candidate_source, verified.commit):
            raise CandidateGateError("candidate source is dirty or commit identity is unavailable")
        if trusted_probes is None:
            raise CandidateGateError("trusted probe bundle is required")
        release_id = release_id_for(verified)
        release = self.layout.create_immutable_release(release_id, candidate_source, verified)
        run_root = self.layout.run_dir(run_id)
        try:
            run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
            clone = run_root / "raw-clone.db"
            clone_live_database(live_database, clone)
            if migrate_clone is None:
                raise CandidateGateError("candidate migration runner is required")
            migrate_clone(clone)
            validate_migrated_clone(clone)
            fixture = assemble_fixture(clone, run_root / "runtime-fixture.db", workspace=run_root / "workspace", runner_home=run_root / "runner-home")
            build = self.builder.build(release, cache_root=cache_root)
            assets = asset_manifest_digest(release / "apps" / "web" / "dist")
            bundle = self.evidence.persist(run_id, {
                "build.json": json.dumps({"steps": sorted(build.logs), "locks": build.lock_digests}, sort_keys=True),
                "assets.json": json.dumps({"asset_manifest_digest": assets, "release_id": release_id, "commit": verified.commit}, sort_keys=True),
                "trusted-probes.json": json.dumps({"digest": trusted_probes.digest}, sort_keys=True),
            })
            return CandidateGateResult(release_id, release, clone, fixture, bundle)
        except BaseException as exc:
            # Active state remains untouched. Candidate artifacts are retained as
            # controller-owned forensic evidence, never copied back to live paths.
            evidence_dir = self.root / "evidence" / run_id
            if not evidence_dir.exists():
                self.evidence.persist(run_id, {
                    "failure.json": json.dumps({"phase": "candidate_gate", "error": type(exc).__name__}, sort_keys=True),
                })
            raise

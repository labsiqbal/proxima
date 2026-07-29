"""Trusted controller facade for candidate-only qualification."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .evidence import EvidenceStore
from .journal import Journal
from .layout import RUN_ID, ReleaseLayout
from .locks import SingleFlightLock
from .recovery import RecoveryStatus, inspect
from .sqlite_image import SealedImage, checkpoint_truncate, quarantine_sidecars, replace_from_sealed, seal_backup
from .state_machine import Phase
from .write_fence import remove as remove_fence
from .write_fence import write as write_fence
from .circuit_breaker import CircuitBreaker
from .service_adapter import DisposableServiceAdapter

if TYPE_CHECKING:
    from .candidate import CandidateGate, CandidateGateResult


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    run_id: str
    reason: str | None = None


class SafeUpdateController:
    """Owns the lock, journal and candidate gate without activation authority."""
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = SingleFlightLock(root / "controller.lock")

    def _active_run(self) -> str | None:
        journal_dir = self.root / "journal"
        if not journal_dir.exists():
            return None
        if journal_dir.is_symlink() or not journal_dir.is_dir():
            return "unknown"
        for path in sorted(journal_dir.iterdir()):
            match = RUN_ID.fullmatch(path.name.removesuffix(".jsonl"))
            if path.suffix != ".jsonl" or match is None or path.is_symlink() or not path.is_file():
                return "unknown"
            try:
                first = path.read_bytes().splitlines()[0]
                intent_digest = str(json.loads(first)["intent_digest"])
                records = Journal(path, intent_digest).records()
            except (IndexError, KeyError, OSError, ValueError, json.JSONDecodeError):
                return match.group(0)
            if not records or records[-1].phase is not Phase.COMPLETED:
                return match.group(0)
        return None

    def submit(self, intent: dict[str, Any]) -> SubmitResult:
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        run_id = secrets.token_hex(16)
        acquired = self.lock.acquire(run_id, publish_owner=False)
        if not acquired.acquired:
            return SubmitResult(False, acquired.run_id or "unknown", "safe_update_in_progress")
        try:
            active_run = self._active_run()
            if active_run is not None:
                self.lock.set_owner(active_run)
                return SubmitResult(False, active_run, "safe_update_in_progress")
            self.lock.set_owner(run_id)
            journal = Journal.create(self.root, run_id, digest)
            journal.append(Phase.PREFLIGHT)
            # Submission stops at preflight. Candidate qualification is a separate,
            # explicit step and cannot activate a release.
            return SubmitResult(True, run_id)
        finally:
            self.lock.release()

    def recovery_status(self, run_id: str, intent: dict[str, Any]) -> RecoveryStatus:
        if not RUN_ID.fullmatch(run_id):
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                None,
                "invalid journal run id",
            )
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        return inspect(
            Journal(self.root / "journal" / f"{run_id}.jsonl", digest),
            evidence_store=EvidenceStore(self.root),
            run_id=run_id,
        )

    def qualify_candidate(
        self,
        run_id: str,
        intent: dict[str, Any],
        gate: CandidateGate,
        **kwargs: Any,
    ) -> CandidateGateResult:
        """Run only the pre-switch candidate gate for an accepted journal.

        This deliberately has no access to active pointers, fences, backups or a
        service adapter.  Later groups own every transition after
        ``candidate_staged``.
        """
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid journal run id")
        digest = hashlib.sha256(
            json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        journal = Journal(self.root / "journal" / f"{run_id}.jsonl", digest)
        records = journal.records()
        if not records or records[-1].phase is not Phase.PREFLIGHT:
            raise RuntimeError("candidate qualification requires an accepted preflight run")
        result = gate.prepare(run_id, **kwargs)
        EvidenceStore(self.root).load(run_id, result.evidence.digest)
        journal.append(Phase.CANDIDATE_STAGED, {"candidate_evidence": result.evidence.digest})
        return result

    def promote_disposable_fixture(
        self,
        run_id: str,
        intent: dict[str, Any],
        *,
        adapter: DisposableServiceAdapter,
        fence_path: Path,
        live_database: Path,
        staged_database: Path,
        previous_release_id: str,
        candidate_release_id: str,
        probe: Any,
    ) -> str:
        """Exercise the complete A/B transaction against disposable fixture data only.

        No production adapter implements ``DisposableServiceAdapter``.  Requiring
        every path to resolve beneath this controller root prevents a caller from
        accidentally pointing this harness at live data while the feature stays
        disabled and enrollment remains unavailable.
        """
        if not isinstance(adapter, DisposableServiceAdapter) or not adapter.disposable_fixture:
            raise RuntimeError("promotion requires a disposable fixture adapter")
        root = self.root.resolve()
        for path in (fence_path, live_database, staged_database):
            resolved = path.resolve(strict=False)
            if root not in (resolved, *resolved.parents):
                raise RuntimeError("promotion fixture path escapes controller root")
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        journal = Journal(self.root / "journal" / f"{run_id}.jsonl", digest)
        records = journal.records()
        if not records or records[-1].phase is not Phase.CANDIDATE_STAGED:
            raise RuntimeError("fixture promotion requires staged candidate evidence")
        layout = ReleaseLayout(self.root)
        if layout.pointer_release("active") != previous_release_id:
            raise RuntimeError("fixture previous release does not match active pointer")
        breaker = CircuitBreaker(self.root)
        if breaker.status().latched:
            raise RuntimeError("safe_update_breaker_latched")
        backup: SealedImage | None = None
        switched = False
        try:
            write_fence(fence_path, run_id, Phase.WRITE_FENCED.value)
            journal.append(Phase.WRITE_FENCED)
            adapter.pause_autonomous_writers()
            adapter.drain()
            journal.append(Phase.DRAINED)
            adapter.stop_and_verify()
            journal.append(Phase.OLD_SERVICE_STOPPED)
            checkpoint_truncate(live_database)
            journal.append(Phase.WAL_CHECKPOINTED)
            backup = seal_backup(live_database, self.root / "backups" / run_id / "final.db")
            journal.append(Phase.FINAL_BACKUP, {"final_backup": backup.digest})
            journal.append(Phase.STAGED_MIGRATED)
            staged = seal_backup(staged_database, self.root / "backups" / run_id / "staged.db")
            journal.append(Phase.STAGED_VALIDATED, {"staged_database": staged.digest})
            journal.append(Phase.IMAGE_SEALED, {"sealed_database": staged.digest})
            quarantine_sidecars(live_database, self.root / "backups" / run_id / "sidecars")
            journal.append(Phase.SIDECARS_QUARANTINED)
            replace_from_sealed(staged, live_database)
            journal.append(Phase.DB_SWAPPED, {"live_database": staged.digest})
            layout.set_pointer("active", candidate_release_id)
            switched = True
            journal.append(Phase.RELEASE_SWITCHED)
            adapter.start_readonly_candidate(candidate_release_id)
            journal.append(Phase.READONLY_STARTED)
            probe("readonly", candidate_release_id)
            journal.append(Phase.READONLY_SOAKED)
            adapter.stop_candidate()
            adapter.start_writable_candidate(candidate_release_id)
            journal.append(Phase.WRITABLE_STARTED)
            probe("writable", candidate_release_id)
            journal.append(Phase.WRITABLE_PROBED)
            layout.set_pointer("last-good", candidate_release_id)
            journal.append(Phase.LAST_GOOD_COMMITTED)
            remove_fence(fence_path)
            adapter.resume_autonomous_writers()
            journal.append(Phase.COMPLETED)
            return "candidate_good"
        except Exception as exc:
            rollback_failed = False
            try:
                if switched and backup is not None:
                    adapter.stop_candidate()
                    quarantine_sidecars(live_database, self.root / "backups" / run_id / "rollback-sidecars")
                    replace_from_sealed(backup, live_database)
                    layout.set_pointer("active", previous_release_id)
                adapter.start_previous_release()
                probe("rollback", previous_release_id)
                remove_fence(fence_path)
                adapter.resume_autonomous_writers()
            except Exception:
                rollback_failed = True
            breaker.record_failure(type(exc).__name__, rollback_failed=rollback_failed)
            if rollback_failed:
                raise RuntimeError("safe_update_breaker_latched") from exc
            raise

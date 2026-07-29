"""Trusted controller facade for candidate-only qualification."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .circuit_breaker import CircuitBreaker
from .durability import ensure_durable_directory, fsync_directory, write_all
from .evidence import EvidenceStore
from .journal import Journal
from .layout import RUN_ID, ReleaseLayout
from .locks import SingleFlightLock
from .recovery import RecoveryStatus, inspect
from .service_adapter import DisposableServiceAdapter
from .sqlite_image import SealedImage, checkpoint_truncate, quarantine_sidecars, replace_from_sealed, seal_backup
from .state_machine import ORDER, Phase
from .write_fence import IngressActivationError
from .write_fence import read_activation_state
from .write_fence import remove as remove_fence
from .write_fence import write as write_fence

if TYPE_CHECKING:
    from .candidate import CandidateGate, CandidateGateResult


_DISPOSABLE_FIXTURE_MARKER = ".proxima-disposable-safe-update-fixture"
_DISPOSABLE_FIXTURE_MARKER_VALUE = b"proxima-safe-update-fixture-v1\n"


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

    @classmethod
    def create_disposable_fixture(cls, root: Path) -> "SafeUpdateController":
        resolved = root.resolve()
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if resolved == temporary_root or temporary_root not in resolved.parents:
            raise RuntimeError("disposable fixture root must be beneath the temporary directory")
        ensure_durable_directory(resolved, 0o700)
        if any(resolved.iterdir()):
            raise RuntimeError("disposable fixture root must be empty")
        marker = resolved / _DISPOSABLE_FIXTURE_MARKER
        if os.path.lexists(marker):
            raise RuntimeError("disposable fixture root is already initialized")
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            write_all(descriptor, _DISPOSABLE_FIXTURE_MARKER_VALUE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(resolved)
        return cls(resolved)

    def _disposable_fixture_paths(
        self,
        fence_path: Path,
        live_database: Path,
        staged_database: Path,
    ) -> tuple[Path, Path, Path]:
        root = self.root.resolve()
        temporary_root = Path(tempfile.gettempdir()).resolve()
        marker = root / _DISPOSABLE_FIXTURE_MARKER
        if root == temporary_root or temporary_root not in root.parents:
            raise RuntimeError("promotion requires a temporary disposable fixture root")
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError("promotion requires an initialized disposable fixture root")
        try:
            marker_value = marker.read_bytes()
        except OSError as exc:
            raise RuntimeError("disposable fixture marker is unreadable") from exc
        if marker_value != _DISPOSABLE_FIXTURE_MARKER_VALUE:
            raise RuntimeError("disposable fixture marker is invalid")

        resolved_paths: list[Path] = []
        for value, role in (
            (fence_path, "status"),
            (live_database, "data"),
            (staged_database, "candidate"),
        ):
            resolved = value.resolve(strict=False)
            role_root = root / role
            if role_root not in resolved.parents:
                raise RuntimeError(f"promotion fixture {role} path is outside its role root")
            if (
                role == "status"
                and resolved != role_root / "fence.json"
            ):
                raise RuntimeError(
                    "promotion fixture status path must be canonical"
                )
            resolved_paths.append(resolved)
        if len(set(resolved_paths)) != len(resolved_paths):
            raise RuntimeError("promotion fixture paths must be distinct")
        return resolved_paths[0], resolved_paths[1], resolved_paths[2]

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
        breaker = CircuitBreaker(self.root).status()
        if breaker.latched:
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                None,
                breaker.reason or "safe_update_breaker_latched",
            )
        digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        recovered = inspect(
            Journal(self.root / "journal" / f"{run_id}.jsonl", digest),
            evidence_store=EvidenceStore(self.root),
            run_id=run_id,
        )
        try:
            activation = read_activation_state(
                self.root / "status" / "fence.json"
            )
        except RuntimeError as exc:
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                recovered.journal_hash,
                str(exc),
            )
        if activation is not None and activation.run_id != run_id:
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                recovered.journal_hash,
                "maintenance activation belongs to another run",
            )
        if (
            activation is not None
            and recovered.safe
            and recovered.action == "discard_candidate"
        ):
            return RecoveryStatus(
                False,
                "do_not_start_any_release",
                recovered.journal_hash,
                "maintenance activation was not acknowledged by the journal",
            )
        return recovered

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

        No production adapter implements ``DisposableServiceAdapter``. The
        initialized temporary root and role-confined paths prevent this harness
        from targeting live data while enrollment remains unavailable.
        """
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid journal run id")
        if not isinstance(adapter, DisposableServiceAdapter) or not adapter.disposable_fixture:
            raise RuntimeError("promotion requires a disposable fixture adapter")
        fence_path, live_database, staged_database = self._disposable_fixture_paths(
            fence_path,
            live_database,
            staged_database,
        )
        acquired = self.lock.acquire(run_id)
        if not acquired.acquired:
            raise RuntimeError("safe_update_in_progress")
        try:
            digest = hashlib.sha256(
                json.dumps(
                    intent,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
            ).hexdigest()
            journal = Journal(self.root / "journal" / f"{run_id}.jsonl", digest)
            records = journal.records()
            if not records or records[-1].phase is not Phase.CANDIDATE_STAGED:
                raise RuntimeError("fixture promotion requires staged candidate evidence")
            layout = ReleaseLayout(self.root)
            previous_active = layout.pointer_release("active")
            previous_last_good = layout.pointer_release("last-good")
            if previous_active != previous_release_id:
                raise RuntimeError("fixture previous release does not match active pointer")
            breaker = CircuitBreaker(self.root)
            if breaker.status().latched:
                raise RuntimeError("safe_update_breaker_latched")
            backup: SealedImage | None = None
            acknowledged_phase = records[-1].phase
            journal_append_failed = False
            fence_installed = False
            writers_pause_attempted = False
            service_stop_attempted = False
            database_swap_attempted = False
            pointer_change_attempted = False
            candidate_start_attempted = False

            def append_phase(
                phase: Phase,
                evidence: dict[str, str] | None = None,
            ) -> None:
                nonlocal acknowledged_phase, journal_append_failed
                try:
                    record = journal.append(phase, evidence)
                except Exception:
                    journal_append_failed = True
                    raise
                acknowledged_phase = record.phase

            try:
                write_fence(fence_path, run_id, Phase.WRITE_FENCED.value)
                fence_installed = True
                append_phase(Phase.WRITE_FENCED)
                writers_pause_attempted = True
                adapter.pause_autonomous_writers()
                adapter.drain()
                append_phase(Phase.DRAINED)
                service_stop_attempted = True
                adapter.stop_and_verify()
                append_phase(Phase.OLD_SERVICE_STOPPED)
                checkpoint_truncate(live_database)
                append_phase(Phase.WAL_CHECKPOINTED)
                backup = seal_backup(
                    live_database,
                    self.root / "backups" / run_id / "final.db",
                )
                append_phase(Phase.FINAL_BACKUP, {"final_backup": backup.digest})
                append_phase(Phase.STAGED_MIGRATED)
                staged = seal_backup(
                    staged_database,
                    self.root / "backups" / run_id / "staged.db",
                )
                append_phase(
                    Phase.STAGED_VALIDATED,
                    {"staged_database": staged.digest},
                )
                append_phase(Phase.IMAGE_SEALED, {"sealed_database": staged.digest})
                quarantine_sidecars(
                    live_database,
                    self.root / "backups" / run_id / "sidecars",
                )
                append_phase(Phase.SIDECARS_QUARANTINED)
                database_swap_attempted = True
                replace_from_sealed(staged, live_database)
                append_phase(Phase.DB_SWAPPED, {"live_database": staged.digest})
                pointer_change_attempted = True
                layout.set_pointer("active", candidate_release_id)
                append_phase(Phase.RELEASE_SWITCHED)
                candidate_start_attempted = True
                adapter.start_readonly_candidate(candidate_release_id)
                append_phase(Phase.READONLY_STARTED)
                probe("readonly", candidate_release_id)
                append_phase(Phase.READONLY_SOAKED)
                adapter.stop_candidate()
                adapter.start_writable_candidate(candidate_release_id)
                append_phase(Phase.WRITABLE_STARTED)
                probe("writable", candidate_release_id)
                append_phase(Phase.WRITABLE_PROBED)
                layout.set_pointer("last-good", candidate_release_id)
                append_phase(Phase.LAST_GOOD_COMMITTED)
                adapter.resume_autonomous_writers()
                remove_fence(fence_path, run_id)
                append_phase(Phase.COMPLETED)
                return "candidate_good"
            except Exception as exc:
                if (
                    ORDER[acknowledged_phase]
                    >= ORDER[Phase.LAST_GOOD_COMMITTED]
                ):
                    try:
                        if (
                            layout.pointer_release("active") != candidate_release_id
                            or layout.pointer_release("last-good")
                            != candidate_release_id
                        ):
                            raise RuntimeError("committed fixture pointers diverged")
                        adapter.stop_candidate()
                        adapter.start_writable_candidate(candidate_release_id)
                        adapter.resume_autonomous_writers()
                        remove_fence(fence_path, run_id)
                        if (
                            acknowledged_phase is Phase.LAST_GOOD_COMMITTED
                            and not journal_append_failed
                        ):
                            append_phase(Phase.COMPLETED)
                        elif acknowledged_phase is not Phase.COMPLETED:
                            raise RuntimeError("committed fixture journal diverged")
                        if journal_append_failed:
                            raise RuntimeError(
                                "fixture journal append was not acknowledged"
                            )
                        return "candidate_good"
                    except Exception as committed_recovery_error:
                        try:
                            breaker.record_failure(
                                type(exc).__name__,
                                rollback_failed=True,
                            )
                        except Exception:
                            pass
                        raise RuntimeError("safe_update_breaker_latched") from committed_recovery_error

                activation_ambiguous = isinstance(
                    exc,
                    IngressActivationError,
                )
                rollback_failed = activation_ambiguous
                try:
                    breaker.begin_rollback(type(exc).__name__)
                except Exception as breaker_error:
                    raise RuntimeError("safe_update_breaker_latched") from breaker_error
                try:
                    if candidate_start_attempted:
                        adapter.stop_candidate()
                    if database_swap_attempted:
                        if backup is None:
                            raise RuntimeError("fixture backup is unavailable")
                        quarantine_sidecars(
                            live_database,
                            self.root
                            / "backups"
                            / run_id
                            / "rollback-sidecars",
                        )
                        replace_from_sealed(backup, live_database)
                    if pointer_change_attempted:
                        layout.set_pointer("active", previous_active)
                        layout.set_pointer("last-good", previous_last_good)
                    if service_stop_attempted:
                        adapter.start_previous_release()
                        probe("rollback", previous_release_id)
                    if writers_pause_attempted:
                        adapter.resume_autonomous_writers()
                    if fence_installed:
                        remove_fence(fence_path, run_id)
                except Exception:
                    rollback_failed = True
                try:
                    breaker_status = breaker.finish_rollback(
                        type(exc).__name__,
                        latch=rollback_failed or journal_append_failed,
                    )
                except Exception as breaker_error:
                    raise RuntimeError("safe_update_breaker_latched") from breaker_error
                if rollback_failed or breaker_status.latched:
                    raise RuntimeError("safe_update_breaker_latched") from exc
                raise
        finally:
            self.lock.release()

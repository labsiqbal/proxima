"""Trusted, platform-neutral safe-update phase contract.

This module deliberately contains no activation primitive. Current candidate
qualification can reach only ``candidate_staged``; later delivery groups may bind
side effects to subsequent phases.
"""
from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    PREFLIGHT = "preflight"
    CANDIDATE_STAGED = "candidate_staged"
    WRITE_FENCED = "write_fenced"
    DRAINED = "drained"
    OLD_SERVICE_STOPPED = "old_service_stopped"
    WAL_CHECKPOINTED = "wal_checkpointed"
    FINAL_BACKUP = "final_backup"
    STAGED_MIGRATED = "staged_migrated"
    STAGED_VALIDATED = "staged_validated"
    IMAGE_SEALED = "image_sealed"
    SIDECARS_QUARANTINED = "sidecars_quarantined"
    DB_SWAPPED = "db_swapped"
    RELEASE_SWITCHED = "release_switched"
    READONLY_STARTED = "readonly_started"
    READONLY_SOAKED = "readonly_soaked"
    WRITABLE_STARTED = "writable_started"
    WRITABLE_PROBED = "writable_probed"
    LAST_GOOD_COMMITTED = "last_good_committed"
    COMPLETED = "completed"


ORDER = {phase: position for position, phase in enumerate(Phase)}


class StateTransitionError(ValueError):
    pass


def validate_transition(previous: Phase | None, current: Phase) -> None:
    """Allow only monotonic, one-boundary-at-a-time durable transitions."""
    if previous is None:
        if current is not Phase.PREFLIGHT:
            raise StateTransitionError("the first durable phase must be preflight")
        return
    if ORDER[current] != ORDER[previous] + 1:
        raise StateTransitionError(f"invalid phase transition: {previous.value} -> {current.value}")


def recovery_action(phase: Phase) -> str:
    """Fixed recovery table.  Candidate reports never influence this decision."""
    if ORDER[phase] < ORDER[Phase.WRITE_FENCED]:
        return "discard_candidate"
    if ORDER[phase] <= ORDER[Phase.FINAL_BACKUP]:
        return "restore_previous_release_and_remove_fence"
    if phase in {Phase.STAGED_MIGRATED, Phase.STAGED_VALIDATED}:
        return "discard_staged_data_restore_previous_release_and_remove_fence"
    if phase in {Phase.IMAGE_SEALED, Phase.SIDECARS_QUARANTINED, Phase.DB_SWAPPED, Phase.RELEASE_SWITCHED}:
        return "restore_final_backup_and_previous_release"
    if phase in {Phase.READONLY_STARTED, Phase.READONLY_SOAKED, Phase.WRITABLE_STARTED, Phase.WRITABLE_PROBED}:
        return "stop_candidate_restore_final_backup_and_previous_release"
    if phase in {Phase.LAST_GOOD_COMMITTED, Phase.COMPLETED}:
        return "resume_committed_candidate_or_latch_breaker"
    raise StateTransitionError(f"unknown phase: {phase}")

"""Canonical API timestamps and execution lifecycle projection."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

_TERMINAL_STATUSES = {"done", "failed", "cancelled"}


def api_timestamp(value: Any) -> Any:
    """Return a valid stored timestamp as a timezone-aware UTC ISO string."""
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(
            f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
        )
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    normalized = parsed.astimezone(timezone.utc).isoformat()
    return normalized.replace("+00:00", "Z")


def canonicalize_api_timestamps(value: Any) -> Any:
    """Normalize timestamp fields recursively without changing domain data."""
    if isinstance(value, list):
        return [canonicalize_api_timestamps(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key.endswith("_at") and isinstance(raw_value, str):
            normalized[key] = api_timestamp(raw_value)
        else:
            normalized[key] = canonicalize_api_timestamps(raw_value)
    return normalized


def _child_states(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    states = payload.get("node_states")
    if (
        not isinstance(states, Sequence)
        or isinstance(states, (str, bytes))
        or not states
    ):
        states = payload.get("steps_state")
    if not isinstance(states, Sequence) or isinstance(states, (str, bytes)):
        return []
    return [state for state in states if isinstance(state, Mapping)]


def _timestamp_seconds(value: Any) -> float | None:
    normalized = api_timestamp(value)
    if not isinstance(normalized, str):
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _boundary(
    payload: Mapping[str, Any],
    states: list[Mapping[str, Any]],
    field: str,
    *,
    earliest: bool,
) -> Any:
    direct = payload.get(field)
    if direct:
        return api_timestamp(direct)
    candidates = [
        (seconds, api_timestamp(state.get(field)))
        for state in states
        if (seconds := _timestamp_seconds(state.get(field))) is not None
    ]
    if not candidates:
        return None
    return (min if earliest else max)(candidates, key=lambda item: item[0])[1]


def project_job_run(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project the status and timing shared by every run surface."""
    states = _child_states(payload)
    stored_status = str(payload.get("status") or "queued")
    child_failed = any(str(state.get("status")) == "failed" for state in states)
    status = (
        "failed"
        if child_failed and stored_status not in _TERMINAL_STATUSES
        else stored_status
    )
    started_at = _boundary(payload, states, "started_at", earliest=True)
    finished_at = _boundary(payload, states, "finished_at", earliest=False)
    start_seconds = _timestamp_seconds(started_at)
    finish_seconds = _timestamp_seconds(finished_at)
    duration_seconds = None
    if (
        start_seconds is not None
        and finish_seconds is not None
        and finish_seconds >= start_seconds
    ):
        duration_seconds = round(finish_seconds - start_seconds)
    return {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
    }

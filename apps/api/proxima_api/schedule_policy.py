"""Schedule trust policy shared by routes and the scheduler.

Schedules are unattended. They may use durable bindings stored on the schedule,
but they never receive the manual intake prompt shown for an on-demand run.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .graph import GraphValidationError, normalize_graph


def local_timezone_name() -> str:
    """Return a stable IANA name for the host timezone, falling back to UTC."""
    candidates: list[str] = []
    configured = os.environ.get("TZ", "").strip()
    if configured:
        candidates.append(configured)
    try:
        target = Path("/etc/localtime").resolve()
        marker = "zoneinfo/"
        if marker in str(target):
            candidates.append(str(target).split(marker, 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    for candidate in candidates:
        if candidate and timezone_valid(candidate):
            return candidate
    return "UTC"


def timezone_valid(name: str) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def schedule_local_time(now: datetime, timezone_name: str) -> datetime:
    """Project an absolute tick into one schedule's timezone.

    Tests and internal callers may pass a naive wall clock. It is interpreted in
    the schedule timezone for backwards-compatible deterministic seams. The live
    scheduler passes an aware UTC timestamp.
    """
    zone = ZoneInfo(timezone_name)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def current_tick_time() -> datetime:
    return datetime.now(timezone.utc)


def minute_claim_key(local_now: datetime, timezone_name: str) -> str:
    return f"{local_now.strftime('%Y-%m-%dT%H:%M%z')}[{timezone_name}]"


def decode_bindings(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def workflow_input_contract(workflow: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read the canonical trigger intake, with the legacy column as fallback."""
    legacy = workflow.get("inputs")
    if isinstance(legacy, str):
        try:
            legacy = json.loads(legacy)
        except json.JSONDecodeError:
            legacy = []
    legacy_inputs = [dict(item) for item in legacy or [] if isinstance(item, Mapping)]

    raw_graph = workflow.get("graph")
    if raw_graph:
        try:
            graph = normalize_graph(raw_graph)
        except (GraphValidationError, TypeError, ValueError):
            graph = None
        if graph is not None:
            trigger = next(
                (node for node in graph["nodes"] if node.get("type") == "trigger"),
                None,
            )
            if trigger is not None and "inputs" in trigger:
                return [dict(item) for item in trigger.get("inputs") or []]
    return legacy_inputs


def _binding_present(declaration: Mapping[str, Any], bindings: Mapping[str, Any]) -> bool:
    input_id = str(declaration.get("id") or "")
    if input_id not in bindings:
        return False
    value = bindings[input_id]
    kind = declaration.get("kind", "text")
    if kind == "number":
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value.strip())
            except ValueError:
                return False
            return bool(value.strip())
        return False
    return isinstance(value, str) and bool(value.strip())


def unresolved_required_inputs(
    workflow: Mapping[str, Any], bindings: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    values = bindings or {}
    return [
        declaration
        for declaration in workflow_input_contract(workflow)
        if declaration.get("required") and not _binding_present(declaration, values)
    ]


def readiness_payload(
    workflow: Mapping[str, Any], bindings: Mapping[str, Any] | None
) -> dict[str, Any]:
    unresolved = unresolved_required_inputs(workflow, bindings)
    return {
        "ready": not unresolved,
        "unresolved_inputs": [str(item.get("id") or "") for item in unresolved],
        "unresolved_labels": [
            str(item.get("label") or item.get("id") or "required input")
            for item in unresolved
        ],
    }


def missing_sources_detail(
    workflow: Mapping[str, Any], bindings: Mapping[str, Any] | None
) -> dict[str, Any]:
    state = readiness_payload(workflow, bindings)
    labels = ", ".join(state["unresolved_labels"])
    return {
        "code": "schedule_missing_sources",
        "message": (
            f"Cannot enable this schedule because required manual input has no "
            f"durable binding: {labels}. Save a durable binding for each required "
            "input in Schedules, then turn the schedule On."
        ),
        "unresolved_inputs": state["unresolved_inputs"],
    }

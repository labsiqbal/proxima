from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

DESIGN_STUDIO = "design_studio"
WORKFLOW_GRAPH = "workflow_graph"
# Repo jobs run in isolated git worktrees with diff review + local merge
# (Phase-1 slices 2+4, T1). On by default since slice 4 shipped the review UI;
# the switch stays as an owner escape hatch - while off the worktree machinery
# is fully inert and jobs behave exactly as before it existed.
REPO_WORKTREES = "repo_worktrees"

_CONFIG_KEYS = {
    DESIGN_STUDIO: "feature_design_studio",
    WORKFLOW_GRAPH: "feature_workflow_graph",
    REPO_WORKTREES: "feature_repo_worktrees",
}

_DISPLAY_NAMES = {
    DESIGN_STUDIO: "Design Studio",
    WORKFLOW_GRAPH: "Workflow Graph",
    REPO_WORKTREES: "Repo worktrees",
}

_COMMAND_FEATURES = {
    "/design": DESIGN_STUDIO,
    "/image-studio": DESIGN_STUDIO,  # back-compat aliases for /design
    "/design-studio": DESIGN_STUDIO,
}


def enabled(config: Mapping[str, Any] | None, feature: str) -> bool:
    key = _CONFIG_KEYS[feature]
    value = (config or {}).get(key, False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def public_flags(config: Mapping[str, Any] | None) -> dict[str, bool]:
    return {
        DESIGN_STUDIO: enabled(config, DESIGN_STUDIO),
        WORKFLOW_GRAPH: enabled(config, WORKFLOW_GRAPH),
        REPO_WORKTREES: enabled(config, REPO_WORKTREES),
    }


def disabled_payload(feature: str) -> dict[str, str]:
    return {
        "code": "feature_disabled",
        "feature": feature,
        "message": f"{_DISPLAY_NAMES[feature]} is temporarily disabled.",
    }


def require(config: Mapping[str, Any] | None, feature: str) -> None:
    if not enabled(config, feature):
        raise HTTPException(status_code=503, detail=disabled_payload(feature))


def command_feature(message: str | None) -> str | None:
    text = (message or "").strip().lower()
    if not text:
        return None
    token = text.split(maxsplit=1)[0]
    if token.startswith("//"):
        token = token[1:]
    return _COMMAND_FEATURES.get(token)


def require_command(config: Mapping[str, Any] | None, message: str | None) -> None:
    feature = command_feature(message)
    if feature:
        require(config, feature)


def queued_run_feature(run: Mapping[str, Any], session_mode: str) -> str | None:
    if session_mode == "design":
        return DESIGN_STUDIO
    kind = str(run.get("kind") or "")
    if kind in {"wf_node", "workflow_graph_draft"}:
        return WORKFLOW_GRAPH
    if kind == "media_image-studio":
        return DESIGN_STUDIO
    return command_feature(str(run.get("prompt") or ""))

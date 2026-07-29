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
# Durable Master data is migrated independently, while the runtime and product
# surface stay fail-closed until the integrated orchestrator slices ship.
MASTER_ORCHESTRATOR = "master_orchestrator"
# The external updater remains unavailable until its installer enrollment and
# fault/soak evidence are accepted.  This flag gates only app request surfaces;
# it never grants the app authority over updater-owned state.
SAFE_SELF_UPDATE = "safe_self_update"

_CONFIG_KEYS = {
    DESIGN_STUDIO: "feature_design_studio",
    WORKFLOW_GRAPH: "feature_workflow_graph",
    REPO_WORKTREES: "feature_repo_worktrees",
    MASTER_ORCHESTRATOR: "feature_master_orchestrator",
    SAFE_SELF_UPDATE: "feature_safe_self_update",
}

_DISPLAY_NAMES = {
    DESIGN_STUDIO: "Design Studio",
    WORKFLOW_GRAPH: "Workflow Graph",
    REPO_WORKTREES: "Repo worktrees",
    MASTER_ORCHESTRATOR: "Master orchestrator",
    SAFE_SELF_UPDATE: "Safe self-update",
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
        MASTER_ORCHESTRATOR: enabled(config, MASTER_ORCHESTRATOR),
        SAFE_SELF_UPDATE: enabled(config, SAFE_SELF_UPDATE),
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
    if session_mode == "master":
        return MASTER_ORCHESTRATOR
    if session_mode == "design":
        return DESIGN_STUDIO
    kind = str(run.get("kind") or "")
    if kind in {"wf_node", "wf_script_node", "workflow_graph_draft"}:
        return WORKFLOW_GRAPH
    if kind == "media_image-studio":
        return DESIGN_STUDIO
    return command_feature(str(run.get("prompt") or ""))

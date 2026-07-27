"""One-release import compatibility for the former Alpha runtime."""
from __future__ import annotations

from .master_runtime import (
    MASTER_INSTRUCTIONS as ALPHA_INSTRUCTIONS,
    MASTER_MAX_PARALLEL as ALPHA_MAX_PARALLEL,
    MASTER_MAX_TOOL_ROUNDS as ALPHA_MAX_TOOL_ROUNDS,
    MASTER_TOOL_RE as ALPHA_TOOL_RE,
    MasterToolError as AlphaToolError,
    master_capacity as alpha_capacity,
    create_master_plan as create_alpha_plan,
    ensure_master_identity as ensure_alpha_identity,
    execute_tool,
    handle_master_response as handle_alpha_response,
    start_master_job as start_alpha_job,
)

__all__ = [
    "ALPHA_INSTRUCTIONS",
    "ALPHA_MAX_PARALLEL",
    "ALPHA_MAX_TOOL_ROUNDS",
    "ALPHA_TOOL_RE",
    "AlphaToolError",
    "alpha_capacity",
    "create_alpha_plan",
    "ensure_alpha_identity",
    "execute_tool",
    "handle_alpha_response",
    "start_alpha_job",
]

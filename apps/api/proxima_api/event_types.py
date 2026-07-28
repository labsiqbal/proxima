"""Canonical names for durable session-stream events.

The database remains the event source of truth. These registries keep producers
and clients aligned without turning event names into control authority.
"""
from __future__ import annotations

MASTER_TASK_EVENT_TYPES = frozenset(
    {
        "master.task.started",
        "master.task.completed",
        "master.task.failed",
        "master.task.cancelled",
        "master.task.review_ready",
        "master.task.blocked",
    }
)

MASTER_SUPERVISION_EVENT_TYPES = frozenset(
    {
        "master.attention.required",
        "master.supervisor.outcome",
        "master.satpam.steered",
        "master.satpam.restart_queued",
        "master.satpam.restarted",
        "master.satpam.recovery_failed",
        "master.satpam.escalated",
    }
)

MASTER_PROJECTION_EVENT_TYPES = (
    MASTER_TASK_EVENT_TYPES | MASTER_SUPERVISION_EVENT_TYPES
)

MASTER_FOCUS_EVENT_TYPES = frozenset({"master.focus.changed"})

GRAPH_STATE_EVENT_TYPES = frozenset(
    {
        "graph.state.missing",
        "graph.state.queued",
        "graph.state.building",
        "graph.state.fresh",
        "graph.state.stale",
        "graph.state.failed",
    }
)

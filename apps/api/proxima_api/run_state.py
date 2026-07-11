from __future__ import annotations


def active_run_clause(alias: str | None = None) -> str:
    """SQL predicate for runs that should still count as actively in flight.

    Queued runs only use created_at; running runs prefer heartbeat_at so orphaned
    rows age out consistently across sidebar, dashboard, and debug surfaces.
    """
    prefix = f"{alias}." if alias else ""
    return (
        "("
        f"({prefix}status = 'running' AND COALESCE({prefix}heartbeat_at, {prefix}started_at, {prefix}created_at) >= datetime('now', ?)) "
        f"OR ({prefix}status = 'queued' AND {prefix}created_at >= datetime('now', ?))"
        ")"
    )


def stale_params(seconds: int) -> tuple[str, str]:
    window = f"-{int(seconds)} seconds"
    return (window, window)

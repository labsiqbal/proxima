"""Key/value app settings store, backed by the `app_settings` table.

Single-user cockpit: settings are owner-scoped (one row per key). Values are
strings; structured values are stored as JSON. API keys live here in plaintext:
the owner session protects the API, while the DB itself relies on the server-user
filesystem boundary under ~/.local/share. This is the same host trust boundary as
the provider config files Proxima reads.
"""
from __future__ import annotations

import json
from typing import Any


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (key, value),
    )


def get_json(conn, key: str, default: Any = None) -> Any:
    raw = get_setting(conn, key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def set_json(conn, key: str, value: Any) -> None:
    set_setting(conn, key, json.dumps(value))


# ── run execution settings ─────────────────────────────────────────────────

RUN_TIMEOUT_KEY = "run_timeout_seconds"
# Sanity bounds for the owner-set per-turn quota (T5): floor keeps a typo from
# making every run die instantly; ceiling keeps a run from wedging the worker
# for hours. The T5 envelope is "default 15 min, raiseable to ~30"; 2h is the
# hard stop for exotic setups.
RUN_TIMEOUT_MIN_SECONDS = 60
RUN_TIMEOUT_MAX_SECONDS = 7200


def get_run_timeout_seconds(conn, config: dict[str, Any] | None = None) -> int:
    """The effective per-turn quota: the in-app setting when set and sane,
    else the config/env value, else 900s. DB-backed so it applies identically
    to every entrypoint (scripts/serve.py AND plain `uvicorn proxima_api.main:app`)."""
    raw = get_setting(conn, RUN_TIMEOUT_KEY)
    if raw is not None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = None
        if value is not None and RUN_TIMEOUT_MIN_SECONDS <= value <= RUN_TIMEOUT_MAX_SECONDS:
            return value
    try:
        fallback = int((config or {}).get("run_timeout_seconds") or 900)
    except (TypeError, ValueError):
        fallback = 900
    return fallback if fallback > 0 else 900


def set_run_timeout_seconds(conn, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (
        RUN_TIMEOUT_MIN_SECONDS <= value <= RUN_TIMEOUT_MAX_SECONDS
    ):
        raise ValueError(
            f"run_timeout_seconds must be an integer between {RUN_TIMEOUT_MIN_SECONDS} and {RUN_TIMEOUT_MAX_SECONDS}"
        )
    set_setting(conn, RUN_TIMEOUT_KEY, str(value))
    return value


def get_continuation_limit(config: dict[str, Any] | None) -> int:
    """Max automatic timeout continuations per job turn chain (config-only, T5 default 5)."""
    try:
        return max(0, int((config or {}).get("run_continuation_limit", 5)))
    except (TypeError, ValueError):
        return 5


# ── image-generation config ────────────────────────────────────────────────

IMAGE_GEN_KEY = "image_gen"
HIGGSFIELD_KEY = "higgsfield"
COLLABORATION_BRAINSTORM_AGENTS_KEY = "collaboration_brainstorm_agents"
COLLABORATION_DEBATE_ROUNDS_KEY = "collaboration_debate_rounds"


def _choice_int(raw: str | None, default: int, allowed: tuple[int, ...]) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return value if value in allowed else default


def get_collaboration_settings(conn) -> dict[str, int]:
    return {
        "brainstorm_agents": _choice_int(get_setting(conn, COLLABORATION_BRAINSTORM_AGENTS_KEY), 3, (2, 3)),
        "debate_rounds": _choice_int(get_setting(conn, COLLABORATION_DEBATE_ROUNDS_KEY), 2, (2, 3, 4)),
    }


def set_collaboration_settings(conn, brainstorm_agents: int, debate_rounds: int) -> dict[str, int]:
    if brainstorm_agents not in (2, 3):
        raise ValueError("brainstorm_agents must be 2 or 3")
    if debate_rounds not in (2, 3, 4):
        raise ValueError("debate_rounds must be 2, 3, or 4")
    set_setting(conn, COLLABORATION_BRAINSTORM_AGENTS_KEY, str(brainstorm_agents))
    set_setting(conn, COLLABORATION_DEBATE_ROUNDS_KEY, str(debate_rounds))
    return {"brainstorm_agents": brainstorm_agents, "debate_rounds": debate_rounds}


def get_image_gen_config(conn) -> dict[str, Any]:
    """The saved image-gen provider config, or None if unset."""
    return get_json(conn, IMAGE_GEN_KEY)

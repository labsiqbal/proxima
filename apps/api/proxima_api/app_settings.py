"""Key/value app settings store, backed by the `app_settings` table.

Single-user cockpit: settings are owner-scoped (one row per key). Values are
strings; structured values are stored as JSON. API keys live here in plaintext:
the owner session protects the API, while the DB itself relies on the server-user
filesystem boundary under ~/.local/share. This is the same host trust boundary as
the provider config files Proxima reads.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


# ── satpam supervision settings (Phase-1 slice 12, T10) ────────────────────

SATPAM_STALL_TURNS_KEY = "satpam_stall_turns"
SATPAM_CHECK_SECONDS_KEY = "satpam_check_seconds"
# Conservative defaults (T10 #6): N=2 consecutive no-progress continuation
# turns before the satpam acts; one evaluation sweep per minute. Bounds keep a
# typo from making the watchman hyperactive (N=0 would flag every turn) or
# blind (a day-long cadence supervises nothing).
SATPAM_STALL_TURNS_DEFAULT = 2
SATPAM_STALL_TURNS_MIN = 1
SATPAM_STALL_TURNS_MAX = 10
SATPAM_CHECK_SECONDS_DEFAULT = 60
SATPAM_CHECK_SECONDS_MIN = 15
SATPAM_CHECK_SECONDS_MAX = 3600


def _bounded_int(raw: str | None, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    return value if lo <= value <= hi else default


def get_satpam_settings(conn) -> dict[str, int]:
    """The satpam's tunable thresholds: N (consecutive no-progress continuation
    turns before it acts) and the sweep cadence in seconds."""
    return {
        "stall_turns": _bounded_int(
            get_setting(conn, SATPAM_STALL_TURNS_KEY),
            SATPAM_STALL_TURNS_DEFAULT, SATPAM_STALL_TURNS_MIN, SATPAM_STALL_TURNS_MAX,
        ),
        "check_seconds": _bounded_int(
            get_setting(conn, SATPAM_CHECK_SECONDS_KEY),
            SATPAM_CHECK_SECONDS_DEFAULT, SATPAM_CHECK_SECONDS_MIN, SATPAM_CHECK_SECONDS_MAX,
        ),
    }


def set_satpam_settings(conn, stall_turns: int, check_seconds: int) -> dict[str, int]:
    if not isinstance(stall_turns, int) or isinstance(stall_turns, bool) or not (
        SATPAM_STALL_TURNS_MIN <= stall_turns <= SATPAM_STALL_TURNS_MAX
    ):
        raise ValueError(
            f"stall_turns must be an integer between {SATPAM_STALL_TURNS_MIN} and {SATPAM_STALL_TURNS_MAX}"
        )
    if not isinstance(check_seconds, int) or isinstance(check_seconds, bool) or not (
        SATPAM_CHECK_SECONDS_MIN <= check_seconds <= SATPAM_CHECK_SECONDS_MAX
    ):
        raise ValueError(
            f"check_seconds must be an integer between {SATPAM_CHECK_SECONDS_MIN} and {SATPAM_CHECK_SECONDS_MAX}"
        )
    set_setting(conn, SATPAM_STALL_TURNS_KEY, str(stall_turns))
    set_setting(conn, SATPAM_CHECK_SECONDS_KEY, str(check_seconds))
    return {"stall_turns": stall_turns, "check_seconds": check_seconds}


# ── Alpha orchestration settings ─────────────────────────────────────────

ALPHA_UNATTENDED_KEY = "alpha.unattended"
ALPHA_BUDGET_TURNS_KEY = "alpha.budget.turns"
ALPHA_BUDGET_WALL_SECONDS_KEY = "alpha.budget.wall_seconds"
ALPHA_BUDGET_TOKENS_KEY = "alpha.budget.tokens_optional"
ALPHA_TOUR_CORE_DONE_KEY = "alpha.tour.core_done"
ALPHA_BUDGET_TURNS_DEFAULT = 20
ALPHA_BUDGET_WALL_SECONDS_DEFAULT = 14_400
ALPHA_BUDGET_TURNS_MIN = 1
ALPHA_BUDGET_TURNS_MAX = 200
ALPHA_BUDGET_WALL_SECONDS_MIN = 300
ALPHA_BUDGET_WALL_SECONDS_MAX = 86_400
ALPHA_BUDGET_TOKENS_MAX = 10_000_000


def get_alpha_settings(conn) -> dict[str, Any]:
    token_raw = get_setting(conn, ALPHA_BUDGET_TOKENS_KEY)
    try:
        tokens = int(token_raw) if token_raw not in (None, "") else None
    except (TypeError, ValueError):
        tokens = None
    return {
        "unattended": get_setting(conn, ALPHA_UNATTENDED_KEY, "0") == "1",
        "budget_turns": _bounded_int(
            get_setting(conn, ALPHA_BUDGET_TURNS_KEY),
            ALPHA_BUDGET_TURNS_DEFAULT,
            ALPHA_BUDGET_TURNS_MIN,
            ALPHA_BUDGET_TURNS_MAX,
        ),
        "budget_wall_seconds": _bounded_int(
            get_setting(conn, ALPHA_BUDGET_WALL_SECONDS_KEY),
            ALPHA_BUDGET_WALL_SECONDS_DEFAULT,
            ALPHA_BUDGET_WALL_SECONDS_MIN,
            ALPHA_BUDGET_WALL_SECONDS_MAX,
        ),
        "budget_tokens": tokens if tokens is not None and 1 <= tokens <= ALPHA_BUDGET_TOKENS_MAX else None,
        "tour_core_done": get_setting(conn, ALPHA_TOUR_CORE_DONE_KEY, "0") == "1",
    }


def set_alpha_settings(
    conn,
    *,
    unattended: bool | None = None,
    budget_turns: int | None = None,
    budget_wall_seconds: int | None = None,
    budget_tokens: int | None | object = ...,
    tour_core_done: bool | None = None,
) -> dict[str, Any]:
    if budget_turns is not None and (
        not isinstance(budget_turns, int)
        or isinstance(budget_turns, bool)
        or not ALPHA_BUDGET_TURNS_MIN <= budget_turns <= ALPHA_BUDGET_TURNS_MAX
    ):
        raise ValueError(
            f"budget_turns must be between {ALPHA_BUDGET_TURNS_MIN} and {ALPHA_BUDGET_TURNS_MAX}"
        )
    if budget_wall_seconds is not None and (
        not isinstance(budget_wall_seconds, int)
        or isinstance(budget_wall_seconds, bool)
        or not ALPHA_BUDGET_WALL_SECONDS_MIN <= budget_wall_seconds <= ALPHA_BUDGET_WALL_SECONDS_MAX
    ):
        raise ValueError(
            "budget_wall_seconds must be between "
            f"{ALPHA_BUDGET_WALL_SECONDS_MIN} and {ALPHA_BUDGET_WALL_SECONDS_MAX}"
        )
    if budget_tokens is not ... and budget_tokens is not None and (
        not isinstance(budget_tokens, int)
        or isinstance(budget_tokens, bool)
        or not 1 <= budget_tokens <= ALPHA_BUDGET_TOKENS_MAX
    ):
        raise ValueError(f"budget_tokens must be between 1 and {ALPHA_BUDGET_TOKENS_MAX}, or empty")
    if unattended is not None:
        set_setting(conn, ALPHA_UNATTENDED_KEY, "1" if unattended else "0")
        if unattended:
            set_setting(conn, "alpha.budget.started_at", datetime.now(timezone.utc).isoformat())
            set_setting(conn, "alpha.budget.turns_used", "0")
        else:
            set_setting(conn, "alpha.budget.started_at", "")
    if budget_turns is not None:
        set_setting(conn, ALPHA_BUDGET_TURNS_KEY, str(budget_turns))
    if budget_wall_seconds is not None:
        set_setting(conn, ALPHA_BUDGET_WALL_SECONDS_KEY, str(budget_wall_seconds))
    if budget_tokens is not ...:
        set_setting(conn, ALPHA_BUDGET_TOKENS_KEY, "" if budget_tokens is None else str(budget_tokens))
    if tour_core_done is not None:
        set_setting(conn, ALPHA_TOUR_CORE_DONE_KEY, "1" if tour_core_done else "0")
    return get_alpha_settings(conn)


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

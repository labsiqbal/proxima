"""Key/value app settings store, backed by the `app_settings` table.

Single-user cockpit: settings are owner-scoped (one row per key). Values are
strings; structured values are stored as JSON. API keys live here in plaintext,
which is acceptable because the access gate is the network (loopback / Cloudflare
Access) and the DB file is owner-owned under ~/.local/share — the same trust
boundary as the NINEROUTER env-file already in use.
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


# ── image-generation config ────────────────────────────────────────────────

IMAGE_GEN_KEY = "image_gen"
VIDEO_GEN_KEY = "video_gen"
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

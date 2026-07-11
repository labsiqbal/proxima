from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

VIDEO = "video"
DESIGN_STUDIO = "design_studio"

_CONFIG_KEYS = {
    VIDEO: "feature_video",
    DESIGN_STUDIO: "feature_design_studio",
}

_DISPLAY_NAMES = {
    VIDEO: "Video",
    DESIGN_STUDIO: "Design Studio",
}

_COMMAND_FEATURES = {
    "/video": VIDEO,
    "/video-studio": VIDEO,
    "/image-studio": DESIGN_STUDIO,
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
        VIDEO: enabled(config, VIDEO),
        DESIGN_STUDIO: enabled(config, DESIGN_STUDIO),
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
    if kind in {"media_video", "media_video-studio"}:
        return VIDEO
    if kind == "media_image-studio":
        return DESIGN_STUDIO
    return command_feature(str(run.get("prompt") or ""))

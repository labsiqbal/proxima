"""Shared media-provider settings resolution.

The active image / higgsfield provider config (from app_settings, with defaults)
is needed by both the files routes and the design
routes. Keep the resolution in one place so the defaults can't drift between them.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import app_settings
from . import higgsfield
from . import image_providers
from . import video_providers


def resolve_image_gen(conn: sqlite3.Connection) -> dict[str, Any]:
    """Active image provider config from Settings; defaults to codex (no key)."""
    cfg = app_settings.get_json(conn, app_settings.IMAGE_GEN_KEY)
    if cfg and isinstance(cfg, dict) and cfg.get("provider") in image_providers.IMAGE_PROVIDER_IDS:
        return cfg
    return {"provider": image_providers.DEFAULT_PROVIDER, "apiKey": None, "baseUrl": None, "model": None}


def unavailable_provider_note(
    stored: Any, valid_ids: tuple[str, ...], default_id: str
) -> str | None:
    """Actionable note when the saved provider no longer exists (else None).

    Retiring a provider (e.g. the xai-oauth media provider) must not break an
    install that had it selected: `resolve_*` falls back to the default so
    generation keeps working. The owner's stored row is deliberately *not*
    rewritten - their choice is data, not ours to edit - so the Settings card
    tells them what happened and asks them to pick a replacement.
    """
    if not isinstance(stored, dict):
        return None
    provider_id = stored.get("provider")
    if not isinstance(provider_id, str) or not provider_id or provider_id in valid_ids:
        return None
    return (
        f'The saved provider "{provider_id}" is no longer available, so requests use '
        f'"{default_id}". Pick a provider and save to update your configuration.'
    )


def resolve_video_gen(conn: sqlite3.Connection) -> dict[str, Any]:
    """Active video provider config from Settings.

    Unlike image generation there is no key-less fallback: video always needs an
    endpoint + key, so an unconfigured install resolves to the openai-compatible
    provider with its default base URL and no credentials.
    """
    cfg = app_settings.get_json(conn, app_settings.VIDEO_GEN_KEY)
    if cfg and isinstance(cfg, dict) and cfg.get("provider") in video_providers.VIDEO_PROVIDER_IDS:
        return cfg
    spec = video_providers.get_provider(video_providers.DEFAULT_PROVIDER)
    return {
        "provider": spec.id,
        "apiKey": None,
        "baseUrl": spec.default_base_url or None,
        "model": None,
    }


def resolve_higgsfield_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg = app_settings.get_json(conn, app_settings.HIGGSFIELD_KEY)
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "imagePolicy": cfg.get("imagePolicy") or "zero-credit-only",
        "imageModel": cfg.get("imageModel") or higgsfield.DEFAULT_IMAGE_MODEL,
    }

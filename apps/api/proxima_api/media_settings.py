"""Shared media-provider settings resolution.

The active image / higgsfield provider config (from app_settings, with defaults)
is needed by both the files routes (video + image-gen settings) and the design
routes. Keep the resolution in one place so the defaults can't drift between them.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import app_settings
from . import higgsfield
from . import image_providers


def resolve_image_gen(conn: sqlite3.Connection) -> dict[str, Any]:
    """Active image provider config from Settings; defaults to codex (no key)."""
    cfg = app_settings.get_json(conn, app_settings.IMAGE_GEN_KEY)
    if cfg and isinstance(cfg, dict) and cfg.get("provider") in image_providers.IMAGE_PROVIDER_IDS:
        return cfg
    return {"provider": image_providers.DEFAULT_PROVIDER, "apiKey": None, "baseUrl": None, "model": None}


def resolve_higgsfield_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg = app_settings.get_json(conn, app_settings.HIGGSFIELD_KEY)
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "imagePolicy": cfg.get("imagePolicy") or "zero-credit-only",
        "imageModel": cfg.get("imageModel") or higgsfield.DEFAULT_IMAGE_MODEL,
        "videoPolicy": cfg.get("videoPolicy") or "confirm-credits",
        "videoModel": cfg.get("videoModel") or higgsfield.DEFAULT_VIDEO_MODEL,
        "maxVideoCredits": cfg.get("maxVideoCredits") if isinstance(cfg.get("maxVideoCredits"), (int, float)) else 50,
    }

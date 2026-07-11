"""Health, setup status, and auth (auto-login/logout/me) routes for the Proxima
OS API. Single-user cockpit: no login wall, no team bootstrap.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Header
from fastapi.responses import JSONResponse

from ..auth import hash_token
from ..runners import runner_readiness

logger = logging.getLogger("proxima.api")


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    current_user_strict_token = deps["current_user_strict_token"]
    visible_project = deps["visible_project"]
    create_token = deps["create_token"]
    public_user = deps["public_user"]
    ensure_single_user_owner = deps["ensure_single_user_owner"]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/setup/status")
    def setup_status() -> dict[str, Any]:
        # Single-user cockpit: the frontend auto-logs-in via /auth/auto, so there is
        # never a bootstrap wall. Runner list lets the UI show what's installed.
        readiness = runner_readiness()
        return {
            "bootstrap_required": False,
            "single_user": True,
            "mode": "single",
            "hermes_profiles_root": cfg["hermes_profiles_root"],
            "runners": [{"id": r["id"], "displayName": r["displayName"], "installed": r["installed"]} for r in readiness.values()],
        }

    @app.post("/auth/auto")
    def auth_auto():
        """Single-user cockpit auto-login: no credentials, returns the owner + a
        token (for SSE/WS/preview URLs that can't send an auth header).

        Also mints an HttpOnly session cookie (proxima_session) so SSE/WS can
        authenticate without carrying the token in the URL (?token=) — the first
        step of moving auth off URL/localStorage. Additive: the JSON token still
        flows for the existing Bearer-header path."""
        user = ensure_single_user_owner()
        with app.state.db_lock:
            token = create_token(user["id"])
        resp = JSONResponse({"token": token, "user": public_user(user)})
        ttl = int(cfg.get("auth_token_ttl_hours") or 24 * 14) * 3600
        resp.set_cookie("proxima_session", token, path="/", httponly=True,
                        samesite="lax", secure=True, max_age=ttl)
        return resp

    @app.post("/auth/logout")
    def logout(user: dict[str, Any] = Depends(current_user), authorization: str | None = Header(default=None)):
        if authorization:
            db().execute("UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ?", (hash_token(authorization.removeprefix("Bearer ").strip()),))
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict[str, Any] = Depends(current_user_strict_token)):
        return public_user(user)

    # NOTE: the advisory command-policy classifier + POST /api/policy/command/check
    # endpoint were removed. They never gated real agent/tool execution (the agent
    # runs its own shell inside the runner CLI, not through this API), so they gave a
    # false impression of a guard. The access boundary is network reachability
    # (single-user, network-gated). See docs/security-boundaries.md.

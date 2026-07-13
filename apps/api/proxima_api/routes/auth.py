"""Health, setup status, and auth (auto-login/logout/me) routes for the Proxima
OS API. Single-user cockpit: no login wall, no team bootstrap.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import hash_password, hash_token, iso_now, verify_password
from ..runners import runner_readiness
from ..schemas import PasswordRequest

_SESSION_MAX_AGE = 10 * 365 * 24 * 3600  # persistent cookie (DB session itself never expires)

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
        # Single-user cockpit. `password_set` tells the frontend whether to show the
        # first-run "set a password" screen, the login screen, or (passwordless) the
        # auto-login path. Runner list lets the UI show what's installed.
        owner = ensure_single_user_owner()
        readiness = runner_readiness()
        return {
            "bootstrap_required": False,
            "single_user": True,
            "mode": "single",
            "password_set": bool(owner.get("password_hash")),
            "hermes_profiles_root": cfg["hermes_profiles_root"],
            "runners": [{"id": r["id"], "displayName": r["displayName"], "installed": r["installed"]} for r in readiness.values()],
        }

    def _session_response(request: Request, user: dict[str, Any], token: str) -> JSONResponse:
        # Secure only over https (incl. behind a tunnel via X-Forwarded-Proto) so the
        # cookie is actually sent on plain-http localhost dev — otherwise SSE/WS,
        # which rely on this cookie instead of a ?token= URL, wouldn't authenticate.
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp = JSONResponse({"token": token, "user": public_user(user)})
        resp.set_cookie("proxima_session", token, path="/", httponly=True,
                        samesite="lax", secure=secure, max_age=_SESSION_MAX_AGE)
        return resp

    @app.post("/auth/set-password")
    def set_password(request: Request, payload: PasswordRequest):
        """First-run: set the owner's password. Only allowed while none is set (later
        changes go through Settings with the current password). Logs you in on success."""
        user = ensure_single_user_owner()
        if user.get("password_hash"):
            raise HTTPException(status_code=409, detail="password already set")
        try:
            digest = hash_password(payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with app.state.db_lock:
            db().execute("UPDATE users SET password_hash = ?, password_set_at = ? WHERE id = ?", (digest, iso_now(), user["id"]))
            token = create_token(user["id"])
        return _session_response(request, user, token)

    @app.post("/auth/login")
    def login_with_password(request: Request, payload: PasswordRequest):
        """Verify the owner's password and start a session (no expiry until logout)."""
        user = ensure_single_user_owner()
        if not user.get("password_hash"):
            raise HTTPException(status_code=409, detail="no password set")
        if not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="incorrect password")
        with app.state.db_lock:
            token = create_token(user["id"])
        return _session_response(request, user, token)

    @app.post("/auth/auto")
    def auth_auto(request: Request):
        """Passwordless auto-login (network-only mode). Disabled once a password is
        set — clients must then use /auth/login. Mints an HttpOnly session cookie so
        SSE/WS don't need the token in the URL."""
        user = ensure_single_user_owner()
        if user.get("password_hash"):
            raise HTTPException(status_code=401, detail="login required")
        with app.state.db_lock:
            token = create_token(user["id"])
        return _session_response(request, user, token)

    @app.post("/auth/logout")
    def logout(request: Request, user: dict[str, Any] = Depends(current_user), authorization: str | None = Header(default=None)):
        # Revoke the current session by whichever it was presented as — bearer token
        # OR the proxima_session cookie (the cookie is the persistent auth now) — and
        # clear the cookie so a reload lands on the login screen.
        token = authorization.removeprefix("Bearer ").strip() if authorization else ""
        token = token or request.cookies.get("proxima_session", "")
        if token:
            db().execute("UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ?", (hash_token(token),))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("proxima_session", path="/")
        return resp

    @app.get("/api/me")
    def me(user: dict[str, Any] = Depends(current_user_strict_token)):
        return public_user(user)

    # NOTE: the advisory command-policy classifier + POST /api/policy/command/check
    # endpoint were removed. They never gated real agent/tool execution (the agent
    # runs its own shell inside the runner CLI, not through this API), so they gave a
    # false impression of a guard. The access boundary is network reachability
    # (single-user, network-gated). See docs/security-boundaries.md.

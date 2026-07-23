"""Auth/readiness health checks surfaced on the Home dashboard.

Checks whether the things the owner actually works with are ready *before* work
starts: the selected image-generation provider (OAuth tokens, CLI logins)
and every runner referenced by a profile. Checks shell out to CLIs and probe
HTTP endpoints, so they never run on the request path — the dashboard returns
the cached snapshot and kicks a background refresh when it is stale.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import app_settings, image_providers
from .db import connect
from .runners import hermes_status, runner_readiness

REFRESH_SECONDS = 60.0

_lock = threading.Lock()
_snapshot: dict[str, Any] | None = None
_refreshed_at: float = 0.0
_refreshing = False


def reset() -> None:
    """Drop the cache (tests)."""
    global _snapshot, _refreshed_at, _refreshing
    with _lock:
        _snapshot = None
        _refreshed_at = 0.0
        _refreshing = False


def invalidate() -> None:
    """Mark the cache stale (e.g. after a provider-settings change) so the next
    dashboard poll re-checks immediately. Keeps the old snapshot visible while
    the refresh runs — no "checking" flash."""
    global _refreshed_at
    with _lock:
        _refreshed_at = 0.0


def _check(check_id: str, area: str, label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "area": area, "label": label, "ok": bool(ok), "detail": detail}


def _media_checks(conn) -> list[dict[str, Any]]:
    """Only the providers currently selected in Settings — unselected ones are noise."""
    checks: list[dict[str, Any]] = []
    icfg = app_settings.get_json(conn, app_settings.IMAGE_GEN_KEY) or {}
    if not isinstance(icfg, dict):
        icfg = {}
    iprovider = image_providers.get_provider(icfg.get("provider"))
    try:
        result = image_providers.test_connection(iprovider.id, icfg.get("apiKey"), base_url=icfg.get("baseUrl"))
    except Exception as exc:  # a broken checker must not take down the snapshot
        result = {"ok": False, "detail": f"Check failed: {exc}"}
    iok = bool(result.get("ok", result.get("ready", False)))
    checks.append(_check(f"image:{iprovider.id}", "image", f"Image generation · {iprovider.display_name}",
                         iok, str(result.get("detail") or ("Ready." if iok else "Connection test failed."))))
    return checks


def _runner_checks(conn) -> list[dict[str, Any]]:
    """Runners referenced by at least one profile. Installed check for all; deeper
    auth check where one exists (Hermes/Grok home credentials, Codex login)."""
    used = [r["runner_id"] for r in conn.execute("SELECT DISTINCT runner_id FROM profiles ORDER BY runner_id").fetchall()]
    readiness = runner_readiness()
    checks: list[dict[str, Any]] = []
    for rid in used:
        info = readiness.get(rid)
        if not info:
            continue
        label = f"Runner · {info.get('displayName') or rid}"
        if not info.get("installed"):
            checks.append(_check(f"runner:{rid}", "runner", label, False,
                                 info.get("authHint") or f"{info.get('displayName') or rid} CLI is not installed on the Proxima server."))
            continue
        ok = bool(info.get("ready"))
        detail = "Ready." if ok else str(info.get("authHint") or f"{info.get('displayName') or rid} is not authenticated.")
        try:
            if rid == "hermes":
                st = hermes_status()
                ok = bool(st.get("ready"))
                detail = "Ready." if ok else (st.get("guidance") or "Hermes is not ready.")
            elif rid == "codex":
                st = image_providers.codex_ready()
                ok = bool(st.get("ready"))
                detail = st.get("detail") or ("Ready." if ok else "Codex is not logged in.")
        except Exception as exc:
            ok, detail = False, f"Check failed: {exc}"
        checks.append(_check(f"runner:{rid}", "runner", label, ok, detail))
    return checks


def _refresh(database_path: str) -> None:
    global _snapshot, _refreshed_at, _refreshing
    try:
        conn = connect(database_path)
        try:
            checks = _media_checks(conn) + _runner_checks(conn)
        finally:
            conn.close()
        snap = {
            "status": "ok" if all(c["ok"] for c in checks) else "error",
            "checks": checks,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }
        with _lock:
            _snapshot = snap
            _refreshed_at = time.time()
    except Exception:
        logging.getLogger("proxima.auth_health").exception("auth health refresh failed")
        with _lock:
            _refreshed_at = time.time()  # don't hot-loop a persistently failing refresh
    finally:
        with _lock:
            _refreshing = False


def snapshot(database_path: str, *, enabled: bool = True) -> dict[str, Any]:
    """Latest cached snapshot, kicking a background refresh when stale.

    Never blocks: the first call returns {"status": "checking"} and the Home
    poll picks up the real result a few seconds later.
    """
    global _refreshing
    kick = False
    with _lock:
        if enabled and not _refreshing and time.time() - _refreshed_at > REFRESH_SECONDS:
            _refreshing = True
            kick = True
        snap = _snapshot
    if kick:
        # Outside the lock: _refresh re-acquires it, and the lock is not reentrant.
        threading.Thread(target=_refresh, args=(database_path,), daemon=True, name="auth-health-refresh").start()
    return snap if snap is not None else {"status": "checking", "checks": []}

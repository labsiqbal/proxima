from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import hash_token, iso_now
from .db import connect, init_db
from .migrations import run_migrations
from .acp import AcpManager
from .apprunner import AppManager
from .preview_proxy import PreviewProxyMiddleware
from .settings import DEFAULT_CONFIG, hermes_home_for, normalize_config
from .updates import (
    UPDATE_CHECK_INTERVAL_SECONDS,
    UPDATE_FIRST_CHECK_DELAY_SECONDS,
    UpdateManager,
    read_local_version,
)
from .provisioning import backfill
from .event_hub import EventHub
from .frontend_static import register_frontend
from .features import public_flags
from .route_deps import build_route_deps
from .worker import RunWorker
from .scheduler import _scheduler_tick, archive_old_jobs
from .routes import (
    admin as routes_admin,
    auth as routes_auth,
    chat as routes_chat,
    design as routes_design,
    files as routes_files,
    profiles as routes_profiles,
    projects as routes_projects,
    reviews as routes_reviews,
    tasks as routes_tasks,
    update as routes_update,
    wiki as routes_wiki,
    work as routes_work,
)

logger = logging.getLogger("proxima.api")


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    cfg = normalize_config(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.hub.bind_loop(asyncio.get_running_loop())
        if cfg.get("auto_provision", True):
            try:
                backfill(app.state.db, cfg)
            except Exception:
                logging.getLogger("proxima.provisioning").exception("startup backfill failed")
        try:
            with app.state.db_lock:
                archive_old_jobs(app.state.db, int(cfg.get("job_archive_days", 30)))
        except Exception:
            logging.getLogger("proxima.api").exception("job archive sweep failed (non-fatal)")
        worker = app.state.worker
        # Reclaim runs orphaned by a previous shutdown: a run left in 'running'
        # had in-memory ACP state that's now gone, so it can never complete.
        # Mark it failed (and emit a terminal event) instead of leaving it stuck.
        try:
            with app.state.db_lock:
                orphaned = [dict(r) for r in app.state.worker_db.execute(
                    "SELECT id, session_id, project_id FROM runs WHERE status = 'running'"
                ).fetchall()]
            for r in orphaned:
                worker._fail_interrupted(r["id"], r["session_id"], r["project_id"], "Interrupted by server restart")
            worker.reap_orphaned_jobs()
        except Exception:
            logging.getLogger("proxima.worker").exception("orphaned run cleanup failed")
        if cfg.get("start_worker", True):
            worker.start()
        scheduler_task: asyncio.Task | None = None
        if cfg.get("start_worker", True) and cfg.get("start_scheduler", True):
            async def _scheduler_loop() -> None:
                while True:
                    # Align to the top of each minute. A fixed sleep(60) measured from
                    # the end of the previous tick drifts forward (tick work + scheduling
                    # jitter), eventually skipping a whole wall-clock minute so a cron
                    # set for that minute never fires. Sleeping to the next :00 keeps
                    # every minute sampled exactly once.
                    now = datetime.now()
                    await asyncio.sleep(max(1.0, 60 - now.second - now.microsecond / 1_000_000))
                    try:
                        _scheduler_tick(app)
                    except Exception:
                        logging.getLogger("proxima.scheduler").exception("scheduler tick failed")
            scheduler_task = asyncio.create_task(_scheduler_loop())
        app.state.updates.reconcile_marker()  # finalize a marker left by a self-update restart
        update_task: asyncio.Task | None = None
        if cfg.get("update_check", True):
            async def _update_check_loop() -> None:
                await asyncio.sleep(UPDATE_FIRST_CHECK_DELAY_SECONDS)
                while True:
                    try:
                        await app.state.updates.check_now()  # contract: never raises
                    except Exception:
                        logging.getLogger("proxima.updates").exception("update check loop tick failed")
                    await asyncio.sleep(UPDATE_CHECK_INTERVAL_SECONDS)
            update_task = asyncio.create_task(_update_check_loop())
        yield
        if scheduler_task:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        if update_task:
            update_task.cancel()
            with suppress(asyncio.CancelledError):
                await update_task
        await worker.stop()
        await app.state.acp_manager.shutdown()
        await app.state.app_manager.shutdown()

    app = FastAPI(title="Proxima API", version=read_local_version(), lifespan=lifespan)
    app.state.config = cfg
    app.state.db = connect(cfg["database_path"])
    app.state.db_lock = __import__("threading").RLock()
    init_db(app.state.db, cfg.get("seed_users") or [], lambda username, slug: hermes_home_for(cfg, username, slug), source_hermes_home=cfg.get("source_hermes_home"))
    run_migrations(app.state.db, cfg.get("database_path"))  # versioned migrations (backs up before applying)
    app.state.worker_db = connect(cfg["database_path"])  # dedicated connection for the async run worker
    app.state.worker = RunWorker(app)
    app.state.acp_manager = AcpManager()
    app.state.app_manager = AppManager()
    app.state.login_attempts = {}  # ip -> [monotonic timestamps] for login throttling
    app.state.hub = EventHub()
    app.state.updates = UpdateManager(cfg)

    register_frontend(
        app,
        cfg.get("web_dist_path"),
        cfg.get("env_name"),
        static_files_cls=StaticFiles,
        file_response=FileResponse,
        html_response=HTMLResponse,
    )

    # Per-thread connection. Sync request handlers run across FastAPI's threadpool, so a
    # single shared connection would let concurrent cursor use raise / corrupt. One WAL
    # connection per thread lets reads + writes run safely in parallel (writes serialized
    # by SQLite's own lock + busy_timeout). The async/event-loop thread shares its own one.
    _db_local = __import__("threading").local()

    def db():
        conn = getattr(_db_local, "conn", None)
        if conn is None:
            conn = connect(cfg["database_path"])
            _db_local.conn = conn
        return conn

    _route_deps = build_route_deps(
        app,
        cfg,
        db,
        depends=Depends,
        header=Header,
        http_exception=HTTPException,
        status_module=status,
    )
    current_user = _route_deps["current_user"]

    @app.get("/api/health", include_in_schema=False)
    def health():
        try:
            db().execute("SELECT 1").fetchone()
        except Exception as exc:
            logger.exception("health check failed")
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {
            "ok": True,
            "product": "proxima",
            "service": "proxima",
            "version": app.version,
            "database": "ok",
            "worker": "enabled" if cfg.get("start_worker", True) else "disabled",
        }

    routes_tasks.register(app, _route_deps)
    routes_work.register(app, _route_deps)
    routes_profiles.register(app, _route_deps)
    routes_projects.register(app, _route_deps)
    routes_files.register(app, _route_deps)
    routes_design.register(app, _route_deps)
    routes_wiki.register(app, _route_deps)
    routes_admin.register(app, _route_deps)
    routes_update.register(app, _route_deps)
    routes_chat.register(app, _route_deps)
    routes_reviews.register(app, _route_deps)
    routes_auth.register(app, _route_deps)

    # Config the SPA reads at bootstrap to build preview URLs. apps_domain is not
    # secret; expose it without auth so the frontend can read it early.
    @app.get("/api/config")
    def public_config():
        return {"apps_domain": cfg.get("apps_domain"), "features": public_flags(cfg)}

    def _valid_preview_token(token: str) -> bool:
        # Validation for the preview-subdomain cookie gate; runs in the ASGI
        # middleware (outside a request) so it uses a fresh connection. Fail-closed.
        try:
            conn = connect(cfg["database_path"])
            try:
                row = conn.execute(
                    "SELECT 1 FROM auth_sessions WHERE token_hash=? AND revoked_at IS NULL "
                    "AND (expires_at IS NULL OR expires_at > ?)",
                    (hash_token(token), iso_now()),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception:
            return False

    @app.post("/api/preview-auth")
    def preview_auth(request: Request, user: dict[str, Any] = Depends(current_user)):
        # Mint the domain-wide cookie that gates preview subdomains (they carry no CF
        # Access so they can be iframed). Echoes the caller's already-valid bearer token
        # into an HttpOnly, same-site cookie the preview iframe then carries itself.
        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        resp = JSONResponse({"ok": bool(token)})
        if token and cfg.get("apps_domain"):
            resp.set_cookie("proxima_preview", token, domain="." + cfg["apps_domain"], path="/",
                            httponly=True, secure=True, samesite="lax", max_age=86400)
        return resp

    # Host-based reverse proxy for per-app remote previews (<slug>.<apps_domain> → that
    # app's dev port, HTTP + WebSocket). Gated by the proxima_preview cookie (no CF Access on
    # these subdomains, so they can be iframed). No-op when apps_domain is unset.
    app.add_middleware(PreviewProxyMiddleware, fastapi_app=app, apps_domain=cfg.get("apps_domain"),
                       validate_token=_valid_preview_token)

    return app


def _config_from_env() -> dict[str, Any]:
    """Config for the ASGI entrypoint (`uvicorn proxima_api.main:app`), from env.

    Mirrors scripts/serve.py so running either way behaves the same and never
    falls back to the /srv demo defaults.
    """
    workspace_root = Path(os.environ.get("PROXIMA_WORKSPACE_ROOT", str(Path.home() / ".local/share/proxima")))
    update_check_env = os.environ.get("PROXIMA_UPDATE_CHECK")
    return {
        "database_path": os.environ.get("PROXIMA_DB_PATH", str(workspace_root / "proxima.db")),
        "workspace_root": str(workspace_root),
        "hermes_profiles_root": os.environ.get("PROXIMA_HERMES_PROFILES_ROOT", str(workspace_root / "hermes-profiles")),
        "web_dist_path": os.environ.get("PROXIMA_WEB_DIST") or None,
        "public_base_url": os.environ.get("PROXIMA_PUBLIC_BASE_URL") or None,
        "projectctl_command": os.environ.get("PROXIMA_PROJECTCTL_COMMAND", "").split() or None,
        # Proxima is single-user by design: one owner, no login wall / team
        # management; the access gate is the network (loopback / Cloudflare Access).
        "single_user": True,
        "single_user_name": os.environ.get("PROXIMA_SINGLE_USER_NAME", "admin"),
        # Point the claude-code runner at the live ~/.claude (full skills/plugins/
        # rules/memory) instead of an isolated seeded profile home.
        "claude_live_home": os.environ.get("PROXIMA_CLAUDE_LIVE_HOME", "").lower() in ("1", "true", "yes"),
        "link_roots": [p for p in os.environ.get("PROXIMA_LINK_ROOTS", os.path.expanduser("~")).split(":") if p],
        # Per-app remote preview: apps run on their own subdomain (<slug>.<apps_domain>)
        # that rides the tunnel; unset ⇒ local-only preview (no subdomain routing). The
        # cf_* creds let the app create/remove that subdomain hostname on the tunnel.
        "apps_domain": os.environ.get("PROXIMA_APPS_DOMAIN") or None,
        # Browser-tab label (e.g. "STAGING") so staging/prod aren't confused. Unset ⇒ none.
        "env_name": (os.environ.get("PROXIMA_ENV_NAME") or "").strip() or None,
        "cf_api_token": os.environ.get("PROXIMA_CF_API_TOKEN") or None,
        "cf_account_id": os.environ.get("PROXIMA_CF_ACCOUNT_ID") or None,
        "cf_tunnel_id": os.environ.get("PROXIMA_CF_TUNNEL_ID") or None,
        "cf_zone": os.environ.get("PROXIMA_CF_ZONE") or None,
        "cf_zone_id": os.environ.get("PROXIMA_CF_ZONE_ID") or None,
        # Release update check — PROXIMA_UPDATE_CHECK=0 disables the periodic
        # phone-home; PROXIMA_UPDATE_REPO points forks at their own releases.
        "update_check": (
            DEFAULT_CONFIG["update_check"] if update_check_env is None
            else update_check_env.lower() in ("1", "true", "yes")
        ),
        "update_repo": os.environ.get("PROXIMA_UPDATE_REPO") or DEFAULT_CONFIG["update_repo"],
        "update_token": os.environ.get("PROXIMA_UPDATE_TOKEN") or os.environ.get("GITHUB_TOKEN") or None,
        "feature_video": os.environ.get("PROXIMA_FEATURE_VIDEO", "").lower() in ("1", "true", "yes", "on"),
        "feature_design_studio": os.environ.get("PROXIMA_FEATURE_DESIGN_STUDIO", "").lower() in ("1", "true", "yes", "on"),
    }


# Lazily build the ASGI app only when `app` is actually accessed (e.g. by
# uvicorn), so importing this module (tests, serve.py) has no side effects and
# never opens the /srv demo database.
def __getattr__(name: str):
    if name == "app":
        return create_app(_config_from_env())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

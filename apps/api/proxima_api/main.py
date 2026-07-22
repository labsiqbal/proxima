from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import connect, init_db
from .migrations import run_migrations
from .acp import AcpManager
from .apprunner import AppManager
from .preview_proxy import (
    PREVIEW_COOKIE,
    PREVIEW_TOKEN_TTL_SECONDS,
    PreviewProxyMiddleware,
    PreviewRelayManager,
    mint_preview_token,
    valid_preview_token,
)
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
    archive as routes_archive,
    auth as routes_auth,
    chat as routes_chat,
    design as routes_design,
    files as routes_files,
    profiles as routes_profiles,
    projects as routes_projects,
    reviews as routes_reviews,
    update as routes_update,
    wiki as routes_wiki,
    work as routes_work,
)
from .routes import graph as routes_graph  # pyright: ignore[reportAttributeAccessIssue]

logger = logging.getLogger("proxima.api")


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = app.state.config
    app.state.hub.bind_loop(asyncio.get_running_loop())
    if cfg.get("auto_provision", True):
        try:
            backfill(app.state.db, cfg)
        except Exception as _exc:
            logging.getLogger("proxima.provisioning").exception("startup backfill failed")
    try:
        with app.state.db_lock:
            archive_old_jobs(app.state.db, _as_int(cfg.get("job_archive_days", 30)))
    except Exception as _exc:
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
    except Exception as _exc:
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
                except Exception as _exc:
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
                except Exception as _exc:
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
    await app.state.preview_relays.shutdown()


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    cfg = normalize_config(config)
    app = FastAPI(title="Proxima API", version=read_local_version(), lifespan=_lifespan)
    app.state.config = cfg
    app.state.db = connect(cfg["database_path"])
    app.state.db_lock = __import__("threading").RLock()
    init_db(app.state.db, cfg.get("seed_users") or [], lambda username, slug: hermes_home_for(cfg, username, slug), source_hermes_home=cfg.get("source_hermes_home"))
    run_migrations(app.state.db, cfg.get("database_path"))  # versioned migrations (backs up before applying)
    app.state.worker_db = connect(cfg["database_path"])  # dedicated connection for the async run worker
    app.state.worker = RunWorker(app)
    app.state.acp_manager = AcpManager()
    app.state.app_manager = AppManager()
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
        cookie=Cookie,
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

    routes_work.register(app, _route_deps)
    routes_graph.register(app, _route_deps)
    routes_profiles.register(app, _route_deps)
    routes_projects.register(app, _route_deps)
    routes_files.register(app, _route_deps)
    routes_archive.register(app, _route_deps)
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

    preview_secret = secrets.token_bytes(32)

    def _valid_preview_token(token: str) -> bool:
        return valid_preview_token(preview_secret, token)

    @app.post("/api/preview-auth")
    def preview_auth(request: Request, user: dict[str, Any] = Depends(current_user)):
        # A preview capability can load previews but is useless against the
        # main API. Never reuse or copy the owner's bearer/session token here.
        token = mint_preview_token(preview_secret)
        resp = JSONResponse({"ok": True})
        # Host-only cookie for the UI's own host: authorizes the per-app preview
        # relay ports (cookies are host-scoped but port-blind, so the browser
        # sends it to http://<this-host>:<relay port>/ and its subresources).
        resp.set_cookie(PREVIEW_COOKIE, token, path="/", httponly=True,
                        secure=request.url.scheme == "https", samesite="lax",
                        max_age=PREVIEW_TOKEN_TTL_SECONDS)
        if cfg.get("apps_domain"):
            resp.set_cookie(PREVIEW_COOKIE, token, domain="." + cfg["apps_domain"], path="/",
                            httponly=True, secure=True, samesite="lax",
                            max_age=PREVIEW_TOKEN_TTL_SECONDS)
        return resp

    # Port-based preview origins for installs without an apps domain (LAN/Tailscale):
    # each running app gets its own credential-stripping listener; see preview_proxy.py.
    app.state.preview_relays = PreviewRelayManager(
        cfg.get("preview_bind_host"),
        port_for=lambda slug: app.state.app_manager.port(slug),
        validate_token=_valid_preview_token,
    )

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
    try:
        max_upload_mb = max(1, int(os.environ.get("PROXIMA_MAX_UPLOAD_MB", "100")))
    except ValueError:
        max_upload_mb = 100

    def env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            return default

    return {
        "database_path": os.environ.get("PROXIMA_DB_PATH", str(workspace_root / "proxima.db")),
        "workspace_root": str(workspace_root),
        "hermes_profiles_root": os.environ.get("PROXIMA_HERMES_PROFILES_ROOT", str(workspace_root / "hermes-profiles")),
        "web_dist_path": os.environ.get("PROXIMA_WEB_DIST") or None,
        "max_upload_bytes": max_upload_mb * 1024 * 1024,
        "projectctl_command": os.environ.get("PROXIMA_PROJECTCTL_COMMAND", "").split() or None,
        # Turn quota + worker knobs, mirrored from serve.py (T5): before this the
        # env overrides only existed via scripts/serve.py and this entrypoint was
        # stuck at the defaults. The in-app run_timeout_seconds setting overrides
        # these at run time on both entrypoints (app_settings.get_run_timeout_seconds).
        "run_timeout_seconds": env_int("PROXIMA_RUN_TIMEOUT_SECONDS", int(DEFAULT_CONFIG["run_timeout_seconds"])),
        "run_continuation_limit": env_int("PROXIMA_RUN_CONTINUATION_LIMIT", int(DEFAULT_CONFIG["run_continuation_limit"])),
        "run_worker_concurrency": env_int("PROXIMA_RUN_WORKER_CONCURRENCY", int(DEFAULT_CONFIG["run_worker_concurrency"])),
        "graph_node_concurrency": env_int("PROXIMA_GRAPH_NODE_CONCURRENCY", int(DEFAULT_CONFIG["graph_node_concurrency"])),
        # Proxima is single-user by design: one owner and no team management. The
        # network gate remains primary; the owner password/session is defense-in-depth.
        "single_user": True,
        "single_user_name": os.environ.get("PROXIMA_SINGLE_USER_NAME", "admin"),
        # Point the claude-code runner at the live ~/.claude (full skills/plugins/
        # rules/memory) instead of an isolated seeded profile home.
        "claude_live_home": os.environ.get("PROXIMA_CLAUDE_LIVE_HOME", "").lower() in ("1", "true", "yes"),
        # Capability bundle (T8): unset -> <repo root>/bundled-skills via normalize_config.
        "bundled_skills_dir": os.environ.get("PROXIMA_BUNDLED_SKILLS_DIR") or None,
        "link_roots": [p for p in os.environ.get("PROXIMA_LINK_ROOTS", os.path.expanduser("~")).split(":") if p],
        # Per-app remote preview: apps run on their own subdomain (<slug>.<apps_domain>)
        # that rides the tunnel; unset ⇒ local-only preview (no subdomain routing). The
        # cf_* creds let the app create/remove that subdomain hostname on the tunnel.
        "apps_domain": os.environ.get("PROXIMA_APPS_DOMAIN") or None,
        # Interface for per-app preview relay ports (remote preview without an apps
        # domain). "off" disables relays for strict loopback-only installs.
        "preview_bind_host": os.environ.get("PROXIMA_PREVIEW_BIND") or DEFAULT_CONFIG["preview_bind_host"],
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
        "feature_design_studio": os.environ.get("PROXIMA_FEATURE_DESIGN_STUDIO", "1").lower() in ("1", "true", "yes", "on"),
        "feature_workflow_graph": os.environ.get("PROXIMA_FEATURE_WORKFLOW_GRAPH", "1").lower() in ("1", "true", "yes", "on"),
        "feature_repo_worktrees": os.environ.get("PROXIMA_FEATURE_REPO_WORKTREES", "1").lower() in ("1", "true", "yes", "on"),
    }


# Lazily build the ASGI app only when `app` is actually accessed (e.g. by
# uvicorn), so importing this module (tests, serve.py) has no side effects and
# never opens the /srv demo database.
def __getattr__(name: str):
    if name == "app":
        return create_app(_config_from_env())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

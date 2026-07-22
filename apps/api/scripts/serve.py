from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from proxima_api.main import create_app
from proxima_api.logging_config import uvicorn_log_config
from proxima_api.settings import DEFAULT_CONFIG


def env_path(name: str, default: Path) -> str:
    return os.environ.get(name, str(default))


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def default_data_dir() -> Path:
    """Per-OS default for runtime data (DB, projects, profiles). Overridable via
    PROXIMA_WORKSPACE_ROOT."""
    if os.name == "nt":  # Windows
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "proxima"
    if sys.platform == "darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "proxima"
    return Path.home() / ".local" / "share" / "proxima"  # Linux/XDG


workspace_root = Path(env_path("PROXIMA_WORKSPACE_ROOT", default_data_dir()))
web_dist = Path(env_path("PROXIMA_WEB_DIST", REPO_ROOT / "apps/web/dist"))
projectctl = Path(env_path("PROXIMA_PROJECTCTL", REPO_ROOT / "infra/scripts/projectctl"))

app = create_app(
    {
        "database_path": env_path("PROXIMA_DB_PATH", workspace_root / "proxima.db"),
        "workspace_root": str(workspace_root),
        "hermes_profiles_root": env_path("PROXIMA_HERMES_PROFILES_ROOT", workspace_root / "hermes-profiles"),
        "projectctl_path": str(projectctl),
        "projectctl_command": os.environ.get("PROXIMA_PROJECTCTL_COMMAND", "").split() or None,
        # Off by default (single-user $HOME install). The /srv multi-user root
        # deployment sets PROXIMA_MANAGE_OS_ACL=1 to enable ownership/ACL ops.
        "manage_os_acl": env_bool("PROXIMA_MANAGE_OS_ACL", False),
        "web_dist_path": str(web_dist),
        "source_hermes_home": os.environ.get("PROXIMA_SOURCE_HERMES_HOME") or None,
        "hermes_bin": os.environ.get("PROXIMA_HERMES_BIN") or None,
        "refresh_credentials": env_bool("PROXIMA_REFRESH_CREDENTIALS", True),
        "run_timeout_seconds": env_int("PROXIMA_RUN_TIMEOUT_SECONDS", 900),
        "max_upload_bytes": env_int("PROXIMA_MAX_UPLOAD_MB", 100) * 1024 * 1024,
        "run_worker_poll_interval_ms": env_int("PROXIMA_RUN_WORKER_POLL_MS", 250),
        # Graph fan-out is bounded by both of these: the graph budget decides how
        # many nodes are dispatched, the worker decides how many actually execute.
        "run_worker_concurrency": env_int(
            "PROXIMA_RUN_WORKER_CONCURRENCY", int(DEFAULT_CONFIG["run_worker_concurrency"])
        ),
        "graph_node_concurrency": env_int(
            "PROXIMA_GRAPH_NODE_CONCURRENCY", int(DEFAULT_CONFIG["graph_node_concurrency"])
        ),
        "seed_users": [],
        # Single-user owner identity. The password/session gate is established by
        # first-run setup; this flag is retained for config compatibility.
        "single_user": env_bool("PROXIMA_SINGLE_USER", False),
        "single_user_name": os.environ.get("PROXIMA_SINGLE_USER_NAME") or "owner",
        # Point claude-code runner at live ~/.claude (full skills/plugins/rules/memory).
        "claude_live_home": env_bool("PROXIMA_CLAUDE_LIVE_HOME", False),
        "link_roots": [p for p in os.environ.get("PROXIMA_LINK_ROOTS", os.path.expanduser("~")).split(":") if p],
        # Per-app remote preview: <slug>.<apps_domain> rides the tunnel; cf_* creds
        # let the app create/remove that hostname. Unset ⇒ local-only preview.
        "apps_domain": os.environ.get("PROXIMA_APPS_DOMAIN") or None,
        # Browser-tab label (e.g. "STAGING") so staging/prod tabs aren't confused.
        "env_name": (os.environ.get("PROXIMA_ENV_NAME") or "").strip() or None,
        "cf_api_token": os.environ.get("PROXIMA_CF_API_TOKEN") or None,
        "cf_account_id": os.environ.get("PROXIMA_CF_ACCOUNT_ID") or None,
        "cf_tunnel_id": os.environ.get("PROXIMA_CF_TUNNEL_ID") or None,
        "cf_zone": os.environ.get("PROXIMA_CF_ZONE") or None,
        "cf_zone_id": os.environ.get("PROXIMA_CF_ZONE_ID") or None,
        # Release update check — PROXIMA_UPDATE_CHECK=0 disables the periodic
        # phone-home; PROXIMA_UPDATE_REPO points forks at their own releases.
        "update_check": env_bool("PROXIMA_UPDATE_CHECK", bool(DEFAULT_CONFIG["update_check"])),
        "update_repo": os.environ.get("PROXIMA_UPDATE_REPO") or DEFAULT_CONFIG["update_repo"],
        "update_token": os.environ.get("PROXIMA_UPDATE_TOKEN") or os.environ.get("GITHUB_TOKEN") or None,
        "feature_design_studio": env_bool("PROXIMA_FEATURE_DESIGN_STUDIO", False),
        "feature_workflow_graph": env_bool("PROXIMA_FEATURE_WORKFLOW_GRAPH", True),
        # On by default since slice 4 (review UI); the env var is the escape hatch.
        "feature_repo_worktrees": env_bool("PROXIMA_FEATURE_REPO_WORKTREES", True),
    }
)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("PROXIMA_HOST", "127.0.0.1"),
        port=env_int("PROXIMA_PORT", 8765),
        log_config=uvicorn_log_config(),
    )

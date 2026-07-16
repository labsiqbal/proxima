from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")

DEFAULT_CONFIG: dict[str, Any] = {
    "database_path": str(Path.home() / ".local" / "share" / "proxima" / "proxima.db"),
    "workspace_root": str(Path.home() / ".local" / "share" / "proxima"),
    "projectctl_path": None,
    "projectctl_command": None,
    # Run the privileged projectctl helper (OS ownership + POSIX ACLs) on project
    # create/invite/remove. Only meaningful for the multi-OS-user /srv deployment
    # running as root. Off by default: single-user $HOME installs just scaffold
    # the project dir (no root, no ACLs needed).
    "manage_os_acl": False,
    "hermes_profiles_root": None,
    "source_hermes_home": None,
    "hermes_bin": None,
    "run_worker_poll_interval_ms": 250,
    "run_worker_concurrency": 2,
    "run_timeout_seconds": 900,
    "auth_token_ttl_hours": 24 * 14,
    "seed_users": [],
    "default_team_name": "Team",
    "provision_starter_dirs": ["wiki", "tasks", "artifacts"],
    "auto_provision": True,
    "start_worker": True,
    "web_dist_path": None,
    "public_base_url": None,  # override for invite links; else auto-detected from Tailscale
    # Release update check (docs/installation.md#updating). update_check=False
    # disables only the periodic loop; the manual "Check now" endpoint still works.
    "update_check": True,
    "update_repo": "labsiqbal/proxima",
    "update_token": None,
    "feature_video": False,
    "feature_design_studio": False,
    # Graph workflow engine (ADR-0001). Master safety switch: OFF by default so
    # the new engine is inert until explicitly enabled.
    "feature_workflow_graph": False,
    # How many nodes of one graph job may be in flight at once. This is a dispatch
    # budget, not a guarantee: node runs are executed by the run worker, so
    # run_worker_concurrency above is the real ceiling. Raise both to widen a fan-out.
    "graph_node_concurrency": 4,
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    workspace_root = Path(cfg["workspace_root"])
    cfg["workspace_root"] = str(workspace_root)
    cfg["hermes_profiles_root"] = str(Path(cfg.get("hermes_profiles_root") or workspace_root / "hermes-profiles"))
    cfg["projectctl_path"] = str(Path(cfg.get("projectctl_path") or repo_root() / "infra/scripts/projectctl"))
    cfg["source_hermes_home"] = str(Path(cfg.get("source_hermes_home") or os.path.expanduser("~/.hermes")))
    cfg["manage_os_acl"] = bool(cfg.get("manage_os_acl"))
    cfg["feature_video"] = _bool_flag(cfg.get("feature_video"))
    cfg["feature_design_studio"] = _bool_flag(cfg.get("feature_design_studio"))
    cfg["feature_workflow_graph"] = _bool_flag(cfg.get("feature_workflow_graph"))
    return cfg


def validate_slug(slug: str) -> str:
    candidate = slug.strip().lower()
    if not SLUG_RE.match(candidate):
        raise ValueError("slug must use lowercase letters, numbers, and hyphens")
    return candidate


def hermes_home_for(cfg: dict[str, Any], username: str, profile_slug: str) -> Path:
    safe_user = validate_slug(username)
    safe_profile = validate_slug(profile_slug)
    root = Path(cfg["hermes_profiles_root"]).resolve()
    home = (root / safe_user / safe_profile).resolve()
    if root not in home.parents:
        raise ValueError("invalid Hermes home path")
    return home

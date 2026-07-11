"""Version identity + release update checking / self-update orchestration.

The single source of truth for the app version is the VERSION file at repo
root. Everything else (FastAPI app version, /api/health, the update check)
reads it from here.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import httpx

from .auth import iso_now
from .settings import repo_root

logger = logging.getLogger("proxima.updates")


def read_local_version() -> str:
    """VERSION file at repo root; '0.0.0' if unreadable (broken checkout)."""
    try:
        return (repo_root() / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except (OSError, UnicodeDecodeError):
        return "0.0.0"


def parse_version(v: str) -> tuple[int, int, int]:
    """Lenient semver → 3-int tuple. 'v1.2.3' → (1,2,3); junk parts → 0."""
    parts: list[int] = []
    for chunk in v.strip().lstrip("vV").split(".")[:3]:
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


UPDATE_CHECK_INTERVAL_SECONDS = 6 * 3600
UPDATE_FIRST_CHECK_DELAY_SECONDS = 60
GITHUB_TIMEOUT_SECONDS = 10
LOG_TAIL_LINES = 50
MANUAL_UPDATE_COMMAND = (
    "cd <your proxima checkout> && git pull --ff-only, "
    "then re-run the installer for your OS (see docs/installation.md)"
)


class UpdateError(Exception):
    pass


class UpdateInProgress(UpdateError):
    pass


class NoUpdateAvailable(UpdateError):
    pass


class UpdateUnsupported(UpdateError):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        # Reap it first if it's our zombie child (the Popen object was discarded);
        # otherwise a failed updater would look alive forever and wedge the marker
        # at "running", blocking every retry.
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _parse_release(data: dict[str, Any]) -> dict[str, Any]:
    """GitHub /releases/latest payload → the four fields we keep."""
    tag = str(data.get("tag_name") or "").strip()
    return {
        "version": tag.lstrip("vV"),
        "notes": str(data.get("body") or ""),
        "url": str(data.get("html_url") or ""),
        "published_at": data.get("published_at"),
    }


class UpdateManager:
    """Holds update-check state and runs the self-update. One per app."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.repo = str(cfg.get("update_repo") or "")
        self.token = str(cfg.get("update_token") or "")
        self.repo_root = Path(os.environ.get("PROXIMA_REPO_ROOT") or repo_root())
        data_dir = Path(cfg["database_path"]).expanduser().parent
        self.marker_path = data_dir / "update-status.json"
        self.log_path = data_dir / "update.log"
        self.current = read_local_version()
        self._latest: dict[str, Any] | None = None
        self.checked_at: str | None = None
        self.last_error: str | None = None
        self._apply_lock = threading.Lock()

    async def _fetch_latest_release(self) -> dict[str, Any]:
        """The only network call — split out so tests can monkeypatch it."""
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "proxima-update-check",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return _parse_release(resp.json())

    async def check_now(self) -> None:
        """Refresh `_latest`. Never raises — a private repo (404 today), an
        offline host, or a GitHub hiccup must stay invisible to the user."""
        try:
            release = await self._fetch_latest_release()
        except Exception as exc:
            self.last_error = str(exc)
            logger.debug("update check failed: %s", exc)
            return
        self._latest = release
        self.checked_at = iso_now()
        self.last_error = None

    def _write_marker(self, data: dict[str, Any]) -> None:
        try:
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            self.marker_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            logger.exception("could not write update marker")

    def reconcile_marker(self) -> None:
        """Self-heal the marker at startup (and lazily on every status read)."""
        self._marker_state()

    def apply(self) -> dict[str, Any]:
        with self._apply_lock:
            state, _ = self._marker_state()
            if state == "running":
                raise UpdateInProgress("an update is already running")
            if sys.platform == "win32":
                raise UpdateUnsupported(MANUAL_UPDATE_COMMAND)
            latest = self._latest
            if not latest or not is_newer(latest["version"], self.current):
                raise NoUpdateAvailable("no newer release is known")
            target = latest["version"]
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # Detached (start_new_session): the updater's only kill arrives from the
            # `systemctl restart` it issues itself AFTER a successful pull+build, at
            # which point the new code is already on disk — dying there is harmless.
            # On macOS/launchd the detached child simply survives the restart.
            with open(self.log_path, "ab") as log_file:
                log_file.write(f"\n===== update to v{target} started {iso_now()} =====\n".encode())
                proc = subprocess.Popen(
                    ["bash", str(self.repo_root / "scripts" / "proxima"), "update"],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=str(self.repo_root),
                )
            self._write_marker({
                "state": "running",
                "target": target,
                "started_at": iso_now(),
                "pid": proc.pid,
            })
            return {"started": True, "target": target}

    def _marker_state(self) -> tuple[str, str | None]:
        """Derive the live update state from the marker file, self-healing it.

        running + we now run the target  → the update finished → "done"/idle
        running + updater pid is gone    → it died before restarting → "failed"
        running + pid alive              → genuinely still updating
        """
        try:
            data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ("idle", None)
        state = str(data.get("state") or "")
        target = data.get("target")
        if state == "failed":
            return ("failed", target)
        if state != "running":
            return ("idle", target)
        if target and self.current == str(target):
            self._write_marker({**data, "state": "done"})
            return ("idle", target)
        pid = data.get("pid")
        if pid and _pid_alive(int(pid)):
            return ("running", target)
        self._write_marker({**data, "state": "failed"})
        return ("failed", target)

    def status(self) -> dict[str, Any]:
        state, _target = self._marker_state()
        latest = self._latest
        return {
            "current_version": self.current,
            "latest": latest,
            "update_available": bool(latest and is_newer(latest["version"], self.current)),
            "state": state,
            "checked_at": self.checked_at,
            "last_error": self.last_error,
            "log_tail": self._log_tail() if state in ("running", "failed") else None,
            "apply_supported": sys.platform != "win32",
            "manual_command": MANUAL_UPDATE_COMMAND,
        }

    def _log_tail(self, lines: int = LOG_TAIL_LINES) -> str | None:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return "\n".join(text.splitlines()[-lines:]) or None

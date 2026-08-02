from __future__ import annotations

import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
HARNESS_ROOT = ROOT / "scripts" / "browser-harness"


def _request(
    url: str,
    *,
    body: dict | None = None,
    token: str | None = None,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=10) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected response from {url}")
    return value


def _port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _browser() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError("Chromium or Google Chrome is required")


def _build_web() -> None:
    completed = subprocess.run(
        ["npm", "--prefix", str(WEB_DIR), "run", "build"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout or "web build failed")


def main() -> None:
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")
    _build_web()
    sys.path.insert(0, str(HARNESS_ROOT))
    from browser import run_scenario

    manifest = json.loads(
        (HARNESS_ROOT / "browser-scenarios.json").read_text(encoding="utf-8")
    )
    scenario = next(
        item
        for item in manifest["scenarios"]
        if item["name"] == "master-popup-home"
    )
    with tempfile.TemporaryDirectory(prefix="proxima-master-browser-") as raw_root:
        fixture = Path(raw_root)
        home = fixture / "home"
        workspace = fixture / "workspace"
        container = workspace / "candidate"
        runner_home = fixture / "runner-home"
        fake_bin = fixture / "bin"
        for path in (home, workspace, container, runner_home, fake_bin):
            path.mkdir(parents=True)
        fixture_codex = HARNESS_ROOT / "codex-fixture"
        if (
            not fixture_codex.is_file()
            or fixture_codex.is_symlink()
            or not os.access(fixture_codex, os.X_OK)
        ):
            raise RuntimeError("tracked Codex fixture is unavailable")
        codex = fake_bin / "codex"
        shutil.copyfile(fixture_codex, codex, follow_symlinks=False)
        codex.chmod(0o555)
        port = _port()
        base_url = f"http://127.0.0.1:{port}"
        environment = {
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_CLAUDE_LIVE_HOME": "0",
            "PROXIMA_DB_PATH": str(fixture / "candidate.db"),
            "PROXIMA_HERMES_PROFILES_ROOT": str(runner_home),
            "PROXIMA_LINK_ROOTS": str(workspace),
            "PROXIMA_PORT": str(port),
            "PROXIMA_PROJECTCTL_COMMAND": "/usr/bin/true",
            "PROXIMA_REFRESH_CREDENTIALS": "0",
            "PROXIMA_SINGLE_USER": "1",
            "PROXIMA_SINGLE_USER_NAME": "candidate",
            "PROXIMA_UPDATE_CHECK": "0",
            "PROXIMA_WEB_DIST": str(WEB_DIR / "dist"),
            "PROXIMA_WORKSPACE_ROOT": str(workspace),
            "PYTHONPATH": str(ROOT / "apps" / "api"),
            "TMPDIR": str(fixture),
        }
        log_path = fixture / "server.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                [str(API_PYTHON), str(ROOT / "apps" / "api" / "scripts" / "serve.py")],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding="utf-8"))
                    try:
                        _request(f"{base_url}/api/health")
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("disposable server readiness timed out")
                        time.sleep(0.1)
                token = str(
                    _request(
                        f"{base_url}/auth/set-password",
                        body={"password": "candidate-browser-password"},
                    )["token"]
                )
                projects = _request(f"{base_url}/api/projects", token=token)
                if not projects.get("projects"):
                    _request(
                        f"{base_url}/api/projects/link",
                        body={
                            "name": "Candidate",
                            "path": str(container),
                            "slug": "candidate-browser",
                        },
                        token=token,
                    )
                transcript = run_scenario(
                    executable=_browser(),
                    base_url=base_url,
                    scenario=scenario,
                    profile=fixture / "browser-profile",
                    auth_token=token,
                    drop_prefix=[],
                )
                print(
                    json.dumps(
                        {
                            "fixture": "disposable",
                            "ok": True,
                            "scenario": scenario["name"],
                            "transcript": json.loads(transcript),
                        },
                        sort_keys=True,
                    )
                )
            finally:
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if server.poll() is None:
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                try:
                    os.killpg(server.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if server.poll() is None:
                    server.wait()


if __name__ == "__main__":
    main()

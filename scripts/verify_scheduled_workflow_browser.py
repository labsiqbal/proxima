from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
HARNESS_ROOT = ROOT / "scripts" / "browser-harness"
WORKFLOW_NAME = "Scheduled browser trust"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SCREENSHOT_NAMES = (
    "before-missing-binding",
    "after-missing-binding-refusal",
    "before-run-now",
    "after-run-now-exact-job",
)


def screenshot_paths(directory: Path) -> dict[str, Path]:
    return {name: directory / f"{name}.png" for name in SCREENSHOT_NAMES}


def assert_valid_png(path: Path, *, minimum_bytes: int = 256) -> None:
    data = path.read_bytes()
    if len(data) < minimum_bytes or not data.startswith(PNG_MAGIC):
        raise RuntimeError(f"invalid or empty PNG screenshot: {path}")


def assert_screenshot_bundle(directory: Path) -> dict[str, Path]:
    paths = screenshot_paths(directory)
    for name, path in paths.items():
        if path.name != f"{name}.png":
            raise RuntimeError(f"screenshot name drift for {name}: {path.name}")
        if not path.is_file():
            raise RuntimeError(f"missing screenshot: {path.name}")
        assert_valid_png(path)
    return paths


def _request(
    url: str,
    *,
    body: dict | None = None,
    token: str | None = None,
    method: str | None = None,
    expected_status: int = 200,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method or ("POST" if body is not None else "GET"),
    )
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
            payload = json.loads(response.read())
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read())
    if status != expected_status:
        raise RuntimeError(
            f"{request.method} {url} returned {status}, expected {expected_status}: "
            f"{payload}"
        )
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected response from {url}")
    return payload


def _port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _browser() -> str:
    for key in ("CHROME_PATH", "CHROMIUM_PATH", "GOOGLE_CHROME_BIN"):
        configured = os.environ.get(key, "").strip()
        if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
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


def _scenario_missing_binding() -> dict:
    return {
        "name": "scheduled-workflow-missing-binding",
        "authenticated": True,
        "steps": [
            {"action": "click", "selector": "button", "text": "Skip tour"},
            {"action": "click", "selector": "nav button", "text": "Workflows"},
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row",
                "text": WORKFLOW_NAME,
            },
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row .workflow-home-available",
                "text": "Available",
            },
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row [data-label='Automation']",
                "text": "1 needs binding",
            },
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row button",
                "text": "Run",
            },
            {"action": "screenshot", "name": "before-missing-binding"},
            {
                "action": "click",
                "selector": ".workflow-home-workflow-row button",
                "text": "Run",
            },
            {"action": "assert", "selector": ".modal-card", "text": "Topic (required)"},
            {
                "action": "click",
                "selector": ".modal-card button",
                "text": "Cancel",
            },
            {
                "action": "click",
                "selector": ".workflow-home-workflow-row button",
                "text": "Schedules",
            },
            {
                "action": "assert",
                "selector": ".schedule-row > div",
                "text": WORKFLOW_NAME,
            },
            {
                "action": "assert",
                "selector": ".schedule-row .schedule-needs-source",
                "text": "Needs binding: Topic",
            },
            {
                "action": "assert",
                "selector": ".schedule-row input[aria-label='Schedule off']",
            },
            {
                "action": "assert",
                "selector": ".schedule-row button[title^='Run this schedule now'][disabled]",
            },
            {"action": "screenshot", "name": "after-missing-binding-refusal"},
        ],
    }


def _scenario_ready_run_now() -> dict:
    return {
        "name": "scheduled-workflow-ready-run-now",
        "authenticated": True,
        "steps": [
            # Second scenario, fresh browser profile, same owner. With Master on
            # the core tour records completion server-side, so the first
            # scenario's skip already retired it and there is nothing to click;
            # with Master off it is localStorage-only and the tour returns.
            {"action": "click_if_present", "selector": "button", "text": "Skip tour"},
            {"action": "click", "selector": "nav button", "text": "Workflows"},
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row",
                "text": WORKFLOW_NAME,
            },
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row .workflow-home-available",
                "text": "Available",
            },
            {
                "action": "assert",
                "selector": ".workflow-home-workflow-row [data-label='Automation']",
                "text": "1 schedule on",
            },
            {
                "action": "click",
                "selector": ".workflow-home-workflow-row button",
                "text": "Schedules",
            },
            {
                "action": "assert",
                "selector": ".schedule-row > div",
                "text": WORKFLOW_NAME,
            },
            {
                "action": "assert",
                "selector": ".schedule-row .schedule-ready",
                "text": "Inputs ready",
            },
            {
                "action": "assert",
                "selector": ".schedule-row input[aria-label='Schedule on']",
            },
            {"action": "screenshot", "name": "before-run-now"},
            {
                "action": "click",
                "selector": ".schedule-row button",
                "text": "Run now",
            },
            {
                "action": "assert",
                "selector": f"button[aria-label='Rename workflow {WORKFLOW_NAME}']",
            },
            {
                "action": "assert",
                "selector": "button[aria-label^='Active project: Scheduled browser'][disabled]",
            },
            {"action": "screenshot", "name": "after-run-now-exact-job"},
        ],
    }


def _wait_for_server(server: subprocess.Popen, base_url: str, log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while True:
        if server.poll() is not None:
            raise RuntimeError(log_path.read_text(encoding="utf-8"))
        try:
            _request(f"{base_url}/api/health")
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("disposable server readiness timed out")
            time.sleep(0.1)


def _stop_server(server: subprocess.Popen) -> None:
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


def _seed_fixture(base_url: str, token: str, container: Path) -> tuple[int, int]:
    roots = _request(f"{base_url}/api/fs/dirs", token=token)
    root_id = roots.get("root_id")
    if not isinstance(root_id, str) or not root_id:
        raise RuntimeError("browse roots did not return a root_id")
    _request(
        f"{base_url}/api/projects/link",
        body={
            "name": "Scheduled browser",
            "path": str(container),
            "slug": "scheduled-browser",
            "root_id": root_id,
        },
        token=token,
        expected_status=201,
    )
    job = _request(
        f"{base_url}/api/graph/jobs",
        body={
            "title": "Scheduled browser seed",
            "project_slug": "scheduled-browser",
            "graph": {
                "nodes": [
                    {
                        "id": "trigger",
                        "type": "trigger",
                        "name": "When I run it",
                        "trigger_kind": "manual",
                        "output_kind": "json",
                        "inputs": [
                            {
                                "id": "topic",
                                "label": "Topic",
                                "kind": "text",
                                "required": True,
                            }
                        ],
                    },
                    {
                        "id": "write",
                        "type": "agent",
                        "name": "Write",
                        "instruction": "Write about {{topic}}",
                        "output_kind": "text",
                        "depends_on": ["trigger"],
                    },
                ],
                "edges": [{"from": "trigger", "to": "write"}],
            },
        },
        token=token,
        expected_status=201,
    )
    template = _request(
        f"{base_url}/api/graph/jobs/{job['id']}/save-template",
        body={"name": WORKFLOW_NAME, "category": "content"},
        token=token,
        expected_status=201,
    )
    schedule = _request(
        f"{base_url}/api/schedules",
        body={
            "workflow_id": template["id"],
            "cron": "0 9 * * *",
            "timezone": "Asia/Jakarta",
            "overlap_policy": "allow",
            "enabled": False,
        },
        token=token,
    )
    return int(template["id"]), int(schedule["id"])


def main(screenshot_dir: Path | None = None) -> None:
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")
    if screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
    _build_web()
    sys.path.insert(0, str(HARNESS_ROOT))
    from browser import run_scenario

    with tempfile.TemporaryDirectory(prefix="proxima-schedule-browser-") as raw_root:
        fixture = Path(raw_root)
        workspace = fixture / "workspace"
        container = workspace / "scheduled-browser"
        runner_home = fixture / "runner-home"
        fake_bin = fixture / "bin"
        for path in (fixture / "home", workspace, container, runner_home, fake_bin):
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
            "HOME": str(fixture / "home"),
            "LANG": "C.UTF-8",
            "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_DB_PATH": str(fixture / "candidate.db"),
            "PROXIMA_HERMES_PROFILES_ROOT": str(runner_home),
            "PROXIMA_LINK_ROOTS": str(workspace),
            "PROXIMA_PORT": str(port),
            "PROXIMA_PROJECTCTL_COMMAND": "/usr/bin/true",
            "PROXIMA_REFRESH_CREDENTIALS": "0",
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
                _wait_for_server(server, base_url, log_path)
                token = str(
                    _request(
                        f"{base_url}/auth/set-password",
                        body={"password": "candidate-browser-password"},
                    )["token"]
                )
                _, schedule_id = _seed_fixture(base_url, token, container)

                refused = _request(
                    f"{base_url}/api/schedules/{schedule_id}",
                    body={"enabled": True},
                    token=token,
                    method="PATCH",
                    expected_status=422,
                )
                detail = refused.get("detail")
                if not isinstance(detail, dict) or detail.get("code") != (
                    "schedule_missing_sources"
                ):
                    raise RuntimeError("missing-binding enablement was not refused")
                message = str(detail.get("message") or "")
                if "source node" in message.lower():
                    raise RuntimeError("refusal copy still mentions source nodes")
                if "durable binding" not in message.lower():
                    raise RuntimeError("refusal copy does not explain durable bindings")

                missing_transcript = json.loads(
                    run_scenario(
                        executable=_browser(),
                        base_url=base_url,
                        scenario=_scenario_missing_binding(),
                        profile=fixture / "browser-missing-binding",
                        auth_token=token,
                        drop_prefix=[],
                        screenshot_dir=screenshot_dir,
                    )
                )
                schedule_row = next(
                    step["text"]
                    for step in missing_transcript
                    if step.get("text", "").startswith(WORKFLOW_NAME)
                    and "Needs binding:" in step.get("text", "")
                )
                if "0 9 * * *" in schedule_row:
                    raise RuntimeError("schedule row duplicated preset cron text")
                if "Every day at 9am" not in schedule_row:
                    raise RuntimeError("schedule row lost its human-readable cadence")
                if "Asia/Jakarta" not in schedule_row:
                    raise RuntimeError("schedule row lost its configured timezone")

                _request(
                    f"{base_url}/api/schedules/{schedule_id}",
                    body={
                        "bindings": {"topic": "Durable browser value"},
                        "enabled": True,
                    },
                    token=token,
                    method="PATCH",
                )
                ready_transcript = json.loads(
                    run_scenario(
                        executable=_browser(),
                        base_url=base_url,
                        scenario=_scenario_ready_run_now(),
                        profile=fixture / "browser-ready",
                        auth_token=token,
                        drop_prefix=[],
                        screenshot_dir=screenshot_dir,
                    )
                )
                jobs = _request(
                    f"{base_url}/api/graph/jobs?project_slug=scheduled-browser",
                    token=token,
                )["items"]
                spawned = [
                    item for item in jobs if item.get("schedule_id") == schedule_id
                ]
                if len(spawned) != 1:
                    raise RuntimeError("Run now did not create exactly one schedule job")
                if spawned[0].get("input") != {"topic": "Durable browser value"}:
                    raise RuntimeError("Run now did not use the durable schedule binding")
                if screenshot_dir is not None:
                    captured = assert_screenshot_bundle(screenshot_dir)
                else:
                    captured = {}
                print(
                    json.dumps(
                        {
                            "fixture": "disposable",
                            "job_id": spawned[0]["id"],
                            "ok": True,
                            "scenarios": [
                                missing_transcript[0]["name"],
                                ready_transcript[0]["name"],
                            ],
                            "screenshots": sorted(path.name for path in captured.values()),
                        },
                        sort_keys=True,
                    )
                )
            finally:
                _stop_server(server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the disposable scheduled-workflow browser regression."
    )
    parser.add_argument(
        "--screenshots",
        type=Path,
        help=(
            "Optional directory for stable before/after PNGs "
            f"({', '.join(SCREENSHOT_NAMES)})."
        ),
    )
    options = parser.parse_args()
    main(options.screenshots)

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

import uvicorn
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
WEB_ROOT = ROOT / "apps" / "web"
PROBE_ROOT = ROOT / "trusted-probes" / "safe-update"
DEFAULT_SCREENSHOT_ROOT = Path(
    "/tmp/no-mistakes-evidence/task-reconciliation"
)
SCREENSHOT_NAMES = (
    "after-attention-approval-done.png",
    "after-checkpoint-restore-queued.png",
    "after-checkpoint-recovery-history.png",
)
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(PROBE_ROOT))

from browser import run_scenario  # noqa: E402
from proxima_api.job_checkpoints import create_checkpoint  # noqa: E402
from proxima_api.main import create_app  # noqa: E402
from proxima_api.master_runtime import execute_tool  # noqa: E402


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
        ["npm", "--prefix", str(WEB_ROOT), "run", "build"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout)


def _config(fixture: Path) -> dict:
    workspace = fixture / "workspace"
    return {
        "database_path": str(fixture / "proxima.db"),
        "workspace_root": str(workspace),
        "hermes_profiles_root": str(fixture / "runner-home"),
        "web_dist_path": str(WEB_ROOT / "dist"),
        "projectctl_path": "/usr/bin/true",
        "link_roots": [str(workspace)],
        "seed_users": [{"username": "owner", "os_user": "owner"}],
        "single_user": True,
        "single_user_name": "owner",
        "start_worker": False,
        "start_scheduler": False,
        "update_check": False,
        "feature_master_orchestrator": True,
    }


def _seed(app) -> tuple[str, int, int, int]:
    client = TestClient(app)
    client.post("/auth/auto")
    password = client.post(
        "/auth/set-password",
        json={"password": "disposable-browser-password"},
    )
    if password.status_code != 200:
        raise RuntimeError(password.text)
    token = password.json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    project = client.post(
        "/api/projects",
        json={"slug": "reconcile", "name": "Reconciliation fixture"},
    )
    if project.status_code != 201:
        raise RuntimeError(project.text)
    container = client.get("/api/projects").json()["projects"][0]
    desk = client.get("/api/master/desk").json()
    owner_id = app.state.db.execute(
        "SELECT id FROM users WHERE username = 'owner'"
    ).fetchone()["id"]
    delegated = execute_tool(
        app.state.db,
        app,
        {"id": owner_id},
        desk["session"]["id"],
        "dispatch_jobs",
        {
            "start": False,
            "idempotency_key": "browser-task-reconciliation",
            "tasks": [
                {
                    "key": "review",
                    "title": "Approve mounted Task",
                    "brief": "Approve this Task from Attention.",
                    "project_slug": container["slug"],
                },
                {
                    "key": "restore",
                    "title": "Restore mounted Task",
                    "brief": "Restore this Task from Safety.",
                    "project_slug": container["slug"],
                },
            ],
        },
    )
    if not delegated["ok"]:
        raise RuntimeError(json.dumps(delegated))
    review_id, restore_id = [
        int(item["id"]) for item in delegated["result"]["jobs"]
    ]
    app.state.db.execute(
        "UPDATE jobs SET status = 'review', "
        "steps_state = json_set(steps_state, '$[0].status', 'done') "
        "WHERE id = ?",
        (review_id,),
    )
    checkpoint = create_checkpoint(app.state.db, restore_id)
    app.state.db.execute(
        "UPDATE jobs SET status = 'failed', "
        "rejected_reason = 'A bad continuation changed the Task state.', "
        "finished_at = CURRENT_TIMESTAMP, "
        "steps_state = json_set(steps_state, '$[0].status', 'failed') "
        "WHERE id = ?",
        (restore_id,),
    )
    app.state.master_projection.project_task(review_id)
    app.state.master_projection.project_task(restore_id)
    return str(token), review_id, restore_id, int(checkpoint["id"])


def _scenario(name: str, steps: list[dict]) -> dict:
    return {"name": name, "authenticated": True, "steps": steps}


def _screenshot_step(root: Path, name: str) -> list[dict]:
    return [{"action": "screenshot", "path": str(root / name)}]


def _screenshot_root() -> Path:
    configured = os.environ.get(
        "PROXIMA_TASK_RECONCILIATION_SCREENSHOTS",
        "",
    ).strip()
    root = Path(configured).resolve() if configured else DEFAULT_SCREENSHOT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def _runtime_fixture() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(
        prefix="task-reconciliation-browser-",
    ) as raw_fixture:
        yield Path(raw_fixture)


def main() -> None:
    _build_web()
    screenshot_root = _screenshot_root()
    with _runtime_fixture() as fixture:
        app = create_app(_config(fixture))
        token, review_id, restore_id, checkpoint_id = _seed(app)
        port = _port()
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urlopen(f"{base_url}/api/health", timeout=1):
                        break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "disposable server readiness timed out"
                        )
                    time.sleep(0.05)

            executable = _browser()
            approval = json.loads(
                run_scenario(
                    executable=executable,
                    base_url=base_url,
                    path=f"/?mode=work#task/{review_id}",
                    scenario=_scenario(
                        "mounted-review-to-done",
                        [
                            {
                                "action": "assert",
                                "selector": "main .job-pill",
                                "text": "review",
                            },
                            {
                                "action": "click_if_present",
                                "selector": "[role='dialog'] button",
                                "text": "Skip tour",
                            },
                            {
                                "action": "click",
                                "selector": "button[aria-label*='attention item']",
                            },
                            {
                                "action": "click",
                                "selector": ".attention-actions button",
                                "text": "Approve",
                            },
                            {
                                "action": "assert",
                                "selector": "main .job-pill",
                                "text": "done",
                                "timeout": 15,
                            },
                            *_screenshot_step(
                                screenshot_root,
                                "after-attention-approval-done.png",
                            ),
                        ],
                    ),
                    profile=fixture / "approval-browser",
                    auth_token=token,
                    drop_prefix=[],
                )
            )

            observer = json.loads(
                run_scenario(
                    executable=executable,
                    base_url=base_url,
                    path=f"/?mode=work#task/{restore_id}",
                    scenario=_scenario(
                        "mounted-failed-to-queued",
                        [
                            {
                                "action": "assert",
                                "selector": "main .job-pill",
                                "text": "failed",
                            },
                            {
                                "action": "click_if_present",
                                "selector": "[role='dialog'] button",
                                "text": "Skip tour",
                            },
                            {
                                "action": "request",
                                "path": (
                                    f"/api/jobs/{restore_id}/checkpoint/restore"
                                ),
                                "body": {
                                    "checkpoint_id": checkpoint_id,
                                    "confirm": True,
                                },
                            },
                            {
                                "action": "assert",
                                "selector": "main .job-pill",
                                "text": "queued",
                                "timeout": 20,
                            },
                            *_screenshot_step(
                                screenshot_root,
                                "after-checkpoint-restore-queued.png",
                            ),
                        ],
                    ),
                    profile=fixture / "restore-observer-browser",
                    auth_token=token,
                    drop_prefix=[],
                )
            )
            history = json.loads(
                run_scenario(
                    executable=executable,
                    base_url=base_url,
                    path="/?mode=delegate",
                    scenario=_scenario(
                        "durable-checkpoint-recovery-history",
                        [
                            {
                                "action": "assert",
                                "selector": ".master-conversation",
                                "text": (
                                    f"checkpoint #{checkpoint_id}: "
                                    "Failed to Queued"
                                ),
                                "timeout": 20,
                            },
                            *_screenshot_step(
                                screenshot_root,
                                "after-checkpoint-recovery-history.png",
                            ),
                        ],
                    ),
                    profile=fixture / "history-browser",
                    auth_token=token,
                    drop_prefix=[],
                )
            )
            screenshot_paths = [
                screenshot_root / name for name in SCREENSHOT_NAMES
            ]
            missing = [
                str(path)
                for path in screenshot_paths
                if not path.is_file() or path.stat().st_size == 0
            ]
            if missing:
                raise RuntimeError(
                    "browser screenshot evidence is missing: "
                    + ", ".join(missing)
                )
            print(
                json.dumps(
                    {
                        "fixture": "disposable",
                        "ok": True,
                        "approval": approval,
                        "restore_observer": observer,
                        "recovery_history": history,
                        "screenshots": [
                            str(path) for path in screenshot_paths
                        ],
                    },
                    sort_keys=True,
                )
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()

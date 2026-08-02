#!/usr/bin/env python3
"""Disposable real-Chrome regression for Project ownership context.

The fixture is created under a temporary directory, serves the built web app on
loopback, and never reads or writes the configured Proxima runtime.
"""
from __future__ import annotations

import argparse
import base64
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
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
HARNESS_ROOT = ROOT / "scripts" / "browser-harness"


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(
            f"{method} {url} failed with {exc.code}: {exc.read().decode()}"
        ) from exc


def _port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _browser_executable() -> str:
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
        raise RuntimeError(
            completed.stderr or completed.stdout or "web build failed"
        )


def _visible_element_expression(
    selector: str,
    *,
    text: str | None = None,
    action: str = "inspect",
    value: str | None = None,
) -> str:
    return f"""
(() => {{
  const visible = element => {{
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 0 && rect.height > 0;
  }};
  const wanted = {json.dumps(text)};
  const nodes = Array.from(document.querySelectorAll({json.dumps(selector)}))
    .filter(visible);
  const element = nodes.find(node =>
    wanted === null || (node.textContent || "").includes(wanted));
  if (!element) return {{ok:false, count:nodes.length}};
  if ({json.dumps(action)} === "click") element.click();
  if ({json.dumps(action)} === "focus") element.focus();
  if ({json.dumps(action)} === "select-text") {{
    const option = Array.from(element.options).find(item =>
      (item.textContent || "").includes({json.dumps(value)}));
    if (!option) return {{ok:false, reason:"option missing"}};
    const setter = Object.getOwnPropertyDescriptor(
      HTMLSelectElement.prototype, "value").set;
    setter.call(element, option.value);
    element.dispatchEvent(new Event("input", {{bubbles:true}}));
    element.dispatchEvent(new Event("change", {{bubbles:true}}));
  }}
  return {{
    ok:true,
    text:(element.textContent || "").trim().slice(0, 1000),
    aria:element.getAttribute("aria-label"),
    disabled:Boolean(element.disabled),
  }};
}})()
"""


class Browser:
    def __init__(
        self,
        *,
        executable: str,
        base_url: str,
        profile: Path,
        token: str,
    ) -> None:
        sys.path.insert(0, str(HARNESS_ROOT))
        from browser import _WebSocket, _evaluation

        self._WebSocket = _WebSocket
        self._evaluation = _evaluation
        self.base_url = base_url
        profile.mkdir(mode=0o700)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        debug_port = int(listener.getsockname()[1])
        listener.close()
        self.process = subprocess.Popen(
            [
                executable,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-sync",
                "--no-first-run",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                f"--user-data-dir={profile}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={debug_port}",
                "--window-size=1440,1000",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.connection = None
        deadline = time.monotonic() + 20
        page = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("browser exited before debugging was ready")
            try:
                with urlopen(
                    f"http://127.0.0.1:{debug_port}/json/list", timeout=1
                ) as response:
                    pages = json.loads(response.read())
                page = next(
                    item
                    for item in pages
                    if item.get("type") == "page"
                    and item.get("url") == "about:blank"
                )
                break
            except Exception:
                time.sleep(0.05)
        if page is None:
            raise RuntimeError("browser debugging startup timed out")
        self.connection = self._WebSocket(page["webSocketDebuggerUrl"])
        self.connection.call("Page.enable")
        self.connection.call("Runtime.enable")
        self.connection.call("Network.enable")
        self.connection.call(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Authorization": f"Bearer {token}"}},
        )
        self.navigate("/")

    def evaluate(self, expression: str) -> Any:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        return self._evaluation(self.connection, expression)

    def navigate(self, path: str) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call("Page.navigate", {"url": f"{self.base_url}{path}"})
        self._wait_document()

    def reload(self) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call("Page.reload", {"ignoreCache": True})
        self._wait_document()

    def _wait_document(self) -> None:
        deadline = time.monotonic() + 20
        while self.evaluate("document.readyState") != "complete":
            if time.monotonic() >= deadline:
                raise RuntimeError("browser page load timed out")
            time.sleep(0.05)

    def wait(
        self,
        selector: str,
        *,
        text: str | None = None,
        timeout: float = 15,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        expression = _visible_element_expression(selector, text=text)
        while time.monotonic() < deadline:
            result = self.evaluate(expression)
            if isinstance(result, dict) and result.get("ok"):
                return result
            time.sleep(0.05)
        raise RuntimeError(f"visible browser element missing: {selector} {text or ''}")

    def click(self, selector: str, *, text: str | None = None) -> None:
        self.wait(selector, text=text)
        result = self.evaluate(
            _visible_element_expression(selector, text=text, action="click")
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(f"browser click failed: {selector} {text or ''}")
        time.sleep(0.2)

    def focus(self, selector: str, *, text: str | None = None) -> None:
        result = self.evaluate(
            _visible_element_expression(selector, text=text, action="focus")
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(f"browser focus failed: {selector} {text or ''}")

    def select_text(self, selector: str, text: str) -> None:
        result = self.evaluate(
            _visible_element_expression(
                selector,
                action="select-text",
                value=text,
            )
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(f"browser selection failed: {selector} {text}")
        time.sleep(0.3)

    def assert_true(self, expression: str, label: str) -> None:
        result = self.evaluate(expression)
        if result is not True:
            raise RuntimeError(f"browser assertion failed: {label}: {result!r}")

    def wait_true(
        self,
        expression: str,
        label: str,
        *,
        timeout: float = 15,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.evaluate(expression) is True:
                return
            time.sleep(0.05)
        raise RuntimeError(f"browser condition timed out: {label}")

    def press(
        self,
        key: str,
        *,
        code: str | None = None,
        modifiers: int = 0,
    ) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        virtual_key = {
            "Enter": 13,
            "Escape": 27,
            "Tab": 9,
            "m": 77,
        }.get(key, ord(key.upper()) if len(key) == 1 else 0)
        params = {
            "key": key,
            "code": code or key,
            "modifiers": modifiers,
            "windowsVirtualKeyCode": virtual_key,
            "nativeVirtualKeyCode": virtual_key,
        }
        if key == "Enter":
            params["text"] = "\r"
            params["unmodifiedText"] = "\r"
        self.connection.call(
            "Input.dispatchKeyEvent", {"type": "keyDown", **params}
        )
        self.connection.call(
            "Input.dispatchKeyEvent", {"type": "keyUp", **params}
        )
        time.sleep(0.2)

    def resize(self, width: int, height: int) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
                "screenWidth": width,
                "screenHeight": height,
            },
        )
        time.sleep(0.3)

    def set_network_latency(self, milliseconds: int) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": milliseconds,
                "downloadThroughput": -1,
                "uploadThroughput": -1,
                "connectionType": "wifi",
            },
        )

    def screenshot(self, path: Path) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self.connection.call(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        )
        path.write_bytes(base64.b64decode(result["data"], validate=True))

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()


def _seed(base_url: str, token: str, fixture: Path) -> dict[str, Any]:
    roots = _request(f"{base_url}/api/fs/dirs", token=token)
    root_id = str(roots.get("root_id") or "")
    if not root_id:
        raise RuntimeError(f"link root_id missing from /api/fs/dirs: {roots!r}")
    projects: dict[str, dict[str, Any]] = {}
    for slug, name in (
        ("atlas", "Atlas private ops"),
        ("beacon", "Beacon release"),
    ):
        path = fixture / "workspace" / slug
        path.mkdir(parents=True)
        project = _request(
            f"{base_url}/api/projects/link",
            method="POST",
            body={
                "path": str(path),
                "slug": slug,
                "name": name,
                "root_id": root_id,
            },
            token=token,
        )
        projects[slug] = project

    atlas_job = _request(
        f"{base_url}/api/jobs",
        method="POST",
        body={
            "project_slug": "atlas",
            "title": "Atlas privacy review",
            "input": {"brief": "Confirm local-only boundaries."},
        },
        token=token,
    )
    beacon_job = _request(
        f"{base_url}/api/jobs",
        method="POST",
        body={
            "project_slug": "beacon",
            "title": "Beacon rollout review",
            "input": {"brief": "Review the release evidence."},
        },
        token=token,
    )
    graph_job = _request(
        f"{base_url}/api/graph/jobs",
        method="POST",
        body={
            "project_slug": "beacon",
            "title": "Beacon release plan",
            "graph": {
                "nodes": [
                    {
                        "id": "review",
                        "name": "Review",
                        "instruction": "Review release evidence.",
                    }
                ]
            },
        },
        token=token,
    )

    _request(f"{base_url}/api/containers", token=token)
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from proxima_api import app_settings, container_registry
    from proxima_api.db import connect

    connection = connect(fixture / "candidate.db")
    app_settings.set_master_settings(connection, tour_core_done=True)
    for slug in ("atlas", "beacon"):
        project = connection.execute(
            "SELECT * FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        ops_root = container_registry.ops_root(connection, project)
        (ops_root / container_registry.CONTAINER_DOC).write_text(
            "---\nidentity: General\n"
            f"summary: Browser fixture for {slug}.\n---\n",
            encoding="utf-8",
        )
        container_registry.refresh_registry_projection(connection, project)
    fleet = container_registry.list_fleet_containers(connection, 1)
    if {item["identity_label"] for item in fleet} != {"General"}:
        raise RuntimeError(f"fixture identity projection failed: {fleet!r}")
    done_step = json.dumps(
        [
            {
                "id": "execute",
                "name": "Execute delegated work",
                "instruction": "Review release evidence.",
                "status": "done",
                "output_summary": "Release evidence is ready for approval.",
            }
        ]
    )
    connection.execute(
        "UPDATE jobs SET status = 'review', steps_state = ?, "
        "current_step_idx = 0 WHERE id = ?",
        (done_step, beacon_job["id"]),
    )
    connection.execute(
        "INSERT INTO attention_items("
        "kind, title, target_json, inline_ok, actions_json, source_key"
        ") VALUES ('master_decision', 'Choose the Beacon rollout window', "
        "?, 0, '[]', 'ownership-browser:beacon-review')",
        (
            json.dumps(
                {
                    "view": "master",
                    "job_id": beacon_job["id"],
                    "engine": "linear",
                }
            ),
        ),
    )
    connection.close()
    return {
        "projects": projects,
        "atlas_job": atlas_job,
        "beacon_job": beacon_job,
        "graph_job": graph_job,
    }


def _select_project(browser: Browser, name: str) -> None:
    browser.click("button[aria-label^='Active project:']")
    browser.click("[role='option']", text=name)
    browser.wait("button[aria-label^='Active project:']", text=name)


def _run_acceptance(
    browser: Browser,
    *,
    base_url: str,
    token: str,
    beacon_task_id: int,
    screenshots: Path,
) -> None:
    browser.wait("button[aria-label^='Active project:']")
    _select_project(browser, "Atlas private ops")
    browser.click("nav button", text="Tasks")
    browser.wait("h1", text="Tasks")
    browser.wait(".job-row[aria-label]", text="Atlas privacy review")
    browser.assert_true(
        """(() => {
          const view = [...document.querySelectorAll(".tasks-view")]
            .find(node => getComputedStyle(node).display !== "none");
          const rows = [...view.querySelectorAll(".job-row[aria-label]")];
          return rows.length === 1
            && rows[0].getAttribute("aria-label").includes("Atlas privacy review")
            && !rows[0].getAttribute("aria-label").includes("Project:")
            && view.querySelector(".task-project-tag") === null;
        })()""",
        "Work Tasks stay Project scoped without redundant ownership labels",
    )
    browser.screenshot(screenshots / "after-work-project-scoped-tasks.png")

    browser.click("button", text="Delegate")
    browser.click("nav button", text="Tasks")
    browser.wait(".job-row[aria-label]", text="Atlas privacy review")
    browser.assert_true(
        """(() => {
          const view = [...document.querySelectorAll(".tasks-view")]
            .find(node => getComputedStyle(node).display !== "none");
          const labels = [...view.querySelectorAll(".job-row[aria-label]")]
            .map(node => node.getAttribute("aria-label"));
          return labels.some(label =>
              label.includes("Atlas privacy review")
              && label.includes("Project: Atlas private ops"))
            && labels.some(label =>
              label.includes("Beacon rollout review")
              && label.includes("Project: Beacon release"))
            && labels.some(label =>
              label.includes("Beacon release plan")
              && label.includes("Project: Beacon release"))
            && view.querySelectorAll(".task-project-tag").length >= 3;
        })()""",
        "Delegate global list attributes classic Tasks and plans",
    )
    browser.screenshot(screenshots / "after-delegate-global-tasks-list.png")

    browser.click(".tasks-head button", text="Board")
    browser.wait(".kanban-card[aria-label]", text="Beacon rollout review")
    browser.assert_true(
        """[...document.querySelectorAll(".kanban-card[aria-label]")]
          .filter(node => getComputedStyle(node).display !== "none")
          .every(node => node.getAttribute("aria-label").includes("Project:"))""",
        "Delegate global board card names include Project",
    )
    browser.screenshot(screenshots / "after-delegate-global-tasks-board.png")

    browser.click(".tasks-head button", text="Review")
    browser.wait(".job-row[aria-label]", text="Beacon rollout review")
    browser.assert_true(
        """[...document.querySelectorAll(".job-row[aria-label]")]
          .filter(node => getComputedStyle(node).display !== "none")
          .every(node => node.getAttribute("aria-label")
            .includes("Project: Beacon release"))""",
        "Delegate review rows include owning Project",
    )
    browser.screenshot(screenshots / "after-delegate-global-tasks-review.png")

    browser.click("nav button", text="Master")
    browser.wait("select[aria-label='Master message target']")
    browser.select_text(
        "select[aria-label='Master message target']", "Beacon release (General)"
    )
    browser.wait(
        ".master-target-warning",
        text="Sending will Focus Master on Beacon release",
    )
    browser.wait(
        ".master-target-warning",
        text="Identity: General · Area: Master chooses",
    )
    browser.screenshot(screenshots / "after-master-send-target.png")

    browser.click("button", text="Work")
    browser.press("m", code="KeyM", modifiers=2 | 8)
    browser.wait(
        "[role='dialog'][aria-modal='true'][aria-labelledby='master-popup-title']"
    )
    browser.wait(".master-popup-project", text="Beacon release")
    browser.wait(
        ".master-popup-context",
        text="Identity: General · Area: Master chooses",
    )
    browser.assert_true(
        """document.activeElement?.getAttribute("aria-label") === "Message Master" """,
        "Master popup shortcut places keyboard focus in composer",
    )
    browser.screenshot(screenshots / "after-master-popup.png")
    browser.press("Escape")
    browser.assert_true(
        """document.activeElement?.getAttribute("aria-label")
          === "Open Master popup" """,
        "Escape closes popup and restores trigger focus",
    )

    browser.click("nav button", text="Tasks")
    browser.click("button.attention-trigger[aria-label*='attention item']")
    browser.click(".attention-main", text="Choose the Beacon rollout window")
    browser.wait(".task-project-context", text="Beacon release")
    browser.wait(
        ".task-project-context",
        text="Identity: General · Area: Operations",
    )
    browser.wait(
        ".task-project-context",
        text="Project locked to this Task",
    )
    browser.wait(".task-project-context", text="Work remains Atlas private ops")
    browser.assert_true(
        """(() => {
          const switcher = [...document.querySelectorAll(
            "button[aria-label^='Active project:']")]
            .find(node => getComputedStyle(node).display !== "none");
          const back = [...document.querySelectorAll(
            "button[aria-label='Back to Tasks']")]
            .find(node => getComputedStyle(node).display !== "none");
          const tools = document.querySelector("[aria-label='Tools']");
          return switcher?.disabled === true
            && switcher.getAttribute("aria-label")
              .includes("Atlas private ops (locked)")
            && Boolean(back) && back.disabled === false
            && back.textContent.includes("Back to Tasks")
            && tools?.hidden === true;
        })()""",
        "cross-Project Task locks ownership, preserves Work, and exposes return",
    )
    browser.screenshot(screenshots / "after-cross-project-attention-task.png")

    browser.resize(390, 844)
    browser.wait("button[aria-label='Menu']")
    browser.focus("button[aria-label='Menu']")
    browser.press("Enter")
    browser.wait_true(
        """document.querySelector("#mobile-nav-drawer")
          ?.classList.contains("is-open") === true
          && document.activeElement?.getAttribute("aria-label") === "Close menu" """,
        "mobile drawer opens and receives focus",
    )
    browser.assert_true(
        """document.activeElement?.getAttribute("aria-label") === "Close menu" """,
        "mobile menu opened by keyboard moves focus into the drawer",
    )
    browser.screenshot(screenshots / "after-mobile-keyboard-navigation.png")
    browser.press("Escape")
    browser.wait_true(
        """document.querySelector("#mobile-nav-drawer")
          ?.classList.contains("is-open") === false
          && document.activeElement?.getAttribute("aria-label") === "Menu" """,
        "mobile drawer closes and restores focus",
    )
    browser.assert_true(
        """document.activeElement?.getAttribute("aria-label") === "Menu" """,
        "mobile Escape restores menu button focus",
    )
    browser.screenshot(screenshots / "after-mobile-cross-project-task.png")
    browser.resize(1440, 1000)

    browser.click("button[aria-label='Back to Tasks']")
    browser.wait("h1", text="Tasks")
    browser.wait(
        "button[aria-label^='Active project:']", text="Atlas private ops"
    )

    browser.set_network_latency(1200)
    browser.navigate(f"/#task/{beacon_task_id}")
    browser.wait(".center-screen", text="Resolving Task Project...")
    browser.assert_true(
        """document.querySelector("[aria-label='Tools']") === null
          && document.querySelector("button[aria-label='Files']") === null
          && document.querySelector("button[aria-label='Preview']") === null""",
        "Task permalink suppresses Project tools before ownership synchronization",
    )
    browser.screenshot(screenshots / "during-task-permalink-project-sync.png")
    browser.set_network_latency(0)
    browser.wait(".task-project-context", text="Beacon release")
    browser.wait(
        ".task-project-context",
        text="Identity: General · Area: Operations",
    )
    browser.assert_true(
        """(() => {
          const switcher = [...document.querySelectorAll(
            "button[aria-label^='Active project:']")]
            .find(node => getComputedStyle(node).display !== "none");
          const tools = document.querySelector("[aria-label='Tools']");
          const context = document.querySelector(".task-project-context");
          return switcher?.disabled === true
            && switcher.getAttribute("aria-label")
              .includes("Beacon release (locked)")
            && Boolean(tools) && tools.hidden === false
            && !context.textContent.includes("Work remains Atlas private ops");
        })()""",
        "Task permalink atomically adopts and locks its owning Project",
    )
    browser.screenshot(screenshots / "after-task-permalink-project-sync.png")
    browser.click("button[aria-label='Back to Tasks']")
    browser.wait("h1", text="Tasks")
    browser.wait(
        "button[aria-label^='Active project:']", text="Beacon release"
    )

    browser.reload()
    browser.wait(
        "button[aria-label^='Active project:']", text="Beacon release"
    )
    browser.screenshot(screenshots / "after-work-project-refresh.png")

    _request(
        f"{base_url}/api/projects/beacon",
        method="DELETE",
        token=token,
    )
    browser.reload()
    browser.wait(
        ".project-fallback-notice",
        text='Saved Work Project "Beacon release" is no longer available.',
    )
    browser.wait(
        "button[aria-label^='Active project:']", text="Atlas private ops"
    )
    browser.screenshot(screenshots / "after-removed-project-fallback.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screenshots",
        type=Path,
        help="optional directory for browser evidence PNGs",
    )
    args = parser.parse_args()
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")
    _build_web()
    screenshot_root = args.screenshots
    with tempfile.TemporaryDirectory(
        prefix="proxima-ownership-browser-"
    ) as raw_fixture:
        fixture = Path(raw_fixture)
        home = fixture / "home"
        workspace = fixture / "workspace"
        runner_home = fixture / "runner-home"
        fake_bin = fixture / "bin"
        for path in (home, workspace, runner_home, fake_bin):
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
            "PROXIMA_SINGLE_USER_NAME": "ownership-browser",
            "PROXIMA_UPDATE_CHECK": "0",
            "PROXIMA_WEB_DIST": str(WEB_DIR / "dist"),
            "PROXIMA_WORKSPACE_ROOT": str(workspace),
            "PYTHONPATH": str(ROOT / "apps" / "api"),
            "TMPDIR": str(fixture),
        }
        log_path = fixture / "server.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                [
                    str(API_PYTHON),
                    str(ROOT / "apps" / "api" / "scripts" / "serve.py"),
                ],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            browser = None
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
                            raise RuntimeError(
                                "disposable server readiness timed out"
                            )
                        time.sleep(0.1)
                token = str(
                    _request(
                        f"{base_url}/auth/set-password",
                        method="POST",
                        body={"password": "ownership-browser-password"},
                    )["token"]
                )
                fixture_ids = _seed(base_url, token, fixture)
                evidence = screenshot_root or (fixture / "screenshots")
                browser = Browser(
                    executable=_browser_executable(),
                    base_url=base_url,
                    profile=fixture / "browser-profile",
                    token=token,
                )
                _run_acceptance(
                    browser,
                    base_url=base_url,
                    token=token,
                    beacon_task_id=int(fixture_ids["beacon_job"]["id"]),
                    screenshots=evidence,
                )
                print(
                    json.dumps(
                        {
                            "fixture": "disposable",
                            "ok": True,
                            "screenshots": str(evidence),
                            "seed": fixture_ids,
                        },
                        sort_keys=True,
                    )
                )
            finally:
                if browser is not None:
                    browser.close()
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if server.poll() is None:
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(server.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        server.wait()


if __name__ == "__main__":
    main()

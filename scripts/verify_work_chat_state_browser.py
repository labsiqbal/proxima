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
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
PROBE_ROOT = ROOT / "trusted-probes" / "safe-update"
ATLAS_DRAFT = "Atlas draft survives Work and Delegate."
BOREALIS_DRAFT = "Borealis draft stays isolated from Atlas."
WORKFLOW_TITLE = "URL restore plan"
DESIGN_ID = "url-restored-design"
DESIGN_TITLE = "URL Restored Design"


def _request(
    url: str,
    *,
    method: str = "GET",
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
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            value = json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(
            f"{method} {url} failed with {exc.code}: {exc.read().decode()}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected response from {url}")
    return value


def _free_port() -> int:
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
        raise RuntimeError(completed.stderr or completed.stdout or "web build failed")


class Browser:
    def __init__(
        self,
        *,
        executable: str,
        base_url: str,
        token: str,
        profile: Path,
        width: int,
        height: int,
        mobile: bool,
        start_url: str | None = None,
    ) -> None:
        sys.path.insert(0, str(PROBE_ROOT))
        from browser import _WebSocket, _evaluation

        self._WebSocket = _WebSocket
        self._evaluation = _evaluation
        self.base_url = base_url
        self.connection = None
        debug_port = _free_port()
        profile.mkdir(mode=0o700, exist_ok=True)
        command = [
            executable,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-component-extensions-with-background-pages",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={debug_port}",
            f"--window-size={width},{height}",
            "about:blank",
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 20
        page = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("browser exited before debugging was ready")
            try:
                with urlopen(
                    f"http://127.0.0.1:{debug_port}/json/list",
                    timeout=1,
                ) as response:
                    pages = json.loads(response.read())
                page = next(
                    item
                    for item in pages
                    if item.get("type") == "page"
                    and item.get("url") == "about:blank"
                    and isinstance(item.get("webSocketDebuggerUrl"), str)
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
        if mobile:
            self.connection.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 2,
                    "mobile": True,
                },
            )
        try:
            self.navigate(start_url)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.call("Browser.close")
            except Exception:
                pass
            self.connection.close()
            self.connection = None
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()

    def evaluate(self, expression: str) -> object:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        return self._evaluation(self.connection, expression)

    def wait_for(
        self, expression: str, description: str, timeout: float = 10
    ) -> object:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.evaluate(expression)
            if result:
                return result
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {description}")

    def navigate(self, url: str | None = None) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call("Page.navigate", {"url": url or f"{self.base_url}/"})
        self.wait_for(
            "document.readyState === 'complete'",
            "document load",
            timeout=20,
        )
        self.wait_for(
            "document.querySelector('.shell-mode-switch') !== null",
            "authenticated application shell",
            timeout=20,
        )
        # The tour mounts after the shell while owner preferences finish loading.
        # Give it a short window and dismiss it before any interaction or evidence.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            dismissed = self.evaluate(
                """(() => {
                  const skip = [...document.querySelectorAll('button')]
                    .find(node => (node.textContent || '').includes('Skip tour'));
                  if (!skip) return false;
                  skip.click();
                  return true;
                })()"""
            )
            if dismissed:
                break
            time.sleep(0.05)

    def history(self, direction: str) -> None:
        if direction not in {"back", "forward"}:
            raise ValueError(f"unsupported history direction: {direction}")
        self.evaluate(f"history.{direction}()")
        time.sleep(0.15)

    def current_url(self) -> str:
        value = self.evaluate("location.href")
        if not isinstance(value, str):
            raise AssertionError("browser URL was unavailable")
        return value

    def reload(self) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call("Page.reload", {"ignoreCache": True})
        self.wait_for(
            "document.readyState === 'complete'",
            "document reload",
            timeout=20,
        )
        self.wait_for(
            "document.querySelector('.shell-mode-switch') !== null",
            "application shell after reload",
            timeout=20,
        )

    def click(self, selector: str, text: str | None = None) -> None:
        selector_json = json.dumps(selector)
        text_json = json.dumps(text)
        clicked = self.wait_for(
            f"""(() => {{
              const wanted = {text_json};
              const node = [...document.querySelectorAll({selector_json})]
                .find(item => wanted === null || (item.textContent || '').trim().includes(wanted));
              if (!node) return false;
              node.click();
              return true;
            }})()""",
            f"click target {selector} {text or ''}".strip(),
        )
        if not clicked:
            raise AssertionError(f"could not click {selector}")
        time.sleep(0.1)

    def click_within(
        self, selector: str, container_text: str, target_text: str
    ) -> None:
        self.wait_for(
            f"""(() => {{
              const container = [...document.querySelectorAll({json.dumps(selector)})]
                .find(node => (node.textContent || '').includes({json.dumps(container_text)}));
              const target = container && [...container.querySelectorAll('button, [role="button"]')]
                .find(node => (node.textContent || '').trim().includes({json.dumps(target_text)}));
              if (!target) return false;
              target.click();
              return true;
            }})()""",
            f"{target_text} within {container_text}",
        )
        time.sleep(0.1)

    def set_project(self, name: str, *, mobile: bool = False) -> None:
        if mobile and not self.evaluate(
            "document.querySelector('.sidebar.is-open') !== null"
        ):
            self.click("button[aria-label='Menu']")
        self.click("button[aria-label^='Active project:']")
        self.click("[role='option'], [role='listbox'] button", name)
        self.wait_for(
            f"""(() => {{
              const node = document.querySelector("button[aria-label^='Active project:']");
              return node && node.getAttribute('aria-label').includes({json.dumps(name)});
            }})()""",
            f"active project {name}",
        )

    def open_destination(self, label: str, *, mobile: bool = False) -> None:
        if mobile and not self.evaluate(
            "document.querySelector('.sidebar.is-open') !== null"
        ):
            self.click("button[aria-label='Menu']")
        self.click("nav button", label)
        time.sleep(0.15)

    def set_draft(self, value: str, start: int, end: int) -> None:
        result = self.evaluate(
            """(() => {
              const node = document.querySelector(".surface-pane[data-surface='chat'] textarea");
              if (!node) return false;
              node.focus();
              node.select();
              window.__workChatTextarea = node;
              return true;
            })()"""
        )
        if not result:
            raise AssertionError("chat composer was unavailable")
        if self.connection is None:
            raise RuntimeError("browser is closed")
        self.connection.call("Input.insertText", {"text": value})
        self.evaluate(
            f"""(() => {{
              const node = document.querySelector(".surface-pane[data-surface='chat'] textarea");
              node.focus();
              node.setSelectionRange({start}, {end});
              document.dispatchEvent(new Event('selectionchange', {{bubbles: true}}));
              node.dispatchEvent(new KeyboardEvent('keyup', {{
                bubbles: true,
                key: 'Shift',
              }}));
              return true;
            }})()"""
        )
        time.sleep(0.1)

    def attach(self, path: Path) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        document = self.connection.call("DOM.getDocument")
        root_id = document["root"]["nodeId"]
        query = self.connection.call(
            "DOM.querySelector",
            {
                "nodeId": root_id,
                "selector": ".surface-pane[data-surface='chat'] input[name='attachments']",
            },
        )
        node_id = query.get("nodeId")
        if not node_id:
            raise AssertionError("attachment input was unavailable")
        self.connection.call(
            "DOM.setFileInputFiles",
            {"nodeId": node_id, "files": [str(path)]},
        )
        self.wait_for(
            "document.querySelector('.composer-att') !== null",
            "pending attachment",
        )

    def prepare_state(self, draft: str, attachment: Path) -> dict:
        self.wait_for(
            "document.querySelector(\".surface-pane[data-surface='chat'] textarea\") !== null",
            "chat composer",
        )
        self.set_draft(draft, 6, 17)
        self.click(".composer-modes button", "Brainstorm")
        self.attach(attachment)
        # File chooser focus can legitimately collapse a DOM selection. Restore the
        # user's final selection immediately before testing navigation preservation.
        self.set_draft(draft, 6, 17)
        state = self.evaluate(
            """(() => {
              const thread = document.querySelector(".surface-pane[data-surface='chat'] .thread");
              if (!thread) return null;
              const top = Math.max(1, thread.scrollHeight - thread.clientHeight - 180);
              thread.scrollTop = top;
              thread.dispatchEvent(new Event('scroll', {bubbles: true}));
              return {
                top: thread.scrollTop,
                height: thread.scrollHeight,
                client: thread.clientHeight,
              };
            })()"""
        )
        if not isinstance(state, dict) or not state["top"]:
            raise AssertionError(
                f"chat fixture did not produce a scroll anchor: {state}"
            )
        return state

    def assert_state(
        self,
        draft: str,
        *,
        scroll_top: float | None = None,
        attachment: bool,
        same_node: bool,
    ) -> None:
        state = self.evaluate(
            """(() => {
              const textarea = document.querySelector(".surface-pane[data-surface='chat'] textarea");
              const thread = document.querySelector(".surface-pane[data-surface='chat'] .thread");
              const activeMode = document.querySelector(".composer-modes button.active");
              return textarea && thread ? {
                draft: textarea.value,
                start: textarea.selectionStart,
                end: textarea.selectionEnd,
                mode: (activeMode?.textContent || '').trim(),
                attachment: document.querySelector('.composer-att') !== null,
                sameNode: window.__workChatTextarea === textarea,
                scrollTop: thread.scrollTop,
              } : null;
            })()"""
        )
        if not isinstance(state, dict):
            raise AssertionError("chat state was unavailable")
        if state["draft"] != draft:
            raise AssertionError(f"draft changed: {state}")
        if (state["start"], state["end"]) != (6, 17):
            raise AssertionError(f"selection changed: {state}")
        if state["mode"] != "Brainstorm":
            raise AssertionError(f"composer mode changed: {state}")
        if bool(state["attachment"]) != attachment:
            raise AssertionError(f"pending attachment changed: {state}")
        if bool(state["sameNode"]) != same_node:
            raise AssertionError(f"chat composer remounted: {state}")
        if scroll_top is not None and abs(float(state["scrollTop"]) - scroll_top) > 2:
            raise AssertionError(f"scroll anchor changed: {state}")

    def wait_for_scroll(self, scroll_top: float) -> None:
        try:
            self.wait_for(
                f"""(() => {{
                  const thread = document.querySelector(
                    ".surface-pane[data-surface='chat'] .thread"
                  );
                  return thread && Math.abs(thread.scrollTop - {scroll_top}) <= 2;
                }})()""",
                f"chat scroll anchor {scroll_top}",
                timeout=20,
            )
        except AssertionError:
            detail = self.evaluate(
                f"""(() => {{
                  const thread = document.querySelector(
                    ".surface-pane[data-surface='chat'] .thread"
                  );
                  const entry = Object.entries(localStorage)
                    .find(([key]) => key.startsWith('proxima.work-chat-state.v1.'));
                  const stored = entry ? JSON.parse(entry[1]) : null;
                  return {{
                    expected: {scroll_top},
                    scrollTop: thread?.scrollTop ?? null,
                    scrollHeight: thread?.scrollHeight ?? null,
                    clientHeight: thread?.clientHeight ?? null,
                    maxScroll: thread
                      ? thread.scrollHeight - thread.clientHeight
                      : null,
                    messageCount: document.querySelectorAll(
                      ".surface-pane[data-surface='chat'] .msg"
                    ).length,
                    stored,
                    url: location.href,
                  }};
                }})()"""
            )
            raise AssertionError(
                f"timed out waiting for chat scroll anchor {scroll_top}: {detail!r}"
            ) from None

    def switch_mode(self, label: str) -> None:
        self.click(".shell-mode-switch button", label)
        mode = label.lower()
        self.wait_for(
            f"new URL(location.href).searchParams.get('mode') === {json.dumps(mode)}",
            f"{label} mode URL",
        )

    def screenshot(self, path: Path) -> None:
        if self.connection is None:
            raise RuntimeError("browser is closed")
        path.parent.mkdir(parents=True, exist_ok=True)
        captured = self.connection.call(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False},
        )
        path.write_bytes(base64.b64decode(captured["data"]))


def _launch_browser(**kwargs: object) -> Browser:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return Browser(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
    if last_error is None:
        raise RuntimeError("browser launch did not run")
    raise last_error


def _seed_fixture(
    base_url: str,
    fixture: Path,
) -> tuple[str, dict[str, int], int]:
    token = str(
        _request(
            f"{base_url}/auth/set-password",
            method="POST",
            body={"password": "candidate-browser-password"},
        )["token"]
    )
    sessions: dict[str, int] = {}
    root_id = str(_request(f"{base_url}/api/fs/dirs", token=token)["root_id"])
    # Seed Atlas last so it is the most recent chat and therefore the explicit
    # reload fallback used by the production shell.
    for slug, name in (
        ("borealis", "Borealis Ops"),
        ("atlas", "Atlas Launch Lab"),
    ):
        project_path = fixture / "workspace" / slug
        project_path.mkdir(parents=True)
        _request(
            f"{base_url}/api/projects/link",
            method="POST",
            body={
                "name": name,
                "path": str(project_path),
                "slug": slug,
                "root_id": root_id,
            },
            token=token,
        )
        session = _request(
            f"{base_url}/api/sessions",
            method="POST",
            body={"title": f"{name} planning", "project_slug": slug},
            token=token,
        )
        session_id = int(session["id"])
        sessions[slug] = session_id
        for index in range(24):
            for role, prefix in (
                ("user", "Owner checkpoint"),
                ("assistant", "Agent response"),
            ):
                _request(
                    f"{base_url}/api/sessions/{session_id}/messages",
                    method="POST",
                    body={
                        "role": role,
                        "content": (
                            f"{prefix} {index + 1}: preserve this long browser "
                            "conversation while moving through the workspace."
                        ),
                    },
                    token=token,
                )
    workflow = _request(
        f"{base_url}/api/graph/jobs",
        method="POST",
        body={
            "title": WORKFLOW_TITLE,
            "project_slug": "atlas",
            "graph": {
                "nodes": [
                    {
                        "id": "research",
                        "name": "Research",
                        "instruction": "Collect the launch facts.",
                    },
                    {
                        "id": "review",
                        "name": "Review",
                        "instruction": "Review the facts.",
                        "depends_on": ["research"],
                    },
                ]
            },
        },
        token=token,
    )
    workflow_id = int(workflow["id"])
    listed = _request(
        f"{base_url}/api/graph/jobs?project_slug=atlas",
        token=token,
    )
    seeded = next(
        (
            item
            for item in listed.get("items", [])
            if int(item.get("id", 0)) == workflow_id
        ),
        None,
    )
    if (
        seeded is None
        or seeded.get("project_slug") != "atlas"
        or seeded.get("status") != "queued"
        or seeded.get("engine") != "graph"
        or seeded.get("workflow_id") is not None
    ):
        raise AssertionError(
            "seeded workflow is not an Atlas graph draft: "
            f"created={workflow!r} listed={listed!r}"
        )
    design_dir = (
        fixture / "workspace" / "atlas" / "ops" / "artifacts" / "design" / DESIGN_ID
    )
    design_dir.mkdir(parents=True)
    (design_dir / "scene.json").write_text(
        json.dumps(
            {
                "id": DESIGN_ID,
                "type": "graphic",
                "title": DESIGN_TITLE,
                "artboards": [
                    {
                        "id": "artboard-1",
                        "width": 1080,
                        "height": 1080,
                        "background": "#ffffff",
                        "layers": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    design_listing = _request(
        f"{base_url}/api/projects/atlas/tree?path=artifacts%2Fdesign",
        token=token,
    )
    if not any(
        entry.get("name") == DESIGN_ID and entry.get("type") == "dir"
        for entry in design_listing.get("entries", [])
    ):
        raise AssertionError(
            "seeded Design scene is outside the Atlas Ops filesystem: "
            f"{design_listing!r}"
        )
    attachment = fixture / "safe-note.txt"
    attachment.write_text("safe pending attachment fixture\n", encoding="utf-8")
    return token, sessions, workflow_id


def _wait_for_server(base_url: str, server: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(log_path.read_text(encoding="utf-8"))
        try:
            _request(f"{base_url}/api/health")
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("disposable server readiness timed out")


def _run(
    *,
    screenshots_dir: Path | None,
    skip_build: bool,
) -> None:
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")
    if not skip_build:
        _build_web()
    temp_parent = ROOT / ".tmp"
    temp_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="proxima-work-chat-browser-",
        dir=temp_parent,
    ) as raw_fixture:
        fixture = Path(raw_fixture)
        for path in (
            fixture / "runner-home",
            fixture / "bin",
            fixture / "workspace",
        ):
            path.mkdir(parents=True)
        fixture_codex = PROBE_ROOT / "codex-fixture"
        fake_codex = fixture / "bin" / "codex"
        shutil.copyfile(fixture_codex, fake_codex, follow_symlinks=False)
        fake_codex.chmod(0o555)
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "PATH": f"{fixture / 'bin'}:/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_CLAUDE_LIVE_HOME": "0",
            "PROXIMA_DB_PATH": str(fixture / "candidate.db"),
            "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "1",
            "PROXIMA_FEATURE_SAFE_SELF_UPDATE": "0",
            "PROXIMA_HERMES_PROFILES_ROOT": str(fixture / "runner-home"),
            "PROXIMA_LINK_ROOTS": str(fixture / "workspace"),
            "PROXIMA_PORT": str(port),
            "PROXIMA_PROJECTCTL_COMMAND": "/usr/bin/true",
            "PROXIMA_REFRESH_CREDENTIALS": "0",
            "PROXIMA_SINGLE_USER": "1",
            "PROXIMA_SINGLE_USER_NAME": "work-chat-browser",
            "PROXIMA_UPDATE_CHECK": "0",
            "PROXIMA_WEB_DIST": str(WEB_DIR / "dist"),
            "PROXIMA_WORKSPACE_ROOT": str(fixture / "workspace"),
            "PYTHONPATH": str(ROOT / "apps" / "api"),
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
            desktop = None
            mobile = None
            try:
                _wait_for_server(base_url, server, log_path)
                token, sessions, workflow_id = _seed_fixture(base_url, fixture)
                executable = _browser_executable()
                attachment = fixture / "safe-note.txt"
                desktop = _launch_browser(
                    executable=executable,
                    base_url=base_url,
                    token=token,
                    profile=fixture / "desktop-profile",
                    width=1440,
                    height=1000,
                    mobile=False,
                )
                desktop.set_project("Atlas Launch Lab")
                desktop.wait_for(
                    "document.body.innerText.includes('Atlas Launch Lab planning')",
                    "Atlas chat session",
                )
                atlas_scroll = desktop.prepare_state(ATLAS_DRAFT, attachment)

                desktop.open_destination("Tasks")
                desktop.open_destination("Chat")
                desktop.assert_state(
                    ATLAS_DRAFT,
                    scroll_top=float(atlas_scroll["top"]),
                    attachment=True,
                    same_node=True,
                )
                desktop.history("back")
                desktop.wait_for(
                    "new URL(location.href).searchParams.get('view') === 'activity'",
                    "native Back to Tasks",
                )
                desktop.history("forward")
                desktop.wait_for(
                    "new URL(location.href).searchParams.get('view') === 'chat'",
                    "native Forward to Chat",
                )
                desktop.assert_state(
                    ATLAS_DRAFT,
                    scroll_top=float(atlas_scroll["top"]),
                    attachment=True,
                    same_node=True,
                )

                for _ in range(3):
                    desktop.switch_mode("Delegate")
                    desktop.switch_mode("Work")
                    desktop.assert_state(
                        ATLAS_DRAFT,
                        scroll_top=float(atlas_scroll["top"]),
                        attachment=True,
                        same_node=True,
                    )
                if screenshots_dir:
                    desktop.screenshot(
                        screenshots_dir / "desktop-work-state-after-mode.png"
                    )
                desktop.wait_for(
                    f"""(() => {{
                      const entry = Object.entries(localStorage)
                        .find(([key]) => key.startsWith('proxima.work-chat-state.v1.'));
                      if (!entry) return false;
                      const state = JSON.parse(entry[1])['atlas:{sessions["atlas"]}'];
                      return state && Math.abs(state.scrollTop - {float(atlas_scroll["top"])}) <= 2;
                    }})()""",
                    "persisted Atlas scroll anchor before reload",
                )
                before_reload_url = desktop.current_url()
                if f"session={sessions['atlas']}" not in before_reload_url:
                    raise AssertionError(
                        "session missing from Work URL before reload: "
                        f"{before_reload_url!r} expected session={sessions['atlas']}"
                    )

                desktop.reload()
                url_samples: list[str] = []
                for _ in range(40):
                    url_samples.append(str(desktop.current_url()))
                    if f"session={sessions['atlas']}" in url_samples[-1]:
                        break
                    time.sleep(0.1)
                desktop.wait_for(
                    "document.body.innerText.includes('Atlas Launch Lab planning')",
                    "Atlas session after reload",
                )
                after_reload_url = desktop.current_url()
                if f"session={sessions['atlas']}" not in after_reload_url:
                    raise AssertionError(
                        "session missing from Work URL after reload: "
                        f"before={before_reload_url!r} after={after_reload_url!r} "
                        f"samples={url_samples!r}"
                    )
                desktop.wait_for_scroll(float(atlas_scroll["top"]))
                desktop.assert_state(
                    ATLAS_DRAFT,
                    scroll_top=float(atlas_scroll["top"]),
                    attachment=True,
                    same_node=False,
                )

                restart_url = desktop.current_url()
                desktop.close()
                desktop = _launch_browser(
                    executable=executable,
                    base_url=base_url,
                    token=token,
                    profile=fixture / "desktop-profile",
                    width=1440,
                    height=1000,
                    mobile=False,
                    start_url=restart_url,
                )
                desktop.wait_for(
                    "document.body.innerText.includes('Atlas Launch Lab planning')",
                    "Atlas session after PWA-style restart",
                )
                desktop.wait_for_scroll(float(atlas_scroll["top"]))
                desktop.assert_state(
                    ATLAS_DRAFT,
                    scroll_top=float(atlas_scroll["top"]),
                    attachment=True,
                    same_node=False,
                )
                desktop.evaluate(
                    """(() => {
                      window.__workChatTextarea = document.querySelector(
                        ".surface-pane[data-surface='chat'] textarea"
                      );
                      return true;
                    })()"""
                )

                desktop.set_project("Borealis Ops")
                desktop.wait_for(
                    "document.body.innerText.includes('Borealis Ops planning')",
                    "Borealis chat session",
                )
                borealis_scroll = desktop.prepare_state(BOREALIS_DRAFT, attachment)
                desktop.switch_mode("Delegate")
                desktop.switch_mode("Work")
                desktop.assert_state(
                    BOREALIS_DRAFT,
                    scroll_top=float(borealis_scroll["top"]),
                    attachment=True,
                    same_node=True,
                )
                desktop.set_project("Atlas Launch Lab")
                desktop.wait_for(
                    "document.body.innerText.includes('Atlas Launch Lab planning')",
                    "return to Atlas chat",
                )
                atlas_value = desktop.evaluate(
                    "document.querySelector(\".surface-pane[data-surface='chat'] textarea\")?.value"
                )
                if atlas_value != ATLAS_DRAFT:
                    raise AssertionError(
                        f"Atlas draft was replaced by another project: {atlas_value!r}"
                    )

                desktop.open_destination("Workflows")
                desktop.wait_for(
                    "new URL(location.href).searchParams.get('view') === 'workflows'",
                    "Workflows destination URL",
                )
                browser_graph = desktop.evaluate(
                    """(async () => {
                      const response = await fetch('/api/graph/jobs?project_slug=atlas');
                      return {
                        status: response.status,
                        body: await response.json(),
                        resources: performance.getEntriesByType('resource')
                          .map(entry => entry.name)
                          .filter(name => name.includes('/api/graph/jobs')),
                        activeProject: document.querySelector(
                          "button[aria-label^='Active project:']"
                        )?.getAttribute('aria-label'),
                        tabs: [...document.querySelectorAll(
                          ".workflow-home-tabs [role='tab']"
                        )].map(node => ({
                          text: (node.textContent || '').trim(),
                          selected: node.getAttribute('aria-selected'),
                        })),
                      };
                    })()"""
                )
                if not isinstance(browser_graph, dict):
                    raise AssertionError("browser graph inspection was unavailable")
                browser_items = browser_graph.get("body", {}).get("items", [])
                browser_seed = next(
                    (
                        item
                        for item in browser_items
                        if int(item.get("id", 0)) == workflow_id
                    ),
                    None,
                )
                if (
                    browser_graph.get("status") != 200
                    or browser_seed is None
                    or browser_seed.get("project_slug") != "atlas"
                    or browser_seed.get("status") != "queued"
                    or browser_seed.get("engine") != "graph"
                    or not any(
                        "/api/graph/jobs?project_slug=atlas" in name
                        for name in browser_graph.get("resources", [])
                    )
                ):
                    raise AssertionError(
                        "browser did not receive the seeded Atlas graph draft: "
                        f"{browser_graph!r}"
                    )
                desktop.click("button[role='tab']", "Drafts")
                desktop.wait_for(
                    """document.querySelector(
                      ".workflow-home-tabs [role='tab'][aria-selected='true']"
                    )?.textContent.includes('Drafts')""",
                    "Drafts tab",
                )
                desktop.wait_for(
                    f"document.body.innerText.includes({json.dumps(WORKFLOW_TITLE)})",
                    "workflow draft row",
                    timeout=20,
                )
                desktop.click_within(
                    ".workflow-home-row",
                    WORKFLOW_TITLE,
                    "Edit",
                )
                desktop.wait_for(
                    f"""new URL(location.href).searchParams.get('workflow')
                      === {json.dumps(str(workflow_id))}""",
                    "workflow deep URL",
                )
                desktop.wait_for(
                    f"document.body.innerText.includes({json.dumps(WORKFLOW_TITLE)})",
                    "workflow editor",
                )
                if screenshots_dir:
                    desktop.screenshot(
                        screenshots_dir / "desktop-workflow-deep-state.png"
                    )
                desktop.reload()
                desktop.wait_for(
                    f"""new URL(location.href).searchParams.get('workflow')
                      === {json.dumps(str(workflow_id))}
                      && document.body.innerText.includes({json.dumps(WORKFLOW_TITLE)})""",
                    "workflow after reload",
                    timeout=20,
                )
                desktop.history("back")
                desktop.wait_for(
                    """new URL(location.href).searchParams.get('view') === 'workflows'
                      && !new URL(location.href).searchParams.has('workflow')""",
                    "workflow home URL after native Back",
                )
                desktop.wait_for(
                    f"""document.querySelector(
                      ".workflow-home-tabs [role='tab'][aria-selected='true']"
                    )?.textContent.includes('Drafts')
                      && document.body.innerText.includes({json.dumps(WORKFLOW_TITLE)})""",
                    "Drafts item after native Back",
                )
                desktop.history("forward")
                desktop.wait_for(
                    f"""new URL(location.href).searchParams.get('workflow')
                      === {json.dumps(str(workflow_id))}
                      && document.body.innerText.includes({json.dumps(WORKFLOW_TITLE)})""",
                    "workflow editor after native Forward",
                )

                desktop.open_destination("Design")
                desktop.wait_for(
                    "document.body.innerText.includes('Your designs (1)')",
                    "Design start with saved design",
                    timeout=20,
                )
                desktop.click("button", "Your designs (1)")
                desktop.click("[role='button']", DESIGN_TITLE)
                desktop.wait_for(
                    f"""new URL(location.href).searchParams.get('design')
                      === {json.dumps(DESIGN_ID)}
                      && document.body.innerText.includes({json.dumps(DESIGN_TITLE)})""",
                    "Design deep URL",
                    timeout=20,
                )
                if screenshots_dir:
                    desktop.screenshot(
                        screenshots_dir / "desktop-design-deep-state.png"
                    )
                desktop.reload()
                desktop.wait_for(
                    f"""new URL(location.href).searchParams.get('design')
                      === {json.dumps(DESIGN_ID)}
                      && document.body.innerText.includes({json.dumps(DESIGN_TITLE)})""",
                    "Design after reload",
                    timeout=20,
                )
                desktop.history("back")
                desktop.wait_for(
                    """new URL(location.href).searchParams.get('view') === 'design'
                      && !new URL(location.href).searchParams.has('design')
                      && document.body.innerText.includes('What do you want to make?')""",
                    "Design home after native Back",
                    timeout=20,
                )
                desktop.history("forward")
                desktop.wait_for(
                    f"""new URL(location.href).searchParams.get('design')
                      === {json.dumps(DESIGN_ID)}
                      && new URL(location.href).searchParams.get('session')
                        === {json.dumps(str(sessions["atlas"]))}
                      && document.body.innerText.includes({json.dumps(DESIGN_TITLE)})""",
                    "Design after native Forward",
                    timeout=20,
                )
                design_restart_url = desktop.current_url()
                desktop.close()
                desktop = _launch_browser(
                    executable=executable,
                    base_url=base_url,
                    token=token,
                    profile=fixture / "desktop-profile",
                    width=1440,
                    height=1000,
                    mobile=False,
                    start_url=design_restart_url,
                )
                try:
                    desktop.wait_for(
                        f"""new URL(location.href).searchParams.get('design')
                          === {json.dumps(DESIGN_ID)}
                          && new URL(location.href).searchParams.get('session')
                            === {json.dumps(str(sessions["atlas"]))}
                          && document.body.innerText.includes({json.dumps(DESIGN_TITLE)})""",
                        "Design after PWA-style restart",
                        timeout=20,
                    )
                except AssertionError as exc:
                    restart_state = desktop.evaluate(
                        """({
                          url: location.href,
                          body: document.body.innerText.slice(0, 2000),
                          activeProject: document.querySelector(
                            "button[aria-label^='Active project:']"
                          )?.getAttribute('aria-label'),
                        })"""
                    )
                    raise AssertionError(
                        f"{exc}; restart={restart_state!r}; requested={design_restart_url}"
                    ) from exc
                desktop.open_destination("Chat")
                desktop.wait_for(
                    """document.querySelector(
                      ".surface-pane[data-surface='chat'] .code-header strong"
                    )?.textContent === 'Atlas Launch Lab planning'""",
                    "Atlas Chat after deep surface navigation",
                )
                desktop.wait_for_scroll(float(atlas_scroll["top"]))
                try:
                    desktop.assert_state(
                        ATLAS_DRAFT,
                        scroll_top=float(atlas_scroll["top"]),
                        attachment=True,
                        same_node=False,
                    )
                except AssertionError as exc:
                    debug_state = desktop.evaluate(
                        """({
                          url: location.href,
                          local: Object.fromEntries(Object.entries(localStorage)),
                          title: document.querySelector(
                            ".surface-pane[data-surface='chat'] .code-header strong"
                          )?.textContent,
                        })"""
                    )
                    raise AssertionError(
                        f"{exc}; Design restart state={debug_state!r}"
                    ) from exc

                mobile = _launch_browser(
                    executable=executable,
                    base_url=base_url,
                    token=token,
                    profile=fixture / "mobile-profile",
                    width=390,
                    height=844,
                    mobile=True,
                )
                mobile.set_project("Atlas Launch Lab", mobile=True)
                mobile.wait_for(
                    "document.body.innerText.includes('Atlas Launch Lab planning')",
                    "mobile Atlas chat session",
                )
                mobile_scroll = mobile.prepare_state(ATLAS_DRAFT, attachment)
                mobile.open_destination("Tasks", mobile=True)
                mobile.open_destination("Chat", mobile=True)
                mobile.assert_state(
                    ATLAS_DRAFT,
                    scroll_top=float(mobile_scroll["top"]),
                    attachment=True,
                    same_node=True,
                )
                for _ in range(2):
                    mobile.switch_mode("Delegate")
                    mobile.switch_mode("Work")
                    mobile.assert_state(
                        ATLAS_DRAFT,
                        scroll_top=float(mobile_scroll["top"]),
                        attachment=True,
                        same_node=True,
                    )
                if screenshots_dir:
                    mobile.screenshot(
                        screenshots_dir / "mobile-work-state-after-mode.png"
                    )

                desktop.set_project("Borealis Ops")
                desktop.set_draft(BOREALIS_DRAFT, 6, 17)
                _request(
                    f"{base_url}/api/projects/borealis",
                    method="DELETE",
                    token=token,
                )
                desktop.reload()
                desktop.wait_for(
                    """(() => {
                      const node = document.querySelector(
                        "button[aria-label^='Active project:']"
                      );
                      return node && node.getAttribute('aria-label')
                        .includes('Atlas Launch Lab');
                    })()""",
                    "explicit Atlas fallback after Borealis deletion",
                )
                fallback_title = desktop.evaluate(
                    "document.querySelector('.chat-header h1, .chat-header h2')?.textContent"
                )
                fallback_value = desktop.evaluate(
                    "document.querySelector(\".surface-pane[data-surface='chat'] textarea\")?.value"
                )
                if (
                    fallback_value != ATLAS_DRAFT
                    or fallback_value == BOREALIS_DRAFT
                    or fallback_title == "Borealis Ops planning"
                ):
                    raise AssertionError(
                        "deleted project fallback leaked its draft: "
                        f"title={fallback_title!r} draft={fallback_value!r}"
                    )
                desktop.wait_for(
                    "document.body.innerText.includes('Owner checkpoint')",
                    "Atlas messages after deleted-project fallback",
                )
                if screenshots_dir:
                    desktop.screenshot(screenshots_dir / "deleted-project-fallback.png")
                print(
                    json.dumps(
                        {
                            "fixture": "disposable",
                            "ok": True,
                            "sessions": sessions,
                            "coverage": [
                                "destination navigation",
                                "reload",
                                "PWA-style restart",
                                "native Back and Forward",
                                "repeated mode switches",
                                "two projects",
                                "workflow deep item",
                                "design deep item",
                                "deleted project fallback",
                                "mobile",
                            ],
                        },
                        sort_keys=True,
                    )
                )
            finally:
                if mobile is not None:
                    mobile.close()
                if desktop is not None:
                    desktop.close()
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(server.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    server.wait()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Work Chat state with a disposable real-browser fixture."
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Use the existing apps/web/dist build.",
    )
    parser.add_argument(
        "--screenshots-dir",
        type=Path,
        help="Write after-state screenshots to this directory.",
    )
    args = parser.parse_args()
    _run(
        screenshots_dir=args.screenshots_dir,
        skip_build=args.skip_build,
    )


if __name__ == "__main__":
    main()

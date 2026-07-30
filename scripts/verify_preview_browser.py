from __future__ import annotations

import argparse
import base64
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
PROBE_ROOT = ROOT / "trusted-probes" / "safe-update"
ASTRO_VERSION = "5.13.5"


def _request(
    url: str,
    *,
    body: dict | None = None,
    token: str | None = None,
    method: str | None = None,
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
    with urlopen(request, timeout=15) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected response from {url}")
    return value


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _browser() -> str:
    for name in ("google-chrome", "chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError("Chromium or Google Chrome is required")


def _run(command: list[str], *, cwd: Path, timeout: int) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout)


def _wait_http(url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            raise RuntimeError(log_path.read_text(encoding="utf-8"))
        try:
            _request(url)
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("disposable server readiness timed out")
            time.sleep(0.1)


def _stop_group(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _browser_session(executable: str, profile: Path):
    sys.path.insert(0, str(PROBE_ROOT))
    from browser import _WebSocket

    debug_port = _port()
    profile.mkdir(parents=True)
    process = subprocess.Popen(
        [
            executable,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-extensions",
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
    deadline = time.monotonic() + 20
    page = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
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
                and isinstance(item.get("webSocketDebuggerUrl"), str)
            )
            break
        except Exception:
            time.sleep(0.05)
    if page is None:
        _stop_group(process)
        raise RuntimeError("browser debugging startup timed out")
    return process, _WebSocket(page["webSocketDebuggerUrl"])


def _evaluate(connection, expression: str):
    result = connection.call(
        "Runtime.evaluate",
        {
            "awaitPromise": True,
            "expression": expression,
            "returnByValue": True,
        },
    )
    if "exceptionDetails" in result:
        raise RuntimeError("browser JavaScript failed")
    return result["result"].get("value")


def _wait_expression(connection, expression: str, expected, timeout: int = 30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = _evaluate(connection, expression)
            if value == expected:
                return value
        except Exception:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"browser condition timed out: {expression}")
        time.sleep(0.1)


def _screenshot(connection, path: Path) -> None:
    captured = connection.call(
        "Page.captureScreenshot",
        {"format": "png", "captureBeyondViewport": False},
    )
    path.write_bytes(base64.b64decode(captured["data"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")

    _run(["npm", "--prefix", str(WEB_DIR), "run", "build"], cwd=ROOT, timeout=180)

    with tempfile.TemporaryDirectory(prefix="proxima-preview-browser-") as raw_root:
        fixture = Path(raw_root)
        workspace = fixture / "workspace"
        project = workspace / "astro-preview"
        project.mkdir(parents=True)
        (project / "src" / "pages").mkdir(parents=True)
        (project / "package.json").write_text(
            json.dumps(
                {
                    "name": "disposable-astro-preview",
                    "private": True,
                    "scripts": {"dev": "astro dev"},
                    "dependencies": {"astro": ASTRO_VERSION},
                }
            ),
            encoding="utf-8",
        )
        (project / "src" / "pages" / "index.astro").write_text(
            "<html><body><h1>Disposable Astro preview</h1></body></html>\n",
            encoding="utf-8",
        )
        (project / "index.html").write_text(
            "<h1>FOREIGN PREVIEW</h1>\n",
            encoding="utf-8",
        )
        _run(
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=project,
            timeout=180,
        )

        api_port = _port()
        app_port = _port()
        base_url = f"http://127.0.0.1:{api_port}"
        environment = {
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_CLAUDE_LIVE_HOME": "0",
            "PROXIMA_DB_PATH": str(fixture / "proxima.db"),
            "PROXIMA_FEATURE_SAFE_SELF_UPDATE": "0",
            "PROXIMA_HERMES_PROFILES_ROOT": str(fixture / "runner-home"),
            "PROXIMA_LINK_ROOTS": str(workspace),
            "PROXIMA_PORT": str(api_port),
            "PROXIMA_PREVIEW_BIND": "127.0.0.1",
            "PROXIMA_PROJECTCTL_COMMAND": "/usr/bin/true",
            "PROXIMA_REFRESH_CREDENTIALS": "0",
            "PROXIMA_SINGLE_USER": "1",
            "PROXIMA_SINGLE_USER_NAME": "preview-browser",
            "PROXIMA_UPDATE_CHECK": "0",
            "PROXIMA_WEB_DIST": str(WEB_DIR / "dist"),
            "PROXIMA_WORKSPACE_ROOT": str(workspace),
            "PYTHONPATH": str(ROOT / "apps" / "api"),
            "TMPDIR": str(fixture),
        }
        Path(environment["PROXIMA_HERMES_PROFILES_ROOT"]).mkdir(parents=True)
        server_log = fixture / "server.log"
        foreign: subprocess.Popen | None = None
        foreign_output = None
        browser_process: subprocess.Popen | None = None
        connection = None
        with server_log.open("wb") as log:
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
            try:
                _wait_http(f"{base_url}/api/health", server, server_log)
                token = str(
                    _request(
                        f"{base_url}/auth/set-password",
                        body={"password": "preview-browser-password"},
                    )["token"]
                )
                _request(
                    f"{base_url}/api/projects/link",
                    body={
                        "name": "Astro Preview",
                        "path": str(project),
                        "slug": "astro-preview",
                    },
                    token=token,
                )
                browser_process, connection = _browser_session(
                    _browser(),
                    fixture / "browser-profile",
                )
                connection.call("Page.enable")
                connection.call("Runtime.enable")
                connection.call("Network.enable")
                connection.call(
                    "Network.setCookie",
                    {
                        "name": "proxima_session",
                        "value": token,
                        "url": base_url,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    },
                )
                connection.call("Page.navigate", {"url": base_url})
                _wait_expression(connection, "document.readyState", "complete")
                start_result = _evaluate(
                    connection,
                    f"""
(async () => {{
  const response = await fetch(
    "/api/projects/astro-preview/app/start",
    {{
      method: "POST",
      credentials: "include",
      headers: {{"content-type": "application/json"}},
      body: JSON.stringify({{
        command: "npm run dev -- --host 127.0.0.1 --port $PORT",
        port: {app_port},
        dir: ""
      }})
    }}
  );
  return {{status: response.status, body: await response.text()}};
}})()
""",
                )
                if start_result["status"] != 200:
                    raise RuntimeError(f"browser app start failed: {start_result}")
                status = _evaluate(
                    connection,
                    """
(async () => {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const response = await fetch(
      "/api/projects/astro-preview/app/status",
      {credentials: "include"}
    );
    const status = await response.json();
    if (status.ready) return status;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("Astro preview readiness timed out");
})()
""",
                )
                _evaluate(
                    connection,
                    """
fetch("/api/preview-auth", {
  method: "POST",
  credentials: "include"
}).then(response => response.json())
""",
                )
                relay_url = f"http://127.0.0.1:{status['preview_port']}/"
                connection.call("Page.navigate", {"url": relay_url})
                _wait_expression(
                    connection,
                    "document.querySelector('h1')?.textContent",
                    "Disposable Astro preview",
                )
                ready_shot = evidence_dir / "preview-ownership-ready.png"
                _screenshot(connection, ready_shot)

                _request(
                    f"{base_url}/api/projects/astro-preview/app/stop",
                    body={},
                    token=token,
                )
                foreign_log = fixture / "foreign.log"
                foreign_output = foreign_log.open("wb")
                foreign = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "http.server",
                        str(app_port),
                        "--bind",
                        "127.0.0.1",
                    ],
                    cwd=project,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=foreign_output,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(
                            ("127.0.0.1", app_port),
                            timeout=0.1,
                        ):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("foreign listener readiness timed out")
                connection.call(
                    "Page.navigate",
                    {"url": f"{base_url}/api/appview/astro-preview/"},
                )
                _wait_expression(
                    connection,
                    "document.body.textContent.includes('stopped')",
                    True,
                )
                if _evaluate(
                    connection,
                    "document.body.textContent.includes('FOREIGN PREVIEW')",
                ):
                    raise RuntimeError("browser received foreign preview content")
                stopped_shot = evidence_dir / "preview-ownership-stopped.png"
                _screenshot(connection, stopped_shot)
                if foreign.poll() is not None:
                    raise RuntimeError("Proxima terminated the foreign listener")
                foreign_output.flush()
                if foreign_log.read_text(encoding="utf-8"):
                    raise RuntimeError("browser request reached the foreign listener")
                print(
                    json.dumps(
                        {
                            "app": "Astro",
                            "authenticated": True,
                            "fixture": "disposable",
                            "ok": True,
                            "ready_screenshot": str(ready_shot),
                            "stopped_screenshot": str(stopped_shot),
                        },
                        sort_keys=True,
                    )
                )
            finally:
                if connection is not None:
                    connection.close()
                _stop_group(browser_process)
                _stop_group(foreign)
                if foreign_output is not None:
                    foreign_output.close()
                _stop_group(server)


if __name__ == "__main__":
    main()

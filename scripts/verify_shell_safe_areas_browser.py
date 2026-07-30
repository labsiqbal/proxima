from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "apps" / "web"
API_DIR = ROOT / "apps" / "api"
PROBE_ROOT = ROOT / "trusted-probes" / "safe-update"
MIN_TARGET_SIZE = 34
SCREENSHOT_SIZES = (
    (320, 844),
    (390, 844),
    (767, 844),
    (1024, 768),
    (1280, 800),
    (1440, 1000),
)


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
    for name in ("google-chrome", "chromium", "chromium-browser"):
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


def _api_command() -> list[str]:
    python = API_DIR / ".venv" / "bin" / "python"
    if python.is_file():
        return [str(python), str(API_DIR / "scripts" / "serve.py")]
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv or apps/api/.venv/bin/python is required")
    return [
        uv,
        "run",
        "--project",
        str(API_DIR),
        "python",
        str(API_DIR / "scripts" / "serve.py"),
    ]


def _wait_for(
    connection: object,
    expression: str,
    *,
    message: str,
    timeout: float = 20,
) -> object:
    from browser import _evaluation

    deadline = time.monotonic() + timeout
    while True:
        try:
            value = _evaluation(connection, expression)
        except Exception:
            value = None
        if value:
            return value
        if time.monotonic() >= deadline:
            try:
                state = _evaluation(
                    connection,
                    """({
                      url: location.href,
                      text: (document.body?.innerText || "").slice(0, 1200),
                      composers: document.querySelectorAll(".composer").length,
                      mainPane: document.querySelectorAll(".main-pane").length,
                      forms: Array.from(document.forms).map(form => form.className),
                    })""",
                )
            except Exception:
                state = "browser state unavailable"
            raise RuntimeError(f"{message}: {state}")
        time.sleep(0.05)


def _set_viewport(connection: object, width: int, height: int) -> None:
    connection.call(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    )
    from browser import _evaluation

    _evaluation(
        connection,
        """
        new Promise(resolve => requestAnimationFrame(
          () => requestAnimationFrame(() => resolve(true))
        ))
        """,
    )


def _geometry(connection: object) -> dict:
    from browser import _evaluation

    value = _evaluation(
        connection,
        """
        (() => {
          const visible = element => {
            if (!(element instanceof HTMLElement)) return false;
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== "none"
              && style.visibility !== "hidden"
              && rect.width > 0
              && rect.height > 0;
          };
          const rect = element => {
            const value = element.getBoundingClientRect();
            return {
              x: value.x,
              y: value.y,
              width: value.width,
              height: value.height,
              right: value.right,
              bottom: value.bottom,
            };
          };
          const one = selector => {
            const element = document.querySelector(selector);
            return element && visible(element) ? rect(element) : null;
          };
          const mobileControls = [
            [".mobile-topbar [aria-label='Menu']", "Menu"],
            [".mobile-topbar [aria-label='Back']", "Back"],
            [".mobile-topbar [aria-label='Search']", "Search"],
            [".mobile-topbar [aria-label='New chat']", "New chat"],
            [".mobile-topbar .attention-trigger", "Attention"],
            [".mobile-topbar .shell-mode-switch button:first-child", "Work"],
            [".mobile-topbar .shell-mode-switch button:last-child", "Delegate"],
          ].map(([selector, name]) => {
            const element = document.querySelector(selector);
            return element && visible(element) ? {name, rect: rect(element)} : null;
          }).filter(Boolean);
          return {
            viewport: {width: innerWidth, height: innerHeight},
            overflow: {
              document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              body: document.body.scrollWidth - document.body.clientWidth,
            },
            master: one(".master-popup-trigger"),
            composer: one("form.composer"),
            send: one("form.composer button[type='submit']"),
            attach: one("form.composer [aria-label='Attach files']"),
            modes: Array.from(document.querySelectorAll("form.composer .composer-modes button"))
              .filter(visible)
              .map(rect),
            toolRail: one(".tool-rail"),
            mobileControls,
          };
        })()
        """,
    )
    if not isinstance(value, dict):
        raise RuntimeError("browser geometry was unavailable")
    return value


def _intersection(a: dict, b: dict) -> float:
    width = max(0.0, min(a["right"], b["right"]) - max(a["x"], b["x"]))
    height = max(0.0, min(a["bottom"], b["bottom"]) - max(a["y"], b["y"]))
    return width * height


def _contained(inner: dict, outer: dict, tolerance: float = 0.5) -> bool:
    return (
        inner["x"] >= outer["x"] - tolerance
        and inner["y"] >= outer["y"] - tolerance
        and inner["right"] <= outer["right"] + tolerance
        and inner["bottom"] <= outer["bottom"] + tolerance
    )


def _assert_geometry(value: dict, *, mobile: bool) -> None:
    width = value["viewport"]["width"]
    height = value["viewport"]["height"]
    if value["overflow"]["document"] > 0 or value["overflow"]["body"] > 0:
        raise AssertionError(
            f"{width}x{height}: horizontal overflow {value['overflow']}"
        )
    master = value["master"]
    composer = value["composer"]
    tool_rail = value["toolRail"]
    if not master or not composer or not tool_rail:
        raise AssertionError(f"{width}x{height}: shell controls are missing")
    for name, target in (
        ("composer", composer),
        ("Send", value["send"]),
        ("Attach", value["attach"]),
        ("tool rail", tool_rail),
    ):
        if not target:
            raise AssertionError(f"{width}x{height}: {name} is missing")
        overlap = _intersection(master, target)
        if overlap:
            raise AssertionError(
                f"{width}x{height}: Master overlaps {name} by {overlap:.0f}px2"
            )
        if name in {"Send", "Attach"} and not _contained(target, composer):
            raise AssertionError(
                f"{width}x{height}: {name} escapes the visible composer"
            )
    for index, mode in enumerate(value["modes"]):
        overlap = _intersection(master, mode)
        if overlap:
            raise AssertionError(
                f"{width}x{height}: Master overlaps mode {index} by {overlap:.0f}px2"
            )
        if not _contained(mode, composer):
            raise AssertionError(
                f"{width}x{height}: mode {index} escapes the visible composer"
            )
    if not mobile:
        return
    controls = value["mobileControls"]
    expected = {"Menu", "Back", "Search", "New chat", "Attention", "Work", "Delegate"}
    names = {control["name"] for control in controls}
    if names != expected:
        raise AssertionError(
            f"{width}x{height}: mobile controls differ, expected {sorted(expected)}, "
            f"found {sorted(names)}"
        )
    for control in controls:
        rect = control["rect"]
        if rect["width"] < MIN_TARGET_SIZE or rect["height"] < MIN_TARGET_SIZE:
            raise AssertionError(
                f"{width}x{height}: {control['name']} target is "
                f"{rect['width']:.0f}x{rect['height']:.0f}"
            )
    for index, control in enumerate(controls):
        for other in controls[index + 1 :]:
            overlap = _intersection(control["rect"], other["rect"])
            if overlap:
                raise AssertionError(
                    f"{width}x{height}: {control['name']} overlaps "
                    f"{other['name']} by {overlap:.0f}px2"
                )


def _contrast(connection: object) -> dict[str, float]:
    from browser import _evaluation

    value = _evaluation(
        connection,
        """
        (() => {
          const parse = raw => {
            const parts = raw.match(/[\\d.]+/g)?.map(Number) || [];
            return {
              r: parts[0] || 0,
              g: parts[1] || 0,
              b: parts[2] || 0,
              a: parts.length > 3 ? parts[3] : 1,
            };
          };
          const composite = (front, back) => ({
            r: front.r * front.a + back.r * (1 - front.a),
            g: front.g * front.a + back.g * (1 - front.a),
            b: front.b * front.a + back.b * (1 - front.a),
            a: 1,
          });
          const background = element => {
            let result = {r: 255, g: 255, b: 255, a: 1};
            const layers = [];
            for (let node = element; node instanceof Element; node = node.parentElement) {
              const color = parse(getComputedStyle(node).backgroundColor);
              if (color.a > 0) layers.push(color);
            }
            for (const layer of layers.reverse()) result = composite(layer, result);
            return result;
          };
          const luminance = color => {
            const channel = value => {
              const normalized = value / 255;
              return normalized <= 0.04045
                ? normalized / 12.92
                : ((normalized + 0.055) / 1.055) ** 2.4;
            };
            return 0.2126 * channel(color.r)
              + 0.7152 * channel(color.g)
              + 0.0722 * channel(color.b);
          };
          const ratio = element => {
            const foreground = parse(getComputedStyle(element).color);
            const fg = composite(foreground, background(element));
            const first = luminance(fg);
            const second = luminance(background(element));
            return (Math.max(first, second) + 0.05)
              / (Math.min(first, second) + 0.05);
          };
          const visible = element => {
            if (!(element instanceof HTMLElement)) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0
              && rect.height > 0
              && style.display !== "none"
              && style.visibility !== "hidden";
          };
          const work = Array.from(document.querySelectorAll(".shell-mode-switch button"))
            .find(element => visible(element) && (element.textContent || "").trim() === "Work");
          const project = document.querySelector(".sidebar-work-context > span");
          const recent = document.querySelector(".group-toggle");
          if (!work || !project || !recent) return null;
          return {
            Work: ratio(work),
            "WORK PROJECT": ratio(project),
            "RECENT CHATS": ratio(recent),
          };
        })()
        """,
    )
    if not isinstance(value, dict):
        raise AssertionError("shared shell contrast targets are missing")
    ratios = {str(name): float(ratio) for name, ratio in value.items()}
    failures = {name: ratio for name, ratio in ratios.items() if ratio < 4.5}
    if failures:
        raise AssertionError(f"shared shell contrast is below 4.5:1: {failures}")
    return ratios


def _capture(connection: object, path: Path) -> None:
    result = connection.call(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True},
    )
    data = result.get("data")
    if not isinstance(data, str):
        raise RuntimeError("browser screenshot was unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data))


def _pointer_click(connection: object, selector: str) -> None:
    from browser import _evaluation

    center = _evaluation(
        connection,
        f"""
        (() => {{
          const element = document.querySelector({json.dumps(selector)});
          if (!element) return null;
          const rect = element.getBoundingClientRect();
          return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()
        """,
    )
    if not isinstance(center, dict):
        raise RuntimeError(f"pointer target is missing: {selector}")
    for event_type in ("mousePressed", "mouseReleased"):
        connection.call(
            "Input.dispatchMouseEvent",
            {
                "type": event_type,
                "x": center["x"],
                "y": center["y"],
                "button": "left",
                "clickCount": 1,
            },
        )
    time.sleep(0.15)


def _keyboard_activate(connection: object, selector: str) -> None:
    from browser import _evaluation

    focused = _evaluation(
        connection,
        f"""
        (() => {{
          const element = document.querySelector({json.dumps(selector)});
          if (!element) return false;
          element.focus();
          const style = getComputedStyle(element);
          const ring = parseFloat(style.outlineWidth) > 0
            && style.outlineStyle !== "none"
            && style.outlineColor !== "transparent";
          return document.activeElement === element && ring;
        }})()
        """,
    )
    if not focused:
        raise AssertionError(f"keyboard target lacks focus or a visible ring: {selector}")
    connection.call(
        "Input.dispatchKeyEvent",
        {
            "type": "rawKeyDown",
            "key": " ",
            "code": "Space",
            "windowsVirtualKeyCode": 32,
            "nativeVirtualKeyCode": 32,
        },
    )
    connection.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": " ",
            "code": "Space",
            "windowsVirtualKeyCode": 32,
            "nativeVirtualKeyCode": 32,
        },
    )
    time.sleep(0.15)


def _assert_activation_and_state(connection: object) -> None:
    from browser import _evaluation

    textarea = "form.composer textarea"
    _evaluation(
        connection,
        f"""
        (() => {{
          const element = document.querySelector({json.dumps(textarea)});
          const setter = Object.getOwnPropertyDescriptor(
            HTMLTextAreaElement.prototype, "value"
          ).set;
          setter.call(element, "Draft survives Master");
          element.dispatchEvent(new Event("input", {{bubbles: true}}));
          return true;
        }})()
        """,
    )
    before = _geometry(connection)["composer"]
    _pointer_click(connection, ".master-popup-trigger")
    _wait_for(
        connection,
        "!!document.querySelector(\"[role='dialog'][aria-labelledby='master-popup-title']\")",
        message="Master popup did not open by pointer",
    )
    preserved = _evaluation(
        connection,
        """
        (() => ({
          draft: document.querySelector("form.composer textarea")?.value,
          composer: (() => {
            const rect = document.querySelector("form.composer")
              ?.getBoundingClientRect();
            return rect && {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
          })(),
        }))()
        """,
    )
    if not isinstance(preserved, dict) or preserved.get("draft") != "Draft survives Master":
        raise AssertionError("opening Master did not preserve the Chat draft")
    after = preserved.get("composer")
    if not isinstance(after, dict):
        raise AssertionError("Chat composer disappeared while Master was open")
    for key in ("x", "y", "width", "height"):
        if abs(float(before[key]) - float(after[key])) > 0.5:
            raise AssertionError(f"opening Master shifted Chat composer {key}")
    connection.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    )
    connection.call(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    )
    _wait_for(
        connection,
        "!document.querySelector(\"[role='dialog'][aria-labelledby='master-popup-title']\")",
        message="Master popup did not close by keyboard",
    )

    _pointer_click(connection, ".mobile-topbar [aria-label='Search']")
    _wait_for(
        connection,
        "!!document.querySelector(\"[role='dialog'][aria-label='Search']\")",
        message="Search did not open by pointer",
    )
    connection.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    )
    connection.call(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    )
    _wait_for(
        connection,
        "!document.querySelector(\"[role='dialog'][aria-label='Search']\")",
        message="Search did not close by keyboard",
    )

    _keyboard_activate(connection, ".mobile-topbar .attention-trigger")
    _wait_for(
        connection,
        "!!document.querySelector(\"[role='dialog'][aria-label='Attention inbox']\")",
        message="Attention did not open by keyboard",
    )
    connection.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    )
    connection.call(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    )
    _wait_for(
        connection,
        "!document.querySelector(\"[role='dialog'][aria-label='Attention inbox']\")",
        message="Attention did not close by keyboard",
    )
    stable_targets = _evaluation(
        connection,
        """
        (async () => {
          const rect = (selector) => {
            const value = document.querySelector(selector)?.getBoundingClientRect();
            return value && {
              x: value.x,
              y: value.y,
              width: value.width,
              height: value.height,
            };
          };
          const selectors = {
            search: ".mobile-topbar [aria-label='Search']",
            newChat: ".mobile-topbar [aria-label='New chat']",
          };
          const trigger = document.querySelector(".mobile-topbar .attention-trigger");
          const slot = document.querySelector(".mobile-topbar .attention-control-slot");
          if (!trigger || !slot || !trigger.parentNode) return null;
          const parent = trigger.parentNode;
          const sibling = trigger.nextSibling;
          const before = {
            search: rect(selectors.search),
            newChat: rect(selectors.newChat),
          };
          trigger.remove();
          await new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve))
          );
          const absent = {
            search: rect(selectors.search),
            newChat: rect(selectors.newChat),
            slot: rect(".mobile-topbar .attention-control-slot"),
          };
          parent.insertBefore(trigger, sibling);
          await new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve))
          );
          const restored = {
            search: rect(selectors.search),
            newChat: rect(selectors.newChat),
          };
          return {before, absent, restored};
        })()
        """,
    )
    if not isinstance(stable_targets, dict):
        raise AssertionError("Attention trigger could not be toggled for pointer stability")
    for phase in ("absent", "restored"):
        current = stable_targets.get(phase)
        if not isinstance(current, dict):
            raise AssertionError(f"Attention {phase} geometry is unavailable")
        for target in ("search", "newChat"):
            baseline = stable_targets["before"].get(target)
            candidate = current.get(target)
            if not isinstance(baseline, dict) or not isinstance(candidate, dict):
                raise AssertionError(f"{target} geometry is unavailable with Attention {phase}")
            for key in ("x", "y", "width", "height"):
                if abs(float(baseline[key]) - float(candidate[key])) > 0.5:
                    raise AssertionError(
                        f"{target} pointer target shifted when Attention was {phase}"
                    )
    absent_slot = stable_targets["absent"].get("slot")
    if (
        not isinstance(absent_slot, dict)
        or float(absent_slot["width"]) < MIN_TARGET_SIZE
        or float(absent_slot["height"]) < MIN_TARGET_SIZE
    ):
        raise AssertionError("Attention's reserved pointer slot collapsed while absent")

    _keyboard_activate(connection, ".mobile-topbar [aria-label='Search']")
    _wait_for(
        connection,
        "!!document.querySelector(\"[role='dialog'][aria-label='Search']\")",
        message="Search did not open by keyboard",
    )


def _run_browser(
    *,
    executable: str,
    base_url: str,
    profile: Path,
    token: str,
    output_dir: Path,
) -> dict:
    sys.path.insert(0, str(PROBE_ROOT))
    from browser import _WebSocket, _evaluation

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    debug_port = int(listener.getsockname()[1])
    listener.close()
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
        "--window-size=1440,1000",
        "about:blank",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    connection = None
    try:
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
                    and item.get("url") == "about:blank"
                    and isinstance(item.get("webSocketDebuggerUrl"), str)
                )
                break
            except Exception:
                time.sleep(0.05)
        if page is None:
            raise RuntimeError("browser debugging startup timed out")
        connection = _WebSocket(page["webSocketDebuggerUrl"])
        connection.call("Page.enable")
        connection.call("Runtime.enable")
        connection.call("Network.enable")
        connection.call(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Authorization": f"Bearer {token}"}},
        )
        connection.call("Page.navigate", {"url": f"{base_url}/"})
        _wait_for(
            connection,
            "document.readyState === 'complete'",
            message="browser page load timed out",
        )
        _wait_for(
            connection,
            "!!document.querySelector('form.composer')",
            message="Chat composer did not render",
        )
        _evaluation(
            connection,
            """
            (() => {
              const style = document.createElement("style");
              style.textContent = `
                *, *::before, *::after {
                  animation-duration: 0s !important;
                  caret-color: transparent !important;
                  transition-duration: 0s !important;
                }
              `;
              document.head.append(style);
              return true;
            })()
            """,
        )
        _wait_for(
            connection,
            "!!document.querySelector('.attention-trigger')",
            message="Attention trigger did not render",
        )

        results: list[dict] = []
        contrast: dict[str, float] = {}
        tested_390 = False
        for width in range(320, 768):
            _set_viewport(connection, width, 844)
            value = _geometry(connection)
            if width == 320:
                _capture(connection, output_dir / "shell-safe-320x844.png")
            _assert_geometry(value, mobile=True)
            results.append(value)
            if width == 390:
                tested_390 = True
                _assert_activation_and_state(connection)
                connection.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
                )
                connection.call(
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
                )
        if not tested_390:
            raise AssertionError("390px activation viewport was not tested")

        for width, height in SCREENSHOT_SIZES:
            _set_viewport(connection, width, height)
            value = _geometry(connection)
            _assert_geometry(value, mobile=width <= 767)
            _capture(connection, output_dir / f"shell-safe-{width}x{height}.png")
            if width > 767:
                results.append(value)
                contrast = _contrast(connection)
        return {
            "contrast": contrast,
            "ok": True,
            "width_sweep": "320-767",
            "screenshots": [
                str(output_dir / f"shell-safe-{width}x{height}.png")
                for width, height in SCREENSHOT_SIZES
            ],
            "viewports_checked": len(results),
        }
    finally:
        if connection is not None:
            connection.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "shell-safe-areas",
    )
    args = parser.parse_args()
    if not args.skip_build:
        _build_web()

    with tempfile.TemporaryDirectory(prefix="proxima-shell-safe-areas-") as raw_root:
        fixture = Path(raw_root)
        home = fixture / "home"
        workspace = fixture / "workspace"
        container = workspace / "candidate"
        runner_home = fixture / "runner-home"
        fake_bin = fixture / "bin"
        for path in (home, workspace, container, runner_home, fake_bin):
            path.mkdir(parents=True)
        fixture_codex = PROBE_ROOT / "codex-fixture"
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
        database = fixture / "candidate.db"
        environment = {
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_CLAUDE_LIVE_HOME": "0",
            "PROXIMA_DB_PATH": str(database),
            "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "1",
            "PROXIMA_FEATURE_SAFE_SELF_UPDATE": "0",
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
            "PYTHONPATH": str(API_DIR),
            "TMPDIR": str(fixture),
            "UV_CACHE_DIR": str(fixture / "uv-cache"),
        }
        log_path = fixture / "server.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                _api_command(),
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 60
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
                _request(
                    f"{base_url}/api/projects/link",
                    body={
                        "name": "Candidate",
                        "path": str(container),
                        "slug": "candidate-browser",
                    },
                    token=token,
                )
                _request(
                    f"{base_url}/api/settings/master",
                    body={"tour_core_done": True},
                    token=token,
                    method="PUT",
                )
                with sqlite3.connect(database) as connection:
                    owner_id = int(
                        connection.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
                    )
                    project_id = int(
                        connection.execute(
                            "SELECT id FROM projects WHERE slug = ?",
                            ("candidate-browser",),
                        ).fetchone()[0]
                    )
                    profile_id = int(
                        connection.execute(
                            "SELECT id FROM profiles WHERE user_id = ? LIMIT 1",
                            (owner_id,),
                        ).fetchone()[0]
                    )
                    session_id = int(connection.execute(
                        "INSERT INTO sessions("
                        "title, project_id, owner_user_id, profile_id, runner_id, mode"
                        ") VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            "Safe shell review",
                            project_id,
                            owner_id,
                            profile_id,
                            "codex",
                            "chat",
                        ),
                    ).lastrowid)
                    connection.execute(
                        "INSERT INTO messages(session_id, role, content, author) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            session_id,
                            "user",
                            "Keep shared shell controls readable and clear.",
                            "candidate",
                        ),
                    )
                    connection.execute(
                        "INSERT INTO attention_items("
                        "kind, title, target_json, inline_ok, actions_json, source_key"
                        ") VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            "master_decision",
                            "Choose the safe shell direction",
                            json.dumps({"view": "master"}),
                            0,
                            "[]",
                            "shell-safe-area-browser",
                        ),
                    )
                result = _run_browser(
                    executable=_browser(),
                    base_url=base_url,
                    profile=fixture / "browser-profile",
                    token=token,
                    output_dir=args.output_dir.resolve(),
                )
                print(json.dumps(result, indent=2, sort_keys=True))
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

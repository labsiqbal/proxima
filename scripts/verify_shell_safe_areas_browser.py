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
VISUAL_ORACLE_PATH = ROOT / "scripts" / "shell_safe_area_visual_oracle.json"
MIN_TARGET_SIZE = 34
SCREENSHOT_SIZES = (
    (320, 844),
    (390, 844),
    (767, 844),
    (1024, 768),
    (1280, 800),
    (1440, 1000),
)
STATUS_SCREENSHOTS = (
    "shell-safe-390x844-attention.png",
    "shell-safe-390x844-running.png",
    "shell-safe-390x844-live-toast.png",
    "shell-safe-390x844-status-switch.png",
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


def _inject_live_master_toast(
    *,
    base_url: str,
    token: str,
    database: Path,
    project_id: int,
) -> str:
    desk = _request(f"{base_url}/api/master/desk", token=token)
    session = desk.get("session")
    focus = desk.get("focus")
    if not isinstance(session, dict) or not isinstance(focus, dict):
        raise RuntimeError("Master desk could not provide a live-toast fixture")
    session_id = int(session["id"])
    version = int(focus["version"])
    current_container_id = focus.get("current_container_id")
    with sqlite3.connect(database) as connection:
        owner_id = int(
            connection.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        )
        task_id = int(
            connection.execute(
                "INSERT INTO jobs("
                "project_id, title, status, created_by, started_at, finished_at"
                ") VALUES (?, ?, 'done', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (project_id, "Live shell toast fixture", owner_id),
            ).lastrowid
        )
        message_id = int(
            connection.execute(
                "INSERT INTO messages(session_id, role, content, author) "
                "VALUES (?, 'assistant', ?, 'Master')",
                (session_id, f"Completed Task #{task_id}."),
            ).lastrowid
        )
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
        payload = {
            "message_id": message_id,
            "task_id": task_id,
            "focus_epoch_id": focus.get("current_epoch_id"),
            "focus_container_id": current_container_id,
            "subject_container_id": project_id,
            "container_id": project_id,
            "container_slug": "candidate-browser",
        }
        connection.execute(
            "INSERT INTO events("
            "run_id, session_id, project_id, seq, type, payload"
            ") VALUES (NULL, ?, ?, ?, 'master.task.completed', ?)",
            (
                session_id,
                project_id,
                sequence,
                json.dumps(payload, separators=(",", ":")),
            ),
        )
    _request(
        f"{base_url}/api/master/focus",
        body={
            "container_id": (
                None if current_container_id is not None else project_id
            ),
            "version": version,
        },
        token=token,
        method="PUT",
    )
    return f"Task #{task_id} completed"


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
            const hit = document.elementFromPoint(
              value.x + value.width / 2,
              value.y + value.height / 2
            );
            return {
              x: value.x,
              y: value.y,
              width: value.width,
              height: value.height,
              right: value.right,
              bottom: value.bottom,
              hittable: hit === element || element.contains(hit),
            };
          };
          const one = selector => {
            const element = document.querySelector(selector);
            return element && visible(element) ? rect(element) : null;
          };
          const mobileControls = [
            [".mobile-topbar [aria-label='Menu']", "Menu"],
            [".mobile-topbar [aria-label='Back']", "Back"],
            [".mobile-topbar .running-pill", "Running"],
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
            topbar: one(".mobile-topbar") || one(".top-bar"),
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


def _load_visual_oracle() -> dict:
    try:
        value = json.loads(VISUAL_ORACLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"shell visual oracle is unavailable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise RuntimeError("shell visual oracle schema is unsupported")
    tolerance = value.get("tolerance_px")
    viewports = value.get("viewports")
    if not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise RuntimeError("shell visual oracle tolerance is invalid")
    if not isinstance(viewports, dict):
        raise RuntimeError("shell visual oracle viewports are invalid")
    expected = {f"{width}x{height}" for width, height in SCREENSHOT_SIZES}
    if set(viewports) != expected:
        raise RuntimeError(
            "shell visual oracle viewports differ from screenshot viewports"
        )
    return value


def _assert_visual_oracle(value: dict, oracle: dict) -> None:
    width = value["viewport"]["width"]
    height = value["viewport"]["height"]
    key = f"{width}x{height}"
    expected = oracle["viewports"][key]
    tolerance = float(oracle["tolerance_px"])
    for name in ("topbar", "toolRail", "master"):
        actual_rect = value.get(name)
        expected_rect = expected.get(name)
        if not isinstance(actual_rect, dict) or not isinstance(expected_rect, dict):
            raise AssertionError(f"{key}: visual oracle target {name} is missing")
        for dimension in ("x", "y", "width", "height"):
            actual = float(actual_rect[dimension])
            wanted = float(expected_rect[dimension])
            if abs(actual - wanted) > tolerance:
                raise AssertionError(
                    f"{key}: {name}.{dimension} is {actual:.2f}px, "
                    f"visual oracle requires {wanted:.2f}px"
                )


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
    topbar = value["topbar"]
    if not master or not composer or not tool_rail or not topbar:
        raise AssertionError(f"{width}x{height}: shell controls are missing")
    if abs(float(topbar["bottom"]) - float(tool_rail["y"])) > 0.5:
        raise AssertionError(
            f"{width}x{height}: tool rail does not start below the visible top bar"
        )
    for name, target in (
        ("Master", master),
        ("composer", composer),
        ("Send", value["send"]),
        ("Attach", value["attach"]),
        ("tool rail", tool_rail),
    ):
        if not target:
            raise AssertionError(f"{width}x{height}: {name} is missing")
        if not target["hittable"]:
            raise AssertionError(f"{width}x{height}: {name} is pointer-occluded")
        overlap = _intersection(master, target)
        if name != "Master" and overlap:
            raise AssertionError(
                f"{width}x{height}: Master overlaps {name} by {overlap:.0f}px2"
            )
        if name in {"Send", "Attach"} and not _contained(target, composer):
            raise AssertionError(
                f"{width}x{height}: {name} escapes the visible composer"
            )
    for index, mode in enumerate(value["modes"]):
        if not mode["hittable"]:
            raise AssertionError(
                f"{width}x{height}: mode {index} is pointer-occluded"
            )
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
    expected = {
        "Menu",
        "Back",
        "Running",
        "Search",
        "New chat",
        "Attention",
        "Work",
        "Delegate",
    }
    names = {control["name"] for control in controls}
    if names != expected:
        raise AssertionError(
            f"{width}x{height}: mobile controls differ, expected {sorted(expected)}, "
            f"found {sorted(names)}"
        )
    for control in controls:
        rect = control["rect"]
        if not rect["hittable"]:
            raise AssertionError(
                f"{width}x{height}: {control['name']} is pointer-occluded"
            )
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


def _assert_status_popover(
    connection: object,
    *,
    trigger_selector: str,
    dialog_label: str,
    screenshot: Path,
    live_toast_title: str,
    switch_from_selector: str | None = None,
    switch_from_dialog_label: str | None = None,
) -> None:
    from browser import _evaluation

    precondition = _evaluation(
        connection,
        f"""
        (() => {{
          const master = document.querySelector(".master-popup-trigger");
          const toast = Array.from(document.querySelectorAll(".master-toast"))
            .find(element => element.querySelector("strong")?.textContent
              === {json.dumps(live_toast_title)});
          return {{
            master: master instanceof HTMLElement,
            toast: toast instanceof HTMLElement,
          }};
        }})()
        """,
    )
    if (
        not isinstance(precondition, dict)
        or not precondition.get("master")
        or not precondition.get("toast")
    ):
        raise AssertionError(
            f"{dialog_label} lacks live Master overlay preconditions: {precondition}"
        )
    dialog_selector = f"[role='dialog'][aria-label={json.dumps(dialog_label)}]"
    source_dialog_selector = None
    if switch_from_selector and switch_from_dialog_label:
        _keyboard_activate(connection, switch_from_selector)
        source_dialog_selector = (
            f"[role='dialog'][aria-label={json.dumps(switch_from_dialog_label)}]"
        )
        _wait_for(
            connection,
            f"!!document.querySelector({json.dumps(source_dialog_selector)})",
            message=f"{switch_from_dialog_label} did not open before keyboard switch",
        )
        connection.call(
            "Input.dispatchKeyEvent",
            {
                "type": "rawKeyDown",
                "key": "Tab",
                "code": "Tab",
                "windowsVirtualKeyCode": 9,
                "nativeVirtualKeyCode": 9,
                "modifiers": 8,
            },
        )
        connection.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Tab",
                "code": "Tab",
                "windowsVirtualKeyCode": 9,
                "nativeVirtualKeyCode": 9,
                "modifiers": 8,
            },
        )
        focused = _evaluation(
            connection,
            f"""
            (() => {{
              const target = document.querySelector({json.dumps(trigger_selector)});
              if (!(target instanceof HTMLElement) || document.activeElement !== target) {{
                return false;
              }}
              const style = getComputedStyle(target);
              return parseFloat(style.outlineWidth) > 0
                && style.outlineStyle !== "none"
                && style.outlineColor !== "transparent";
            }})()
            """,
        )
        if not focused:
            raise AssertionError(
                f"Shift+Tab did not visibly focus {dialog_label}'s trigger"
            )
        for event_type in ("rawKeyDown", "keyUp"):
            connection.call(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": " ",
                    "code": "Space",
                    "windowsVirtualKeyCode": 32,
                    "nativeVirtualKeyCode": 32,
                },
            )
        time.sleep(0.15)
    else:
        _keyboard_activate(connection, trigger_selector)
    source_closed_expression = (
        f"!document.querySelector({json.dumps(source_dialog_selector)})"
        if source_dialog_selector
        else "true"
    )
    _wait_for(
        connection,
        f"""
        (() => {{
          const dialog = document.querySelector({json.dumps(dialog_selector)});
          const statusDialogs = document.querySelectorAll(
            "[role='dialog'][aria-label='Attention inbox'], "
              + "[role='dialog'][aria-label='Running tasks']"
          );
          return !!dialog
            && statusDialogs.length === 1
            && {source_closed_expression}
            && !document.querySelector(".master-popup-trigger")
            && !document.querySelector(".master-toast-region");
        }})()
        """,
        message=f"{dialog_label} did not exclusively open by keyboard",
    )
    value = _evaluation(
        connection,
        f"""
        (() => {{
          const visible = element => {{
            if (!(element instanceof HTMLElement)) return false;
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== "none"
              && style.visibility !== "hidden"
              && rect.width > 0
              && rect.height > 0;
          }};
          const rect = element => {{
            const value = element.getBoundingClientRect();
            return {{
              x: value.x,
              y: value.y,
              width: value.width,
              height: value.height,
              right: value.right,
              bottom: value.bottom,
            }};
          }};
          const dialog = document.querySelector({json.dumps(dialog_selector)});
          const rail = document.querySelector(".tool-rail");
          if (!(dialog instanceof HTMLElement) || !(rail instanceof HTMLElement)) {{
            return null;
          }}
          const controls = Array.from(dialog.querySelectorAll(
            "a[href], button:not([disabled]), input:not([disabled]), "
              + "select:not([disabled]), textarea:not([disabled]), "
              + "[tabindex]:not([tabindex='-1'])"
          )).filter(visible);
          return {{
            popover: rect(dialog),
            rail: rect(rail),
            master: visible(document.querySelector(".master-popup-trigger"))
              ? rect(document.querySelector(".master-popup-trigger"))
              : null,
            toasts: Array.from(document.querySelectorAll(".master-toast"))
              .filter(visible)
              .map(rect),
            viewport: {{x: 0, y: 0, right: innerWidth, bottom: innerHeight}},
            controlCount: controls.length,
            saturated: dialog.scrollHeight > dialog.clientHeight + 1,
          }};
        }})()
        """,
    )
    if not isinstance(value, dict):
        raise AssertionError(f"{dialog_label} geometry is unavailable")
    popover = value["popover"]
    viewport = value["viewport"]
    rail = value["rail"]
    if not _contained(popover, viewport):
        raise AssertionError(f"{dialog_label} escapes the mobile viewport")
    overlap = _intersection(popover, rail)
    if overlap:
        raise AssertionError(
            f"{dialog_label} overlaps the tool rail by {overlap:.0f}px2"
        )
    if not value.get("saturated"):
        raise AssertionError(f"{dialog_label} fixture did not saturate its scroll area")
    overlays = [value.get("master"), *value.get("toasts", [])]
    for overlay in overlays:
        if isinstance(overlay, dict):
            overlap = _intersection(popover, overlay)
            if overlap:
                raise AssertionError(
                    f"{dialog_label} intersects a Master overlay by {overlap:.0f}px2"
                )
            raise AssertionError(
                f"{dialog_label} left a Master overlay visible while open"
            )
    control_count = value.get("controlCount")
    if not isinstance(control_count, int) or control_count <= 0:
        raise AssertionError(f"{dialog_label} has no reachable controls")
    _capture(connection, screenshot)
    reached: set[int] = set()
    for _ in range(control_count):
        connection.call(
            "Input.dispatchKeyEvent",
            {
                "type": "rawKeyDown",
                "key": "Tab",
                "code": "Tab",
                "windowsVirtualKeyCode": 9,
                "nativeVirtualKeyCode": 9,
            },
        )
        connection.call(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Tab",
                "code": "Tab",
                "windowsVirtualKeyCode": 9,
                "nativeVirtualKeyCode": 9,
            },
        )
        focused = _evaluation(
            connection,
            f"""
            (() => {{
              const dialog = document.querySelector({json.dumps(dialog_selector)});
              if (!(dialog instanceof HTMLElement)) return null;
              const controls = Array.from(dialog.querySelectorAll(
                "a[href], button:not([disabled]), input:not([disabled]), "
                  + "select:not([disabled]), textarea:not([disabled]), "
                  + "[tabindex]:not([tabindex='-1'])"
              )).filter(element => {{
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== "none"
                  && style.visibility !== "hidden"
                  && rect.width > 0
                  && rect.height > 0;
              }});
              const active = document.activeElement;
              const style = active instanceof HTMLElement
                ? getComputedStyle(active)
                : null;
              const bounds = active instanceof HTMLElement
                ? active.getBoundingClientRect()
                : null;
              const dialogBounds = dialog.getBoundingClientRect();
              const hit = bounds
                ? document.elementFromPoint(
                    bounds.x + bounds.width / 2,
                    bounds.y + bounds.height / 2
                  )
                : null;
              return {{
                index: controls.indexOf(active),
                ring: !!style
                  && parseFloat(style.outlineWidth) > 0
                  && style.outlineStyle !== "none"
                  && style.outlineColor !== "transparent",
                bounds: bounds && {{
                  x: bounds.x,
                  y: bounds.y,
                  width: bounds.width,
                  height: bounds.height,
                  right: bounds.right,
                  bottom: bounds.bottom,
                }},
                popover: {{
                  x: dialogBounds.x,
                  y: dialogBounds.y,
                  right: dialogBounds.right,
                  bottom: dialogBounds.bottom,
                }},
                hittable: active instanceof HTMLElement
                  && (hit === active || active.contains(hit)),
                hit: hit instanceof Element ? {{
                  tag: hit.tagName,
                  className: String(hit.className || ""),
                  label: hit.getAttribute("aria-label") || "",
                }} : null,
              }};
            }})()
            """,
        )
        if not isinstance(focused, dict) or int(focused.get("index", -1)) < 0:
            raise AssertionError(f"{dialog_label} keyboard focus escaped the popover")
        if not focused.get("ring"):
            raise AssertionError(f"{dialog_label} keyboard target lacks a focus ring")
        bounds = focused.get("bounds")
        focused_popover = focused.get("popover")
        if (
            not isinstance(bounds, dict)
            or not isinstance(focused_popover, dict)
            or not _contained(bounds, focused_popover)
        ):
            raise AssertionError(
                f"{dialog_label} keyboard target is outside the visible popover"
            )
        if (
            float(bounds["width"]) < MIN_TARGET_SIZE
            or float(bounds["height"]) < MIN_TARGET_SIZE
        ):
            raise AssertionError(
                f"{dialog_label} keyboard target is "
                f"{bounds['width']:.0f}x{bounds['height']:.0f}"
            )
        if not focused.get("hittable"):
            raise AssertionError(
                f"{dialog_label} keyboard target is pointer-occluded by "
                f"{focused.get('hit')}"
            )
        reached.add(int(focused["index"]))
    if reached != set(range(control_count)):
        raise AssertionError(f"{dialog_label} keyboard order skips a control")
    for event_type in ("keyDown", "keyUp"):
        connection.call(
            "Input.dispatchKeyEvent",
            {
                "type": event_type,
                "key": "Escape",
                "code": "Escape",
                "windowsVirtualKeyCode": 27,
            },
        )
    _wait_for(
        connection,
        f"!document.querySelector({json.dumps(dialog_selector)})",
        message=f"{dialog_label} did not close by keyboard",
    )
    _wait_for(
        connection,
        "!!document.querySelector('.master-popup-trigger')",
        message=f"Master launcher did not return after {dialog_label} closed",
    )
    _evaluation(
        connection,
        f"document.querySelector({json.dumps(trigger_selector)})?.focus() || true",
    )


def _assert_activation_and_state(
    connection: object,
    output_dir: Path,
    *,
    base_url: str,
    token: str,
    database: Path,
    project_id: int,
) -> None:
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
    _wait_for(
        connection,
        """
        Array.from(document.querySelectorAll(".master-popup-head small"))
          .some(element => element.textContent?.includes("Live"))
        """,
        message="Master live stream did not connect before fixture injection",
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

    attention_toast = _inject_live_master_toast(
        base_url=base_url,
        token=token,
        database=database,
        project_id=project_id,
    )
    _wait_for(
        connection,
        f"""
        Array.from(document.querySelectorAll(".master-toast strong"))
          .some(element => element.textContent === {json.dumps(attention_toast)})
        """,
        message="live Master toast did not render before Attention opened",
    )
    _capture(connection, output_dir / STATUS_SCREENSHOTS[2])
    _assert_status_popover(
        connection,
        trigger_selector=".mobile-topbar .attention-trigger",
        dialog_label="Attention inbox",
        screenshot=output_dir / STATUS_SCREENSHOTS[0],
        live_toast_title=attention_toast,
    )
    running_toast = _inject_live_master_toast(
        base_url=base_url,
        token=token,
        database=database,
        project_id=project_id,
    )
    _wait_for(
        connection,
        f"""
        Array.from(document.querySelectorAll(".master-toast strong"))
          .some(element => element.textContent === {json.dumps(running_toast)})
        """,
        message="live Master toast did not render before Running opened",
    )
    _assert_status_popover(
        connection,
        trigger_selector=".mobile-topbar .running-pill",
        dialog_label="Running tasks",
        screenshot=output_dir / STATUS_SCREENSHOTS[1],
        live_toast_title=running_toast,
    )
    switch_toast = _inject_live_master_toast(
        base_url=base_url,
        token=token,
        database=database,
        project_id=project_id,
    )
    _wait_for(
        connection,
        f"""
        Array.from(document.querySelectorAll(".master-toast strong"))
          .some(element => element.textContent === {json.dumps(switch_toast)})
        """,
        message="live Master toast did not render before status keyboard switch",
    )
    _assert_status_popover(
        connection,
        trigger_selector=".mobile-topbar .running-pill",
        dialog_label="Running tasks",
        screenshot=output_dir / STATUS_SCREENSHOTS[3],
        live_toast_title=switch_toast,
        switch_from_selector=".mobile-topbar .attention-trigger",
        switch_from_dialog_label="Attention inbox",
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
    database: Path,
    project_id: int,
    output_dir: Path,
    visual_oracle: dict,
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
        cookie = connection.call(
            "Network.setCookie",
            {
                "name": "proxima_session",
                "value": token,
                "url": base_url,
                "httpOnly": True,
                "sameSite": "Lax",
            },
        )
        if not cookie.get("success"):
            raise RuntimeError("browser session cookie could not be installed")
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
        _wait_for(
            connection,
            "!!document.querySelector('.running-pill')",
            message="Running trigger did not render",
        )

        results: list[dict] = []
        contrast: dict[str, float] = {}
        tested_390 = False
        for width in range(320, 768):
            _set_viewport(connection, width, 844)
            if width == 320:
                _wait_for(
                    connection,
                    """
                    (() => {
                      const trigger = document.querySelector(
                        ".mobile-topbar .attention-trigger"
                      );
                      return !!trigger && trigger.getBoundingClientRect().width > 0;
                    })()
                    """,
                    message="Mobile Attention trigger did not render",
                )
            value = _geometry(connection)
            _assert_geometry(value, mobile=True)
            results.append(value)
            if width == 390:
                tested_390 = True
                _assert_activation_and_state(
                    connection,
                    output_dir,
                    base_url=base_url,
                    token=token,
                    database=database,
                    project_id=project_id,
                )
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
            _assert_visual_oracle(value, visual_oracle)
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
            ] + [str(output_dir / name) for name in STATUS_SCREENSHOTS],
            "visual_oracle": str(VISUAL_ORACLE_PATH),
            "visual_oracle_viewports": len(visual_oracle["viewports"]),
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
    visual_oracle = _load_visual_oracle()

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
                        "INSERT INTO runs("
                        "session_id, project_id, user_id, profile_id, runner_id, "
                        "kind, status, prompt, started_at, heartbeat_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, "
                        "datetime('now', '+1 hour'))",
                        (
                            session_id,
                            project_id,
                            owner_id,
                            profile_id,
                            "codex",
                            "chat",
                            "running",
                            "Keep the Running popover visible.",
                        ),
                    )
                    for index in range(14):
                        connection.execute(
                            "INSERT INTO jobs("
                            "project_id, title, status, created_by, started_at"
                            ") VALUES (?, ?, 'running', ?, CURRENT_TIMESTAMP)",
                            (
                                project_id,
                                f"Running shell fixture {index + 1}",
                                owner_id,
                            ),
                        )
                        connection.execute(
                            "INSERT INTO attention_items("
                            "kind, title, target_json, inline_ok, actions_json, "
                            "source_key"
                            ") VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                "master_decision",
                                f"Choose safe shell direction {index + 1}",
                                json.dumps({"view": "master"}),
                                0,
                                "[]",
                                f"shell-safe-area-browser-{index + 1}",
                            ),
                        )
                result = _run_browser(
                    executable=_browser(),
                    base_url=base_url,
                    profile=fixture / "browser-profile",
                    token=token,
                    database=database,
                    project_id=project_id,
                    output_dir=args.output_dir.resolve(),
                    visual_oracle=visual_oracle,
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

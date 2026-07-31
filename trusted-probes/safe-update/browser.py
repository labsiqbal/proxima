"""Chrome DevTools driver for trusted candidate browser scenarios."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen


class BrowserProbeError(RuntimeError):
    pass


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        try:
            chunk = connection.recv(size - len(result))
        except TimeoutError as exc:
            raise BrowserProbeError(
                "browser debugging response timed out"
            ) from exc
        if not chunk:
            raise BrowserProbeError("browser debugging connection closed")
        result.extend(chunk)
    return bytes(result)


class _WebSocket:
    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise BrowserProbeError("browser debugging endpoint is invalid")
        self.connection = socket.create_connection(
            (parsed.hostname, parsed.port),
            timeout=30,
        )
        self.connection.settimeout(30)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
        self.connection.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = self.connection.recv(1)
            if not chunk:
                raise BrowserProbeError("browser debugging handshake closed")
            response.extend(chunk)
            if len(response) > 65536:
                raise BrowserProbeError("browser debugging handshake is oversized")
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        )
        if (
            not response.startswith(b"HTTP/1.1 101 ")
            or b"sec-websocket-accept: " + expected.lower()
            not in bytes(response).lower()
        ):
            raise BrowserProbeError("browser debugging handshake failed")
        self.sequence = 0

    def close(self) -> None:
        self.connection.close()

    def _send(self, payload: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length <= 65535:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        header.extend(
            byte ^ mask[index % 4]
            for index, byte in enumerate(payload)
        )
        self.connection.sendall(header)

    def _receive(self) -> bytes:
        payload = bytearray()
        started = False
        while True:
            first, second = _recv_exact(self.connection, 2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            reserved = first & 0x70
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", _recv_exact(self.connection, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _recv_exact(self.connection, 8))[0]
            if length > 16 * 1024 * 1024:
                raise BrowserProbeError("browser debugging message is oversized")
            mask = _recv_exact(self.connection, 4) if masked else b""
            frame = _recv_exact(self.connection, length)
            if masked:
                frame = bytes(
                    byte ^ mask[index % 4]
                    for index, byte in enumerate(frame)
                )
            if opcode >= 8 and (not final or reserved or length > 125):
                raise BrowserProbeError("browser debugging control frame is invalid")
            if opcode == 8:
                raise BrowserProbeError("browser debugging connection closed")
            if opcode == 9:
                self._send(frame, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode == 1:
                if reserved:
                    raise BrowserProbeError("browser debugging frame is invalid")
                payload.clear()
                started = True
            elif opcode != 0 or not started:
                raise BrowserProbeError("browser debugging frame is invalid")
            elif reserved:
                raise BrowserProbeError("browser debugging frame is invalid")
            payload.extend(frame)
            if final:
                return bytes(payload)

    def send(self, method: str, params: dict | None = None) -> int:
        self.sequence += 1
        sequence = self.sequence
        self._send(
            json.dumps(
                {"id": sequence, "method": method, "params": params or {}},
                separators=(",", ":"),
            ).encode()
        )
        return sequence

    def call(self, method: str, params: dict | None = None) -> dict:
        sequence = self.send(method, params)
        while True:
            try:
                value = json.loads(self._receive())
            except json.JSONDecodeError as exc:
                raise BrowserProbeError("browser debugging response is invalid") from exc
            if not isinstance(value, dict) or value.get("id") != sequence:
                continue
            if "error" in value or not isinstance(value.get("result"), dict):
                raise BrowserProbeError(f"browser command failed: {method}")
            return value["result"]


def _evaluation(connection: _WebSocket, expression: str) -> object:
    response = connection.call(
        "Runtime.evaluate",
        {
            "awaitPromise": True,
            "expression": expression,
            "returnByValue": True,
        },
    )
    result = response.get("result")
    if (
        not isinstance(result, dict)
        or result.get("subtype") == "error"
        or "exceptionDetails" in response
    ):
        raise BrowserProbeError("browser scenario JavaScript failed")
    return result.get("value")


def _element_expression(step: dict, action: str) -> str:
    selector = json.dumps(step["selector"])
    text = json.dumps(step.get("text"))
    value = json.dumps(step.get("value"))
    return f"""
(() => {{
  const nodes = Array.from(document.querySelectorAll({selector}));
  const wanted = {text};
  const element = nodes.find(node => wanted === null || (node.textContent || "").trim().includes(wanted));
  if ({json.dumps(action)} === "assert_absent") {{
    return {{ok:element === undefined,count:nodes.length}};
  }}
  if (!element) return {{ok:false}};
  if ({json.dumps(action)} === "click") {{
    element.click();
  }} else if ({json.dumps(action)} === "fill") {{
    const setter = Object.getOwnPropertyDescriptor(element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype, "value").set;
    setter.call(element, {value});
    element.dispatchEvent(new Event("input", {{bubbles:true}}));
    element.dispatchEvent(new Event("change", {{bubbles:true}}));
  }} else if ({json.dumps(action)} === "select") {{
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
    setter.call(element, {value});
    element.dispatchEvent(new Event("input", {{bubbles:true}}));
    element.dispatchEvent(new Event("change", {{bubbles:true}}));
  }}
  return {{ok:true,text:(element.textContent || "").trim().slice(0,512),value:element.value ?? null}};
}})()
"""


def _step(connection: _WebSocket, step: dict) -> dict:
    action = step["action"]
    if action == "screenshot":
        result = connection.call(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True},
        )
        encoded = result.get("data")
        if not isinstance(encoded, str):
            raise BrowserProbeError("browser screenshot did not return image data")
        path = Path(step["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
        return {"ok": True, "path": str(path)}
    if action == "request":
        result = _evaluation(
            connection,
            f"""
(async () => {{
  const response = await fetch({json.dumps(step["path"])}, {{
    method: {json.dumps(step.get("method", "POST"))},
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({json.dumps(step.get("body"))}),
  }});
  const text = await response.text();
  return {{ok:response.ok,status:response.status,text:text.slice(0,2048)}};
}})()
""",
        )
        if isinstance(result, dict) and result.get("ok") is True:
            return result
        raise BrowserProbeError(
            f"browser scenario request failed: {json.dumps(result, sort_keys=True)}"
        )
    if action == "click_if_present":
        result = _evaluation(
            connection,
            _element_expression(step, "click"),
        )
        if isinstance(result, dict) and result.get("ok") is True:
            time.sleep(0.15)
            return result
        return {"ok": True, "skipped": True}
    deadline = time.monotonic() + float(step.get("timeout", 10))
    while True:
        result = _evaluation(
            connection,
            _element_expression(
                step,
                "wait" if action == "assert" else action,
            ),
        )
        if isinstance(result, dict) and result.get("ok") is True:
            if action in {"click", "fill", "select"}:
                time.sleep(0.15)
            return result
        if time.monotonic() >= deadline:
            page = _evaluation(
                connection,
                "({url:location.href,text:(document.body?.innerText || '').slice(0,2048)})",
            )
            raise BrowserProbeError(
                f"browser scenario step failed: {action} {step['selector']}; "
                f"page={json.dumps(page, sort_keys=True)}"
            )
        time.sleep(0.05)


def _capture_png(connection: _WebSocket, path: Path) -> None:
    captured = connection.call(
        "Page.captureScreenshot",
        {
            "captureBeyondViewport": True,
            "format": "png",
            "fromSurface": True,
        },
    ).get("data")
    if not isinstance(captured, str):
        raise BrowserProbeError("browser screenshot data is unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(captured, validate=True))


def run_scenario(
    *,
    executable: str,
    base_url: str,
    scenario: dict,
    profile: Path,
    auth_token: str,
    drop_prefix: list[str],
    path: str = "/",
    screenshot_path: Path | None = None,
    screenshot_dir: Path | None = None,
) -> bytes:
    profile.mkdir(mode=0o777)
    profile.chmod(0o777)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    debug_port = int(listener.getsockname()[1])
    listener.close()
    command = [
        *drop_prefix,
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
    connection: _WebSocket | None = None
    try:
        deadline = time.monotonic() + 20
        page: dict | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BrowserProbeError("browser exited before debugging was ready")
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
            raise BrowserProbeError("browser debugging startup timed out")
        connection = _WebSocket(page["webSocketDebuggerUrl"])
        connection.call("Page.enable")
        connection.call("Runtime.enable")
        connection.call("Network.enable")
        if scenario["authenticated"]:
            connection.call(
                "Network.setExtraHTTPHeaders",
                {
                    "headers": {
                        "Authorization": f"Bearer {auth_token}",
                    },
                },
            )
            connection.call(
                "Network.setCookie",
                {
                    "name": "proxima_session",
                    "value": auth_token,
                    "url": base_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
            )
        connection.call("Page.navigate", {"url": f"{base_url}{path}"})
        deadline = time.monotonic() + 20
        while _evaluation(connection, "document.readyState") != "complete":
            if time.monotonic() >= deadline:
                raise BrowserProbeError("browser page load timed out")
            time.sleep(0.05)
        transcript = [
            {
                "name": scenario["name"],
                "authenticated": scenario["authenticated"],
            }
        ]
        for step in scenario["steps"]:
            if step.get("action") == "screenshot":
                name = str(step.get("name") or "").strip()
                if not name:
                    raise BrowserProbeError("screenshot step requires a name")
                if screenshot_dir is not None:
                    path = screenshot_dir / f"{name}.png"
                    _capture_png(connection, path)
                    transcript.append(
                        {"action": "screenshot", "name": name, "path": str(path)}
                    )
                else:
                    transcript.append(
                        {"action": "screenshot", "name": name, "skipped": True}
                    )
                continue
            transcript.append(_step(connection, step))
        if screenshot_path is not None:
            _capture_png(connection, screenshot_path)
        return json.dumps(
            transcript,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    finally:
        if connection is not None:
            connection.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
            process.wait()

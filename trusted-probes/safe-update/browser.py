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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from urllib.request import urlopen


class BrowserProbeError(RuntimeError):
    pass


_NETWORK_RESOURCE_EVENTS = frozenset(
    {
        "Network.loadingFailed",
        "Network.loadingFinished",
        "Network.requestWillBeSent",
        "Network.requestWillBeSentExtraInfo",
        "Network.responseReceived",
    }
)


@dataclass(frozen=True)
class _NetworkResourceObservation:
    request_id: str
    url: str
    method: str
    status: int
    mime_type: str
    content_type: str
    fetch_metadata: dict[str, str]
    response_headers: dict[str, str]


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
        self.network_failures: list[dict[str, object]] = []
        self.network_events: list[dict[str, object]] = []

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
                if isinstance(value, dict):
                    event_method = value.get("method")
                    event_params = value.get("params")
                    if (
                        event_method in _NETWORK_RESOURCE_EVENTS
                        and isinstance(event_params, dict)
                    ):
                        self.network_events.append(
                            {
                                "method": event_method,
                                "params": event_params,
                            }
                        )
                        self.network_events = self.network_events[-1000:]
                    if (
                        event_method == "Network.loadingFailed"
                        and isinstance(event_params, dict)
                    ):
                        params = event_params
                        self.network_failures.append(
                            {
                                key: params[key]
                                for key in (
                                    "blockedReason",
                                    "canceled",
                                    "errorText",
                                    "type",
                                )
                                if key in params
                            }
                        )
                        self.network_failures = self.network_failures[-10:]
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
        details = response.get("exceptionDetails")
        exception = details.get("exception") if isinstance(details, dict) else None
        description = (
            exception.get("description")
            if isinstance(exception, dict)
            else details.get("text")
            if isinstance(details, dict)
            else None
        )
        suffix = f": {description}" if description else ""
        failures = (
            f"; network failures: {json.dumps(connection.network_failures)}"
            if connection.network_failures
            else ""
        )
        raise BrowserProbeError(
            f"browser scenario JavaScript failed{suffix}{failures}"
        )
    return result.get("value")


def _debug_pages(debug_port: int) -> list[dict]:
    with urlopen(
        f"http://127.0.0.1:{debug_port}/json/list",
        timeout=1,
    ) as response:
        value = json.loads(response.read())
    if not isinstance(value, list) or not all(
        isinstance(item, dict)
        for item in value
    ):
        raise BrowserProbeError("browser debugging target list is invalid")
    return value


def _browser_url(connection: _WebSocket, expression: str) -> str:
    value = _evaluation(
        connection,
        f"new URL(({expression}), location.href).href",
    )
    if not isinstance(value, str):
        raise BrowserProbeError("browser popup URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise BrowserProbeError("browser popup URL is invalid")
    return value


def _network_header(headers: object, name: str) -> str:
    if not isinstance(headers, dict):
        return ""
    return next(
        (
            str(value)
            for key, value in headers.items()
            if str(key).lower() == name.lower()
        ),
        "",
    )


def _network_resource_request_ids(
    events: list[dict[str, object]],
    expected_url: str,
) -> set[str]:
    return {
        str(params["requestId"])
        for event in events
        if event.get("method") == "Network.requestWillBeSent"
        and isinstance((params := event.get("params")), dict)
        and isinstance(params.get("request"), dict)
        and params["request"].get("url") == expected_url
        and isinstance(params.get("requestId"), str)
    }


def _network_resource_terminal(
    events: list[dict[str, object]],
    expected_url: str,
) -> bool:
    request_ids = _network_resource_request_ids(events, expected_url)
    if len(request_ids) != 1:
        return False
    request_id = next(iter(request_ids))
    methods = {
        str(event.get("method"))
        for event in events
        if isinstance(event.get("params"), dict)
        and event["params"].get("requestId") == request_id
    }
    return (
        "Network.requestWillBeSentExtraInfo" in methods
        and (
            "Network.loadingFailed" in methods
            or (
                "Network.responseReceived" in methods
                and "Network.loadingFinished" in methods
            )
        )
    )


def _network_resource_observation(
    events: list[dict[str, object]],
    *,
    expected_url: str,
    expected_mime_type: str,
    expected_fetch_metadata: dict[str, str],
    required_response_headers: tuple[str, ...],
) -> _NetworkResourceObservation:
    request_ids = _network_resource_request_ids(events, expected_url)
    if not request_ids:
        raise BrowserProbeError("browser resource request is missing")
    if len(request_ids) != 1:
        raise BrowserProbeError("browser resource request is ambiguous")
    request_id = next(iter(request_ids))
    request_events = [
        event["params"]
        for event in events
        if event.get("method") == "Network.requestWillBeSent"
        and isinstance(event.get("params"), dict)
        and event["params"].get("requestId") == request_id
    ]
    if any(event.get("redirectResponse") for event in request_events):
        raise BrowserProbeError("browser resource request redirected")
    request_urls = [
        event["request"].get("url")
        for event in request_events
        if isinstance(event.get("request"), dict)
    ]
    if request_urls != [expected_url]:
        raise BrowserProbeError("browser resource final URL is invalid")
    request_methods = [
        event["request"].get("method")
        for event in request_events
        if isinstance(event.get("request"), dict)
    ]
    if (
        len(request_methods) != 1
        or not isinstance(request_methods[0], str)
        or not request_methods[0]
    ):
        raise BrowserProbeError("browser resource request method is invalid")
    failures = [
        event["params"]
        for event in events
        if event.get("method") == "Network.loadingFailed"
        and isinstance(event.get("params"), dict)
        and event["params"].get("requestId") == request_id
    ]
    if any(failure.get("blockedReason") for failure in failures):
        raise BrowserProbeError("browser resource request was blocked")
    if failures:
        raise BrowserProbeError("browser resource request failed")
    extra_infos = [
        event["params"]
        for event in events
        if event.get("method") == "Network.requestWillBeSentExtraInfo"
        and isinstance(event.get("params"), dict)
        and event["params"].get("requestId") == request_id
    ]
    if len(extra_infos) != 1:
        raise BrowserProbeError("browser resource request metadata is missing")
    headers = extra_infos[0].get("headers")
    fetch_metadata = {
        name: _network_header(headers, f"sec-fetch-{name}")
        for name in ("site", "mode", "dest")
    }
    if fetch_metadata != expected_fetch_metadata:
        raise BrowserProbeError(
            "browser resource request metadata is invalid: "
            + json.dumps(fetch_metadata, sort_keys=True)
        )
    responses = [
        event["params"].get("response")
        for event in events
        if event.get("method") == "Network.responseReceived"
        and isinstance(event.get("params"), dict)
        and event["params"].get("requestId") == request_id
        and isinstance(event["params"].get("response"), dict)
    ]
    if len(responses) != 1:
        raise BrowserProbeError("browser resource response is missing")
    response = responses[0]
    if response.get("url") != expected_url:
        raise BrowserProbeError("browser resource final URL is invalid")
    status = int(response.get("status") or 0)
    if status != 200:
        raise BrowserProbeError(
            f"browser resource response status is invalid: {status}"
        )
    mime_type = str(response.get("mimeType") or "").lower()
    if mime_type != expected_mime_type.lower():
        raise BrowserProbeError("browser resource MIME type is invalid")
    content_type = _network_header(response.get("headers"), "content-type")
    if content_type.split(";", 1)[0].strip().lower() != expected_mime_type.lower():
        raise BrowserProbeError("browser resource Content-Type is invalid")
    response_headers = {
        name.lower(): _network_header(response.get("headers"), name)
        for name in required_response_headers
    }
    if any(not value for value in response_headers.values()):
        raise BrowserProbeError("browser resource response header is missing")
    completed = any(
        event.get("method") == "Network.loadingFinished"
        and isinstance(event.get("params"), dict)
        and event["params"].get("requestId") == request_id
        for event in events
    )
    if not completed:
        raise BrowserProbeError("browser resource response did not complete")
    return _NetworkResourceObservation(
        request_id=request_id,
        url=expected_url,
        method=request_methods[0],
        status=status,
        mime_type=mime_type,
        content_type=content_type,
        fetch_metadata=fetch_metadata,
        response_headers=response_headers,
    )


def _network_resource_summary(
    connection: _WebSocket,
    *,
    expected_url: str,
    expectation: dict[str, object],
) -> dict[str, object]:
    expected_mime_type = expectation.get("mime_type")
    expected_json = expectation.get("body_json")
    expected_fetch_metadata = expectation.get("fetch_metadata")
    required_response_headers = expectation.get("response_headers", [])
    if (
        not isinstance(expected_mime_type, str)
        or not isinstance(expected_json, dict)
        or not isinstance(expected_fetch_metadata, dict)
        or not isinstance(required_response_headers, list)
        or not all(
            isinstance(name, str) and name
            for name in required_response_headers
        )
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in expected_fetch_metadata.items()
        )
    ):
        raise BrowserProbeError("browser resource expectation is invalid")
    try:
        observation = _network_resource_observation(
            connection.network_events,
            expected_url=expected_url,
            expected_mime_type=expected_mime_type,
            expected_fetch_metadata=expected_fetch_metadata,
            required_response_headers=tuple(required_response_headers),
        )
    except BrowserProbeError as exc:
        request_ids = _network_resource_request_ids(
            connection.network_events,
            expected_url,
        )
        if (
            "response status is invalid" not in str(exc)
            or len(request_ids) != 1
        ):
            raise
        failure_body = connection.call(
            "Network.getResponseBody",
            {"requestId": next(iter(request_ids))},
        ).get("body")
        detail = (
            failure_body[:256]
            if isinstance(failure_body, str)
            else "unavailable"
        )
        raise BrowserProbeError(f"{exc}; body: {detail}") from exc
    response = connection.call(
        "Network.getResponseBody",
        {"requestId": observation.request_id},
    )
    body = response.get("body")
    if not isinstance(body, str):
        raise BrowserProbeError("browser resource response body is missing")
    if response.get("base64Encoded") is True:
        try:
            body_bytes = base64.b64decode(body, validate=True)
        except ValueError as exc:
            raise BrowserProbeError(
                "browser resource response body is invalid"
            ) from exc
    else:
        body_bytes = body.encode()
    if len(body_bytes) > 1024 * 1024:
        raise BrowserProbeError("browser resource response body is oversized")
    try:
        body_json = json.loads(body_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserProbeError(
            "browser resource response content is invalid"
        ) from exc
    if body_json != expected_json:
        raise BrowserProbeError("browser resource response content is invalid")
    return {
        "body_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "content_type": observation.content_type,
        "fetch_metadata": observation.fetch_metadata,
        "method": observation.method,
        "mime_type": observation.mime_type,
        "request_id": observation.request_id,
        "response_headers": observation.response_headers,
        "status": observation.status,
        "url": observation.url,
    }


def _popup_response_step(
    connection: _WebSocket,
    step: dict,
    *,
    debug_port: int,
) -> dict:
    url = _browser_url(connection, step["url_expression"])
    candidate = urlsplit(url)
    expected_origin = (
        _browser_url(
            connection,
            step["expected_final_origin_expression"],
        ).rstrip("/")
        if "expected_final_origin_expression" in step
        else f"{candidate.scheme}://{candidate.netloc}"
    )
    expected_path = step.get("expected_final_path", candidate.path)
    network_resource = step.get("network_resource")
    expected_network_url: str | None = None
    if network_resource is not None:
        if not isinstance(network_resource, dict):
            raise BrowserProbeError("browser resource expectation is invalid")
        url_expression = network_resource.get("url_expression")
        if not isinstance(url_expression, str):
            raise BrowserProbeError("browser resource URL is invalid")
        expected_network_url = _browser_url(connection, url_expression)
    before = {
        item.get("id")
        for item in _debug_pages(debug_port)
        if isinstance(item.get("id"), str)
    }
    request_headers = step.get("request_headers")
    target_id: str | None = None
    if request_headers is not None:
        if not isinstance(request_headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in request_headers.items()
        ):
            raise BrowserProbeError("browser popup request headers are invalid")
    if request_headers is not None or network_resource is not None:
        created = connection.call(
            "Target.createTarget",
            {"url": "about:blank"},
        )
        candidate_id = created.get("targetId")
        if not isinstance(candidate_id, str):
            raise BrowserProbeError("browser popup target could not open")
        target_id = candidate_id
    else:
        opened = _evaluation(
            connection,
            (
                "(() => {"
                f"const popup=window.open({json.dumps(url)},'_blank');"
                "return {ok:Boolean(popup)};"
                "})()"
            ),
        )
        if not isinstance(opened, dict) or opened.get("ok") is not True:
            raise BrowserProbeError("browser popup could not open")

    deadline = time.monotonic() + float(step.get("timeout", 10))
    target: dict | None = None
    while time.monotonic() < deadline:
        target = next(
            (
                item
                for item in _debug_pages(debug_port)
                if item.get("type") == "page"
                and (
                    item.get("id") == target_id
                    if target_id is not None
                    else item.get("id") not in before
                )
                and isinstance(item.get("webSocketDebuggerUrl"), str)
            ),
            None,
        )
        if target is not None:
            break
        time.sleep(0.025)
    if target is None:
        raise BrowserProbeError("browser popup target was not discovered")

    popup = _WebSocket(target["webSocketDebuggerUrl"])
    result: dict | None = None
    try:
        popup.call("Page.enable")
        popup.call("Runtime.enable")
        popup.call("Network.enable")
        if request_headers is not None:
            popup.call(
                "Network.setExtraHTTPHeaders",
                {"headers": request_headers},
            )
            popup.call("Page.navigate", {"url": url})
        elif network_resource is not None:
            popup.call("Page.navigate", {"url": url})
        marker = json.dumps(
            step.get("execution_marker", "__proximaPreviewExecuted")
        )
        expected_status = int(step["expected_status"])
        wait_for_execution = step.get("expected_executed") is True
        expression = f"""
(() => {{
  const navigation = performance.getEntriesByType("navigation")[0];
  const status = Number(navigation?.responseStatus || 0);
  const executed = Boolean(globalThis[{marker}]);
  return {{
    ok: document.readyState === "complete"
      && status > 0
      && (
        !{json.dumps(wait_for_execution)}
        || executed
        || status !== {expected_status}
      ),
    status,
    finalUrl: location.href,
    body: (document.body?.textContent || "").slice(0, 1024),
    executed
  }};
}})()
"""
        network_summary: dict[str, object] | None = None
        while time.monotonic() < deadline:
            try:
                value = _evaluation(popup, expression)
            except BrowserProbeError:
                time.sleep(0.025)
                continue
            if isinstance(value, dict) and value.get("ok") is True:
                result = value
                if network_resource is None:
                    break
                if (
                    expected_network_url is not None
                    and _network_resource_terminal(
                        popup.network_events,
                        expected_network_url,
                    )
                ):
                    network_summary = _network_resource_summary(
                        popup,
                        expected_url=expected_network_url,
                        expectation=network_resource,
                    )
                    break
            time.sleep(0.025)
        if result is None:
            raise BrowserProbeError("browser popup response did not finish")
        if network_resource is not None and network_summary is None:
            if expected_network_url is None:
                raise BrowserProbeError("browser resource URL is invalid")
            network_summary = _network_resource_summary(
                popup,
                expected_url=expected_network_url,
                expectation=network_resource,
            )
    finally:
        popup.close()
        target_id = target.get("id")
        if isinstance(target_id, str):
            try:
                with urlopen(
                    f"http://127.0.0.1:{debug_port}/json/close/{target_id}",
                    timeout=1,
                ):
                    pass
            except Exception:
                pass

    final_url = result.get("finalUrl")
    if not isinstance(final_url, str):
        raise BrowserProbeError("browser popup final URL is invalid")
    final = urlsplit(final_url)
    final_origin = f"{final.scheme}://{final.netloc}"
    has_capability = any(
        key == "__proxima_cap"
        for key, _value in parse_qsl(
            final.query,
            keep_blank_values=True,
        )
    )
    summary = {
        "ok": True,
        "name": step.get("name", "popup response"),
        "status": result.get("status"),
        "final_origin": final_origin,
        "final_path": final.path,
        "capability_query": has_capability,
        "executed": result.get("executed"),
    }
    if network_summary is not None:
        summary["network_resource"] = network_summary
    expected = (
        result.get("status") == step["expected_status"]
        and final_origin == expected_origin
        and final.path == expected_path
        and result.get("executed") is step.get("expected_executed", False)
    )
    if "expected_capability_query" in step:
        expected = expected and (
            has_capability is step["expected_capability_query"]
        )
    expected_body = step.get("expected_body")
    if expected_body is not None:
        expected = (
            expected
            and isinstance(result.get("body"), str)
            and expected_body in result["body"]
        )
    if not expected:
        summary["ok"] = False
        raise BrowserProbeError(
            "browser popup response assertion failed: "
            + json.dumps(summary, sort_keys=True)
        )
    return summary


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


def _step(
    connection: _WebSocket,
    step: dict,
    *,
    debug_port: int,
) -> dict:
    action = step["action"]
    if action == "popup_response":
        return _popup_response_step(
            connection,
            step,
            debug_port=debug_port,
        )
    if action == "screenshot":
        path = Path(step["path"])
        if not path.is_absolute() or not path.parent.is_dir():
            raise BrowserProbeError("browser screenshot destination is invalid")
        response = connection.call(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False},
        )
        data = response.get("data")
        if not isinstance(data, str):
            raise BrowserProbeError("browser screenshot data is missing")
        path.write_bytes(base64.b64decode(data, validate=True))
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
        if action == "script":
            result = _evaluation(connection, step["expression"])
        else:
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
            label = step.get("name") if action == "script" else step.get("selector")
            page = _evaluation(
                connection,
                "({url:location.href,text:(document.body?.innerText || '').slice(0,2048)})",
            )
            raise BrowserProbeError(
                f"browser scenario step failed: {action} {label or ''}; "
                f"page={json.dumps(page, sort_keys=True)}"
            )
        time.sleep(0.05)


def run_scenario(
    *,
    executable: str,
    base_url: str,
    scenario: dict,
    profile: Path,
    auth_token: str,
    drop_prefix: list[str],
    path: str = "/",
    host_resolver_rules: str = "MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    ignore_certificate_errors: bool = False,
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
        "--disable-popup-blocking",
        "--disable-background-networking",
        "--disable-component-extensions-with-background-pages",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        f"--host-resolver-rules={host_resolver_rules}",
        *(["--ignore-certificate-errors"] if ignore_certificate_errors else []),
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
                    "secure": urlsplit(base_url).scheme == "https",
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
            transcript.append(
                _step(
                    connection,
                    step,
                    debug_port=debug_port,
                )
            )
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

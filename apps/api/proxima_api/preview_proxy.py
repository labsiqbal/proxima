"""Reverse proxying for remote app previews — one engine, two front doors.

A project's running dev server (Vite/Next/static/…) must be served **root-
relative on its own origin** for a preview to actually work: SPA HTML references
absolute asset paths (`/assets/x.js`, `/@vite/client`) and the HMR client opens
a WebSocket to the page origin, none of which survive a sub-path proxy like
`/api/appview/<slug>/`. The engine here forwards HTTP + WebSocket to the app's
local dev port, rewrites Host to `127.0.0.1:<port>` (so Vite-style allowed-host
checks pass), and strips cookies/authorization so project code never sees
Proxima credentials.

Two front doors share it:

- `PreviewProxyMiddleware` — host-based: `preview-<slug>.<APPS_DOMAIN>` rides
  the Cloudflare tunnel. Unset APPS_DOMAIN ⇒ no-op passthrough.
- `PreviewRelayManager` — port-based, for deployments without an apps domain
  (LAN / Tailscale): each running app gets its own listener on the Proxima
  host, so `http://<proxima-host>:<relay port>/` is that app's origin.

Auth for both: the short-lived `proxima_preview` capability cookie minted by
`POST /api/preview-auth`. It is never an owner API session, is host-scoped (so
the browser sends it to relay ports — cookies ignore ports), and is stripped
before forwarding.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import secrets
import socket
import time
from typing import Any, Callable

import httpx
import uvicorn
import websockets

_LOG = logging.getLogger("proxima.preview_proxy")

# Hop-by-hop headers must not be forwarded verbatim across a proxy.
_HOP = {"authorization", "cf-access-jwt-assertion", "connection", "cookie",
        "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "content-length",
        "content-encoding", "host"}
_RESPONSE_HOP = _HOP | {"set-cookie", "www-authenticate"}
PREVIEW_COOKIE = "proxima_preview"
PREVIEW_TOKEN_TTL_SECONDS = 60 * 60


def mint_preview_token(secret: bytes, ttl_seconds: int = PREVIEW_TOKEN_TTL_SECONDS) -> str:
    """Mint a short-lived capability that authorizes previews only.

    It is intentionally unrelated to the owner's API session. Tokens are signed
    in memory and expire quickly; restarting Proxima invalidates them all.
    """
    payload = f"{int(time.time()) + ttl_seconds}:{secrets.token_urlsafe(18)}".encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded}.{signed}"


def valid_preview_token(secret: bytes, token: str, now: int | None = None) -> bool:
    try:
        encoded, signed = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signed, expected):
            return False
        padding = "=" * (-len(encoded) % 4)
        expires_raw, _nonce = base64.urlsafe_b64decode(encoded + padding).decode().split(":", 1)
        return int(expires_raw) >= (int(time.time()) if now is None else now)
    except (ValueError, UnicodeDecodeError):
        return False


def _authed(scope: dict[str, Any], validate_token) -> bool:
    if not validate_token:
        return False
    cookie = ""
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            cookie = v.decode("latin-1")
            break
    token = ""
    for part in cookie.split(";"):
        p = part.strip()
        if p.startswith(PREVIEW_COOKIE + "="):
            token = p[len(PREVIEW_COOKIE) + 1:]
            break
    if not token:
        return False
    try:
        return bool(validate_token(token))
    except Exception:
        return False


async def _reject(scope, send, status: int, msg: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1013})
        return
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
    await send({"type": "http.response.body", "body": msg.encode()})


async def _proxy_http(scope, receive, send, port: int) -> None:
    body = b""
    more = True
    while more:
        m = await receive()
        body += m.get("body", b"")
        more = m.get("more_body", False)
    url = f"http://127.0.0.1:{port}{scope['path']}"
    qs = scope.get("query_string") or b""
    if qs:
        url += "?" + qs.decode("latin-1")
    # Forward headers but rewrite Host → the local dev server, so frameworks
    # that guard on Host (Vite `allowedHosts`) don't reject the proxied request.
    fwd = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"]
           if k.decode("latin-1").lower() not in _HOP]
    fwd.append(("host", f"127.0.0.1:{port}"))
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            async with client.stream(scope["method"], url, content=body or None, headers=fwd) as resp:
                out = [(k.encode("latin-1"), v.encode("latin-1"))
                       for k, v in resp.headers.items() if k.lower() not in _RESPONSE_HOP]
                await send({"type": "http.response.start", "status": resp.status_code, "headers": out})
                async for chunk in resp.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
    except httpx.RequestError:
        await _reject(scope, send, 502, "preview app not reachable yet")


async def _proxy_ws(scope, receive, send, port: int) -> None:
    path = scope["path"]
    qs = scope.get("query_string") or b""
    if qs:
        path += "?" + qs.decode("latin-1")
    first = await receive()
    if first["type"] != "websocket.connect":
        return
    subprotocols = scope.get("subprotocols") or None
    uri = f"ws://127.0.0.1:{port}{path}"
    try:
        up = await websockets.connect(uri, subprotocols=subprotocols, open_timeout=10, max_size=None)
    except Exception:
        await send({"type": "websocket.close", "code": 1013})
        return
    accept: dict[str, Any] = {"type": "websocket.accept"}
    if getattr(up, "subprotocol", None):
        accept["subprotocol"] = up.subprotocol
    await send(accept)

    async def client_to_up() -> None:
        while True:
            m = await receive()
            t = m["type"]
            if t == "websocket.receive":
                if m.get("text") is not None:
                    await up.send(m["text"])
                elif m.get("bytes") is not None:
                    await up.send(m["bytes"])
            elif t == "websocket.disconnect":
                return

    async def up_to_client() -> None:
        try:
            async for data in up:
                if isinstance(data, (bytes, bytearray)):
                    await send({"type": "websocket.send", "bytes": bytes(data)})
                else:
                    await send({"type": "websocket.send", "text": data})
        except Exception:
            pass

    t1 = asyncio.create_task(client_to_up())
    t2 = asyncio.create_task(up_to_client())
    try:
        _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
    finally:
        await up.close()
        try:
            await send({"type": "websocket.close"})
        except Exception:
            pass


async def _serve_preview(scope, receive, send, *, validate_token, port: int | None) -> None:
    """Shared request path: capability gate → target lookup → proxy."""
    if not _authed(scope, validate_token):
        return await _reject(scope, send, 403, "preview: not authorized")
    if not port:
        return await _reject(scope, send, 503, "preview app not running")
    if scope["type"] == "http":
        return await _proxy_http(scope, receive, send, port)
    return await _proxy_ws(scope, receive, send, port)


class PreviewProxyMiddleware:
    def __init__(self, app: Any, fastapi_app: Any, apps_domain: str | None, validate_token=None) -> None:
        self.app = app
        self.fastapi_app = fastapi_app  # for app.state.app_manager at request time
        self.suffix = ("." + apps_domain.lower()) if apps_domain else None
        # Preview subdomains have NO Cloudflare Access gate (so they can be iframed),
        # so THIS is their only auth: require a short-lived preview-only capability.
        # It is never an owner API session and is never forwarded to project code.
        self.validate_token = validate_token

    def _slug_for(self, scope: dict[str, Any]) -> str | None:
        """Return the project slug if this request targets a preview subdomain."""
        if not self.suffix:
            return None
        host = ""
        for k, v in scope.get("headers", []):
            if k == b"host":
                host = v.decode("latin-1").split(":")[0].lower()
                break
        if host and host.endswith(self.suffix):
            label = host[: -len(self.suffix)]  # e.g. "preview-myapp" — or "os" for the main app
            # Only intercept our own `preview-<slug>` single-label hosts; everything
            # else under the zone (proxima.example.com, www, …) passes through untouched.
            if "." not in label and label.startswith("preview-"):
                slug = label[len("preview-"):]
                return slug or None
        return None

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            slug = self._slug_for(scope)
            if slug is not None:
                port = self.fastapi_app.state.app_manager.port(slug)
                return await _serve_preview(scope, receive, send,
                                            validate_token=self.validate_token, port=port)
        await self.app(scope, receive, send)


class _RelayServer(uvicorn.Server):
    # The relay runs as a task inside the API's own event loop; uvicorn's default
    # signal capture would displace the parent server's SIGINT/SIGTERM handlers.
    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:
        pass


class PreviewRelayManager:
    """Port-based preview origins for deployments without an apps domain.

    One listener per running app, started/stopped with it. The relay resolves
    the app's current dev port per request (so a port sniffed from server
    output after startup keeps working) and serves the same capability-gated,
    credential-stripping proxy engine as the subdomain middleware.
    """

    def __init__(self, bind_host: str | None, port_for: Callable[[str], int | None],
                 validate_token=None) -> None:
        # bind_host must be remote-reachable for remote preview to work; "off"
        # (or empty) disables relays entirely for strict loopback-only installs.
        self.bind_host = (bind_host or "").strip()
        self.enabled = self.bind_host.lower() not in ("", "off", "none", "disabled")
        self.port_for = port_for
        self.validate_token = validate_token
        self._relays: dict[str, dict[str, Any]] = {}

    def port(self, slug: str) -> int | None:
        relay = self._relays.get(slug)
        return relay["port"] if relay else None

    def _asgi_for(self, slug: str):
        async def relay_app(scope, receive, send):
            if scope["type"] not in ("http", "websocket"):
                return
            await _serve_preview(scope, receive, send,
                                 validate_token=self.validate_token,
                                 port=self.port_for(slug))
        return relay_app

    async def start(self, slug: str) -> int | None:
        if not self.enabled:
            return None
        await self.stop(slug)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind_host, 0))  # OS-assigned free port
        sock.listen(128)
        port = int(sock.getsockname()[1])
        server = _RelayServer(uvicorn.Config(
            self._asgi_for(slug), lifespan="off", access_log=False, log_level="warning",
        ))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        self._relays[slug] = {"server": server, "task": task, "socket": sock, "port": port}
        return port

    async def stop(self, slug: str) -> None:
        relay = self._relays.pop(slug, None)
        if not relay:
            return
        relay["server"].should_exit = True
        try:
            await asyncio.wait_for(relay["task"], timeout=5)
        except Exception:
            relay["task"].cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await relay["task"]
        with contextlib.suppress(OSError):
            relay["socket"].close()

    async def shutdown(self) -> None:
        for slug in list(self._relays):
            await self.stop(slug)

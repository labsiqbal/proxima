"""Reverse proxying for app previews - one engine, two front doors.

A project's running dev server (Vite/Next/static/...) must be served **root-
relative on its own origin** for a preview to actually work: SPA HTML references
absolute asset paths (`/assets/x.js`, `/@vite/client`) and the HMR client opens
a WebSocket to the page origin, none of which survive a sub-path proxy like
`/api/appview/<slug>/`. The engine authenticates the preview capability first,
then opens an upstream connection and verifies that the connected server socket
belongs to a currently ready managed endpoint before sending HTTP or WebSocket
bytes. It rewrites Host to `127.0.0.1:<port>` (so Vite-style allowed-host checks
pass) and strips cookies/authorization so project code never sees Proxima
credentials. Starting, conflict, ownership-unknown, and exited states have no
proxy target.

Two front doors share it:

- `PreviewProxyMiddleware` - host-based: `preview-<slug>.<APPS_DOMAIN>` rides
  the Cloudflare tunnel. Unset APPS_DOMAIN => no-op passthrough.
- `PreviewRelayManager` - port-based origin for local and remote browsers when
  no apps-domain subdomain applies: each running app gets its own listener on
  the Proxima host, so `http://<proxima-host>:<relay port>/` is that app's
  origin. Default bind is loopback plus Tailscale when present; never
  `0.0.0.0` unless configured explicitly.

Auth for both: the short-lived `proxima_preview` capability cookie minted by
`POST /api/preview-auth`. It is never an owner API session, is host-scoped (so
the browser sends it to relay ports - cookies ignore ports), and is stripped
before forwarding.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
import time
from typing import Any, Callable

import httpcore
import uvicorn
import websockets

_LOG = logging.getLogger("proxima.preview_proxy")

# Hop-by-hop headers must not be forwarded verbatim across a proxy.
_HOP = {
    "authorization",
    "cf-access-jwt-assertion",
    "connection",
    "cookie",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}
_RESPONSE_HOP = _HOP | {"set-cookie", "www-authenticate"}
PREVIEW_COOKIE = "proxima_preview"
PREVIEW_TOKEN_TTL_SECONDS = 60 * 60

# Tailscale gives every node an IPv4 address from the CGNAT range.
TAILNET_IPV4_NET = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_PROBE = ("100.100.100.100", 53)  # Tailscale MagicDNS anycast address


def tailnet_address() -> str | None:
    """This host's Tailscale IPv4 address, or None when not on a tailnet.

    Connecting a UDP socket sends no packet; the kernel just resolves which
    source address it would route to the tailnet from - the tailscale
    interface address whenever tailscale is up.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(_TAILNET_PROBE)
            addr = probe.getsockname()[0]
    except OSError:
        return None
    try:
        return addr if ipaddress.ip_address(addr) in TAILNET_IPV4_NET else None
    except ValueError:
        return None


def resolve_preview_bind_host(configured: str | None) -> str:
    """Resolve the relay bind interface, keeping the default off the plain LAN.

    "auto" resolves the preferred remote interface. The relay manager adds
    loopback separately when that interface is Tailscale. The fallback is
    loopback, never 0.0.0.0. Every other value is the operator's explicit
    choice and passes through.
    """
    value = (configured or "").strip()
    if value.lower() != "auto":
        return value
    return tailnet_address() or "127.0.0.1"


def resolve_preview_bind_hosts(configured: str | None) -> tuple[str, ...]:
    host = resolve_preview_bind_host(configured)
    if (configured or "").strip().lower() == "auto" and host != "127.0.0.1":
        return ("127.0.0.1", host)
    return (host,)


def mint_preview_token(
    secret: bytes, ttl_seconds: int = PREVIEW_TOKEN_TTL_SECONDS
) -> str:
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
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(signed, expected):
            return False
        padding = "=" * (-len(encoded) % 4)
        expires_raw, _nonce = (
            base64.urlsafe_b64decode(encoded + padding).decode().split(":", 1)
        )
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
            token = p[len(PREVIEW_COOKIE) + 1 :]
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
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": msg.encode()})


def _ingress_lease(maintenance):
    if maintenance is None:
        return None, True
    lease = maintenance.acquire()
    allowed = lease.acquired and not maintenance.fenced()
    if not allowed:
        lease.release()
    return lease, allowed


class PreviewConnectionRejected(httpcore.ConnectError):
    pass


class PreviewUpstreamError(RuntimeError):
    pass


class _VerifiedNetworkBackend(httpcore.AnyIOBackend):
    def __init__(self, verify_connection: Callable[[int, int], bool]) -> None:
        self.verify_connection = verify_connection

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        stream = await super().connect_tcp(
            host,
            port,
            timeout,
            local_address,
            socket_options,
        )
        raw_socket = stream.get_extra_info("socket")
        try:
            client_port = int(raw_socket.getsockname()[1])
        except (AttributeError, IndexError, OSError, TypeError, ValueError):
            await stream.aclose()
            raise PreviewConnectionRejected(
                "preview connection ownership is unavailable"
            ) from None
        if not self.verify_connection(port, client_port):
            await stream.aclose()
            raise PreviewConnectionRejected(
                "preview connection ownership was not verified"
            )
        return stream


def _request_target(path: str | bytes, query_string: bytes) -> bytes:
    target = path if isinstance(path, bytes) else path.encode("utf-8")
    if query_string:
        target += b"?" + query_string
    return target


def _forward_headers(
    headers: list[tuple[bytes, bytes]],
    port: int,
) -> list[tuple[bytes, bytes]]:
    forwarded = [
        (key, value)
        for key, value in headers
        if key.decode("latin-1").lower() not in _HOP
    ]
    forwarded.append((b"host", f"127.0.0.1:{port}".encode("ascii")))
    return forwarded


@contextlib.asynccontextmanager
async def _upstream_http(
    method: str,
    target: bytes,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    port: int,
    verify_connection: Callable[[int, int], bool],
):
    backend = _VerifiedNetworkBackend(verify_connection)
    timeout = {
        "connect": 60,
        "read": 60,
        "write": 60,
        "pool": 60,
    }
    url = httpcore.URL(
        scheme=b"http",
        host=b"127.0.0.1",
        port=port,
        target=target,
    )
    async with httpcore.AsyncConnectionPool(
        network_backend=backend,
        max_connections=1,
        max_keepalive_connections=0,
    ) as pool:
        async with pool.stream(
            method,
            url,
            content=body or None,
            headers=_forward_headers(headers, port),
            extensions={"timeout": timeout},
        ) as response:
            yield response


async def proxy_http_request(
    *,
    method: str,
    path: str,
    query_string: bytes,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    port: int,
    verify_connection: Callable[[int, int], bool],
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    target = _request_target(path, query_string)
    try:
        async with _upstream_http(
            method,
            target,
            headers,
            body,
            port,
            verify_connection,
        ) as response:
            content = await response.aread()
            response_headers = [
                (key, value)
                for key, value in response.headers
                if key.decode("latin-1").lower() not in _RESPONSE_HOP
            ]
            return response.status, response_headers, content
    except PreviewConnectionRejected:
        raise
    except (
        httpcore.NetworkError,
        httpcore.ProtocolError,
        httpcore.TimeoutException,
    ) as exc:
        raise PreviewUpstreamError("preview app not reachable yet") from exc


async def _proxy_http(
    scope,
    receive,
    send,
    port: int,
    verify_connection: Callable[[int, int], bool],
    maintenance=None,
) -> None:
    body = b""
    more = True
    while more:
        m = await receive()
        body += m.get("body", b"")
        more = m.get("more_body", False)
    lease, allowed = _ingress_lease(maintenance)
    if not allowed:
        await _reject(scope, send, 423, "maintenance write fenced")
        return
    raw_path = scope.get("raw_path") or scope["path"].encode("utf-8")
    target = _request_target(raw_path, scope.get("query_string") or b"")
    try:
        async with _upstream_http(
            scope["method"],
            target,
            list(scope["headers"]),
            body,
            port,
            verify_connection,
        ) as resp:
            out = [
                (key, value)
                for key, value in resp.headers
                if key.decode("latin-1").lower() not in _RESPONSE_HOP
            ]
            await send(
                {"type": "http.response.start", "status": resp.status, "headers": out}
            )
            async for chunk in resp.aiter_stream():
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
    except PreviewConnectionRejected:
        await _reject(scope, send, 503, "preview app ownership changed")
    except (
        httpcore.NetworkError,
        httpcore.ProtocolError,
        httpcore.TimeoutException,
    ):
        await _reject(scope, send, 502, "preview app not reachable yet")
    finally:
        if lease is not None:
            lease.release()


async def _proxy_ws(
    scope,
    receive,
    send,
    port: int,
    verify_connection: Callable[[int, int], bool],
    maintenance=None,
) -> None:
    path = scope["path"]
    qs = scope.get("query_string") or b""
    if qs:
        path += "?" + qs.decode("latin-1")
    first = await receive()
    if first["type"] != "websocket.connect":
        return
    lease, allowed = _ingress_lease(maintenance)
    if not allowed:
        await _reject(scope, send, 423, "maintenance write fenced")
        return
    subprotocols = scope.get("subprotocols") or None
    uri = f"ws://127.0.0.1:{port}{path}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_connect(
                sock,
                ("127.0.0.1", port),
            ),
            timeout=10,
        )
        client_port = int(sock.getsockname()[1])
        if not verify_connection(port, client_port):
            raise PreviewConnectionRejected(
                "preview connection ownership was not verified"
            )
        up = await websockets.connect(
            uri,
            subprotocols=subprotocols,
            open_timeout=10,
            max_size=None,
            proxy=None,
            sock=sock,
        )
    except Exception:
        sock.close()
        if lease is not None:
            lease.release()
        await send({"type": "websocket.close", "code": 1013})
        return
    accept: dict[str, Any] = {"type": "websocket.accept"}
    if getattr(up, "subprotocol", None):
        accept["subprotocol"] = up.subprotocol
    try:
        await send(accept)
    except Exception:
        await up.close()
        raise
    finally:
        if lease is not None:
            lease.release()

    async def client_to_up() -> None:
        while True:
            m = await receive()
            input_lease, input_allowed = _ingress_lease(maintenance)
            if not input_allowed:
                return
            try:
                t = m["type"]
                if t == "websocket.receive":
                    if m.get("text") is not None:
                        await up.send(m["text"])
                    elif m.get("bytes") is not None:
                        await up.send(m["bytes"])
                elif t == "websocket.disconnect":
                    return
            finally:
                if input_lease is not None:
                    input_lease.release()

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


async def _serve_preview(
    scope,
    receive,
    send,
    *,
    validate_token,
    resolve_port: Callable[[], int | None],
    verify_connection: Callable[[int, int], bool],
    maintenance=None,
) -> None:
    """Shared request path: capability gate → target lookup → proxy."""
    if maintenance is not None and maintenance.fenced():
        return await _reject(scope, send, 423, "maintenance write fenced")
    if not _authed(scope, validate_token):
        return await _reject(scope, send, 403, "preview: not authorized")
    port = resolve_port()
    if not port:
        return await _reject(scope, send, 503, "preview app not running")
    if scope["type"] == "http":
        return await _proxy_http(
            scope,
            receive,
            send,
            port,
            verify_connection,
            maintenance,
        )
    return await _proxy_ws(
        scope,
        receive,
        send,
        port,
        verify_connection,
        maintenance,
    )


class PreviewProxyMiddleware:
    def __init__(
        self,
        app: Any,
        fastapi_app: Any,
        apps_domain: str | None,
        validate_token=None,
        maintenance=None,
    ) -> None:
        self.app = app
        self.fastapi_app = fastapi_app  # for app.state.app_manager at request time
        self.suffix = ("." + apps_domain.lower()) if apps_domain else None
        # Preview subdomains have NO Cloudflare Access gate (so they can be iframed),
        # so THIS is their only auth: require a short-lived preview-only capability.
        # It is never an owner API session and is never forwarded to project code.
        self.validate_token = validate_token
        self.maintenance = maintenance

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
            label = host[
                : -len(self.suffix)
            ]  # e.g. "preview-myapp" — or "os" for the main app
            # Only intercept our own `preview-<slug>` single-label hosts; everything
            # else under the zone (proxima.example.com, www, …) passes through untouched.
            if "." not in label and label.startswith("preview-"):
                slug = label[len("preview-") :]
                return slug or None
        return None

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            slug = self._slug_for(scope)
            if slug is not None:

                def resolve_port() -> int | None:
                    return self.fastapi_app.state.app_manager.preview_target(slug)

                def verify_connection(
                    target_port: int,
                    client_port: int,
                ) -> bool:
                    return self.fastapi_app.state.app_manager.verify_preview_connection(
                        slug,
                        target_port,
                        client_port,
                    )

                return await _serve_preview(
                    scope,
                    receive,
                    send,
                    validate_token=self.validate_token,
                    resolve_port=resolve_port,
                    verify_connection=verify_connection,
                    maintenance=self.maintenance,
                )
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

    def __init__(
        self,
        bind_host: str | None,
        port_for: Callable[[str], int | None],
        verify_connection: Callable[[str, int, int], bool] | None = None,
        validate_token=None,
        maintenance=None,
    ) -> None:
        # bind_host must be remote-reachable for remote preview to work. "auto"
        # adds loopback beside the tailnet interface - never 0.0.0.0; "off"
        # (or empty) disables relays entirely for strict loopback-only installs.
        self.bind_hosts = resolve_preview_bind_hosts(bind_host)
        self.bind_host = self.bind_hosts[-1]
        self.enabled = self.bind_host.lower() not in ("", "off", "none", "disabled")
        if self.enabled:
            _LOG.info("preview relays bind %s", ", ".join(self.bind_hosts))
        self.port_for = port_for
        self.verify_connection = verify_connection
        self.validate_token = validate_token
        self.maintenance = maintenance
        self._relays: dict[str, dict[str, Any]] = {}

    def port(self, slug: str) -> int | None:
        relay = self._relays.get(slug)
        return relay["port"] if relay else None

    def _asgi_for(self, slug: str):
        async def relay_app(scope, receive, send):
            if scope["type"] not in ("http", "websocket"):
                return

            def verify_connection(
                target_port: int,
                client_port: int,
            ) -> bool:
                return bool(
                    self.verify_connection
                    and self.verify_connection(
                        slug,
                        target_port,
                        client_port,
                    )
                )

            await _serve_preview(
                scope,
                receive,
                send,
                validate_token=self.validate_token,
                resolve_port=lambda: self.port_for(slug),
                verify_connection=verify_connection,
                maintenance=self.maintenance,
            )

        return relay_app

    async def start(self, slug: str) -> int | None:
        if not self.enabled:
            return None
        await self.stop(slug)
        sockets: list[socket.socket] = []
        port = 0
        try:
            for bind_host in self.bind_hosts:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((bind_host, port))
                sock.listen(128)
                sockets.append(sock)
                if port == 0:
                    port = int(sock.getsockname()[1])
        except OSError:
            for sock in sockets:
                with contextlib.suppress(OSError):
                    sock.close()
            raise
        server = _RelayServer(
            uvicorn.Config(
                self._asgi_for(slug),
                lifespan="off",
                access_log=False,
                log_level="warning",
                ws="websockets-sansio",
            )
        )
        task = asyncio.create_task(server.serve(sockets=sockets))
        self._relays[slug] = {
            "server": server,
            "task": task,
            "sockets": sockets,
            "port": port,
        }
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
        for sock in relay["sockets"]:
            with contextlib.suppress(OSError):
                sock.close()

    async def shutdown(self) -> None:
        for slug in list(self._relays):
            await self.stop(slug)

"""Host-based reverse proxy for remote app previews.

A project's running dev server (Vite/Next/static/…) is reachable at its own
subdomain `<slug>.<APPS_DOMAIN>` that rides the same Cloudflare tunnel as the
main app. This ASGI middleware intercepts requests whose Host is such a
subdomain and proxies them (HTTP + WebSocket) to that app's local dev port —
root-relative, so SPA absolute asset paths (`/assets/x.js`) and Vite HMR just
work, without the path-rewriting the sub-path proxy needs.

Unset APPS_DOMAIN ⇒ this is a no-op passthrough (local-only preview unchanged).
Auth: preview subdomains are gated at the edge by Cloudflare Access (same policy
as the main app), so requests arriving over the tunnel are already the owner.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import websockets

_LOG = logging.getLogger("proxima.preview_proxy")

# Hop-by-hop headers must not be forwarded verbatim across a proxy.
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "content-length",
        "content-encoding", "host"}


class PreviewProxyMiddleware:
    def __init__(self, app: Any, fastapi_app: Any, apps_domain: str | None, validate_token=None) -> None:
        self.app = app
        self.fastapi_app = fastapi_app  # for app.state.app_manager at request time
        self.suffix = ("." + apps_domain.lower()) if apps_domain else None
        # Preview subdomains have NO Cloudflare Access gate (so they can be iframed),
        # so THIS is their only auth: require a valid app-session token in the
        # `proxima_preview` cookie. validate_token(token) -> truthy if the token is a live
        # session. Fail-closed: no validator or bad/absent cookie ⇒ 403.
        self.validate_token = validate_token

    def _authed(self, scope: dict[str, Any]) -> bool:
        if not self.validate_token:
            return False
        cookie = ""
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                cookie = v.decode("latin-1")
                break
        token = ""
        for part in cookie.split(";"):
            p = part.strip()
            if p.startswith("proxima_preview="):
                token = p[len("proxima_preview="):]
                break
        if not token:
            return False
        try:
            return bool(self.validate_token(token))
        except Exception:
            return False

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
                if not self._authed(scope):
                    return await self._reject(scope, send, 403, "preview: not authorized")
                port = self.fastapi_app.state.app_manager.port(slug)
                if not port:
                    return await self._reject(scope, send, 503, "preview app not running")
                if scope["type"] == "http":
                    return await self._proxy_http(scope, receive, send, port)
                return await self._proxy_ws(scope, receive, send, port)
        await self.app(scope, receive, send)

    async def _reject(self, scope, send, status: int, msg: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": msg.encode()})

    async def _proxy_http(self, scope, receive, send, port: int) -> None:
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
                           for k, v in resp.headers.items() if k.lower() not in _HOP]
                    await send({"type": "http.response.start", "status": resp.status_code, "headers": out})
                    async for chunk in resp.aiter_raw():
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
        except httpx.RequestError:
            await self._reject(scope, send, 502, "preview app not reachable yet")

    async def _proxy_ws(self, scope, receive, send, port: int) -> None:
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

"""Capability-gated, Area-bound origins for canonical file previews."""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode

import uvicorn
from starlette.responses import FileResponse, RedirectResponse, Response

from . import container_registry, file_targets
from .db import connect
from .preview_proxy import resolve_preview_bind_host

FILE_PREVIEW_COOKIE = "proxima_file_preview"
FILE_PREVIEW_TTL_SECONDS = 60 * 60
_CAPABILITY_QUERY = "__proxima_cap"


@dataclass(frozen=True)
class PreviewArea:
    project_id: int
    kind: str
    area_id: int | None

    @classmethod
    def from_locator(cls, project_id: int, locator: file_targets.FileLocator) -> PreviewArea:
        return cls(
            project_id=project_id,
            kind=locator.area.kind,
            area_id=locator.area.id,
        )

    def cookie_name(self) -> str:
        return (
            f"{FILE_PREVIEW_COOKIE}_{self.project_id}_"
            f"{self.kind}_{self.area_id or 0}"
        )

    def host_label(self) -> str:
        return f"file-{self.project_id}-{self.kind}-{self.area_id or 0}"


def mint_file_preview_token(
    secret: bytes,
    area: PreviewArea,
    *,
    ttl_seconds: int = FILE_PREVIEW_TTL_SECONDS,
) -> str:
    payload = {
        "expires": int(time.time()) + ttl_seconds,
        "project": area.project_id,
        "kind": area.kind,
        "area": area.area_id,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{encoded}.{signature}"


def valid_file_preview_token(
    secret: bytes,
    token: str,
    area: PreviewArea,
    *,
    now: int | None = None,
) -> bool:
    try:
        encoded, signature = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            return False
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        return (
            isinstance(payload, dict)
            and payload.get("project") == area.project_id
            and payload.get("kind") == area.kind
            and payload.get("area") == area.area_id
            and int(payload.get("expires") or 0)
            >= (int(time.time()) if now is None else now)
        )
    except (
        binascii.Error,
        OverflowError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False


class _TargetPreviewServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:
        pass


class TargetPreviewManager:
    def __init__(
        self,
        *,
        database_path: str,
        apps_domain: str | None,
        bind_host: str | None,
        maintenance: Any = None,
        provision_hostname: Callable[[PreviewArea], Awaitable[None]] | None = None,
    ) -> None:
        self.database_path = database_path
        self.apps_domain = (apps_domain or "").strip().lower() or None
        self.bind_host = resolve_preview_bind_host(bind_host)
        self.maintenance = maintenance
        self.provision_hostname = provision_hostname
        self.secret = secrets.token_bytes(32)
        self._relays: dict[tuple[PreviewArea, str], dict[str, Any]] = {}
        self._provisioned: set[PreviewArea] = set()
        self._provision_locks: dict[PreviewArea, asyncio.Lock] = {}

    @staticmethod
    def _is_loopback_address(host: str) -> bool:
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _format_host(host: str) -> str:
        return f"[{host}]" if ":" in host and not host.startswith("[") else host

    @staticmethod
    def _encoded_path(path: str) -> str:
        return "/".join(
            quote(part, safe="")
            for part in path.split("/")
            if part
        )

    def _preview_host_label(self, host: str) -> str | None:
        hostname = host.split(":", 1)[0].lower().rstrip(".")
        suffixes = [".localhost", ".testserver"]
        if self.apps_domain:
            suffixes.append(f".{self.apps_domain}")
        for suffix in suffixes:
            if hostname.endswith(suffix):
                label = hostname[: -len(suffix)]
                return label if label and "." not in label else None
        return None

    def _host_area(self, host: str) -> PreviewArea | None:
        label = self._preview_host_label(host)
        if label is None:
            return None
        parts = label.split("-")
        if len(parts) != 4 or parts[0] != "file":
            return None
        try:
            project_id = int(parts[1])
            area_id_raw = int(parts[3])
        except ValueError:
            return None
        kind = parts[2]
        if project_id <= 0 or kind not in file_targets.AREA_KINDS:
            return None
        if kind == "container":
            if area_id_raw != 0:
                return None
            area_id = None
        else:
            if area_id_raw <= 0:
                return None
            area_id = area_id_raw
        return PreviewArea(project_id=project_id, kind=kind, area_id=area_id)

    async def issue_url(
        self,
        request: Any,
        project_id: int,
        locator: file_targets.FileLocator,
    ) -> str:
        if self.maintenance is not None and self.maintenance.fenced():
            raise RuntimeError("dedicated file previews are unavailable")
        area = PreviewArea.from_locator(project_id, locator)
        token = mint_file_preview_token(self.secret, area)
        hostname = str(request.url.hostname or "").lower().rstrip(".")
        if self.apps_domain:
            if self.provision_hostname is not None and area not in self._provisioned:
                lock = self._provision_locks.setdefault(area, asyncio.Lock())
                async with lock:
                    if area not in self._provisioned:
                        try:
                            await self.provision_hostname(area)
                        except Exception as exc:
                            raise RuntimeError(
                                "file preview hostname provisioning failed"
                            ) from exc
                        self._provisioned.add(area)
            origin = f"https://{area.host_label()}.{self.apps_domain}"
        elif (
            hostname in {"localhost", "testserver"}
            or hostname.endswith(".localhost")
        ):
            suffix = "testserver" if hostname == "testserver" else "localhost"
            server = request.scope.get("server") or (None, None)
            port = server[1]
            default_port = 443 if request.url.scheme == "https" else 80
            port_suffix = f":{port}" if port and int(port) != default_port else ""
            origin = (
                f"{request.url.scheme}://{area.host_label()}."
                f"{suffix}{port_suffix}"
            )
        else:
            bind_host = (
                hostname
                if (
                    self._is_loopback_address(hostname)
                    and self.bind_host.lower()
                    not in {"", "off", "none", "disabled"}
                )
                else self.bind_host
            )
            port = await self._relay_port(area, bind_host)
            origin = (
                f"http://{self._format_host(hostname)}:{port}"
            )
        encoded = self._encoded_path(locator.path)
        query = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key != _CAPABILITY_QUERY
        ]
        query.append((_CAPABILITY_QUERY, token))
        return f"{origin}/{encoded}?{urlencode(query)}"

    async def _relay_port(self, area: PreviewArea, bind_host: str) -> int:
        relay_key = (area, bind_host)
        relay = self._relays.get(relay_key)
        if relay is not None:
            return int(relay["port"])
        if bind_host.lower() in {"", "off", "none", "disabled"}:
            raise RuntimeError("dedicated file preview origins are disabled")
        family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, 0))
            sock.listen(128)
            port = int(sock.getsockname()[1])
            server = _TargetPreviewServer(
                uvicorn.Config(
                    self._relay_app(area),
                    lifespan="off",
                    access_log=False,
                    log_level="warning",
                )
            )
            task = asyncio.create_task(server.serve(sockets=[sock]))
        except BaseException:
            sock.close()
            raise
        self._relays[relay_key] = {
            "server": server,
            "task": task,
            "socket": sock,
            "port": port,
        }
        return port

    def _relay_app(self, area: PreviewArea):
        async def relay_app(scope, receive, send):
            if scope["type"] != "http":
                await self._reject(scope, send, 404, "preview route not found")
                return
            await self.serve(area, scope, receive, send)

        return relay_app

    async def _reject(
        self,
        scope: dict[str, Any],
        send,
        status: int,
        message: str,
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = Response(message, status_code=status, media_type="text/plain")
        await response(scope, self._empty_receive, send)

    @staticmethod
    async def _empty_receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }

    @staticmethod
    def _cookie(scope: dict[str, Any], name: str) -> str:
        for key, value in scope.get("headers", []):
            if key != b"cookie":
                continue
            for part in value.decode("latin-1").split(";"):
                item = part.strip()
                if item.startswith(f"{name}="):
                    return item[len(name) + 1 :]
        return ""

    @staticmethod
    def _capability_query(scope: dict[str, Any]) -> tuple[str, str]:
        pairs = parse_qsl(
            (scope.get("query_string") or b"").decode("latin-1"),
            keep_blank_values=True,
        )
        token = next(
            (value for key, value in pairs if key == _CAPABILITY_QUERY),
            "",
        )
        clean = urlencode(
            [(key, value) for key, value in pairs if key != _CAPABILITY_QUERY]
        )
        return token, clean

    async def serve(
        self,
        area: PreviewArea,
        scope: dict[str, Any],
        receive,
        send,
    ) -> None:
        if self.maintenance is not None and self.maintenance.fenced():
            await self._reject(scope, send, 423, "maintenance write fenced")
            return
        if scope.get("method") not in {"GET", "HEAD"}:
            await self._reject(scope, send, 405, "preview method not allowed")
            return
        query_token, clean_query = self._capability_query(scope)
        cookie_name = area.cookie_name()
        cookie_token = self._cookie(scope, cookie_name)
        token = query_token or cookie_token
        if not valid_file_preview_token(self.secret, token, area):
            await self._reject(scope, send, 403, "preview capability is invalid")
            return
        if query_token:
            location = scope.get("path") or "/"
            if clean_query:
                location = f"{location}?{clean_query}"
            response = RedirectResponse(location, status_code=307)
            response.set_cookie(
                cookie_name,
                query_token,
                path="/",
                max_age=FILE_PREVIEW_TTL_SECONDS,
                httponly=True,
                secure=scope.get("scheme") == "https",
                samesite=(
                    "none"
                    if scope.get("scheme") == "https"
                    else "lax"
                ),
            )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            await response(scope, receive, send)
            return
        relative = str(scope.get("path") or "").lstrip("/")
        try:
            normalized = file_targets.normalize_relative_path(
                relative,
                allow_empty=False,
            )
            conn = connect(self.database_path, read_only=True)
            try:
                project = conn.execute(
                    "SELECT id, slug, path FROM projects WHERE id = ?",
                    (area.project_id,),
                ).fetchone()
                if project is None:
                    raise file_targets.FileTargetError(
                        "file preview Container is unavailable"
                    )
                locator = file_targets.FileLocator(
                    project=str(project["slug"]),
                    area=file_targets.FileArea(
                        kind=area.kind,
                        id=area.area_id,
                    ),
                    path=normalized,
                )
                resolved = file_targets.resolve_locator(
                    conn,
                    project,
                    locator,
                )
            finally:
                conn.close()
        except (
            FileNotFoundError,
            container_registry.ContainerBoundaryError,
            file_targets.FileTargetError,
        ):
            await self._reject(scope, send, 404, "preview file not found")
            return
        if not resolved.path.is_file():
            await self._reject(scope, send, 404, "preview file not found")
            return
        headers = {
            "Cache-Control": "private, no-store",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }
        if resolved.path.suffix.lower() in {".html", ".htm"}:
            headers["Content-Security-Policy"] = "; ".join(
                (
                    "sandbox allow-scripts allow-same-origin",
                    "default-src 'self' data: blob:",
                    "script-src 'self' 'unsafe-inline' blob:",
                    "style-src 'self' 'unsafe-inline'",
                    "img-src 'self' data: blob:",
                    "media-src 'self' blob:",
                    "font-src 'self' data:",
                    "connect-src 'self'",
                    "worker-src 'self' blob:",
                    "frame-src 'self'",
                    "object-src 'none'",
                    "base-uri 'none'",
                    "form-action 'none'",
                    "navigate-to 'self'",
                )
            )
        response = FileResponse(str(resolved.path), headers=headers)
        await response(scope, receive, send)

    async def shutdown(self) -> None:
        for relay_key in list(self._relays):
            relay = self._relays.pop(relay_key)
            relay["server"].should_exit = True
            try:
                await asyncio.wait_for(relay["task"], timeout=5)
            except Exception:
                relay["task"].cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await relay["task"]
            with contextlib.suppress(OSError):
                relay["socket"].close()


class TargetPreviewMiddleware:
    def __init__(self, app: Any, manager: TargetPreviewManager) -> None:
        self.app = app
        self.manager = manager

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] in {"http", "websocket"}:
            host = ""
            for key, value in scope.get("headers", []):
                if key == b"host":
                    host = value.decode("latin-1")
                    break
            area = self.manager._host_area(host)
            if area is not None:
                await self.manager.serve(area, scope, receive, send)
                return
            label = self.manager._preview_host_label(host)
            if label is not None and label.startswith("file-"):
                await self.manager._reject(
                    scope,
                    send,
                    404,
                    "preview origin is invalid",
                )
                return
        await self.app(scope, receive, send)

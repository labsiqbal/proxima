"""Capability-gated, Area-bound origins for canonical file previews."""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import hmac
import html
import ipaddress
import json
import mimetypes
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import uvicorn
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import container_registry, file_targets
from .db import connect
from .preview_proxy import resolve_preview_bind_host

FILE_PREVIEW_COOKIE = "proxima_file_preview"
FILE_PREVIEW_TTL_SECONDS = 60 * 60
_CAPABILITY_QUERY = "__proxima_cap"
_ACTIVE_MEDIA_TYPES = frozenset(
    {
        "application/xhtml+xml",
        "application/xml",
        "image/svg+xml",
        "text/html",
        "text/xml",
    }
)
_HTML_MEDIA_TYPES = frozenset({"text/html"})
_SCRIPT_MEDIA_TYPES = frozenset(
    {
        "application/ecmascript",
        "application/javascript",
        "text/ecmascript",
        "text/javascript",
    }
)
_SAME_ORIGIN_RESOURCE_FETCH_METADATA = frozenset(
    {
        ("cors", "empty"),
        ("cors", "audio"),
        ("cors", "audioworklet"),
        ("cors", "font"),
        ("cors", "image"),
        ("cors", "json"),
        ("cors", "manifest"),
        ("cors", "paintworklet"),
        ("cors", "script"),
        ("cors", "sharedworker"),
        ("cors", "style"),
        ("cors", "text"),
        ("cors", "track"),
        ("cors", "video"),
        ("cors", "worker"),
        ("no-cors", "empty"),
        ("no-cors", "audio"),
        ("no-cors", "image"),
        ("no-cors", "manifest"),
        ("no-cors", "script"),
        ("no-cors", "style"),
        ("no-cors", "track"),
        ("no-cors", "video"),
        ("same-origin", "empty"),
        ("same-origin", "audioworklet"),
        ("same-origin", "paintworklet"),
        ("same-origin", "script"),
        ("same-origin", "serviceworker"),
        ("same-origin", "sharedworker"),
        ("same-origin", "style"),
        ("same-origin", "worker"),
    }
)


@dataclass(frozen=True)
class PreviewArea:
    project_id: int
    kind: str
    area_id: int | None

    @classmethod
    def from_locator(
        cls,
        project_id: int,
        locator: file_targets.FileLocator,
    ) -> PreviewArea:
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


def preview_media_type(path: Path | str) -> str:
    guessed, _ = mimetypes.guess_type(str(path), strict=False)
    return (guessed or "application/octet-stream").lower()


def is_active_preview_media_type(media_type: str) -> bool:
    return media_type.split(";", 1)[0].strip().lower() in _ACTIVE_MEDIA_TYPES


def _encode_payload(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")


def mint_file_preview_token(
    secret: bytes,
    area: PreviewArea,
    *,
    ttl_seconds: int = FILE_PREVIEW_TTL_SECONDS,
    frame_origin: str = "",
) -> str:
    encoded = _encode_payload(
        {
            "expires": int(time.time()) + ttl_seconds,
            "project": area.project_id,
            "kind": area.kind,
            "area": area.area_id,
            "nonce": secrets.token_urlsafe(12),
            "frame_origin": frame_origin,
        }
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{encoded}.{signature}"


def _file_preview_token_payload(
    secret: bytes,
    token: str,
    *,
    now: int | None = None,
) -> dict[str, Any] | None:
    try:
        encoded, signature = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("expires") or 0) < (
            int(time.time()) if now is None else now
        ):
            return None
        return payload
    except (
        binascii.Error,
        OverflowError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None


def _area_from_payload(payload: dict[str, Any]) -> PreviewArea | None:
    try:
        project_id = int(payload.get("project"))
        kind = str(payload.get("kind") or "")
        raw_area_id = payload.get("area")
        area_id = None if raw_area_id is None else int(raw_area_id)
    except (TypeError, ValueError, OverflowError):
        return None
    if project_id <= 0 or kind not in file_targets.AREA_KINDS:
        return None
    if kind == "container" and area_id is not None:
        return None
    if kind != "container" and (area_id is None or area_id <= 0):
        return None
    return PreviewArea(project_id=project_id, kind=kind, area_id=area_id)


def valid_file_preview_token(
    secret: bytes,
    token: str,
    area: PreviewArea,
    *,
    now: int | None = None,
) -> bool:
    payload = _file_preview_token_payload(secret, token, now=now)
    return payload is not None and _area_from_payload(payload) == area


def _normalized_origin(scheme: str, host: str) -> str:
    scheme = scheme.strip().lower()
    host = host.strip().lower().rstrip(".")
    if scheme not in {"http", "https"} or not host:
        raise ValueError("preview origin is invalid")
    if any(character in host for character in "\r\n/#?@"):
        raise ValueError("preview origin is invalid")
    parsed = urlsplit(f"{scheme}://{host}")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("preview origin is invalid")
    return f"{scheme}://{parsed.netloc}"


def _request_origin(request: Request) -> str:
    scheme = (
        request.headers.get("x-forwarded-proto")
        or request.url.scheme
    ).split(",", 1)[0]
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",", 1)[0]
    return _normalized_origin(scheme, host)


def _optional_header(scope: Scope, name: str) -> str | None:
    encoded = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key.lower() == encoded:
            return value.decode("latin-1")
    return None


def _header(scope: Scope, name: str) -> str:
    return _optional_header(scope, name) or ""


def _capability_query(scope: Scope) -> tuple[str, str]:
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


@dataclass(frozen=True)
class _PreviewFetchMetadata:
    site: str | None
    mode: str | None
    destination: str | None
    opaque_origin: bool

    @classmethod
    def from_scope(cls, scope: Scope) -> _PreviewFetchMetadata:
        site = _optional_header(scope, "sec-fetch-site")
        mode = _optional_header(scope, "sec-fetch-mode")
        destination = _optional_header(scope, "sec-fetch-dest")
        return cls(
            site=site.strip().lower() if site is not None else None,
            mode=mode.strip().lower() if mode is not None else None,
            destination=(
                destination.strip().lower()
                if destination is not None
                else None
            ),
            opaque_origin=(
                _header(scope, "origin").strip().lower() == "null"
            ),
        )

    def admits_area_request(self, *, capability_present: bool) -> bool:
        if (
            self.opaque_origin
            or self.site is None
            or self.mode is None
            or self.destination is None
        ):
            return False
        if self.destination == "document":
            return False
        if self.mode == "navigate":
            return (
                self.destination in {"iframe", "frame"}
                and (
                    self.site == "same-origin"
                    or (
                        self.site in {"same-site", "cross-site"}
                        and capability_present
                    )
                )
            )
        if self.site == "same-origin":
            return (
                self.mode,
                self.destination,
            ) in _SAME_ORIGIN_RESOURCE_FETCH_METADATA
        return False


def _scope_origin(scope: Scope) -> str:
    forwarded_scheme = _header(scope, "x-forwarded-proto")
    forwarded_host = _header(scope, "x-forwarded-host")
    scheme = (forwarded_scheme or str(scope.get("scheme") or "http")).split(
        ",",
        1,
    )[0]
    host = (forwarded_host or _header(scope, "host")).split(",", 1)[0]
    return _normalized_origin(scheme, host)


def _frame_source(origin: str) -> str:
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "'none'"
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return "'none'"
        return _normalized_origin(parsed.scheme, parsed.netloc)
    except ValueError:
        return "'none'"


def passive_preview_headers(request: Request) -> dict[str, str]:
    frame_source = _frame_source(_request_origin(request))
    return {
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": f"frame-ancestors {frame_source}",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


def _is_service_worker_request(scope: Scope) -> bool:
    return (
        _header(scope, "service-worker").strip().lower() == "script"
        or _header(scope, "sec-fetch-dest").strip().lower() == "serviceworker"
    )


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
        request: Request,
        project_id: int,
        locator: file_targets.FileLocator,
    ) -> str:
        if self.maintenance is not None and self.maintenance.fenced():
            raise RuntimeError("dedicated file previews are unavailable")
        area = PreviewArea.from_locator(project_id, locator)
        frame_origin = _request_origin(request)
        parsed_origin = urlsplit(frame_origin)
        hostname = str(parsed_origin.hostname or "")
        encoded = self._encoded_path(locator.path)
        query = [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key != _CAPABILITY_QUERY
        ]

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
        elif hostname in {"localhost", "testserver"} or hostname.endswith(
            ".localhost"
        ):
            suffix_host = "testserver" if hostname == "testserver" else "localhost"
            default_port = 443 if parsed_origin.scheme == "https" else 80
            port_suffix = (
                f":{parsed_origin.port}"
                if parsed_origin.port and parsed_origin.port != default_port
                else ""
            )
            origin = (
                f"{parsed_origin.scheme}://{area.host_label()}."
                f"{suffix_host}{port_suffix}"
            )
        elif parsed_origin.scheme == "https":
            raise RuntimeError(
                "a distinct TLS file preview origin is unavailable"
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
            origin = f"http://{self._format_host(hostname)}:{port}"

        token = mint_file_preview_token(
            self.secret,
            area,
            frame_origin=frame_origin,
        )
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

    def _relay_app(self, area: PreviewArea) -> ASGIApp:
        async def relay_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self._reject(scope, send, 404, "preview route not found")
                return
            await self.dispatch(area, scope, receive, send)

        return relay_app

    async def _reject(
        self,
        scope: Scope,
        send: Send,
        status: int,
        message: str,
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = Response(message, status_code=status, media_type="text/plain")
        await response(scope, self._empty_receive, send)

    @staticmethod
    async def _empty_receive() -> Message:
        return {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }

    @staticmethod
    def _cookie(scope: Scope, name: str) -> str:
        raw = _header(scope, "cookie")
        for part in raw.split(";"):
            item = part.strip()
            if item.startswith(f"{name}="):
                return item[len(name) + 1 :]
        return ""

    async def dispatch(
        self,
        area: PreviewArea,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        query_token, _ = _capability_query(scope)
        cookie_token = self._cookie(scope, area.cookie_name())
        metadata = _PreviewFetchMetadata.from_scope(scope)
        if not metadata.admits_area_request(
            capability_present=bool(query_token or cookie_token),
        ):
            await self._reject(
                scope,
                send,
                403,
                "preview request metadata is invalid",
            )
            return
        await self._serve_admitted(area, scope, receive, send)

    async def _serve_admitted(
        self,
        area: PreviewArea,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if self.maintenance is not None and self.maintenance.fenced():
            await self._reject(scope, send, 423, "maintenance write fenced")
            return
        if scope.get("method") not in {"GET", "HEAD"}:
            await self._reject(scope, send, 405, "preview method not allowed")
            return
        query_token, clean_query = _capability_query(scope)
        cookie_name = area.cookie_name()
        token = query_token or self._cookie(scope, cookie_name)
        payload = _file_preview_token_payload(self.secret, token)
        if payload is None or _area_from_payload(payload) != area:
            await self._reject(scope, send, 403, "preview capability is invalid")
            return
        if _is_service_worker_request(scope):
            await self._reject(scope, send, 403, "service workers are unavailable")
            return
        if query_token:
            location = scope.get("path") or "/"
            if clean_query:
                location = f"{location}?{clean_query}"
            secure = scope.get("scheme") == "https"
            host_routed = self._host_area(_header(scope, "host")) == area
            hostname = _header(scope, "host").split(":", 1)[0].lower()
            secure_cookie = secure or (
                host_routed and hostname.endswith(".localhost")
            )
            if secure or host_routed:
                frame_source = _frame_source(
                    str(payload.get("frame_origin") or "")
                )
                escaped_location = html.escape(location, quote=True)
                response = Response(
                    (
                        "<!doctype html><meta charset=\"utf-8\">"
                        "<meta http-equiv=\"refresh\" "
                        f"content=\"0;url={escaped_location}\">"
                    ),
                    media_type="text/html",
                    headers={
                        "Content-Security-Policy": (
                            "default-src 'none'; base-uri 'none'; "
                            "form-action 'none'; "
                            f"frame-ancestors {frame_source}"
                        ),
                    },
                )
            else:
                response = RedirectResponse(location, status_code=307)
            response.set_cookie(
                cookie_name,
                query_token,
                path="/",
                max_age=FILE_PREVIEW_TTL_SECONDS,
                httponly=True,
                secure=secure_cookie,
                samesite="none" if secure_cookie else "strict",
            )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            await response(scope, receive, send)
            return
        await self._serve_file(
            area,
            str(scope.get("path") or "").lstrip("/"),
            scope,
            receive,
            send,
            frame_origin=str(payload.get("frame_origin") or ""),
        )

    async def _serve_file(
        self,
        area: PreviewArea,
        relative: str,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        frame_origin: str,
    ) -> None:
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

        media_type = preview_media_type(resolved.path)
        frame_source = _frame_source(frame_origin)
        try:
            preview_source = _frame_source(_scope_origin(scope))
        except ValueError:
            preview_source = "'none'"
        frame_ancestors = " ".join(
            dict.fromkeys(
                source
                for source in (frame_source, preview_source)
                if source != "'none'"
            )
        ) or "'none'"
        headers = {
            "Cache-Control": "private, no-store",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }
        if media_type in _HTML_MEDIA_TYPES:
            policy = (
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
            )
        elif is_active_preview_media_type(media_type):
            policy = (
                "sandbox",
                "default-src 'none'",
                "object-src 'none'",
            )
        elif media_type in _SCRIPT_MEDIA_TYPES:
            policy = (
                "default-src 'none'",
                "script-src 'self'",
                "connect-src 'self'",
                "worker-src 'none'",
                "object-src 'none'",
            )
        else:
            policy = ()
        headers["Content-Security-Policy"] = "; ".join(
            (*policy, f"frame-ancestors {frame_ancestors}")
        )

        if is_active_preview_media_type(media_type) and media_type not in _HTML_MEDIA_TYPES:
            response = FileResponse(
                str(resolved.path),
                filename=resolved.path.name,
                content_disposition_type="attachment",
                media_type=media_type,
                headers=headers,
            )
        else:
            response = FileResponse(
                str(resolved.path),
                media_type=media_type,
                headers=headers,
            )
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
    def __init__(self, app: ASGIApp, manager: TargetPreviewManager) -> None:
        self.app = app
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            host = _header(scope, "host")
            area = self.manager._host_area(host)
            if area is not None:
                await self.manager.dispatch(area, scope, receive, send)
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
            if label is not None and label.startswith("preview-"):
                await self.app(scope, receive, send)
                return
            fetch_site = _header(scope, "sec-fetch-site").strip().lower()
            fetch_destination = _header(
                scope,
                "sec-fetch-dest",
            ).strip().lower()
            if (
                fetch_site in {"same-site", "cross-site"}
                and fetch_destination
                and fetch_destination != "document"
            ) or _header(scope, "origin").strip().lower() == "null":
                await self.manager._reject(
                    scope,
                    send,
                    403,
                    "preview navigation cannot access Proxima",
                )
                return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def guarded_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if is_active_preview_media_type(
                    headers.get("content-type", "")
                ):
                    policy = headers.get("content-security-policy", "")
                    if "frame-ancestors" not in policy.lower():
                        headers["Content-Security-Policy"] = (
                            f"{policy}; frame-ancestors 'none'"
                            if policy
                            else "frame-ancestors 'none'"
                        )
                    headers["X-Frame-Options"] = "DENY"
                    headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, guarded_send)

"""Sandboxed canonical file preview: passive by default, active on consent.

Previews are served from Proxima's own origin and rendered inside an iframe
whose `sandbox` attribute never contains `allow-same-origin`. Every preview
response repeats that decision as a CSP `sandbox` directive, so the preview
document always lands in an **opaque origin**: it cannot read Proxima's DOM,
storage, cookies, or session, and the browser refuses to hand it any
same-site credential.

Two modes:

- **Passive** (default): no scripts at all - `sandbox` without
  `allow-scripts`, plus a `default-src 'none'` policy that only permits
  inline styles and `data:` media. This is what plain preview uses.
- **Active**: scripts run, still inside the opaque sandbox, and only after
  the owner accepts the consent screen in Artifact Review. Consent is
  recorded server-side by `ActivePreviewConsent`, scoped to one owner
  session, one Area, and one viewer, and requires the bearer token so an
  ambient cookie or a cross-site form can never self-enable it.

`PreviewIsolationMiddleware` protects the other direction: framed content
(a preview document, a running app preview) may not pull Proxima routes as
a subresource, and Proxima's own HTML never becomes frameable.

See ADR-0042; it supersedes the retired capability-origin choreography.
"""
from __future__ import annotations

import mimetypes
import threading
from dataclasses import dataclass
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import file_targets

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

# Passive: an opaque sandbox with no script execution of any kind. `'self'`
# never appears because an opaque origin matches nothing - listing it would
# only suggest a same-origin power the document does not have.
_PASSIVE_HTML_POLICY = (
    "sandbox",
    "default-src 'none'",
    "style-src 'unsafe-inline'",
    "img-src data:",
    "media-src data:",
    "font-src data:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
)
# Active: scripts and outbound network, still opaque. The consent screen says
# exactly this - Proxima makes no confidentiality promise for the previewed
# Area once the owner turns it on.
_ACTIVE_HTML_POLICY = (
    "sandbox allow-scripts",
    "default-src 'none'",
    "script-src 'unsafe-inline' blob: data:",
    "style-src 'unsafe-inline'",
    "img-src * data: blob:",
    "media-src * data: blob:",
    "font-src * data:",
    "connect-src *",
    "worker-src blob:",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
)
# Non-HTML executable media (SVG, XML) is downloaded, never rendered inline.
_INERT_ACTIVE_MEDIA_POLICY = (
    "sandbox",
    "default-src 'none'",
    "object-src 'none'",
)


def preview_media_type(path: Path | str) -> str:
    guessed, _ = mimetypes.guess_type(str(path), strict=False)
    return (guessed or "application/octet-stream").lower()


def is_active_preview_media_type(media_type: str) -> bool:
    return media_type.split(";", 1)[0].strip().lower() in _ACTIVE_MEDIA_TYPES


def is_html_preview_media_type(media_type: str) -> bool:
    return media_type.split(";", 1)[0].strip().lower() in _HTML_MEDIA_TYPES


def preview_headers(media_type: str, *, active: bool = False) -> dict[str, str]:
    """Response headers for one preview body.

    `active` is only honoured for HTML; every other media type keeps its
    passive policy regardless of the requested mode.
    """
    if is_html_preview_media_type(media_type):
        policy = _ACTIVE_HTML_POLICY if active else _PASSIVE_HTML_POLICY
    elif is_active_preview_media_type(media_type):
        policy = _INERT_ACTIVE_MEDIA_POLICY
    else:
        policy = ()
    return {
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "; ".join(
            (*policy, "frame-ancestors 'self'")
        ),
        "Cross-Origin-Opener-Policy": "same-origin",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


@dataclass(frozen=True)
class PreviewArea:
    """The Area a preview document belongs to - the consent scope."""

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


class ActivePreviewConsent:
    """Owner consent for running scripts in a preview.

    One grant covers one owner session, one Area, and one mounted viewer.
    Nothing is persisted: a restart, a logout, or closing the viewer returns
    every preview to passive.
    """

    def __init__(self) -> None:
        self._granted: set[tuple[str, PreviewArea, str]] = set()
        self._lock = threading.RLock()

    def grant(
        self,
        *,
        owner_session: str,
        area: PreviewArea,
        preview_session: str,
    ) -> None:
        with self._lock:
            self._granted.add((owner_session, area, preview_session))

    def revoke(
        self,
        *,
        owner_session: str,
        area: PreviewArea,
        preview_session: str,
    ) -> None:
        with self._lock:
            self._granted.discard((owner_session, area, preview_session))

    def granted(
        self,
        *,
        owner_session: str,
        area: PreviewArea,
        preview_session: str,
    ) -> bool:
        with self._lock:
            return (owner_session, area, preview_session) in self._granted


def _header(scope: Scope, name: str) -> str:
    encoded = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key.lower() == encoded:
            return value.decode("latin-1")
    return ""


def blocks_application_request(scope: Scope) -> bool:
    """True when framed content is trying to pull a Proxima route.

    Preview documents and app previews are same-site with Proxima (same host,
    another port) or opaque-origin sandboxes. Browsers label their requests
    `Sec-Fetch-Site: same-site` / `cross-site`; only a top-level document
    navigation is a legitimate reason for such a request to reach Proxima.
    Sec-Fetch-* headers are forbidden header names, so page content cannot
    forge them.
    """
    if _header(scope, "origin").strip().lower() == "null":
        return True
    site = _header(scope, "sec-fetch-site").strip().lower()
    destination = _header(scope, "sec-fetch-dest").strip().lower()
    return site in {"same-site", "cross-site"} and destination != "document"


class PreviewIsolationMiddleware:
    """Keeps previewed content and Proxima apart, in both directions."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        apps_domain: str | None = None,
    ) -> None:
        self.app = app
        self.apps_domain = (apps_domain or "").strip().lower() or None

    def _is_app_preview_host(self, host: str) -> bool:
        hostname = host.split(":", 1)[0].lower().rstrip(".")
        suffixes = [".localhost", ".testserver"]
        if self.apps_domain:
            suffixes.append(f".{self.apps_domain}")
        for suffix in suffixes:
            if hostname.endswith(suffix):
                label = hostname[: -len(suffix)]
                return bool(label) and "." not in label and label.startswith(
                    "preview-"
                )
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            # App preview subdomains are proxied by PreviewProxyMiddleware and
            # legitimately serve subresources of their own.
            if not self._is_app_preview_host(_header(scope, "host")):
                if blocks_application_request(scope):
                    await self._reject(scope, send)
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
                        # Anything that did not declare its own framing policy
                        # (the SPA itself, generated HTML) is never frameable.
                        headers["Content-Security-Policy"] = (
                            f"{policy}; frame-ancestors 'none'"
                            if policy
                            else "frame-ancestors 'none'"
                        )
                        headers["X-Frame-Options"] = "DENY"
                    headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, guarded_send)

    @staticmethod
    async def _reject(scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = Response(
            "preview content cannot access Proxima",
            status_code=403,
            media_type="text/plain",
        )

        async def empty_receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        await response(scope, empty_receive, send)

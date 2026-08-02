"""Project-scoped Moodboard storage and lightweight link previews.

Moodboard items live under ``<artifacts>/moodboard`` in the project so they
move with the project and remain inspectable outside Proxima. The base follows
the per-project layout map (prune C4/#138): the project's mapped artifacts
location - ``ops/artifacts`` for a detected Ops layout, a root-level
``artifacts/`` when that is where the folder really lives. Link previews cache
the page's OG image locally, which makes the gallery reliable and lets design
runs attach the same image as vision without fetching it again.

All paths this module stores and returns are **container-root-relative** -
paths mean exactly what they say on disk (decision #121). Items written under
the pre-#138 reroute era stored Ops-relative strings behind reserved first
segments; :func:`_frozen_item_path` upgrades them to their historical meaning
at the read boundary (reads never rewrite the store; the next explicit owner
write persists the upgrade).
"""
from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import socket
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import fsapi

MAX_ITEMS = 500
MAX_HTML_BYTES = 750_000
MAX_IMAGE_BYTES = 8_000_000
MAX_REDIRECTS = 4
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_IMAGE_EXTENSIONS = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}
_REMOTE_IMAGE_EXTENSIONS = {mime: ext for mime, ext in _IMAGE_EXTENSIONS.items() if mime != "image/svg+xml"}


# The names the pre-#138 virtual mapping rerouted to the Ops root - a frozen
# historical list used only to upgrade reroute-era stored item paths.
_REROUTE_ERA_NAMES = frozenset(
    ("wiki", "artifacts", "reports", "exports", "scripts", "tasks", "uploads")
)


@dataclass(frozen=True)
class MoodboardStore:
    """One project's moodboard location, resolved through the layout map.

    ``artifacts_rel`` is the mapped artifacts folder and ``ops_rel`` the
    persisted Ops path, both container-root-relative (``.`` = the root).
    """

    container_root: Path
    artifacts_rel: str
    ops_rel: str

    @property
    def base_rel(self) -> str:
        return f"{self.artifacts_rel}/moodboard"

    @property
    def store_rel(self) -> str:
        return f"{self.base_rel}/items.json"

    @property
    def image_dir_rel(self) -> str:
        return f"{self.base_rel}/images"

    def resolve(self, rel: str) -> Path:
        return fsapi.resolve_in_project(self.container_root, rel)


def store_for_layout(layout: Any) -> MoodboardStore:
    """Build the store from a resolved :class:`layout_map.ProjectLayout`."""
    return MoodboardStore(
        container_root=layout.container_root,
        artifacts_rel=layout.rel_paths["artifacts"],
        ops_rel=layout.ops_rel,
    )


def _frozen_item_path(store: "MoodboardStore", path: str) -> str:
    """A reroute-era item path's historical (Ops-relative) meaning, literal.

    A first segment from the reserved list resolved against the Ops root
    before #138. The literal container-relative file wins whenever it exists
    (paths mean what they say on disk); only a dangling reserved-name path
    whose Ops-prefixed sibling exists is upgraded to that historical meaning.
    Paths already under the Ops prefix stay as they are (which also makes the
    upgrade idempotent), and dot-Ops projects need no upgrade.
    """
    text = str(path or "")
    ops_rel = store.ops_rel
    if not text or ops_rel in ("", "."):
        return text
    first = text.split("/", 1)[0]
    if first not in _REROUTE_ERA_NAMES:
        return text
    if text == ops_rel or text.startswith(f"{ops_rel}/"):
        return text
    prefixed = f"{ops_rel}/{text}"
    try:
        if store.resolve(text).exists():
            return text
        if store.resolve(prefixed).exists():
            return prefixed
    except (OSError, fsapi.FsError):
        return text
    return text


class UnsafeUrlError(ValueError):
    """The URL targets a network location the preview service must not access."""


class UrlResolutionError(ValueError):
    """The URL is syntactically valid but its host could not be resolved."""


class _PreviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.icon = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(k).lower(): str(v or "").strip() for k, v in attrs}
        if tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content") or ""
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag.lower() == "link":
            rel = values.get("rel", "").lower().split()
            href = values.get("href") or ""
            if href and "icon" in rel and not self.icon:
                self.icon = href

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and len(self.title) < 300:
            self.title += data


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _store_file(store: MoodboardStore) -> Path:
    return store.resolve(store.store_rel)


def _lock_for(store: MoodboardStore) -> threading.Lock:
    key = str(_store_file(store))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def read_items(store: MoodboardStore) -> list[dict[str, Any]]:
    path = _store_file(store)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else raw
    upgraded: list[dict[str, Any]] = []
    for item in (items or [])[: MAX_ITEMS * 2]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        image_path = str(item.get("imagePath") or "")
        frozen = _frozen_item_path(store, image_path)
        if frozen != image_path:
            item = {**item, "imagePath": frozen}
        upgraded.append(item)
    return upgraded[:MAX_ITEMS]


def write_items(store: MoodboardStore, items: list[dict[str, Any]]) -> None:
    path = _store_file(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items[:MAX_ITEMS]}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_item(store: MoodboardStore, item: dict[str, Any]) -> None:
    with _lock_for(store):
        items = read_items(store)
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"Moodboard is limited to {MAX_ITEMS} items.")
        items.insert(0, item)
        write_items(store, items)


def patch_item(store: MoodboardStore, item_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _lock_for(store):
        items = read_items(store)
        found: dict[str, Any] | None = None
        for index, item in enumerate(items):
            if item.get("id") == item_id:
                found = {**item, **patch, "updatedAt": now_iso()}
                items[index] = found
                break
        if found is not None:
            write_items(store, items)
        return found


def delete_item(store: MoodboardStore, item_id: str) -> dict[str, Any] | None:
    with _lock_for(store):
        items = read_items(store)
        found = next((item for item in items if item.get("id") == item_id), None)
        if found is None:
            return None
        remaining = [item for item in items if item.get("id") != item_id]
        write_items(store, remaining)
        image_path = str(found.get("imagePath") or "")
        if image_path.startswith(f"{store.image_dir_rel}/") and not any(item.get("imagePath") == image_path for item in remaining):
            try:
                store.resolve(image_path).unlink(missing_ok=True)
            except (OSError, fsapi.FsError):
                pass
        return found


def normalize_tags(raw: Any) -> list[str]:
    source = raw if isinstance(raw, list) else str(raw or "").replace(",", " ").split()
    tags: list[str] = []
    for value in source:
        tag = str(value).strip().lower().lstrip("#")[:32]
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 12:
            break
    return tags


def validate_local_image(store: MoodboardStore, rel: str) -> Path:
    path = store.resolve(rel)
    mime = mimetypes.guess_type(path.name)[0] or ""
    if not path.is_file() or not mime.startswith("image/"):
        raise ValueError("imagePath must point to an uploaded project image.")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Moodboard images must be 8 MB or smaller.")
    return path


def _normalize_url_syntax(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("URL is required.")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Use a valid http or https URL.")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not supported.")
    host = parsed.hostname.encode("idna").decode("ascii")
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))


def _is_safe_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_public_ip(host: str | None, port: int | None) -> str:
    if not host:
        raise UnsafeUrlError("Private or local network links cannot be previewed.")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UrlResolutionError("The link host could not be resolved.") from exc
    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise UrlResolutionError("The link host could not be resolved.")
    for address in addresses:
        if not _is_safe_public_ip(ipaddress.ip_address(address)):
            raise UnsafeUrlError("Private or local network links cannot be previewed.")
    return addresses[0]


def normalize_public_url(raw: str) -> str:
    normalized = _normalize_url_syntax(raw)
    parsed = urlsplit(normalized)
    _resolve_public_ip(parsed.hostname, parsed.port)
    return normalized


def _pin_connection(parsed: Any, ip: str) -> tuple[str, str, str]:
    ip_literal = f"[{ip}]" if ipaddress.ip_address(ip).version == 6 else ip
    connect_netloc = f"{ip_literal}:{parsed.port}" if parsed.port else ip_literal
    connect_url = urlunsplit((parsed.scheme, connect_netloc, parsed.path or "/", parsed.query, ""))
    return connect_url, parsed.netloc, parsed.hostname


def _bounded_get(client: httpx.Client, url: str, *, max_bytes: int) -> tuple[bytes, str, str]:
    target = _normalize_url_syntax(url)
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(target)
        pinned_ip = _resolve_public_ip(parsed.hostname, parsed.port)
        connect_url, host_header, sni_hostname = _pin_connection(parsed, pinned_ip)
        extensions = {"sni_hostname": sni_hostname} if parsed.scheme == "https" else {}
        with client.stream("GET", connect_url, headers={"Host": host_header}, extensions=extensions) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise httpx.HTTPError("redirect response had no location")
                target = _normalize_url_syntax(urljoin(target, location))
                continue
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise httpx.HTTPError("response is too large")
            return bytes(body), response.headers.get("content-type", "").split(";")[0].lower(), target
    raise httpx.HTTPError("too many redirects")


def fetch_link_preview(raw_url: str, timeout: float = 12.0) -> dict[str, Any]:
    """Fetch HTML metadata and its OG image. Network failures are returned as data."""
    target = _normalize_url_syntax(raw_url)
    result: dict[str, Any] = {
        "url": target,
        "siteName": (urlsplit(target).hostname or "").removeprefix("www."),
        "title": "",
        "faviconUrl": "",
        "imageBytes": None,
        "imageMime": "",
        "warning": "",
    }
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Proxima moodboard-preview)"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            body, content_type, final_url = _bounded_get(client, target, max_bytes=MAX_HTML_BYTES)
            result["url"] = final_url
            result["siteName"] = (urlsplit(final_url).hostname or result["siteName"]).removeprefix("www.")
            if content_type and "html" not in content_type:
                raise httpx.HTTPError("link did not return an HTML page")
            parser = _PreviewParser()
            parser.feed(body.decode("utf-8", errors="ignore"))
            result["title"] = (
                parser.meta.get("og:title")
                or parser.meta.get("twitter:title")
                or parser.title.strip()
            )[:240]
            image_url = parser.meta.get("og:image") or parser.meta.get("twitter:image")
            icon_url = parser.icon or "/favicon.ico"
            try:
                result["faviconUrl"] = normalize_public_url(urljoin(final_url, icon_url))
            except ValueError:
                result["faviconUrl"] = ""
            if image_url:
                image_body, image_mime, _ = _bounded_get(
                    client,
                    urljoin(final_url, image_url),
                    max_bytes=MAX_IMAGE_BYTES,
                )
                if image_mime not in _REMOTE_IMAGE_EXTENSIONS:
                    raise httpx.HTTPError("preview image used an unsupported format")
                result["imageBytes"] = image_body
                result["imageMime"] = image_mime
    except UnsafeUrlError:
        raise
    except Exception as exc:  # noqa: BLE001 - previews are deliberately best-effort
        result["warning"] = f"Preview unavailable: {str(exc)[:180]}"
    return result


def cache_preview_image(store: MoodboardStore, item_id: str, data: bytes | None, mime: str) -> str | None:
    if not data or mime not in _REMOTE_IMAGE_EXTENSIONS:
        return None
    rel = f"{store.image_dir_rel}/{item_id}{_REMOTE_IMAGE_EXTENSIONS[mime]}"
    target = store.resolve(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel


def active_references(store: MoodboardStore | None) -> list[dict[str, Any]]:
    if store is None:
        return []
    return [item for item in read_items(store) if item.get("useAsReference")][:10]

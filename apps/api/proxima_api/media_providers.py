"""Shared plumbing for the media-provider family (image + video).

Image and video generation talk to the same class of endpoint: an OpenAI-shaped
gateway addressed by a **base URL without the endpoint path** - the client
appends `/images/generations`, `/videos/generations`, … itself. Everything that
is identical between the two lives here so the two provider modules cannot drift:

  - `normalize_base_url` / `endpoint` - the base-URL join semantics.
  - `probe_models_endpoint` - the Settings "Test connection" reachability probe.
  - `error_message` / `response_error_detail` - turning a failed payload or
    response into a message the owner can act on. Gateways answer a wrong base
    URL with an HTML 404 page, so that case names the base URL instead of
    dumping markup into the chat.

Why: the owner already lost time pasting a base URL that included the endpoint
path. One place decides how a URL is joined and how a failure reads, for every
media provider.
"""
from __future__ import annotations

from typing import Any

import httpx

# A wrong base URL (one that already contains a path, or points at a web app
# rather than an API root) answers with a rendered page, not JSON.
_HTML_MARKERS = ("<!doctype html", "<html")

_BASE_URL_HINT = (
    "Check the base URL: it must be the API root (e.g. https://api.openai.com/v1) "
    "with no endpoint path after it."
)


def normalize_base_url(base_url: str | None, default: str = "") -> str:
    """The base URL with trailing slashes removed, falling back to `default`."""
    return (base_url or default or "").strip().rstrip("/")


def endpoint(base_url: str | None, path: str, default: str = "") -> str:
    """Join a provider base URL with an endpoint path the client owns."""
    return f"{normalize_base_url(base_url, default)}/{path.lstrip('/')}"


def _body_text(response: Any) -> str:
    text = getattr(response, "text", "") or ""
    if not text:
        raw = getattr(response, "content", b"") or b""
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
    return text


def _looks_like_html(response: Any) -> bool:
    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            content_type = str(headers.get("content-type") or "")
        except Exception:
            content_type = ""
    if "html" in content_type.lower():
        return True
    return _body_text(response).lstrip()[:64].lower().startswith(_HTML_MARKERS)


def error_message(payload: Any) -> str | None:
    """The human-readable message inside the error shapes gateways use.

    Handles `{"error": {"message": …}}` (OpenAI), `{"error": "…"}` (bare string),
    and the `detail`/`failure_reason` variants, at any nesting depth.
    """
    if isinstance(payload, str):
        return payload.strip() or None
    if not isinstance(payload, dict):
        return None
    for key in ("error", "message", "detail", "failure_reason", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        nested = error_message(value)
        if nested:
            return nested
    return None


def response_error_detail(response: Any, *, fallback: str = "") -> str:
    """A short, actionable description of a failed provider response."""
    status = getattr(response, "status_code", 0)
    if _looks_like_html(response):
        return f"the endpoint returned an HTML page, not JSON (HTTP {status}). {_BASE_URL_HINT}"
    try:
        message = error_message(response.json())
    except Exception:
        message = None
    if not message:
        message = _body_text(response).strip()[:300] or fallback
    return message or f"HTTP {status}"


def probe_models_endpoint(
    base_url: str | None,
    key: str | None,
    *,
    default_base_url: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Settings "Test connection": is the endpoint reachable and the key accepted?

    A tiny `GET {base}/models` surfaces auth/base-URL mistakes before the owner
    spends credits on a real generation. Never raises.
    """
    if not key:
        return {"ok": False, "detail": "Missing API key."}
    url = endpoint(base_url, "models", default_base_url)
    try:
        with httpx.Client(timeout=timeout) as cx:
            r = cx.get(url, headers={"Authorization": f"Bearer {key}"})
            if r.status_code in (401, 403):
                return {"ok": False, "detail": f"Key rejected ({r.status_code})."}
            if r.status_code >= 500:
                return {"ok": False, "detail": f"Endpoint error ({r.status_code})."}
            # A base URL that already contains the endpoint path still answers -
            # with a rendered 404 page. Reporting that as "reachable" is a false
            # green on the single mistake owners actually make, so require the
            # endpoint to answer as an API before calling it good.
            try:
                payload = r.json()
            except Exception:
                return {"ok": False, "detail": response_error_detail(r, fallback="the endpoint did not answer with JSON")}
            n = 0
            if isinstance(payload, dict):
                n = len(payload.get("data") or [])
            elif isinstance(payload, list):
                n = len(payload)
            return {"ok": True, "detail": f"Endpoint reachable — {n} models listed." if n else "Endpoint reachable."}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Network error: {exc}"}
    except Exception as exc:
        return {"ok": False, "detail": f"Unexpected: {exc}"}

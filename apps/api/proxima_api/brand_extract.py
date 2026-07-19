"""Best-effort brand-signal extraction for the Design Studio "Generate design.md"
feature. Given any reference URL (a brand site, a Pinterest/Instagram page, anything),
pull the cheap-but-real signal out of its HTML — title, description, the colours and
fonts declared in its CSS, its og:image, a text snippet — and hand it to the design
agent, which synthesises a `design.md` brand guideline from these digests plus any
uploaded reference images (vision) and the owner's notes.

JS-rendered pages (Instagram, some SPAs) yield thin HTML; that's expected and called
out to the user — uploading a screenshot as a reference image is the reliable path for
those. Nothing here raises: a URL that can't be fetched becomes a digest with ok=False.
"""
from __future__ import annotations

import re
from collections import Counter
from html import unescape
from typing import Any

import httpx

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_FONT = re.compile(r"font-family\s*:\s*([^;{}]+)", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC = re.compile(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)", re.I)
_OG_IMAGE = re.compile(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)", re.I)
_TAGS = re.compile(r"<(script|style|head|nav|footer|svg)[^>]*>.*?</\1>", re.I | re.S)
_ANYTAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_GENERIC_FONTS = {"sans-serif", "serif", "monospace", "system-ui", "inherit", "initial", "unset", "-apple-system", "blinkmacsystemfont", "cursive"}


def _top(items: list[str], n: int) -> list[str]:
    """Most-common values, order preserved, de-duplicated case-insensitively."""
    seen: dict[str, str] = {}
    for raw in items:
        v = raw.strip()
        if not v:
            continue
        k = v.lower()
        if k not in seen:
            seen[k] = v
    ordered = [seen[k] for k, _ in Counter(x.strip().lower() for x in items if x.strip()).most_common()]
    return ordered[:n]


def _visible_text(html: str) -> str:
    stripped = _ANYTAG.sub(" ", _TAGS.sub(" ", html))
    return _WS.sub(" ", unescape(stripped)).strip()


def fetch_url_digest(url: str, timeout: float = 15.0) -> dict[str, Any]:
    """Extract brand signal from a URL. Never raises — failures return ok=False."""
    raw = (url or "").strip()
    if not raw:
        return {"url": url, "ok": False, "error": "empty url"}
    target = raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}"
    out: dict[str, Any] = {"url": raw, "ok": False}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; Proxima brand-guide)"}) as cx:
            r = cx.get(target)
        html = r.text[:500_000]
        colors = _top(_HEX.findall(html), 8)
        fonts = [f for f in _top([f.split(",")[0].strip().strip("\"'") for f in _FONT.findall(html)], 8) if f.lower() not in _GENERIC_FONTS][:6]
        tm = _TITLE.search(html)
        dm = _META_DESC.search(html)
        om = _OG_IMAGE.search(html)
        out.update(
            ok=True,
            status=r.status_code,
            title=(unescape(tm.group(1)).strip()[:200] if tm else ""),
            description=(unescape(dm.group(1)).strip()[:400] if dm else ""),
            colors=colors,
            fonts=fonts,
            ogImage=(om.group(1).strip() if om else ""),
            text=_visible_text(html)[:1200],
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, any failure is a thin digest
        out["error"] = str(exc)[:200]
    return out


def digest_to_markdown(d: dict[str, Any]) -> str:
    """Render one URL digest as a compact markdown block for the agent prompt."""
    if not d.get("ok"):
        return f"- **{d.get('url')}** — could not fetch ({d.get('error', 'unknown error')}). If this is a visual reference (Pinterest/Instagram), ask the owner to upload a screenshot instead."
    parts = [f"- **{d['url']}**"]
    if d.get("title"):
        parts.append(f"  - Title: {d['title']}")
    if d.get("description"):
        parts.append(f"  - Description: {d['description']}")
    if d.get("colors"):
        parts.append(f"  - Colours found in CSS: {', '.join(d['colors'])}")
    if d.get("fonts"):
        parts.append(f"  - Fonts found: {', '.join(d['fonts'])}")
    if d.get("ogImage"):
        parts.append(f"  - Social image: {d['ogImage']}")
    if d.get("text"):
        parts.append(f"  - Copy sample: {d['text'][:400]}")
    return "\n".join(parts)

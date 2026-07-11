"""Video-generation provider registry.

This is intentionally settings/status first: Proxima's chat stays ACP-driven,
while media generation can pick a concrete backend independently.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import higgsfield, image_providers


class VideoProviderError(RuntimeError):
    """A video provider request failed. Message is safe to show the user."""


@dataclass(frozen=True)
class VideoResult:
    filename: str
    content: bytes | None = None
    url: str | None = None
    content_type: str = "video/mp4"
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class VideoProvider:
    id: str
    display_name: str
    kind: str  # "oauth" | "higgsfield"
    requires_key: bool = False
    note: str = ""
    capabilities: dict[str, bool] | None = None


PROVIDERS: dict[str, VideoProvider] = {
    "xai-oauth": VideoProvider(
        id="xai-oauth",
        display_name="xAI / Grok Imagine OAuth",
        kind="oauth",
        note="Uses Hermes xAI OAuth tokens (`hermes auth add xai-oauth`) — no API key stored in Proxima.",
        capabilities={"textToVideo": True, "imageToVideo": True, "editVideo": True, "extendVideo": True},
    ),
    "higgsfield": VideoProvider(
        id="higgsfield",
        display_name="Higgsfield CLI",
        kind="higgsfield",
        note="Uses the local Higgsfield CLI login/workspace and Proxima credit policy.",
        capabilities={"textToVideo": True, "imageToVideo": True, "editVideo": False, "extendVideo": False},
    ),
}

DEFAULT_PROVIDER = "xai-oauth"
VIDEO_PROVIDER_IDS = ("xai-oauth", "higgsfield")
DEFAULT_MODEL = "grok-imagine-video"  # accepts text-to-video; -1.5 is image-to-video only


def provider_list() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "displayName": p.display_name,
            "kind": p.kind,
            "requiresKey": p.requires_key,
            "note": p.note,
            "capabilities": p.capabilities or {},
        }
        for p in (PROVIDERS[pid] for pid in VIDEO_PROVIDER_IDS)
    ]


def get_provider(provider_id: str | None) -> VideoProvider:
    if provider_id and provider_id in PROVIDERS:
        return PROVIDERS[provider_id]
    return PROVIDERS[DEFAULT_PROVIDER]


def test_connection(provider_id: str) -> dict[str, Any]:
    provider = get_provider(provider_id)
    if provider.id == "xai-oauth":
        ready = image_providers.xai_oauth_ready()
        return {"ok": bool(ready.get("ready")), **ready}
    if provider.id == "higgsfield":
        status = higgsfield.status()
        return {"ok": bool(status.get("ready")), "detail": status.get("detail") or "Higgsfield status unknown.", "higgsfield": status}
    return {"ok": False, "detail": f"Unknown video provider: {provider_id}"}


def _extract_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else None
    if isinstance(value, dict):
        for key in ("video", "video_url", "videoUrl", "url", "download_url", "downloadUrl", "result_url", "resultUrl"):
            found = _extract_url(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _extract_url(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _extract_url(child)
            if found:
                return found
    return None


def _download(url: str, *, timeout: float) -> bytes:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as cx:
            res = cx.get(url)
            if res.status_code >= 400:
                raise VideoProviderError(f"Video result download failed ({res.status_code}).")
            return res.content
    except httpx.HTTPError as exc:
        raise VideoProviderError(f"Video result download failed: {exc}") from exc


def generate(
    provider_id: str,
    *,
    prompt: str,
    model: str | None = None,
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    timeout: float = 900.0,
) -> VideoResult:
    provider = get_provider(provider_id)
    if provider.id == "higgsfield":
        try:
            content = higgsfield.generate_video(
                prompt=prompt,
                model=model,
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                timeout=timeout,
            )
        except higgsfield.HiggsfieldError as exc:
            raise VideoProviderError(str(exc)) from exc
        return VideoResult(filename="video.mp4", content=content)
    if provider.id == "xai-oauth":
        token = image_providers._read_hermes_oauth_token("xai-oauth")  # local auth store; never returned to frontend
        if not token:
            raise VideoProviderError(image_providers.xai_oauth_ready()["detail"])
        chosen_model = model or DEFAULT_MODEL
        if chosen_model == "grok-imagine-video-1.5":
            # 1.5 rejects prompt-only requests ("Text-to-video is not supported for
            # this model") — it animates a provided image. Our pipeline sends text
            # only, so route those to the text-capable model instead of failing.
            chosen_model = DEFAULT_MODEL
        body: dict[str, Any] = {"model": chosen_model, "prompt": prompt}
        if duration:
            body["duration"] = duration
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if resolution:
            body["resolution"] = resolution
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        # xAI video generation is asynchronous (API change observed 2026-07):
        # POST /v1/videos/generations returns a request_id, then GET /v1/videos/{id}
        # is polled until status is done | failed | expired, and the finished file
        # lives at payload.video.url.
        try:
            with httpx.Client(timeout=min(timeout, 120.0), follow_redirects=True) as cx:
                res = cx.post("https://api.x.ai/v1/videos/generations", headers=headers, content=json.dumps(body))
                if res.status_code >= 400:
                    raise VideoProviderError(f"xAI video request failed ({res.status_code}): {res.text[:300]}")
                try:
                    started = res.json()
                except ValueError as exc:
                    raise VideoProviderError("xAI video request returned non-JSON response.") from exc
                request_id = started.get("request_id") or started.get("id")
                if not request_id:
                    raise VideoProviderError("xAI video request returned no request id.")
                deadline = time.monotonic() + timeout
                payload: dict[str, Any] = {}
                while True:
                    if time.monotonic() >= deadline:
                        raise VideoProviderError(f"xAI video generation timed out after {int(timeout)}s.")
                    time.sleep(5)
                    poll = cx.get(f"https://api.x.ai/v1/videos/{request_id}", headers=headers)
                    if poll.status_code >= 400:
                        raise VideoProviderError(f"xAI video poll failed ({poll.status_code}): {poll.text[:300]}")
                    try:
                        payload = poll.json()
                    except ValueError as exc:
                        raise VideoProviderError("xAI video poll returned non-JSON response.") from exc
                    status = str(payload.get("status") or "").lower()
                    if status == "done":
                        break
                    if status in ("failed", "expired"):
                        raise VideoProviderError(f"xAI video generation {status}: {str(payload)[:300]}")
        except httpx.HTTPError as exc:
            raise VideoProviderError(f"xAI video request failed: {exc}") from exc
        url = _extract_url(payload)
        if not url:
            raise VideoProviderError("xAI video generation returned no video URL.")
        return VideoResult(filename="video.mp4", content=_download(url, timeout=timeout), url=url, raw=payload)
    raise VideoProviderError(f"Unknown video provider: {provider_id}")

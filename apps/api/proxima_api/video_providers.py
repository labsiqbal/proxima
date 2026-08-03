"""Video-generation provider abstraction - the sibling of `image_providers`.

One user-facing provider kind today, chosen in Settings → Video generation:

  - openai-compatible  Any gateway that speaks an OpenAI-shaped video surface.
                       The owner pastes an endpoint base URL + key + model name.

Base-URL semantics are identical to image generation: the owner stores the API
**root** (e.g. `https://api.linc.id/v1`) and this client appends the endpoint
path itself.

Two real-world job contracts are supported, probed in order, because gateways
disagree about where a video job is submitted:

  1. `POST {base}/videos/generations` → `{"request_id": "…"}`
     (the owner's api.linc.id gateway; verified live)
  2. `POST {base}/videos` → `{"id": "video_…", "status": "queued"}`
     (the OpenAI Sora contract; used when (1) answers 404)

Both then poll `GET {base}/videos/{job_id}` until the job reports a terminal
status. The finished job either carries a video URL (gateway) or nothing, in
which case the bytes come from `GET {base}/videos/{job_id}/content` (Sora). A
gateway that answers the submit synchronously - with a URL or inline base64 -
short-circuits the polling entirely.

Every failure raises `VideoProviderError` with a message safe to show the owner.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from . import media_providers


class VideoProviderError(RuntimeError):
    """A provider request failed. Message is safe to show the user."""


@dataclass(frozen=True)
class VideoProvider:
    id: str
    display_name: str
    requires_key: bool
    kind: str  # "http"
    default_base_url: str = ""
    note: str = ""
    capabilities: dict[str, bool] | None = None


PROVIDERS: dict[str, VideoProvider] = {
    "openai-compatible": VideoProvider(
        id="openai-compatible",
        display_name="OpenAI-compatible endpoint",
        requires_key=True,
        kind="http",
        default_base_url="https://api.openai.com/v1",
        note=(
            "Any endpoint that speaks a /v1/videos job surface (OpenAI Sora, api.linc.id, "
            "FAL, …). Paste the API root - e.g. https://api.linc.id/v1 - with no path after "
            "it; Proxima appends /videos/generations and polls the job itself."
        ),
        capabilities={"textToVideo": True, "imageToVideo": False},
    ),
}

DEFAULT_PROVIDER = "openai-compatible"
VIDEO_PROVIDER_IDS = ("openai-compatible",)

# Job polling. A video job runs far longer than an image request, so the default
# ceiling sits well inside the chat media-run budget (MEDIA_RUN_MAX_SECONDS).
DEFAULT_TIMEOUT = 900.0
DEFAULT_POLL_INTERVAL = 5.0

_PENDING = {"pending", "queued", "in_progress", "in-progress", "processing", "running", "started", "waiting"}
_SUCCESS = {"done", "completed", "complete", "succeeded", "success", "ready", "finished"}
_FAILURE = {"failed", "failure", "error", "errored", "cancelled", "canceled", "rejected", "expired"}

_EXTENSIONS = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


@dataclass(frozen=True)
class VideoResult:
    """A generated clip plus what the caller needs to name and describe it."""

    data: bytes
    extension: str = ".mp4"
    content_type: str = "video/mp4"
    duration_seconds: float | None = None
    source_url: str | None = None


def provider_list() -> list[dict[str, Any]]:
    """Provider metadata for the Settings UI (no secrets)."""
    return [
        {
            "id": p.id,
            "displayName": p.display_name,
            "requiresKey": p.requires_key,
            "kind": p.kind,
            "note": p.note,
            "defaultBaseUrl": p.default_base_url,
            "capabilities": p.capabilities or {},
        }
        for p in (PROVIDERS[pid] for pid in VIDEO_PROVIDER_IDS)
    ]


def get_provider(provider_id: str | None) -> VideoProvider:
    if provider_id and provider_id in PROVIDERS:
        return PROVIDERS[provider_id]
    return PROVIDERS[DEFAULT_PROVIDER]


def test_connection(provider_id: str, key: str | None, *, base_url: str | None = None) -> dict[str, Any]:
    """Verify the endpoint is reachable and the key is accepted. Never raises."""
    provider = get_provider(provider_id)
    return media_providers.probe_models_endpoint(
        base_url, key, default_base_url=provider.default_base_url
    )


# ── generation ─────────────────────────────────────────────────────────────

def generate(
    provider_id: str,
    key: str | None,
    *,
    prompt: str,
    model: str | None = None,
    seconds: int | float | str | None = None,
    size: str | None = None,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> VideoResult:
    """Generate a clip from a text prompt. Returns the raw video bytes."""
    provider = get_provider(provider_id)
    if not key:
        raise VideoProviderError("This provider requires an API key (set it in Settings → Video generation).")
    base = media_providers.normalize_base_url(base_url, provider.default_base_url)
    if not base:
        raise VideoProviderError("This provider requires an endpoint base URL (set it in Settings → Video generation).")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {"prompt": prompt}
    if model:
        body["model"] = model
    if seconds is not None:
        # Sora types `seconds` as a string; gateways accept either.
        body["seconds"] = str(seconds)
    if size:
        body["size"] = size

    deadline = time.monotonic() + max(timeout, 0.0)
    request_timeout = httpx.Timeout(60.0, connect=30.0, read=120.0, write=60.0, pool=30.0)
    try:
        with httpx.Client(timeout=request_timeout) as cx:
            job = _submit(cx, base, headers, body)
            # A gateway may answer the submit with the finished clip.
            ready = _video_ref(job)
            if ready.url or ready.b64:
                return _materialize(cx, base, headers, ready)
            job_id = _job_id(job)
            if not job_id:
                raise VideoProviderError(
                    f"The endpoint accepted the request but returned no video and no job id: {_short(job)}"
                )
            return _poll(cx, base, headers, job_id, deadline=deadline, poll_interval=poll_interval)
    except httpx.HTTPError as exc:
        raise VideoProviderError(f"Endpoint request failed: {exc}") from exc


def _submit(cx: httpx.Client, base: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    """POST the job, preferring `/videos/generations` and falling back to Sora's
    `/videos` when the gateway does not know the first path."""
    primary = cx.post(f"{base}/videos/generations", headers=headers, json=body)
    if primary.status_code == 404:
        fallback = cx.post(f"{base}/videos", headers=headers, json=body)
        if fallback.status_code >= 400:
            raise VideoProviderError(
                f"Video request failed ({fallback.status_code}): {media_providers.response_error_detail(fallback)}"
            )
        return _json(fallback)
    if primary.status_code >= 400:
        raise VideoProviderError(
            f"Video request failed ({primary.status_code}): {media_providers.response_error_detail(primary)}"
        )
    return _json(primary)


def _poll(
    cx: httpx.Client,
    base: str,
    headers: dict[str, str],
    job_id: str,
    *,
    deadline: float,
    poll_interval: float,
) -> VideoResult:
    url = f"{base}/videos/{job_id}"
    while True:
        response = cx.get(url, headers=headers)
        if response.status_code >= 400:
            raise VideoProviderError(
                f"Video job {job_id} could not be read ({response.status_code}): "
                f"{media_providers.response_error_detail(response)}"
            )
        payload = _json(response)
        status = str((payload or {}).get("status") or "").strip().lower()
        if status in _FAILURE:
            detail = media_providers.error_message(payload) or "no reason given"
            raise VideoProviderError(f"The video job failed: {detail}")
        if status in _SUCCESS or (not status and _video_ref(payload).url):
            ref = _video_ref(payload)
            if not (ref.url or ref.b64):
                # Sora keeps the bytes behind a separate content endpoint.
                ref = _VideoRef(url=f"{base}/videos/{job_id}/content", duration=ref.duration)
            return _materialize(cx, base, headers, ref)
        if status and status not in _PENDING:
            raise VideoProviderError(f"The video job reported an unknown status: {status!r}")
        if time.monotonic() >= deadline:
            raise VideoProviderError(
                f"The video job is still pending after the timeout ({_progress(payload)}). "
                "Try a shorter clip, or raise the timeout."
            )
        if poll_interval > 0:
            time.sleep(poll_interval)


@dataclass(frozen=True)
class _VideoRef:
    url: str | None = None
    b64: str | None = None
    duration: float | None = None


def _video_ref(payload: Any) -> _VideoRef:
    """Find the clip in whatever shape the gateway answered with."""
    if not isinstance(payload, dict):
        return _VideoRef()
    duration = _duration(payload)
    video = payload.get("video")
    if isinstance(video, dict):
        url = _first_str(video, ("url", "video_url", "downloadUrl", "download_url"))
        if url:
            return _VideoRef(url=url, duration=_duration(video) or duration)
    if isinstance(video, str) and video.startswith(("http://", "https://")):
        return _VideoRef(url=video, duration=duration)
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        first = data[0]
        url = _first_str(first, ("url", "video_url", "downloadUrl", "download_url"))
        b64 = _first_str(first, ("b64_json", "b64", "video_base64"))
        if url or b64:
            return _VideoRef(url=url, b64=b64, duration=_duration(first) or duration)
    url = _first_str(payload, ("url", "video_url", "videoUrl", "downloadUrl", "download_url", "output_url"))
    b64 = _first_str(payload, ("b64_json", "video_base64"))
    if url or b64:
        return _VideoRef(url=url, b64=b64, duration=duration)
    output = payload.get("output")
    if isinstance(output, str) and output.startswith(("http://", "https://")):
        return _VideoRef(url=output, duration=duration)
    if isinstance(output, list) and output and isinstance(output[0], str):
        return _VideoRef(url=output[0], duration=duration)
    return _VideoRef(duration=duration)


def _materialize(cx: httpx.Client, base: str, headers: dict[str, str], ref: _VideoRef) -> VideoResult:
    if ref.b64:
        try:
            return VideoResult(data=base64.b64decode(ref.b64), duration_seconds=ref.duration)
        except Exception as exc:
            raise VideoProviderError(f"The endpoint returned invalid inline video data: {exc}") from exc
    if not ref.url:
        raise VideoProviderError("The video job finished but carried no video URL or data.")
    # The API key travels only to the provider's own host, never to a CDN link.
    download_headers = {"Authorization": headers["Authorization"]} if _same_host(ref.url, base) else {}
    response = cx.get(ref.url, headers=download_headers)
    if response.status_code >= 400:
        raise VideoProviderError(
            f"Downloading the generated video failed ({response.status_code}): "
            f"{media_providers.response_error_detail(response)}"
        )
    data = response.content or b""
    if not data:
        raise VideoProviderError("The generated video download was empty.")
    content_type = _content_type(response) or _guess_content_type(ref.url)
    return VideoResult(
        data=data,
        extension=_EXTENSIONS.get(content_type, _url_extension(ref.url)),
        content_type=content_type,
        duration_seconds=ref.duration,
        source_url=ref.url,
    )


# ── small helpers ──────────────────────────────────────────────────────────

def _json(response: Any) -> Any:
    try:
        return response.json()
    except Exception as exc:
        raise VideoProviderError(
            f"The endpoint returned a non-JSON response: {media_providers.response_error_detail(response)}"
        ) from exc


def _job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("request_id", "id", "job_id", "task_id", "requestId", "jobId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _duration(payload: dict[str, Any]) -> float | None:
    for key in ("duration", "seconds", "duration_seconds", "durationSeconds"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _progress(payload: Any) -> str:
    value = (payload or {}).get("progress") if isinstance(payload, dict) else None
    if isinstance(value, (int, float)):
        return f"{int(value)}%"
    status = (payload or {}).get("status") if isinstance(payload, dict) else None
    return str(status or "no progress reported")


def _content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get("content-type") or "").split(";")[0].strip().lower()
    except Exception:
        return ""


def _guess_content_type(url: str) -> str:
    ext = _url_extension(url)
    for mime, suffix in _EXTENSIONS.items():
        if suffix == ext:
            return mime
    return "video/mp4"


def _url_extension(url: str) -> str:
    path = urlparse(url).path
    for suffix in (".mp4", ".webm", ".mov", ".m4v"):
        if path.lower().endswith(suffix):
            return suffix
    return ".mp4"


def _same_host(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:
        return False


def _short(payload: Any) -> str:
    return str(payload)[:200]

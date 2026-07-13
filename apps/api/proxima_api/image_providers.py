"""Image-generation provider abstraction for the Design Studio.

Two user-facing provider kinds, chosen in Settings:

  - codex            Use the Codex CLI's ChatGPT OAuth token directly against the
                     Codex Responses image_generation surface (no API key).
  - openai-compatible  A generic OpenAI-shaped /v1/images/generations endpoint.
                     The user pastes an endpoint URL + key + model name; works for
                     OpenAI, FAL, 9router, or any gateway that speaks that surface.

Higgsfield remains available to the app as a local CLI integration, but it is not
the default image path because CLI/MCP image jobs are credit-based. Video
generation can still use Higgsfield explicitly. Requests use httpx (already a
dependency). Every failure raises ImageProviderError with a message safe to show
the user.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import higgsfield


class ImageProviderError(RuntimeError):
    """A provider request failed. Message is safe to show the user."""


@dataclass(frozen=True)
class ImageProvider:
    id: str
    display_name: str
    requires_key: bool
    kind: str  # "auto" | "codex" | "oauth" | "higgsfield" | "http"
    default_base_url: str = ""
    note: str = ""
    capabilities: dict[str, bool] | None = None


PROVIDERS: dict[str, ImageProvider] = {
    "auto": ImageProvider(
        id="auto",
        display_name="Auto (safe)",
        requires_key=False,
        kind="auto",
        note="Uses zero-credit Higgsfield first for text-to-image, then falls back to Codex. Image edits require zero-credit Higgsfield.",
    ),
    "codex": ImageProvider(
        id="codex",
        display_name="Codex / ChatGPT auth",
        requires_key=False,
        kind="codex",
        note="Uses your `codex login` token directly — no API key needed. Supports text-to-image and, when you attach a source/reference image, image edits.",
        capabilities={"textToImage": True, "imageEdit": True, "referenceImages": True},
    ),
    "xai-oauth": ImageProvider(
        id="xai-oauth",
        display_name="xAI / SuperGrok OAuth",
        requires_key=False,
        kind="oauth",
        default_base_url="https://api.x.ai/v1",
        note="Uses Hermes xAI OAuth tokens (`hermes auth add xai-oauth`) — no API key stored in Proxima.",
        # referenceImages stays False until _gen_http actually sends more than the
        # single edit image — advertising it made consumers silently drop references.
        capabilities={"textToImage": True, "imageEdit": True, "referenceImages": False},
    ),
    "openai-compatible": ImageProvider(
        id="openai-compatible",
        display_name="OpenAI-compatible endpoint",
        requires_key=True,
        kind="http",
        default_base_url="https://api.openai.com/v1",
        note="Any endpoint that speaks /v1/images/generations (OpenAI, FAL, 9router, …). Paste your endpoint + key + model.",
        capabilities={"textToImage": True, "imageEdit": True, "referenceImages": False},
    ),
    "higgsfield": ImageProvider(
        id="higgsfield",
        display_name="Higgsfield zero-credit only",
        requires_key=False,
        kind="higgsfield",
        note="Uses the local Higgsfield CLI login and blocks image requests unless the estimated cost is zero credits.",
        capabilities={"textToImage": True, "imageEdit": True, "referenceImages": False},
    ),
}

DEFAULT_PROVIDER = "codex"
IMAGE_PROVIDER_IDS = ("codex", "xai-oauth", "higgsfield", "openai-compatible")


def provider_list() -> list[dict[str, Any]]:
    """Provider metadata for the Settings UI (no secrets)."""
    return [
        {"id": p.id, "displayName": p.display_name, "requiresKey": p.requires_key, "kind": p.kind, "note": p.note, "defaultBaseUrl": p.default_base_url, "capabilities": p.capabilities or {}}
        for p in (PROVIDERS[pid] for pid in IMAGE_PROVIDER_IDS)
    ]


def get_provider(provider_id: str | None) -> ImageProvider:
    if provider_id and provider_id in PROVIDERS:
        return PROVIDERS[provider_id]
    return PROVIDERS[DEFAULT_PROVIDER]


# ── Codex auto-detect (login-based, no key) ────────────────────────────────

def codex_binary(path_env: str | None = None) -> str | None:
    """Resolve the codex CLI on PATH (None if not installed)."""
    base = path_env or os.environ.get("PATH", "")
    for extra in (os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.codex/bin")):
        if extra and extra not in base.split(os.pathsep):
            base = base + os.pathsep + extra
    return shutil.which("codex", path=base) if base else None


def codex_ready(binary: str | None = None, path_env: str | None = None) -> dict[str, Any]:
    """Is codex installed AND logged in? Runs `codex login status` and looks for
    a logged-in signal. Returns {ready, detail}. Never raises."""
    resolved = binary or codex_binary(path_env)
    if not resolved:
        return {"ready": False, "detail": "Codex CLI not found on PATH. Install it and run `codex login`."}
    try:
        r = subprocess.run([resolved, "login", "status"], capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return {"ready": False, "detail": f"Could not check codex login: {exc}"}
    out = ((r.stdout or "") + " " + (r.stderr or "")).lower()
    # "logged in" / "authenticated" → good. "not logged in" / "run login" → not.
    if "not logged in" in out or "run `codex login`" in out or "log in" in out and "logged in" not in out:
        return {"ready": False, "detail": "Codex is not logged in. Run `codex login` (ChatGPT) and retry."}
    if "logged in" in out or "authenticated" in out or r.returncode == 0:
        return {"ready": True, "detail": "Codex is logged in.", "binary": resolved}
    return {"ready": False, "detail": f"codex login status unclear (rc={r.returncode}): {out.strip()[:120]}"}


# ── Hermes OAuth-backed providers ──────────────────────────────────────────

_HERMES_AUTH_PATH = Path.home() / ".hermes" / "auth.json"


def _read_hermes_oauth_token(provider: str) -> str | None:
    try:
        data = json.loads(_HERMES_AUTH_PATH.read_text())
    except Exception:
        return None
    candidates: list[Any] = []
    if isinstance(data, dict):
        providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
        state = providers.get(provider) if isinstance(providers, dict) else None
        if isinstance(state, dict) and isinstance(state.get("tokens"), dict):
            candidates.append(state["tokens"].get("access_token"))
        pool_root = data.get("credential_pool") if isinstance(data.get("credential_pool"), dict) else {}
        pool = pool_root.get(provider) if isinstance(pool_root, dict) else None
        if isinstance(pool, list):
            for entry in pool:
                if isinstance(entry, dict):
                    tokens = entry.get("tokens") if isinstance(entry.get("tokens"), dict) else entry
                    candidates.append(tokens.get("access_token") if isinstance(tokens, dict) else None)
    for token in candidates:
        if not isinstance(token, str) or not token.strip():
            continue
        exp = _jwt_payload(token).get("exp")
        if isinstance(exp, (int, float)) and time.time() > exp:
            continue
        return token.strip()
    return None


def xai_oauth_ready() -> dict[str, Any]:
    if not _HERMES_AUTH_PATH.exists():
        return {"ready": False, "detail": "Hermes auth store not found. Run `hermes auth add xai-oauth`."}
    if _read_hermes_oauth_token("xai-oauth"):
        return {"ready": True, "detail": "xAI OAuth is available from Hermes auth."}
    return {"ready": False, "detail": "xAI OAuth token not found or expired. Run `hermes auth add xai-oauth`."}


# ── generate ───────────────────────────────────────────────────────────────

def generate(
    provider_id: str,
    key: str | None,
    *,
    prompt: str,
    model: str | None = None,
    size: str | None = None,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    extra_images: list[tuple[bytes, str | None]] | None = None,
    base_url: str | None = None,
    timeout: float = 300.0,
) -> bytes:
    """Generate (text→image) or edit (image+prompt→image). Returns raw PNG bytes.

    For codex auth, the ChatGPT/Codex Responses image_generation surface returns
    base64 image data directly. For http providers a standard generation/edit
    request is made and the bytes returned directly.
    """
    provider = get_provider(provider_id)
    if provider.kind == "auto":
        try:
            return higgsfield.generate_image(
                prompt=prompt,
                model=model,
                size=size,
                image_bytes=image_bytes,
                image_mime=image_mime,
                timeout=timeout,
                zero_credit_only=True,
            )
        except higgsfield.HiggsfieldError as exc:
            if image_bytes is not None:
                raise ImageProviderError(f"Auto image edit requires zero-credit Higgsfield: {exc}") from exc
            try:
                return _gen_codex(prompt=prompt, size=size, image_bytes=None, timeout=timeout)
            except ImageProviderError as codex_exc:
                raise ImageProviderError(f"Higgsfield was not available ({exc}); Codex fallback also failed: {codex_exc}") from codex_exc
    if provider.kind == "codex":
        return _gen_codex(prompt=prompt, size=size, image_bytes=image_bytes, image_mime=image_mime, extra_images=extra_images, timeout=timeout)
    if provider.kind == "oauth" and provider.id == "xai-oauth":
        token = _read_hermes_oauth_token("xai-oauth")
        if not token:
            raise ImageProviderError(xai_oauth_ready()["detail"])
        return _gen_http(
            provider, token,
            prompt=prompt, model=model, size=size, image_bytes=image_bytes, image_mime=image_mime,
            base_url=(base_url or provider.default_base_url).rstrip("/"), timeout=timeout,
        )
    if provider.kind == "higgsfield":
        try:
            return higgsfield.generate_image(
                prompt=prompt,
                model=model,
                size=size,
                image_bytes=image_bytes,
                image_mime=image_mime,
                timeout=timeout,
                zero_credit_only=True,
            )
        except higgsfield.HiggsfieldError as exc:
            raise ImageProviderError(str(exc)) from exc
    return _gen_http(
        provider, key,
        prompt=prompt, model=model, size=size, image_bytes=image_bytes, image_mime=image_mime,
        base_url=(base_url or provider.default_base_url).rstrip("/"), timeout=timeout,
    )


def test_connection(provider_id: str, key: str | None, *, base_url: str | None = None) -> dict[str, Any]:
    """Verify a provider. Codex → login check; http → no key needed at test time
    beyond presence (the endpoint is exercised on first generate). Never raises."""
    provider = get_provider(provider_id)
    if provider.kind == "auto":
        codex = codex_ready()
        hstatus = higgsfield.status()
        ok = bool(codex.get("ready") or hstatus.get("ready"))
        return {
            "ok": ok,
            "detail": "Auto ready." if ok else f"Codex: {codex.get('detail')}; Higgsfield: {hstatus.get('detail')}",
            "codex": codex,
            "higgsfield": hstatus,
        }
    if provider.kind == "codex":
        return codex_ready()
    if provider.kind == "oauth" and provider.id == "xai-oauth":
        ready = xai_oauth_ready()
        return {"ok": bool(ready.get("ready")), **ready}
    if provider.kind == "higgsfield":
        hstatus = higgsfield.status()
        return {"ok": bool(hstatus.get("ready")), "detail": hstatus.get("detail") or "Higgsfield status unknown.", "higgsfield": hstatus}
    if not key:
        return {"ok": False, "detail": "Missing API key."}
    base = (base_url or provider.default_base_url).rstrip("/")
    # Probe with a tiny request so we surface auth/format errors before a real
    # generation. Most gateways return 400/401 quickly; 4xx here just tells us
    # the endpoint is reachable and the key shape was accepted.
    try:
        with httpx.Client(timeout=20) as cx:
            r = cx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            if r.status_code in (401, 403):
                return {"ok": False, "detail": f"Key rejected ({r.status_code})."}
            if r.status_code >= 500:
                return {"ok": False, "detail": f"Endpoint error ({r.status_code})."}
            n = 0
            try:
                n = len((r.json().get("data") or []))
            except Exception:
                pass
            return {"ok": True, "detail": f"Endpoint reachable — {n} models listed." if n else "Endpoint reachable."}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Network error: {exc}"}
    except Exception as exc:
        return {"ok": False, "detail": f"Unexpected: {exc}"}


# ── codex generation ───────────────────────────────────────────────────────

_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_CHAT_MODEL = "gpt-5.5"
_CODEX_IMAGE_MODEL = "gpt-image-2"
_CODEX_IMAGE_QUALITY = "low"


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_codex_access_token() -> str | None:
    """Read the Codex CLI's ChatGPT OAuth access token from ~/.codex/auth.json."""
    try:
        data = json.loads(_CODEX_AUTH_PATH.read_text())
        tokens = data.get("tokens") if isinstance(data, dict) else None
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not isinstance(token, str) or not token.strip():
            return None
        exp = _jwt_payload(token).get("exp")
        if isinstance(exp, (int, float)) and time.time() > exp:
            return None
        return token.strip()
    except Exception:
        return None


def _codex_headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "codex_cli_rs/0.0.0 (Proxima)",
        "originator": "codex_cli_rs",
    }
    acct = _jwt_payload(token).get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
    if isinstance(acct, str) and acct:
        headers["ChatGPT-Account-ID"] = acct
    return headers


def _codex_size(size: str | None) -> str:
    if size in {"1024x1024", "1536x1024", "1024x1536"}:
        return size
    return "1024x1024"


def _codex_payload(prompt: str, size: str | None, image_data_urls: list[str] | None = None) -> dict[str, Any]:
    # Reference/source images ride along as input_image content parts on the user
    # message. The image_generation tool reads them from the conversation context and
    # edits/combines them, rather than generating from the prompt alone. Multiple
    # images → compose them into one.
    urls = image_data_urls or []
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for url in urls:
        content.append({"type": "input_image", "image_url": url})
    if len(urls) > 1:
        instructions = (
            "Use the image_generation tool to combine and compose the attached images "
            "according to the user's prompt, returning exactly one image."
        )
    elif urls:
        instructions = (
            "Use the image_generation tool to edit or transform the attached image "
            "according to the user's prompt, returning exactly one image."
        )
    else:
        instructions = "Use the image_generation tool to generate exactly one image for the user's prompt."
    return {
        "model": _CODEX_CHAT_MODEL,
        "store": False,
        "instructions": instructions,
        "input": [{
            "type": "message",
            "role": "user",
            "content": content,
        }],
        "tools": [{
            "type": "image_generation",
            "model": _CODEX_IMAGE_MODEL,
            "size": _codex_size(size),
            "quality": _CODEX_IMAGE_QUALITY,
            "output_format": "png",
            "background": "opaque",
            "partial_images": 1,
        }],
        "tool_choice": {
            "type": "allowed_tools",
            "mode": "required",
            "tools": [{"type": "image_generation"}],
        },
        "stream": True,
    }


def _iter_sse_json(response: httpx.Response):
    event_name: str | None = None
    data_lines: list[str] = []

    def flush():
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return None
        raw = "\n".join(data_lines).strip()
        event = event_name
        event_name = None
        data_lines = []
        if not raw or raw == "[DONE]":
            return None
        payload = json.loads(raw)
        if isinstance(payload, dict) and event and "type" not in payload:
            payload["type"] = event
        return payload

    for line in response.iter_lines():
        line = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
        if line == "":
            payload = flush()
            if payload is not None:
                yield payload
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    payload = flush()
    if payload is not None:
        yield payload


def _extract_image_b64(value: Any) -> str | None:
    found: str | None = None
    if isinstance(value, dict):
        if value.get("type") == "image_generation_call" and isinstance(value.get("result"), str):
            found = value["result"]
        if isinstance(value.get("partial_image_b64"), str):
            found = value["partial_image_b64"]
        for child in value.values():
            nested = _extract_image_b64(child)
            if nested:
                found = nested
    elif isinstance(value, list):
        for child in value:
            nested = _extract_image_b64(child)
            if nested:
                found = nested
    return found


def _gen_codex(*, prompt: str, size: str | None, image_bytes: bytes | None, timeout: float, image_mime: str | None = None, extra_images: list[tuple[bytes, str | None]] | None = None) -> bytes:
    # Source/reference images are passed to the image_generation tool as input_image
    # content parts (base64 data URLs). The ChatGPT-OAuth surface may reject image
    # input for image_generation; if so the >=400 branch below surfaces the server's
    # message so it fails loudly, not silently.
    image_data_urls: list[str] = []
    if image_bytes is not None:
        image_data_urls.append(f"data:{image_mime or 'image/png'};base64,{base64.b64encode(image_bytes).decode()}")
    for raw, mime in (extra_images or []):
        image_data_urls.append(f"data:{mime or 'image/png'};base64,{base64.b64encode(raw).decode()}")
    token = _read_codex_access_token()
    if not token:
        ready = codex_ready()
        detail = ready.get("detail") if isinstance(ready, dict) else None
        raise ImageProviderError(detail or "Codex token not found. Run `codex login` and retry.")

    timeout_cfg = httpx.Timeout(timeout, connect=30.0, read=timeout, write=30.0, pool=30.0)
    image_b64: str | None = None
    try:
        with httpx.Client(timeout=timeout_cfg, headers=_codex_headers(token)) as cx:
            deadline = time.monotonic() + timeout
            with cx.stream("POST", f"{_CODEX_BASE_URL}/responses", json=_codex_payload(prompt, size, image_data_urls)) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")[:500]
                    hint = " (the ChatGPT/Codex auth surface may not accept reference images for image_generation)" if image_data_urls else ""
                    raise ImageProviderError(f"Codex image request failed ({response.status_code}){hint}: {body}")
                for event in _iter_sse_json(response):
                    if time.monotonic() >= deadline:
                        raise ImageProviderError(f"Codex image generation timed out after {int(timeout)}s.")
                    found = _extract_image_b64(event)
                    if found:
                        image_b64 = found
    except httpx.HTTPError as exc:
        raise ImageProviderError(f"Codex image request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ImageProviderError(f"Codex image stream returned malformed JSON: {exc}") from exc

    if not image_b64:
        raise ImageProviderError("Codex response contained no image_generation result.")
    try:
        return base64.b64decode(image_b64)
    except Exception as exc:
        raise ImageProviderError(f"Codex returned invalid image data: {exc}") from exc


# ── openai-compatible HTTP generation ──────────────────────────────────────

def _gen_http(provider, key, *, prompt, model, size, image_bytes, image_mime, base_url, timeout) -> bytes:
    if not key:
        raise ImageProviderError("This provider requires an API key (set it in Settings).")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        if image_bytes is not None:
            mime = image_mime or "image/png"
            if provider.id == "xai-oauth" or "api.x.ai" in (base_url or ""):
                # xAI's /images/edits speaks JSON, not multipart (verified against the
                # live API): {"prompt", "image": {"url": <data URL or https URL>}}.
                body_edit: dict[str, Any] = {
                    "prompt": prompt,
                    "n": 1,
                    "image": {"url": f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"},
                    "response_format": "b64_json",
                }
                if model:
                    body_edit["model"] = model
                with httpx.Client(timeout=timeout) as cx:
                    r = cx.post(f"{base_url}/images/edits", headers=headers, json=body_edit)
            else:
                # OpenAI-style /images/edits (multipart). Works for gpt-image models;
                # gateways that don't support edits will 4xx and the message is surfaced.
                ext = mimetypes.guess_extension(mime) or ".png"
                files = {"image": (f"src{ext}", image_bytes, mime)}
                data: dict[str, Any] = {"prompt": prompt, "n": "1"}
                if model:
                    data["model"] = model
                if size:
                    data["size"] = size
                with httpx.Client(timeout=timeout) as cx:
                    r = cx.post(f"{base_url}/images/edits", headers={"Authorization": f"Bearer {key}"}, data=data, files=files)
        else:
            body: dict[str, Any] = {"prompt": prompt, "n": 1}
            if model:
                body["model"] = model
            if size:
                body["size"] = size
            body["response_format"] = "b64_json"
            with httpx.Client(timeout=timeout) as cx:
                r = cx.post(f"{base_url}/images/generations", headers=headers, json=body)
        if r.status_code >= 400:
            raise ImageProviderError(f"Endpoint error ({r.status_code}): {r.text[:300]}")
        d = (r.json().get("data") or [{}])[0]
        if d.get("b64_json"):
            return base64.b64decode(d["b64_json"])
        if d.get("url"):
            return httpx.get(d["url"], timeout=timeout).content
        raise ImageProviderError("Endpoint returned no image data.")
    except httpx.HTTPError as exc:
        raise ImageProviderError(f"Endpoint request failed: {exc}") from exc

"""Higgsfield CLI adapter.

Proxima treats Higgsfield as a local integration: the server runs the installed
CLI, uses the user's CLI login/workspace, and enforces credit policy before
submitting generation jobs.
"""
from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx


class HiggsfieldError(RuntimeError):
    """A Higgsfield operation failed. Message is safe to show the user."""


DEFAULT_IMAGE_MODEL = "nano_banana_2"
DEFAULT_VIDEO_MODEL = ""


def binary(path_env: str | None = None) -> str | None:
    base = path_env or os.environ.get("PATH", "")
    for extra in (
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/.nvm/current/bin"),
    ):
        if extra and extra not in base.split(os.pathsep):
            base = base + os.pathsep + extra
    # NOT "hf" — that's the Hugging Face CLI (a real collision), which made this
    # adapter drive a foreign tool and surface nonsense errors.
    for name in ("higgsfield", "higgs"):
        found = shutil.which(name, path=base)
        if found:
            return found
    return None


def _run(args: list[str], *, timeout: float = 30.0, path_env: str | None = None) -> subprocess.CompletedProcess[str]:
    exe = binary(path_env)
    if not exe:
        raise HiggsfieldError("Higgsfield CLI not found. Install `@higgsfield/cli` on the Proxima server.")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HiggsfieldError(f"Higgsfield CLI timed out after {int(timeout)}s.") from exc
    except OSError as exc:
        raise HiggsfieldError(f"Could not run Higgsfield CLI: {exc.strerror or exc}") from exc


def _json_or_text(proc: subprocess.CompletedProcess[str]) -> Any:
    out = (proc.stdout or "").strip()
    if out:
        try:
            return json.loads(out)
        except ValueError:
            return out
    err = (proc.stderr or "").strip()
    if err:
        try:
            return json.loads(err)
        except ValueError:
            return err
    return None


def status() -> dict[str, Any]:
    exe = binary()
    if not exe:
        return {
            "installed": False,
            "authenticated": False,
            "workspaceSelected": False,
            "ready": False,
            "detail": "Higgsfield CLI not found on PATH.",
        }

    try:
        token = _run(["auth", "token"], timeout=15)
    except HiggsfieldError as exc:
        return {
            "installed": True,
            "authenticated": False,
            "workspaceSelected": False,
            "ready": False,
            "binary": exe,
            "detail": str(exc)[:300],
        }
    token_text = f"{token.stdout or ''}\n{token.stderr or ''}".strip()
    if token.returncode != 0 or "not authenticated" in token_text.lower():
        return {
            "installed": True,
            "authenticated": False,
            "workspaceSelected": False,
            "ready": False,
            "binary": exe,
            "detail": "Higgsfield is not logged in on this server user. Run `higgsfield auth login` on the same machine/user that runs Proxima.",
        }

    try:
        account = _run(["account", "status", "--json"], timeout=20)
    except HiggsfieldError as exc:
        return {
            "installed": True,
            "authenticated": True,
            "workspaceSelected": False,
            "ready": False,
            "binary": exe,
            "detail": str(exc)[:300],
        }
    account_payload = _json_or_text(account)
    account_text = f"{account.stdout or ''}\n{account.stderr or ''}".strip()
    workspace_selected = account.returncode == 0
    if account.returncode == 2 or "not authenticated" in account_text.lower():
        return {
            "installed": True,
            "authenticated": False,
            "workspaceSelected": False,
            "ready": False,
            "binary": exe,
            "detail": "Higgsfield is not logged in on this server user. Run `higgsfield auth login` on the same machine/user that runs Proxima.",
        }
    if account.returncode == 4 or "workspace" in account_text.lower() and "selected" in account_text.lower():
        return {
            "installed": True,
            "authenticated": True,
            "workspaceSelected": False,
            "ready": False,
            "binary": exe,
            "account": account_payload,
            "detail": "Higgsfield login is present, but no workspace is selected on this server user. Run `higgsfield workspace list` then `higgsfield workspace set <workspace_id>`.",
        }
    if account.returncode != 0:
        detail = account_text or f"Higgsfield account status failed with code {account.returncode}."
        return {
            "installed": True,
            "authenticated": True,
            "workspaceSelected": workspace_selected,
            "ready": False,
            "binary": exe,
            "detail": detail[:300],
        }

    workspace_payload: Any = None
    workspace = _run(["workspace", "status", "--json"], timeout=20)
    if workspace.returncode == 0:
        workspace_payload = _json_or_text(workspace)
    return {
        "installed": True,
        "authenticated": True,
        "workspaceSelected": True,
        "ready": True,
        "binary": exe,
        "account": account_payload,
        "workspace": workspace_payload,
        "detail": "Higgsfield is connected.",
    }


def list_models(kind: str = "image") -> list[dict[str, Any]]:
    flag = "--video" if kind == "video" else "--image"
    proc = _run(["model", "list", flag, "--json"], timeout=30)
    if proc.returncode != 0:
        raise HiggsfieldError(((proc.stderr or proc.stdout or "").strip() or "Could not list Higgsfield models.")[:300])
    payload = _json_or_text(proc)
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        for key in ("models", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [m for m in value if isinstance(m, dict)]
    return []


def _append_param(args: list[str], name: str, value: Any) -> None:
    if value is None or value == "":
        return
    flag = f"--{name}"
    if isinstance(value, bool):
        if value:
            args.append(flag)
        return
    args.extend([flag, str(value)])


def _aspect_ratio_from_size(size: str | None) -> str | None:
    if not size:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", size)
    if not match:
        return size
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _contains_free_marker(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.search(r"\b(free|unlimited|included)\b", value, re.I))
    if isinstance(value, dict):
        return any(_contains_free_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_free_marker(v) for v in value)
    return False


def _extract_credit_cost(value: Any) -> float | None:
    numbers: list[float] = []

    def walk(node: Any, key_hint: str = "") -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                hint = f"{key_hint}.{key}".lower()
                if isinstance(child, (int, float)) and ("credit" in hint or "cost" in hint or "price" in hint):
                    numbers.append(float(child))
                elif isinstance(child, str) and ("credit" in hint or "cost" in hint or "price" in hint):
                    m = re.search(r"-?\d+(?:\.\d+)?", child)
                    if m:
                        numbers.append(float(m.group(0)))
                walk(child, hint)
        elif isinstance(node, list):
            for child in node:
                walk(child, key_hint)

    walk(value)
    if numbers:
        return max(numbers)
    if _contains_free_marker(value):
        return 0.0
    if isinstance(value, str):
        m = re.search(r"(\d+(?:\.\d+)?)\s*credits?", value, re.I)
        if m:
            return float(m.group(1))
    return None


def estimate_cost(job_type: str, params: dict[str, Any] | None = None, *, media: dict[str, str] | None = None) -> dict[str, Any]:
    args = ["generate", "cost", job_type]
    for key, value in (params or {}).items():
        _append_param(args, key, value)
    for key, value in (media or {}).items():
        _append_param(args, key, value)
    args.append("--json")
    proc = _run(args, timeout=60)
    payload = _json_or_text(proc)
    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "").strip() or "Could not estimate Higgsfield cost.")[:300]
        raise HiggsfieldError(detail)
    credits = _extract_credit_cost(payload)
    return {"credits": credits, "raw": payload}


def assert_zero_credit(job_type: str, params: dict[str, Any] | None = None, *, media: dict[str, str] | None = None) -> dict[str, Any]:
    cost = estimate_cost(job_type, params, media=media)
    credits = cost.get("credits")
    if credits is None:
        raise HiggsfieldError("Could not verify zero-credit Higgsfield cost, so the image request was blocked.")
    if float(credits) > 0:
        raise HiggsfieldError(f"Higgsfield estimated {credits:g} credits; blocked by zero-credit image policy.")
    return cost


def _extract_url(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return value
        return None
    if isinstance(value, dict):
        preferred_keys = ("url", "downloadUrl", "download_url", "resultUrl", "result_url", "imageUrl", "image_url", "videoUrl", "video_url")
        for key in preferred_keys:
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
                raise HiggsfieldError(f"Higgsfield result download failed ({res.status_code}).")
            return res.content
    except httpx.HTTPError as exc:
        raise HiggsfieldError(f"Higgsfield result download failed: {exc}") from exc


def generate_video(
    *,
    prompt: str,
    model: str | None = None,
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    timeout: float = 1200.0,
) -> bytes:
    job_type = model or DEFAULT_VIDEO_MODEL
    if not job_type:
        raise HiggsfieldError("No Higgsfield video model configured. Choose a video model in Settings → Video generation.")
    params: dict[str, Any] = {"prompt": prompt}
    if duration:
        params["duration"] = duration
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    if resolution:
        params["resolution"] = resolution
    args = ["generate", "create", job_type]
    for key, value in params.items():
        _append_param(args, key, value)
    args.extend(["--wait", "--wait-timeout", f"{max(1, int(timeout // 60))}m", "--wait-interval", "5s", "--json"])
    proc = _run(args, timeout=timeout + 30)
    payload = _json_or_text(proc)
    if proc.returncode != 0:
        detail = ((proc.stderr or proc.stdout or "").strip() or "Higgsfield video generation failed.")[:500]
        raise HiggsfieldError(detail)
    url = _extract_url(payload)
    if not url:
        raise HiggsfieldError("Higgsfield video generation completed but returned no result URL.")
    return _download(url, timeout=timeout)


def generate_image(
    *,
    prompt: str,
    model: str | None = None,
    size: str | None = None,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    timeout: float = 900.0,
    zero_credit_only: bool = True,
) -> bytes:
    job_type = model or DEFAULT_IMAGE_MODEL
    params: dict[str, Any] = {"prompt": prompt}
    if size:
        params["aspect_ratio"] = _aspect_ratio_from_size(size)
    media: dict[str, str] = {}
    tmp_path: str | None = None
    try:
        if image_bytes is not None:
            ext = mimetypes.guess_extension(image_mime or "image/png") or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            media["image"] = tmp_path
        if zero_credit_only:
            assert_zero_credit(job_type, params, media=media)
        args = ["generate", "create", job_type]
        for key, value in params.items():
            _append_param(args, key, value)
        for key, value in media.items():
            _append_param(args, key, value)
        args.extend(["--wait", "--wait-timeout", f"{max(1, int(timeout // 60))}m", "--wait-interval", "5s", "--json"])
        proc = _run(args, timeout=timeout + 30)
        payload = _json_or_text(proc)
        if proc.returncode != 0:
            detail = ((proc.stderr or proc.stdout or "").strip() or "Higgsfield generation failed.")[:500]
            raise HiggsfieldError(detail)
        url = _extract_url(payload)
        if not url:
            raise HiggsfieldError("Higgsfield generation completed but returned no result URL.")
        return _download(url, timeout=timeout)
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

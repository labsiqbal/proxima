from __future__ import annotations

import base64

import httpx
import pytest

from proxima_api import media_providers, video_providers


# ── shared media-provider helpers ──────────────────────────────────────────

def test_base_url_join_drops_trailing_slash_and_appends_path():
    assert media_providers.endpoint("https://api.linc.id/v1/", "videos/generations") == (
        "https://api.linc.id/v1/videos/generations"
    )
    assert media_providers.endpoint("https://api.linc.id/v1", "videos/abc") == (
        "https://api.linc.id/v1/videos/abc"
    )


def test_error_detail_names_the_base_url_when_the_endpoint_answers_html():
    """A base URL that already contains the path (or points at a web app) answers
    with an HTML page. The message must say so instead of dumping markup."""
    resp = httpx.Response(
        404,
        headers={"content-type": "text/html; charset=utf-8"},
        text="<!DOCTYPE html><html><head><title>404</title></head><body>Not found</body></html>",
        request=httpx.Request("POST", "https://api.linc.id/v1/videos/generations"),
    )
    detail = media_providers.response_error_detail(resp)
    assert "HTML" in detail
    assert "base URL" in detail
    assert "<html" not in detail.lower()


def test_error_detail_unwraps_openai_style_and_bare_string_errors():
    nested = httpx.Response(
        400,
        json={"error": {"message": "Provider 'nope' does not support video generation", "code": "bad_request"}},
        request=httpx.Request("POST", "https://x/v1/videos/generations"),
    )
    assert "does not support video generation" in media_providers.response_error_detail(nested)

    bare = httpx.Response(
        401,
        json={"error": "API key required for remote API access"},
        request=httpx.Request("POST", "https://x/v1/videos/generations"),
    )
    assert "API key required" in media_providers.response_error_detail(bare)


# ── provider metadata ──────────────────────────────────────────────────────

def test_openai_compatible_is_the_default_video_provider():
    assert video_providers.DEFAULT_PROVIDER == "openai-compatible"
    assert video_providers.get_provider(None).id == "openai-compatible"
    assert video_providers.get_provider("nope").id == "openai-compatible"


def test_provider_list_exposes_endpoint_metadata_for_settings():
    listed = video_providers.provider_list()
    assert {p["id"] for p in listed} == set(video_providers.VIDEO_PROVIDER_IDS)
    http = next(p for p in listed if p["id"] == "openai-compatible")
    assert http["kind"] == "http"
    assert http["requiresKey"] is True
    assert http["defaultBaseUrl"] == "https://api.openai.com/v1"
    assert http["capabilities"]["textToVideo"] is True


# ── generate: async job (gateway contract) ─────────────────────────────────

class _Resp:
    """Minimal httpx.Response stand-in matching the existing image-provider tests."""

    def __init__(self, status, json_body=None, content=b"", headers=None):
        self.status_code = status
        self._json = json_body
        self.text = "" if json_body is None else str(json_body)
        self.content = content
        self.headers = headers or ({"content-type": "application/json"} if json_body is not None else {})

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _routes(monkeypatch, posts, gets):
    """Route POST/GET by URL through dicts of url -> list-of-responses (or callable)."""
    calls: dict[str, list] = {"post": [], "get": []}

    def take(table, url):
        entry = table.get(url)
        if entry is None:
            raise AssertionError(f"unexpected request to {url}")
        if isinstance(entry, list):
            return entry.pop(0) if len(entry) > 1 else entry[0]
        return entry

    def fake_post(self, url, headers=None, json=None, **kw):
        calls["post"].append({"url": url, "json": json, "headers": headers})
        return take(posts, url)

    def fake_get(self, url, headers=None, **kw):
        calls["get"].append({"url": url, "headers": headers})
        return take(gets, url)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(httpx.Client, "get", fake_get)
    return calls


def test_generate_submits_to_videos_generations_and_polls_the_job(monkeypatch):
    """The owner's gateway: POST {base}/videos/generations -> {request_id},
    GET {base}/videos/{id} -> pending… then done with a video URL."""
    base = "https://api.linc.id/v1/"
    calls = _routes(
        monkeypatch,
        posts={
            "https://api.linc.id/v1/videos/generations": _Resp(200, {"request_id": "job-1"}),
        },
        gets={
            "https://api.linc.id/v1/videos/job-1": [
                _Resp(202, {"status": "pending", "progress": 1}),
                _Resp(200, {
                    "status": "done",
                    "video": {"url": "https://cdn.example/x.mp4", "duration": 8},
                    "model": "grok-imagine-video",
                }),
            ],
            "https://cdn.example/x.mp4": _Resp(200, content=b"MP4BYTES", headers={"content-type": "video/mp4"}),
        },
    )
    result = video_providers.generate(
        "openai-compatible",
        "sk-test",
        prompt="a cat waving",
        model="xai/grok-imagine-video",
        base_url=base,
        poll_interval=0.0,
    )
    assert result.data == b"MP4BYTES"
    assert result.extension == ".mp4"
    assert result.duration_seconds == 8
    submit = calls["post"][0]
    assert submit["json"]["prompt"] == "a cat waving"
    assert submit["json"]["model"] == "xai/grok-imagine-video"
    assert submit["headers"]["Authorization"] == "Bearer sk-test"


def test_generate_falls_back_to_the_sora_videos_endpoint(monkeypatch):
    """OpenAI's Sora contract: POST {base}/videos -> job {id,status}, poll, then
    GET {base}/videos/{id}/content for the bytes."""
    calls = _routes(
        monkeypatch,
        posts={
            "https://api.openai.com/v1/videos/generations": _Resp(404, {"error": {"message": "Unknown endpoint"}}),
            "https://api.openai.com/v1/videos": _Resp(200, {"id": "video_123", "status": "queued", "progress": 0}),
        },
        gets={
            "https://api.openai.com/v1/videos/video_123": [
                _Resp(200, {"id": "video_123", "status": "in_progress", "progress": 40}),
                _Resp(200, {"id": "video_123", "status": "completed", "progress": 100, "seconds": "4"}),
            ],
            "https://api.openai.com/v1/videos/video_123/content": _Resp(
                200, content=b"SORABYTES", headers={"content-type": "video/mp4"}
            ),
        },
    )
    result = video_providers.generate(
        "openai-compatible",
        "sk-test",
        prompt="a cat",
        model="sora-2",
        seconds=4,
        size="720x1280",
        base_url="https://api.openai.com/v1",
        poll_interval=0.0,
    )
    assert result.data == b"SORABYTES"
    sora_submit = calls["post"][1]
    assert sora_submit["json"]["seconds"] == "4"
    assert sora_submit["json"]["size"] == "720x1280"


def test_generate_returns_a_synchronous_response_without_polling(monkeypatch):
    b64 = base64.b64encode(b"INLINE").decode()
    calls = _routes(
        monkeypatch,
        posts={"https://gw/v1/videos/generations": _Resp(200, {"data": [{"b64_json": b64}]})},
        gets={},
    )
    result = video_providers.generate(
        "openai-compatible", "sk", prompt="x", base_url="https://gw/v1", poll_interval=0.0
    )
    assert result.data == b"INLINE"
    assert calls["get"] == []


# ── generate: error surfacing ──────────────────────────────────────────────

def test_generate_requires_an_api_key():
    with pytest.raises(video_providers.VideoProviderError, match="API key"):
        video_providers.generate("openai-compatible", None, prompt="x", base_url="https://gw/v1")


def test_generate_surfaces_an_html_response_as_a_base_url_hint(monkeypatch):
    html = "<!DOCTYPE html><html><body>Next.js 404</body></html>"
    _routes(
        monkeypatch,
        posts={
            "https://gw/v1/videos/generations": _Resp(404, None, content=html.encode(), headers={"content-type": "text/html"}),
            "https://gw/v1/videos": _Resp(404, None, content=html.encode(), headers={"content-type": "text/html"}),
        },
        gets={},
    )
    with pytest.raises(video_providers.VideoProviderError) as exc:
        video_providers.generate(
            "openai-compatible", "sk", prompt="x", base_url="https://gw/v1", poll_interval=0.0
        )
    message = str(exc.value)
    assert "base URL" in message
    assert "<html" not in message.lower()


def test_generate_surfaces_a_json_error_message(monkeypatch):
    _routes(
        monkeypatch,
        posts={
            "https://gw/v1/videos/generations": _Resp(
                400, {"error": {"message": "Provider 'nope' does not support video generation"}}
            )
        },
        gets={},
    )
    with pytest.raises(video_providers.VideoProviderError, match="does not support video generation"):
        video_providers.generate(
            "openai-compatible", "sk", prompt="x", model="nope/x", base_url="https://gw/v1", poll_interval=0.0
        )


def test_generate_surfaces_a_failed_job(monkeypatch):
    _routes(
        monkeypatch,
        posts={"https://gw/v1/videos/generations": _Resp(200, {"request_id": "j1"})},
        gets={
            "https://gw/v1/videos/j1": _Resp(
                200, {"status": "failed", "error": {"message": "moderation blocked the prompt"}}
            )
        },
    )
    with pytest.raises(video_providers.VideoProviderError, match="moderation blocked the prompt"):
        video_providers.generate(
            "openai-compatible", "sk", prompt="x", base_url="https://gw/v1", poll_interval=0.0
        )


def test_generate_times_out_with_an_actionable_message(monkeypatch):
    _routes(
        monkeypatch,
        posts={"https://gw/v1/videos/generations": _Resp(200, {"request_id": "j1"})},
        gets={"https://gw/v1/videos/j1": _Resp(202, {"status": "pending", "progress": 3})},
    )
    with pytest.raises(video_providers.VideoProviderError) as exc:
        video_providers.generate(
            "openai-compatible", "sk", prompt="x", base_url="https://gw/v1", timeout=0.0, poll_interval=0.0
        )
    assert "still pending" in str(exc.value)
    assert "3%" in str(exc.value)


def test_generate_reports_a_finished_job_whose_content_is_missing(monkeypatch):
    """A job that reports done but carries no URL falls back to the Sora content
    endpoint; when that has nothing either, the message says so."""
    _routes(
        monkeypatch,
        posts={"https://gw/v1/videos/generations": _Resp(200, {"request_id": "j1"})},
        gets={
            "https://gw/v1/videos/j1": _Resp(200, {"status": "done"}),
            "https://gw/v1/videos/j1/content": _Resp(404, {"error": {"message": "no content for this job"}}),
        },
    )
    with pytest.raises(video_providers.VideoProviderError, match="no content for this job"):
        video_providers.generate(
            "openai-compatible", "sk", prompt="x", base_url="https://gw/v1", poll_interval=0.0
        )


# ── test_connection ────────────────────────────────────────────────────────

def test_test_connection_reports_reachable_endpoint(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: _Resp(200, {"data": [1, 2, 3]}))
    out = video_providers.test_connection("openai-compatible", "sk", base_url="https://gw/v1")
    assert out["ok"] is True
    assert "3 models" in out["detail"]


def test_test_connection_rejects_a_bad_key(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: _Resp(401, {"error": "bad key"}))
    out = video_providers.test_connection("openai-compatible", "sk-bad", base_url="https://gw/v1")
    assert out["ok"] is False
    assert "401" in out["detail"]


def test_test_connection_requires_a_key():
    out = video_providers.test_connection("openai-compatible", None, base_url="https://gw/v1")
    assert out["ok"] is False
    assert "API key" in out["detail"]

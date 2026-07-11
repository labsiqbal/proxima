"""File, design-image, artifact, and app-runner routes for the Proxima API.

Extracted via the register() pattern — handler bodies verbatim. No behavior change.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import fsapi
from .. import app_settings
from .. import auth_health
from .. import design_scenes
from .. import features
from .. import higgsfield
from .. import image_providers
from .. import video_providers
from .. import cf_hostnames
from ..artifacts import scan_project_artifacts
from ..schemas import (
    AppStartRequest, FileWriteRequest, FsPathRequest, FsRenameRequest, ImageGenRequest,
)


def register(app, deps):
    db = deps["db"]
    feature_cfg = deps["cfg"]
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    session_for_user = deps["session_for_user"]
    _project_root = deps["_project_root"]
    user_from_token_query = deps["user_from_token_query"]
    video_render_jobs: dict[str, str] = {}

    def _audit_fs(user: dict[str, Any], action: str, slug: str, path: str) -> None:
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, ?, 'project', ?, ?)",
            (user["id"], action, slug, json.dumps({"path": path})),
        )

    def _parse_video_studio_id(studio_id: str) -> tuple[str, str] | None:
        prefix = "proxima-video__"
        if not studio_id.startswith(prefix):
            return None
        parts = studio_id[len(prefix):].split("__", 1)
        if len(parts) != 2:
            return None
        slug, video_id = parts
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug or ""):
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", video_id or ""):
            return None
        return slug, video_id

    def _video_studio_dir(studio_id: str, user: dict[str, Any]) -> tuple[str, str, Path]:
        features.require(feature_cfg, features.VIDEO)
        parsed = _parse_video_studio_id(studio_id)
        if not parsed:
            raise HTTPException(status_code=404, detail="video studio project not found")
        slug, video_id = parsed
        root = _project_root(slug, user)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not (d / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        return slug, video_id, d

    @app.get("/api/projects/{slug}/tree")
    def project_tree(slug: str, path: str = "", user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            return {"path": path, "entries": fsapi.list_tree(root, path)}
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/file")
    def project_read_file(slug: str, path: str, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            return {"path": path, "content": fsapi.read_file(root, path)}
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/projects/{slug}/file")
    def project_write_file(slug: str, path: str, payload: FileWriteRequest, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            fsapi.write_file(root, path, payload.content)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_fs(user, "file.write", slug, path)
        return {"ok": True, "path": path}

    @app.post("/api/projects/{slug}/upload")
    async def project_upload(slug: str, request: Request, file: UploadFile = File(...), dir: str = "uploads", user: dict[str, Any] = Depends(current_user)):
        parsed = _parse_video_studio_id(slug)
        if parsed:
            features.require(feature_cfg, features.VIDEO)
            project_slug, video_id, _ = _video_studio_dir(slug, user)
            port = app.state.app_manager.port(project_slug)
            if not port:
                raise HTTPException(status_code=503, detail="HyperFrames Studio API is not running")
            url = f"http://127.0.0.1:{port}/api/projects/{video_id}/upload"
            fwd = {k: v for k, v in request.headers.items() if k.lower() not in _HOP and k.lower() != "content-type"}
            try:
                uploaded = await file.read()
                async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                    up = await client.request(
                        request.method, url, params=request.query_params, headers=fwd,
                        files={"file": (file.filename or "file", uploaded, file.content_type or "application/octet-stream")},
                    )
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail="HyperFrames Studio API not reachable yet") from None
            out = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}
            return Response(content=up.content, status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))
        root = _project_root(slug, user)
        name = Path(file.filename or "file").name or "file"
        folder = (dir or "uploads").strip("/") or "uploads"
        try:
            target = fsapi.resolve_in_project(root, f"{folder}/{name}")
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"cannot create upload directory: {exc.strerror}") from exc
        if target.exists():  # de-dupe: name.ext -> name-1.ext
            stem, suffix, i = target.stem, target.suffix, 1
            while target.exists():
                target = target.parent / f"{stem}-{i}{suffix}"; i += 1
        try:
            target.write_bytes(await file.read())
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"cannot write upload: {exc.strerror}") from exc
        rel = f"{folder}/{target.name}"
        _audit_fs(user, "file.upload", slug, rel)
        return {"path": rel, "name": target.name}

    def _ninerouter() -> tuple[str, str | None]:
        """9router base URL + API key, from env (NINEROUTER_URL / NINEROUTER_KEY /
        NINEROUTER_TOKEN) or an optional env-file at NINEROUTER_ENV_FILE."""
        url = os.environ.get("NINEROUTER_URL") or "http://localhost:20128"
        key = os.environ.get("NINEROUTER_KEY") or os.environ.get("NINEROUTER_TOKEN")
        env_file = os.environ.get("NINEROUTER_ENV_FILE")
        if not key and env_file:
            try:
                for line in Path(os.path.expanduser(env_file)).read_text().splitlines():
                    s = line.strip()
                    if s.startswith(("NINEROUTER_TOKEN=", "NINEROUTER_KEY=")):
                        key = s.split("=", 1)[1].strip().strip('"').strip("'")
                    elif s.startswith("NINEROUTER_URL="):
                        url = s.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
        return url, key

    @app.post("/api/projects/{slug}/designs/from-image")
    def design_from_image(slug: str, payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        """Seed a new Design Studio scene containing an existing project image as a
        full-bleed layer — the 'edit this image in Design Studio' bridge from chat."""
        features.require(feature_cfg, features.DESIGN_STUDIO)
        payload = payload or {}
        root = _project_root(slug, user)
        rel = str(payload.get("path") or "").strip()
        if not rel:
            raise HTTPException(status_code=400, detail="path is required")
        try:
            source = fsapi.resolve_in_project(root, rel)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not source.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {rel}")
        design_id, scene = design_scenes.scene_for_image(rel, design_scenes.image_dims(source), payload.get("title"))
        d = fsapi.resolve_in_project(root, f"artifacts/design/{design_id}")
        d.mkdir(parents=True, exist_ok=True)
        (d / "scene.json").write_text(json.dumps(scene, indent=2), encoding="utf-8")
        _audit_fs(user, "design.from_image", slug, f"{rel} -> artifacts/design/{design_id}")
        return {"ok": True, "id": design_id, "title": scene["title"], "path": f"artifacts/design/{design_id}"}

    @app.post("/api/projects/{slug}/design/image")
    async def design_image(slug: str, payload: ImageGenRequest, user: dict[str, Any] = Depends(current_user)):
        """Generate (text→image) or edit (image+prompt→image) via the configured
        provider (Settings); save the result into the project's shared design
        asset library and return its path."""
        features.require(feature_cfg, features.DESIGN_STUDIO)
        root = _project_root(slug, user)
        prov = _resolve_image_gen()
        image_bytes: bytes | None = None
        image_mime: str | None = None
        if payload.image:
            try:
                src = fsapi.resolve_in_project(root, payload.image)
                if not src.is_file():
                    raise fsapi.FsError("source image does not exist")
                image_bytes = src.read_bytes()
            except fsapi.FsError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=400, detail=f"cannot read source image: {exc.strerror}") from exc
            image_mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        # Edit/reference requests need an edit-capable provider. When the selected one
        # is explicitly text-to-image only (codex), fall back to xAI OAuth if it's
        # connected — otherwise explain instead of failing with a provider error.
        if image_bytes is not None and (image_providers.get_provider(prov.get("provider")).capabilities or {}).get("imageEdit") is False:
            if image_providers.xai_oauth_ready().get("ready"):
                prov = {**prov, "provider": "xai-oauth", "apiKey": None, "baseUrl": None, "model": None}
            else:
                raise HTTPException(status_code=400, detail="The selected image provider is text-to-image only and no edit-capable fallback is connected. Switch the provider in Settings → Image generation (e.g. xAI OAuth) to edit or use reference images.")
        target = fsapi.resolve_in_project(root, f"artifacts/design/_assets/gen-{int(time.time())}.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        i = 1
        while target.exists():
            target = target.parent / f"gen-{int(time.time())}-{i}.png"; i += 1
        model = payload.model or prov.get("model")
        if not model and prov.get("provider") in {"auto", "higgsfield"}:
            model = _resolve_higgsfield_settings().get("imageModel")
        try:
            raw = image_providers.generate(
                prov["provider"], prov.get("apiKey"),
                prompt=payload.prompt,
                model=model,
                size=payload.size,
                image_bytes=image_bytes,
                image_mime=image_mime,
                base_url=prov.get("baseUrl"),
            )
        except image_providers.ImageProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not raw:
            raise HTTPException(status_code=502, detail="provider returned no image data")
        # Every provider returns bytes — persist them here (the out_path shortcut was
        # dead: generate() never wrote files itself).
        target.write_bytes(raw)
        rel = f"artifacts/design/_assets/{target.name}"
        _audit_fs(user, "design.image", slug, rel)
        return {"path": rel, "name": target.name}

    @app.get("/api/projects/{slug}/design/image-models")
    def design_image_models(slug: str, user: dict[str, Any] = Depends(current_user)):
        """For the codex provider there's no static model list (login-based); for
        an openai-compatible endpoint we report configured + the saved model."""
        features.require(feature_cfg, features.DESIGN_STUDIO)
        prov = _resolve_image_gen()
        spec = image_providers.get_provider(prov["provider"])
        if spec.kind == "codex":
            return {"models": [], "configured": True, "kind": "codex"}
        if spec.kind == "auto":
            return {"models": [], "configured": True, "kind": "auto", "model": prov.get("model")}
        if spec.kind == "higgsfield":
            return {"models": [], "configured": bool(higgsfield.status().get("ready")), "kind": "higgsfield", "model": prov.get("model")}
        return {"models": [], "configured": bool(prov.get("apiKey")), "kind": "http", "model": prov.get("model")}

    # ── HyperFrames video projects ───────────────────────────────────────

    def _video_id(name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", (name or "video").lower()).strip("-")[:54] or "video"
        return f"{base}-{int(time.time())}"

    def _video_template(title: str, brief: str, w: int = 1080, h: int = 1920, duration: int = 10) -> str:
        esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<style>
html,body{{margin:0;width:100%;height:100%;background:#080a10;font-family:Inter,system-ui,sans-serif;color:white;overflow:hidden}}
[data-composition-id=root]{{position:relative;width:{w}px;height:{h}px;background:radial-gradient(circle at 20% 20%,#3b82f6 0,#111827 34%,#050816 100%);overflow:hidden}}
.scene-content{{position:absolute;inset:0;display:grid;align-content:center;gap:28px;padding:96px;box-sizing:border-box}}
.eyebrow{{width:max-content;padding:12px 18px;border:1px solid rgba(255,255,255,.22);border-radius:999px;background:rgba(255,255,255,.08);font-size:26px;text-transform:uppercase;letter-spacing:.12em;color:#bfdbfe}}
h1{{font-size:92px;line-height:.96;margin:0;letter-spacing:-.04em;max-width:850px}}
p{{font-size:34px;line-height:1.25;margin:0;color:#dbeafe;max-width:760px}}
.orb{{position:absolute;width:520px;height:520px;border-radius:50%;right:-180px;bottom:-130px;background:linear-gradient(135deg,#60a5fa,#f472b6);filter:blur(10px);opacity:.55}}
</style></head>
<body>
<div data-composition-id="root" data-width="{w}" data-height="{h}" data-start="0" data-duration="{duration}">
  <div id="motion-orb" class="clip orb" data-start="0" data-duration="{duration}" data-track-index="0"></div>
  <section id="main-message" class="clip scene-content" data-start="0" data-duration="{duration}" data-track-index="1">
    <div class="eyebrow">Proxima Video</div>
    <h1 id="title">{esc(title)}</h1>
    <p id="subtitle">{esc(brief or "Edit this HyperFrames composition with the agent.")}</p>
  </section>
</div>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
window.__timelines = window.__timelines || {{}};
const duration = {duration};
if (window.gsap) {{
  const tl = gsap.timeline({{ paused: true }});
  tl.from('.eyebrow', {{ y: 28, opacity: 0, duration: .55, ease: 'power3.out' }}, 0)
    .from('h1', {{ y: 64, opacity: 0, duration: .7, ease: 'power3.out' }}, .15)
    .from('p', {{ y: 34, opacity: 0, duration: .55, ease: 'power2.out' }}, .35)
    .fromTo('.orb', {{ scale: .75, rotate: 0 }}, {{ scale: 1.12, rotate: 18, duration, ease: 'none' }}, 0);
  window.__timelines.root = tl;
}} else {{
  let current = 0;
  const clamp = v => Math.max(0, Math.min(duration, Number(v) || 0));
  const apply = t => {{
    current = clamp(t);
    const p = current / duration;
    document.querySelector('.eyebrow').style.opacity = p > .03 ? '1' : '0';
    document.querySelector('h1').style.opacity = p > .06 ? '1' : '0';
    document.querySelector('p').style.opacity = p > .1 ? '1' : '0';
    document.querySelector('.orb').style.transform = `scale(${{.75 + p * .37}}) rotate(${{p * 18}}deg)`;
  }};
  window.__timelines.root = {{
    duration: () => duration,
    time: value => value == null ? current : (apply(value), window.__timelines.root),
    seek: value => (apply(value), window.__timelines.root),
    play: () => window.__timelines.root,
    pause: () => window.__timelines.root,
  }};
  apply(0);
}}
</script>
</body></html>
"""

    @app.get("/api/projects/{slug}/videos")
    def list_videos(slug: str, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        root = _project_root(slug, user)
        base = fsapi.resolve_in_project(root, "artifacts/video")
        if not base.exists():
            return {"videos": []}
        out = []
        for d in sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
            idx = d / "index.html"
            if not idx.is_file():
                continue
            title = d.name
            width = 1080
            height = 1920
            try:
                html = idx.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip() or title
                wm = re.search(r'data-width=["\'](\d+)["\']', html, re.I)
                hm = re.search(r'data-height=["\'](\d+)["\']', html, re.I)
                if wm and hm:
                    width = int(wm.group(1))
                    height = int(hm.group(1))
            except OSError:
                pass
            out.append({"id": d.name, "title": title, "path": f"artifacts/video/{d.name}", "width": width, "height": height, "updated_at": d.stat().st_mtime})
        return {"videos": out}

    @app.post("/api/projects/{slug}/videos")
    def create_video(slug: str, payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        payload = payload or {}
        root = _project_root(slug, user)
        title = str(payload.get("title") or "Untitled video").strip() or "Untitled video"
        brief = str(payload.get("brief") or "").strip()
        vid = _video_id(title)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{vid}")
        d.mkdir(parents=True, exist_ok=False)
        (d / "index.html").write_text(_video_template(title, brief), encoding="utf-8")
        (d / "hyperframes.json").write_text(json.dumps({"name": vid, "entry": "index.html"}, indent=2), encoding="utf-8")
        (d / "renders").mkdir(exist_ok=True)
        _audit_fs(user, "video.create", slug, f"artifacts/video/{vid}")
        return {"id": vid, "title": title, "path": f"artifacts/video/{vid}"}

    MEDIA_IMPORT_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".mp4", ".webm", ".mov", ".mp3", ".wav", ".ogg"}

    @app.post("/api/projects/{slug}/videos/{video_id}/import-file")
    def video_import_file(slug: str, video_id: str, payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        """Copy an existing project media file into the video project's assets/ so the
        studio (which only sees its own directory) can use it on the timeline."""
        features.require(feature_cfg, features.VIDEO)
        payload = payload or {}
        root = _project_root(slug, user)
        video_dir = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not (video_dir / "index.html").is_file():
            raise HTTPException(status_code=404, detail=f"video project not found: {video_id}")
        rel = str(payload.get("path") or "").strip()
        if not rel:
            raise HTTPException(status_code=400, detail="path is required")
        source = fsapi.resolve_in_project(root, rel)
        if not source.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {rel}")
        if source.suffix.lower() not in MEDIA_IMPORT_EXTS:
            raise HTTPException(status_code=400, detail=f"not an importable media file: {source.name}")
        assets = video_dir / "assets"
        assets.mkdir(exist_ok=True)
        target = assets / source.name
        i = 1
        while target.exists():
            target = assets / f"{source.stem}-{i}{source.suffix}"; i += 1
        shutil.copy2(source, target)
        _audit_fs(user, "video.import_file", slug, f"{rel} -> artifacts/video/{video_id}/assets/{target.name}")
        return {"ok": True, "video_id": video_id, "path": f"assets/{target.name}"}

    @app.delete("/api/projects/{slug}/videos/{video_id}")
    def delete_video(slug: str, video_id: str, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", video_id or ""):
            raise HTTPException(status_code=404, detail="video project not found")
        root = _project_root(slug, user)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not d.is_dir() or not (d / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        shutil.rmtree(d)
        _audit_fs(user, "video.delete", slug, f"artifacts/video/{video_id}")
        return {"ok": True, "id": video_id, "path": f"artifacts/video/{video_id}"}

    @app.post("/api/projects/{slug}/videos/{video_id}/studio/start")
    async def start_video_studio(slug: str, video_id: str, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        root = _project_root(slug, user)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not (d / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        if not shutil.which("npx"):
            raise HTTPException(status_code=400, detail="npx is not installed; install Node.js/HyperFrames tooling to run Studio")
        port = 3920 + (abs(hash(f"{slug}:{video_id}")) % 600)
        cmd = f"npx --yes hyperframes preview . --port {port} --no-open --force-new"
        await app.state.app_manager.start(slug, str(d), cmd, port)
        _audit_fs(user, "video.studio.start", slug, f"artifacts/video/{video_id}")
        return {"ok": True, "port": port, "path": f"artifacts/video/{video_id}"}

    @app.post("/api/projects/{slug}/videos/{video_id}/lint")
    def lint_video(slug: str, video_id: str, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        root = _project_root(slug, user)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not (d / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        if not shutil.which("npx"):
            raise HTTPException(status_code=400, detail="npx is not installed; install Node.js/HyperFrames tooling to lint video")
        try:
            proc = subprocess.run(
                ["npx", "--yes", "hyperframes", "lint"],
                cwd=str(d), text=True, capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="HyperFrames lint timed out") from exc
        log = ((proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")).strip()
        return {"ok": proc.returncode == 0, "log": log[-8000:]}

    @app.post("/api/projects/{slug}/videos/{video_id}/render")
    def render_video(slug: str, video_id: str, payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        root = _project_root(slug, user)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not (d / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        if not shutil.which("npx"):
            raise HTTPException(status_code=400, detail="npx is not installed; install Node.js/HyperFrames tooling to render MP4")
        payload = payload or {}
        fmt = str(payload.get("format") or "mp4").lower()
        if fmt not in {"mp4", "webm"}:
            raise HTTPException(status_code=400, detail="format must be mp4 or webm")
        out = d / "renders" / f"{video_id}.{fmt}"
        cmd = ["npx", "--yes", "hyperframes", "render", "--output", str(out), "--quality", str(payload.get("quality") or "draft")]
        fps = payload.get("fps")
        if fps:
            cmd.extend(["--fps", str(fps)])
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(d), text=True, capture_output=True, timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="HyperFrames render timed out") from exc
        if proc.returncode != 0:
            raise HTTPException(status_code=400, detail=(proc.stderr or proc.stdout or "HyperFrames render failed")[-4000:])
        _audit_fs(user, "video.render", slug, str(out.relative_to(root)))
        return {"ok": True, "path": str(out.relative_to(root)), "log": (proc.stdout or "")[-4000:]}

    # ── Image-generation provider settings ────────────────────────────────

    def _resolve_image_gen() -> dict[str, Any]:
        """Active provider config from Settings. Defaults to codex (no key) when
        nothing is saved yet."""
        cfg = app_settings.get_json(db(), app_settings.IMAGE_GEN_KEY)
        if cfg and isinstance(cfg, dict) and cfg.get("provider") in image_providers.IMAGE_PROVIDER_IDS:
            return cfg
        return {"provider": image_providers.DEFAULT_PROVIDER, "apiKey": None, "baseUrl": None, "model": None}

    def _resolve_higgsfield_settings() -> dict[str, Any]:
        cfg = app_settings.get_json(db(), app_settings.HIGGSFIELD_KEY)
        if not isinstance(cfg, dict):
            cfg = {}
        return {
            "imagePolicy": cfg.get("imagePolicy") or "zero-credit-only",
            "imageModel": cfg.get("imageModel") or higgsfield.DEFAULT_IMAGE_MODEL,
            "videoPolicy": cfg.get("videoPolicy") or "confirm-credits",
            "videoModel": cfg.get("videoModel") or higgsfield.DEFAULT_VIDEO_MODEL,
            "maxVideoCredits": cfg.get("maxVideoCredits") if isinstance(cfg.get("maxVideoCredits"), (int, float)) else 50,
        }

    def _public_higgsfield_settings(settings: dict[str, Any]) -> dict[str, Any]:
        if features.enabled(feature_cfg, features.VIDEO):
            return settings
        return {key: settings[key] for key in ("imagePolicy", "imageModel")}

    def _resolve_video_gen() -> dict[str, Any]:
        cfg = app_settings.get_json(db(), app_settings.VIDEO_GEN_KEY)
        if cfg and isinstance(cfg, dict) and cfg.get("provider") in video_providers.VIDEO_PROVIDER_IDS:
            return cfg
        hcfg = _resolve_higgsfield_settings()
        return {
            "provider": video_providers.DEFAULT_PROVIDER,
            "model": video_providers.DEFAULT_MODEL,
            "videoPolicy": hcfg["videoPolicy"],
            "maxVideoCredits": hcfg["maxVideoCredits"],
        }

    @app.get("/api/settings/permissions")
    def get_permission_settings(user: dict[str, Any] = Depends(current_user)):
        """Auto-approve toggle: when on, agent permission prompts are approved
        automatically (no cards). Default ON (unset ⇒ on)."""
        return {"auto_approve": app_settings.get_setting(db(), "auto_approve_permissions", "1") != "0"}

    @app.put("/api/settings/permissions")
    def set_permission_settings(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        on = bool(payload.get("auto_approve"))
        app_settings.set_setting(db(), "auto_approve_permissions", "1" if on else "0")
        return {"auto_approve": on}

    @app.get("/api/settings/collaboration")
    def get_collaboration_settings(user: dict[str, Any] = Depends(current_user)):
        return app_settings.get_collaboration_settings(db())

    @app.put("/api/settings/collaboration")
    def set_collaboration_settings(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        try:
            return app_settings.set_collaboration_settings(
                db(),
                int(payload.get("brainstorm_agents", 3)),
                int(payload.get("debate_rounds", 2)),
            )
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid collaboration settings")

    @app.get("/api/settings/image-gen")
    def get_image_gen_settings(user: dict[str, Any] = Depends(current_user)):
        """The saved image-gen config + provider metadata + codex readiness."""
        cfg = _resolve_image_gen()
        key = cfg.get("apiKey")
        spec = image_providers.get_provider(cfg["provider"])
        # Codex readiness is surfaced here so the UI can show ready/not-logged-in.
        codex = image_providers.codex_ready() if spec.kind in ("auto", "codex") else None
        hstatus = higgsfield.status() if spec.kind in ("auto", "higgsfield") else None
        return {
            "provider": cfg["provider"],
            "model": cfg.get("model"),
            "baseUrl": cfg.get("baseUrl"),
            "hasApiKey": bool(key),
            "providers": image_providers.provider_list(),
            "defaultProvider": image_providers.DEFAULT_PROVIDER,
            "codexReady": codex,
            "higgsfieldReady": hstatus,
            # Edit/reference requests can fall back to xAI OAuth when the selected
            # provider is text-to-image only — the UI keeps "Edit with AI" enabled.
            "xaiOauthReady": image_providers.xai_oauth_ready(),
        }

    @app.put("/api/settings/image-gen")
    def put_image_gen_settings(payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        """Save the image-gen provider/model/key/baseUrl. An empty apiKey keeps the
        existing key (so a UI round-trip never erases it)."""
        payload = payload or {}
        provider_id = payload.get("provider")
        if provider_id and provider_id not in image_providers.IMAGE_PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"unknown provider: {provider_id}")
        current = app_settings.get_json(db(), app_settings.IMAGE_GEN_KEY) or {}
        effective_provider = provider_id or current.get("provider") or image_providers.DEFAULT_PROVIDER
        spec = image_providers.get_provider(effective_provider)
        provider_changed = bool(provider_id and provider_id != current.get("provider"))
        key = payload.get("apiKey")
        if key in (None, ""):
            key = current.get("apiKey")  # preserve existing key on empty submit
        if "model" in payload:
            model = payload.get("model") or None
        else:
            model = None if provider_changed else current.get("model")
        if "baseUrl" in payload:
            base_url = payload.get("baseUrl") or spec.default_base_url or None
        elif provider_changed:
            base_url = spec.default_base_url or None
        else:
            base_url = current.get("baseUrl") or spec.default_base_url or None
        cfg = {
            "provider": effective_provider,
            "model": model,
            "baseUrl": base_url,
            "apiKey": key,
        }
        app_settings.set_json(db(), app_settings.IMAGE_GEN_KEY, cfg)
        auth_health.invalidate()  # Home Connections card re-checks on its next poll
        _audit_fs(user, "settings.image_gen", "-", json.dumps({"provider": cfg["provider"], "model": cfg["model"], "baseUrl": cfg["baseUrl"], "key_set": bool(cfg["apiKey"])}))
        return {"ok": True, "provider": cfg["provider"], "model": cfg["model"], "hasApiKey": bool(cfg["apiKey"])}

    @app.post("/api/settings/image-gen/test")
    def test_image_gen(payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        """Test a provider. Codex/xAI → OAuth status; openai-compatible → endpoint probe.
        Uses the submitted key/baseUrl directly (no save needed)."""
        payload = payload or {}
        provider_id = payload.get("provider") or image_providers.DEFAULT_PROVIDER
        if provider_id not in image_providers.IMAGE_PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"unknown provider: {provider_id}")
        current = app_settings.get_json(db(), app_settings.IMAGE_GEN_KEY) or {}
        key = payload.get("apiKey") or current.get("apiKey")
        result = image_providers.test_connection(provider_id, key, base_url=payload.get("baseUrl"))
        ok = bool(result.get("ok", result.get("ready", False)))
        status = "ok" if ok else "fail"
        _audit_fs(user, "settings.image_gen.test", "-", json.dumps({"provider": provider_id, "status": status}))
        return result

    @app.get("/api/settings/video-gen")
    def get_video_gen_settings(user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        cfg = _resolve_video_gen()
        provider = video_providers.get_provider(cfg["provider"])
        return {
            "provider": provider.id,
            "model": cfg.get("model") or (higgsfield.DEFAULT_VIDEO_MODEL if provider.id == "higgsfield" else video_providers.DEFAULT_MODEL),
            "videoPolicy": cfg.get("videoPolicy") or "confirm-credits",
            "maxVideoCredits": cfg.get("maxVideoCredits") if isinstance(cfg.get("maxVideoCredits"), (int, float)) else 50,
            "providers": video_providers.provider_list(),
            "defaultProvider": video_providers.DEFAULT_PROVIDER,
            "status": video_providers.test_connection(provider.id),
        }

    @app.put("/api/settings/video-gen")
    def put_video_gen_settings(payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        payload = payload or {}
        current = _resolve_video_gen()
        provider_id = payload.get("provider") or current["provider"]
        if provider_id not in video_providers.VIDEO_PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"unknown provider: {provider_id}")
        policy = payload.get("videoPolicy") or current.get("videoPolicy") or "confirm-credits"
        if policy not in {"confirm-credits", "allow-with-limit", "disabled"}:
            raise HTTPException(status_code=400, detail="invalid video policy")
        max_video = payload.get("maxVideoCredits", current.get("maxVideoCredits", 50))
        try:
            max_video = max(0, int(max_video))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid max video credits")
        model = payload.get("model") if "model" in payload else current.get("model")
        if not model:
            model = higgsfield.DEFAULT_VIDEO_MODEL if provider_id == "higgsfield" else video_providers.DEFAULT_MODEL
        cfg = {"provider": provider_id, "model": str(model).strip(), "videoPolicy": policy, "maxVideoCredits": max_video}
        app_settings.set_json(db(), app_settings.VIDEO_GEN_KEY, cfg)
        auth_health.invalidate()  # Home Connections card re-checks on its next poll
        if provider_id == "higgsfield":
            hcfg = _resolve_higgsfield_settings()
            hcfg.update({"videoModel": cfg["model"], "videoPolicy": policy, "maxVideoCredits": max_video})
            app_settings.set_json(db(), app_settings.HIGGSFIELD_KEY, hcfg)
        _audit_fs(user, "settings.video_gen", "-", json.dumps({"provider": cfg["provider"], "model": cfg["model"], "videoPolicy": policy, "maxVideoCredits": max_video}))
        return {"ok": True, **cfg, "status": video_providers.test_connection(provider_id)}

    @app.post("/api/settings/video-gen/test")
    def test_video_gen_settings(payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        payload = payload or {}
        provider_id = payload.get("provider") or _resolve_video_gen()["provider"]
        if provider_id not in video_providers.VIDEO_PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"unknown provider: {provider_id}")
        result = video_providers.test_connection(provider_id)
        _audit_fs(user, "settings.video_gen.test", "-", json.dumps({"provider": provider_id, "status": "ok" if result.get("ok") else "fail"}))
        return result

    @app.get("/api/settings/higgsfield")
    def get_higgsfield_settings(user: dict[str, Any] = Depends(current_user)):
        return {"settings": _public_higgsfield_settings(_resolve_higgsfield_settings()), "status": higgsfield.status()}

    @app.put("/api/settings/higgsfield")
    def put_higgsfield_settings(payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        payload = payload or {}
        video_keys = {"videoPolicy", "videoModel", "maxVideoCredits"}
        if video_keys.intersection(payload) and not features.enabled(feature_cfg, features.VIDEO):
            features.require(feature_cfg, features.VIDEO)
        current = _resolve_higgsfield_settings()
        image_policy = payload.get("imagePolicy") or current["imagePolicy"]
        video_policy = payload.get("videoPolicy") or current["videoPolicy"]
        if image_policy not in {"zero-credit-only", "ask-before-credits"}:
            raise HTTPException(status_code=400, detail="invalid image policy")
        if video_policy not in {"confirm-credits", "allow-with-limit", "disabled"}:
            raise HTTPException(status_code=400, detail="invalid video policy")
        max_video = payload.get("maxVideoCredits", current["maxVideoCredits"])
        try:
            max_video = max(0, int(max_video))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid max video credits")
        saved = app_settings.get_json(db(), app_settings.HIGGSFIELD_KEY) or {}
        if not isinstance(saved, dict):
            saved = {}
        cfg = {
            **saved,
            "imagePolicy": image_policy,
            "imageModel": (payload.get("imageModel") or current["imageModel"] or higgsfield.DEFAULT_IMAGE_MODEL).strip(),
        }
        if features.enabled(feature_cfg, features.VIDEO):
            cfg.update({
                "videoPolicy": video_policy,
                "videoModel": (payload.get("videoModel") or current["videoModel"] or "").strip(),
                "maxVideoCredits": max_video,
            })
        app_settings.set_json(db(), app_settings.HIGGSFIELD_KEY, cfg)
        auth_health.invalidate()  # Home Connections card re-checks on its next poll
        audit = {"imagePolicy": cfg["imagePolicy"]}
        if features.enabled(feature_cfg, features.VIDEO):
            audit.update({"videoPolicy": cfg["videoPolicy"], "maxVideoCredits": cfg["maxVideoCredits"]})
        _audit_fs(user, "settings.higgsfield", "-", json.dumps(audit))
        return {"ok": True, "settings": _public_higgsfield_settings(cfg), "status": higgsfield.status()}

    @app.post("/api/settings/higgsfield/test")
    def test_higgsfield_settings(user: dict[str, Any] = Depends(current_user)):
        result = higgsfield.status()
        _audit_fs(user, "settings.higgsfield.test", "-", json.dumps({"status": "ok" if result.get("ready") else "fail"}))
        return result

    @app.get("/api/projects/{slug}/artifacts")
    def list_artifacts(slug: str, since_minutes: int = 1440, user: dict[str, Any] = Depends(current_user)):
        """Typed artifacts recently produced in a project (design/app/page/doc/file) so
        the iterate stage can show a universal Result. since_minutes bounds the window."""
        p = visible_project(slug, user)
        if not p.get("path"):
            return {"artifacts": []}
        start = time.time() - max(1, since_minutes) * 60
        return {"artifacts": scan_project_artifacts(Path(p["path"]), start)}

    @app.get("/api/sessions/{session_id}/artifacts")
    def session_artifacts(session_id: int, user: dict[str, Any] = Depends(current_user)):
        """Artifacts produced BY this session's runs (accumulated) — scopes the iterate
        Result to the iteration's own output instead of the whole project."""
        session_for_user(session_id, user)
        row = db().execute("SELECT produced_artifacts FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return {"artifacts": json.loads((row["produced_artifacts"] if row else None) or "[]")}

    @app.delete("/api/sessions/{session_id}/artifacts")
    def delete_session_artifact(session_id: int, path: str, user: dict[str, Any] = Depends(current_user)):
        session = session_for_user(session_id, user)
        row = db().execute("SELECT produced_artifacts FROM sessions WHERE id = ?", (session_id,)).fetchone()
        artifacts = json.loads((row["produced_artifacts"] if row else None) or "[]")
        if session.get("project_id"):
            p = db().execute("SELECT slug FROM projects WHERE id = ?", (session["project_id"],)).fetchone()
            if p:
                root = _project_root(p["slug"], user)
                try:
                    fsapi.delete(root, path)
                except fsapi.FsError:
                    pass
                _audit_fs(user, "artifact.delete", p["slug"], path)
        kept = [a for a in artifacts if a.get("path") != path]
        db().execute("UPDATE sessions SET produced_artifacts = ? WHERE id = ?", (json.dumps(kept), session_id))
        for m in db().execute("SELECT id, output_links FROM messages WHERE session_id = ? AND output_links != '[]'", (session_id,)).fetchall():
            try:
                links = json.loads(m["output_links"] or "[]")
            except Exception:
                continue
            filtered = [a for a in links if a.get("path") != path]
            if len(filtered) != len(links):
                db().execute("UPDATE messages SET output_links = ? WHERE id = ?", (json.dumps(filtered), m["id"]))
        for ev in db().execute("SELECT id, payload FROM events WHERE session_id = ? AND payload LIKE ?", (session_id, f"%{path}%")).fetchall():
            try:
                payload = json.loads(ev["payload"] or "{}")
            except Exception:
                continue
            links = payload.get("output_links")
            if not isinstance(links, list):
                continue
            filtered = [a for a in links if a.get("path") != path]
            if len(filtered) != len(links):
                payload["output_links"] = filtered
                db().execute("UPDATE events SET payload = ? WHERE id = ?", (json.dumps(payload), ev["id"]))
        return {"ok": True, "path": path}

    # ── Run & preview a project app (managed dev server + proxy) ──────
    @app.get("/api/projects/{slug}/apps")
    def detect_apps(slug: str, user: dict[str, Any] = Depends(current_user)):
        """Scan the project for runnable apps so the user picks one instead of
        guessing the folder/command. Looks for package.json scripts, static
        index.html, Django/Python entry points (depth-limited, skips heavy dirs)."""
        root = _project_root(slug, user)
        SKIP = {"node_modules", ".git", ".venv", "venv", "dist", "build", ".next", "__pycache__", ".cache", "target", ".hermes", ".claude"}
        found: list[dict[str, Any]] = []
        def rel(p: Path) -> str:
            return "" if p == root else str(p.relative_to(root))
        def scan(d: Path, depth: int) -> None:
            if depth > 2 or len(found) >= 25:
                return
            try:
                entries = sorted(d.iterdir(), key=lambda c: c.name.lower())
            except OSError:
                return
            pkg = d / "package.json"
            if pkg.is_file():
                try:
                    import json as _json
                    scripts = (_json.loads(pkg.read_text(encoding="utf-8", errors="ignore")) or {}).get("scripts", {}) or {}
                except Exception:
                    scripts = {}
                script = next((s for s in ("dev", "start", "serve", "preview") if s in scripts), None)
                if script:
                    found.append({"dir": rel(d), "command": f"npm run {script}", "kind": f"node · npm run {script}"})
            elif (d / "manage.py").is_file():
                found.append({"dir": rel(d), "command": "python manage.py runserver 0.0.0.0:$PORT", "kind": "django"})
            elif (d / "app.py").is_file() or (d / "main.py").is_file():
                entry = "app.py" if (d / "app.py").is_file() else "main.py"
                found.append({"dir": rel(d), "command": f"python {entry}", "kind": "python"})
            elif (d / "index.html").is_file():
                found.append({"dir": rel(d), "command": "python3 -m http.server $PORT", "kind": "static · index.html"})
            for c in entries:
                try:
                    if c.is_dir() and c.name not in SKIP and not c.name.startswith("."):
                        scan(c, depth + 1)
                except OSError:
                    pass
        scan(root, 0)
        return {"apps": found}

    @app.post("/api/projects/{slug}/app/start")
    async def app_start(slug: str, payload: AppStartRequest, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        cwd = root
        if payload.dir:
            try:
                cwd = fsapi.resolve_in_project(root, payload.dir)
            except fsapi.FsError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not cwd.is_dir():
                raise HTTPException(status_code=400, detail="folder not found")
        await app.state.app_manager.start(slug, str(cwd), payload.command, int(payload.port or 5180))
        # Provision the remote-preview subdomain in the background (best-effort; app
        # start never waits on / fails from Cloudflare). No-op if CF isn't configured.
        if cf_hostnames.configured(app.state.config):
            asyncio.create_task(cf_hostnames.provision(app.state.config, slug))
        _audit_fs(user, "app.start", slug, f"{payload.dir or '.'}: {payload.command}")
        return {"ok": True}

    @app.post("/api/projects/{slug}/app/stop")
    async def app_stop(slug: str, user: dict[str, Any] = Depends(current_user)):
        _project_root(slug, user)
        await app.state.app_manager.stop(slug)
        if cf_hostnames.configured(app.state.config):
            asyncio.create_task(cf_hostnames.deprovision(app.state.config, slug))
        return {"ok": True}

    @app.get("/api/projects/{slug}/app/status")
    def app_status(slug: str, user: dict[str, Any] = Depends(current_user)):
        _project_root(slug, user)
        return app.state.app_manager.status(slug)

    _HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host"}

    @app.api_route("/api/appview/{token}/{slug}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def app_view(token: str, slug: str, path: str, request: Request):
        # Proxy to the project's running app. Token in path so the iframe + its
        # relative assets authenticate (same pattern as file preview).
        user = user_from_token_query(token)
        _project_root(slug, user)  # access check
        port = app.state.app_manager.port(slug)
        if not port:
            raise HTTPException(status_code=503, detail="app not running")
        url = f"http://127.0.0.1:{port}/{path}"
        fwd = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                up = await client.request(request.method, url, params=request.query_params, content=await request.body(), headers=fwd)
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="app not reachable yet") from None
        out = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}
        return Response(content=up.content, status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))

    def _rewrite_video_studio_text(text: str, prefix: str) -> str:
        # HyperFrames Studio is served as a root app and uses absolute /assets and
        # /api paths. Inside Proxima it lives behind a project-scoped proxy.
        p = prefix.rstrip("/")
        text = re.sub(r'(src|href)="(/(?!api/video-studio/)[^"]*)"', lambda m: f'{m.group(1)}="{p}{m.group(2)}"', text)
        text = re.sub(r'url\((/(?!api/video-studio/)[^)]+)\)', lambda m: f'url({p}{m.group(1)})', text)
        for quote in ('"', "'", "`"):
            text = text.replace(f"{quote}/api/", f"{quote}{p}/api/")
            text = text.replace(f"{quote}{p}/api/video-studio/", f"{quote}/api/video-studio/")
            text = text.replace(f"{quote}/assets/", f"{quote}{p}/assets/")
            text = text.replace(f"{quote}/favicon.svg", f"{quote}{p}/favicon.svg")
        return text

    async def _video_studio_proxy(token: str, slug: str, video_id: str, path: str, request: Request):
        features.require(feature_cfg, features.VIDEO)
        user = user_from_token_query(token)
        root = _project_root(slug, user)
        d = fsapi.resolve_in_project(root, f"artifacts/video/{video_id}")
        if not (d / "index.html").is_file():
            raise HTTPException(status_code=404, detail="video project not found")
        port = app.state.app_manager.port(slug)
        if not port:
            raise HTTPException(status_code=503, detail="HyperFrames Studio is not running")
        url = f"http://127.0.0.1:{port}/{path}"
        fwd = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                up = await client.request(request.method, url, params=request.query_params, content=await request.body(), headers=fwd)
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="HyperFrames Studio not reachable yet") from None
        out = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}
        media = up.headers.get("content-type") or ""
        content = up.content
        if "text/html" in media or "javascript" in media or path.endswith((".js", ".mjs", ".css")):
            prefix = f"/api/video-studio/{token}/{slug}/{video_id}"
            content = _rewrite_video_studio_text(up.text, prefix).encode("utf-8")
            out.pop("content-length", None)
        return Response(content=content, status_code=up.status_code, headers=out, media_type=media)

    @app.api_route("/api/video-studio/{token}/{slug}/{video_id}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_root(token: str, slug: str, video_id: str, request: Request):
        return await _video_studio_proxy(token, slug, video_id, "", request)

    @app.api_route("/api/video-studio/{token}/{slug}/{video_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_view(token: str, slug: str, video_id: str, path: str, request: Request):
        return await _video_studio_proxy(token, slug, video_id, path, request)

    @app.post("/api/projects/{slug}/fs/mkdir")
    def project_mkdir(slug: str, payload: FsPathRequest, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            fsapi.mkdir(root, payload.path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_fs(user, "fs.mkdir", slug, payload.path)
        return {"ok": True, "path": payload.path}

    @app.post("/api/projects/{slug}/fs/rename")
    def project_rename(slug: str, payload: FsRenameRequest, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            fsapi.rename(root, payload.from_, payload.to)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_fs(user, "fs.rename", slug, f"{payload.from_} -> {payload.to}")
        return {"ok": True}

    @app.delete("/api/projects/{slug}/fs")
    def project_delete(slug: str, path: str, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            fsapi.delete(root, path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit_fs(user, "fs.delete", slug, path)
        return {"ok": True, "path": path}

    @app.get("/api/projects/{slug}/raw")
    def project_raw(slug: str, path: str, user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            target = fsapi.resolve_in_project(root, path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        return FileResponse(str(target), filename=target.name)

    @app.get("/api/preview/{token}/{slug}/{file_path:path}")
    def project_preview(token: str, slug: str, file_path: str):
        # Serve a project file inline for live preview (e.g. rendering a built
        # site in an <iframe>). The token sits in the path — not a header — so the
        # iframe AND its relative asset requests (styles.css, script.js) all carry
        # it (same exposure as the SSE ?token= stream). Path-jailed to the project.
        user = user_from_token_query(token)
        root = _project_root(slug, user)
        try:
            target = fsapi.resolve_in_project(root, file_path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        return FileResponse(str(target))  # inline (no attachment) so HTML/CSS/JS render

    async def _proxy_video_studio_project_api_for(project_slug: str, video_id: str, path: str, request: Request, *, stream: bool = False):
        features.require(feature_cfg, features.VIDEO)
        port = app.state.app_manager.port(project_slug)
        if not port:
            raise HTTPException(status_code=503, detail="HyperFrames Studio API is not running")
        url = f"http://127.0.0.1:{port}/api/projects/{video_id}/{path}"
        fwd = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        try:
            if stream:
                client = httpx.AsyncClient(timeout=None, follow_redirects=False)
                req = client.build_request(request.method, url, params=request.query_params, content=await request.body(), headers=fwd)
                up = await client.send(req, stream=True)
            else:
                async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                    up = await client.request(request.method, url, params=request.query_params, content=await request.body(), headers=fwd)
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="HyperFrames Studio API not reachable yet") from None
        out = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}
        if stream:
            async def body():
                try:
                    async for chunk in up.aiter_bytes():
                        yield chunk
                finally:
                    await up.aclose()
                    await client.aclose()
            return StreamingResponse(body(), status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))
        return Response(content=up.content, status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))

    async def _proxy_video_studio_project_api(studio_id: str, path: str, request: Request, user: dict[str, Any], *, stream: bool = False):
        project_slug, video_id, _ = _video_studio_dir(studio_id, user)
        return await _proxy_video_studio_project_api_for(project_slug, video_id, path, request, stream=stream)

    @app.api_route("/api/projects/{studio_id}/files/{file_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_files_api(studio_id: str, file_path: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, f"files/{file_path}", request, user)

    @app.api_route("/api/projects/{studio_id}/preview", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_preview_root_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, "preview", request, user)

    @app.api_route("/api/projects/{studio_id}/preview/{preview_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_preview_api(studio_id: str, preview_path: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, f"preview/{preview_path}", request, user)

    @app.api_route("/api/projects/{studio_id}/lint", methods=["GET", "POST"])
    async def video_studio_lint_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, "lint", request, user)

    @app.api_route("/api/projects/{studio_id}/gsap-mutations/{mutation_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_gsap_mutations_api(studio_id: str, mutation_path: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, f"gsap-mutations/{mutation_path}", request, user)

    @app.api_route("/api/projects/{studio_id}/file-mutations/{mutation_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def video_studio_file_mutations_api(studio_id: str, mutation_path: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, f"file-mutations/{mutation_path}", request, user)

    @app.api_route("/api/projects/{studio_id}/duplicate-file", methods=["POST"])
    async def video_studio_duplicate_file_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, "duplicate-file", request, user)

    @app.api_route("/api/projects/{studio_id}/registry/install", methods=["POST"])
    async def video_studio_registry_install_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, "registry/install", request, user)

    @app.api_route("/api/projects/{studio_id}/storyboard", methods=["GET", "POST"])
    async def video_studio_storyboard_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, "storyboard", request, user)

    @app.api_route("/api/projects/{studio_id}/renders", methods=["GET"])
    async def video_studio_renders_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, "renders", request, user)

    @app.api_route("/api/projects/{studio_id}/renders/file/{filename:path}", methods=["GET"])
    async def video_studio_render_file_api(studio_id: str, filename: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_studio_project_api(studio_id, f"renders/file/{filename}", request, user, stream=True)

    @app.api_route("/api/projects/{studio_id}/render", methods=["POST"])
    async def video_studio_render_api(studio_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        project_slug, video_id, _ = _video_studio_dir(studio_id, user)
        response = await _proxy_video_studio_project_api_for(project_slug, video_id, "render", request)
        if 200 <= response.status_code < 300:
            try:
                body = json.loads(bytes(response.body).decode("utf-8"))
                job_id = str(body.get("jobId") or "")
                if job_id:
                    video_render_jobs[job_id] = project_slug
            except Exception:
                pass
        return response

    async def _proxy_video_render_job(job_id: str, path: str, request: Request, user: dict[str, Any], *, stream: bool = False):
        features.require(feature_cfg, features.VIDEO)
        project_slug = video_render_jobs.get(job_id)
        if not project_slug:
            raise HTTPException(status_code=404, detail="render job not found")
        _project_root(project_slug, user)
        port = app.state.app_manager.port(project_slug)
        if not port:
            raise HTTPException(status_code=503, detail="HyperFrames Studio API is not running")
        suffix = f"/{path.strip('/')}" if path else ""
        url = f"http://127.0.0.1:{port}/api/render/{job_id}{suffix}"
        fwd = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        try:
            if stream:
                client = httpx.AsyncClient(timeout=None, follow_redirects=False)
                req = client.build_request(request.method, url, params=request.query_params, content=await request.body(), headers=fwd)
                up = await client.send(req, stream=True)
            else:
                async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                    up = await client.request(request.method, url, params=request.query_params, content=await request.body(), headers=fwd)
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="HyperFrames Studio API not reachable yet") from None
        out = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}
        if stream:
            async def body():
                try:
                    async for chunk in up.aiter_bytes():
                        yield chunk
                finally:
                    await up.aclose()
                    await client.aclose()
            return StreamingResponse(body(), status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))
        return Response(content=up.content, status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))

    @app.api_route("/api/render/{job_id}", methods=["GET", "DELETE"])
    async def video_studio_render_job_api(job_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        response = await _proxy_video_render_job(job_id, "", request, user)
        if request.method == "DELETE" and 200 <= response.status_code < 300:
            video_render_jobs.pop(job_id, None)
        return response

    @app.get("/api/render/{job_id}/progress")
    async def video_studio_render_progress_api(job_id: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        return await _proxy_video_render_job(job_id, "progress", request, user, stream=True)

    @app.get("/api/events")
    async def hyperframes_events(user: dict[str, Any] = Depends(current_user)):
        features.require(feature_cfg, features.VIDEO)
        async def stream():
            yield ": proxima hyperframes events\n\n"
            while True:
                await asyncio.sleep(25)
                yield ": keepalive\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

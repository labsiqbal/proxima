"""Design Studio image routes.

Extracted from routes/files.py: the design-scene + image-generation endpoints are
Design Studio's own surface (gated by PROXIMA_FEATURE_DESIGN_STUDIO). They reach
the shared filesystem + provider settings, but live in their own module so the
feature can be reasoned about (and eventually toggled) as a unit.
"""
from __future__ import annotations

import json
import mimetypes
import time
from typing import Any

from fastapi import Depends, HTTPException

from .. import design_scenes
from .. import features
from .. import fsapi
from .. import higgsfield
from .. import image_providers
from .. import media_settings
from ..schemas import ImageGenRequest


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    _project_root = deps["_project_root"]

    def _audit_fs(user: dict[str, Any], action: str, slug: str, path: str) -> None:
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, ?, 'project', ?, ?)",
            (user["id"], action, slug, json.dumps({"path": path})),
        )

    @app.post("/api/projects/{slug}/designs/from-image")
    def design_from_image(slug: str, payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        """Seed a new Design Studio scene containing an existing project image as a
        full-bleed layer — the 'edit this image in Design Studio' bridge from chat."""
        features.require(cfg, features.DESIGN_STUDIO)
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
        features.require(cfg, features.DESIGN_STUDIO)
        root = _project_root(slug, user)
        prov = media_settings.resolve_image_gen(db())
        # Source/reference images — the multi-image list wins over the single `image`.
        src_paths = payload.images if payload.images else ([payload.image] if payload.image else [])
        sources: list[tuple[bytes, str]] = []
        for rel in src_paths:
            try:
                src = fsapi.resolve_in_project(root, rel)
                if not src.is_file():
                    raise fsapi.FsError(f"source image does not exist: {rel}")
                sources.append((src.read_bytes(), mimetypes.guess_type(src.name)[0] or "application/octet-stream"))
            except fsapi.FsError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=400, detail=f"cannot read source image: {exc.strerror}") from exc
        caps = image_providers.get_provider(prov.get("provider")).capabilities or {}
        # Edit/reference requests need an edit-capable provider. When the selected one
        # is text-to-image only, fall back to xAI OAuth (single image) if connected.
        if sources and caps.get("imageEdit") is False:
            if image_providers.xai_oauth_ready().get("ready"):
                prov = {**prov, "provider": "xai-oauth", "apiKey": None, "baseUrl": None, "model": None}
                caps = image_providers.get_provider("xai-oauth").capabilities or {}
            else:
                raise HTTPException(status_code=400, detail="The selected image provider is text-to-image only and no edit-capable fallback is connected. Switch the provider in Settings → Image generation (e.g. xAI OAuth) to edit or use reference images.")
        # Only referenceImages-capable providers get multiple images; others use the first.
        if len(sources) > 1 and not caps.get("referenceImages"):
            sources = sources[:1]
        image_bytes = sources[0][0] if sources else None
        image_mime = sources[0][1] if sources else None
        extra_images = sources[1:] or None
        target = fsapi.resolve_in_project(root, f"artifacts/design/_assets/gen-{int(time.time())}.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        i = 1
        while target.exists():
            target = target.parent / f"gen-{int(time.time())}-{i}.png"; i += 1
        model = payload.model or prov.get("model")
        if not model and prov.get("provider") in {"auto", "higgsfield"}:
            model = media_settings.resolve_higgsfield_settings(db()).get("imageModel")
        try:
            raw = image_providers.generate(
                prov["provider"], prov.get("apiKey"),
                prompt=payload.prompt,
                model=model,
                size=payload.size,
                image_bytes=image_bytes,
                image_mime=image_mime,
                extra_images=extra_images,
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
        features.require(cfg, features.DESIGN_STUDIO)
        prov = media_settings.resolve_image_gen(db())
        spec = image_providers.get_provider(prov["provider"])
        if spec.kind == "codex":
            return {"models": [], "configured": True, "kind": "codex"}
        if spec.kind == "auto":
            return {"models": [], "configured": True, "kind": "auto", "model": prov.get("model")}
        if spec.kind == "higgsfield":
            return {"models": [], "configured": bool(higgsfield.status().get("ready")), "kind": "higgsfield", "model": prov.get("model")}
        return {"models": [], "configured": bool(prov.get("apiKey")), "kind": "http", "model": prov.get("model")}

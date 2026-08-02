"""Design Studio image routes.

Extracted from routes/files.py: the design-scene + image-generation endpoints are
Design Studio's own surface. They reach the shared filesystem + provider
settings, but live in their own module so the feature can be reasoned about as
a unit.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import time
from contextlib import contextmanager
from typing import Any

from fastapi import Depends, HTTPException

from .. import brand_extract
from .. import container_registry
from .. import design_scenes
from .. import file_targets
from .. import fsapi
from .. import image_providers
from .. import layout_map
from .. import media_settings
from .. import moodboard
from ..schemas import ImageGenRequest


def register(app, deps):
    db = deps["db"]
    current_user = deps["current_user"]
    _ops_root = deps["_ops_root"]
    _project_root = deps["_project_root"]
    profile_for_user = deps["profile_for_user"]
    visible_project = deps["visible_project"]

    @contextmanager
    def _project_mutation(slug: str, user: dict[str, Any]):
        project = visible_project(slug, user)
        with container_registry.container_mutation_lock(db(), project):
            yield project

    def _audit_fs(user: dict[str, Any], action: str, slug: str, path: str) -> None:
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, ?, 'project', ?, ?)",
            (user["id"], action, slug, json.dumps({"path": path})),
        )

    def _moodboard_store(slug: str, user: dict[str, Any]) -> moodboard.MoodboardStore:
        """The project's moodboard location, resolved through the layout map
        (prune #138) - container-relative paths, real folders only."""
        project = visible_project(slug, user)
        try:
            return moodboard.store_for_layout(
                layout_map.project_layout(db(), project)
            )
        except container_registry.ContainerBoundaryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/design/moodboard")
    def list_moodboard(slug: str, user: dict[str, Any] = Depends(current_user)):
        """List this project's curated visual references."""
        return {"items": moodboard.read_items(_moodboard_store(slug, user))}

    @app.post("/api/projects/{slug}/design/moodboard")
    def add_moodboard_item(
        slug: str,
        payload: dict[str, Any] | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Add a URL preview or an already-uploaded screenshot to the Moodboard.

        URL preview fetches are best-effort. A reachable page without usable OG
        metadata, or a transient network failure, still produces a fallback card.
        """
        data = payload or {}
        raw_url = str(data.get("url") or "").strip()
        image_path = str(data.get("imagePath") or "").strip()
        if not raw_url and not image_path:
            raise HTTPException(
                status_code=400, detail="Give a URL or an uploaded screenshot."
            )
        item_id = f"mb-{int(time.time() * 1000)}-{secrets.token_hex(3)}"
        warning = ""
        title = str(data.get("title") or "").strip()[:240]
        site_name = str(data.get("siteName") or "").strip()[:120]
        favicon_url = ""
        url = ""
        preview_image: tuple[bytes | None, str] | None = None
        if raw_url:
            try:
                preview = moodboard.fetch_link_preview(raw_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            url = preview["url"]
            title = title or preview["title"]
            site_name = site_name or preview["siteName"]
            favicon_url = preview["faviconUrl"]
            warning = preview["warning"]
            preview_image = (
                preview.get("imageBytes"),
                str(preview.get("imageMime") or ""),
            )
        else:
            with _project_mutation(slug, user):
                store = _moodboard_store(slug, user)
                try:
                    source = moodboard.validate_local_image(store, image_path)
                except (ValueError, fsapi.FsError, OSError) as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                title = title or source.stem
                site_name = site_name or "Uploaded screenshot"
        created = moodboard.now_iso()
        item = {
            "id": item_id,
            "kind": "link" if url else "upload",
            "url": url or None,
            "imagePath": image_path or None,
            "title": title,
            "siteName": site_name or "Reference",
            "faviconUrl": favicon_url or None,
            "note": str(data.get("note") or "").strip()[:2000],
            "tags": moodboard.normalize_tags(data.get("tags")),
            "useAsReference": bool(data.get("useAsReference", False)),
            "createdAt": created,
            "updatedAt": created,
        }
        with _project_mutation(slug, user):
            store = _moodboard_store(slug, user)
            if preview_image is not None:
                image_path = (
                    moodboard.cache_preview_image(
                        store,
                        item_id,
                        preview_image[0],
                        preview_image[1],
                    )
                    or ""
                )
                item["imagePath"] = image_path or None
            try:
                moodboard.append_item(store, item)
            except ValueError as exc:
                if (
                    image_path
                    and image_path.startswith(f"{store.image_dir_rel}/")
                    and raw_url
                ):
                    try:
                        store.resolve(image_path).unlink(missing_ok=True)
                    except (OSError, fsapi.FsError):
                        pass
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            _audit_fs(user, "design.moodboard.add", slug, store.store_rel)
        return {"item": item, "warning": warning or None}

    @app.patch("/api/projects/{slug}/design/moodboard/{item_id}")
    def update_moodboard_item(
        slug: str,
        item_id: str,
        payload: dict[str, Any] | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Edit a Moodboard note/tags or select it for design-run context."""
        data = payload or {}
        patch: dict[str, Any] = {}
        if "note" in data:
            patch["note"] = str(data.get("note") or "").strip()[:2000]
        if "tags" in data:
            patch["tags"] = moodboard.normalize_tags(data.get("tags"))
        if "useAsReference" in data:
            patch["useAsReference"] = bool(data.get("useAsReference"))
        if not patch:
            raise HTTPException(
                status_code=400, detail="No editable Moodboard fields were provided."
            )
        with _project_mutation(slug, user):
            store = _moodboard_store(slug, user)
            item = moodboard.patch_item(store, item_id, patch)
            if item is not None:
                _audit_fs(
                    user,
                    "design.moodboard.update",
                    slug,
                    store.store_rel,
                )
        if item is None:
            raise HTTPException(status_code=404, detail="Moodboard item not found.")
        return {"item": item}

    @app.delete("/api/projects/{slug}/design/moodboard/{item_id}")
    def remove_moodboard_item(
        slug: str, item_id: str, user: dict[str, Any] = Depends(current_user)
    ):
        """Delete a Moodboard card and its private cached/uploaded image."""
        with _project_mutation(slug, user):
            store = _moodboard_store(slug, user)
            item = moodboard.delete_item(store, item_id)
            if item is not None:
                _audit_fs(
                    user,
                    "design.moodboard.delete",
                    slug,
                    store.store_rel,
                )
        if item is None:
            raise HTTPException(status_code=404, detail="Moodboard item not found.")
        return {"ok": True, "id": item_id}

    @app.post("/api/projects/{slug}/design/brand-guide")
    def generate_brand_guide(
        slug: str,
        payload: dict[str, Any] | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Kick off an agent run that synthesises a project's brand guideline into
        <project>/design.md from reference URLs (crawled here), uploaded reference
        images (vision), and free-text notes. The generated design.md is then
        auto-injected into every future design run (see wiki_memory.read_design_guidelines).
        Async, like /design: returns {run_id, session_id}; the client watches the run
        and reloads design.md when it finishes."""
        data = payload or {}
        urls = [
            u for u in (data.get("urls") or []) if isinstance(u, str) and u.strip()
        ][:8]
        notes = (data.get("notes") or "").strip()[:4000]
        image_rels: list[str] = []
        # Reference images are container-relative real paths (prune #138); the
        # worker's vision loader resolves them with the same semantics.
        root = _project_root(slug, user)
        for rel in (data.get("imagePaths") or [])[:6]:
            if not isinstance(rel, str):
                continue
            try:
                if fsapi.resolve_in_project(root, rel).is_file():
                    image_rels.append(rel)
            except fsapi.FsError:
                continue
        if not urls and not image_rels and not notes:
            raise HTTPException(
                status_code=400,
                detail="Give at least one reference URL, image, or a note to generate from.",
            )

        digests = [brand_extract.fetch_url_digest(u) for u in urls]
        parts = [
            "You are a brand/design director. Study the references below and write a concise, "
            "OPINIONATED brand guideline to a file named `design.md` at the ROOT of this project "
            "(overwrite it if it exists). This file steers every future design in Design Studio.",
            "",
            "Cover, in this order, as short markdown sections:",
            "1. **Essence** — one line: what this brand feels like.",
            "2. **Palette** — 3–6 named roles with exact hex (background / ink / accent / muted / …).",
            "3. **Typography** — display + body pairing (real font names) and how to use them.",
            "4. **Imagery & texture** — photo/illustration direction, mood, what to avoid.",
            "5. **Tone of voice** — 3–5 adjectives + a do/don't for copy.",
            "6. **Do / Don't** — a handful of concrete rules a designer can follow.",
            "Keep it tight and usable — floors and direction, not an essay. Infer a coherent system "
            "even from thin references; where the references conflict, pick one deliberate direction.",
        ]
        if digests:
            parts += [
                "",
                "## Reference URLs (crawled)",
                *[brand_extract.digest_to_markdown(d) for d in digests],
            ]
        if image_rels:
            parts += [
                "",
                "## Reference images",
                "The attached image(s) are visual references — read their palette, mood, composition, and type.",
            ]
        if notes:
            parts += ["", "## Owner's notes / preferences", notes]
        prompt = "\n".join(parts)
        if image_rels:
            # The worker strips this marker and delivers the images to the agent as vision.
            prompt += "\n\n⟦VISION:" + "|".join(image_rels) + "⟧"

        project = visible_project(slug, user)
        profile = profile_for_user(data.get("profile_id"), user)
        title = "Brand guide"
        cur = db().execute(
            "INSERT INTO sessions(title, project_id, owner_user_id, profile_id, runner_id, visibility, mode) VALUES (?, ?, ?, ?, ?, 'private', 'chat')",
            (title, project["id"], user["id"], profile["id"], profile["runner_id"]),
        )
        session_id = int(cur.lastrowid)
        db().execute(
            "INSERT INTO messages(session_id, role, content, author) VALUES (?, 'user', ?, ?)",
            (
                session_id,
                "Generate design.md brand guideline from the provided references.",
                user["username"],
            ),
        )
        run = db().execute(
            "INSERT INTO runs(session_id, project_id, user_id, profile_id, runner_id, status, prompt, model, hermes_home, kind) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, 'brand_guide')",
            (
                session_id,
                project["id"],
                user["id"],
                profile["id"],
                profile["runner_id"],
                prompt,
                profile["default_model"],
                profile["hermes_home"],
            ),
        )
        run_id = int(run.lastrowid)
        app.state.worker.add_event(
            run_id,
            session_id,
            project["id"],
            "run.queued",
            {"runner": profile["runner_id"], "kind": "brand_guide"},
        )
        _audit_fs(user, "design.brand_guide", slug, "design.md")
        return {
            "run_id": run_id,
            "session_id": session_id,
            "urls": [{"url": d["url"], "ok": d.get("ok", False)} for d in digests],
        }

    @app.post("/api/projects/{slug}/designs/from-image")
    def design_from_image(
        slug: str,
        payload: dict[str, Any] | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Seed a new Design Studio scene containing an existing project image as a
        full-bleed layer — the 'edit this image in Design Studio' bridge from chat."""
        payload = payload or {}
        rel = str(payload.get("path") or "").strip()
        target = payload.get("target", "")
        if target is None:
            target = ""
        if not rel and target == "":
            raise HTTPException(status_code=400, detail="path is required")
        with _project_mutation(slug, user) as project:
            try:
                layout = layout_map.project_layout(db(), project)
                resolved = file_targets.resolve_request(
                    db(),
                    project,
                    path=rel,
                    target=target,
                )
            except (
                container_registry.ContainerBoundaryError,
                file_targets.FileTargetError,
            ) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            source = resolved.path
            # Sources stay inside the Ops workspace or the project's mapped
            # artifacts/uploads areas (layout map, prune #138) - never
            # arbitrary repo files.
            allowed_roots = [
                layout.ops_root,
                layout.dir("artifacts"),
                layout.dir("uploads"),
            ]
            if resolved.locator.area.kind != "ops" and not any(
                source == allowed or allowed in source.parents
                for allowed in allowed_roots
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Design Studio image sources must belong to the Ops "
                        "workspace or the project's artifacts/uploads areas"
                    ),
                )
            rel = resolved.locator.path
            if not source.is_file():
                raise HTTPException(status_code=404, detail=f"file not found: {rel}")
            design_id, scene = design_scenes.scene_for_image(
                rel,
                design_scenes.image_dims(source),
                payload.get("title"),
                resolved.locator.payload(),
            )
            design_rel = f"{layout.rel_paths['artifacts']}/design/{design_id}"
            d = fsapi.resolve_in_project(layout.container_root, design_rel)
            d.mkdir(parents=True, exist_ok=True)
            (d / "scene.json").write_text(json.dumps(scene, indent=2), encoding="utf-8")
            _audit_fs(user, "design.from_image", slug, f"{rel} -> {design_rel}")
        return {"ok": True, "id": design_id, "title": scene["title"], "path": design_rel}

    @app.post("/api/projects/{slug}/design/image")
    async def design_image(
        slug: str,
        payload: ImageGenRequest,
        user: dict[str, Any] = Depends(current_user),
    ):
        """Generate (text→image) or edit (image+prompt→image) via the configured
        provider (Settings); save the result into the project's shared design
        asset library and return its path."""
        prov = media_settings.resolve_image_gen(db())
        # Source/reference images — the multi-image list wins over the single `image`.
        src_paths = (
            payload.images
            if payload.images
            else ([payload.image] if payload.image else [])
        )
        sources: list[tuple[bytes, str]] = []
        with _project_mutation(slug, user):
            # Asset paths are container-relative real paths (prune #138).
            root = _project_root(slug, user)
            for rel in src_paths:
                try:
                    src = fsapi.resolve_in_project(root, rel)
                    if not src.is_file():
                        raise fsapi.FsError(f"source image does not exist: {rel}")
                    sources.append(
                        (
                            src.read_bytes(),
                            mimetypes.guess_type(src.name)[0]
                            or "application/octet-stream",
                        )
                    )
                except fsapi.FsError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                except OSError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"cannot read source image: {exc.strerror}",
                    ) from exc
        caps = image_providers.get_provider(prov.get("provider")).capabilities or {}
        # Edit/reference requests need an edit-capable provider. When the selected one
        # is text-to-image only, fall back to xAI OAuth (single image) if connected.
        if sources and caps.get("imageEdit") is False:
            if image_providers.xai_oauth_ready().get("ready"):
                prov = {
                    **prov,
                    "provider": "xai-oauth",
                    "apiKey": None,
                    "baseUrl": None,
                    "model": None,
                }
                caps = image_providers.get_provider("xai-oauth").capabilities or {}
            else:
                raise HTTPException(
                    status_code=400,
                    detail="The selected image provider is text-to-image only and no edit-capable fallback is connected. Switch the provider in Settings → Image generation (e.g. xAI OAuth) to edit or use reference images.",
                )
        # Only referenceImages-capable providers get multiple images; others use the first.
        if len(sources) > 1 and not caps.get("referenceImages"):
            sources = sources[:1]
        image_bytes = sources[0][0] if sources else None
        image_mime = sources[0][1] if sources else None
        extra_images = sources[1:] or None
        model = payload.model or prov.get("model")
        if not model and prov.get("provider") in {"auto", "higgsfield"}:
            model = media_settings.resolve_higgsfield_settings(db()).get("imageModel")
        try:
            raw = image_providers.generate(
                prov["provider"],
                prov.get("apiKey"),
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
            raise HTTPException(
                status_code=502, detail="provider returned no image data"
            )
        # Every provider returns bytes — persist them here (the out_path shortcut was
        # dead: generate() never wrote files itself). The asset library lives in
        # the project's mapped artifacts location (layout map, prune #138) and
        # the returned path is container-relative.
        with _project_mutation(slug, user) as project:
            try:
                layout = layout_map.project_layout(db(), project)
            except container_registry.ContainerBoundaryError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            assets_rel = f"{layout.rel_paths['artifacts']}/design/_assets"
            target = fsapi.resolve_in_project(
                layout.container_root, f"{assets_rel}/gen-{int(time.time())}.png"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            i = 1
            while target.exists():
                target = target.parent / f"gen-{int(time.time())}-{i}.png"
                i += 1
            target.write_bytes(raw)
            rel = f"{assets_rel}/{target.name}"
            _audit_fs(user, "design.image", slug, rel)
        return {"path": rel, "name": target.name}

    @app.get("/api/projects/{slug}/design/image-models")
    def design_image_models(slug: str, user: dict[str, Any] = Depends(current_user)):
        """For the codex provider there's no static model list (login-based); for
        an openai-compatible endpoint we report configured + the saved model."""
        prov = media_settings.resolve_image_gen(db())
        spec = image_providers.get_provider(prov["provider"])
        if spec.kind == "codex":
            return {"models": [], "configured": True, "kind": "codex"}
        if spec.kind == "auto":
            return {
                "models": [],
                "configured": True,
                "kind": "auto",
                "model": prov.get("model"),
            }
        if spec.kind == "higgsfield":
            readiness = image_providers.readiness_snapshot(
                higgsfield_cli=True,
            )
            hstatus = readiness["higgsfield"] or {}
            return {
                "models": [],
                "configured": bool(hstatus.get("ready")),
                "kind": "higgsfield",
                "model": prov.get("model"),
            }
        return {
            "models": [],
            "configured": bool(prov.get("apiKey")),
            "kind": "http",
            "model": prov.get("model"),
        }

"""File, design-image, artifact, and app-runner routes for the Proxima API.

Extracted via the register() pattern — handler bodies verbatim. No behavior change.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .. import fsapi
from .. import app_settings
from .. import auth_health
from .. import higgsfield
from .. import image_providers
from .. import media_settings
from .. import cf_hostnames
from ..artifacts import scan_project_artifacts, update_produced_artifacts
from ..schemas import (
    AppStartRequest, FileWriteRequest, FsPathRequest, FsRenameRequest,
)


def register(app, deps):
    db = deps["db"]
    cfg = deps["cfg"]
    current_user = deps["current_user"]
    visible_project = deps["visible_project"]
    session_for_user = deps["session_for_user"]
    _project_root = deps["_project_root"]

    def _audit_fs(user: dict[str, Any], action: str, slug: str, path: str) -> None:
        db().execute(
            "INSERT INTO audit_log(actor_user_id, action, target_type, target_id, metadata) VALUES (?, ?, 'project', ?, ?)",
            (user["id"], action, slug, json.dumps({"path": path})),
        )

    @app.get("/api/projects/{slug}/tree")
    def project_tree(slug: str, path: str = "", user: dict[str, Any] = Depends(current_user)):
        root = _project_root(slug, user)
        try:
            return {"path": path, "entries": fsapi.list_tree(root, path)}
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/reference-files")
    def project_reference_files(
        slug: str,
        limit: int = Query(default=fsapi.REFERENCE_MAX_RESULTS, ge=1, le=fsapi.REFERENCE_MAX_RESULTS),
        user: dict[str, Any] = Depends(current_user),
    ):
        """Safe, path-only project file index for @-reference autocomplete."""
        root = _project_root(slug, user)
        files, truncated = fsapi.list_reference_files(root, limit=limit)
        return {"files": files, "truncated": truncated}

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
    async def project_upload(slug: str, file: UploadFile = File(...), dir: str = "uploads", user: dict[str, Any] = Depends(current_user)):
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
        # Stream from UploadFile's spool instead of copying the whole upload into
        # RAM. Exclusive creation also makes same-name concurrent uploads de-dupe
        # safely instead of racing between exists() and write_bytes().
        stem, suffix, index = target.stem, target.suffix, 0
        max_bytes = int(cfg.get("max_upload_bytes") or 100 * 1024 * 1024)
        while True:
            candidate = target if index == 0 else target.parent / f"{stem}-{index}{suffix}"
            try:
                written = 0
                with candidate.open("xb") as output:
                    while chunk := await file.read(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=f"upload exceeds {max_bytes // (1024 * 1024)} MB limit",
                            )
                        output.write(chunk)
                target = candidate
                break
            except FileExistsError:
                index += 1
            except HTTPException:
                candidate.unlink(missing_ok=True)
                raise
            except OSError as exc:
                candidate.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail=f"cannot write upload: {exc.strerror}") from exc
        rel = f"{folder}/{target.name}"
        _audit_fs(user, "file.upload", slug, rel)
        return {"path": rel, "name": target.name}

    # ── Image-generation provider settings ────────────────────────────────

    def _resolve_image_gen() -> dict[str, Any]:
        return media_settings.resolve_image_gen(db())

    def _resolve_higgsfield_settings() -> dict[str, Any]:
        return media_settings.resolve_higgsfield_settings(db())

    @app.get("/api/settings/permissions")
    def get_permission_settings(user: dict[str, Any] = Depends(current_user)):
        """Auto-approve toggle: when on, agent permission prompts are approved
        automatically (no cards). Default OFF; trusted owners may opt in."""
        return {"auto_approve": app_settings.get_setting(db(), "auto_approve_permissions", "0") == "1"}

    @app.put("/api/settings/permissions")
    def set_permission_settings(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        on = bool(payload.get("auto_approve"))
        app_settings.set_setting(db(), "auto_approve_permissions", "1" if on else "0")
        return {"auto_approve": on}

    @app.get("/api/settings/runs")
    def get_run_settings(user: dict[str, Any] = Depends(current_user)):
        """Turn quota (T5): the per-turn run timeout as a first-class in-app
        setting, plus the (config-only) automatic continuation limit."""
        cfg = app.state.config
        return {
            "run_timeout_seconds": app_settings.get_run_timeout_seconds(db(), cfg),
            "default_run_timeout_seconds": int(cfg.get("run_timeout_seconds") or 900),
            "min_seconds": app_settings.RUN_TIMEOUT_MIN_SECONDS,
            "max_seconds": app_settings.RUN_TIMEOUT_MAX_SECONDS,
            "continuation_limit": app_settings.get_continuation_limit(cfg),
        }

    @app.put("/api/settings/runs")
    def set_run_settings(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)):
        try:
            value = app_settings.set_run_timeout_seconds(db(), int(payload.get("run_timeout_seconds")))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid run_timeout_seconds") from exc
        return {
            "run_timeout_seconds": value,
            "continuation_limit": app_settings.get_continuation_limit(app.state.config),
        }

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


    @app.get("/api/settings/higgsfield")
    def get_higgsfield_settings(user: dict[str, Any] = Depends(current_user)):
        return {"settings": _resolve_higgsfield_settings(), "status": higgsfield.status()}

    @app.put("/api/settings/higgsfield")
    def put_higgsfield_settings(payload: dict[str, Any] | None = None, user: dict[str, Any] = Depends(current_user)):
        payload = payload or {}
        current = _resolve_higgsfield_settings()
        image_policy = payload.get("imagePolicy") or current["imagePolicy"]
        if image_policy not in {"zero-credit-only", "ask-before-credits"}:
            raise HTTPException(status_code=400, detail="invalid image policy")
        cfg = {
            "imagePolicy": image_policy,
            "imageModel": (payload.get("imageModel") or current["imageModel"] or higgsfield.DEFAULT_IMAGE_MODEL).strip(),
        }
        app_settings.set_json(db(), app_settings.HIGGSFIELD_KEY, cfg)
        auth_health.invalidate()  # Home Connections card re-checks on its next poll
        _audit_fs(user, "settings.higgsfield", "-", json.dumps({"imagePolicy": cfg["imagePolicy"]}))
        return {"ok": True, "settings": cfg, "status": higgsfield.status()}

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
        if session.get("project_id"):
            p = db().execute("SELECT slug FROM projects WHERE id = ?", (session["project_id"],)).fetchone()
            if p:
                root = _project_root(p["slug"], user)
                try:
                    fsapi.delete(root, path)
                except fsapi.FsError:
                    pass
                _audit_fs(user, "artifact.delete", p["slug"], path)
        update_produced_artifacts(db(), session_id, lambda current: [a for a in current if a.get("path") != path])
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

    # Project code is owner-triggered but still untrusted. Never pass Proxima/API
    # credentials into it and never let it set cookies on the Proxima origin.
    _HOP = {
        "authorization", "cf-access-jwt-assertion", "connection", "content-encoding",
        "content-length", "cookie", "host", "keep-alive", "proxy-authorization",
        "transfer-encoding",
    }
    _RESPONSE_HOP = _HOP | {"set-cookie", "www-authenticate"}

    @app.api_route("/api/appview/{slug}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def app_view(slug: str, path: str, request: Request, user: dict[str, Any] = Depends(current_user)):
        # Proxy to the project's running app. Proxima authenticates the inbound request,
        # then strips its session/auth headers before forwarding to project code.
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
        out = {k: v for k, v in up.headers.items() if k.lower() not in _RESPONSE_HOP}
        return Response(content=up.content, status_code=up.status_code, headers=out, media_type=up.headers.get("content-type"))


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

    @app.get("/api/preview/{slug}/{file_path:path}")
    def project_preview(slug: str, file_path: str, user: dict[str, Any] = Depends(current_user)):
        # Serve a project file inline for live preview (rendering a built site in an
        # <iframe>). Auth via the HttpOnly proxima_session cookie, sent same-origin on
        # the iframe AND its relative asset requests — no token in the URL. Path-jailed.
        root = _project_root(slug, user)
        try:
            target = fsapi.resolve_in_project(root, file_path)
        except fsapi.FsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        return FileResponse(str(target))  # inline (no attachment) so HTML/CSS/JS render

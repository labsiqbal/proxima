"""Static frontend/PWA route registration for Proxima."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

ResponseFactory = Callable[..., Any]


def register_frontend(
    app: Any,
    web_dist_path: str | None,
    env_name: str | None = None,
    *,
    static_files_cls: type[Any],
    file_response: ResponseFactory,
    html_response: ResponseFactory,
) -> None:
    """Mount the built web frontend and its PWA shell routes if dist exists.

    FastAPI/Starlette classes are injected by main.py so this stays a focused
    route-registration helper that is easy to test with lightweight fakes.
    """
    if not web_dist_path:
        return
    dist = Path(web_dist_path)
    if not dist.exists():
        return

    # Vite content-hashes asset filenames, so they're safe to cache FOREVER —
    # without this, StaticFiles only sends an ETag, so every reload revalidates
    # the (1.8MB+) bundle over the network and feels slow. New build = new hash.
    class _ImmutableStatic(static_files_cls):  # type: ignore
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if response.status_code == 200:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", _ImmutableStatic(directory=str(assets)), name="assets")

    icons = dist / "icons"
    if icons.exists():
        app.mount("/icons", static_files_cls(directory=str(icons)), name="icons")

    # The HTML shell + service worker must NOT be cached: they're the entry
    # points that reference content-hashed (immutable) assets. Without no-cache
    # the browser keeps serving a stale index.html → stale CSS/JS after a deploy.
    no_cache = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def web_manifest():
        return file_response(dist / "manifest.webmanifest", headers=no_cache)

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return file_response(dist / "sw.js", headers=no_cache)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return file_response(dist / "icons" / "icon-192.png", media_type="image/png")

    @app.get("/", include_in_schema=False)
    def web_index():
        if env_name:
            # Stamp the environment into <title> so staging/prod browser tabs are
            # unmistakable. The SPA never touches document.title, so this persists
            # for the tab's lifetime. No-op when PROXIMA_ENV_NAME is unset (prod).
            try:
                html = (dist / "index.html").read_text(encoding="utf-8").replace(
                    "<title>Proxima</title>",
                    f"<title>Proxima · {env_name}</title>",
                    1,
                )
                return html_response(html, headers=no_cache)
            except OSError:
                pass
        return file_response(dist / "index.html", headers=no_cache)

"""Project artifact scanning.

The typed list of artifacts a run leaves behind (design / app / page / doc / file /
video), for chat result cards and the Artifacts view. Pure + best-effort: prunes
heavy dirs, absorbs files inside a produced app dir, and caps the result.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

_ARTIFACT_EXCLUDE = {"node_modules", ".git", ".next", "dist", "build", "out", "__pycache__", ".cache", ".turbo", ".venv", "venv", "wiki"}
_ARTIFACT_SKIP_NAMES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md", ".impeccable.md", "CONTRIBUTING.md", "LICENSE.md"}


def scan_project_artifacts(root: "Path", start_ts: float) -> list[dict[str, Any]]:
    """Typed list of artifacts under `root` modified at/after `start_ts`, so the UI can
    preview each: design / app (runnable package.json) / page (.html) / doc (.md) / file.
    Prunes heavy dirs and absorbs files inside a produced app dir. Pure + best-effort."""
    if not root.is_dir():
        return []

    def mtime(p: "Path") -> float:
        try:
            return p.stat().st_mtime
        except Exception:
            return 0.0

    designs: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
    apps: list[dict[str, Any]] = []
    misc: list[dict[str, Any]] = []
    app_dirs: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _ARTIFACT_EXCLUDE and not d.startswith(".")]
        dp = Path(dirpath)
        parts = dp.relative_to(root).parts
        if len(parts) > 4:
            dirnames[:] = []
            continue
        for fn in filenames:
            f = dp / fn
            rel = str(f.relative_to(root))
            if fn == "scene.json" and rel.startswith("artifacts/design/"):
                if mtime(f) >= start_ts:
                    try:
                        s = json.loads(f.read_text())
                        designs.append({"type": "design", "id": str(s.get("id") or dp.name), "title": str(s.get("title") or dp.name), "path": f"artifacts/design/{dp.name}", "_m": mtime(f)})
                    except Exception:
                        pass
                continue
            if fn == "index.html" and rel.startswith("artifacts/video/"):
                if mtime(f) >= start_ts:
                    try:
                        title = dp.name
                        text = f.read_text(encoding="utf-8", errors="ignore")
                        m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
                        if m:
                            title = re.sub(r"\s+", " ", m.group(1)).strip() or title
                        videos.append({"type": "video", "id": dp.name, "title": title, "path": str(dp.relative_to(root)), "_m": mtime(f)})
                    except Exception:
                        videos.append({"type": "video", "id": dp.name, "title": dp.name, "path": str(dp.relative_to(root)), "_m": mtime(f)})
                continue
            if fn == "package.json" and mtime(f) >= start_ts:
                try:
                    scripts = (json.loads(f.read_text()) or {}).get("scripts", {}) or {}
                    cmd = next((s for s in ("dev", "start", "serve", "preview") if s in scripts), None)
                    if cmd:
                        reld = str(dp.relative_to(root)) if dp != root else "."
                        app_dirs.add(reld)
                        apps.append({"type": "app", "dir": reld, "title": (dp.name or "app"), "command": f"npm run {cmd}", "path": reld, "_m": mtime(f)})
                except Exception:
                    pass
                continue
            if mtime(f) < start_ts or rel.startswith("artifacts/design/") or fn in _ARTIFACT_SKIP_NAMES:
                continue
            ext = f.suffix.lower()
            if ext in (".html", ".htm"):
                misc.append({"type": "page", "title": fn, "path": rel, "_m": mtime(f)})
            elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif") and parts and parts[0] in ("artifacts", "reports", "exports"):
                misc.append({"type": "image", "title": fn, "path": rel, "_m": mtime(f)})
            elif ext in (".pdf", ".doc", ".docx", ".txt", ".rtf") and parts and parts[0] in ("artifacts", "reports", "exports"):
                misc.append({"type": "doc", "title": fn, "path": rel, "_m": mtime(f)})
            elif ext == ".md":
                misc.append({"type": "doc", "title": fn, "path": rel, "_m": mtime(f)})
            elif ext in (".mp4", ".webm", ".mov") and parts and parts[0] in ("artifacts", "reports", "exports"):
                misc.append({"type": "video-file", "title": fn, "path": rel, "_m": mtime(f)})
            elif parts and parts[0] in ("artifacts", "reports", "exports"):
                misc.append({"type": "file", "title": fn, "path": rel, "_m": mtime(f)})
    misc = [m for m in misc if not any(d != "." and (m["path"] == d or m["path"].startswith(d + "/")) for d in app_dirs)]
    # Sort newest-first BEFORE the cap so the result is deterministic + most-relevant
    # (os.walk order is unstable; without this the capped subset flickers between polls).
    allitems = sorted(designs + videos + apps + misc, key=lambda a: (-a.get("_m", 0.0), a.get("path") or ""))
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for a in allitems:
        k = (a["type"], a.get("path"))
        if k in seen:
            continue
        seen.add(k)
        a.pop("_m", None)
        out.append(a)
    return out[:40]


def artifacts_for_output_links(artifacts: list[dict[str, Any]], project_slug: str | None = None) -> list[dict[str, Any]]:
    """Normalize scanned artifacts for chat result cards.

    The scanner result is intentionally small and serializable. Adding project_slug
    here keeps frontend routing origin-aware without introducing a larger artifact
    registry before the product contract has settled.
    """
    out: list[dict[str, Any]] = []
    for item in artifacts:
        link = {k: v for k, v in item.items() if not str(k).startswith("_")}
        if project_slug:
            link["project_slug"] = project_slug
        out.append(link)
    return out


def update_produced_artifacts(
    conn: sqlite3.Connection,
    session_id: int,
    mutate: Callable[[list[Any]], list[Any]],
    *,
    attempts: int = 12,
) -> None:
    """Compare-and-swap ``sessions.produced_artifacts`` (a JSON list).

    ``mutate(current) -> new`` transforms the current list (append/merge/prune).
    The write only lands if the value is unchanged since we read it
    (``WHERE produced_artifacts = <old>``); if a concurrent writer changed it
    under us the UPDATE matches no row and we re-read + re-apply ``mutate`` and
    retry. This is optimistic concurrency scoped to one field: no new column, no
    lock — the compare-and-swap is the guard, atomic under SQLite's single-writer
    rule — so two writers on different connections can't silently lose an artifact
    (the lost-update the audit flagged: worker finishing a run while the UI deletes
    an artifact). ``mutate`` must be safe to re-run (merge/filter both are).
    """
    for _ in range(attempts):
        row = conn.execute("SELECT produced_artifacts FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return
        old = row["produced_artifacts"] or "[]"
        new = json.dumps(mutate(json.loads(old)))
        if new == old:
            return
        if conn.execute(
            "UPDATE sessions SET produced_artifacts = ? WHERE id = ? AND produced_artifacts = ?",
            (new, session_id, old),
        ).rowcount:
            return
    logging.getLogger("proxima.artifacts").warning(
        "produced_artifacts CAS gave up after %d attempts (session %s) — high contention", attempts, session_id
    )

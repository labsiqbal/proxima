"""Server-side Design Studio scene builders.

Design Studio normally creates scenes in the browser and auto-saves them to
`artifacts/design/<id>/scene.json`; these helpers let the backend seed a valid
scene the studio can open — for the `/design` chat command and the
"edit this image in Design Studio" bridge. Field names mirror
`apps/web/src/components/design/scene.ts` (Scene → Artboard → Layer).
"""
from __future__ import annotations

import json
import re
import struct
import time
from pathlib import Path
from typing import Any

from . import fsapi

ARTBOARD_BG = "#0b1020"
TEXT_FILL = "#f8fafc"
MUTED_FILL = "#94a3b8"


def slugify(text: str, fallback: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
    return base or fallback


def design_title(prompt: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9 ]+", " ", prompt).strip().split()
    return " ".join(words[:8]) or "Design draft"


def image_dims(path: Path) -> tuple[int, int] | None:
    """Width/height for PNG and JPEG without Pillow. None when unknown."""
    try:
        with path.open("rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:2] == b"\xff\xd8":  # JPEG: scan for a SOF marker
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return int(w), int(h)
                    (size,) = struct.unpack(">H", f.read(2))
                    f.seek(size - 2, 1)
    except Exception:
        return None
    return None


def scene_shell(prompt: str) -> tuple[str, dict[str, Any]]:
    """A brief-seeded blank scene for /design. Returns (design_id, scene)."""
    title = design_title(prompt)
    design_id = f"{slugify(title, 'design')}-{int(time.time())}"
    scene = {
        "id": design_id,
        "type": "graphic",
        "title": title,
        "artboards": [
            {
                "id": "a1",
                "width": 1080,
                "height": 1080,
                "background": ARTBOARD_BG,
                "layers": [
                    {"id": "t1", "type": "text", "x": 80, "y": 96, "width": 920, "text": title, "fontSize": 64, "fill": TEXT_FILL, "lineHeight": 1.15},
                    {"id": "t2", "type": "text", "x": 80, "y": 220, "width": 920, "text": prompt, "fontSize": 28, "fill": MUTED_FILL, "lineHeight": 1.5},
                ],
            }
        ],
    }
    return design_id, scene


def design_run_message(scene: dict[str, Any], brief: str) -> str:
    """The first design-session run for an /design draft. The design-session
    guardrail (reply as <design-scene>) and the DESIGN_GUIDE quality bar are injected
    by run_prompting/build_run_preamble — this only carries the scene + the ask."""
    return (
        "Current scene:\n```json\n" + json.dumps(scene) + "\n```\n\n"
        f"Design request: {brief}\n\n"
        "The current scene is only a seeded shell (title + brief as placeholder text). "
        "Replace it with a real composition for this request — keep the artboard size "
        "unless the format clearly demands otherwise."
    )


def persist_draft(root: Path, design_id: str, scene: dict[str, Any], project_slug: str, *, run_pending_id: int) -> dict[str, Any]:
    """Write a seeded /design draft to disk and return its chat artifact.

    The scene must already carry its ``sessionId`` (the linked design session).
    ``run_pending_id`` marks the scene as awaiting exactly this run — Design
    Studio's recovery-on-open only auto-applies a finished run the on-disk scene
    was still waiting for. Keeping the scene shape + on-disk layout here (not in
    the chat gate) means create_run stays feature-blind about design internals.
    """
    scene["runPendingId"] = run_pending_id
    d = fsapi.resolve_in_project(root, f"artifacts/design/{design_id}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "scene.json").write_text(json.dumps(scene, indent=2), encoding="utf-8")
    return {"type": "design", "id": design_id, "title": scene["title"], "path": f"artifacts/design/{design_id}", "project_slug": project_slug}


def scene_for_image(image_rel_path: str, dims: tuple[int, int] | None, title: str | None = None) -> tuple[str, dict[str, Any]]:
    """A scene whose artboard is the image, full-bleed — the 'edit this image'
    entry point. Returns (design_id, scene)."""
    name = Path(image_rel_path).stem
    scene_title = title or name or "Image design"
    design_id = f"{slugify(scene_title, 'image')}-{int(time.time())}"
    w, h = dims or (1080, 1080)
    # Cap the artboard so huge sources stay workable; the layer scales with it.
    scale = min(1.0, 1600 / max(w, h))
    aw, ah = max(1, round(w * scale)), max(1, round(h * scale))
    scene = {
        "id": design_id,
        "type": "graphic",
        "title": scene_title,
        "artboards": [
            {
                "id": "a1",
                "width": aw,
                "height": ah,
                "background": ARTBOARD_BG,
                "layers": [
                    {"id": "i1", "type": "image", "x": 0, "y": 0, "width": aw, "height": ah, "src": image_rel_path}
                ],
            }
        ],
    }
    return design_id, scene

"""Recommended host tools: detect-and-advertise, never vendor (T8 decision 3).

Proxima is not a package manager. The capability bundle ships an advisory list of
CLIs (`bundled-skills/recommended-tools.json` - data, not code); this module probes
PATH for them so run setup can advertise the PRESENT ones in the agent preamble
(one line each) and Settings can quietly hint at the missing ones. A missing tool
never blocks anything.

Everything here is defensive: a missing or malformed JSON file degrades to an
empty list.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

log = logging.getLogger("proxima.recommended_tools")

RECOMMENDED_TOOLS_FILENAME = "recommended-tools.json"


def load_recommended_tools(bundle_dir: str | Path | None) -> list[dict[str, str]]:
    """The bundle's tool advisory list: `[{bin, use, install}]`. Entries without
    a `bin` are dropped; extra keys are ignored."""
    if not bundle_dir:
        return []
    path = Path(bundle_dir) / RECOMMENDED_TOOLS_FILENAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        log.warning("unreadable recommended-tools file: %s", path)
        return []
    tools = data.get("tools") if isinstance(data, dict) else None
    out: list[dict[str, str]] = []
    for t in tools if isinstance(tools, list) else []:
        if not isinstance(t, dict) or not str(t.get("bin") or "").strip():
            continue
        out.append({
            "bin": str(t["bin"]).strip(),
            "use": str(t.get("use") or "").strip(),
            "install": str(t.get("install") or "").strip(),
        })
    return out


def probe_recommended_tools(bundle_dir: str | Path | None) -> list[dict[str, Any]]:
    """The advisory list with a `present` flag per tool (PATH probe, cheap)."""
    return [{**t, "present": shutil.which(t["bin"]) is not None}
            for t in load_recommended_tools(bundle_dir)]


def present_tools(bundle_dir: str | Path | None) -> list[dict[str, Any]]:
    return [t for t in probe_recommended_tools(bundle_dir) if t["present"]]


def tools_preamble_block(tools: list[dict[str, Any]]) -> str | None:
    """The one-liner-per-tool advertisement for the run preamble. Only PRESENT
    tools are listed - the preamble never nags about missing ones."""
    if not tools:
        return None
    lines = ["### Host tools available"]
    lines += [f"- `{t['bin']}`" + (f" - available for {t['use']}" if t.get("use") else " - available")
              for t in tools]
    return "\n".join(lines)

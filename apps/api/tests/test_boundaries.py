"""Architecture boundary gates (CI-enforced).

These lock in the future-proof contract: feature modules stay isolated from the
core substrate. If a future edit makes a feature module reach into core tables or
import core internals, these fail loudly — the boundary can't erode silently.
Modelled on the kind of structural gate used to keep a clean-rewrite honest.
"""
from __future__ import annotations

import pathlib
import re

PKG = pathlib.Path(__file__).resolve().parents[1] / "proxima_api"

# Modules that implement a feature's own logic — they may reference core rows by
# id and go through the sanctioned write paths, but must never mutate core tables
# directly nor import the run engine / chat gate internals.
FEATURE_MODULES = [
    "design_scenes.py",
    "image_providers.py",
    "higgsfield.py",
]

CORE_TABLES = {"sessions", "runs", "events", "messages", "prompt_collaborations"}
_MUTATION = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)", re.IGNORECASE)
_CORE_IMPORT = re.compile(r"(?:from\s+\.\s*import\s+worker|from\s+\.worker\b|from\s+\.routes\b|import\s+worker\b)")


def test_feature_modules_do_not_mutate_core_tables():
    offenders = []
    for name in FEATURE_MODULES:
        src = (PKG / name).read_text()
        for m in _MUTATION.finditer(src):
            if m.group(1).lower() in CORE_TABLES:
                offenders.append(f"{name}: `{m.group(0)}` — mutate core via a command/tx path, not directly")
    assert not offenders, "feature module writes a core table directly:\n" + "\n".join(offenders)


def test_feature_modules_do_not_import_core_internals():
    offenders = [name for name in FEATURE_MODULES if _CORE_IMPORT.search((PKG / name).read_text())]
    assert not offenders, f"feature module imports core internals (worker/routes): {offenders}"


def test_chat_gate_does_not_hardcode_feature_require():
    """The main-chat gate must decide feature access through the kinds registry
    (_require_mode_feature), never open-code `require(feature_cfg, DESIGN_STUDIO)`.
    A new gated session kind is added by registering it, not by editing chat.py."""
    src = (PKG / "routes" / "chat.py").read_text()
    bad = re.findall(r"features\.require\(\s*feature_cfg\s*,\s*features\.(?:DESIGN_STUDIO|VIDEO)\b", src)
    assert not bad, f"chat gate hardcodes a feature require ({len(bad)}×) — route it through the kinds registry"

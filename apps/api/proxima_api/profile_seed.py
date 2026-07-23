from __future__ import annotations

import shutil
from pathlib import Path

# Default Hermes credential/config files (back-compat default for the helpers
# below). Conversation state (sessions/, checkpoints/, logs/, *.bak*) is
# intentionally excluded so per-profile conversation isolation is preserved.
SEED_FILES: tuple[str, ...] = (".env", "auth.json", "config.yaml")
REFRESH_FILES: tuple[str, ...] = ("auth.json", "config.yaml")


def seed_agent_home(source: Path, target: Path, files: tuple[str, ...]) -> list[str]:
    """Copy an agent's credential/config files from the host's source dir into a
    fresh per-profile home so the agent is authenticated out of the box.

    Idempotent: never overwrites a file that already exists in target.
    No-op (returns []) when source is missing. Returns the names copied.
    """
    source = Path(source)
    target = Path(target)
    if not source.is_dir():
        return []
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in files:
        src = source / name
        dst = target / name
        # is_file() follows symlinks; copy2 copies the TARGET's content as a plain
        # file. Credential files are often symlinked (e.g. multi-account setups),
        # so we deliberately follow them rather than skip.
        if src.is_file() and not dst.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(name)
            except OSError:
                continue
    return copied


def refresh_agent_credentials(source: Path, target: Path, files: tuple[str, ...]) -> list[str]:
    """Force-copy an agent's credential files from source into target, overwriting
    when they differ. Agents (Hermes/Codex/Claude Code/Grok) rotate OAuth tokens, so a
    one-time per-profile copy goes stale; refreshing before each run keeps the
    profile on the host's current auth. Returns the names that actually changed.
    """
    source = Path(source)
    target = Path(target)
    if not source.is_dir() or not target.is_dir():
        return []
    changed: list[str] = []
    for name in files:
        src = source / name
        dst = target / name
        if src.is_file():  # follows symlinks; compares the target's content
            try:
                differs = (not dst.exists()) or src.read_bytes() != dst.read_bytes()
                if differs:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    changed.append(name)
            except OSError:
                continue
    return changed


# Back-compat thin wrappers (Hermes defaults), used by existing callers.
def seed_hermes_home(source: Path, target: Path) -> list[str]:
    return seed_agent_home(source, target, SEED_FILES)


def refresh_hermes_credentials(source: Path, target: Path) -> list[str]:
    return refresh_agent_credentials(source, target, REFRESH_FILES)

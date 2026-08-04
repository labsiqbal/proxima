from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:  # POSIX advisory locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX host
    fcntl = None  # type: ignore[assignment]

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


# ── credential sync (single-use OAuth rotation safe) ────────────────────────
#
# Agents rotate OAuth tokens, and the ChatGPT/Codex login rotates them
# SINGLE-USE: a refresh burns the old refresh token and mints a new pair. Since
# Proxima fans one host login out into every profile home, a plain host ->
# profile force-copy is destructive: whichever home refreshed first holds the
# only live pair, and copying the host's now-burnt pair over it kills the last
# working credential ("your refresh token was already used"). The owner's only
# escape was to re-login, which reseeded a fresh token everywhere - until the
# next rotation.
#
# So the sync is *newest wins*, with the host dir as the hub: a rotation that
# happened inside a profile is published back to the host, and every other
# profile picks it up on its next run. Guards:
#   - identity: a file is only published to the host when it is recognisably the
#     same credential (same JSON shape, same account) - a profile that was last
#     used by a different runner must never overwrite the host login;
#   - single-flight: one exclusive lock per source dir, so two runs starting at
#     once cannot interleave;
#   - atomic: write a private temp file and rename over the destination, so a
#     failure can never leave a truncated credential behind.


def _lock_path(source: Path) -> Path:
    """Per-source-dir lock file. Lives in the temp dir (not in the agent's own
    home) so Proxima never writes stray files into a CLI's config directory."""
    key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"proxima-credentials-{uid}-{key}.lock"


@contextmanager
def _credential_lock(source: Path) -> Iterator[None]:
    if fcntl is None:  # pragma: no cover - non-POSIX host
        yield
        return
    fd = None
    try:
        fd = os.open(_lock_path(source), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:  # locking must never block a run
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        yield
        return
    try:
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(fd)


def _atomic_copy(src: Path, dst: Path) -> None:
    """Replace dst's content with src's, atomically and mode 0600.

    A symlinked dst (multi-account setups point a profile at a shared file) is
    written *through*: the real file is replaced, the link is preserved.
    """
    real = Path(os.path.realpath(dst)) if dst.is_symlink() else dst
    data = src.read_bytes()
    tmp = real.parent / f".{real.name}.proxima-tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        with suppress(OSError):
            st = src.stat()
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, real)
    except BaseException:
        with suppress(OSError):
            tmp.unlink()
        raise


_FRACTION = re.compile(r"\.(\d{1,9})")


def _parse_stamp(value: object) -> float:
    """Parse an agent's rotation timestamp (ISO-8601, often with nanoseconds
    and a trailing Z) into epoch seconds. 0.0 when absent/unparsable."""
    if not isinstance(value, str) or not value.strip():
        return 0.0
    text = value.strip().replace("Z", "+00:00")
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _credential_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _rotation_stamp(path: Path) -> tuple[float, int]:
    """How recent this credential is: (agent-recorded rotation time, mtime).

    The embedded stamp is authoritative because copies preserve mtime and an
    agent rewrites the file in place on every refresh; mtime is the fallback
    for opaque (non-JSON) credential/config files.
    """
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return (0.0, 0)
    data = _credential_json(path)
    embedded = 0.0
    if data is not None:
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        embedded = max(
            _parse_stamp(data.get("last_refresh")),
            _parse_stamp(tokens.get("last_refresh")),
        )
    return (embedded, mtime)


def _identity(path: Path) -> tuple[tuple[str, ...], str] | None:
    """Fingerprint of *which* login a credential file holds - its JSON shape
    plus the account it belongs to. None for opaque files."""
    data = _credential_json(path)
    if data is None:
        return None
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    account = data.get("account_id") or tokens.get("account_id") or ""
    return (tuple(sorted(data.keys())), str(account))


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


def _publishable(host: Path, profile: Path) -> bool:
    """May the profile's copy become the host's? Only when it is newer AND
    recognisably the same login - never a leftover from another runner."""
    identity = _identity(profile)
    if identity is None or identity != _identity(host):
        return False
    return _rotation_stamp(profile) > _rotation_stamp(host)


def _reconcile(
    source: Path, target: Path, files: tuple[str, ...], *, pull: bool
) -> list[str]:
    source = Path(source)
    target = Path(target)
    if not source.is_dir() or not target.is_dir():
        return []
    changed: list[str] = []
    with _credential_lock(source):
        for name in files:
            src = source / name
            dst = target / name
            if not src.is_file():
                continue
            try:
                if _same_file(src, dst):
                    continue  # live-home profiles share the host's file
                if not dst.exists():
                    if pull:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_copy(src, dst)
                        changed.append(name)
                    continue
                if src.read_bytes() == dst.read_bytes():
                    continue
                if _publishable(src, dst):
                    _atomic_copy(dst, src)  # the profile rotated - host follows
                    if not pull:
                        changed.append(name)
                elif pull:
                    _atomic_copy(src, dst)
                    changed.append(name)
            except OSError:
                continue
    return changed


def sync_agent_credentials(
    source: Path, target: Path, files: tuple[str, ...]
) -> list[str]:
    """Reconcile a profile's credential files with the host's before a run.

    Newest wins in both directions: a host re-login lands in the profile, and a
    token the profile rotated to is published back to the host so sibling
    profiles heal on their next run. Returns the names whose *profile* copy
    changed - the caller recycles the cached agent process for those, since it
    holds the old token in memory.
    """
    return _reconcile(source, target, files, pull=True)


def publish_agent_credentials(
    source: Path, target: Path, files: tuple[str, ...]
) -> list[str]:
    """Publish a rotation that happened inside a profile back to the host, after
    a run. Push-only on purpose: the profile's cached agent process may still be
    alive holding the token it rotated to, so nothing is pulled underneath it.
    Returns the names whose *host* copy changed.
    """
    return _reconcile(source, target, files, pull=False)


# Back-compat thin wrappers (Hermes defaults), used by existing callers.
def seed_hermes_home(source: Path, target: Path) -> list[str]:
    return seed_agent_home(source, target, SEED_FILES)


def sync_hermes_credentials(source: Path, target: Path) -> list[str]:
    return sync_agent_credentials(source, target, REFRESH_FILES)

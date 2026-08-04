"""Per-profile credential sync must survive single-use OAuth token rotation.

ChatGPT (Codex) refresh tokens are single-use: refreshing rotates the pair and
burns the old refresh token. Proxima fans the host login out into every profile
home, so a one-way host -> profile force-copy destroys the one copy that
actually rotated and leaves every home holding a burnt token ("your refresh
token was already used"). These tests pin the newest-wins, identity-guarded,
atomic, single-flight behaviour that keeps rotation from being lost.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from proxima_api.profile_seed import (
    publish_agent_credentials,
    sync_agent_credentials,
)

FILES = ("auth.json",)


def _auth(refresh_token: str, *, last_refresh: str, account: str = "acct-1") -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": "id",
                "access_token": f"access-for-{refresh_token}",
                "refresh_token": refresh_token,
                "account_id": account,
            },
            "last_refresh": last_refresh,
        }
    )


def _homes(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "host"
    target = tmp_path / "profile"
    source.mkdir()
    target.mkdir()
    return source, target


def _refresh_token(path: Path) -> str:
    return json.loads(path.read_text())["tokens"]["refresh_token"]


def test_rotated_profile_token_survives_and_reaches_the_host(tmp_path: Path):
    """The bug: the profile rotated, the host copy is burnt, and the pre-run
    sync used to overwrite the good token with the dead one."""
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-0", last_refresh="2026-07-23T10:43:55.609742788Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-1", last_refresh="2026-08-01T09:00:00.000000000Z")
    )

    changed = sync_agent_credentials(source, target, FILES)

    # The profile keeps the token it rotated to...
    assert _refresh_token(target / "auth.json") == "rt-1"
    # ...and the host becomes the hub for it, so sibling profiles heal too.
    assert _refresh_token(source / "auth.json") == "rt-1"
    # Nothing changed in the profile home, so no cached agent needs recycling.
    assert changed == []


def test_host_relogin_wins_over_an_older_profile_copy(tmp_path: Path):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-fresh", last_refresh="2026-08-02T00:00:00.000000000Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-old", last_refresh="2026-07-23T10:43:55.609742788Z")
    )

    changed = sync_agent_credentials(source, target, FILES)

    assert _refresh_token(target / "auth.json") == "rt-fresh"
    assert _refresh_token(source / "auth.json") == "rt-fresh"
    assert changed == ["auth.json"]  # profile changed -> recycle the cached agent


def test_missing_embedded_stamp_falls_back_to_mtime(tmp_path: Path):
    """Non-JSON credential files (Hermes config.yaml, opaque tokens) still sync
    host -> profile when the host is newer."""
    source, target = _homes(tmp_path)
    (target / "auth.json").write_text("token-v1")
    time.sleep(0.01)
    (source / "auth.json").write_text("token-v2")

    assert sync_agent_credentials(source, target, FILES) == ["auth.json"]
    assert (target / "auth.json").read_text() == "token-v2"


def test_a_different_credential_never_leaks_into_the_host(tmp_path: Path):
    """A profile switched from another runner still holds that runner's
    auth.json. It is newer, but it is NOT this login - the host must win and
    must never be overwritten with a foreign credential."""
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-codex", last_refresh="2026-07-23T10:43:55.609742788Z")
    )
    (target / "auth.json").write_text(
        json.dumps({"provider": "other-runner", "session": "unrelated"})
    )
    os.utime(target / "auth.json", (time.time() + 60, time.time() + 60))

    changed = sync_agent_credentials(source, target, FILES)

    assert _refresh_token(source / "auth.json") == "rt-codex"
    assert _refresh_token(target / "auth.json") == "rt-codex"
    assert changed == ["auth.json"]


def test_a_different_account_never_leaks_into_the_host(tmp_path: Path):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-a", last_refresh="2026-07-23T10:43:55.609742788Z", account="acct-1")
    )
    (target / "auth.json").write_text(
        _auth("rt-b", last_refresh="2026-08-02T00:00:00.000000000Z", account="acct-2")
    )

    sync_agent_credentials(source, target, FILES)

    assert _refresh_token(source / "auth.json") == "rt-a"
    assert _refresh_token(target / "auth.json") == "rt-a"


def test_identical_copies_are_a_no_op(tmp_path: Path):
    source, target = _homes(tmp_path)
    blob = _auth("rt-0", last_refresh="2026-07-23T10:43:55.609742788Z")
    (source / "auth.json").write_text(blob)
    (target / "auth.json").write_text(blob)

    assert sync_agent_credentials(source, target, FILES) == []


def test_first_copy_is_created_when_the_profile_has_none(tmp_path: Path):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-0", last_refresh="2026-07-23T10:43:55.609742788Z")
    )

    assert sync_agent_credentials(source, target, FILES) == ["auth.json"]
    assert _refresh_token(target / "auth.json") == "rt-0"
    assert (target / "auth.json").stat().st_mode & 0o777 == 0o600


def test_write_is_atomic_so_a_failure_never_truncates_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-new", last_refresh="2026-08-02T00:00:00.000000000Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-old", last_refresh="2026-07-23T10:43:55.609742788Z")
    )

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    assert sync_agent_credentials(source, target, FILES) == []

    # The old credential is intact (not truncated) and no temp file is left.
    assert _refresh_token(target / "auth.json") == "rt-old"
    assert [p.name for p in target.iterdir()] == ["auth.json"]


def test_symlinked_profile_credential_is_written_through(tmp_path: Path):
    """Multi-account setups symlink a profile's auth.json at a shared file;
    syncing must update that file, not replace the link with a copy."""
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-new", last_refresh="2026-08-02T00:00:00.000000000Z")
    )
    shared = tmp_path / "shared-auth.json"
    shared.write_text(_auth("rt-old", last_refresh="2026-07-23T10:43:55.609742788Z"))
    os.symlink(shared, target / "auth.json")

    sync_agent_credentials(source, target, FILES)

    assert (target / "auth.json").is_symlink()
    assert _refresh_token(shared) == "rt-new"


def test_source_and_target_pointing_at_one_home_is_a_no_op(tmp_path: Path):
    """claude-code's live-home mode points the profile at the host dir itself."""
    source = tmp_path / "host"
    source.mkdir()
    (source / "auth.json").write_text(
        _auth("rt-0", last_refresh="2026-07-23T10:43:55.609742788Z")
    )

    assert sync_agent_credentials(source, source, FILES) == []
    assert _refresh_token(source / "auth.json") == "rt-0"


def test_concurrent_syncs_are_single_flight(tmp_path: Path, monkeypatch):
    """Two runs starting at once must not interleave reads and writes of the
    same credential pair."""
    from proxima_api import profile_seed

    source = tmp_path / "host"
    source.mkdir()
    (source / "auth.json").write_text(
        _auth("rt-0", last_refresh="2026-07-23T10:43:55.609742788Z")
    )
    targets = []
    for name in ("a", "b"):
        t = tmp_path / name
        t.mkdir()
        targets.append(t)

    inside = 0
    overlapped = False
    guard = threading.Lock()
    real_copy = profile_seed._atomic_copy

    def slow_copy(src, dst):
        nonlocal inside, overlapped
        with guard:
            inside += 1
            if inside > 1:
                overlapped = True
        time.sleep(0.05)
        real_copy(src, dst)
        with guard:
            inside -= 1

    monkeypatch.setattr(profile_seed, "_atomic_copy", slow_copy)

    threads = [
        threading.Thread(
            target=sync_agent_credentials, args=(source, t, FILES)
        )
        for t in targets
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not overlapped, "credential sync must be single-flight per source"
    for t in targets:
        assert _refresh_token(t / "auth.json") == "rt-0"


def test_publish_only_pushes_a_rotation_and_never_pulls(tmp_path: Path):
    """Called after a run: a cached agent process still holds the token it has
    in memory, so publishing must not silently swap the file underneath it."""
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-host", last_refresh="2026-08-02T00:00:00.000000000Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-profile", last_refresh="2026-07-23T10:43:55.609742788Z")
    )

    publish_agent_credentials(source, target, FILES)

    assert _refresh_token(target / "auth.json") == "rt-profile"  # untouched
    assert _refresh_token(source / "auth.json") == "rt-host"

    # Now the profile is the one that rotated: publish it to the host.
    (target / "auth.json").write_text(
        _auth("rt-rotated", last_refresh="2026-08-03T00:00:00.000000000Z")
    )
    assert publish_agent_credentials(source, target, FILES) == ["auth.json"]
    assert _refresh_token(source / "auth.json") == "rt-rotated"


# ── run wiring ─────────────────────────────────────────────────────────────


class _FakeAcpManager:
    def __init__(self) -> None:
        self.recycled: list[tuple] = []

    async def recycle(self, spec, home, cwd, **kwargs):
        self.recycled.append((spec.id, home, cwd))


class _FakeApp:
    def __init__(self) -> None:
        self.state = type("S", (), {})()
        self.state.acp_manager = _FakeAcpManager()


class _Spec:
    id = "codex"
    refresh_files = FILES

    def __init__(self, source_dir: Path) -> None:
        self.source_dir = str(source_dir)


def _prompting(app=None):
    from proxima_api.run_prompting import RunPrompting

    return RunPrompting(app or _FakeApp())


def test_pre_run_sync_keeps_a_rotated_profile_and_skips_the_recycle(
    tmp_path: Path,
):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-burnt", last_refresh="2026-07-23T10:43:55.609742788Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-live", last_refresh="2026-08-01T09:00:00.000000000Z")
    )
    app = _FakeApp()
    prompting = _prompting(app)

    asyncio.run(
        prompting.refresh_credentials_if_needed({}, _Spec(source), str(target), "/tmp")
    )

    assert _refresh_token(target / "auth.json") == "rt-live"
    assert _refresh_token(source / "auth.json") == "rt-live"
    assert app.state.acp_manager.recycled == []


def test_pre_run_sync_recycles_when_the_profile_copy_changed(tmp_path: Path):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-new", last_refresh="2026-08-02T00:00:00.000000000Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-old", last_refresh="2026-07-23T10:43:55.609742788Z")
    )
    app = _FakeApp()

    asyncio.run(
        _prompting(app).refresh_credentials_if_needed(
            {}, _Spec(source), str(target), "/tmp"
        )
    )

    assert _refresh_token(target / "auth.json") == "rt-new"
    assert app.state.acp_manager.recycled == [("codex", str(target), "/tmp")]


def test_post_run_publish_shares_a_rotation_with_the_host(tmp_path: Path):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-burnt", last_refresh="2026-07-23T10:43:55.609742788Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-live", last_refresh="2026-08-01T09:00:00.000000000Z")
    )

    _prompting().publish_credentials_after_run({}, _Spec(source), str(target))

    assert _refresh_token(source / "auth.json") == "rt-live"


def test_credential_sync_can_be_turned_off(tmp_path: Path):
    source, target = _homes(tmp_path)
    (source / "auth.json").write_text(
        _auth("rt-new", last_refresh="2026-08-02T00:00:00.000000000Z")
    )
    (target / "auth.json").write_text(
        _auth("rt-old", last_refresh="2026-07-23T10:43:55.609742788Z")
    )

    _prompting().publish_credentials_after_run(
        {"refresh_credentials": False}, _Spec(source), str(target)
    )

    assert _refresh_token(source / "auth.json") == "rt-new"
    assert _refresh_token(target / "auth.json") == "rt-old"

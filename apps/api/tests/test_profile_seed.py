from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.profile_seed import SEED_FILES, seed_hermes_home


def test_seed_copies_credential_files_and_skips_state(tmp_path):
    source = tmp_path / "src-hermes"
    source.mkdir()
    (source / ".env").write_text("OPENROUTER_API_KEY=abc\n", encoding="utf-8")
    (source / "auth.json").write_text("{}", encoding="utf-8")
    (source / "config.yaml").write_text("model: x\n", encoding="utf-8")
    (source / "sessions").mkdir()
    (source / "sessions" / "a.json").write_text("{}", encoding="utf-8")
    (source / "checkpoints").mkdir()
    (source / "config.yaml.bak.20260101").write_text("old", encoding="utf-8")

    target = tmp_path / "profile-home"
    target.mkdir()

    copied = seed_hermes_home(source, target)

    assert (target / ".env").read_text(encoding="utf-8") == "OPENROUTER_API_KEY=abc\n"
    assert (target / "auth.json").exists()
    assert (target / "config.yaml").exists()
    assert not (target / "sessions").exists()
    assert not (target / "checkpoints").exists()
    assert not (target / "config.yaml.bak.20260101").exists()
    assert set(copied) == set(SEED_FILES)


def test_seed_is_idempotent_and_does_not_overwrite(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / ".env").write_text("NEW=2\n", encoding="utf-8")
    target = tmp_path / "tgt"
    target.mkdir()
    (target / ".env").write_text("KEEP=1\n", encoding="utf-8")

    copied = seed_hermes_home(source, target)

    assert (target / ".env").read_text(encoding="utf-8") == "KEEP=1\n"
    assert ".env" not in copied


def test_seed_missing_source_is_noop(tmp_path):
    target = tmp_path / "tgt"
    target.mkdir()
    assert seed_hermes_home(tmp_path / "nope", target) == []


def test_seed_follows_symlinked_credentials(tmp_path):
    # Credential files are often symlinks (e.g. multi-account setups), so seeding
    # FOLLOWS the link and copies the target's content as a plain file.
    source = tmp_path / "src"
    source.mkdir()
    real_file = tmp_path / "real.env"
    real_file.write_text("SECRET=value\n", encoding="utf-8")
    os.symlink(real_file, source / ".env")

    target = tmp_path / "tgt"
    target.mkdir()

    copied = seed_hermes_home(source, target)

    assert ".env" in copied
    dst = target / ".env"
    assert dst.is_file() and not dst.is_symlink()        # copied as a plain file
    assert dst.read_text() == "SECRET=value\n"           # content followed


def test_owner_provisioning_seeds_profile_home_from_source(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXIMA_DEFAULT_RUNNER", "hermes")
    source = tmp_path / "source-hermes"
    source.mkdir()
    (source / ".env").write_text("OPENROUTER_API_KEY=xyz\n", encoding="utf-8")

    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "source_hermes_home": str(source),
        "single_user_name": "alice",
        "start_worker": False,
    })
    client = TestClient(app)
    # Single-user: first request auto-creates the owner and provisions its profile home.
    client.post("/auth/auto")

    seeded = tmp_path / "ws" / "hermes-profiles" / "alice" / "default" / ".env"
    assert seeded.read_text(encoding="utf-8") == "OPENROUTER_API_KEY=xyz\n"


def test_seed_users_seeds_credential_file(tmp_path):
    """Users pre-seeded via seed_users config also get their credential files."""
    source = tmp_path / "source-hermes"
    source.mkdir()
    (source / ".env").write_text("OPENROUTER_API_KEY=seeded\n", encoding="utf-8")

    app = create_app({
        "database_path": str(tmp_path / "proxima.db"),
        "workspace_root": str(tmp_path / "ws"),
        "projectctl_path": "/usr/bin/true",
        "source_hermes_home": str(source),
        "start_worker": False,
        "seed_users": [
            {"username": "alice", "password": "password123", "role": "member"},
        ],
    })
    # Instantiate the app (which calls init_db with seed_users and hermes_home_factory).
    with TestClient(app):
        pass

    seeded = tmp_path / "ws" / "hermes-profiles" / "alice" / "default" / ".env"
    assert seeded.exists(), "credential file not seeded for seed_users entry"
    assert seeded.read_text(encoding="utf-8") == "OPENROUTER_API_KEY=seeded\n"

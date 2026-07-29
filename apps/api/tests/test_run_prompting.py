from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from types import SimpleNamespace

from proxima_api import run_prompting


def test_markdown_image_paths_only_returns_explicit_local_references():
    text = (
        "Use ![logo](assets/logo.png) and ![same](assets/logo.png), "
        "not ![remote](https://example.com/logo.png) or ![marker](bad|path.png)."
    )

    assert run_prompting.markdown_image_paths(text) == ["assets/logo.png"]
    assert run_prompting.append_vision_references("Design this", ["assets/logo.png"]).endswith(
        "⟦VISION:assets/logo.png⟧"
    )


def test_extract_vision_images_is_jailed_image_only_and_size_bounded(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "good.png").write_bytes(b"image")
    (root / "not-image.txt").write_bytes(b"text")
    (root / "large.webp").write_bytes(b"x" * 9)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    os.symlink(outside, root / "linked.jpg")
    monkeypatch.setattr(run_prompting, "_VISION_MAX_BYTES", 8)
    monkeypatch.setattr(run_prompting, "_VISION_MAX_TOTAL_BYTES", 8)

    prompt = (
        "Build the composition\n\n"
        "⟦VISION:good.png|not-image.txt|large.webp|linked.jpg|../outside.jpg⟧"
    )
    clean, images = run_prompting.extract_vision_images(prompt, str(root))

    assert clean == "Build the composition"
    assert images == [(b"image", "image/png")]


def test_load_project_images_enforces_total_byte_budget(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "one.png").write_bytes(b"1234")
    (root / "two.png").write_bytes(b"5678")
    monkeypatch.setattr(run_prompting, "_VISION_MAX_BYTES", 8)
    monkeypatch.setattr(run_prompting, "_VISION_MAX_TOTAL_BYTES", 6)

    assert run_prompting.load_project_images(root, ["one.png", "two.png"]) == [
        (b"1234", "image/png")
    ]


def test_restricted_master_turn_recycles_process_and_provider_session():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE agent_sessions("
        "session_id INTEGER NOT NULL, hermes_home TEXT NOT NULL, "
        "acp_session_id TEXT NOT NULL, PRIMARY KEY(session_id, hermes_home))"
    )
    db.execute(
        "INSERT INTO agent_sessions(session_id, hermes_home, acp_session_id) "
        "VALUES (7, '/managed/master', 'old-provider-thread')"
    )

    calls: list[tuple[str, bool]] = []

    class Process:
        async def new_master_session(self, _cwd, _tools):
            calls.append(("new_master_session", True))
            return f"fresh-{len(calls)}"

    class Manager:
        async def recycle(self, _spec, _home, _cwd, *, master_chat_only=False):
            calls.append(("recycle", master_chat_only))

        async def get(self, _spec, _home, _cwd, *, master_chat_only=False):
            calls.append(("get", master_chat_only))
            return Process()

    app = SimpleNamespace(
        state=SimpleNamespace(
            worker_db=db,
            db_lock=threading.Lock(),
            acp_manager=Manager(),
        )
    )
    prompting = run_prompting.RunPrompting(app)

    for run_id in (11, 12):
        process, session_id, fresh = asyncio.run(
            prompting.load_or_create_agent_session(
                run_id,
                7,
                SimpleNamespace(),
                "/managed/master",
                "/empty/master/workspace",
                {},
                master_dynamic_tools=[{"name": "list_containers"}],
            )
        )
        assert isinstance(process, Process)
        assert session_id.startswith("fresh-")
        assert fresh is True

    assert calls == [
        ("recycle", True),
        ("get", True),
        ("new_master_session", True),
        ("recycle", True),
        ("get", True),
        ("new_master_session", True),
    ]
    assert db.execute(
        "SELECT acp_session_id FROM agent_sessions "
        "WHERE session_id = 7 AND hermes_home = '/managed/master'"
    ).fetchone()["acp_session_id"] == "fresh-6"

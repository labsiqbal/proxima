from __future__ import annotations

import os

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

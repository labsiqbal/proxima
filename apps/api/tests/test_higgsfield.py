from __future__ import annotations

import subprocess

import pytest

from proxima_api import higgsfield, image_providers


def test_higgsfield_status_without_binary(monkeypatch):
    monkeypatch.setattr(higgsfield, "binary", lambda path_env=None: None)

    status = higgsfield.status()

    assert status["installed"] is False
    assert status["ready"] is False
    assert "not found" in status["detail"].lower()


def test_higgsfield_status_checks_login_before_workspace(monkeypatch):
    monkeypatch.setattr(higgsfield, "binary", lambda path_env=None: "/usr/bin/higgsfield")

    def fake_run(args, **_kwargs):
        if args[:2] == ["auth", "token"]:
            return subprocess.CompletedProcess(args, 2, stdout="", stderr="Error: Not authenticated")
        raise AssertionError(f"unexpected Higgsfield command: {args}")

    monkeypatch.setattr(higgsfield, "_run", fake_run)

    status = higgsfield.status()

    assert status["installed"] is True
    assert status["authenticated"] is False
    assert status["workspaceSelected"] is False
    assert status["ready"] is False
    assert "same machine/user" in status["detail"]


def test_higgsfield_status_reports_workspace_after_login(monkeypatch):
    monkeypatch.setattr(higgsfield, "binary", lambda path_env=None: "/usr/bin/higgsfield")

    def fake_run(args, **_kwargs):
        if args[:2] == ["auth", "token"]:
            return subprocess.CompletedProcess(args, 0, stdout="secret-token", stderr="")
        if args[:2] == ["account", "status"]:
            return subprocess.CompletedProcess(args, 4, stdout="", stderr="Error: No workspace selected.")
        raise AssertionError(f"unexpected Higgsfield command: {args}")

    monkeypatch.setattr(higgsfield, "_run", fake_run)

    status = higgsfield.status()

    assert status["installed"] is True
    assert status["authenticated"] is True
    assert status["workspaceSelected"] is False
    assert status["ready"] is False
    assert "no workspace is selected" in status["detail"]


def test_higgsfield_zero_credit_guard_allows_free_cost(monkeypatch):
    def fake_run(args, **_kwargs):
        assert args[:2] == ["generate", "cost"]
        return subprocess.CompletedProcess(args, 0, stdout='{"credits":0}', stderr="")

    monkeypatch.setattr(higgsfield, "_run", fake_run)

    cost = higgsfield.assert_zero_credit("nano_banana_2", {"prompt": "test"})

    assert cost["credits"] == 0


def test_higgsfield_zero_credit_guard_blocks_paid_cost(monkeypatch):
    def fake_run(args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, stdout='{"credits":12}', stderr="")

    monkeypatch.setattr(higgsfield, "_run", fake_run)

    with pytest.raises(higgsfield.HiggsfieldError, match="12 credits"):
        higgsfield.assert_zero_credit("nano_banana_2", {"prompt": "test"})


def test_higgsfield_cost_uses_cli_param_names(monkeypatch):
    seen = {}

    def fake_run(args, **_kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout='{"credits":0}', stderr="")

    monkeypatch.setattr(higgsfield, "_run", fake_run)

    higgsfield.estimate_cost("nano_banana_2", {"prompt": "test", "aspect_ratio": "1:1"})

    assert "--aspect_ratio" in seen["args"]
    assert "--aspect-ratio" not in seen["args"]


def test_higgsfield_image_size_maps_to_aspect_ratio_before_cost(monkeypatch):
    seen = {}

    def fake_run(args, **_kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout='{"credits":1}', stderr="")

    monkeypatch.setattr(higgsfield, "_run", fake_run)

    with pytest.raises(higgsfield.HiggsfieldError, match="1 credit"):
        higgsfield.generate_image(prompt="test", model="nano_banana_2", size="1536x1024")

    assert "--aspect_ratio" in seen["args"]
    assert "3:2" in seen["args"]
    assert "--size" not in seen["args"]


def test_image_provider_auto_falls_back_to_codex_for_text_to_image(monkeypatch):
    def fake_higgsfield(**_kwargs):
        raise higgsfield.HiggsfieldError("paid credits")

    monkeypatch.setattr(higgsfield, "generate_image", fake_higgsfield)
    monkeypatch.setattr(image_providers, "_gen_codex", lambda **_kwargs: b"codex-image")

    raw = image_providers.generate("auto", None, prompt="test")

    assert raw == b"codex-image"

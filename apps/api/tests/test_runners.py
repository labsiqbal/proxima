from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.runners import (
    RunnerDefinition,
    augmented_path,
    detect_runners,
    ensure_python_compat_shim,
    hermes_status,
    subprocess_env,
)


def _make_hermes_bin(tmp_path: Path) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "hermes"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return str(bindir)


def test_hermes_status_ready_when_bin_and_home_present(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    bindir = _make_hermes_bin(tmp_path)
    st = hermes_status(source_home=str(home), path_env=bindir)
    assert st["ready"] is True
    assert st["binary"].endswith("/hermes")
    assert st["home"] == str(home)
    assert st["guidance"] == ""


def test_hermes_status_not_ready_when_auth_requires_relogin(tmp_path):
    import json

    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({
        "active_provider": "xai-oauth",
        "providers": {
            "xai-oauth": {
                "last_auth_error": {
                    "message": 'xAI token refresh failed. Response: {"error":"invalid_grant","error_description":"Refresh token has been revoked"}',
                    "relogin_required": True,
                }
            }
        },
    }))
    bindir = _make_hermes_bin(tmp_path)
    st = hermes_status(source_home=str(home), path_env=bindir)
    assert st["ready"] is False
    assert st["home"] == str(home)
    assert "expired" in st["guidance"].lower() or "revoked" in st["guidance"].lower()
    assert "hermes -z" in st["guidance"] or "hermes setup" in st["guidance"]
    assert "Agents menu" in st["guidance"]


def test_runner_readiness_marks_hermes_not_ready_on_relogin(tmp_path, monkeypatch):
    import json
    import proxima_api.runners as r

    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({
        "active_provider": "xai-oauth",
        "providers": {
            "xai-oauth": {
                "last_auth_error": {"message": "token refresh failed", "relogin_required": True}
            }
        },
    }))
    bindir = _make_hermes_bin(tmp_path)
    real_expand = os.path.expanduser
    monkeypatch.setattr(
        r.os.path,
        "expanduser",
        lambda p: str(home) if p == "~/.hermes" else real_expand(p),
    )
    out = r.runner_readiness(path_env=bindir)
    assert out["hermes"]["installed"] is True
    assert out["hermes"]["ready"] is False
    assert out["hermes"]["authHint"]
    assert "Agents menu" in out["hermes"]["authHint"] or "hermes" in out["hermes"]["authHint"]


def test_hermes_status_missing_binary(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text("x: 1")
    st = hermes_status(source_home=str(home), path_env=str(tmp_path / "empty"))
    assert st["ready"] is False
    assert st["binary"] is None
    assert "PATH" in st["guidance"] or "install" in st["guidance"].lower()


def test_hermes_status_missing_home(tmp_path):
    bindir = _make_hermes_bin(tmp_path)
    st = hermes_status(source_home=str(tmp_path / "nope"), path_env=bindir)
    assert st["ready"] is False
    assert st["home"] is None
    assert "hermes -z" in st["guidance"]


def test_detect_runners_uses_proxima_registry_and_controlled_path(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["hermes", "codex", "aider"]:
        file = bin_dir / name
        file.write_text("#!/bin/sh\nexit 0\n")
        file.chmod(0o755)

    registry = (
        RunnerDefinition("hermes", "Hermes", ("hermes",), True),
        RunnerDefinition("claude-code", "Claude Code", ("definitely-missing-claude",), True),
        RunnerDefinition("codex", "Codex", ("codex",), True),
        RunnerDefinition("aider", "Aider", ("aider",), False, detection_only=True),
    )
    result = {runner["id"]: runner for runner in detect_runners(path_env=str(bin_dir), registry=registry)}

    assert result["hermes"]["installed"] is True
    assert result["hermes"]["runnable"] is True
    assert result["hermes"]["path"] == str(bin_dir / "hermes")

    assert result["codex"]["installed"] is True
    assert result["codex"]["runnable"] is True

    assert result["claude-code"]["installed"] is False
    assert result["claude-code"]["runnable"] is False

    assert result["aider"]["installed"] is True
    assert result["aider"]["hasAdapter"] is False
    assert result["aider"]["detectionOnly"] is True
    assert result["aider"]["runnable"] is False


def test_detect_endpoint_includes_hermes_status(tmp_path):
    app = create_app({
        "database_path": str(tmp_path / "h.db"),
        "workspace_root": str(tmp_path / "rt"),
        "seed_users": [{"username": "alice", "os_user": "alice", "role": "environment_admin"}],
    })
    api = TestClient(app)
    tok = api.post("/auth/auto").json()["token"]
    body = api.get("/api/runners/detect", headers={"Authorization": f"Bearer {tok}"}).json()
    assert "hermes" in body
    assert set(["ready", "binary", "home", "guidance"]).issubset(body["hermes"].keys())


def test_hermes_status_explicit_binary_used(tmp_path):
    home = tmp_path / "h"; home.mkdir(); (home / "auth.json").write_text("{}")
    exe = tmp_path / "myhermes"; exe.write_text("#!/bin/sh\nexit 0\n"); exe.chmod(0o755)
    st = hermes_status(source_home=str(home), binary=str(exe), path_env=str(tmp_path / "empty"))
    assert st["ready"] is True
    assert st["binary"] == str(exe)


def test_hermes_status_explicit_binary_missing_falls_back(tmp_path):
    home = tmp_path / "h"; home.mkdir(); (home / "auth.json").write_text("{}")
    st = hermes_status(source_home=str(home), binary=str(tmp_path / "nope"), path_env=str(tmp_path / "empty"))
    assert st["ready"] is False
    assert st["binary"] is None


def test_runner_readiness_reports_specs():
    from proxima_api.runners import runner_readiness
    r = runner_readiness()
    assert "hermes" in r and "claude-code" in r
    for v in r.values():
        assert set(["id", "displayName", "installed", "ready", "authHint"]).issubset(v.keys())


def test_runner_readiness_shape_for_uninstalled(monkeypatch):
    import proxima_api.runners as r
    monkeypatch.setattr(r, "resolve_binary", lambda *a, **k: None)
    out = r.runner_readiness()
    assert out["hermes"]["installed"] is False
    assert out["hermes"]["ready"] is False
    assert out["hermes"]["authHint"]  # hint shown when not installed


def test_detect_endpoint_includes_runner_readiness(tmp_path):
    from fastapi.testclient import TestClient
    from proxima_api.main import create_app
    app = create_app({"database_path": str(tmp_path / "h.db"), "workspace_root": str(tmp_path / "rt"), "seed_users": [{"username": "alice", "os_user": "alice", "role": "environment_admin"}]})
    api = TestClient(app)
    tok = api.post("/auth/auto").json()["token"]
    body = api.get("/api/runners/detect", headers={"Authorization": f"Bearer {tok}"}).json()
    assert "runnerReadiness" in body
    assert "hermes" in body["runnerReadiness"] and "claude-code" in body["runnerReadiness"]


def test_runners_detect_endpoint_lists_runners(tmp_path: Path):
    app = create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "seed_users": [{"username": "bob", "role": "member", "os_user": "bob"}],
        }
    )
    client = TestClient(app)

    token = client.post("/auth/auto").json()["token"]
    res = client.get("/api/runners/detect", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    body = res.json()
    assert body["user"] == "bob"
    assert any(runner["id"] == "hermes" for runner in body["runners"])
    assert all(runner["id"] != "manual" for runner in body["runners"])


def test_runner_subprocess_env_drops_service_secrets_but_keeps_provider_auth(monkeypatch):
    monkeypatch.setenv("PROXIMA_CF_API_TOKEN", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "runner-needs-this")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = subprocess_env(provider_auth=True, allowlist_env="PROXIMA_RUNNER_ENV_ALLOWLIST")

    assert env["OPENAI_API_KEY"] == "runner-needs-this"
    assert "PROXIMA_CF_API_TOKEN" not in env
    assert env["PATH"]


def test_app_subprocess_env_requires_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-project-code")
    monkeypatch.setenv("PROJECT_PUBLIC_URL", "https://example.test")
    monkeypatch.setenv("PROXIMA_APP_ENV_ALLOWLIST", "PROJECT_PUBLIC_URL")

    env = subprocess_env(allowlist_env="PROXIMA_APP_ENV_ALLOWLIST")

    assert env["PROJECT_PUBLIC_URL"] == "https://example.test"
    assert "OPENAI_API_KEY" not in env


def test_python_compat_shim_when_only_python3(tmp_path, monkeypatch):
    """Hosts with python3 but no python get a workspace shim on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py3 = bindir / "python3"
    py3.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py3.chmod(0o755)
    monkeypatch.setenv("PROXIMA_WORKSPACE_ROOT", str(tmp_path / "ws"))

    path = augmented_path(str(bindir))
    assert shutil_which_python(path) is not None
    assert path.split(":")[0].endswith("/shims")
    shim = Path(path.split(":")[0]) / "python"
    assert shim.exists()
    assert os.path.realpath(shim) == os.path.realpath(py3)


def test_python_compat_shim_not_added_when_python_exists(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py = bindir / "python"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)
    monkeypatch.setenv("PROXIMA_WORKSPACE_ROOT", str(tmp_path / "ws"))

    assert ensure_python_compat_shim(str(bindir)) is None
    path = augmented_path(str(bindir))
    assert not path.startswith(str(tmp_path / "ws" / "shims"))


def test_python_compat_shim_skipped_without_python3(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PROXIMA_WORKSPACE_ROOT", str(tmp_path / "ws"))
    assert ensure_python_compat_shim(str(bindir)) is None


def shutil_which_python(path: str) -> str | None:
    import shutil

    return shutil.which("python", path=path)

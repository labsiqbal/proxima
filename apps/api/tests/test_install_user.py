from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "scripts" / "install-user"
MACOS_INSTALLER = REPO_ROOT / "scripts" / "install-macos"
WINDOWS_INSTALLER = REPO_ROOT / "scripts" / "install-windows.ps1"


def test_install_user_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "calls.log"
    home.mkdir()
    fake_bin.mkdir()

    stub = '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$CALL_LOG"\n'
    for name in ("uv", "npm", "node", "systemctl", "loginctl"):
        path = fake_bin / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "PROXIMA_CONFIG": str(config_home / "proxima" / "proxima.env"),
        "CALL_LOG": str(call_log),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Dry run complete; no files or services changed." in result.stdout
    assert "write " in result.stdout
    assert "proxima-preview-output.socket" in result.stdout
    assert "proxima-preview-output@.service" in result.stdout
    assert "enable --now proxima-preview-output.socket" in result.stdout
    assert "check-preview-drained" in result.stdout
    assert "stop proxima.service" in result.stdout
    assert result.stdout.index("stop proxima.service") < result.stdout.index(
        "check-preview-drained"
    )
    assert result.stdout.index("check-preview-drained") < result.stdout.index(
        "Installing systemd user units"
    )
    assert not call_log.exists()
    assert not (config_home / "proxima" / "proxima.env").exists()
    assert not (config_home / "systemd" / "user").exists()
    assert not (home / ".local" / "bin" / "proxima").exists()


def test_install_user_refuses_non_linux_before_any_side_effect(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "calls.log"
    home.mkdir()
    fake_bin.mkdir()

    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    stub = '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$CALL_LOG"\n'
    for name in ("uv", "npm", "node", "systemctl", "loginctl"):
        path = fake_bin / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALLER), "--dry-run"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "CALL_LOG": str(call_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "supported systemd installer requires Linux" in result.stderr
    assert "No dependencies, config, units, services, or runtime data were changed." in result.stderr
    assert not call_log.exists()
    assert list(home.iterdir()) == []


def test_linux_reinstall_preserves_master_on_and_safe_update_off(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "calls.log"
    config_file = config_home / "proxima" / "proxima.env"
    home.mkdir()
    fake_bin.mkdir()
    config_file.parent.mkdir(parents=True)
    original = (
        'PROXIMA_FEATURE_MASTER_ORCHESTRATOR="1"\n'
        'PROXIMA_FEATURE_SAFE_SELF_UPDATE="0"\n'
        'PROXIMA_PORT="18765"\n'
    )
    config_file.write_text(original, encoding="utf-8")

    stub = '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$CALL_LOG"\n'
    for name in (
        "uv",
        "npm",
        "node",
        "systemctl",
        "loginctl",
        "check-preview-drained",
    ):
        path = fake_bin / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
            "PROXIMA_CONFIG": str(config_file),
            "CALL_LOG": str(call_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert config_file.read_text(encoding="utf-8") == original
    calls = call_log.read_text(encoding="utf-8")
    assert "check-preview-drained --protocol proxima-preview-supervisor-v2:user" in calls
    assert "systemctl --user restart proxima.service" in calls
    assert "systemctl --user enable --now proxima-backup.timer" in calls
    assert (config_home / "systemd" / "user" / "proxima.service").is_file()
    assert "Open:        http://127.0.0.1:18765" in result.stdout


def test_macos_installer_refuses_linux_before_any_side_effect(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "calls.log"
    home.mkdir()
    fake_bin.mkdir()
    stub = '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$CALL_LOG"\n'
    for name in ("uv", "npm", "node", "launchctl"):
        path = fake_bin / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(MACOS_INSTALLER)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "CALL_LOG": str(calls),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "requires macOS" in result.stderr
    assert "No dependencies, config, LaunchAgent, service, or runtime data were changed." in result.stderr
    assert not calls.exists()
    assert list(home.iterdir()) == []


def test_windows_installer_platform_guard_precedes_path_or_build_work() -> None:
    script = WINDOWS_INSTALLER.read_text(encoding="utf-8")

    guard = script.index("if (-not $IsWindows")
    first_path_resolution = script.index("$Root   =")
    first_build = script.index('Write-Host "==> Building backend deps"')
    assert guard < first_path_resolution < first_build
    assert "On Linux use scripts/install-user, the supported daily-driver path." in script
    assert "Nothing was changed." in script

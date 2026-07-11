from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "scripts" / "install-user"


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
    assert not call_log.exists()
    assert not (config_home / "proxima" / "proxima.env").exists()
    assert not (config_home / "systemd" / "user").exists()
    assert not (home / ".local" / "bin" / "proxima").exists()

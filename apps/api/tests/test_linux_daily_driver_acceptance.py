from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from proxima_api.main import create_app
from proxima_api.terminal import TerminalSession


REPO_ROOT = Path(__file__).resolve().parents[3]


def _app(tmp_path: Path):
    return create_app(
        {
            "database_path": str(tmp_path / "proxima.db"),
            "workspace_root": str(tmp_path / "workspace"),
            "projectctl_path": "/usr/bin/true",
            "start_worker": False,
            "feature_master_orchestrator": True,
            "preview_bind_host": "127.0.0.1",
            "update_check": False,
        }
    )


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_service_lifecycle_targets_only_the_isolated_linux_user_unit(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    config = tmp_path / "config" / "proxima.env"
    calls = tmp_path / "systemctl.log"
    home.mkdir()
    fake_bin.mkdir()
    config.parent.mkdir()
    config.write_text(
        'PROXIMA_SERVICE_NAME="proxima-acceptance"\n'
        'PROXIMA_FEATURE_MASTER_ORCHESTRATOR="1"\n',
        encoding="utf-8",
    )
    _write_executable(
        fake_bin / "systemctl",
        (
            "#!/bin/sh\n"
            'if [ "$1" = "--user" ] && [ "$2" = "cat" ]; then exit 0; fi\n'
            'printf \'%s\\n\' "$*" >> "$CALL_LOG"\n'
        ),
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "PROXIMA_CONFIG": str(config),
        "CALL_LOG": str(calls),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    }
    for action in ("status", "restart", "stop"):
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "proxima"), action],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--user status proxima-acceptance",
        "--user restart proxima-acceptance",
        "--user stop proxima-acceptance",
    ]
    persisted = config.read_text(encoding="utf-8")
    assert 'PROXIMA_FEATURE_MASTER_ORCHESTRATOR="1"' in persisted


def test_service_lifecycle_refuses_unknown_platform_before_manager_call(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "systemctl.log"
    home.mkdir()
    fake_bin.mkdir()
    _write_executable(fake_bin / "uname", "#!/bin/sh\nprintf 'FreeBSD\\n'\n")
    _write_executable(
        fake_bin / "systemctl",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$CALL_LOG"\n',
    )

    for action in ("status", "init-config", "build", "serve"):
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "proxima"), action],
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
        assert "unsupported host platform" in result.stderr
        assert (
            "No service, config, database, or runtime data was changed."
            in result.stderr
        )
    assert not calls.exists()
    assert list(home.iterdir()) == []


def test_linux_doctor_reports_supported_platform_in_isolated_runtime(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    data = tmp_path / "data"
    config = tmp_path / "config" / "proxima.env"
    home.mkdir()
    fake_bin.mkdir()
    config.parent.mkdir()
    config.write_text(
        f'PROXIMA_DATA_DIR="{data}"\n'
        f'PROXIMA_DB_PATH="{data / "proxima.db"}"\n'
        f'PROXIMA_WORKSPACE_ROOT="{data / "workspace"}"\n'
        f'PROXIMA_HERMES_PROFILES_ROOT="{data / "profiles"}"\n'
        'PROXIMA_FEATURE_MASTER_ORCHESTRATOR="1"\n',
        encoding="utf-8",
    )
    for name in ("uv", "npm", "python3"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "proxima"), "doctor"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "PROXIMA_CONFIG": str(config),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ok: platform Linux (supported)" in result.stdout
    assert "ok: data dirs writable" in result.stdout
    assert (data / "workspace").is_dir()
    assert (data / "profiles").is_dir()
    assert 'PROXIMA_FEATURE_MASTER_ORCHESTRATOR="1"' in config.read_text(
        encoding="utf-8"
    )


def test_pty_terminal_round_trip_matches_owner_shell(tmp_path: Path) -> None:
    terminal = TerminalSession(str(tmp_path))
    terminal.start()
    try:
        terminal.write(b"printf 'proxima-pty-ready\\n'\n")
        output = b""
        for _ in range(100):
            output += terminal.read()
            if b"proxima-pty-ready" in output:
                break
        assert b"proxima-pty-ready" in output
    finally:
        closed = terminal.close()
        assert closed.session_stopped is True
        assert closed.child_reaped is True


def test_online_backup_restores_a_verified_isolated_database(tmp_path: Path) -> None:
    database = tmp_path / "data" / "proxima.db"
    backups = tmp_path / "backups"
    database.parent.mkdir()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE acceptance(value TEXT NOT NULL)")
        conn.execute("INSERT INTO acceptance(value) VALUES ('before-backup')")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "backup")],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PROXIMA_DB_PATH": str(database),
            "PROXIMA_BACKUP_DIR": str(backups),
            "PROXIMA_BACKUP_KEEP": "2",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    snapshot = next(backups.glob("proxima-*.db"))

    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE acceptance SET value = 'after-backup'")
    restored = tmp_path / "restore" / "proxima.db"
    restored.parent.mkdir()
    shutil.copy2(snapshot, restored)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            conn.execute("SELECT value FROM acceptance").fetchone()[0]
            == "before-backup"
        )


def test_local_and_tailnet_reverse_proxy_entry_share_the_authenticated_app(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        local = client.get("/api/health", headers={"host": "127.0.0.1:8765"})
        tailnet = client.get(
            "/api/health",
            headers={
                "host": "proxima-nuc.example-tailnet.ts.net",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "proxima-nuc.example-tailnet.ts.net",
            },
        )
        assert local.status_code == tailnet.status_code == 200
        assert local.json()["version"] == tailnet.json()["version"]

        login = client.post(
            "/auth/set-password",
            json={"password": "isolated acceptance password"},
            headers={"x-forwarded-proto": "https"},
        )
        assert login.status_code == 200
        assert login.cookies["proxima_session"]
        assert "Secure" in login.headers["set-cookie"]


def test_upgrade_readiness_is_fail_closed_and_preserves_fixture_flags(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config" / "proxima.env"
    service_calls = tmp_path / "service-calls"
    home.mkdir()
    config.parent.mkdir()
    expected = 'PROXIMA_FEATURE_MASTER_ORCHESTRATOR="1"\n' 
    config.write_text(expected, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "proxima"), "update"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "PROXIMA_CONFIG": str(config),
            "PROXIMA_SERVICE_CALL_LOG": str(service_calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "proxima update is unavailable" in result.stderr
    assert (
        "No checkout, runtime data, service, or database was changed." in result.stderr
    )
    assert config.read_text(encoding="utf-8") == expected
    assert not service_calls.exists()


def test_acceptance_fixture_keeps_master_enabled(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)

    assert app.state.config["feature_master_orchestrator"] is True

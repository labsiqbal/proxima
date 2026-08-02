from __future__ import annotations

import errno
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from proxima_api.container_activity import acquire_container_activity_lease
from proxima_api.db import connect, init_db
from proxima_api.terminal import TerminalSession


def test_close_reaps_child_no_zombie(tmp_path):
    # A closed terminal must reap its shell child — otherwise it lingers as a
    # zombie and PIDs leak over a long-running session.
    t = TerminalSession(str(tmp_path))
    t.start()
    pid = t.pid
    assert pid
    result = t.close()
    assert result.child_reaped is True
    try:
        os.waitpid(pid, os.WNOHANG)
        assert False, "child still reapable -> close() left a zombie"
    except OSError as e:
        assert e.errno == errno.ECHILD  # no such child: already reaped


def test_close_terminates_background_processes(tmp_path):
    child_path = tmp_path / "child.pid"
    terminal = TerminalSession(str(tmp_path))
    terminal.start()
    terminal.write(b"sleep 30 & echo $! > child.pid\n")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not child_path.is_file():
        time.sleep(0.01)
    assert child_path.is_file()
    child_pid = int(child_path.read_text(encoding="utf-8").strip())

    result = terminal.close()
    assert result.session_stopped is True
    assert result.child_reaped is True
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("terminal descendant survived close")


def test_close_fails_closed_when_tree_cannot_be_verified(
    tmp_path,
    monkeypatch,
):
    from proxima_api.container_activity import GuardedWriterTree

    terminal = TerminalSession(str(tmp_path))
    terminal.start()
    monkeypatch.setattr(
        GuardedWriterTree,
        "terminate",
        lambda self, **_kwargs: False,
    )
    monkeypatch.setattr(
        GuardedWriterTree,
        "exited",
        lambda self: None,
    )

    result = terminal.close()
    assert result.session_stopped is False
    assert result.child_reaped is False


def test_close_failure_retains_until_tree_proven(tmp_path, monkeypatch):
    from proxima_api.container_activity import GuardedWriterTree

    terminal = TerminalSession(str(tmp_path))
    terminal.start()
    pid = terminal.pid
    assert pid is not None
    start_identity = terminal.start_identity
    monkeypatch.setattr(
        GuardedWriterTree,
        "terminate",
        lambda self, **_kwargs: False,
    )
    monkeypatch.setattr(
        GuardedWriterTree,
        "exited",
        lambda self: False,
    )

    result = terminal.close()
    assert result.session_stopped is False
    assert result.child_reaped is False
    assert result.pid == pid
    assert result.start_identity == start_identity


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bwrap") is None,
    reason="Bubblewrap is required for process containment",
)
def test_contained_terminal_terminates_detached_descendant(tmp_path):
    child_path = tmp_path / "detached.pid"
    escaped_path = tmp_path / "escaped.txt"
    terminal = TerminalSession(str(tmp_path), contained=True)
    terminal.start()
    terminal.write(
        b"setsid sh -c 'echo $$ > detached.pid; "
        b"sleep 0.4; echo escaped > escaped.txt' "
        b"</dev/null >/dev/null 2>&1 &\n"
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not child_path.is_file():
        time.sleep(0.01)
    assert child_path.is_file()

    result = terminal.close()
    assert result.session_stopped is True
    assert result.child_reaped is True
    time.sleep(0.6)
    assert not escaped_path.exists()


def _activity_lease(tmp_path: Path, slug: str = "term"):
    conn = connect(tmp_path / "proxima.db")
    init_db(conn, [])
    root = tmp_path / slug
    root.mkdir(parents=True, exist_ok=True)
    user_id = conn.execute(
        "INSERT INTO users(username, os_user) VALUES (?, ?)",
        (f"owner-{slug}", f"owner-{slug}"),
    ).lastrowid
    container_id = conn.execute(
        "INSERT INTO projects(slug, name, path, owner_user_id) VALUES (?, ?, ?, ?)",
        (slug, slug.title(), str(root), user_id),
    ).lastrowid
    return acquire_container_activity_lease(conn, int(container_id)), root


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="PTY-aware activity guardian is Linux-only",
)
def test_guarded_terminal_keeps_controlling_tty(tmp_path):
    lease, root = _activity_lease(tmp_path, "term-ctty")
    terminal = TerminalSession(str(root), activity_lease=lease)
    terminal.start()
    try:
        out = root / "tty.txt"
        terminal.write(b"tty > tty.txt\n")
        # The redirection creates the file before `tty` writes into it, so
        # poll for CONTENT, not existence - reading the just-created empty
        # file was a flake.
        deadline = time.monotonic() + 15
        text = ""
        while time.monotonic() < deadline:
            if out.is_file():
                text = out.read_text(encoding="utf-8").strip()
                if text:
                    break
            time.sleep(0.05)
        assert text.startswith("/dev/pts/") or text.startswith("/dev/tty"), text
    finally:
        result = terminal.close()
        assert result.session_stopped is True
        assert result.child_reaped is True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="PTY-aware activity guardian is Linux-only",
)
def test_guarded_terminal_supports_job_control(tmp_path):
    lease, root = _activity_lease(tmp_path, "term-jobs")
    terminal = TerminalSession(str(root), activity_lease=lease)
    terminal.start()
    try:
        out = root / "jobs.txt"
        terminal.write(b"sleep 30 &\njobs > jobs.txt\n")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not out.is_file():
            time.sleep(0.05)
        assert out.is_file()
        text = out.read_text(encoding="utf-8")
        assert "sleep" in text
    finally:
        result = terminal.close()
        assert result.session_stopped is True
        assert result.child_reaped is True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="PTY-aware activity guardian is Linux-only",
)
def test_guarded_terminal_close_kills_descendants_and_releases_lease(tmp_path):
    lease, root = _activity_lease(tmp_path, "term-close-tree")
    released = {"activity": False}

    class TrackingLease:
        def release(self) -> None:
            released["activity"] = True
            lease.release()

        def guard_process(self, command):
            return lease.guard_process(command)

        def mark_process_started(self) -> None:
            lease.mark_process_started()

    tracking = TrackingLease()
    terminal = TerminalSession(str(root), activity_lease=tracking)
    terminal.start()
    child_path = root / "child.pid"
    terminal.write(b"sleep 30 & echo $! > child.pid\n")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not child_path.is_file():
        time.sleep(0.05)
    assert child_path.is_file()
    child_pid = int(child_path.read_text(encoding="utf-8").strip())

    result = terminal.close()
    assert result.session_stopped is True
    assert result.child_reaped is True
    # Mirror routes/chat.py: activity releases only after proven tree exit.
    if result.child_reaped:
        tracking.release()
    assert released["activity"] is True
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("guarded terminal descendant survived close")

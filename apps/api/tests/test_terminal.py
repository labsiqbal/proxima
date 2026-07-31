from __future__ import annotations

import errno
import os
import shutil
import time

import pytest

from proxima_api import terminal as terminal_module
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
    deadline = time.monotonic() + 2
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


def test_close_fails_closed_when_session_cannot_be_verified(
    tmp_path,
    monkeypatch,
):
    terminal = TerminalSession(str(tmp_path))
    terminal.start()
    monkeypatch.setattr(
        terminal_module,
        "_session_members",
        lambda _sid: None,
    )

    result = terminal.close()
    assert result.session_stopped is False
    assert result.child_reaped is True


def test_close_failure_still_reports_reaped_child(tmp_path, monkeypatch):
    terminal = TerminalSession(str(tmp_path))
    terminal.start()
    pid = terminal.pid
    assert pid is not None
    start_identity = terminal.start_identity
    monkeypatch.setattr(
        terminal_module,
        "_stop_session",
        lambda _sid, _leader: False,
    )

    result = terminal.close()
    assert result.session_stopped is False
    assert result.child_reaped is True
    assert result.pid == pid
    assert result.start_identity == start_identity
    try:
        os.waitpid(pid, os.WNOHANG)
        assert False, "child still reapable after close failure path"
    except OSError as exc:
        assert exc.errno == errno.ECHILD


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
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not child_path.is_file():
        time.sleep(0.01)
    assert child_path.is_file()

    result = terminal.close()
    assert result.session_stopped is True
    assert result.child_reaped is True
    time.sleep(0.6)
    assert not escaped_path.exists()

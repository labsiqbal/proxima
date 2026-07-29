from __future__ import annotations

import errno
import os
import time

from proxima_api import terminal as terminal_module
from proxima_api.terminal import TerminalSession


def test_close_reaps_child_no_zombie(tmp_path):
    # A closed terminal must reap its shell child — otherwise it lingers as a
    # zombie and PIDs leak over a long-running session.
    t = TerminalSession(str(tmp_path))
    t.start()
    pid = t.pid
    assert pid
    t.close()
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

    assert terminal.close() is True
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

    assert terminal.close() is False


def test_contained_terminal_terminates_detached_descendant(tmp_path):
    child_path = tmp_path / "detached.pid"
    escaped_path = tmp_path / "escaped.txt"
    terminal = TerminalSession(str(tmp_path), contained=True)
    terminal.start()
    terminal.write(
        b"setsid sh -c 'echo $$ > detached.pid; "
        b"sleep 30; echo escaped > escaped.txt' "
        b"</dev/null >/dev/null 2>&1 &\n"
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not child_path.is_file():
        time.sleep(0.01)
    assert child_path.is_file()
    child_pid = int(child_path.read_text(encoding="utf-8").strip())

    assert terminal.close() is True
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("detached terminal descendant survived close")
    assert not escaped_path.exists()

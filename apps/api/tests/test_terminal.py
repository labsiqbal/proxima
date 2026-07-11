from __future__ import annotations

import errno
import os

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

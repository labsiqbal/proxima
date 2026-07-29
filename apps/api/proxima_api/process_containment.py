from __future__ import annotations

import os
import shutil
from collections.abc import Sequence


def pid_namespace_argv(
    argv: Sequence[str],
    *,
    cwd: str,
    label: str,
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if os.name != "posix" or bwrap is None:
        raise RuntimeError(f"{label} containment is unavailable")
    return [
        bwrap,
        "--die-with-parent",
        "--unshare-pid",
        "--as-pid-1",
        "--bind",
        "/",
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        "--proc",
        "/proc",
        "--chdir",
        cwd,
        "--",
        *argv,
    ]

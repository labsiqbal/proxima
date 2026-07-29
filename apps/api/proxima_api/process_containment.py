from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Sequence
from typing import Any


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


async def terminate_and_verify(
    process: Any,
    *,
    label: str,
    timeout: float = 5.0,
) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"{label} process did not exit after kill"
            ) from exc
    if process.returncode is None:
        raise RuntimeError(f"{label} process exit was not verified")

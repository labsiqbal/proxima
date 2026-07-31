from __future__ import annotations

import asyncio
import os
import signal
import shutil
import sys
import time
from collections.abc import Sequence
from typing import Any


def pid_namespace_argv(
    argv: Sequence[str],
    *,
    cwd: str,
    label: str,
    info_fd: int | None = None,
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if os.name != "posix" or bwrap is None:
        raise RuntimeError(f"{label} containment is unavailable")
    command = [
        bwrap,
        "--die-with-parent",
        "--unshare-pid",
        "--as-pid-1",
    ]
    if info_fd is not None:
        command.extend(["--info-fd", str(info_fd)])
    command.extend(
        [
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
    )
    return command


def process_tree_pids(root_pid: int) -> set[int] | None:
    """Return ``root_pid`` plus every descendant visible in ``/proc``.

    Activity guardians may put writers in a new session while keeping them under
    the tracked pid tree. Platforms without procfs return None. When the root is
    already gone the result is an empty set (descendants reparented away are not
    discoverable from a dead root alone).
    """
    proc_root = "/proc"
    try:
        root = int(root_pid)
    except (TypeError, ValueError):
        return set()
    try:
        if not os.path.isdir(f"{proc_root}/{root}"):
            return set()
    except OSError:
        return set()
    children: dict[int, set[int]] = {}
    try:
        names = [name for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return None
    for raw_pid in names:
        try:
            with open(f"{proc_root}/{raw_pid}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            # comm may contain spaces/parens; fields after the final ') ' start
            # with state, ppid, pgrp.
            fields = stat.rsplit(") ", 1)[1].split()
            if len(fields) < 2:
                continue
            ppid = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, set()).add(int(raw_pid))
    found = {root}
    pending = list(children.get(root, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return found


def _pid_is_running(pid: int) -> bool:
    """True when ``pid`` exists and is not a zombie."""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            state = stat.rsplit(") ", 1)[1].split()[0]
        except (OSError, IndexError, ValueError, TypeError):
            return False
        return state != "Z"
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def _signal_pid(pid: int, value: int) -> None:
    try:
        os.kill(int(pid), value)
    except OSError:
        pass


def terminate_process_tree(
    root_pid: int,
    *,
    grace_seconds: float = 4.0,
    kill_seconds: float = 2.0,
    initial_signal: int | None = None,
    known_pids: set[int] | None = None,
) -> bool:
    """Signal and wait for the identity-bound process tree under ``root_pid``.

    Never SIGKILL only the launcher: every known live member of the tracked tree
    is signaled, children before the root, and membership is refreshed across
    the wait so late forks stay in scope. ``known_pids`` seeds membership when
    the caller already snapshotted the tree (for example before closing a PTY
    master that would otherwise reparent children). Returns True only when every
    known member is proven exited. Platforms without process-tree inspection fail
    closed (return False) after best-effort signals to the root alone.
    """
    try:
        root = int(root_pid)
    except (TypeError, ValueError):
        return True
    if root <= 1:
        return True

    first = signal.SIGTERM if initial_signal is None else int(initial_signal)
    seen: set[int] = {root}
    if known_pids:
        for pid in known_pids:
            try:
                seen.add(int(pid))
            except (TypeError, ValueError):
                continue
    inspectable = sys.platform.startswith("linux")

    def refresh() -> set[int] | None:
        nonlocal inspectable
        if not inspectable:
            return None
        tree = process_tree_pids(root)
        if tree is None:
            inspectable = False
            return None
        seen.update(tree)
        return tree

    def live_members() -> set[int]:
        refresh()
        return {pid for pid in seen if _pid_is_running(pid)}

    def ordered_live() -> list[int]:
        live = live_members()
        ordered = sorted(live - {root}, reverse=True)
        if root in live:
            ordered.append(root)
        return ordered

    def signal_live(value: int) -> None:
        ordered = ordered_live()
        if not ordered:
            if not inspectable:
                _signal_pid(root, value)
            return
        for pid in ordered:
            _signal_pid(pid, value)

    # Snapshot once while the root is hopefully still alive so setsid() writers
    # under a guardian sentinel stay in ``seen`` even after the launcher dies.
    refresh()
    signal_live(first)
    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while time.monotonic() < deadline:
        if inspectable and not live_members():
            return True
        signal_live(signal.SIGTERM)
        time.sleep(0.05)

    deadline = time.monotonic() + max(0.0, float(kill_seconds))
    while True:
        ordered = ordered_live()
        if inspectable and not ordered:
            return True
        if not ordered and not inspectable:
            _signal_pid(root, signal.SIGKILL)
        for pid in ordered:
            _signal_pid(pid, signal.SIGKILL)
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)

    if not inspectable:
        return not _pid_is_running(root)
    return not live_members()


async def terminate_and_verify(
    process: Any,
    *,
    label: str,
    timeout: float = 5.0,
    tree: Any | None = None,
) -> None:
    if process is None and tree is None:
        return

    if tree is not None:
        grace = max(0.05, float(timeout) * 0.65)
        kill_wait = max(0.05, float(timeout) - grace)
        try:
            tree.seed_live_members()
        except Exception:
            pass
        tree_done = await asyncio.to_thread(
            tree.terminate,
            grace_seconds=grace,
            kill_seconds=kill_wait,
        )
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=max(0.2, float(timeout) * 0.25),
                )
            except asyncio.TimeoutError as exc:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        f"{label} process did not exit after kill"
                    ) from exc
                # Post-kill wait succeeded - fall through to tree proof.
        try:
            tree.seed_live_members()
        except Exception:
            pass
        if tree.exited() is not True:
            raise RuntimeError(
                f"{label} process tree did not exit after kill"
            )
        return

    if process is None or process.returncode is not None:
        # Without a tree handle, launcher returncode alone is the only signal
        # available. Callers that wrap guardians must pass ``tree``.
        return
    pid = getattr(process, "pid", None)
    use_tree = (
        pid is not None
        and os.name != "nt"
        and sys.platform.startswith("linux")
    )
    if use_tree:
        grace = max(0.05, float(timeout) * 0.65)
        kill_wait = max(0.05, float(timeout) - grace)
        tree_done = await asyncio.to_thread(
            terminate_process_tree,
            int(pid),
            grace_seconds=grace,
            kill_seconds=kill_wait,
        )
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=max(0.2, float(timeout) * 0.25),
            )
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"{label} process did not exit after kill"
                ) from exc
            # Post-kill wait succeeded - continue verification below.
        if process.returncode is None:
            raise RuntimeError(f"{label} process exit was not verified")
        if not tree_done:
            raise RuntimeError(
                f"{label} process tree did not exit after kill"
            )
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
            raise RuntimeError(f"{label} process did not exit after kill") from exc
    if process.returncode is None:
        raise RuntimeError(f"{label} process exit was not verified")

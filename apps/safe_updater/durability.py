from __future__ import annotations

import os
import stat
from pathlib import Path


class DurabilityError(OSError):
    pass


def write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise DurabilityError("durable write made no progress")
        offset += written


def _flush_windows_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    flush = kernel32.FlushFileBuffers
    flush.argtypes = [wintypes.HANDLE]
    flush.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not flush(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def fsync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    if os.name == "nt":
        _flush_windows_directory(path)
        return
    raise DurabilityError("directory durability backend unavailable")


def ensure_durable_directory(path: Path, mode: int) -> None:
    absolute = Path(os.path.abspath(path))
    if absolute.parent == absolute:
        raise DurabilityError("filesystem root cannot be a managed directory")
    missing: list[Path] = []
    current = absolute
    while True:
        if os.path.lexists(current):
            try:
                value = current.lstat()
            except OSError as exc:
                raise DurabilityError("durable directory cannot be inspected") from exc
            if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise DurabilityError("durable path must contain only real directories")
        else:
            missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=mode)
            directory.chmod(mode)
            fsync_directory(directory)
            fsync_directory(directory.parent)
        except OSError as exc:
            raise DurabilityError("durable directory cannot be created") from exc
    try:
        absolute.chmod(mode)
        fsync_directory(absolute)
    except OSError as exc:
        raise DurabilityError("durable directory permissions cannot be enforced") from exc

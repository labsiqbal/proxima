from __future__ import annotations

import ctypes
import errno
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .container_activity import ContainerBoundaryError


class OpsMigrationCollision(ContainerBoundaryError):
    """A legacy Ops layout cannot be moved without owner intervention."""


def directory_open_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise OpsMigrationCollision(
            "this platform cannot guarantee stable no-follow directory access"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def directory_fd_path(fd: int) -> Path:
    for prefix in ("/proc/self/fd", "/dev/fd"):
        candidate = Path(prefix) / str(fd)
        if candidate.exists():
            return candidate
    raise OpsMigrationCollision(
        "this platform cannot address an opened migration directory"
    )


def same_identity(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def stat_identity(value: os.stat_result) -> dict[str, int]:
    return {
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
    }


def valid_identity(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("device"), int)
        and isinstance(value.get("inode"), int)
    )


def identity_matches(
    value: Any,
    current: os.stat_result,
) -> bool:
    return (
        valid_identity(value)
        and int(value["device"]) == int(current.st_dev)
        and int(value["inode"]) == int(current.st_ino)
    )


def windows_directory_identity(
    handle: int,
    display: str,
) -> tuple[int, int]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time_low", wintypes.DWORD),
            ("creation_time_high", wintypes.DWORD),
            ("access_time_low", wintypes.DWORD),
            ("access_time_high", wintypes.DWORD),
            ("write_time_low", wintypes.DWORD),
            ("write_time_high", wintypes.DWORD),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    information = ByHandleFileInformation()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    if not get_information(
        ctypes.c_void_p(handle),
        ctypes.byref(information),
    ):
        raise OSError(
            ctypes.get_last_error(),
            f"cannot inspect directory: {display}",
        )
    if (
        not information.attributes & 0x00000010
        or information.attributes & 0x00000400
    ):
        raise ContainerBoundaryError(
            f"directory is missing or is a reparse point: {display}"
        )
    return (
        int(information.volume_serial),
        (int(information.file_index_high) << 32)
        | int(information.file_index_low),
    )


def windows_open_directory(
    path: Path,
) -> tuple[int, tuple[int, int]]:
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
    handle = create_file(
        str(path),
        0x0080,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise OSError(
            ctypes.get_last_error(),
            f"cannot open directory: {path}",
        )
    try:
        identity = windows_directory_identity(
            int(handle),
            str(path),
        )
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    return int(handle), identity


def _relative_object_attributes(
    parent_handle: int,
    name: str,
):
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_qos", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status", wintypes.LPVOID),
            ("information", ctypes.c_size_t),
        ]

    buffer = ctypes.create_unicode_buffer(name)
    encoded = name.encode("utf-16-le")
    unicode_name = UnicodeString(
        len(encoded),
        len(encoded) + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    return buffer, unicode_name, attributes, IoStatusBlock


def windows_create_directory_at(
    parent_handle: int,
    name: str,
) -> tuple[int, tuple[int, int]]:
    from ctypes import wintypes

    relative = _relative_object_attributes(parent_handle, name)
    attributes = relative[2]
    io_status_type = relative[3]
    status_block = io_status_type()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_file = ntdll.NtCreateFile
    create_file.restype = ctypes.c_long
    status = create_file(
        ctypes.byref(handle),
        0x00100000
        | 0x00000001
        | 0x00000002
        | 0x00000004
        | 0x00000080,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000010,
        0x00000001 | 0x00000002 | 0x00000004,
        3,
        0x00000001 | 0x00000020 | 0x00200000,
        None,
        0,
    )
    if status < 0:
        raise OSError(
            int(status),
            f"cannot create directory: {name}",
        )
    value = int(handle.value)
    try:
        identity = windows_directory_identity(value, name)
    except Exception:
        windows_close_handle(value)
        raise
    return value, identity


def windows_close_handle(handle: int) -> None:
    ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).CloseHandle(ctypes.c_void_p(handle))


def _write_all(fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise OSError("file write made no progress")
        offset += written
    os.fsync(fd)


def windows_create_file_at(
    parent_handle: int,
    name: str,
    content: bytes,
) -> None:
    from ctypes import wintypes
    import msvcrt

    relative = _relative_object_attributes(parent_handle, name)
    attributes = relative[2]
    io_status_type = relative[3]
    status_block = io_status_type()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    status = ntdll.NtCreateFile(
        ctypes.byref(handle),
        0x40000000 | 0x00100000 | 0x00000080,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000080,
        0x00000001 | 0x00000002 | 0x00000004,
        2,
        0x00000020 | 0x00000040 | 0x00200000,
        None,
        0,
    )
    if status < 0:
        raise FileExistsError(name)
    fd = msvcrt.open_osfhandle(
        int(handle.value),
        os.O_WRONLY,
    )
    try:
        _write_all(fd, content)
    finally:
        os.close(fd)


def windows_read_file_at(
    parent_handle: int,
    name: str,
    *,
    max_bytes: int,
) -> bytes:
    from ctypes import wintypes
    import msvcrt

    relative = _relative_object_attributes(parent_handle, name)
    attributes = relative[2]
    io_status_type = relative[3]
    status_block = io_status_type()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_file = ntdll.NtCreateFile
    create_file.restype = ctypes.c_long
    status = create_file(
        ctypes.byref(handle),
        0x80000000 | 0x00100000 | 0x00000080,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        1,
        0x00000020 | 0x00000040 | 0x00200000,
        None,
        0,
    )
    if status < 0:
        raise OSError(int(status), f"cannot open file: {name}")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time_low", wintypes.DWORD),
            ("creation_time_high", wintypes.DWORD),
            ("access_time_low", wintypes.DWORD),
            ("access_time_high", wintypes.DWORD),
            ("write_time_low", wintypes.DWORD),
            ("write_time_high", wintypes.DWORD),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    information = ByHandleFileInformation()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    if not get_information(
        ctypes.c_void_p(handle.value),
        ctypes.byref(information),
    ):
        error = ctypes.get_last_error()
        windows_close_handle(int(handle.value))
        raise OSError(error, f"cannot inspect file: {name}")
    if (
        information.attributes & 0x00000010
        or information.attributes & 0x00000400
    ):
        windows_close_handle(int(handle.value))
        raise ContainerBoundaryError(
            f"file is missing or is a reparse point: {name}"
        )
    size = (
        int(information.size_high) << 32
    ) | int(information.size_low)
    if size > max_bytes:
        windows_close_handle(int(handle.value))
        raise ContainerBoundaryError(
            f"file is too large: {name}"
        )
    fd = msvcrt.open_osfhandle(
        int(handle.value),
        os.O_RDONLY,
    )
    try:
        content = bytearray()
        while chunk := os.read(
            fd,
            min(64 * 1024, max_bytes + 1 - len(content)),
        ):
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ContainerBoundaryError(
                    f"file is too large: {name}"
                )
        return bytes(content)
    finally:
        os.close(fd)


def publish_open_regular_file(
    source_fd: int,
    destination_fd: int,
    name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise OpsMigrationCollision(
            "this platform cannot publish an opened migration file"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = getattr(libc, "linkat", None)
    if linkat is None:
        raise OpsMigrationCollision(
            "this platform cannot publish an opened migration file"
        )
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    source_path = os.fsencode(
        f"/proc/self/fd/{source_fd}"
    )
    if linkat(
        -100,
        source_path,
        destination_fd,
        os.fsencode(name),
        0x400,
    ) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(name)
    raise OSError(
        error_number,
        os.strerror(error_number),
        name,
    )


def rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
    source_bytes = os.fsencode(
        source.name if source_dir_fd is not None else source
    )
    destination_bytes = os.fsencode(
        destination.name
        if destination_dir_fd is not None
        else destination
    )
    source_at = (
        source_dir_fd if source_dir_fd is not None else -100
    )
    destination_at = (
        destination_dir_fd
        if destination_dir_fd is not None
        else -100
    )
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OpsMigrationCollision(
                "this platform cannot guarantee a no-clobber Ops migration"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_at,
            source_bytes,
            destination_at,
            destination_bytes,
            1,
        )
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise OpsMigrationCollision(
                "this platform cannot guarantee a no-clobber Ops migration"
            )
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_at,
            source_bytes,
            destination_at,
            destination_bytes,
            4,
        )
    elif os.name == "nt":
        if (
            source_dir_fd is not None
            or destination_dir_fd is not None
        ):
            raise OpsMigrationCollision(
                "this platform cannot guarantee stable no-clobber migration"
            )
        try:
            os.rename(source, destination)
        except FileExistsError as exc:
            raise OpsMigrationCollision(
                f"destination already exists: {destination.name}"
            ) from exc
        return
    else:
        raise OpsMigrationCollision(
            "this platform cannot guarantee a no-clobber Ops migration"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise OpsMigrationCollision(
            f"destination already exists: {destination.name}"
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        str(source),
    )

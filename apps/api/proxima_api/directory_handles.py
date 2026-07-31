from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DirectoryHandle:
    raw: int
    identity: str
    closed: bool = False


class DirectoryNameError(OSError):
    pass


class PosixDirectoryBackend:
    platform = "posix"

    @staticmethod
    def _flags() -> int:
        flags = os.O_RDONLY
        for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        return flags

    @staticmethod
    def _identity(raw: int) -> str:
        info = os.fstat(raw)
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "not a directory")
        return f"posix:{info.st_dev}:{info.st_ino}"

    def open_absolute(self, path: Path) -> DirectoryHandle:
        if not path.is_absolute():
            raise OSError(errno.EINVAL, "path is not absolute")
        current = DirectoryHandle(
            raw=os.open(path.anchor, self._flags()),
            identity="",
        )
        current.identity = self._identity(current.raw)
        try:
            for component in path.parts[1:]:
                next_handle = self.open_child(current, component)
                self.close(current)
                current = next_handle
        except BaseException:
            self.close(current)
            raise
        return current

    def open_child(self, parent: DirectoryHandle, name: str) -> DirectoryHandle:
        raw = os.open(name, self._flags(), dir_fd=parent.raw)
        try:
            return DirectoryHandle(raw=raw, identity=self._identity(raw))
        except BaseException:
            os.close(raw)
            raise

    @staticmethod
    def list_names(handle: DirectoryHandle) -> list[str]:
        return list(os.listdir(handle.raw))

    @staticmethod
    def component_limit(handle: DirectoryHandle) -> int:
        try:
            return int(os.fpathconf(handle.raw, "PC_NAME_MAX"))
        except (AttributeError, OSError, ValueError):
            return -1

    @staticmethod
    def component_size(name: str) -> int:
        return len(os.fsencode(name))

    def create_staging(
        self,
        parent: DirectoryHandle,
        mode: int,
    ) -> tuple[str, DirectoryHandle]:
        for _ in range(16):
            name = f".proxima-create-{secrets.token_hex(24)}"
            try:
                os.mkdir(name, mode=mode, dir_fd=parent.raw)
            except FileExistsError:
                continue
            try:
                handle = self.open_child(parent, name)
            except BaseException:
                try:
                    os.rmdir(name, dir_fd=parent.raw)
                except OSError:
                    pass
                raise
            return name, handle
        raise FileExistsError(errno.EEXIST, "could not allocate staging directory")

    @staticmethod
    def _rename_noreplace(parent_fd: int, source: str, target: str) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        source_bytes = os.fsencode(source)
        target_bytes = os.fsencode(target)
        if hasattr(libc, "renameat2"):
            rename = libc.renameat2
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            result = rename(
                parent_fd,
                source_bytes,
                parent_fd,
                target_bytes,
                1,
            )
        elif hasattr(libc, "renameatx_np"):
            rename = libc.renameatx_np
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            result = rename(
                parent_fd,
                source_bytes,
                parent_fd,
                target_bytes,
                0x00000004,
            )
        else:
            raise OSError(errno.ENOTSUP, "atomic no-clobber rename is unavailable")
        if result != 0:
            error = ctypes.get_errno()
            if error in (errno.EEXIST, errno.ENOTEMPTY):
                raise FileExistsError(error, os.strerror(error), target)
            raise OSError(error, os.strerror(error), target)

    def publish(
        self,
        parent: DirectoryHandle,
        child: DirectoryHandle,
        staging_name: str,
        final_name: str,
    ) -> None:
        self._rename_noreplace(parent.raw, staging_name, final_name)

    def entry_is_owned(
        self,
        parent: DirectoryHandle,
        name: str,
        child: DirectoryHandle,
    ) -> bool:
        try:
            info = os.stat(name, dir_fd=parent.raw, follow_symlinks=False)
        except OSError:
            return False
        return f"posix:{info.st_dev}:{info.st_ino}" == child.identity

    def _clear_directory(self, handle: DirectoryHandle) -> None:
        for entry in list(self.list_names(handle)):
            try:
                info = os.stat(entry, dir_fd=handle.raw, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                child = self.open_child(handle, entry)
                try:
                    self._clear_directory(child)
                finally:
                    self.close(child)
                os.rmdir(entry, dir_fd=handle.raw)
            else:
                os.unlink(entry, dir_fd=handle.raw)

    def remove_owned(
        self,
        parent: DirectoryHandle,
        name: str,
        child: DirectoryHandle,
    ) -> None:
        candidates = [name, *os.listdir(parent.raw)]
        visited: set[str] = set()
        for candidate in candidates:
            if candidate in visited:
                continue
            visited.add(candidate)
            if not self.entry_is_owned(parent, candidate, child):
                continue
            quarantine = f".proxima-remove-{secrets.token_hex(24)}"
            try:
                self._rename_noreplace(parent.raw, candidate, quarantine)
            except OSError:
                continue
            if not self.entry_is_owned(parent, quarantine, child):
                try:
                    self._rename_noreplace(parent.raw, quarantine, candidate)
                except OSError:
                    pass
                continue
            self._clear_directory(child)
            os.rmdir(quarantine, dir_fd=parent.raw)
            return

    @staticmethod
    def close(handle: DirectoryHandle) -> None:
        if handle.closed:
            return
        handle.closed = True
        os.close(handle.raw)


if os.name == "nt":
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_void_p),
            ("Information", ctypes.c_size_t),
        ]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("CreationTime", wintypes.FILETIME),
            ("LastAccessTime", wintypes.FILETIME),
            ("LastWriteTime", wintypes.FILETIME),
            ("VolumeSerialNumber", wintypes.DWORD),
            ("FileSizeHigh", wintypes.DWORD),
            ("FileSizeLow", wintypes.DWORD),
            ("NumberOfLinks", wintypes.DWORD),
            ("FileIndexHigh", wintypes.DWORD),
            ("FileIndexLow", wintypes.DWORD),
        ]

    class _FileDirectoryInformation(ctypes.Structure):
        _fields_ = [
            ("NextEntryOffset", wintypes.ULONG),
            ("FileIndex", wintypes.ULONG),
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("AllocationSize", ctypes.c_longlong),
            ("FileAttributes", wintypes.ULONG),
            ("FileNameLength", wintypes.ULONG),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


class WindowsDirectoryBackend:
    platform = "windows"

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows directory backend is unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        self.kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        self.ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_ObjectAttributes),
            ctypes.POINTER(_IoStatusBlock),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
        ]
        self.ntdll.NtCreateFile.restype = ctypes.c_long
        self.ntdll.NtQueryDirectoryFile.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_IoStatusBlock),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.BOOLEAN,
            ctypes.c_void_p,
            wintypes.BOOLEAN,
        ]
        self.ntdll.NtQueryDirectoryFile.restype = ctypes.c_long
        self.ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
        self.ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    @staticmethod
    def _value(handle: object) -> int:
        value = handle if isinstance(handle, int) else ctypes.cast(handle, ctypes.c_void_p).value
        if value is None or value == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(value)

    def _info(self, raw: int) -> _ByHandleFileInformation:
        info = _ByHandleFileInformation()
        if not self.kernel32.GetFileInformationByHandle(
            wintypes.HANDLE(raw),
            ctypes.byref(info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not info.FileAttributes & 0x10:
            raise NotADirectoryError(errno.ENOTDIR, "not a directory")
        if info.FileAttributes & 0x400:
            raise OSError(errno.ELOOP, "directory reparse points are not allowed")
        return info

    def _handle(self, raw: int) -> DirectoryHandle:
        info = self._info(raw)
        file_id = (int(info.FileIndexHigh) << 32) | int(info.FileIndexLow)
        return DirectoryHandle(
            raw=raw,
            identity=f"windows:{int(info.VolumeSerialNumber)}:{file_id}",
        )

    def _raise_status(self, status: int, name: str) -> None:
        unsigned = status & 0xFFFFFFFF
        if unsigned == 0xC0000035:
            raise FileExistsError(errno.EEXIST, "directory already exists", name)
        error = int(self.ntdll.RtlNtStatusToDosError(status))
        if error == 206:
            raise DirectoryNameError(
                errno.ENAMETOOLONG,
                ctypes.FormatError(error),
                name,
            )
        if error in (87, 123):
            raise DirectoryNameError(errno.EINVAL, ctypes.FormatError(error), name)
        raise OSError(error, ctypes.FormatError(error), name)

    def _open_relative(
        self,
        parent: DirectoryHandle,
        name: str,
        create: bool = False,
    ) -> DirectoryHandle:
        text = ctypes.create_unicode_buffer(name)
        unicode_name = _UnicodeString(
            Length=len(name.encode("utf-16-le")),
            MaximumLength=(len(name) + 1) * 2,
            Buffer=ctypes.cast(text, wintypes.LPWSTR),
        )
        attributes = _ObjectAttributes(
            Length=ctypes.sizeof(_ObjectAttributes),
            RootDirectory=wintypes.HANDLE(parent.raw),
            ObjectName=ctypes.pointer(unicode_name),
            Attributes=0x40,
            SecurityDescriptor=None,
            SecurityQualityOfService=None,
        )
        io_status = _IoStatusBlock()
        result = wintypes.HANDLE()
        status = int(self.ntdll.NtCreateFile(
            ctypes.byref(result),
            0x00100000 | 0x00010000 | 0x00000001 | 0x00000080,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x10,
            0x7,
            2 if create else 1,
            0x1 | 0x20 | 0x00200000,
            None,
            0,
        ))
        if status < 0:
            self._raise_status(status, name)
        raw = self._value(result)
        try:
            return self._handle(raw)
        except BaseException:
            self.kernel32.CloseHandle(wintypes.HANDLE(raw))
            raise

    def open_absolute(self, path: Path) -> DirectoryHandle:
        if not path.is_absolute():
            raise OSError(errno.EINVAL, "path is not absolute")
        root = self.kernel32.CreateFileW(
            path.anchor,
            0x00100000 | 0x00010000 | 0x00000001 | 0x00000080,
            0x7,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        current = self._handle(self._value(root))
        try:
            for component in path.parts[1:]:
                next_handle = self.open_child(current, component)
                self.close(current)
                current = next_handle
        except BaseException:
            self.close(current)
            raise
        return current

    def open_child(self, parent: DirectoryHandle, name: str) -> DirectoryHandle:
        return self._open_relative(parent, name)

    def list_names(self, handle: DirectoryHandle) -> list[str]:
        names: list[str] = []
        restart = True
        while True:
            buffer = ctypes.create_string_buffer(65536)
            io_status = _IoStatusBlock()
            status = int(self.ntdll.NtQueryDirectoryFile(
                wintypes.HANDLE(handle.raw),
                None,
                None,
                None,
                ctypes.byref(io_status),
                buffer,
                len(buffer),
                1,
                False,
                None,
                restart,
            ))
            restart = False
            unsigned = status & 0xFFFFFFFF
            if unsigned == 0x80000006:
                break
            if status < 0 and unsigned != 0x80000005:
                self._raise_status(status, "")
            offset = 0
            while offset < int(io_status.Information):
                entry = _FileDirectoryInformation.from_buffer(buffer, offset)
                name = ctypes.wstring_at(
                    ctypes.addressof(buffer)
                    + offset
                    + _FileDirectoryInformation.FileName.offset,
                    int(entry.FileNameLength) // 2,
                )
                if name not in (".", ".."):
                    names.append(name)
                if entry.NextEntryOffset == 0:
                    break
                offset += int(entry.NextEntryOffset)
            if status == 0 and int(io_status.Information) == 0:
                break
        return names

    @staticmethod
    def component_limit(handle: DirectoryHandle) -> int:
        return 510

    @staticmethod
    def component_size(name: str) -> int:
        return len(name.encode("utf-16-le"))

    def create_staging(
        self,
        parent: DirectoryHandle,
        mode: int,
    ) -> tuple[str, DirectoryHandle]:
        for _ in range(16):
            name = f".proxima-create-{secrets.token_hex(24)}"
            try:
                return name, self._open_relative(parent, name, create=True)
            except FileExistsError:
                continue
        raise FileExistsError(errno.EEXIST, "could not allocate staging directory")

    def publish(
        self,
        parent: DirectoryHandle,
        child: DirectoryHandle,
        staging_name: str,
        final_name: str,
    ) -> None:
        encoded = final_name.encode("utf-16-le")
        size = max(
            ctypes.sizeof(_FileRenameInformation),
            _FileRenameInformation.FileName.offset + len(encoded),
        )
        buffer = ctypes.create_string_buffer(size)
        info = _FileRenameInformation.from_buffer(buffer)
        info.ReplaceIfExists = False
        info.RootDirectory = wintypes.HANDLE(parent.raw)
        info.FileNameLength = len(encoded)
        ctypes.memmove(
            ctypes.addressof(buffer) + _FileRenameInformation.FileName.offset,
            encoded,
            len(encoded),
        )
        if not self.kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(child.raw),
            3,
            buffer,
            size,
        ):
            error = ctypes.get_last_error()
            if error in (80, 183):
                raise FileExistsError(errno.EEXIST, ctypes.FormatError(error), final_name)
            if error == 206:
                raise DirectoryNameError(
                    errno.ENAMETOOLONG,
                    ctypes.FormatError(error),
                    final_name,
                )
            if error in (87, 123):
                raise DirectoryNameError(
                    errno.EINVAL,
                    ctypes.FormatError(error),
                    final_name,
                )
            raise OSError(error, ctypes.FormatError(error), final_name)

    def entry_is_owned(
        self,
        parent: DirectoryHandle,
        name: str,
        child: DirectoryHandle,
    ) -> bool:
        try:
            visible = self.open_child(parent, name)
        except OSError:
            return False
        try:
            return visible.identity == child.identity
        finally:
            self.close(visible)

    def _delete_handle(self, handle: DirectoryHandle, name: str) -> None:
        deletion = _FileDispositionInformation(DeleteFile=True)
        if not self.kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(handle.raw),
            4,
            ctypes.byref(deletion),
            ctypes.sizeof(deletion),
        ):
            error = ctypes.get_last_error()
            if error not in (2, 3):
                raise OSError(error, ctypes.FormatError(error), name)

    def _open_file(self, parent: DirectoryHandle, name: str) -> DirectoryHandle:
        text = ctypes.create_unicode_buffer(name)
        unicode_name = _UnicodeString(
            Length=len(name.encode("utf-16-le")),
            MaximumLength=(len(name) + 1) * 2,
            Buffer=ctypes.cast(text, wintypes.LPWSTR),
        )
        attributes = _ObjectAttributes(
            Length=ctypes.sizeof(_ObjectAttributes),
            RootDirectory=wintypes.HANDLE(parent.raw),
            ObjectName=ctypes.pointer(unicode_name),
            Attributes=0x40,
            SecurityDescriptor=None,
            SecurityQualityOfService=None,
        )
        io_status = _IoStatusBlock()
        result = wintypes.HANDLE()
        status = int(self.ntdll.NtCreateFile(
            ctypes.byref(result),
            0x00100000 | 0x00010000 | 0x00000080,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            0x7,
            1,
            0x40 | 0x00200000,
            None,
            0,
        ))
        if status < 0:
            self._raise_status(status, name)
        raw = self._value(result)
        return DirectoryHandle(raw=raw, identity="")

    def _clear_directory(self, handle: DirectoryHandle) -> None:
        for entry in list(self.list_names(handle)):
            try:
                child = self.open_child(handle, entry)
            except OSError:
                file_handle = self._open_file(handle, entry)
                try:
                    self._delete_handle(file_handle, entry)
                finally:
                    self.close(file_handle)
                continue
            try:
                self._clear_directory(child)
                self._delete_handle(child, entry)
            finally:
                self.close(child)

    def remove_owned(
        self,
        parent: DirectoryHandle,
        name: str,
        child: DirectoryHandle,
    ) -> None:
        _ = parent
        self._clear_directory(child)
        self._delete_handle(child, name)

    def close(self, handle: DirectoryHandle) -> None:
        if handle.closed:
            return
        handle.closed = True
        if not self.kernel32.CloseHandle(wintypes.HANDLE(handle.raw)):
            raise ctypes.WinError(ctypes.get_last_error())


def directory_backend() -> PosixDirectoryBackend | WindowsDirectoryBackend:
    if os.name == "nt":
        return WindowsDirectoryBackend()
    if os.name == "posix":
        return PosixDirectoryBackend()
    raise OSError(errno.ENOTSUP, f"unsupported directory platform: {sys.platform}")


def directory_identity_for_path(path: Path) -> str:
    if path.is_symlink():
        raise OSError(errno.ELOOP, "directory symlinks are not allowed")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise OSError(errno.ENOENT, "directory is not reachable") from exc
    backend = directory_backend()
    verification_path = path.absolute() if backend.platform == "windows" else resolved
    handle = backend.open_absolute(verification_path)
    try:
        return handle.identity
    finally:
        backend.close(handle)


def unavailable_directory_identity(path: str | Path) -> str:
    digest = hashlib.sha256(
        str(path).encode("utf-8", "surrogatepass")
    ).hexdigest()
    return f"unavailable:{digest}"

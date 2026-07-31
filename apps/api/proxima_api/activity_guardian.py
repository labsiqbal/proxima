from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path


_PYTHON_ENV_KEYS = {
    "PYTHONCASEOK",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
}


def _windows_overlapped_type():
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("internal", ctypes.c_size_t),
            ("internal_high", ctypes.c_size_t),
            ("offset", wintypes.DWORD),
            ("offset_high", wintypes.DWORD),
            ("event", wintypes.HANDLE),
        ]

    return Overlapped


def _windows_lock_file(handle: int) -> None:
    from ctypes import wintypes

    overlapped = _windows_overlapped_type()()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lock_file = kernel32.LockFileEx
    lock_file.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(type(overlapped)),
    ]
    lock_file.restype = wintypes.BOOL
    if not lock_file(
        ctypes.c_void_p(handle),
        0,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))


def _adopt_lock(value: str) -> int:
    """Adopt the shared activity lock via path re-open or inherited FD/handle.

    Path mode is required when the guardian is launched through a process that
    does not inherit the owner's lock FD (preview output broker). Separate
    opens still take independent shared flocks, so exclusive quiescence waits
    for every holder - owner lease and guardian - to exit.
    """
    if value.startswith("path:"):
        path = Path(value.removeprefix("path:"))
        flags = os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            if os.name == "nt":
                import msvcrt

                _windows_lock_file(msvcrt.get_osfhandle(fd))
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_SH)
        except Exception:
            os.close(fd)
            raise
        return fd
    if os.name == "nt":
        import msvcrt

        if not value.startswith("handle:"):
            raise ValueError("missing inherited activity handle")
        handle = int(value.removeprefix("handle:"))
        os.set_handle_inheritable(handle, False)
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR)
        _windows_lock_file(msvcrt.get_osfhandle(fd))
        return fd
    fd = int(value)
    os.set_inheritable(fd, True)
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_SH)
    return fd


def _enable_subreaper() -> None:
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _descendants(parent: int) -> set[int]:
    proc = Path("/proc")
    if not proc.is_dir():
        return set()
    children: dict[int, set[int]] = {}
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        try:
            status = (item / "status").read_text(encoding="utf-8")
            line = next(
                value for value in status.splitlines() if value.startswith("PPid:")
            )
            ppid = int(line.split(":", 1)[1])
        except (OSError, StopIteration, ValueError):
            continue
        children.setdefault(ppid, set()).add(int(item.name))
    found: set[int] = set()
    pending = list(children.get(parent, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return found


def _signal_descendants(value: int) -> None:
    for pid in sorted(_descendants(os.getpid()), reverse=True):
        try:
            os.kill(pid, value)
        except OSError:
            pass


def _wait_for_children() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            time.sleep(0.02)


def _process_start_identity(pid: int) -> str | None:
    if os.name == "nt":
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", wintypes.DWORD),
                ("high", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        get_times.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = FileTime()
            exited = FileTime()
            kernel = FileTime()
            user = FileTime()
            if not get_times(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return str((int(created.high) << 32) | int(created.low))
        finally:
            close_handle(handle)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except OSError:
        return None
    return fields[21] if len(fields) > 21 else None


def _write_record(
    path: str,
    *,
    sentinel_pid: int,
    launcher_pid: int,
    owner_pid: int,
    owner_start: str,
    job_name: str | None,
) -> None:
    if not path:
        return
    record = Path(path)
    payload = {
        "guardian": str(Path(__file__).resolve()),
        "python": str(Path(sys.executable).resolve()),
        "sentinel_pid": sentinel_pid,
        "launcher_pid": launcher_pid,
        "sentinel_start": _process_start_identity(sentinel_pid),
        "owner_pid": owner_pid,
        "owner_start": owner_start,
        "job_name": job_name,
    }
    flags = (
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(record, flags, 0o600)
    try:
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("guardian record write made no progress")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.name == "posix":
        parent_fd = os.open(record.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


def _remove_record(path: str) -> None:
    if not path:
        return
    try:
        record = Path(path)
        record.unlink()
    except FileNotFoundError:
        pass
    else:
        if os.name == "posix":
            parent_fd = os.open(record.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)


def _windows_job(
    name: str,
) -> tuple[int, Callable[[], int]]:
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("per_process_user_time_limit", ctypes.c_longlong),
            ("per_job_user_time_limit", ctypes.c_longlong),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("read_operation_count", ctypes.c_ulonglong),
            ("write_operation_count", ctypes.c_ulonglong),
            ("other_operation_count", ctypes.c_ulonglong),
            ("read_transfer_count", ctypes.c_ulonglong),
            ("write_transfer_count", ctypes.c_ulonglong),
            ("other_transfer_count", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("basic_limit_information", BasicLimitInformation),
            ("io_info", IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        ]

    class BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("total_user_time", ctypes.c_longlong),
            ("total_kernel_time", ctypes.c_longlong),
            ("this_period_total_user_time", ctypes.c_longlong),
            ("this_period_total_kernel_time", ctypes.c_longlong),
            ("total_page_fault_count", wintypes.DWORD),
            ("total_processes", wintypes.DWORD),
            ("active_processes", wintypes.DWORD),
            ("total_terminated_processes", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign_process.restype = wintypes.BOOL
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    query_information = kernel32.QueryInformationJobObject
    query_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    job = create_job(None, name)
    if not job:
        raise OSError(ctypes.get_last_error(), "cannot create activity job")
    limits = ExtendedLimitInformation()
    limits.basic_limit_information.limit_flags = 0x00002000
    if not set_information(
        ctypes.c_void_p(job),
        9,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = ctypes.get_last_error()
        close_handle(job)
        raise OSError(error, "cannot configure activity job")
    if not assign_process(
        ctypes.c_void_p(job),
        get_current_process(),
    ):
        error = ctypes.get_last_error()
        close_handle(job)
        raise OSError(error, "cannot own activity process tree")

    def active_processes() -> int:
        value = BasicAccountingInformation()
        if not query_information(
            ctypes.c_void_p(job),
            1,
            ctypes.byref(value),
            ctypes.sizeof(value),
            None,
        ):
            raise OSError(
                ctypes.get_last_error(),
                "cannot inspect activity process tree",
            )
        return int(value.active_processes)

    return int(job), active_processes


def _run_writer(
    activity_fd: int,
    command: list[str],
    target_cwd: str,
    target_env: dict[str, str],
    *,
    job: int | None = None,
    active_processes: Callable[[], int] | None = None,
) -> int:
    _enable_subreaper()
    requested_signal: int | None = None

    def forward_signal(value: int) -> None:
        nonlocal requested_signal
        requested_signal = value
        _signal_descendants(value)

    signals = [
        value
        for value in (
            signal.SIGTERM,
            signal.SIGINT,
            getattr(signal, "SIGHUP", None),
        )
        if value is not None
    ]
    for value in signals:
        signal.signal(
            value,
            lambda _signum, _frame, sent=value: forward_signal(sent),
        )
    options: dict[str, object] = {
        "cwd": target_cwd,
        "env": target_env,
    }
    if os.name == "nt":
        options["close_fds"] = False
    else:
        os.set_inheritable(activity_fd, False)
        options["close_fds"] = True
    if requested_signal is not None:
        os.close(activity_fd)
        if job is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                ctypes.c_void_p(job)
            )
        return 128 + requested_signal
    process = subprocess.Popen(command, **options)
    if requested_signal is not None:
        _signal_descendants(requested_signal)
    result = process.wait()
    if sys.platform.startswith("linux"):
        _wait_for_children()
    elif os.name == "nt":
        assert active_processes is not None
        while active_processes() > 1:
            time.sleep(0.02)
    else:
        while True:
            time.sleep(3600)
    os.close(activity_fd)
    if job is not None:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(ctypes.c_void_p(job))
    return int(result)


def _attached_to_controlling_tty() -> bool:
    """True when this process still owns a controlling terminal.

    Project terminals ``pty.fork()`` the guardian as the interactive session
    leader. Detaching via ``setsid()`` would drop that ctty and break job
    control / tty signal delivery, so PTY-attached guardians stay in-session.
    """
    for fd in (0, 1, 2):
        try:
            if os.isatty(fd):
                return True
        except OSError:
            continue
    return False


def _run_linux_attached(
    activity_fd: int,
    command: list[str],
    target_cwd: str,
    target_env: dict[str, str],
    record_path: str,
    owner_pid: int,
    owner_start: str,
) -> int:
    """Run the writer in-process so a PTY session keeps its controlling tty."""
    record_created = False
    try:
        _write_record(
            record_path,
            sentinel_pid=os.getpid(),
            launcher_pid=os.getpid(),
            owner_pid=owner_pid,
            owner_start=owner_start,
            job_name=None,
        )
        record_created = True
        return _run_writer(
            activity_fd,
            command,
            target_cwd,
            target_env,
        )
    finally:
        if record_created:
            _remove_record(record_path)


def _run_linux_sentinel(
    activity_fd: int,
    command: list[str],
    target_cwd: str,
    target_env: dict[str, str],
    record_path: str,
    owner_pid: int,
    owner_start: str,
) -> int:
    status_read, status_write = os.pipe()
    sentinel = os.fork()
    if sentinel == 0:
        os.close(status_read)
        record_created = False
        try:
            os.setsid()
        except OSError:
            pass
        try:
            _write_record(
                record_path,
                sentinel_pid=os.getpid(),
                launcher_pid=os.getppid(),
                owner_pid=owner_pid,
                owner_start=owner_start,
                job_name=None,
            )
            record_created = True
            result = _run_writer(
                activity_fd,
                command,
                target_cwd,
                target_env,
            )
            os.write(status_write, str(result).encode("ascii"))
        except BaseException:
            try:
                os.write(status_write, b"70")
            except OSError:
                pass
        finally:
            if record_created:
                _remove_record(record_path)
            os._exit(0)

    os.close(status_write)
    os.close(activity_fd)
    signals = [
        value
        for value in (
            signal.SIGTERM,
            signal.SIGINT,
            getattr(signal, "SIGHUP", None),
        )
        if value is not None
    ]
    for value in signals:
        signal.signal(
            value,
            lambda _signum, _frame, sent=value: os.kill(sentinel, sent),
        )
    raw = b""
    while chunk := os.read(status_read, 16):
        raw += chunk
    os.close(status_read)
    try:
        os.waitpid(sentinel, 0)
    except ChildProcessError:
        pass
    try:
        return int(raw or b"70")
    except ValueError:
        return 70


def main() -> int:
    if len(sys.argv) < 8 or sys.argv[6] != "--":
        return 64
    trusted_cwd = str(Path(__file__).resolve().parent)
    target_cwd = os.getcwd()
    target_env = dict(os.environ)
    for key in _PYTHON_ENV_KEYS:
        os.environ.pop(key, None)
    os.chdir(trusted_cwd)
    activity_fd = _adopt_lock(sys.argv[1])
    record_path = sys.argv[2]
    guardian_id = sys.argv[3]
    owner_pid = int(sys.argv[4])
    owner_start = sys.argv[5]
    command = sys.argv[7:]
    if sys.platform.startswith("linux"):
        if _attached_to_controlling_tty():
            return _run_linux_attached(
                activity_fd,
                command,
                target_cwd,
                target_env,
                record_path,
                owner_pid,
                owner_start,
            )
        return _run_linux_sentinel(
            activity_fd,
            command,
            target_cwd,
            target_env,
            record_path,
            owner_pid,
            owner_start,
        )
    job_name = f"Local\\ProximaActivity-{guardian_id}"
    job, active_processes = _windows_job(job_name)
    record_created = False
    try:
        _write_record(
            record_path,
            sentinel_pid=os.getpid(),
            launcher_pid=os.getpid(),
            owner_pid=owner_pid,
            owner_start=owner_start,
            job_name=job_name,
        )
        record_created = True
        return _run_writer(
            activity_fd,
            command,
            target_cwd,
            target_env,
            job=job,
            active_processes=active_processes,
        )
    finally:
        if record_created:
            _remove_record(record_path)


if __name__ == "__main__":
    raise SystemExit(main())

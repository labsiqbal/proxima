"""Mandatory candidate process and filesystem isolation."""
from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class SandboxError(RuntimeError):
    pass


_INPUT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
_SYSTEM_ROOTS = (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"), Path("/sbin"))


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def _real(path: Path) -> Path:
    absolute = path.absolute()
    resolved = path.resolve()
    if absolute != resolved or path.is_symlink():
        raise SandboxError("sandbox paths must not contain symlinks")
    return resolved


def _directory_args(path: Path) -> list[str]:
    directories: list[Path] = []
    current = path
    while current != current.parent:
        directories.append(current)
        current = current.parent
    result: list[str] = []
    for directory in reversed(directories):
        result.extend(("--dir", str(directory)))
    return result


def _allocated_bytes(root: Path, excluded: Path | None = None) -> int:
    total = 0
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_links = [
            current_path / name
            for name in directories
            if (current_path / name).is_symlink()
        ]
        directories[:] = [
            name
            for name in directories
            if not (current_path / name).is_symlink()
        ]
        for path in [
            current_path,
            *directory_links,
            *(current_path / name for name in files),
        ]:
            if excluded is not None and path == excluded:
                continue
            try:
                value = path.lstat()
            except FileNotFoundError:
                continue
            total += int(value.st_blocks) * 512
    return total


def _candidate_writable(path: Path) -> None:
    values = [path]
    if path.is_dir():
        for current, directories, files in os.walk(path, followlinks=False):
            current_path = Path(current)
            values.extend(current_path / name for name in directories)
            values.extend(current_path / name for name in files)
    for value in values:
        if value.is_symlink():
            continue
        current = value.stat().st_mode
        if value.is_dir():
            value.chmod(0o777)
        else:
            value.chmod(0o777 if current & 0o111 else 0o666)


@dataclass(frozen=True)
class CandidateSandbox:
    root: Path
    release: Path
    database: Path
    workspace: Path
    runner_home: Path
    port: int
    cpu_seconds: int = 120
    memory_bytes: int = 1024 * 1024 * 1024
    file_bytes: int = 2 * 1024 * 1024 * 1024
    process_limit: int = 128
    output_bytes: int = 16 * 1024 * 1024
    storage_bytes: int = 4 * 1024 * 1024 * 1024
    reserve_bytes: int = 256 * 1024 * 1024
    tmpfs_bytes: int = 512 * 1024 * 1024

    def validate(self, protected: Sequence[Path]) -> None:
        if not (1024 <= self.port <= 65535):
            raise SandboxError("candidate port is invalid")
        root = _real(self.root)
        if not root.is_dir():
            raise SandboxError("candidate sandbox root is unavailable")
        for value in (self.database, self.workspace, self.runner_home):
            resolved = _real(value)
            if not _contains(root, resolved):
                raise SandboxError("candidate writable path escapes sandbox root")
        release = _real(self.release)
        if not release.is_dir():
            raise SandboxError("candidate release is unavailable")
        for value in protected:
            resolved = value.resolve()
            if _contains(root, resolved) or _contains(resolved, root):
                raise SandboxError("candidate sandbox overlaps a protected path")

    def environment(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        result = {
            "HOME": str(self.runner_home),
            "USER": "candidate",
            "LOGNAME": "candidate",
            "PATH": f"/opt/proxima-tools:{_SYSTEM_PATH}",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": "/tmp",
            "PYTHONNOUSERSITE": "1",
            "PROXIMA_DB_PATH": str(self.database),
            "PROXIMA_WORKSPACE_ROOT": str(self.workspace),
            "PROXIMA_HERMES_PROFILES_ROOT": str(self.runner_home),
            "PROXIMA_CANDIDATE_MODE": "1",
            "PROXIMA_CANDIDATE_PORT": str(self.port),
            "PROXIMA_WEB_DIST": str(self.release / "apps" / "web" / "dist"),
            "PROXIMA_UPDATE_CHECK": "0",
            "PROXIMA_FEATURE_SAFE_SELF_UPDATE": "0",
            "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "0",
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "all_proxy": "",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        if overrides:
            result.update({str(key): str(value) for key, value in overrides.items()})
        return result

    def _command(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        writable_paths: Sequence[Path],
        read_only_paths: Sequence[Path],
        pinned_readonly: Mapping[Path, int],
        writable_overlays: Mapping[Path, Path],
        inputs: Mapping[str, Path],
        tools: Mapping[str, Path],
        environment: Mapping[str, str],
        network_loopback: bool,
        namespace_root: bool,
        memory_bytes: int,
        process_limit: int,
    ) -> list[str]:
        bwrap = shutil.which("bwrap", path=_SYSTEM_PATH)
        if os.name != "posix" or not bwrap:
            raise SandboxError("candidate isolation backend is unavailable")
        if not argv or any("\0" in value for value in argv):
            raise SandboxError("candidate command is invalid")
        root = self.root.resolve()
        writable = [_real(path) for path in writable_paths]
        readonly = [_real(path) for path in read_only_paths]
        overlay_sources = [_real(path) for path in writable_overlays]
        overlay_targets = [path.absolute() for path in writable_overlays.values()]
        if any(not path.exists() for path in (*writable, *readonly, *overlay_sources)):
            raise SandboxError("candidate mount is unavailable")
        if any(not _contains(root, path) for path in (*writable, *overlay_sources)):
            raise SandboxError("candidate writable mount escapes sandbox root")
        release = self.release.resolve()
        if (
            len(set(overlay_targets)) != len(overlay_targets)
            or any(
                target != target.resolve()
                or not _contains(release, target)
                or target == release
                for target in overlay_targets
            )
        ):
            raise SandboxError("candidate writable overlay target is invalid")
        if any(
            _contains(left, right) or _contains(right, left)
            for left in writable
            for right in readonly
        ):
            raise SandboxError("candidate writable and read-only mounts overlap")
        if any(
            _contains(left, right) or _contains(right, left)
            for left in overlay_sources
            for right in readonly
        ):
            raise SandboxError("candidate overlay source overlaps a read-only mount")
        resolved_cwd = _real(cwd)
        if not any(
            _contains(path, resolved_cwd)
            for path in (*writable, *readonly, *overlay_targets)
        ):
            raise SandboxError("candidate working directory is not mounted")
        for path in (*writable, *overlay_sources):
            _candidate_writable(path)

        command = list(argv)
        tool_mounts: dict[str, Path] = {}
        first = command[0]
        if "/" not in first:
            if first in tools:
                tool_mounts[first] = _real(tools[first])
                command[0] = f"/opt/proxima-tools/{first}"
            else:
                executable = shutil.which(first, path=_SYSTEM_PATH)
                if executable is None:
                    raise SandboxError(f"candidate tool is unavailable: {first}")
                command[0] = executable
        else:
            lexical = Path(first)
            if not lexical.is_absolute():
                lexical = resolved_cwd / lexical
            absolute = lexical.absolute()
            target = lexical.resolve()
            if not any(_contains(path, absolute) for path in (*writable, *readonly)):
                if not any(_contains(system, target) for system in _SYSTEM_ROOTS):
                    raise SandboxError("candidate executable is outside mounted paths")
            command[0] = str(absolute)

        sandbox = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--uid",
            "0" if network_loopback or namespace_root else "65534",
            "--gid",
            "0" if network_loopback or namespace_root else "65534",
            "--cap-drop",
            "ALL",
        ]
        if network_loopback:
            sandbox.extend(
                (
                    "--cap-add",
                    "CAP_NET_ADMIN",
                    "--cap-add",
                    "CAP_SETPCAP",
                )
            )
        for source in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(source).exists():
                sandbox.extend(("--ro-bind", source, source))
        sandbox.extend(
            (
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--size",
                str(self.tmpfs_bytes),
                "--tmpfs",
                "/dev/shm",
                "--size",
                str(self.tmpfs_bytes),
                "--tmpfs",
                "/tmp",
                "--dir",
                "/run",
                "--size",
                str(min(self.tmpfs_bytes, 64 * 1024 * 1024)),
                "--tmpfs",
                "/run",
            )
        )
        sandbox.extend(("--dir", "/opt", "--dir", "/opt/proxima-inputs", "--dir", "/opt/proxima-tools"))
        for source in (Path("/etc/fonts"), Path("/etc/ssl/certs")):
            if source.is_dir() and not source.is_symlink():
                sandbox.extend(_directory_args(source.parent))
                sandbox.extend(("--ro-bind", str(source), str(source)))
        for path in readonly:
            sandbox.extend(_directory_args(path.parent))
            descriptor = pinned_readonly.get(path)
            if descriptor is None:
                raise SandboxError("candidate read-only mount is not pinned")
            sandbox.extend(("--ro-bind-fd", str(descriptor), str(path)))
        for path in writable:
            sandbox.extend(_directory_args(path.parent))
            sandbox.extend(("--bind", str(path), str(path)))
        for source, target in sorted(
            (
                (_real(source), target.absolute())
                for source, target in writable_overlays.items()
            ),
            key=lambda item: str(item[1]),
        ):
            sandbox.extend(_directory_args(target.parent))
            sandbox.extend(("--bind", str(source), str(target)))
        for name, source in sorted(inputs.items()):
            if not _INPUT_NAME.fullmatch(name):
                raise SandboxError("candidate input mount name is invalid")
            resolved = _real(source)
            if not resolved.exists():
                raise SandboxError("candidate read-only input is unavailable")
            if any(
                _contains(path, resolved) or _contains(resolved, path)
                for path in writable
            ):
                raise SandboxError("candidate input overlaps a writable mount")
            sandbox.extend(("--ro-bind", str(resolved), f"/opt/proxima-inputs/{name}"))
        for name, source in sorted(tool_mounts.items()):
            if not _INPUT_NAME.fullmatch(name) or not source.is_file():
                raise SandboxError("candidate tool mount is invalid")
            sandbox.extend(("--ro-bind", str(source), f"/opt/proxima-tools/{name}"))
        for key, value in sorted(environment.items()):
            if "\0" in key or "\0" in value or "=" in key:
                raise SandboxError("candidate environment is invalid")
            sandbox.extend(("--setenv", key, value))
        sandbox.extend(
            (
                "--chdir",
                str(resolved_cwd),
                "--",
                "/usr/bin/prlimit",
                f"--cpu={self.cpu_seconds}:{self.cpu_seconds}",
                f"--as={memory_bytes}:{memory_bytes}",
                f"--fsize={self.file_bytes}:{self.file_bytes}",
                f"--nproc={process_limit}:{process_limit}",
                "--",
                *command,
            )
        )
        return sandbox

    @staticmethod
    def _kill(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        writable_paths: Sequence[Path],
        read_only_paths: Sequence[Path] = (),
        inputs: Mapping[str, Path] | None = None,
        tools: Mapping[str, Path] | None = None,
        environment: Mapping[str, str] | None = None,
        network_loopback: bool = False,
        namespace_root: bool = False,
        memory_bytes: int | None = None,
        process_limit: int | None = None,
        writable_overlays: Mapping[Path, Path] | None = None,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[bytes]:
        self.validate(())
        address_space_bytes = self.memory_bytes if memory_bytes is None else memory_bytes
        command_process_limit = (
            self.process_limit if process_limit is None else process_limit
        )
        if (
            address_space_bytes <= 0
            or command_process_limit <= 0
            or self.storage_bytes <= 0
            or self.reserve_bytes <= 0
            or self.tmpfs_bytes <= 0
            or self.file_bytes <= 0
            or self.file_bytes > self.storage_bytes
        ):
            raise SandboxError("candidate storage policy is invalid")
        reserve = self.root / f".candidate-reserve-{uuid.uuid4().hex}"
        initial = _allocated_bytes(self.root)
        if initial > self.storage_bytes:
            raise SandboxError("candidate run storage quota already exceeded")
        free = shutil.disk_usage(self.root).free
        if free < self.reserve_bytes + (self.storage_bytes - initial):
            raise SandboxError("candidate run lacks reserved live storage capacity")
        descriptor = os.open(
            reserve,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o000,
        )
        try:
            if not hasattr(os, "posix_fallocate"):
                raise SandboxError("candidate storage reservation is unavailable")
            os.posix_fallocate(descriptor, 0, self.reserve_bytes)
            os.fsync(descriptor)
        except OSError as exc:
            os.close(descriptor)
            reserve.unlink(missing_ok=True)
            raise SandboxError("candidate storage reservation failed") from exc
        os.close(descriptor)
        pinned_readonly: dict[Path, int] = {}
        try:
            path_flag = getattr(os, "O_PATH", 0)
            if not path_flag:
                raise SandboxError("candidate mount pinning is unavailable")
            for path in read_only_paths:
                resolved = _real(path)
                flags = path_flag | getattr(os, "O_NOFOLLOW", 0)
                if resolved.is_dir():
                    flags |= getattr(os, "O_DIRECTORY", 0)
                try:
                    pinned_readonly[resolved] = os.open(resolved, flags)
                except OSError as exc:
                    raise SandboxError("candidate read-only mount cannot be pinned") from exc
            command = self._command(
                argv,
                cwd=cwd,
                writable_paths=writable_paths,
                read_only_paths=read_only_paths,
                pinned_readonly=pinned_readonly,
                writable_overlays=writable_overlays or {},
                inputs=inputs or {},
                tools=tools or {},
                environment=self.environment(environment),
                network_loopback=network_loopback,
                namespace_root=namespace_root,
                memory_bytes=address_space_bytes,
                process_limit=command_process_limit,
            )
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=tuple(pinned_readonly.values()),
            )
            for pinned in pinned_readonly.values():
                os.close(pinned)
            pinned_readonly.clear()
            if process.stdout is None:
                self._kill(process)
                raise SandboxError("candidate output pipe is unavailable")
            output_descriptor = process.stdout.fileno()
            os.set_blocking(output_descriptor, False)
            deadline = time.monotonic() + timeout
            storage_deadline = time.monotonic()
            output = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    self._kill(process)
                    raise SandboxError("candidate command timed out")
                if time.monotonic() >= storage_deadline:
                    if _allocated_bytes(self.root, reserve) > self.storage_bytes:
                        self._kill(process)
                        raise SandboxError("candidate run storage quota exceeded")
                    storage_deadline = time.monotonic() + 0.5
                if shutil.disk_usage(self.root).free < 16 * 1024 * 1024:
                    self._kill(process)
                    raise SandboxError("candidate exhausted unreserved storage")
                ready, _, _ = select.select([output_descriptor], [], [], 0.05)
                if ready:
                    try:
                        chunk = os.read(output_descriptor, 65536)
                    except BlockingIOError:
                        chunk = None
                    if chunk:
                        output.extend(chunk)
                        if len(output) > self.output_bytes:
                            self._kill(process)
                            raise SandboxError("candidate command output limit exceeded")
                    elif chunk == b"" and process.poll() is not None:
                        break
                elif process.poll() is not None:
                    break
            if _allocated_bytes(self.root, reserve) > self.storage_bytes:
                raise SandboxError("candidate run storage quota exceeded")
            return subprocess.CompletedProcess(
                list(argv),
                process.wait(),
                bytes(output),
            )
        finally:
            for pinned in pinned_readonly.values():
                os.close(pinned)
            reserve.unlink(missing_ok=True)

"""Candidate subprocess limits and isolated filesystem contract."""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class SandboxError(RuntimeError):
    pass


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

    def validate(self, protected: Sequence[Path]) -> None:
        if not (1024 <= self.port <= 65535):
            raise SandboxError("candidate port is invalid")
        for value in (self.root, self.release, self.database, self.workspace, self.runner_home):
            if not value.is_absolute() or value.is_symlink():
                raise SandboxError("candidate path must be absolute and real")
        root = self.root.resolve()
        if any(root not in value.resolve().parents and value.resolve() != root for value in (self.release, self.database, self.workspace, self.runner_home)):
            raise SandboxError("candidate path escapes sandbox root")
        for value in protected:
            resolved = value.resolve()
            if root == resolved or root in resolved.parents or resolved in root.parents:
                raise SandboxError("candidate sandbox overlaps trusted or live path")

    def environment(self) -> dict[str, str]:
        return {
            "HOME": str(self.runner_home), "PROXIMA_DB_PATH": str(self.database),
            "PROXIMA_WORKSPACE_ROOT": str(self.workspace), "PROXIMA_HERMES_PROFILES_ROOT": str(self.runner_home),
            "PROXIMA_CANDIDATE_MODE": "1", "PROXIMA_CANDIDATE_PORT": str(self.port),
            "NO_PROXY": "*", "http_proxy": "", "https_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "",
        }

    def preexec(self) -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (self.memory_bytes, self.memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (self.file_bytes, self.file_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))

    def run(self, argv: Sequence[str], *, cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
        self.validate(())
        # Network isolation is a hard precondition. An unqualified host does not
        # get a best-effort subprocess that can fetch packages or call home.
        if os.name != "posix" or shutil.which("unshare") is None:
            raise SandboxError("candidate network isolation is unavailable")
        return subprocess.run(["unshare", "--net", "--", *argv], cwd=cwd, env=self.environment(), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                              preexec_fn=self.preexec, check=False)

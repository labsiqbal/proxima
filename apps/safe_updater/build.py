"""Fixed offline candidate build manifest owned by the updater."""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .manifest import REQUIRED_LOCK_PATHS


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildStep:
    name: str
    argv: tuple[str, ...]
    cwd: str = "."


# Do not derive this from candidate files. Changing it requires updater bootstrap.
BUILD_MANIFEST: tuple[BuildStep, ...] = (
    BuildStep("python-lock", ("uv", "sync", "--locked", "--offline"), "apps/api"),
    BuildStep("python-lint", ("uv", "run", "ruff", "check", "proxima_api", "tests"), "apps/api"),
    BuildStep("python-tests", ("uv", "run", "pytest", "-q", "tests"), "apps/api"),
    BuildStep("web-lock", ("npm", "ci", "--offline"), "apps/web"),
    BuildStep("web-tests", ("npm", "test"), "apps/web"),
    BuildStep("web-types", ("npx", "tsc", "--noEmit"), "apps/web"),
    BuildStep("web-build", ("npm", "run", "build"), "apps/web"),
    BuildStep("docs", (".venv/bin/python", "../../scripts/gen_docs.py"), "apps/api"),
    BuildStep("docs-clean", ("git", "diff", "--exit-code", "--", "docs/reference/api.md", "docs/reference/database.md")),
    BuildStep("migration-tests", ("uv", "run", "pytest", "-q", "tests/test_migrations.py"), "apps/api"),
)


@dataclass(frozen=True)
class BuildResult:
    logs: dict[str, bytes]
    lock_digests: dict[str, str]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OfflineBuilder:
    """Executes only the controller manifest with an empty network proxy env."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run) -> None:
        self.run = run

    def build(self, release: Path, *, cache_root: Path) -> BuildResult:
        if not release.is_dir() or release.is_symlink() or not cache_root.is_dir():
            raise BuildError("candidate release or offline cache is unavailable")
        locks = {rel: release.joinpath(*rel.split("/")).resolve() for rel in REQUIRED_LOCK_PATHS}
        if any(not path.is_file() or release.resolve() not in path.parents for path in locks.values()):
            raise BuildError("candidate lockfile is missing or unsafe")
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(cache_root),
            "UV_OFFLINE": "1",
            "npm_config_offline": "true",
            "NO_PROXY": "*",
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
        }
        logs: dict[str, bytes] = {}
        for step in BUILD_MANIFEST:
            cwd = release / step.cwd
            if not cwd.is_dir():
                raise BuildError(f"build manifest working directory is missing: {step.name}")
            completed = self.run(
                list(step.argv), cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            output = completed.stdout or b""
            logs[step.name] = output
            if completed.returncode:
                raise BuildError(f"candidate build step failed: {step.name}")
        return BuildResult(logs, {rel: _sha(path) for rel, path in locks.items()})

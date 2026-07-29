"""Fixed offline candidate build manifest owned by the updater."""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from .manifest import REQUIRED_LOCK_PATHS
from .sandbox import CandidateSandbox, SandboxError


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildStep:
    name: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout: int = 300


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
    """Executes only the controller manifest inside the candidate sandbox."""

    @staticmethod
    def _tools() -> dict[str, Path]:
        result: dict[str, Path] = {}
        for step in BUILD_MANIFEST:
            name = step.argv[0]
            if "/" in name or name in result:
                continue
            executable = shutil.which(name)
            if executable is None:
                raise BuildError(f"build manifest tool is unavailable: {name}")
            path = Path(executable)
            if not any(
                path.resolve() == root or root in path.resolve().parents
                for root in (Path("/usr"), Path("/bin"), Path("/sbin"))
            ):
                result[name] = path.resolve()
        return result

    def build(
        self,
        release: Path,
        *,
        cache_root: Path,
        sandbox: CandidateSandbox,
    ) -> BuildResult:
        if (
            not release.is_dir()
            or release.is_symlink()
            or not cache_root.is_dir()
            or cache_root.is_symlink()
            or sandbox.release.resolve() != release.resolve()
        ):
            raise BuildError("candidate release or offline cache is unavailable")
        locks = {rel: release.joinpath(*rel.split("/")).resolve() for rel in REQUIRED_LOCK_PATHS}
        if any(not path.is_file() or release.resolve() not in path.parents for path in locks.values()):
            raise BuildError("candidate lockfile is missing or unsafe")
        environment = {
            "UV_OFFLINE": "1",
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_CACHE_DIR": str(cache_root / ".cache" / "uv"),
            "npm_config_offline": "true",
            "npm_config_cache": str(cache_root / ".npm"),
            "npm_config_logs_dir": str(sandbox.runner_home / "npm-logs"),
            "npm_config_update_notifier": "false",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        tools = self._tools()
        logs: dict[str, bytes] = {}
        for step in BUILD_MANIFEST:
            cwd = release / step.cwd
            if not cwd.is_dir():
                raise BuildError(f"build manifest working directory is missing: {step.name}")
            try:
                completed = sandbox.run(
                    step.argv,
                    cwd=cwd,
                    writable_paths=(release, cache_root, sandbox.runner_home),
                    tools=tools,
                    environment=environment,
                    timeout=step.timeout,
                )
            except SandboxError as exc:
                raise BuildError(str(exc)) from exc
            output = completed.stdout or b""
            logs[step.name] = output
            if completed.returncode:
                raise BuildError(f"candidate build step failed: {step.name}")
        return BuildResult(logs, {rel: _sha(path) for rel, path in locks.items()})

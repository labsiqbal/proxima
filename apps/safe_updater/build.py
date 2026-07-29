"""Fixed offline candidate build manifest owned by the updater."""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .durability import write_all
from .manifest import REQUIRED_LOCK_PATHS
from .sandbox import CandidateSandbox, SandboxError
from .tree import regular_file_digests


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
    BuildStep("migration-tests", ("uv", "run", "pytest", "-q", "tests/test_migrations.py"), "apps/api"),
)


@dataclass(frozen=True)
class BuildResult:
    logs: dict[str, bytes]
    lock_digests: dict[str, str]
    artifacts: tuple[tuple[str, Path], ...] = ()

    def artifact(self, name: str) -> Path:
        result = dict(self.artifacts).get(name)
        if result is None:
            raise BuildError(f"candidate build artifact is unavailable: {name}")
        return result


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
        initial_locks = {rel: _sha(path) for rel, path in locks.items()}
        output_root = sandbox.root / "build-outputs"
        if output_root.exists() or output_root.is_symlink():
            raise BuildError("candidate build output root must be new")
        outputs = {
            "python-environment": output_root / "python-environment",
            "web-dependencies": output_root / "web-dependencies",
            "web-dist": output_root / "web-dist",
            "docs-reference": output_root / "docs-reference",
        }
        output_root.mkdir(mode=0o700)
        for path in outputs.values():
            path.mkdir(mode=0o700)
        for name in ("api.md", "database.md"):
            source = release / "docs" / "reference" / name
            if not source.is_file() or source.is_symlink():
                raise BuildError("candidate generated documentation source is unavailable")
            destination = outputs["docs-reference"] / name
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                payload = source.read_bytes()
                write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        placeholders = {
            release / "apps" / "api" / ".venv",
            release / "apps" / "web" / "node_modules",
            release / "apps" / "web" / "dist",
        }
        for path in placeholders:
            if path.exists() or path.is_symlink():
                raise BuildError("authenticated source contains a build output")
            path.mkdir(mode=0o700)
        for path in sorted(
            [release, *release.rglob("*")],
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            if path.is_symlink():
                raise BuildError("materialized candidate source contains a symlink")
            if path.is_dir():
                path.chmod(0o555)
            else:
                path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        environment = {
            "UV_OFFLINE": "1",
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_CACHE_DIR": str(cache_root / ".cache" / "uv"),
            "npm_config_offline": "true",
            "npm_config_cache": str(cache_root / ".npm"),
            "npm_config_logs_dir": str(sandbox.runner_home / "npm-logs"),
            "npm_config_update_notifier": "false",
            "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "RUFF_CACHE_DIR": str(sandbox.runner_home / "ruff-cache"),
        }
        overlays = {
            outputs["python-environment"]: release / "apps" / "api" / ".venv",
            outputs["web-dependencies"]: release / "apps" / "web" / "node_modules",
            outputs["web-dist"]: release / "apps" / "web" / "dist",
            outputs["docs-reference"]: release / "docs" / "reference",
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
                    writable_paths=(cache_root, sandbox.runner_home),
                    read_only_paths=(release,),
                    writable_overlays=overlays,
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
        for name in ("api.md", "database.md"):
            if _sha(outputs["docs-reference"] / name) != _sha(
                release / "docs" / "reference" / name
            ):
                raise BuildError("generated candidate documentation differs from authenticated source")
        if {rel: _sha(path) for rel, path in locks.items()} != initial_locks:
            raise BuildError("candidate lockfiles changed during the isolated build")
        if not regular_file_digests(outputs["web-dist"]):
            raise BuildError("candidate web build produced no static assets")
        return BuildResult(
            logs,
            initial_locks,
            tuple(sorted(outputs.items())),
        )

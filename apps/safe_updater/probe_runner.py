"""Controller orchestration for the policy-pinned trusted probe suite."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .durability import write_all
from .layout import COMMIT, RELEASE_ID
from .sandbox import CandidateSandbox, SandboxError
from .trusted_probes import TrustedProbeBundle


class ProbeError(RuntimeError):
    pass


# Current Chromium processes can reserve multiple virtual-memory cages totaling
# more than 64 GiB before rendering. RLIMIT_AS measures those reservations rather
# than resident memory, so the trusted browser needs a larger, still finite
# address-space ceiling than build and migration commands.
TRUSTED_BROWSER_ADDRESS_SPACE_BYTES = 128 * 1024 * 1024 * 1024
TRUSTED_BROWSER_PROCESS_LIMIT = 256


@dataclass(frozen=True)
class CandidateIdentity:
    release_id: str
    commit: str
    asset_digest: str
    version: str

    def validate(self) -> None:
        if not RELEASE_ID.fullmatch(self.release_id) or not COMMIT.fullmatch(self.commit):
            raise ProbeError("candidate identity is invalid")
        if self.release_id.split("-")[1] != self.commit:
            raise ProbeError("candidate release and commit identity mismatch")
        if len(self.asset_digest) != 64 or set(self.asset_digest) - set("0123456789abcdef"):
            raise ProbeError("candidate asset digest is invalid")
        if not self.version or len(self.version) > 64 or any(ord(value) < 32 for value in self.version):
            raise ProbeError("candidate version identity is invalid")


@dataclass(frozen=True)
class TrustedProbeResult:
    raw: bytes
    results: dict[str, Any]


def asset_manifest_digest(web_dist: Path) -> str:
    import hashlib

    if not web_dist.is_dir() or web_dist.is_symlink():
        raise ProbeError("candidate static asset directory is unavailable")
    files: list[tuple[str, str]] = []
    for path in sorted(web_dist.rglob("*")):
        if path.is_symlink():
            raise ProbeError("candidate static assets contain a symlink")
        if path.is_file():
            files.append(
                (
                    path.relative_to(web_dist).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    if not files:
        raise ProbeError("candidate static assets are empty")
    return hashlib.sha256(json.dumps(files, separators=(",", ":")).encode()).hexdigest()


class TrustedProbeRunner:
    """Starts the frozen release and requires the installed probe suite to pass."""

    @staticmethod
    def _browser() -> tuple[str, dict[str, Path]]:
        for name in ("chromium", "chromium-browser", "google-chrome"):
            executable = shutil.which(name)
            if executable is None:
                continue
            path = Path(executable).resolve()
            if Path("/usr") in path.parents or Path("/bin") in path.parents:
                return str(path), {}
            return f"/opt/proxima-inputs/browser/{path.name}", {"browser": path.parent}
        raise ProbeError("trusted browser probe executable is unavailable")

    @staticmethod
    def _write_config(path: Path, value: dict[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def run(
        self,
        *,
        sandbox: CandidateSandbox,
        trusted_bundle: TrustedProbeBundle,
        identity: CandidateIdentity,
        auth_token: str,
        session_id: int,
    ) -> TrustedProbeResult:
        identity.validate()
        probe = trusted_bundle.root / "probe.py"
        browser_driver = trusted_bundle.root / "browser.py"
        scenarios = trusted_bundle.root / "browser-scenarios.json"
        if (
            not probe.is_file()
            or probe.is_symlink()
            or not browser_driver.is_file()
            or browser_driver.is_symlink()
            or not scenarios.is_file()
            or scenarios.is_symlink()
        ):
            raise ProbeError("trusted probe bundle is incomplete")
        browser, inputs = self._browser()
        inputs["trusted-probes"] = trusted_bundle.root
        config_path = sandbox.runner_home / "trusted-probe-config.json"
        self._write_config(
            config_path,
            {
                "auth_token": auth_token,
                "base_url": f"http://127.0.0.1:{sandbox.port}",
                "browser_executable": browser,
                "browser_profile": str(sandbox.runner_home / "browser-profile"),
                "browser_scenarios": "/opt/proxima-inputs/trusted-probes/browser-scenarios.json",
                "identity": {
                    "asset_manifest_digest": identity.asset_digest,
                    "commit": identity.commit,
                    "release_id": identity.release_id,
                    "version": identity.version,
                },
                "port": sandbox.port,
                "server_argv": [
                    str(sandbox.release / "apps" / "api" / ".venv" / "bin" / "python"),
                    "-m",
                    "uvicorn",
                    "proxima_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(sandbox.port),
                ],
                "server_cwd": str(sandbox.release / "apps" / "api"),
                "session_id": session_id,
                "web_dist": str(sandbox.release / "apps" / "web" / "dist"),
            },
        )
        try:
            completed = sandbox.run(
                (
                    "/usr/bin/python3",
                    "/opt/proxima-inputs/trusted-probes/probe.py",
                    str(config_path),
                ),
                cwd=sandbox.runner_home,
                writable_paths=(
                    sandbox.database.parent,
                    sandbox.workspace,
                    sandbox.runner_home,
                ),
                read_only_paths=(sandbox.release,),
                inputs=inputs,
                environment={
                    "PROXIMA_CANDIDATE_RELEASE_ID": identity.release_id,
                    "PROXIMA_CANDIDATE_COMMIT": identity.commit,
                    "PROXIMA_CANDIDATE_ASSET_MANIFEST_DIGEST": identity.asset_digest,
                    "PROXIMA_SINGLE_USER_NAME": "candidate",
                    "PROXIMA_LINK_ROOTS": str(sandbox.workspace),
                    "PROXIMA_CLAUDE_LIVE_HOME": "0",
                    "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "1",
                    "PROXIMA_FEATURE_WORKFLOW_GRAPH": "1",
                },
                network_loopback=True,
                memory_bytes=TRUSTED_BROWSER_ADDRESS_SPACE_BYTES,
                process_limit=TRUSTED_BROWSER_PROCESS_LIMIT,
                timeout=180,
            )
        except SandboxError as exc:
            raise ProbeError(str(exc)) from exc
        if completed.returncode:
            raise ProbeError("trusted candidate probe suite failed")
        raw = completed.stdout or b""
        try:
            report = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProbeError("trusted candidate probe report is invalid") from exc
        if (
            not isinstance(report, dict)
            or report.get("ok") is not True
            or not isinstance(report.get("results"), dict)
        ):
            raise ProbeError("trusted candidate probe report is invalid")
        return TrustedProbeResult(raw, dict(report["results"]))

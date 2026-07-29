"""Candidate-independent API, SSE, identity and asset probes."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .layout import COMMIT, RELEASE_ID


class ProbeError(RuntimeError):
    pass


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def asset_manifest_digest(web_dist: Path) -> str:
    if not web_dist.is_dir() or web_dist.is_symlink():
        raise ProbeError("candidate static asset directory is unavailable")
    files: list[tuple[str, str]] = []
    for path in sorted(web_dist.rglob("*")):
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise ProbeError("candidate static assets contain a symlink")
            continue
        files.append((path.relative_to(web_dist).as_posix(), _sha(path.read_bytes())))
    if not files:
        raise ProbeError("candidate static assets are empty")
    return _sha(json.dumps(files, separators=(",", ":")).encode())


@dataclass(frozen=True)
class CandidateIdentity:
    release_id: str
    commit: str
    asset_digest: str

    def validate(self) -> None:
        if not RELEASE_ID.fullmatch(self.release_id) or not COMMIT.fullmatch(self.commit):
            raise ProbeError("candidate identity is invalid")
        if self.release_id.split("-")[1] != self.commit:
            raise ProbeError("candidate release and commit identity mismatch")
        if len(self.asset_digest) != 64 or set(self.asset_digest) - set("0123456789abcdef"):
            raise ProbeError("candidate asset digest is invalid")


class TrustedProbeRunner:
    """Uses fixed controller expectations, never test files from the release."""

    def __init__(self, *, timeout_seconds: float = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def _get_json(self, url: str, token: str | None = None) -> dict:
        request = Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise ProbeError(f"candidate probe failed: {url}")
                value = json.loads(response.read())
        except (OSError, URLError, ValueError) as exc:
            raise ProbeError(f"candidate probe unavailable: {url}") from exc
        if not isinstance(value, dict):
            raise ProbeError("candidate probe response is invalid")
        return value

    def readiness(self, base_url: str, expected: CandidateIdentity) -> dict[str, str]:
        expected.validate()
        payload = self._get_json(f"{base_url}/api/health")
        observed = CandidateIdentity(
            str(payload.get("release_id", "")), str(payload.get("commit", "")),
            str(payload.get("asset_manifest_digest", "")),
        )
        if observed != expected:
            raise ProbeError("candidate API identity mismatch")
        return {"readiness": _sha(json.dumps(payload, sort_keys=True).encode())}

    def authenticated_health(self, base_url: str, token: str) -> dict[str, str]:
        payload = self._get_json(f"{base_url}/api/maintenance", token)
        if payload.get("active") is not False:
            raise ProbeError("candidate maintenance probe is unexpectedly fenced")
        return {"authenticated_health": _sha(json.dumps(payload, sort_keys=True).encode())}

    def wait_ready(self, base_url: str, expected: CandidateIdentity) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.readiness(base_url, expected)
            except ProbeError as exc:
                error = exc
                time.sleep(0.1)
        raise ProbeError("candidate readiness timed out") from error

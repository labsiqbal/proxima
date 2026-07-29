"""Strict release manifest and local-candidate provenance validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .layout import COMMIT, RELEASE_ID, LayoutError

HEX = re.compile(r"^[a-f0-9]{64}$")


class ManifestError(ValueError):
    pass


def canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    commit: str
    tree_digest: str
    lock_digests: dict[str, str]
    files: dict[str, str]
    signature: dict[str, str]

    @classmethod
    def parse(cls, raw: bytes) -> "ReleaseManifest":
        try:
            value = json.loads(raw)
            manifest = cls(
                release_id=str(value["release_id"]), commit=str(value["commit"]),
                tree_digest=str(value["tree_digest"]), lock_digests=dict(value["lock_digests"]),
                files=dict(value["files"]), signature=dict(value["signature"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ManifestError("malformed release manifest") from exc
        try:
            if not RELEASE_ID.fullmatch(manifest.release_id) or not COMMIT.fullmatch(manifest.commit):
                raise ManifestError("invalid release identity")
        except LayoutError as exc:
            raise ManifestError(str(exc)) from exc
        for collection in (manifest.lock_digests, manifest.files):
            if not collection or any(not isinstance(key, str) or not HEX.fullmatch(str(value)) for key, value in collection.items()):
                raise ManifestError("manifest digest invalid")
        if not HEX.fullmatch(manifest.tree_digest):
            raise ManifestError("manifest tree digest invalid")
        if any(not path or path.startswith("/") or ".." in Path(path).parts for path in manifest.files):
            raise ManifestError("manifest path traversal")
        if set(manifest.signature) != {"key_id", "algorithm", "value"}:
            raise ManifestError("manifest signature envelope invalid")
        return manifest

    def signed_payload(self) -> bytes:
        return canonical_json({
            "release_id": self.release_id, "commit": self.commit, "tree_digest": self.tree_digest,
            "lock_digests": self.lock_digests, "files": self.files,
        })

    def verify(self, verify_signature: Callable[[str, str, bytes, str], bool]) -> None:
        if self.signature["algorithm"] not in {"ed25519", "rsa-pss-sha256"}:
            raise ManifestError("unsupported manifest signature algorithm")
        if not verify_signature(self.signature["key_id"], self.signature["algorithm"], self.signed_payload(), self.signature["value"]):
            raise ManifestError("manifest signature rejected")

    def verify_tree(self, release_root: Path) -> None:
        root = release_root.resolve()
        digest = hashlib.sha256()
        for relpath, expected in sorted(self.files.items()):
            path = (root / relpath).resolve()
            if root not in path.parents or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ManifestError("manifest file substitution")
            digest.update(relpath.encode() + b"\0" + expected.encode() + b"\n")
        if digest.hexdigest() != self.tree_digest:
            raise ManifestError("manifest tree mix-and-match")


def local_provenance(task_id: str, base_commit: str, candidate_commit: str, origin: str, tree_digest: str, uv_lock: Path, package_lock: Path) -> dict[str, str]:
    if not task_id or not COMMIT.fullmatch(base_commit) or not COMMIT.fullmatch(candidate_commit) or not origin or not HEX.fullmatch(tree_digest):
        raise ManifestError("invalid local candidate provenance")
    return {
        "kind": "local_provenance_not_signed", "task_id": task_id, "base_commit": base_commit,
        "candidate_commit": candidate_commit, "origin": origin, "tree_digest": tree_digest,
        "uv_lock_digest": hashlib.sha256(uv_lock.read_bytes()).hexdigest(),
        "package_lock_digest": hashlib.sha256(package_lock.read_bytes()).hexdigest(),
    }

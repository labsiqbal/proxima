"""Strict release manifest and local-candidate provenance validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .layout import COMMIT, RELEASE_ID
from .tree import TreeError, VerifiedTree, regular_file_digests

HEX = re.compile(r"^[a-f0-9]{64}$")
REQUIRED_LOCK_PATHS = frozenset({"apps/api/uv.lock", "apps/web/package-lock.json"})


class ManifestError(ValueError):
    pass


def canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate manifest key")
        result[key] = value
    return result


def _safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and "\\" not in value
        and not path.is_absolute()
        and "." not in path.parts
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _digest_tree(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relpath, value in sorted(files.items()):
        digest.update(relpath.encode() + b"\0" + value.encode() + b"\n")
    return digest.hexdigest()


def _validated_digest_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ManifestError("manifest digest map invalid")
    result: dict[str, str] = {}
    for path, digest in value.items():
        if (
            not isinstance(path, str)
            or not _safe_path(path)
            or not isinstance(digest, str)
            or not HEX.fullmatch(digest)
        ):
            raise ManifestError("manifest digest invalid")
        result[path] = digest
    return result


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
            value = json.loads(raw, object_pairs_hook=_object)
            if not isinstance(value, dict) or set(value) != {
                "release_id",
                "commit",
                "tree_digest",
                "lock_digests",
                "files",
                "signature",
            }:
                raise ManifestError("manifest fields invalid")
            lock_digests = _validated_digest_map(value["lock_digests"])
            files = _validated_digest_map(value["files"])
            signature = value["signature"]
            if not isinstance(signature, dict):
                raise ManifestError("manifest signature envelope invalid")
            manifest = cls(
                release_id=str(value["release_id"]), commit=str(value["commit"]),
                tree_digest=str(value["tree_digest"]), lock_digests=lock_digests,
                files=files, signature=dict(signature),
            )
        except ManifestError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ManifestError("malformed release manifest") from exc
        if not RELEASE_ID.fullmatch(manifest.release_id) or not COMMIT.fullmatch(manifest.commit):
            raise ManifestError("invalid release identity")
        if not HEX.fullmatch(manifest.tree_digest):
            raise ManifestError("manifest tree digest invalid")
        if set(manifest.lock_digests) != REQUIRED_LOCK_PATHS:
            raise ManifestError("manifest lock set invalid")
        if any(manifest.files.get(path) != digest for path, digest in manifest.lock_digests.items()):
            raise ManifestError("manifest lock digest mismatch")
        if (
            set(manifest.signature) != {"key_id", "algorithm", "value"}
            or any(not isinstance(value, str) or not value for value in manifest.signature.values())
        ):
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

    def _verified_files(self, release_root: Path) -> dict[str, str]:
        try:
            actual = regular_file_digests(release_root)
        except TreeError as exc:
            raise ManifestError(str(exc)) from exc
        if actual != self.files:
            raise ManifestError("manifest file set substitution")
        if any(actual.get(path) != expected for path, expected in self.lock_digests.items()):
            raise ManifestError("manifest lock substitution")
        if _digest_tree(actual) != self.tree_digest:
            raise ManifestError("manifest tree mix-and-match")
        return actual

    def verify_tree(self, release_root: Path) -> None:
        self._verified_files(release_root)

    def authenticate_tree(
        self,
        release_root: Path,
        verify_signature: Callable[[str, str, bytes, str], bool],
    ) -> VerifiedTree:
        self.verify(verify_signature)
        actual = self._verified_files(release_root)
        return VerifiedTree(
            release_id=self.release_id,
            commit=self.commit,
            file_digests=tuple(sorted(actual.items())),
        )


def _validate_local_metadata(
    task_id: str,
    base_commit: str,
    candidate_commit: str,
    origin: str,
) -> None:
    if (
        not task_id
        or len(task_id) > 128
        or any(ord(char) < 32 for char in task_id)
        or not COMMIT.fullmatch(base_commit)
        or not COMMIT.fullmatch(candidate_commit)
        or not origin
        or len(origin) > 256
        or any(ord(char) < 32 for char in origin)
    ):
        raise ManifestError("invalid local candidate provenance")


def local_provenance(
    task_id: str,
    base_commit: str,
    candidate_commit: str,
    origin: str,
    candidate_root: Path,
) -> dict[str, Any]:
    _validate_local_metadata(task_id, base_commit, candidate_commit, origin)
    try:
        files = regular_file_digests(candidate_root)
    except TreeError as exc:
        raise ManifestError(str(exc)) from exc
    if not REQUIRED_LOCK_PATHS.issubset(files):
        raise ManifestError("local candidate lock set missing")
    return {
        "kind": "local_provenance_not_signed", "task_id": task_id, "base_commit": base_commit,
        "candidate_commit": candidate_commit, "origin": origin,
        "tree_digest": _digest_tree(files),
        "lock_digests": {path: files[path] for path in sorted(REQUIRED_LOCK_PATHS)},
    }


def verify_local_provenance(
    value: Mapping[str, Any],
    candidate_root: Path,
) -> VerifiedTree:
    if set(value) != {
        "kind",
        "task_id",
        "base_commit",
        "candidate_commit",
        "origin",
        "tree_digest",
        "lock_digests",
    } or value.get("kind") != "local_provenance_not_signed":
        raise ManifestError("local provenance fields invalid")
    task_id = value.get("task_id")
    base_commit = value.get("base_commit")
    candidate_commit = value.get("candidate_commit")
    origin = value.get("origin")
    if not all(isinstance(item, str) for item in (task_id, base_commit, candidate_commit, origin)):
        raise ManifestError("invalid local candidate provenance")
    _validate_local_metadata(task_id, base_commit, candidate_commit, origin)
    tree_digest = value.get("tree_digest")
    if not isinstance(tree_digest, str) or not HEX.fullmatch(tree_digest):
        raise ManifestError("invalid local tree digest")
    lock_digests = _validated_digest_map(value.get("lock_digests"))
    if set(lock_digests) != REQUIRED_LOCK_PATHS:
        raise ManifestError("local candidate lock set invalid")
    try:
        files = regular_file_digests(candidate_root)
    except TreeError as exc:
        raise ManifestError(str(exc)) from exc
    if _digest_tree(files) != tree_digest:
        raise ManifestError("local candidate tree substitution")
    if any(files.get(path) != digest for path, digest in lock_digests.items()):
        raise ManifestError("local candidate lock substitution")
    return VerifiedTree(
        release_id=None,
        commit=candidate_commit,
        file_digests=tuple(sorted(files.items())),
    )

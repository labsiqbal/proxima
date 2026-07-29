from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from apps.safe_updater.adapters.launchd import LaunchdAdapter
from apps.safe_updater.adapters.systemd import SystemdAdapter
from apps.safe_updater.adapters.unmanaged import UnmanagedAdapter
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.journal import Journal, JournalIntegrityError
from apps.safe_updater.layout import LayoutError, ReleaseLayout
from apps.safe_updater.manifest import ManifestError, ReleaseManifest
from apps.safe_updater.recovery import inspect
from apps.safe_updater.state_machine import Phase
from apps.safe_updater.write_fence import status, write


def test_journal_replay_is_deterministic_at_every_foundation_phase(tmp_path: Path):
    intent = hashlib.sha256(b"intent").hexdigest()
    for phase in Phase:
        journal = Journal.create(tmp_path / phase.value, "a" * 32, intent)
        for item in list(Phase)[: list(Phase).index(phase) + 1]:
            journal.append(item)
        before = inspect(journal)
        after = inspect(Journal(journal.path, intent))
        assert before == after
        assert after.safe


def test_journal_rejects_tamper_and_non_monotonic_records(tmp_path: Path):
    journal = Journal.create(tmp_path, "a" * 32, "b" * 64)
    journal.append(Phase.PREFLIGHT)
    journal.path.write_text('{"sequence":2}\n', encoding="utf-8")
    with pytest.raises(JournalIntegrityError):
        journal.records()


def test_journal_rejects_path_substitution_before_creating_a_file(tmp_path: Path):
    with pytest.raises(JournalIntegrityError):
        Journal.create(tmp_path, "../" + "a" * 32, "b" * 64)
    assert not (tmp_path / "journal").exists()


def test_single_flight_returns_existing_owner(tmp_path: Path):
    first = SafeUpdateController(tmp_path)
    held = first.lock.acquire("a" * 32)
    assert held.acquired
    second = SafeUpdateController(tmp_path)
    result = second.submit({"candidate_commit": "b" * 40})
    assert result.accepted is False
    assert result.run_id == "a" * 32
    first.lock.release()


@pytest.mark.parametrize("value", ["../x", "a/" + "b" * 30, "A" * 32, "a" * 31])
def test_layout_rejects_hostile_identifiers(tmp_path: Path, value: str):
    with pytest.raises(LayoutError):
        ReleaseLayout(tmp_path).run_dir(value)


def test_fence_is_external_durable_contract(tmp_path: Path):
    fence = tmp_path / "outside-release" / "fence.json"
    write(fence, "a" * 32, "write_fenced")
    assert status(fence) == {"run_id": "a" * 32, "phase": "write_fenced"}


def test_manifest_rejects_path_traversal_and_mix_and_match(tmp_path: Path):
    content = b"trusted release"
    digest = hashlib.sha256(content).hexdigest()
    payload = {
        "release_id": f"sha256-{'a' * 40}-{'b' * 12}", "commit": "a" * 40,
        "lock_digests": {"uv.lock": "c" * 64}, "files": {"app/file.txt": digest},
        "tree_digest": hashlib.sha256(f"app/file.txt\0{digest}\n".encode()).hexdigest(),
        "signature": {"key_id": "release-key", "algorithm": "ed25519", "value": "fixture"},
    }
    manifest = ReleaseManifest.parse(json.dumps(payload).encode())
    root = tmp_path / "release"
    (root / "app").mkdir(parents=True)
    (root / "app/file.txt").write_bytes(content)
    manifest.verify(lambda key, algorithm, signed, signature: key == "release-key" and algorithm == "ed25519")
    manifest.verify_tree(root)
    payload["files"] = {"../outside": digest}
    with pytest.raises(ManifestError):
        ReleaseManifest.parse(json.dumps(payload).encode())


@pytest.mark.parametrize("adapter", [UnmanagedAdapter(), SystemdAdapter("proxima.service"), LaunchdAdapter("com.proxima.service")])
def test_adapters_fail_closed_before_activation(adapter):
    assert adapter.capability().managed is False
    with pytest.raises(RuntimeError):
        adapter.stop_and_verify()

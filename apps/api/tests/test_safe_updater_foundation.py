from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

import apps.safe_updater.journal as journal_module
import apps.safe_updater.locks as lock_module
from apps.safe_updater.adapters.launchd import LaunchdAdapter
from apps.safe_updater.adapters.systemd import SystemdAdapter
from apps.safe_updater.adapters.unmanaged import UnmanagedAdapter
from apps.safe_updater.cli import main as cli_main
from apps.safe_updater.controller import SafeUpdateController
from apps.safe_updater.journal import Journal, JournalIntegrityError
from apps.safe_updater.layout import LayoutError, ReleaseLayout
from apps.safe_updater.manifest import (
    ManifestError,
    ReleaseManifest,
    local_provenance,
    verify_local_provenance,
)
from apps.safe_updater.privileges import (
    CandidateIdentity,
    PrivilegeBoundaryError,
    assert_candidate_cannot_write,
)
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-kill contract")
def test_killed_partial_append_fails_closed_on_replay(tmp_path: Path):
    journal = Journal.create(tmp_path / "accepted", "a" * 32, "b" * 64)
    journal.append(Phase.PREFLIGHT)
    source = Journal.create(tmp_path / "source", "c" * 32, "b" * 64)
    source.append(Phase.PREFLIGHT)
    source.append(Phase.CANDIDATE_STAGED)
    partial = source.path.read_bytes().splitlines(keepends=True)[1][:24]
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os,signal,sys;"
                "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_APPEND);"
                "os.write(fd,bytes.fromhex(sys.argv[2]));"
                "print('written',flush=True);"
                "signal.pause()"
            ),
            str(journal.path),
            partial.hex(),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "written"
    process.send_signal(signal.SIGKILL)
    assert process.wait(timeout=5) == -signal.SIGKILL

    recovered = inspect(Journal(journal.path, "b" * 64))
    assert recovered.safe is False
    assert recovered.action == "do_not_start_any_release"


def test_journal_rejects_path_substitution_before_creating_a_file(tmp_path: Path):
    with pytest.raises(JournalIntegrityError):
        Journal.create(tmp_path, "../" + "a" * 32, "b" * 64)
    assert not (tmp_path / "journal").exists()


def test_journal_creation_fsyncs_each_new_directory_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    synced: list[Path] = []
    monkeypatch.setattr(journal_module, "_fsync_directory", synced.append)
    root = tmp_path / "new-root"
    Journal.create(root, "a" * 32, "b" * 64)
    assert tmp_path in synced
    assert root in synced
    assert root / "journal" in synced


def test_single_flight_returns_existing_owner(tmp_path: Path):
    first = SafeUpdateController(tmp_path)
    held = first.lock.acquire("a" * 32)
    assert held.acquired
    second = SafeUpdateController(tmp_path)
    result = second.submit({"candidate_commit": "b" * 40})
    assert result.accepted is False
    assert result.run_id == "a" * 32
    first.lock.release()


def test_single_flight_selects_windows_backend_without_posix_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    operations: list[int] = []
    backend = SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda _fd, operation, _size: operations.append(operation),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", backend)
    monkeypatch.setattr(lock_module.os, "name", "nt")
    lock = lock_module.SingleFlightLock(tmp_path / "controller.lock")
    assert lock.acquire("a" * 32).acquired
    lock.release()
    assert operations == [backend.LK_NBLCK, backend.LK_UNLCK]


def test_completed_kernel_lock_does_not_release_durable_active_run(tmp_path: Path):
    first = SafeUpdateController(tmp_path)
    accepted = first.submit({"candidate_commit": "a" * 40})
    assert accepted.accepted is True
    second = SafeUpdateController(tmp_path).submit({"candidate_commit": "b" * 40})
    assert second.accepted is False
    assert second.run_id == accepted.run_id


def test_recovery_status_rejects_hostile_and_missing_run_ids(tmp_path: Path):
    controller = SafeUpdateController(tmp_path)
    for run_id in ("../" + "a" * 32, "a" * 31):
        value = controller.recovery_status(run_id, {"candidate_commit": "b" * 40})
        assert value.safe is False
        assert value.action == "do_not_start_any_release"
    missing = controller.recovery_status("a" * 32, {"candidate_commit": "b" * 40})
    assert missing.safe is False
    assert missing.reason == "accepted-run journal is missing"


@pytest.mark.parametrize("value", ["../x", "a/" + "b" * 30, "A" * 32, "a" * 31])
def test_layout_rejects_hostile_identifiers(tmp_path: Path, value: str):
    with pytest.raises(LayoutError):
        ReleaseLayout(tmp_path).run_dir(value)


def test_layout_rejects_hostile_tree_before_publish(tmp_path: Path):
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "safe.txt").write_text("safe", encoding="utf-8")
    (source / "escape").symlink_to(tmp_path / "outside")
    release_id = f"sha256-{'a' * 40}-{'b' * 12}"
    layout = ReleaseLayout(tmp_path / "trusted")
    with pytest.raises(LayoutError, match="symlink"):
        layout.create_immutable_release(release_id, source)
    assert source.exists()
    assert not layout.release_dir(release_id).exists()


def test_fence_is_external_durable_contract(tmp_path: Path):
    fence = tmp_path / "outside-release" / "fence.json"
    write(fence, "a" * 32, "write_fenced")
    assert status(fence) == {"run_id": "a" * 32, "phase": "write_fenced"}


def test_manifest_rejects_path_traversal_and_mix_and_match(tmp_path: Path):
    files = {
        "app/file.txt": b"trusted release",
        "apps/api/uv.lock": b"uv",
        "apps/web/package-lock.json": b"npm",
    }
    digests = {path: hashlib.sha256(content).hexdigest() for path, content in files.items()}
    payload = {
        "release_id": f"sha256-{'a' * 40}-{'b' * 12}", "commit": "a" * 40,
        "lock_digests": {
            path: digests[path]
            for path in ("apps/api/uv.lock", "apps/web/package-lock.json")
        },
        "files": digests,
        "tree_digest": hashlib.sha256(
            b"".join(
                f"{path}\0{digest}\n".encode()
                for path, digest in sorted(digests.items())
            )
        ).hexdigest(),
        "signature": {"key_id": "release-key", "algorithm": "ed25519", "value": "fixture"},
    }
    manifest = ReleaseManifest.parse(json.dumps(payload).encode())
    root = tmp_path / "release"
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest.verify(lambda key, algorithm, signed, signature: key == "release-key" and algorithm == "ed25519")
    manifest.verify_tree(root)
    (root / "extra").write_text("not signed", encoding="utf-8")
    with pytest.raises(ManifestError, match="file set"):
        manifest.verify_tree(root)
    (root / "extra").unlink()
    (root / "apps/api/uv.lock").write_text("changed", encoding="utf-8")
    with pytest.raises(ManifestError):
        manifest.verify_tree(root)
    payload["files"] = {"../outside": "a" * 64}
    with pytest.raises(ManifestError):
        ReleaseManifest.parse(json.dumps(payload).encode())


def test_local_provenance_is_verified_against_candidate_tree(tmp_path: Path):
    root = tmp_path / "candidate"
    for path, content in {
        "app.py": b"print('ok')",
        "apps/api/uv.lock": b"uv",
        "apps/web/package-lock.json": b"npm",
    }.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    provenance = local_provenance(
        "task-1",
        "a" * 40,
        "b" * 40,
        "local-worktree",
        root,
    )
    assert verify_local_provenance(provenance, root) == provenance
    (root / "apps/api/uv.lock").write_text("substituted", encoding="utf-8")
    with pytest.raises(ManifestError, match="tree substitution"):
        verify_local_provenance(provenance, root)


def test_recovery_cli_prints_stable_fail_closed_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    intent = tmp_path / "intent.json"
    intent.write_text('{"candidate_commit":"' + "b" * 40 + '"}', encoding="utf-8")
    result = cli_main(
        [
            "recovery-status",
            "--root",
            str(tmp_path / "trusted"),
            "--run-id",
            "a" * 32,
            "--intent-file",
            str(intent),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 2
    assert output == {
        "action": "do_not_start_any_release",
        "journal_hash": None,
        "reason": "accepted-run journal is missing",
        "run_id": "a" * 32,
        "safe": False,
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX service identity contract")
def test_candidate_identity_cannot_own_or_write_trusted_state(tmp_path: Path):
    paths = [
        tmp_path / "journal",
        tmp_path / "pointers",
        tmp_path / "fence",
        tmp_path / "backups",
        tmp_path / "service.conf",
    ]
    for path in paths[:4]:
        path.mkdir(mode=0o700)
    paths[4].write_text("inert", encoding="utf-8")
    paths[4].chmod(0o600)
    trusted_owner = paths[0].stat().st_uid
    trusted_group = paths[0].stat().st_gid
    candidate = CandidateIdentity(trusted_owner + 1, frozenset())
    assert_candidate_cannot_write(candidate, paths)
    with pytest.raises(PrivilegeBoundaryError, match="owns trusted state"):
        assert_candidate_cannot_write(
            CandidateIdentity(trusted_owner, frozenset({trusted_group})),
            paths,
        )


def test_legacy_update_cli_is_inert(tmp_path: Path):
    script = Path(__file__).resolve().parents[3] / "scripts/proxima"
    result = subprocess.run(
        [str(script), "update"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "activation is unavailable" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("adapter", [UnmanagedAdapter(), SystemdAdapter("proxima.service"), LaunchdAdapter("com.proxima.service")])
def test_adapters_fail_closed_before_activation(adapter):
    assert adapter.capability().managed is False
    with pytest.raises(RuntimeError):
        adapter.stop_and_verify()

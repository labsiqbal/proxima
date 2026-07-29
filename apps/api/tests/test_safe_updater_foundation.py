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

import apps.safe_updater.durability as durability_module
import apps.safe_updater.layout as layout_module
import apps.safe_updater.locks as lock_module
import apps.safe_updater.privileges as privilege_module
import apps.safe_updater.tree as tree_module
from proxima_api.maintenance_status import read_external_fence
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
from apps.safe_updater.tree import VerifiedTree, regular_file_digests
from apps.safe_updater.write_fence import write


def _verified_tree(
    source: Path,
    *,
    commit: str = "a" * 40,
    release_id: str | None = None,
) -> VerifiedTree:
    return VerifiedTree(
        release_id=release_id,
        commit=commit,
        file_digests=tuple(sorted(regular_file_digests(source).items())),
    )


def _candidate_identity(
    uid: int,
    gid: int,
    *,
    service_capabilities: frozenset[str] = frozenset(),
    allows_privilege_escalation: bool = False,
) -> CandidateIdentity:
    return CandidateIdentity(
        uid,
        gid,
        service_capabilities=service_capabilities,
        allows_privilege_escalation=allows_privilege_escalation,
    )


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
    monkeypatch.setattr(durability_module, "fsync_directory", synced.append)
    root = tmp_path / "new-root"
    Journal.create(root, "a" * 32, "b" * 64)
    assert tmp_path in synced
    assert root in synced
    assert root / "journal" in synced


def test_shared_directory_creation_fsyncs_every_new_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    synced: list[Path] = []
    monkeypatch.setattr(durability_module, "fsync_directory", synced.append)
    destination = tmp_path / "trusted" / "releases"
    durability_module.ensure_durable_directory(destination, 0o700)
    assert tmp_path in synced
    assert tmp_path / "trusted" in synced
    assert destination in synced


def test_platform_durability_selects_windows_directory_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    synced: list[Path] = []
    monkeypatch.setattr(durability_module.os, "name", "nt")
    monkeypatch.setattr(
        durability_module,
        "_flush_windows_directory",
        synced.append,
    )
    durability_module.fsync_directory(tmp_path)
    assert synced == [tmp_path]


def test_journal_append_retries_short_writes(tmp_path: Path, monkeypatch):
    journal = Journal.create(tmp_path, "a" * 32, "b" * 64)
    real_write = os.write
    calls = 0

    def short_write(file_descriptor, value):
        nonlocal calls
        calls += 1
        return real_write(file_descriptor, value[: max(1, len(value) // 2)])

    monkeypatch.setattr(durability_module.os, "write", short_write)
    journal.append(Phase.PREFLIGHT)
    assert calls > 1
    assert journal.records()[0].phase is Phase.PREFLIGHT


def test_journal_rejects_unterminated_final_record(tmp_path: Path):
    journal = Journal.create(tmp_path, "a" * 32, "b" * 64)
    journal.append(Phase.PREFLIGHT)
    journal.path.write_bytes(journal.path.read_bytes().removesuffix(b"\n"))
    with pytest.raises(JournalIntegrityError, match="unterminated"):
        journal.records()


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
    verified = _verified_tree(source)
    (source / "escape").symlink_to(tmp_path / "outside")
    release_id = f"sha256-{'a' * 40}-{'b' * 12}"
    layout = ReleaseLayout(tmp_path / "trusted")
    with pytest.raises(LayoutError, match="symlink"):
        layout.create_immutable_release(release_id, source, verified)
    assert source.exists()
    assert not layout.release_dir(release_id).exists()
    assert list((tmp_path / "trusted" / "releases").glob(".incoming-*")) == []


def test_layout_publishes_fresh_controller_owned_inodes(tmp_path: Path):
    outside = tmp_path / "candidate-owned"
    outside.write_bytes(b"verified bytes")
    source = tmp_path / "candidate"
    source.mkdir()
    os.link(outside, source / "app.py")
    release_id = f"sha256-{'a' * 40}-{'b' * 12}"
    verified = _verified_tree(source)
    destination = ReleaseLayout(tmp_path / "trusted").create_immutable_release(
        release_id,
        source,
        verified,
    )

    published = destination / "app.py"
    assert source.exists()
    assert published.read_bytes() == b"verified bytes"
    assert published.stat().st_ino != outside.stat().st_ino
    assert published.stat().st_nlink == 1
    assert published.stat().st_uid == getattr(
        os,
        "geteuid",
        lambda: published.stat().st_uid,
    )()
    outside.write_bytes(b"candidate mutation")
    assert published.read_bytes() == b"verified bytes"


def test_layout_rolls_back_failed_publication_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "app.py").write_bytes(b"verified bytes")
    release_id = f"sha256-{'a' * 40}-{'b' * 12}"
    verified = _verified_tree(source)
    layout = ReleaseLayout(tmp_path / "trusted")
    real_fsync_directory = layout_module.fsync_directory
    calls = 0

    def fail_publication_flush(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected durability failure")
        real_fsync_directory(path)

    monkeypatch.setattr(layout_module, "fsync_directory", fail_publication_flush)
    with pytest.raises(LayoutError, match="publication durability failed"):
        layout.create_immutable_release(release_id, source, verified)
    assert not layout.release_dir(release_id).exists()
    assert list((tmp_path / "trusted" / "releases").glob(".incoming-*")) == []
    assert layout.create_immutable_release(release_id, source, verified).exists()


def test_layout_rejects_substituted_ancestor_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "candidate"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "app.py").write_bytes(b"verified bytes")
    replacement = tmp_path / "host"
    replacement.mkdir()
    (replacement / "app.py").write_bytes(b"host-only bytes")
    verified = _verified_tree(source)
    real_open = os.open
    supports_dir_fd = set(os.supports_dir_fd)
    swapped = False

    def substitute_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "nested" and dir_fd is not None and not swapped:
            swapped = True
            nested.rename(source / "original")
            nested.symlink_to(replacement, target_is_directory=True)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(tree_module.os, "open", substitute_before_open)
    monkeypatch.setattr(
        tree_module.os,
        "supports_dir_fd",
        supports_dir_fd | {substitute_before_open},
    )
    release_id = f"sha256-{'a' * 40}-{'b' * 12}"
    layout = ReleaseLayout(tmp_path / "trusted")
    with pytest.raises(LayoutError, match="substituted"):
        layout.create_immutable_release(release_id, source, verified)
    assert not layout.release_dir(release_id).exists()


def test_fence_is_external_durable_contract(tmp_path: Path):
    fence = tmp_path / "outside-release" / "fence.json"
    write(fence, "a" * 32, "write_fenced")
    assert read_external_fence(fence) == {
        "run_id": "a" * 32,
        "phase": "write_fenced",
    }
    if os.name == "posix":
        assert fence.parent.stat().st_mode & 0o777 == 0o755
        assert fence.stat().st_mode & 0o777 == 0o644


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
    verify_signature = (
        lambda key, algorithm, signed, signature: key == "release-key"
        and algorithm == "ed25519"
    )
    verified = manifest.authenticate_tree(root, verify_signature)
    assert verified.release_id == manifest.release_id
    (root / "app/file.txt").write_bytes(b"changed after authentication")
    layout = ReleaseLayout(tmp_path / "trusted")
    with pytest.raises(LayoutError, match="changed after verification"):
        layout.create_immutable_release(manifest.release_id, root, verified)
    assert not layout.release_dir(manifest.release_id).exists()
    (root / "app/file.txt").write_bytes(files["app/file.txt"])
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
    verified = verify_local_provenance(provenance, root)
    assert verified.commit == "b" * 40
    assert verified.files() == regular_file_digests(root)
    (root / "app.py").write_text("changed", encoding="utf-8")
    release_id = f"sha256-{'b' * 40}-{'c' * 12}"
    layout = ReleaseLayout(tmp_path / "trusted")
    with pytest.raises(LayoutError, match="changed after verification"):
        layout.create_immutable_release(release_id, root, verified)
    assert not layout.release_dir(release_id).exists()
    (root / "app.py").write_text("print('ok')", encoding="utf-8")
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


def test_recovery_cli_fails_closed_when_controller_lock_is_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    root = tmp_path / "trusted"
    root.mkdir()
    (root / "controller.lock").mkdir()
    intent = tmp_path / "intent.json"
    intent.write_text('{"candidate_commit":"' + "b" * 40 + '"}', encoding="utf-8")

    result = cli_main(
        [
            "recovery-status",
            "--root",
            str(root),
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
        "reason": "safe_update_lock_unavailable",
        "run_id": "a" * 32,
        "safe": False,
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX service identity contract")
def test_candidate_identity_cannot_own_or_write_trusted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
    candidate = _candidate_identity(trusted_owner + 1, trusted_group + 1)
    monkeypatch.setattr(
        privilege_module,
        "_effective_access",
        lambda _identity, requests: tuple(False for _ in requests),
    )
    assert_candidate_cannot_write(candidate, paths)
    with pytest.raises(PrivilegeBoundaryError, match="unprivileged"):
        assert_candidate_cannot_write(
            _candidate_identity(0, 0),
            paths,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX service identity contract")
def test_privilege_boundary_rejects_effective_parent_or_acl_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir(mode=0o700)
    journal = trusted_parent / "journal"
    journal.mkdir(mode=0o700)
    identity = _candidate_identity(os.geteuid() + 1, os.getegid() + 1)

    def parent_write(_identity, requests):
        return tuple(request.path == trusted_parent for request in requests)

    monkeypatch.setattr(privilege_module, "_effective_access", parent_write)
    with pytest.raises(PrivilegeBoundaryError, match="replace trusted ancestry"):
        assert_candidate_cannot_write(identity, [journal])

    def leaf_acl_write(_identity, requests):
        return tuple(request.path == journal for request in requests)

    monkeypatch.setattr(privilege_module, "_effective_access", leaf_acl_write)
    with pytest.raises(PrivilegeBoundaryError, match="write trusted state"):
        assert_candidate_cannot_write(identity, [journal])


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() != 0,
    reason="requires root to drop to the candidate identity",
)
def test_privilege_boundary_probes_actual_candidate_identity(tmp_path: Path):
    import pwd

    try:
        candidate = pwd.getpwnam("nobody")
    except KeyError:
        pytest.skip("no unprivileged nobody identity is available")
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    assert_candidate_cannot_write(
        _candidate_identity(candidate.pw_uid, candidate.pw_gid),
        [trusted],
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX service identity contract")
def test_privilege_boundary_rejects_untrusted_owner_and_service_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    trusted_stat = trusted.stat()
    identity = _candidate_identity(trusted_stat.st_uid + 1, trusted_stat.st_gid + 1)
    third_party = SimpleNamespace(
        st_uid=trusted_stat.st_uid + 2,
        st_mode=trusted_stat.st_mode,
    )
    monkeypatch.setattr(
        privilege_module,
        "_ancestry",
        lambda path: ((path, third_party),),
    )
    with pytest.raises(PrivilegeBoundaryError, match="controller-owned"):
        assert_candidate_cannot_write(identity, [trusted])

    candidate_owned = SimpleNamespace(
        st_uid=identity.uid,
        st_mode=trusted_stat.st_mode,
    )
    monkeypatch.setattr(
        privilege_module,
        "_ancestry",
        lambda path: ((path, candidate_owned),),
    )
    with pytest.raises(PrivilegeBoundaryError, match="candidate owns"):
        assert_candidate_cannot_write(identity, [trusted])

    capability_identity = _candidate_identity(
        trusted_stat.st_uid + 1,
        trusted_stat.st_gid + 1,
        service_capabilities=frozenset({"CAP_DAC_OVERRIDE"}),
    )
    with pytest.raises(PrivilegeBoundaryError, match="capability-free"):
        assert_candidate_cannot_write(capability_identity, [trusted])

    escalation_identity = _candidate_identity(
        trusted_stat.st_uid + 1,
        trusted_stat.st_gid + 1,
        allows_privilege_escalation=True,
    )
    with pytest.raises(PrivilegeBoundaryError, match="capability-free"):
        assert_candidate_cannot_write(escalation_identity, [trusted])


def test_recovery_normalizes_journal_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    journal = Journal.create(tmp_path, "a" * 32, "b" * 64)

    def unreadable():
        raise PermissionError("host path must not escape into output")

    monkeypatch.setattr(journal, "records", unreadable)
    result = inspect(journal)
    assert result.safe is False
    assert result.action == "do_not_start_any_release"
    assert result.reason == "accepted-run journal is unreadable"


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


def test_installers_describe_disabled_update_state():
    root = Path(__file__).resolve().parents[3]
    for relative in (
        "scripts/install-user",
        "scripts/install-macos",
        "scripts/install-windows.ps1",
    ):
        content = (root / relative).read_text(encoding="utf-8")
        assert "git pull + rebuild + restart" not in content
        assert "git pull; powershell" not in content
        assert "safe updater activation is disabled" in content


def test_candidate_service_placeholder_grants_no_capabilities():
    root = Path(__file__).resolve().parents[3]
    content = (
        root / "infra/systemd/proxima-candidate@.service"
    ).read_text(encoding="utf-8")
    assert "NoNewPrivileges=yes" in content
    assert "CapabilityBoundingSet=\n" in content
    assert "AmbientCapabilities=\n" in content
    assert "ExecStart=/bin/false" in content


@pytest.mark.parametrize("adapter", [UnmanagedAdapter(), SystemdAdapter("proxima.service"), LaunchdAdapter("com.proxima.service")])
def test_adapters_fail_closed_before_activation(adapter):
    assert adapter.capability().managed is False
    with pytest.raises(RuntimeError):
        adapter.stop_and_verify()
    with pytest.raises(RuntimeError, match="safe_update"):
        adapter.start_readonly_candidate("release")

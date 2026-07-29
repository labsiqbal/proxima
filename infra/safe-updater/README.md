# Safe updater foundation

This directory contains only the root-admin deployment contract for the external
safe updater. It is not installed by the normal user installer. The fail-closed
`install-safe-updater` script enables no updater unit, switches no release, and
changes no live database.

The controller owns its journal, lock, pointers, fence, evidence, and backups
outside candidate releases. The application exposes a narrowly typed,
authenticated request/status projection contract, but cannot write those files.

Enrollment must give the application read-only access to the dedicated, nonsecret
maintenance status path while preserving controller ownership. It must also qualify
pinned candidate-tree traversal, controller-owned ancestry, empty candidate
capability sets, and no privilege escalation before any adapter can report managed.

`install-safe-updater` remains intentionally fail-closed. The repository includes
controller-only candidate qualification with mandatory Bubblewrap isolation,
clone-only migration, immutable release and evidence trees, and hash-pinned trusted
probes, plus a disabled disposable A/B transaction fixture, but the installer does
not enroll either path. Production service-manager enrollment and target-platform
switch, fault, and rollback qualification remain outstanding; no updater unit may
be enabled.

Read-only recovery status is available as stable JSON:

```bash
python -m apps.safe_updater.cli recovery-status \
  --root /path/to/trusted-root \
  --run-id 0123456789abcdef0123456789abcdef \
  --intent-file /path/to/trusted-intent.json
```

No adapter may report managed until the complete matrix in
[`docs/adding-safe-updater-adapter.md`](../../docs/adding-safe-updater-adapter.md)
passes.

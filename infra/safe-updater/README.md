# Safe updater foundation

This directory contains only the root-admin deployment contract for the external
safe updater. It is not installed by the normal user installer, and no script in
this repository enables a service, switches a release, or changes a live database.

The controller owns its journal, lock, pointers, fence, evidence, and backups
outside candidate releases. The application can submit a narrowly typed request
and render an authenticated projection, but cannot write those files.

Enrollment must give the application read-only access to the dedicated, nonsecret
maintenance status path while preserving controller ownership. It must also qualify
pinned candidate-tree traversal, controller-owned ancestry, empty candidate
capability sets, and no privilege escalation before any adapter can report managed.

`install-safe-updater` is intentionally fail-closed until the later candidate
sandbox, trusted probe, and service-manager qualification work lands.

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

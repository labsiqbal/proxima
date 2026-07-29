# Adding a Safe-Updater Service Adapter

This playbook preserves the external authority boundary in
[ADR-0008](adr/0008-external-safe-update-authority.md). The current systemd,
launchd, and unmanaged adapters are contract fixtures. They all report unmanaged
and every activation method raises. `DisposableServiceAdapter` is a test-only
in-memory adapter used by Group 16 transaction fixtures; it is not an enrollment
template and cannot execute manager commands.

## Boundary that cannot move

The external controller, never the application or a candidate release, owns:

- the native single-flight lock and durable active-run journal
- release manifests, trust roots, provenance evidence, and immutable releases
- release pointers, maintenance fence, backups, and recovery decisions
- service configuration and stop/start authority

`self_update_runs` is an authenticated owner projection. It cannot authorize a
transition or replace journal evidence. Candidate output is untrusted data.

## Adapter interface

Implement the narrow `ServiceAdapter` protocol in
`apps/safe_updater/service_adapter.py`:

1. `capability()` reports whether enrollment and qualification are complete.
2. `stop_and_verify()` proves the old writable service is stopped.
3. `start_readonly_candidate(release_id)` starts only the verified immutable
   release under the candidate sandbox.
4. `start_previous_release()` selects only trusted last-good state during recovery.

Do not return `managed=True` until every requirement below is automated and tested.
Do not add side effects to `capability()`. Do not let unit names, labels, release
identifiers, paths, commands, or environment values bypass strict validation.

## Qualification requirements

An adapter needs evidence for all of these:

- The controller identity owns trusted state and service control. The application
  and candidate identities own none of it and have no mode, group, ACL, inherited
  permission, writable ancestry, or service-manager path that can replace or write
  it. Every trusted ancestor is root/controller-owned. Qualification rejects
  candidate service capabilities and privilege escalation, then probes access after
  dropping to the exact candidate UID and groups. Linux probes also require
  no-new-privileges and empty permitted, effective, and ambient capability sets.
- Journal creation and every append survive process kill and power-loss ordering.
  The selected platform backend must durably flush directory entries. Missing,
  partial, unterminated, malformed, reordered, and replayed records fail closed.
- Cross-process lock contention works on the target platform. A nonterminal
  journal continues to block later submissions after the kernel lock is released.
- Signed manifest verification binds the exact regular file set, tree digest,
  `apps/api/uv.lock`, and `apps/web/package-lock.json`, and rejects symlinks, special
  files, traversal, extras, and substitutions. Unsigned local provenance also binds
  normalized file modes and safe in-tree file-symlink targets, then materializes
  their target bytes into regular files. Both paths return the immutable file set
  consumed by publication, so candidate changes cannot redefine expected content.
  Every source component is opened relative to a pinned directory descriptor. Hosts
  without a qualified pinned traversal backend remain unenrolled. Publication
  copies content into fresh controller-owned inodes, rechecks trusted staging, and
  atomically renames it without preserving candidate symlinks, hardlinks, or
  ownership.
- Trusted namespace creation durably flushes each new directory and its parent.
  The maintenance status directory is searchable and the nonsecret fence file is
  readable by the application identity, while neither grants that identity write
  access.
- Candidate execution has read-only source and no access to journals, pointers,
  fences, backups, service configuration, runtime data, or secrets.
- Stop verification detects surviving writable processes and sidecars.
- Read-only probes, writable probes, rollback, disk exhaustion, crash at every
  durable phase, and repeated recovery are deterministic.
- CLI recovery status is stable machine-readable JSON and never treats a missing
  accepted journal as safe.

The platform enrollment must also document the trust-root paths, controller and
candidate identities, unit or label validation, sandbox policy, probes, timeouts,
and operator recovery command.

## Required tests

At minimum add target-platform integration tests for:

- two simultaneous submissions and one sequential submission after a nonterminal run
- controller kill before and after each journal fsync boundary
- short writes and a final record whose newline was never committed
- hostile identifiers, symlink swaps, special files, and exact-tree enforcement
- ancestor-directory replacement between verification and publication
- candidate-owned source modes, hardlinks, and mutation after publication
- mutation between authenticated verification and publication
- signature, lockfile, provenance, and release-identity substitution
- candidate attempts to write or replace each trusted state class through leaf,
  parent, ownership, ACL, inherited, supplementary-group, capability, and
  privileged-identity paths
- plain application entrypoint maintenance reads without repository-root imports
- stale projection rows followed by authoritative external accept/reject responses
- incomplete enrollment, missing service manager, and invalid service configuration
- stop verification with a surviving process and sidecar
- read-only start, health failure, writable start, rollback, and repeated recovery
- stable CLI output for safe, unsafe, missing, and corrupt journals

Keep the adapter unmanaged and activation absent until later delivery groups accept
all target-platform evidence. Adding a template or detecting a service manager is
not enrollment.

## Documentation checklist

Update `docs/CAPABILITIES.md`, `docs/reference/architecture.md`,
`docs/reference/feature-map.md`, `docs/security-boundaries.md`, the relevant
installation guide, and this matrix. If the authority model changes, add a new ADR
that supersedes ADR-0008.

# ADR-0037: Container activity and migration publication

- Status: Superseded by ADR-0038
- Date: 2026-07-31

## Context

Physical Ops migration must remain non-destructive while Project writers can run in
other threads and processes. A process-local lock cannot protect an API crash, a
cached runner, or a detached descendant. Pathname checks also cannot prove that the
inode inspected before a rename is the inode the kernel later moves.

Generated migration documents need the same ownership property across a crash. A
visible temporary name created before its identity is durably recorded is ambiguous
after restart, and deleting a name after checking an opened descriptor can delete a
replacement entry.

## Decision drivers

1. Retry must never expose false quiescence while a Project process can still write.
2. Untrusted Project imports, working directories, and Python environment variables
   must not select guardian code.
3. Authoritative publication must operate from opened, manifest-bound descriptors.
4. Unknown or replaced owner bytes must be retained, never overwritten or deleted.
5. A blocked owner retry must return a bounded, actionable result.
6. Unsupported process-tree or filesystem primitives must fail closed.

## Options considered

1. Keep process-local locks and revalidate after mutation. Rejected because a crash
   drops the lock and post-validation occurs after owner data may have moved.
2. Infer ownership from hidden names, hashes, or extended attributes. Rejected
   because name creation and identity persistence cannot be one durable operation.
3. Use a trusted process guardian plus descriptor publication and retained recovery
   anchors. Chosen.

## Decision

The activity guardian is a standalone standard-library script selected by verified
absolute path and launched with Python isolated from site and import-path influence.
It changes to its trusted package directory before adopting the lease, preserves the
writer's intended working directory and environment separately, and starts the
writer only after lease adoption.

On Linux a detached sentinel owns the shared lease and subreaps the writer tree. The
API-facing launcher may be cancelled or killed without releasing that lease. On
Windows the guardian assigns itself and descendants to a kill-on-close Job object.
On platforms where complete tree exit cannot be proven, Proxima fails closed by
refusing to start the guarded writer.
Exclusive migration acquisition is bounded and reports active processes.
Activity-guarded cached runners are recycled before their API-held lease is
released. On Linux, an explicit owner retry may signal only a project-scoped
guardian whose trusted control record, process start identity, interpreter, and
absolute guardian path still match.

Generated migration documents use anonymous same-filesystem storage only. The
manifest records the opened inode identity and expected hash before the first visible
no-clobber link. The recovery hardlink remains as a manifest-bound anchor after
publication, so cleanup never unlinks a re-resolved name. A filesystem without the
required anonymous publication primitive stops for owner intervention.

Regular legacy files are published from opened descriptors. Directories are
published entry by entry through stable no-follow descriptors, with regular files
hardlinked from their opened descriptors. The original legacy name is then moved
only into a manifest-bound retained namespace. A concurrent replacement can
therefore be retained but cannot become authoritative content. Retained bytes are
never automatically deleted.

Windows opens and identity-binds the Container handle before creating or opening
`ops/` relative to that handle. Every subsequent starter component uses the same
no-reparse handle boundary.

## Consequences

Positive:

- Project-local packages cannot shadow guardian code.
- API exit, launcher cancellation, and cached runner lifetime do not expose a false
  exclusive migration lease.
- Publication cannot move a pathname-swapped inode into an authoritative Ops name.
- Recovery never deletes a late replacement or an ambiguously owned artifact.
- Retry returns instead of waiting forever when a writer remains active.

Negative and accepted trade-offs:

- Activity-guarded cached runners are recycled after a turn.
- Recovery anchors and retained migration sources consume space until an explicit
  future owner-reviewed cleanup design is accepted.
- Unsupported platforms or filesystems stop migration rather than weakening the
  ownership proof.

## Related

- Superseded by [ADR-0038](0038-owner-safe-container-activity-boundaries.md)
- [Container Areas and physical Ops storage](../CAPABILITIES.md#container-areas-and-physical-ops-storage)
- [Architecture](../reference/architecture.md)
- [Security boundaries](../security-boundaries.md)

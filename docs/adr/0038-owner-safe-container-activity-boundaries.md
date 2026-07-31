# ADR-0038: Owner-safe Container activity boundaries

- Status: Accepted
- Date: 2026-07-31
- Supersedes: ADR-0037

## Context

ADR-0037 established durable activity leases and descriptor-based Ops
publication, but its recovery record identified only the guardian. An explicit
retry could therefore terminate a verified guardian without distinguishing live
work from a guardian orphaned by an API crash. Windows also had no identity-bound
way to adopt or terminate an orphaned Job.

Directory publication bound source identities but did not durably bind each
destination directory before filling it. A late empty destination could be
mistaken for a Proxima-created directory. Activity, platform filesystem, and
publication code also accumulated in the registry projection module, which made
ownership policy difficult to review as one coherent boundary.

## Decision drivers

1. Retry must never terminate work whose owning Proxima process is still alive.
2. Orphan recovery must bind both the owner and guardian process identities.
3. Windows recovery must target the exact guardian Job, not a reusable PID.
4. No unbound destination directory may receive migrated content.
5. A changed legacy source name must remain untouched.
6. Shared runner caches must not give one run authority to recycle another.
7. Ownership modules must not depend on registry projection.

## Decision

`container_activity.py` owns mutation locks, activity leases, guardian records,
and orphan recovery. Every guardian record binds the owner PID and process-start
identity in addition to the guardian PID, start identity, interpreter, and
trusted script path. Recovery reports a matching live owner as an active-process
conflict and never signals it. A guardian is recoverable only after the recorded
owner is proven absent.

Windows guardians create an unpredictable named Job and durably record its name.
Owner-authorized retry opens and terminates that exact Job only after the owner
and guardian identities pass the same orphan checks. A missing, mismatched, or
unverifiable identity remains blocked.

Activity-guarded ACP processes use a per-run cache scope. Completion or failure
recycles only that run's process and guardian, so concurrent runs with the same
runner, home, and working directory cannot cancel each other.

`ops_filesystem.py` owns platform identity and no-follow primitives.
`ops_publication.py` owns descriptor-relative regular-file and directory
publication. These modules depend on the activity boundary but never on
`container_registry.py`; the registry remains the projection and migration
orchestrator.

Ops manifest version 6 records the platform identity of every destination
directory created by Proxima. Each identity is persisted before any child is
published. An existing destination without that durable identity is rejected,
even when empty. Version 5 directory publications upgrade only when their phase
and stored destination snapshot prove one unambiguous continuation.

Immediately before retaining a published legacy source, migration revalidates
the complete manifest snapshot. A changed source name is left in place and the
migration stops for owner intervention.

## Consequences

Positive:

- Owner retry cannot kill a live agent, terminal, script, or preview guardian.
- Linux sentinel and Windows Job recovery share one owner-orphan rule.
- Concurrent guarded runs have independent process lifetime authority.
- Late empty directories cannot become authoritative by being populated.
- Activity, native filesystem, and publication policy have acyclic ownership.

Negative and accepted trade-offs:

- Guarded processes are allocated per run instead of reused across concurrent
  runs.
- A crash between directory creation and durable identity persistence leaves an
  empty unbound directory for owner review rather than guessing ownership.
- Old partially published directory manifests stop when destination ownership
  cannot be proven.

## Related

- Supersedes [ADR-0037](0037-container-activity-and-migration-publication.md)
- [Container Areas and physical Ops storage](../CAPABILITIES.md#container-areas-and-physical-ops-storage)
- [Architecture](../reference/architecture.md)
- [Security boundaries](../security-boundaries.md)

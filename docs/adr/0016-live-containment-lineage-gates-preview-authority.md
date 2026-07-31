# ADR-0016: Live containment lineage gates preview authority

- Status: Accepted
- Date: 2026-07-31

## Context

A candidate listener is safe to preview only while its relationship to the
managed launch is still provable. A launch marker can be copied, a namespace
identity can be shared, and either can outlive the ancestry evidence that ties a
socket owner to the process Proxima launched.

## Decision drivers

- Never authorize marker-only or namespace-only socket ownership.
- Require independent, launch-specific containment identity.
- Require positive live ancestry or process-group evidence.
- Fail closed whenever any proof is incomplete.

## Options considered

1. Trust a matching launch marker. A same-user helper can copy it.
2. Trust a matching PID namespace and marker. This can retain authority after
   live lineage disappears.
3. Require the exact launch namespace, marker, and positive live lineage for
   every socket owner.

## Decision

Every contained preview socket owner must match the exact launch-specific PID
namespace, carry the launch marker, and retain positive live process-group or
ancestry evidence to the managed leader. Missing, stale, inaccessible, or
mismatched evidence produces `ownership_unknown` and no proxy target.

## Consequences

- Marker theft cannot create preview authority.
- Reparented or unverifiable listeners remain fail closed.
- Readiness depends on current procfs evidence at the connection boundary.

## Related

- Supersedes ADR-0012.
- [ADR-0017](0017-manager-owned-provisional-preview-cleanup.md)
- `docs/security-boundaries.md`
- `docs/reference/architecture.md`

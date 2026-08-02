# ADR-0030: Area-scoped artifact media and preview routing

- Status: Superseded by ADR-0042 (Area-scoped media and the targeted
  preview namespace stay in force and are restated there)
- Date: 2026-07-31

## Context

ADR-0029 established a canonical file locator, but retaining that identity only
at an initial read is insufficient. Artifact scans can cross from an Ops-at-dot
root into a nested Code Area. Task, Iterate, Archive, and Design Studio consumers
can also drop the locator before resolving Markdown resources or scene images.

A targeted preview route below the legacy `/api/preview/{slug}/{path}` catch-all
also lets project files collide with routing syntax. Browser normalization of
relative `..` resources can leave the Area portion and re-enter path-only legacy
resolution.

## Decision drivers

1. Physical ownership must determine every scanned and produced artifact target.
2. Artifact targets must survive every read, media, preview, and deletion handoff.
3. Browser-relative resources must not normalize into path-only preview routing.
4. Request-level validation reuse must not weaken per-path realpath jailing.
5. Legacy preview and path-only compatibility must remain available.

## Options considered

1. Treat every path found below the Ops scan root as Ops. Rejected because a
   nested Code Area then receives an invalid identity.
2. Reconstruct targets in each UI renderer. Rejected because display paths do
   not prove ownership and drift between consumers is likely.
3. Carry canonical targets with artifact and scene media records, use a disjoint
   targeted preview namespace, and reuse one validated Area context per request.
   Chosen.

## Decision

Artifact enrichment resolves each scan-relative path against the validated scan
root and then derives its most-specific authoritative Area. Chat and task outputs,
session artifacts, Archive records, Iterate documents, and ArtifactViewer retain
that target. Session deletion may act on any authoritative Area descendant of the
validated Ops workspace and removes references by scan-relative identity.

Design scene image layers store an optional target beside `src`. Image frames
carry the same target when absorbing or detaching an image. Canvas, gallery,
Archive thumbnail, and export renderers pass it to the shared file URL resolver.

Targeted resources use
`/api/target-preview/{slug}/{kind}/{id}/{area-relative-path}`. The legacy
`/api/preview/{slug}/{path}` route remains path-only compatibility, but its
namespace no longer contains targeted routing syntax. Leaving a target path by
ordinary browser normalization therefore reaches no legacy preview match.

Tree and Archive requests build one validated Area context and reuse it for all
entries in that request. Each entry still resolves through `fsapi` and the
authoritative ownership check.

## Consequences

**Positive**

- Ops-at-dot scans preserve nested Code Area identity.
- Task, Iterate, Archive, and Design Studio media cannot silently select a
  same-name Container shadow.
- Targeted preview syntax cannot collide with project files in the legacy route.
- Area-root and overlap validation runs once per multi-entry request.

**Negative / accepted trade-offs**

- Artifact and scene payloads carry additional locator data.
- Existing targeted preview URLs from pre-release builds are not stable; legacy
  path-only preview URLs remain compatible.
- Scene transformations must move or clear image targets together with sources.

## Related

- [ADR-0029](0029-canonical-file-targets.md)
- [Architecture and data model](../reference/architecture.md)
- [Files capability](../CAPABILITIES.md#11-files--uploads-apis)

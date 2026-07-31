# ADR-0020: Preview lifecycles use project generations

- Status: Superseded by ADR-0024
- Date: 2026-07-31

## Context

A cancelled launch can finish after its request returns. A retry for the same
project can otherwise install newer authority before stale cleanup registers and
removes the cancelled process.

## Decision drivers

- Reserve a project lifecycle before provisional work can escape cancellation.
- Prevent stale cleanup from mutating newer authority.
- Keep cancellation cleanup independent of the request.
- Serialize Start, Stop, and immediate retry for one project.

## Options considered

1. Compare app object identity after registration. Registration can already have
   overwritten a newer app.
2. Hold request cancellation until cleanup finishes. Repeated cancellation can
   still interrupt request-owned cleanup.
3. Assign a monotonic project generation and make retries wait for its
   manager-owned cleanup.

## Decision

Each project preview lifecycle has a monotonic generation reserved before output
supervisor creation or process spawn. Start and Stop serialize per project. A
retry waits for an unfinished cancelled generation, and every registration,
terminal transition, state-record removal, and cleanup mutation requires the
matching generation.

## Consequences

- Cancel then retry cannot orphan or overwrite the retry process.
- Different projects retain independent lifecycle concurrency.
- Shutdown can reconcile project generations concurrently.

## Related

- Supersedes ADR-0017.
- [ADR-0021](0021-preview-supervisors-own-app-scopes.md)
- `docs/security-boundaries.md`

# ADR-0022: Preview log polling uses versioned deltas

- Status: Accepted
- Date: 2026-07-31

## Context

The bounded log can still contain several megabytes. Sending the complete ring
every polling interval makes bandwidth proportional to the retained log size
even when no output changed.

## Decision drivers

- Keep idle polling constant size.
- Preserve the bounded complete-line and partial-line semantics.
- Recover correctly when a client falls behind the ring.
- Reserve full snapshots for explicit finalization.

## Options considered

1. Poll the complete bounded snapshot. Memory is bounded but bandwidth is not.
2. Increase the polling interval. Large repeated transfers remain and status
   becomes less responsive.
3. Track a stream version and completed-line cursor, returning deltas or a
   bounded reset when the cursor falls behind.

## Decision

Routine preview log polling sends the last stream version and completed-line
cursor. An unchanged stream returns only version metadata. A changed stream
returns new complete lines plus the current bounded partial tail. A cursor older
than the retained ring receives one bounded reset. Stop and exit finalization
request the full atomic snapshot.

## Consequences

- Idle polling bandwidth is constant and independent of log size.
- High-volume output transfers retained data once per cursor advance.
- Final stopped and exited logs retain the same atomic boundary.

## Related

- [ADR-0018](0018-preview-status-log-framing-is-bounded.md)
- [ADR-0021](0021-preview-supervisors-own-app-scopes.md)
- `docs/CAPABILITIES.md`

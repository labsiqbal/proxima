# ADR-0017: Manager-owned provisional preview cleanup

- Status: Accepted
- Date: 2026-07-31

## Context

App process creation can finish after the request that initiated it is
cancelled. If cleanup remains owned by that request, repeated cancellation can
interrupt signaling or reaping and leave a process without manager authority.

## Decision drivers

- Give every successful spawn a lifecycle owner.
- Make cleanup independent of request cancellation.
- Signal only the process Proxima created.
- Reconcile incomplete cleanup during manager shutdown.

## Options considered

1. Await cleanup in the cancelled request. Further cancellation can cancel the
   cleanup itself.
2. Ignore a spawn that finishes after cancellation. This leaks process
   authority and output ownership.
3. Register cleanup in a manager-owned task registry and reconcile the registry
   at shutdown.

## Decision

When cancellation overlaps output-broker creation or process spawn, AppManager
places disposal or provisional process cleanup in its own task registry before
propagating cancellation. Cleanup is idempotent, completes process signaling and
reaping independently of the request, and is reconciled during shutdown.

## Consequences

- Repeated request cancellation cannot abandon a provisional process.
- Shutdown may wait briefly for controlled process cleanup.
- Cleanup tasks retain only the authority needed to complete their lifecycle.

## Related

- Extracts provisional cleanup from ADR-0012.
- [ADR-0016](0016-live-containment-lineage-gates-preview-authority.md)
- `docs/reference/architecture.md`

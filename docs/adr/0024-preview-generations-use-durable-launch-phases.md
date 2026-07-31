# ADR-0024: Preview generations use durable launch phases

- Status: Accepted
- Date: 2026-07-31

## Context

An in-memory generation prevents concurrent request cleanup from overwriting a
retry, but it cannot prevent generation reuse after an API crash between process
spawn and the first durable app record.

## Decision drivers

- Reserve a generation before any external process can start.
- Recover an app whose spawn completed while the API was unavailable.
- Prevent an incomplete generation from being reused or proxied.
- Keep persistence transitions atomic.

## Options considered

1. Persist only after spawn. A crash can leave an unrecorded live process.
2. Persist only a pending marker. A crash during spawn cannot recover the
   supervisor identity.
3. Persist pending, broker-attached, and app-attached launch phases.

## Decision

Each generation writes a pending record before supervisor creation, atomically
attaches the exact supervisor identity before requesting spawn, and atomically
attaches the process identity before Start succeeds. Restart reconciles the
latest durable phase. A phase without recoverable exact authority remains
unadopted and cannot be reused, proxied, or signaled.

## Consequences

- A crash at any launch boundary cannot create an unrecorded preview.
- Failed registration uses the same generation-matched terminal disposal path.
- Startup reconciliation runs project candidates concurrently under one
  aggregate deadline.

## Related

- Supersedes ADR-0020.
- [ADR-0025](0025-preview-apps-use-launch-specific-cgroups.md)
- `docs/security-boundaries.md`

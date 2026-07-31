# ADR-0012: Exact containment proof gates preview authority

- Status: Accepted
- Date: 2026-07-31

## Context

A launch marker and PID namespace identity are not sufficient evidence that a
listener still belongs to the managed launch. A same-user process can copy the
marker, and namespace evidence alone does not establish a live relationship to
the process Proxima spawned.

Containment information also arrives after process creation. Waiting for it
before registering the process leaves a cancellation window where the process
and its output have no lifecycle owner.

## Decision drivers

- Authorize only listeners with independent, launch-specific evidence.
- Fail closed while containment proof is incomplete.
- Own every spawned process and output pipe through cancellation.
- Reap only the provisional process Proxima created.

## Options considered

1. Accept marker plus namespace identity. This can authorize a listener after
   its live relationship to the managed launch is lost.
2. Block launch registration until containment information arrives. This leaves
   process ownership incomplete during setup and makes slow information writes
   abort otherwise controlled launches.
3. Require exact namespace identity, the launch marker, and positive live
   process-group or ancestry evidence while registering the process
   provisionally as soon as spawn completes.

## Decision

Every owner of a contained preview socket must match the launch-specific PID
namespace, carry the launch marker, and have positive live process-group or
ancestry evidence connecting it to the managed leader. Missing or mismatched
evidence yields `ownership_unknown` and never a proxy target.

Proxima registers provisional process authority and begins output draining
immediately after spawn. Containment information completes asynchronously.
Until it completes successfully, the authority has no namespace proof and
preview remains fail closed. Cancellation waits for an in-flight spawn, then
terminates and reaps only the resulting provisional managed process.

## Consequences

- A copied marker and matching namespace cannot replace live launch lineage.
- Slow containment information delays readiness without losing process
  ownership or output.
- Cancellation cleanup may wait for the controlled spawn to resolve so it can
  be terminated and reaped safely.

## Related

- Supersedes ADR-0011.
- [ADR-0013](0013-detached-preview-output-uses-os-sink-helpers.md)
- `docs/security-boundaries.md`
- `docs/reference/architecture.md`
- `docs/CAPABILITIES.md`

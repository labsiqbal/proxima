# ADR-0019: Launch-time broker owns preview output

- Status: Accepted
- Date: 2026-07-31

## Context

An asyncio subprocess reader can buffer bytes that a late pipe handoff cannot
recover. A detached child can also inherit stdout beyond Stop and beyond API
service shutdown. Closing that read end can deliver `EPIPE` or `SIGPIPE`, while
keeping it in the API cgroup ties its lifetime to the service being stopped.

## Decision drivers

- Own the child output pipe before app process creation.
- Produce an atomic final snapshot of all currently available bytes.
- Keep draining detached writers after API disconnect or service restart.
- Fail before app launch when durable output ownership is unavailable.

## Options considered

1. Hand off an asyncio pipe after a bounded drain. Transport-buffered bytes can
   be lost and handoff can fail after Stop begins.
2. Keep a reader task in the API service. Service teardown closes the read end.
3. Create a broker before launch, direct app output to it, and place packaged
   Linux brokers in socket-activated sibling units outside the API cgroup.

## Decision

A preview output broker owns the child pipe before the app process is spawned.
It continuously maintains the bounded log, drains all available pipe bytes
before replying to an atomic snapshot request, and continues draining after its
API control channel closes until every writer reaches EOF.

Packaged Linux services obtain one broker from a socket-activated sibling
systemd unit for each app launch. Windows uses a detached breakaway broker when
the host supports it. If the platform or supervisor cannot provide durable
broker ownership, start fails before spawning the app with the recoverable
`output_sink_unavailable` state. Stop preserves the last available log and
completes transactionally if a broker later disconnects.

## Consequences

- Final stopped logs include output already available at the snapshot boundary.
- API stop and restart do not close a surviving detached writer's read end.
- Brokers consume bounded memory and exit at writer EOF.
- Unsupported hosts receive an explicit retryable lifecycle result without an
  untracked app process.

## Related

- Replaces the helper handoff in ADR-0013.
- [ADR-0018](0018-preview-status-log-framing-is-bounded.md)
- `docs/security-boundaries.md`
- `docs/reference/architecture.md`

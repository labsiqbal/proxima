# ADR-0010: Preview authority requires verified managed connections

- Status: Accepted
- Date: 2026-07-31

## Context

Run & Preview launches owner-selected development servers as the Proxima service
user. The selected port can already belong to another process, or a foreign
process can claim it after the start preflight. A port number that was verified
earlier can also become stale before a proxy opens its upstream connection.
Forwarding to that stale number can expose foreign content through an
authenticated Proxima preview.

Listener ownership is observable on supported Linux hosts through procfs. Other
hosts, restricted procfs mounts, and detached children without a shared process
group do not provide equivalent lifetime proof.

## Decision drivers

- Never send preview request bytes to an unrelated listener.
- Never signal or terminate a process merely because it owns a candidate port.
- Keep appview, relay, subdomain, HTTP, and WebSocket behavior consistent.
- Preserve useful lifecycle and log feedback when ownership cannot be proven.
- Fail closed when the host cannot provide the required ownership evidence.

## Options considered

1. Trust reachability or a prior ready status. This is portable but does not
   prove ownership and leaves a check-to-connect race.
2. Recheck listener ownership immediately before connecting. This narrows but
   does not close the interval between the proof and the TCP connection.
3. Open the TCP connection, map its server-side socket through procfs, and send
   protocol bytes only after that socket belongs to the managed process group.
   This closes the handoff race on supported hosts and fails closed elsewhere.
4. Isolate every preview in a dedicated network namespace. This provides a
   stronger boundary but is not available in every supported installation.

## Decision

The requested port remains a candidate. Readiness requires all listener sockets
to map to the managed process group. Each browser-facing proxy then opens a new
upstream TCP connection and verifies the connected server-side socket before it
sends HTTP or WebSocket bytes.

Appview, per-app relays, and preview subdomains use this same connection
boundary. Under automatic binding, one per-app relay port binds separately on
loopback and the Tailscale interface when present, so local and tailnet origins
remain reachable without a wildcard listener. Appview remains the fallback when
relays are disabled. Capability authentication precedes target resolution and
procfs work. No browser path connects directly to the candidate development-server
port.

A foreign listener produces a sticky `port_conflict` state. Proxima may signal
only the process group it created. Missing procfs data, incomplete socket-owner
visibility, or an uncontained descendant in another process group fails closed
as `ownership_unknown`. An ephemeral launch marker preserves that classification
after a descendant is reparented. The marker never grants preview authority to an
uncontained process. A detached descendant qualifies only while PID namespace
containment owns its lifetime.

## Consequences

- A foreign listener may complete a TCP handshake, but it receives no preview
  request bytes and its content is not sampled.
- Preview proxying depends on procfs ownership evidence and is unavailable when
  that evidence cannot be established.
- Every proxy request uses a newly verified upstream connection instead of a
  reusable connection pool.
- Final stdout draining is bounded and retains output already collected without
  signaling an uncontained detached child.
- Detached development-server patterns require configured PID namespace
  containment or a command that keeps the listener in the managed process group.

## Related

- `docs/security-boundaries.md`
- `docs/reference/architecture.md`
- `docs/CAPABILITIES.md`

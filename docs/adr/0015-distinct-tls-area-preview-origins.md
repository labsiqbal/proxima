# ADR-0015: Active file previews require distinct TLS Area origins

- Status: Accepted
- Date: 2026-07-31

## Context

ADR-0014 added a path-scoped HTTPS gateway on the Proxima application origin for
installations without a dedicated preview hostname. Gateway documents used an
opaque sandbox origin and CORS to read their Area resources.

That boundary cannot support native module workers. A Worker constructor requires
its script URL to be same-origin with the document, while an opaque document origin
is not same-origin with a URL on the Proxima application origin. CORS does not
change the Worker constructor's origin check. Granting the gateway document
same-origin identity would instead give untrusted project content Proxima origin
authority.

## Decision drivers

1. Active project content must never receive Proxima application origin authority.
2. Native same-Area module workers remain a supported preview capability.
3. Every preview resource must remain bound to one canonical Area and its realpath
   jail.
4. An unavailable safe origin must fail closed.

## Options considered

1. Keep the opaque gateway and bootstrap workers through fetched blob URLs.
   Rejected because it changes native worker URL semantics and requires rewriting
   untrusted documents.
2. Give gateway documents same-origin identity. Rejected because project scripts
   could access Proxima routes and owner state.
3. Require a distinct TLS Area origin for HTTPS active previews. Chosen.

## Decision

HTTPS active previews use an Area-specific hostname under the configured apps
domain. Its router is permanently bound to one validated Area and exposes no
Proxima or legacy routes. Capability exchange, exact authenticated
`frame-ancestors`, canonical resolution, worker response policy, and Service Worker
rejection remain mandatory. Native module workers use same-origin Area URLs.

Named local origins and plain HTTP Area relays retain the same isolated-host model.
When an HTTPS installation has no distinct TLS Area origin, active preview entry
fails with 503. It does not fall back to the Proxima origin or a plaintext relay.
Every document-viewable response, including PDF, receives the exact authenticated
frame-ancestor policy.

## Consequences

**Positive**

- Native module workers behave consistently on local, HTTP relay, and TLS Area
  origins.
- Untrusted active content never shares Proxima origin authority.
- Missing TLS preview infrastructure fails visibly and safely.

**Negative / accepted trade-offs**

- HTTPS remote installations must configure a TLS-capable apps domain before
  canonical previews are available.
- A Tailscale Serve entry alone cannot provide a distinct Area origin.

## Related

- Supersedes [ADR-0014](0014-capability-scoped-file-preview-gateways.md)
- [ADR-0010](0010-canonical-file-targets.md)
- [ADR-0013](0013-area-bound-file-preview-origins.md)
- [Architecture and data model](../reference/architecture.md)

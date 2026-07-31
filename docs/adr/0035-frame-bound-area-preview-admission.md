# ADR-0035: Area preview admission is frame-bound

- Status: Accepted
- Date: 2026-07-31

## Context

Area preview origins retain same-origin behavior for relative resources and native
module workers. An authorized frame therefore stores a host-scoped capability
cookie that also accompanies later same-origin requests.

Trusting every same-origin request would let an Area document open its clean URL in
a top-level browsing context. The capability would remain valid, but the document
would no longer be constrained by the authenticated Proxima frame ancestor.

## Decision drivers

1. Active Area documents must execute only inside a proven frame or iframe.
2. Same-origin Area scripts, styles, fonts, workers, and fetches must keep working.
3. Named hosts, HTTP relays, TLS hosts, and clean redirects must apply one policy.
4. Capability cookies must not authorize a different browsing context.

## Options considered

1. Trust same-origin requests before checking their destination. Rejected because
   an ambient cookie would authorize clean top-level navigation.
2. Reject every same-origin request. Rejected because it would break supported
   relative resources and native module workers.
3. Classify browsing context before trust and share that admission boundary across
   every Area transport. Chosen.

## Decision

One manager-owned dispatch gate applies capability and Fetch Metadata admission to
named hosts, plain HTTP relays, TLS hosts, and clean redirects. It rejects a
top-level document destination before considering request site or ambient
capability state. Navigation is admitted only for a proven iframe or frame
destination. Cross-origin and same-site frame entry must carry the signed
capability; a same-origin clean frame may use its validated host-scoped cookie.
Same-origin non-document resources remain available. Every admitted request still
validates its capability and resolves through the canonical Area jail.

## Consequences

**Positive**

- A clean Area URL cannot execute top-level after an authorized frame sets its
  capability cookie.
- All supported Area transports enforce the same browsing-context boundary.
- Relative resources and native module workers keep their same-origin behavior.

**Negative / accepted trade-offs**

- Browsers without usable Fetch Metadata cannot enter an active Area preview.
- Popup and top-level preview workflows remain intentionally unsupported.

## Related

- Extends [ADR-0034](0034-distinct-tls-area-preview-origins.md) without superseding
  its distinct-origin decision.
- [Canonical file targets](0029-canonical-file-targets.md)
- [Capabilities](../CAPABILITIES.md)
- [Security boundaries](../security-boundaries.md)

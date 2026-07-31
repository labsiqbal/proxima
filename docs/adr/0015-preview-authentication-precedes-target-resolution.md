# ADR-0015: Preview authentication precedes target resolution

- Status: Accepted
- Date: 2026-07-31

## Context

Resolving a preview target performs procfs ownership work. If target resolution
runs before capability authentication, unauthenticated traffic can repeatedly
force that work even though it can never receive preview content.

## Decision drivers

- Reject unauthenticated preview requests before ownership scanning.
- Preserve current ownership verification at the upstream connection boundary.
- Apply the same ordering to relay and preview-subdomain paths.

## Options considered

1. Resolve the target before authentication. This exposes ownership scanning to
   unauthenticated load.
2. Cache a resolved port. This weakens current ownership guarantees.
3. Authenticate first, then invoke a request-scoped target resolver immediately
   before opening and verifying the upstream connection.

## Decision

Every browser-facing preview path authenticates its capability before invoking
target resolution or procfs ownership scanning. The authenticated request then
resolves and verifies the managed target at the upstream connection boundary.

## Consequences

- Invalid capabilities cannot trigger ownership scans.
- Authentication does not turn a target into a reusable port grant.
- Relay and subdomain proxy paths share the same ordering invariant.

## Related

- Extracts the authentication-ordering decision from ADR-0011.
- [ADR-0012](0012-exact-containment-proof-gates-preview-authority.md)
- `docs/security-boundaries.md`
- `docs/reference/architecture.md`

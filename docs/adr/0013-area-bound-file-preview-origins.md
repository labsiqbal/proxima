# ADR-0013: Canonical file previews use Area-bound capability origins

- Status: Superseded by ADR-0014
- Date: 2026-07-31

## Context

ADR-0012 put targeted HTML on the owner API origin and relied on an
opaque-origin sandbox plus path-scoped Content Security Policy. A browser
normalizes document navigation before sending a request, so a script could
navigate from the targeted path to a legacy or application route on that same
origin. The opaque origin also made same-Area module scripts, URL workers, web
fonts, and fetch requests cross-origin, which blocked behavior the decision
intended to preserve.

Canonical previews need a boundary that survives arbitrary path normalization.
That boundary must not expose Proxima routes or accept a path-only identity.

## Decision drivers

1. Every preview origin must remain bound to one validated Container Area.
2. Parent traversal and scripted navigation must never reach a legacy or
   application route.
3. Same-Area modules, workers, fonts, media, frames, and fetch must remain
   same-origin.
4. Preview authority must be short-lived and must not reuse the owner API
   session.
5. Local, apps-domain, and relay deployments must preserve the same boundary.

## Options considered

1. Extend the API-origin CSP with more navigation directives. Rejected because
   browser support is incomplete and the API origin remains the routing
   boundary.
2. Keep an opaque origin and add CORS to every resource. Rejected because it
   still leaves document navigation on the API origin and expands resource
   policy across the application.
3. Give each project Area a dedicated origin with an Area-bound capability and
   an Area-only router. Chosen.

## Decision

`/api/target-preview/{slug}/{kind}/{id}/{path}` is an authenticated entry route.
It validates the canonical locator, mints a short-lived capability bound to the
project id and authoritative Area, and redirects to that Area's dedicated
origin. The capability is exchanged there for an HttpOnly, host-scoped cookie
and removed from the visible URL before content is served.

Named localhost previews use an Area-specific `.localhost` host. Apps-domain
deployments use a single-label Area host provisioned onto the existing tunnel.
IP-based and other deployments use one relay listener per Area and interface,
which keeps loopback IP previews in the same browser cookie site. The hostname
or relay listener selects exactly one Area, and its router exposes only
Area-relative file reads. Every request still reloads the Container binding,
verifies authoritative ownership, and crosses the realpath jail.

The dedicated response policy allows same-origin scripts, module scripts,
workers, fonts, fetch, styles, images, media, and frames. HTML remains
sandboxed with scripts and same-origin identity, while forms, objects, base
changes, cross-origin connections, and cross-origin resources remain denied.
Legacy `/api/preview/{slug}/{path}` stays path-only and rejects a canonical
target parameter. Path-only HTML viewers keep the opaque script sandbox and do
not receive same-origin permission.

## Consequences

**Positive**

- Path normalization and scripted navigation stay on an Area-only router.
- Same-Area browser features work without exposing Proxima's application
  origin.
- A capability for one Area cannot authenticate another Area origin.
- Direct and embedded previews use the same validation and isolation model.

**Negative / accepted trade-offs**

- Apps-domain installs provision one stable preview hostname per Area.
- Installs without a usable preview hostname consume one relay port per
  previewed Area and interface.
- Preview URLs redirect twice: once to the dedicated origin and once to remove
  the capability from the URL.

## Related

- Superseded by [ADR-0014](0014-capability-scoped-file-preview-gateways.md)
- Supersedes [ADR-0012](0012-sandboxed-target-preview-resources.md)
- [ADR-0010](0010-canonical-file-targets.md)
- [ADR-0011](0011-area-scoped-artifact-media.md)
- [Architecture and data model](../reference/architecture.md)

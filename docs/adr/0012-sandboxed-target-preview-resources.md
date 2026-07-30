# ADR-0012: Targeted HTML previews use a sandboxed resource scope

- Status: Accepted
- Date: 2026-07-31

## Context

ADR-0011 separated targeted preview URLs from the legacy path-only preview
route. A path namespace alone is not an isolation boundary. A relative URL with
enough parent segments is normalized by the browser before the request and can
therefore reach `/api/preview` or another same-origin route.

Project HTML and runner-produced artifacts are untrusted. Their relative
resources must retain the originating Area without removing legacy preview
compatibility.

## Decision drivers

1. Arbitrary parent traversal must not load a path-only Container resource.
2. Same-Area styles, scripts, images, fonts, media, frames, and workers must
   remain usable.
3. Targeted HTML must remain authenticated and work in local and remote
   deployments without a new DNS or listener requirement.
4. Legacy path-only preview URLs must remain available to legacy callers.

## Options considered

1. Rely on a disjoint route prefix. Rejected because browser path
   normalization can leave any finite prefix.
2. Serve targeted previews from a separate host or port. Rejected because it
   adds DNS, cookie, listener, and remote-tunnel requirements to ordinary file
   preview.
3. Apply an opaque-origin sandbox plus a response policy that allows resources
   only below the exact targeted Area prefix. Chosen.

## Decision

Every targeted HTML response carries a Content Security Policy that applies an
opaque-origin sandbox with scripts enabled, denies resources by default, and
allows local resources only below that response's exact
`/api/target-preview/{slug}/{kind}/{id}/` prefix. Data and blob media remain
available where needed. Objects, base URL changes, and forms are disabled.

Artifact HTML iframes also retain their element-level script sandbox. The
server continues to resolve every allowed resource request through the
canonical Area resolver and realpath jail. Legacy preview remains reachable
only as an explicit legacy request, not as a targeted document subresource.

## Consequences

**Positive**

- Browser normalization cannot turn a targeted document resource into a
  legacy Container preview load.
- The same policy protects both embedded and directly opened targeted HTML.
- No deployment-specific preview origin is required.

**Negative / accepted trade-offs**

- Targeted HTML cannot load arbitrary remote resources or same-origin API
  routes outside its Area resource prefix.
- Consumers that need an interactive application use the managed app preview
  origin rather than an artifact HTML preview.

## Related

- [ADR-0010](0010-canonical-file-targets.md)
- [ADR-0011](0011-area-scoped-artifact-media.md)
- [Architecture and data model](../reference/architecture.md)

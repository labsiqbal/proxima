# ADR-0033: File previews use capability-scoped execution boundaries

- Status: Superseded by ADR-0034
- Date: 2026-07-31

## Context

ADR-0032 isolated canonical file previews on Area-specific origins. Its cookie
exchange and same-origin document policy left four gaps. An embedded document
could navigate itself back to executable content on the Proxima origin, an
external ancestor could frame a known Area document, workers were not governed
by a response policy, and a stable Area origin could retain a Service Worker.
Plain HTTP relay origins also fail when the authenticated Proxima entry point is
HTTPS.

The boundary must preserve useful same-Area scripts, modules, workers, fonts,
and fetch without giving untrusted project content access to Proxima.

## Decision drivers

1. Executable project content must never run with the Proxima application
   origin's authority.
2. Capabilities must be short-lived, Area-bound, and bound to the authenticated
   Proxima origin that opened the preview.
3. Local, apps-domain, Tailscale HTTPS, and plain HTTP installations must avoid
   mixed content.
4. Ordinary workers remain useful, but Service Workers and worker exfiltration
   do not.
5. Active media and every relative resource must still cross the canonical
   resolver and realpath jail.

## Options considered

1. Continue relying on `navigate-to`. Rejected because Chromium does not enforce
   it for document self-navigation.
2. Disable all scripts and workers. Rejected because interactive HTML artifacts
   are a supported preview format.
3. Keep dedicated Area origins and add a TLS-origin gateway for deployments
   without a dedicated HTTPS hostname. Chosen.

## Decision

Apps-domain and named local installations retain the dedicated Area origin.
Its signed capability now carries the exact authenticated Proxima frame origin,
uses a strict host cookie, sets an exact `frame-ancestors` policy, rejects every
Service Worker script request, and applies a restrictive response CSP to worker
scripts. The dedicated origin may retain same-origin identity because its router
exposes only one validated Area and no Proxima routes.

An HTTPS installation without an apps domain uses a reserved capability path on
its existing TLS origin instead of redirecting to a plain HTTP relay. The
gateway verifies the signed Area and gateway origin on every request. Its HTML
response applies `sandbox allow-scripts` without same-origin identity, and its
path-scoped policy plus `Access-Control-Allow-Origin: null` permits same-Area
module scripts, module workers, fonts, and fetch. Parent traversal can leave the
reserved path only as document navigation; executable main-origin responses
deny framing, and Fetch Metadata rejects embedded same-site or cross-site requests
before they reach Proxima routes.

Legacy preview inputs remain path-compatible, but active media is never served
executable on the main origin. It is upgraded to the canonical preview boundary.
HTML is rendered only under the appropriate sandbox. XHTML, SVG, and other
active XML media are forced to download with a deny-by-default policy. All
inline responses use `nosniff`.

Design Studio does not use preview origins for canvas pixels. It fetches
canonical raw bytes through the authenticated API, renders managed blob URLs,
revokes them with component lifetime, and uses the same byte boundary for
exports.

File-preview capability query values, gateway path values, and capability
cookies are redacted from server logs. Cloudflare tunnel ingress changes share
one serialized read-modify-write boundary and verify refreshed state before
reporting success.

## Consequences

**Positive**

- Absolute navigation cannot regain Proxima origin authority inside ArtifactViewer.
- External framing, worker exfiltration, and persistent Service Workers are denied.
- Tailscale Serve and other HTTPS entries avoid mixed-content preview failures.
- Canvas and export pixels retain their canonical Area identity.

**Negative / accepted trade-offs**

- HTTPS fallback resources use explicit opaque-origin CORS headers.
- Active XHTML and SVG files download instead of executing inline.
- Capability paths are longer and require access-log redaction.

## Related

- Superseded by [ADR-0034](0034-distinct-tls-area-preview-origins.md)
- Supersedes [ADR-0032](0032-area-bound-file-preview-origins.md)
- [ADR-0029](0029-canonical-file-targets.md)
- [ADR-0030](0030-area-scoped-artifact-media.md)
- [Architecture and data model](../reference/architecture.md)

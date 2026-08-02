# ADR-0042: File preview is one sandboxed iframe on Proxima's origin

- Status: Accepted
- Date: 2026-08-03

## Context

ADR-0029 through ADR-0036 built canonical file preview as a capability system:
Area-bound origins (named local hosts, apps-domain TLS hostnames, plain-HTTP
relay listeners), signed short-lived capability tokens carried in a query
parameter, a bootstrap exchange that turned that token into an Area-scoped
`SameSite=None` cookie, a Structured-Field-strict Fetch Metadata admission
matrix over ~30 mode/destination tuples, and opaque active generations bound to
an owner session, an Area, and a viewer.

The prune audit ([#114](https://github.com/labsiqbal/proxima/issues/114),
verdict [#117](https://github.com/labsiqbal/proxima/issues/117)) measured that
construction at roughly 2,500 lines - about 1,300 of subsystem, a 2,100-line
browser verification harness, and the tests around them - and judged it
multi-tenant-grade engineering for a single-owner product. Its real protection
is one property: **a previewed document must not run in Proxima's origin**,
because that origin holds the owner's session.

That property does not need a second origin. An iframe whose `sandbox`
attribute omits `allow-same-origin` puts the document in an **opaque origin**
whatever URL it was loaded from - it cannot read the embedder's DOM, storage,
or cookies, and the browser refuses to attach same-site credentials to the
requests it makes.

## Decision drivers

1. A previewed file must never reach the owner's session, Proxima's DOM, or
   another Area - the property ADR-0031..ADR-0036 existed to guarantee.
2. Viewing HTML must not execute project code without informed owner consent
   (ADR-0036's decision, which is still right).
3. The mechanism should be readable in one sitting by one owner.
4. No deployment shape (loopback, tailnet HTTP, apps-domain HTTPS) may need
   extra DNS, TLS, or relay provisioning just to look at a file.
5. Canonical Area identity (ADR-0029/0030) must survive: previews resolve
   through `file_targets.py`, the realpath jail, and Area ownership as before.

## Options considered

1. **Keep the capability origins.** Rejected: the cost is the whole
   subsystem, and on plain-HTTP tailnet installs (the owner's real
   deployment) the `SameSite=None` cookie is unavailable anyway, so the
   choreography degrades to exactly what a sandbox already gives.
2. **Serve previews from Proxima's origin with no sandbox.** Rejected
   outright: that is the session-theft path the ADRs were written against.
3. **Serve previews from Proxima's origin inside a sandbox that never gets
   `allow-same-origin`, and keep the owner consent screen for scripts.**
   Chosen.

## Decision

`/api/target-preview/{slug}/{kind}/{id}/{path}` (and the legacy path-only
`/api/preview/{slug}/{path}`) answer with the file bytes directly. There is no
redirect, no second origin, no minted cookie, and no Fetch Metadata admission
matrix. Authentication is the ordinary owner session on the iframe navigation;
resolution still goes through the canonical locator and the realpath jail.

The sandbox is stated twice, on purpose:

- **In the embedder.** ArtifactViewer renders `<iframe sandbox="">` for passive
  preview and `<iframe sandbox="allow-scripts">` for active mode. Neither ever
  contains `allow-same-origin`.
- **In the response.** Every preview carries `Content-Security-Policy:
  sandbox` (passive) or `sandbox allow-scripts` (active), so the document lands
  in an opaque origin even if it is opened directly or a future UI bug widens
  the iframe attribute.

Passive HTML additionally gets `default-src 'none'` with only inline styles and
`data:` media - no script execution of any kind. Active HTML gets scripts,
blob workers, external media, and `connect-src *`; that is what the consent
screen warns about. Executable non-HTML media (SVG, XHTML, XML) is still
handed over as a download, never rendered inline.

Active mode keeps ADR-0036's decision unchanged in behaviour: passive is the
default and the unknown state, Artifact Review labels the current mode, and the
owner must accept a trust dialog. Consent is recorded server-side against
(owner session, Area, viewer session) and **requires the bearer token**, so an
ambient cookie or a cross-site form cannot self-enable it. Nothing is
persisted: disabling, closing the viewer, changing Areas, logging out, or
restarting the server returns every preview to passive, and a stale active URL
fails closed with 403.

`PreviewIsolationMiddleware` keeps the other direction: a request to Proxima
that a browser labels `Sec-Fetch-Site: same-site`/`cross-site` with a
destination other than `document` - the shape that framed preview content and
app-preview dev servers produce - is refused, and app-origin HTML that does not
declare its own framing policy still gets `frame-ancestors 'none'` and
`X-Frame-Options: DENY`.

This ADR supersedes ADR-0029 through ADR-0036. Two of their decisions survive
verbatim and are restated here so nothing is lost with the file status:

- **Canonical file targets (ADR-0029) and Area-scoped artifact media
  (ADR-0030) remain in force**: one authoritative Area identity per file,
  server-constructed locators, Area-relative preview paths under
  `/api/target-preview/{slug}/{kind}/{id}/...` so browser-relative resources
  keep their Area, and `/api/preview/{slug}/{path}` as path-only compatibility.
- **Active preview is an explicit trusted mode (ADR-0036)**, with the consent
  screen, the labels, and the revocation semantics described above.

## Consequences

**Positive**

- The subsystem is one small module plus response headers; the capability
  tokens, cookies, bootstrap redirects, Area hostnames, relay listeners, TLS
  provisioning, and Fetch Metadata matrix are gone, together with their tests
  and the 2,100-line browser verification harness.
- Every deployment shape behaves identically. HTTPS installs without an
  apps-domain Area hostname no longer fail HTML preview entry with 503, and
  plain-HTTP tailnet installs no longer depend on a cookie the browser will not
  send.
- The security statement is short enough to be checkable: no
  `allow-same-origin`, asserted in the embedder, in the response, and in tests.
- Nothing in the security core (realpath jail, Master broker/firewall, script
  trust, push remote pinning, runner env filtering) is touched.

**Negative / accepted trade-offs**

- A sandboxed document has an opaque origin, so the browser sends no
  credentials with its subresource requests and Proxima refuses framed
  non-document requests. **Previews render self-contained documents**;
  relative stylesheets, scripts, and images stay unfetched. That was already
  true of passive preview under the retired design (the admission matrix
  rejected cross-site subresources); active mode loses it, and multi-file sites
  belong in Run & Preview, which serves a real dev server on its own origin.
- Active previews cannot use same-origin module workers; blob workers remain.
- The `blocks_application_request` guard now matches lowercased header values
  instead of validating Structured Field syntax. Sec-Fetch-* are forbidden
  header names, so only a non-browser client - which has no session cookie to
  abuse - could produce a noncanonical value.

## Related

- Supersedes [ADR-0029](0029-canonical-file-targets.md),
  [ADR-0030](0030-area-scoped-artifact-media.md),
  [ADR-0031](0031-sandboxed-target-preview-resources.md),
  [ADR-0032](0032-area-bound-file-preview-origins.md),
  [ADR-0033](0033-capability-scoped-file-preview-gateways.md),
  [ADR-0034](0034-distinct-tls-area-preview-origins.md),
  [ADR-0035](0035-frame-bound-area-preview-admission.md), and
  [ADR-0036](0036-active-file-preview-is-explicit-trusted-mode.md).
- [Security boundaries](../security-boundaries.md#canonical-file-preview)
- [Capabilities](../CAPABILITIES.md)
- [Prune spec](../PRUNE-SPEC.md) item A5

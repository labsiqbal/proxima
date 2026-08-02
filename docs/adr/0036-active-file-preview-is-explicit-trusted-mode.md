# ADR-0036: Active file preview is an explicit trusted mode

- Status: Superseded by ADR-0042
- Date: 2026-07-31

## Context

ADR-0034 and ADR-0035 isolate executable file previews on an Area-only origin
and constrain document entry to a proven Proxima frame. That boundary protects
Proxima and other Areas, but it cannot promise confidentiality for the selected
Area once trusted content can run scripts, workers, navigation, and network
requests. Treating every HTML preview as executable also crosses that trust
boundary without an owner decision.

## Decision drivers

1. Unknown or absent owner intent must never enable project code.
2. Ordinary preview must remain useful without script execution.
3. Enabling code must be an informed owner action at the narrowest useful scope.
4. Active content must remain isolated from Proxima and every other Area.
5. Disabling active mode must invalidate every previously issued authority.

## Options considered

1. Keep every HTML preview active and rely on origin isolation. Rejected because
   Area data can still leave through active content without owner consent.
2. Remove executable previews entirely. Rejected because trusted interactive
   artifacts, module workers, and same-Area resources remain useful.
3. Default to passive rendering and require an explicit, reversible,
   session-and-Area-scoped active generation. Chosen.

## Decision

Canonical HTML previews use an Area-only origin in both modes. A newly opened
viewer receives a passive capability. Its response policy permits static
same-Area styles, images, fonts, and media, but denies scripts, workers, fetch,
forms, objects, and nested frames.

Artifact Review labels that state as **Passive preview**. The owner can open a
trust dialog that states, before activation, that active content may run scripts
and module workers, use network access, navigate within the preview, and send
Area data externally. Proxima makes no confidentiality guarantee for that Area
while active mode is enabled.

Confirmation creates an opaque generation scoped to the authenticated owner
session, one canonical Area, and one ArtifactViewer session. The mutation
requires the bearer session token, so a capability origin, ambient cookie, or
cross-site form cannot self-enable it. Every active capability and resource
request must match the current server-held generation and a still-valid owner
session.

Active mode permits scripts, dedicated module workers, outbound network
requests, and capability-bound same-Area nested frames. It does not permit
Service Workers or Shared Workers. The frame ancestor policy contains the
signed Proxima origin and, only in active mode, the same Area origin. Content
remains unable to reach Proxima routes or another Area origin.

Disabling active mode removes the generation before the viewer reloads in
passive mode. Closing the viewer or changing Areas sends the same revocation.
Stale capability cookies, clean URLs, frames, and worker script requests fail
generation validation. Reloading the iframe terminates its dedicated workers.
Server restart or an unknown prior state also defaults to passive.

## Consequences

**Positive**

- Viewing HTML no longer executes project code without informed owner consent.
- The UI describes the actual confidentiality boundary instead of implying
  containment can prevent trusted active content from exfiltrating Area data.
- Revocation is independent of browser cookie deletion and fails closed across
  stale URLs and server restarts.
- Interactive trusted artifacts keep native same-Area module worker behavior.

**Negative / accepted trade-offs**

- Active mode is intentionally temporary and must be enabled again in a new
  viewer session.
- Active content can send selected Area data externally after the owner accepts
  the warning.
- Shared Workers remain unavailable so authority cannot outlive the document
  that received it.

## Related

- Qualifies [ADR-0034](0034-distinct-tls-area-preview-origins.md) and
  [ADR-0035](0035-frame-bound-area-preview-admission.md).
- [Canonical file targets](0029-canonical-file-targets.md)
- [Capabilities](../CAPABILITIES.md)
- [Security boundaries](../security-boundaries.md)

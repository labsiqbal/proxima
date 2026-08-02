# ADR-0029: Canonical file targets preserve Area identity

- Status: Accepted (amended 2026-08-02: the reserved-name virtual-path
  compatibility clause was removed by prune #138 / decision #121 - path-only
  requests now resolve literally from the Container root; locators are
  unchanged)
- Date: 2026-07-31

## Context

A Container can expose files from its own root, one Ops Area, and zero or more
Code Areas in one merged tree. Display paths are not unique when a direct Ops file
and a Container file share a name. They are also not sufficient when an Area is
nested below the Container root or when a legacy Ops Area uses `.`.

Choosing a root from a path prefix or basename lets one physical file acquire
multiple accepted identities. That can read, edit, preview, or delete the wrong
same-name file and can bypass Area-specific behavior.

## Decision drivers

1. Every accepted file reference must have one authoritative Area identity.
2. Legacy virtual paths and explicit `ops/...` paths must remain compatible.
3. Ops-at-dot layouts must remain readable without basename-based guessing.
4. Symlink, realpath jail, missing-root, and Area-overlap checks must remain in
   force.
5. Relative HTML and Markdown resources must stay in the originating Area.

## Options considered

1. Keep path-only identity and expand prefix heuristics. Rejected because names
   and prefixes cannot prove Area ownership.
2. Accept Container and Area locators for the same physical descendant. Rejected
   because aliases make Area-specific behavior optional.
3. Use a server-constructed locator and revalidate its Area ownership on every
   resolution. Chosen.

## Decision

The public file identity is a locator containing the Container slug, an Area
kind and id, and an Area-relative path. `file_targets.py` is the shared
construction and validation boundary.

Resolution validates all active Area roots, applies the existing realpath jail,
and determines the authoritative owner of the resolved path. The most specific
containing Area owns a descendant. Ops wins the legacy same-root tie with Code.
A Container locator is valid only for a path not owned by an active Area.
Cross-Area aliases are rejected.

Merged tree traversal switches locators when it enters an Ops or Code Area.
Path-only requests remain compatibility input and are upgraded by the server to
the same canonical locator. Historical virtual Ops names retain their Ops
mapping. A physical `ops/...` prefix maps to physical Ops only when that is the
registered layout. With Ops at `.`, the prefix remains part of the Area-relative
path and is never stripped.

Raw reads, writes, mutation, session artifact deletion, Archive presence, chat
and iterate results, and ArtifactViewer use the locator. Targeted previews use
an Area-stable URL namespace so browser-relative HTML resources retain the Area.
Markdown resolves relative resources against both the document directory and
the same locator.

## Consequences

**Positive**

- Same-name Container and Ops files cannot be confused by a targeted operation.
- Entering a nested Code or Ops Area produces one identity and rejects aliases.
- Legacy callers remain compatible while responses upgrade them to canonical
  targets.
- Preview subresources retain the same jail and Area identity as their document.

**Negative / accepted trade-offs**

- File resolution validates the active Area layout before returning a path.
- Consumers must retain the locator instead of reconstructing identity from a
  display path.
- Preview URLs with a target use the Area namespace rather than the legacy
  path-only shape.

## Related

- [Architecture and data model](../reference/architecture.md)
- [Files capability](../CAPABILITIES.md#11-files--uploads-apis)
- [Prompt injection hardening](../prompt-injection-hardening.md)

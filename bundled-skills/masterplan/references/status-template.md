# STATUS.md Template

Copy everything below the `---` into the package's `STATUS.md`, pre-filled with the build order from masterplan section 18. masterplan creates it; the **executing agent** maintains it; the owner reads it any time to see progress without asking anyone.

Milestones mirror §18's tracer bullets: after the walking skeleton, each is a complete vertical slice through every layer, demoable on its own, sized to one agent context window, with dependencies as §18's blocking-edge diagram shows. A checked milestone is therefore always something the owner can be shown running.

---

# Status: ⟨project name⟩

**masterplan version:** v1.0
**Started:** ⟨YYYY-MM-DD⟩
**Last updated:** ⟨YYYY-MM-DD⟩ by ⟨agent/owner⟩

**Marker convention:**
- `[ ]` pending
- `[x]` done — only with evidence in the note (evidence rule in EXECUTE.md)
- `[!]` needs rework — set by revise mode when a change invalidated a built milestone; treat as unchecked and rebuild per its note. Never silently uncheck history; `[!]` preserves the fact that it was built once.

## Milestones

- [ ] **M1 — Walking skeleton** — ⟨the thinnest end-to-end slice, from masterplan §18⟩
  - Note: —
  - Evidence: —
- [ ] **M2 — ⟨core feature⟩ slice** — ⟨…⟩
  - Note: —
  - Evidence: —
- [ ] **M⟨n⟩ — …**
  - Note: —
  - Evidence: —
- [ ] **M⟨last⟩ — Full QA pass** — run every acceptance criterion from the masterplan end-to-end and record evidence.
  - Note: —
  - Evidence: —

## Blockers

*(none)* — when blocked, record: what, since when, what is needed to unblock, and which milestone it stops.

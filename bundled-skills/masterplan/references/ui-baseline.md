# UI Interaction Baseline — standing standard

The defaults every app with a UI already needs — and that one-shot builds silently skip because nothing forces them. These are **not features to interrogate the user about and not per-project decisions**: they are the floor. A "functionally correct" build that ships buttons with no hover/disabled/loading state, tables that go blank while loading, or forms that fail with a red border and no message is **not done** — it just looks done.

**Scope:** applies to any project with a user-facing UI (web, mobile, desktop, TUI). **Not applicable to** headless API / library / pure-CLI projects — mark it so in the masterplan and move on.

**How this is used**
- masterplan copies this file into the build package at `references/ui-baseline.md`, so the package stays self-contained.
- masterplan §6 (pages) and §15 (design) point here as the mandatory interaction standard; the masterplan only records **additions or deliberate exceptions**, never a restatement.
- EXECUTE.md's definition of done requires this baseline satisfied on every interactive surface; the final QA milestone verifies it.

**How to read it:** each item is an acceptance criterion the executing agent must be able to *demonstrate*, not a suggestion. Scale sensibly — a two-page internal tool doesn't need a design system, but it still needs its one button to show it's disabled. When a project genuinely diverges from an item, that goes in masterplan §20 (Considered and rejected) with a reason, not a silent skip.

---

## 1. Every interactive element has all its states

Buttons, links, inputs, cards, menu items, toggles — anything a user can act on — must render **visibly distinct** states, not just the resting one:

- [ ] **Hover** — visible affordance on pointer devices (cursor + a visual change).
- [ ] **Active / pressed** — feedback on the press itself, so a tap never feels dead.
- [ ] **Focus-visible** — a clear keyboard focus indicator. Never `outline: none` without an equal-or-better replacement; keyboard users must always see where they are.
- [ ] **Disabled** — visually muted, non-interactive, correct cursor, and communicates *why* when the reason isn't obvious.
- [ ] **Loading / busy** — for anything that triggers async work: the control shows progress (spinner/label change) and is **guarded against double-submit** while in flight.
- [ ] **Selected / active-route** — current nav item, active tab, chosen option is unmistakably marked.
- [ ] Touch targets are comfortably tappable (~44px min) on touch surfaces.

*Why default:* a control that looks the same before, during, and after a click reads as broken. This is the single most common thing one-shot builds miss.

## 2. Every data view has its four states

Any screen or component that loads, contains, or submits data must handle **all four**, not just the happy populated one:

- [ ] **Loading** — skeleton or spinner, never a blank flash or layout jump.
- [ ] **Empty** — a helpful zero state that says what goes here and offers the next action; never a bare blank panel that reads as an error.
- [ ] **Error** — a human message and a way to recover (retry / go back), never a raw stack trace or a silent nothing.
- [ ] **Populated** — the normal content.

*Why default:* real networks are slow and fallible; a UI that only designs the success path breaks the moment anything is slow, empty, or down.

## 3. Forms tell the truth

- [ ] **Inline validation** with a **specific message** ("Email is missing an @"), not just a red border and not only a top-of-page summary.
- [ ] **Success / confirmation feedback** after submit — the user knows it worked.
- [ ] **Submit is guarded** — disabled or spinner while in flight; no double-submit; disabled or clearly-flagged while invalid.
- [ ] **Labels are real labels**, always visible; a placeholder is not a label.
- [ ] **Input survives errors** — a failed submit never wipes what the user typed.
- [ ] **Destructive / irreversible actions confirm first** (or offer undo).

*Why default:* the form is where users hand over effort; losing it or failing silently is the fastest way to lose trust.

## 4. Actions are acknowledged — and errors are hard to miss

- [ ] Every action that succeeds or fails **out of the user's direct view** gets feedback. Nothing important happens silently.
- [ ] Slow actions show **pending state immediately** (optimistic update or spinner), so the app never feels frozen.
- [ ] **Errors are not a fleeting toast.** A message that auto-dismisses in a few seconds is fine for a *success* ("Saved"), but a failure the user needs to read and act on must land on a **persistent surface**: a modal/popup for something blocking or destructive, or a **notification region (e.g. anchored bottom / a bell/inbox area) that stays until the user dismisses it** for non-blocking failures. The rule: the more the user must know or do about it, the more persistent and prominent it is — an error must never disappear before it's read.
- [ ] Every error surface says **what failed, why (in human terms), and the way out** (retry / undo / who to contact) — never a bare "Something went wrong" with no next step.
- [ ] Errors are **not lost on navigation** — a failure raised during an action is still visible/recoverable after the view changes, not silently dropped.

## 5. Keyboard & accessibility floor

- [ ] **Fully keyboard-operable** — everything reachable and usable by Tab / Enter / Space / Escape; logical tab order; Escape closes overlays.
- [ ] **Semantic HTML first**, ARIA only to fill real gaps; interactive things are real buttons/links, not click-handlered `div`s.
- [ ] **Text contrast meets WCAG AA** (4.5:1 body, 3:1 large/UI).
- [ ] **Meaningful images have alt text**; decorative ones are hidden from assistive tech.
- [ ] **Respect `prefers-reduced-motion`** — motion has a reduced/none path.

*Why default:* keyboard and contrast aren't "accessibility extras" — they're basic operability, and cheapest to bake in from the first component.

## 6. Responsive & layout stability

- [ ] Works from **small (~360px) to large** with no horizontal scroll and no broken/overlapping layout.
- [ ] **No layout shift** as content/images/async data load in (reserve space).
- [ ] Long content, long names, and overflow are handled (wrap / truncate / scroll on purpose), not left to break the layout.

## 7. Motion is present and tasteful

- [ ] State and view changes **transition** rather than snapping — enough to feel alive, never enough to slow the user down.
- [ ] Motion is **consistent** (shared durations/easings) and **purposeful** (guides attention, shows cause/effect), not decorative jank.
- [ ] Honors reduced-motion (see §5).

*Why default:* instant, transition-less UIs feel cheap; overdone motion feels amateur. The bar is "alive, not busy." Pair with a motion vocabulary/skill where one exists.

## 8. One source of truth for style

- [ ] Colors, spacing, typography, and radii come from **defined tokens/scale**, referenced everywhere — not magic values scattered per component.
- [ ] Spacing and alignment are **consistent** across screens (a rhythm, not eyeballed per page).
- [ ] Light/dark handling is deliberate where the product implies it (respected, not half-done).

*Why default:* a single token source is what makes the whole UI read as one system instead of a patchwork, and makes every later change one edit instead of many.

---

## Verification (final QA)

Before the build is done, the executing agent walks a real interactive surface and confirms, with evidence:

1. Pick the primary form/flow — drive it by **keyboard only**, end to end.
2. Trigger its **loading, empty, error, and success** states and confirm each renders (throttle/kill the network to force them).
3. Confirm every button shows **hover, focus, disabled, and in-flight** states and can't double-submit.
4. Resize from mobile to desktop — no horizontal scroll, no layout break, no shift on load.
5. Note any deliberate exceptions in masterplan §20.

"Looks right in one screenshot" is not evidence. Driving the states is.

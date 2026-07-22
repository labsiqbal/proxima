# Research Playbook (prior-art, phase 2)

Research is the most expensive phase — in tokens, time, and attention. The discipline: **spend nothing until the pitch is locked (GATE A), spend little until the direction is confirmed, and only then go deep.** A wrong guess at the scan stage costs a paragraph; a wrong guess at the deep-dive stage costs the whole afternoon.

## Stage 1 — Quick scan (cheap)

Find the **3–5 existing products/projects closest to the pitch**. Search broadly: commercial products, open-source projects, and "X alternative" / "open source X" queries. For each candidate, write exactly one paragraph:

- What it is and who uses it.
- Its core flow in one sentence.
- Open source or proprietary (with license if visible at a glance).

Do not go deeper yet. No feature matrices, no repo reading.

## Stage 2 — Direction check (with the user)

Present the candidates: *"Your idea resembles X and Y. Their flow works like ⟨summary⟩ — is that what you have in mind?"*

Classify every divergence between the user's mental model and the prior art:

- **Deliberate differentiation** — the user knows the pattern and wants to differ. Record it; it feeds "the one difference".
- **Unknown pattern** — the user simply hadn't seen how the proven products do it. Offer the proven pattern with the reason it won; let them choose knowingly.

Only proceed when the user confirms which candidates are actually the relevant comparison set.

## Stage 3 — Deep-dive (only after confirmation)

For the confirmed candidates: user flows and page structures (what screens exist, in what order), tech stacks where discoverable, open-source repos (activity, quality, license), pricing models. This material becomes masterplan §2 (differentiation table), §5–6 (flows and pages worth absorbing), and §14 (reference map). Save raw notes to the package's `references/` folder.

**Also capture the category's UX conventions — the layer above the universal baseline.** `references/ui-baseline.md` is the floor every app shares; the deep-dive is where you learn what *this industry's* users already expect on top of it, so the build feels native to its category rather than generically correct. For the confirmed candidates note:

- **Table-stakes patterns** every serious product in the category has (e.g. fintech → transaction confirmations, audit trail, clear balances; SaaS dashboard → filters, saved views, bulk actions; consumer/social → onboarding coach, rich empty states; commerce → cart/checkout conventions, trust signals). If the best products all do it, absent it reads as broken.
- **Expected primary flow** — the order and shape users of this category are trained to expect (deviating is allowed, but must be a deliberate §20 choice, not an accident).
- **Density & tone conventions** — data-dense/pro vs airy/consumer; where the category sits sets the design register.

This feeds **masterplan §15 (design direction)** as concrete, sourced convention — "products X and Y in this space all do ⟨pattern⟩" — not opinion, and complements the universal `ui-baseline.md`. Record it in the `references/` notes so §15 and §6 can cite it.

## Stage 4 — The absorption map

The standing assumption is **we are building** — research feeds the build, it does not gate it. Decide honestly how heavily to absorb what exists (every rung is a BUILD outcome):

```
Is there a compatibly-licensed base already close to the pitch?
├─ YES → FORK & ADAPT.
│        Start from it, modify heavily, make it yours. masterplan §14 anchors to
│        the base repo; the build order starts from its scaffold, not from zero.
└─ NO → Are there proven parts to compose from?
    ├─ YES, spread across several sources → ASSEMBLE (CHIMERA).
    │        Compose from proven components/patterns, each anchored to a reference.
    ├─ YES, whole similar products exist but with a clear gap → DIFFERENTIATE.
    │        masterplan §2 must state THE one difference in a single sentence —
    │        and borrow the proven patterns for everything that is not the difference.
    └─ NO → FRESH.
             Rare. Components still get anchored to references where possible —
             even a novel product is assembled from proven parts.
```

Two absorption currencies, one rule: **patterns and ideas** (flows, UX, architecture) are free to absorb from anything, including proprietary products. **Actual code** (the fork/copy path) is gated by the license table below.

**The one brake — false premise.** If the scan or the phase-1 target-grounding shows the request rests on a factually wrong premise — the thing already exists *in the user's own target/codebase*, or the ask is built on a mistaken belief — stop and write `VERDICT.md` per `references/verdict-template.md`. That is the only no-build path, and it is rare. "A similar product exists somewhere" is never it; that is what the ladder above absorbs.

Deliver the absorption decision before interrogation (phase 3): it changes which questions matter.

## License table

Checked per reference **before** it enters the map. When unsure, learn the pattern, don't copy the code.

| License family | Examples | Rule |
|---|---|---|
| Permissive | MIT, Apache-2.0, BSD | Adapt code freely; keep attribution/notice as required. |
| Weak copyleft | MPL, LGPL | Adapt within file/library boundaries; modifications to the covered parts stay open. |
| Strong copyleft | GPL, AGPL | Do **not** absorb code into a closed-source product. Pattern-learning only. AGPL binds even network-served use. |
| Proprietary / no license | closed products, unlicensed repos | Pattern only. An unlicensed public repo is NOT free to copy. |

Cross-check against the product's fate (masterplan §21): an open-source (compatible-licensed) product may absorb more; a commercial closed product is strictest.

## Reference map format

Feeds masterplan §14 directly:

| Component | Reference (repo/product) | License | Absorb |
|---|---|---|---|
| ⟨component⟩ | ⟨URL or product name⟩ | ⟨license⟩ | Code — adapt directly / Pattern only |

**"Absorb" is a decision, not a note** — the executing agent will act on it literally: "Code" means open the repo and adapt; "Pattern" means study the approach and re-implement.

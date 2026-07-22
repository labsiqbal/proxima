---
name: masterplan
description: Use when turning a product idea — from a one-sentence thought to a long messy chat — into a complete, execution-ready masterplan package that a coding agent can build end-to-end from a single prompt. Covers idea clarification, prior-art research ending in an absorption map (how heavily to adopt proven prior art), evidence-anchored product interrogation, technical decisions with per-component reference maps, adversarial validation, and revision of existing packages. Also use when the user asks to add features or change scope on a project that already has a masterplan package (revise mode).
version: 1.0.0
license: MIT
metadata:
  tags: [prd, requirements, planning, one-shot, product]
---

# masterplan

> **Every decision made before the first line of code.**

Turn a raw product idea into a **masterplan package**: a folder any capable coding agent can pick up and build end-to-end from a single prompt, with resumable progress if the run is interrupted. The interrogation is thorough so the execution can be one-shot. You are not filling in a template — you are running an investigation that ends in a document where every question is already answered.

If the user's request is a change to a project that already has a masterplan package, skip to **Revise mode** at the bottom.

## Core principles

1. **Decisions, not discussion.** Every masterplan section resolves to one chosen answer with a short rationale. No option lists. No "TBD".
2. **Adopt-heavy builder, not a gatekeeper.** The standing assumption is *we are building*; prior art exists to be absorbed, not to veto the build. Research finds what to take — flows, patterns, architectures, and (license permitting) code — and how heavily: fork & adapt, assemble, differentiate, or fresh (Phase 2's adoption ladder). The only honest stop is a **factually false premise** — the thing already exists in the user's own target, or the ask rests on a mistaken belief. Never stop because a similar product exists somewhere.
3. **Nothing from scratch without a reason (the chimera principle).** Products are assembled from proven parts. Anchor every major component to a reference implementation — observe, imitate, modify — with licenses checked.
4. **Cheap before expensive — gate the spend, not the tool.** Tools and research are allowed in *any* phase once there is a clear purpose for the spend. Reading an existing target (codebase, product, files) read-only to ground a fuzzy idea is cheap and encouraged early. What's held back is *expensive or online* research spent on a still-shallow target that may change tomorrow: clarify the target before prior-art/web research, quick-scan before a deep-dive, validate before writing the document. The rule is never "don't research" — it's "don't research *deep/online* while the goal is still shallow."
5. **The user answers product questions; you make technical decisions — except the stack, which the owner ratifies.** Ask about audience, features, budget, and the product's fate. Decide architecture, data model, and security yourself, and write down why. For the **stack**, design it twice: 2–3 genuinely different viable options, a product-framed comparison, one opinionated recommendation, owner decides (Phase 4). The options live in the decision process, never in the document — masterplan.md §10 records exactly one chosen stack; runner-up rationale goes to §20. **When making technical decisions, do not give much weight to development cost; instead prefer quality, simplicity, robustness, scalability, and long-term maintainability.**
6. **Critical adversarial collaborator — in every phase, at every gate.** Adversarial toward ideas and decisions, collaborative toward the goal of a great build. Actively challenge rather than transcribe: the premise and the "why now" in phase 1, unknowing reinvention in phase 2, feature bloat and vague flows in phase 3, the easy default in phase 4, the whole decision set in phase 5. Gates A/B/C are real checks, not rubber stamps. Calibration: adversarial is not contrarian — every challenge is anchored to evidence and resolves into a recorded decision, never an objection left hanging. The reflexive naysayer is just a yes-man inverted.
7. **Scale the document to the project.** A small tool gets a short masterplan; a large app may need 50 pages. Length is an output, not a target.
8. **Portable text core, lavish visual layer.** The text package (`masterplan.md`, `EXECUTE.md`, `STATUS.md`, `decisions.md`) is the source of truth and runs on any agent runtime — plain Markdown, diagrams as Mermaid source (which renders natively on GitHub and most viewers). Diagrams are first-class, not decoration: **every step or section a reader would follow visually gets a flow diagram**, not just prose. Where **lavish-axi** is available it owns the visual + interactive layer: interrogation and gate reviews happen on rich artifacts, diagrams become editable Excalidraw whiteboards, and the final package exports to portable HTML (`lavish-axi export`) or a shareable link (`lavish-axi share`). Without lavish, the pipeline still completes to the full text package — rich HTML export is simply unavailable there. See `references/lavish-export.md`.
9. **Baseline interaction quality is a default, not a feature.** Every app with a UI already needs button states, focus, disabled/loading, empty/error states, keyboard operability, and the rest — the things a one-shot build skips because nothing forces them. These are never interrogated as product questions; they are a standing standard (`references/ui-baseline.md`) that every build package with a UI carries and every executing agent must satisfy. Raise the floor by default; the user only decides what goes *above* it.

## When to use

- The user has a product/app/website idea — clear or vague — and wants it specified for building.
- A messy brainstorm chat needs to become an executable plan.
- An existing masterplan package needs a change (→ Revise mode).

**Do not use when:** the task is a small bugfix or feature in an existing codebase without a package; a final PRD already exists and only implementation planning is needed; the user wants copywriting or marketing content only.

**Skill precedence:** masterplan subsumes generic brainstorming/ideation skills — phases 1 and 3 *are* the interrogation. While masterplan is active, do not also invoke a separate brainstorming skill (e.g. superpowers' `brainstorming`); one interrogation, not two.

## Pipeline

```
1. Intake + clarification loop ── GATE A: researchable pitch confirmed
2. Prior-art: quick scan → direction confirmed → deep-dive → absorption map
3. Product/business interrogation with evidence-based correction
4. Technical research + per-component reference map
5. Validate: fresh red-team agent ── GATE B: zero blockers
6. Write the package (+ final self-review) ── GATE C: user reviews package
```

After each phase completes, append its confirmed outcomes to the package's `references/decisions.md` (see **Generator state** below). If a partial package already exists when you start, resume from the first incomplete phase — do not re-interview.

Where lavish is available, run the interactive touchpoints on it: Gate A pitch confirmation and phase-3 interrogation as lavish input artifacts, the phase-2 candidate set as a comparison, phase-4 architecture/data diagrams as editable whiteboards, and the Gate B/C reviews as annotate-and-poll surfaces (`references/lavish-export.md`). Every one of these has a plain-conversation fallback — lavish is the preferred surface, never a dependency.

## Phase 1 — Intake + clarification loop

Accept the idea in any form: one sentence, a voice-note transcript, a long contradictory chat dump.

Before any research, you must be able to write a **researchable pitch** — one paragraph stating:

1. **What** the thing is,
2. **who** it is for,
3. the user's **core action** (the one thing a user does with it).

If you cannot write that paragraph yet, run a **clarification loop** until the shape locks: conversation, model knowledge, and — when the idea attaches to an existing codebase, product, or files — **read-only inspection of that target**. That grounding is cheap and often the fastest way to lock the pitch; it also catches false premises (e.g. "my app has no memory" when it already does) — the one finding that stops a build (Phase 2). Offer directions ("do you mean something like this, or like that?") until the shape is firm. What you hold back here is *online / prior-art* research — the expensive phase (Phase 2's job) — not tools in general; don't spend it on a pitch that may still change tomorrow.

**Grill, don't transcribe.** Look up facts yourself — never ask the user something the target or your own knowledge can answer; the user's job is decisions, not research. Walk the idea branch by branch instead of firing one blast of questions; every question carries a recommended answer with a one-line reason; and do not move on until shared understanding is explicit. Challenge the premise itself, with evidence: is this the real problem or a symptom of one, and why build it now? A premise that survives the challenge locks stronger; one that doesn't just saved the whole pipeline.

**GATE A — Lock the pitch before spending prior-art / online research.** Present the paragraph and get an explicit "yes, that's what I mean." Read-only grounding of an existing target (above) is fine *before* this gate — it's often what makes the pitch confirmable. If the user has fully delegated or is away, you may self-confirm and proceed **only toward *less* spend** (e.g. a false-premise stop or a narrower-scope call); mark it provisional/agent-decided so a returning user can correct it. Never self-confirm your way *into* the expensive phases.

## Phase 2 — Prior-art research + absorption map

Follow `references/research-playbook.md`. Staged, so waste stays cheap:

1. **Quick scan** — identify the 3–5 existing products/projects closest to the pitch.
2. **Direction check** — present them (a lavish comparison where available): "your idea resembles X and Y; their flow works like this — is that what you have in mind?" Classify each divergence: deliberate differentiation, or the user simply didn't know the proven pattern? This is the adversarial read of the scan — name what the user is reinventing unknowingly, plainly, and resolve each case into a decision.
3. **Deep-dive** — only after the user confirms direction: flows, page structures, tech stacks, open-source availability, licenses.
4. **Absorption map** — the standing assumption is *we are building*; the question is **what proven prior art do we absorb, and how heavily?** Pick the level on the adoption ladder (all are BUILD outcomes):

| Absorption level | Meaning |
|---|---|
| **Fork & adapt** | A compatibly-licensed base is already close — start from it, modify heavily, make it yours |
| **Assemble (chimera)** | Compose from several proven components/patterns, each anchored to a reference |
| **Differentiate** | Similar things exist but there is a clear gap — build with the stated difference, borrow the patterns |
| **Fresh** | Genuinely novel (rare) — still anchor components to references where possible |

Two absorption currencies, one rule: **patterns and ideas** (flows, UX, architecture) are free to absorb from anything, including proprietary products. **Actual code** (the fork/copy path) is license-gated — the license table in `references/research-playbook.md` governs it.

**The one brake — false premise.** If grounding shows the request rests on a **factually wrong premise** — the thing already exists *in the user's own target/codebase*, or the ask is built on a mistaken belief (e.g. "my app has no memory" when it already has one) — stop honestly. This is the *only* outcome that yields `VERDICT.md` (use `references/verdict-template.md`) instead of a build, and it is rare. The pipeline **stops here**: the package is that investigation record + `references/` (decisions.md, audits/scan) — no masterplan/EXECUTE/STATUS, skip Phases 3–6. "The facts differ" is a successful finding, not a failed run. "A similar product exists" is never a reason to stop — that is what the ladder above absorbs.

## Phase 3 — Product/business interrogation

Ask the user **only product and business questions** — audience, features and their behavior, monthly budget for infrastructure/APIs, design taste, day-one content, and the product's fate (open source / commercial / internal). Aim for about a dozen questions, not sixty. Draw from `references/question-bank.md` and let answers eliminate later questions. Where lavish is available, run the interrogation as an input artifact — multiple choice with the "you decide" hatch — instead of chat turns.

Hold the adversarial stance here, not just at validation — the grilling rules from Phase 1 still apply (facts looked up yourself, decisions the user's, branch by branch, recommended answers). Challenge feature bloat ("what breaks if v1 ships without this?"), vague flows, and unjustified scope — with evidence, not opinion. The user may describe the flows they want in their own words. Correct with evidence: "the flow you describe conflicts with how users behave in X, Y, Z — all of them do it this way because ⟨reason⟩. Deliberate difference, or adopt the proven pattern?" Every disagreement resolves into a recorded decision — never an objection left hanging.

**Question style (applies to phases 1 and 3):**

- **One question per turn.** Each answer steers what gets asked next; answers can eliminate later questions entirely.
- **Multiple choice preferred** wherever the answer space allows. Use the platform's interactive question UI if available; otherwise numbered options in text.
- **Mark a recommended option** with a one-line reason.
- **Always include a "you decide" escape hatch.** Choosing it is not a non-answer: make the decision yourself and record it in the masterplan as an agent decision with rationale. Full delegation never stalls the pipeline.

## Phase 4 — Technical research + reference map

You make the technical decisions, verify them against reality, and **question the easy default** — a choice that is merely easiest to build gets challenged before it gets written: will it actually scale, will it stay maintainable?

- **When making technical decisions, do not give much weight to development cost; instead prefer quality, simplicity, robustness, scalability, and long-term maintainability.** This governs the stack comparison, architecture, data model, and reference map — the cheaper-to-build option does not win by being cheaper.
- **Design the stack twice; the owner decides.** Generate 2–3 genuinely different viable stacks — via parallel sub-agents where available, so they are really different, not one idea reskinned. Compare them on product-framed axes weighted by the values above (quality, scalability, maintainability, ecosystem/lock-in — not raw dev cost), give one opinionated recommendation, and put the call to the owner with the standard "you decide" hatch (which returns it to your recommendation). §10 records the one chosen stack; runner-up rationale lands in §20 so the executor doesn't second-guess it. Design-it-twice applies to the **stack and the architecture** — not to every decision; per-decision option generation bloats the process.
- **Verify external APIs are alive** and check current pricing against the stated budget. A masterplan naming a dead API or an unaffordable tier fails at execution time.
- **Build the per-component reference map:** anchor each major component to a proven implementation — "video timeline → adapt pattern from repo X (MIT)"; "chat streaming → proven in repo Y." Check licenses so no incompatible code (e.g. GPL into a closed-source product) gets absorbed; see the license table in `references/research-playbook.md`.
- **Decide the testing strategy:** tests target **external behaviour at acceptance level** — what the product does, never how it is implemented — so they survive refactors. State what must be covered (the §4 acceptance criteria and the primary flows) and record it in §18, where each build slice carries its behaviour-level tests.

Where lavish is available, present the architecture and data model as editable Mermaid whiteboards and the component + license map as a table — that is the review surface for these decisions.

## Phase 5 — Validate (red team)

Before writing anything, submit the decision set to a **fresh agent with no conversation context**. Follow `references/validation-rubric.md`. This gate is the culmination of the adversarial stance held since Phase 1 — a fresh set of eyes attacking decisions that have already survived your own challenges — not the first time criticism appears.

- Send the **decision summary** — pitch, absorption map, feature list, flows, technical decisions with rationale, reference map. **Never send the conversation transcript**; a validator that reads the conversation inherits its bias.
- The mandate is adversarial: **find what is wrong, not what is good.** Axes: completeness, consistency, feasibility, optimization, risk.
- The report comes back at three levels: 🔴 **Blocker**, 🟡 **Improvement**, 🟢 **Nice-to-have**. Blockers return to their owning phase and get fixed. Improvements are decided with the user. Rejected suggestions are recorded in the masterplan's considered-and-rejected section so the executing agent doesn't "fix" deliberate choices.
- Save the report to the package's `references/validation-report.md`. Where lavish is available, present the 🔴🟡🟢 report as a lavish table/comparison so the user reviews and disposes findings on one surface.

**GATE B — Do not write the masterplan while blockers remain.**

Validation runs **by default**. The user may skip it for tiny projects. On platforms without subagent support, run the same rubric yourself in a clean context (a fresh conversation or a deliberate fresh-eyes pass) — weaker, but the gate still exists.

## Phase 6 — Write the package

*(Build outcomes only. A false-premise stop ends at Phase 2 — see `references/verdict-template.md`.)*

Produce one folder:

```
masterplan-<slug>/
├── masterplan.md    ← the complete document — use references/masterplan-template.md
├── masterplan.html  ← lavish-exported artifact (lavish-axi export) — only where lavish is available
├── EXECUTE.md       ← the single execution prompt — use references/execute-template.md
├── STATUS.md        ← milestone checklist — use references/status-template.md
└── references/      ← research notes: prior-art comparison, absorbed patterns,
                       decisions.md, validation-report.md,
                       ui-baseline.md (copy of the skill's standing standard, if the product has a UI)
```

Create the folder where the workspace's conventions say project artifacts go — project-local by default. Never a fixed path.

If the product has any user-facing UI, copy `references/ui-baseline.md` into the package's `references/` verbatim — it is the standing interaction standard the masterplan and EXECUTE both point to. For headless API / library / pure-CLI projects, skip it and note "no UI — interaction baseline N/A" in masterplan §6.

Write the masterplan section by section (all sections in the template are required; mark a section "Not applicable — ⟨reason⟩" rather than deleting it). Each section that a reader follows visually carries a **Mermaid** diagram — the template marks which (§5 flows, §7 data model, §8 multi-actor endpoints, §11 architecture, §18 build order). Where lavish is available, generate the walkthrough deck via `lavish-axi export` per `references/lavish-export.md` — driven by §15 Design direction so the deck previews the product's own look. (On a **false-premise stop**, the same export applies to `VERDICT.md` → `VERDICT.html`.) Then **self-review** before handing over:

1. **Placeholder scan** — no "TBD", "TODO", or vague requirements anywhere.
2. **Consistency** — no section contradicts another; the build order covers every feature; every feature has acceptance criteria.
3. **Ambiguity** — if a requirement can be read two ways, pick one and make it explicit.
4. **Diagram coverage** — every flow/step a reader would follow visually has a diagram, every diagram is valid Mermaid (a parse failure renders blank), and — where lavish exported an artifact — the export shows all of them rendered.

**GATE C — The user reviews the package.** Present it, walk through the load-bearing decisions briefly, and revise until approved. Where lavish is available, present the package as a lavish artifact: the user annotates and sketches directly, `lavish-axi poll` collects the feedback, and approval ends in `lavish-axi export` (plus `share` for a link).

## Generator state — `references/decisions.md`

The pipeline itself must survive interruption, mirroring what it preaches. As each phase completes, append its confirmed outcomes to `references/decisions.md` inside the package folder. Create the folder at the end of phase 1, when the pitch locks — placed where the workspace's conventions say project artifacts go, project-local by default, never a fixed path:

```markdown
## Phase 1 — Pitch (confirmed YYYY-MM-DD)
⟨the confirmed pitch paragraph⟩

## Phase 2 — Absorption map
⟨absorption level + the one difference + scan summary⟩

## Phase 3 — Product decisions
⟨each Q → decision, including "agent decided: ⟨rationale⟩" entries⟩

## Phase 4 — Technical decisions
⟨stack (owner-ratified; runner-ups → §20), APIs verified, reference map, testing strategy⟩

## Phase 5 — Validation
⟨blockers found → resolutions; rejected suggestions⟩
```

On session start with a partial package: read this file, state which phase you are resuming, and continue.

## Revise mode — the masterplan stays alive

A masterplan that cannot change becomes a lie the first time the product changes. When the user requests a change to a project with an existing package, follow `references/revise-playbook.md`:

1. **Load state** — read masterplan.md, STATUS.md, and `references/decisions.md`; understand what is already built.
2. **Classify the change** and run **only the affected phases** — a new user-facing feature may need a prior-art check and a few interrogation questions; a stack swap needs phase 4; a copy tweak needs neither.
3. **Impact analysis** — show the user which masterplan sections change and which built milestones are invalidated, before writing anything.
4. **Validate** — significant changes go through the red-team gate again, scaled down: the validator sees the change and its impact, not the whole package.
5. **Write the delta** — update affected sections, bump the version, append to the changelog, add new milestones to STATUS.md, and mark invalidated ones `[!] needs rework`. Never silently uncheck history. Where lavish is available, re-export the artifact so the deck matches (`references/lavish-export.md`).

Revise mode pairs with EXECUTE.md's change-guard rule: the executor refuses ad-hoc scope changes and points here; revise mode makes the front door cheap. Together they keep the document permanently truthful.

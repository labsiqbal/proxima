# Question Bank

A **menu, not a script**. Phase 1 uses only what the idea dump leaves unclear; aim for ~12 questions in the phase-3 interrogation. Let every answer eliminate later questions. One question per turn. Multiple choice wherever the answer space allows — mark a recommended option with a one-line reason, and **every question ends with the escape hatch**:

> **You decide** — make the call and record your rationale in the masterplan.

Choosing it is a real answer: decide yourself, write the decision into the masterplan as an agent decision with rationale, and move on.

Rephrase questions naturally in context — these are patterns, not scripts to read verbatim. Questions are challenges, not a survey: where evidence (phase-2 findings, the target itself) contradicts an answer, say so plainly and resolve it into a recorded decision. Never ask the user for a fact you can look up yourself — the user's job is decisions.

## Phase 1 — Locking the pitch

Use only what the idea dump leaves unclear; skip anything already answered.

1. **What is it, closest shape?** — "If you had to place this next to something that exists: is it closest to ⟨A⟩, ⟨B⟩, or ⟨C⟩?" (offer 2–3 concrete analogies from model knowledge — no research yet)
2. **Who is it for?** — "Who opens this on day one: you personally / a specific team / paying strangers?"
3. **Core action?** — "The one thing a user does with it — what is it? ⟨guess A⟩ / ⟨guess B⟩ / something else?"
4. **Success in one sentence?** — "Six months in, what's true if this worked?"
5. **Scope shape** — "Is this a single focused tool, or a platform with several parts?"
6. **Pitch confirmation (GATE A, mandatory)** — present the pitch paragraph: "⟨what / for whom / core action⟩ — is that exactly what you mean?" yes → research / no → keep clarifying.

## Phase 3 — Interrogation

### Audience & problem
7. **Concrete first user** — "Describe the very first real user. Recommended: start with the narrowest persona that would pay/use — narrow beats broad for a v1."
8. **Today's workaround** — "How do these users solve this today: manually / a competitor (⟨from phase 2⟩) / they don't?" (their current tool defines your migration story)

### Features & flows
9. **Core feature confirmation (challenge the bloat)** — from everything so far: "The core is ⟨X⟩; ⟨Y⟩ and ⟨Z⟩ are secondary — nothing breaks if v1 ships without them. Right, or is the weight elsewhere?" (anything the user promotes to core must name what breaks without it)
10. **Flow check (evidence-based correction)** — when the user's described flow conflicts with phase 2 findings: "You describe ⟨flow⟩; X, Y, Z all do ⟨other flow⟩ because ⟨reason⟩. Deliberate difference / adopt the proven pattern?"
11. **Launch cut** — "Anything you're explicitly NOT building in v1? Recommended: name at least two — non-goals protect the build from drift." (feeds masterplan §19)

### Business
12. **Monthly budget** — "Budget per month for hosting + APIs: ~0 (free tiers) / small (~tens) / real (~hundreds+)? Recommended: name a hard number — every technical decision downstream must fit inside it."
13. **Revenue model** — "Free / one-time purchase / subscription / internal (no revenue)?" *(skip if fate = internal)*

### Fate
14. **Product fate** — "Open source / commercial product / internal tool? This decides license, docs, and hardening level." (feeds masterplan §21; "internal, just me" eliminates pricing/onboarding/marketing questions)

### Design taste
15. **Look references** — "Name 2–3 products whose look this should live up to." (feeds masterplan §15)
16. **Register & mood** — "Should it feel like a brand site (expressive, loud) or a product tool (calm, workhorse)? Any mood words?" *(skip if the product is API/CLI-only)*

### Day-one content
17. **Seed content** — "On day one, what's inside: AI-generated seed content / content you'll provide / imported from ⟨existing source⟩? Recommended: never ship empty — an empty product looks broken." (feeds masterplan §16)

## Phase 4 — The stack decision (owner ratifies)

18. **Stack ratification** — after the design-it-twice comparison (2–3 genuinely different stacks, compared on quality / scalability / maintainability / ecosystem, not dev cost): "Stack ⟨A — one-line character⟩ vs ⟨B — one-line character⟩⟨ vs C⟩: they trade off like ⟨one-line summary per axis⟩. Recommended: ⟨A⟩ because ⟨reason⟩." The owner picks; "you decide" returns the call to the recommendation. masterplan §10 records the one chosen stack; runner-up rationale goes to §20. (This is the only technical decision put to the owner — architecture, data model, and security stay agent-owned.)

## Adaptive rules

- **Fate = "internal, just me"** → skip 13, 15–16 become optional, marketing-ish questions never asked; security scales down (masterplan §12 notes it).
- **False-premise stop (phase 2)** → the pipeline stopped before interrogation; no phase-3 questions exist to ask. If the user corrects course to a different, real premise, restart at phase 1 with the corrected pitch.
- **Budget = ~0** → phase 4 constrains to free tiers; flag any feature that can't survive that before continuing.
- **User picked "you decide" ≥3 times in a row** → stop asking; decide the rest yourself, list all agent decisions at GATE C for one batch review.
- **Answer already present in the idea dump** → never ask it again; record it as confirmed.

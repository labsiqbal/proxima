# Revise Playbook (revise mode)

A masterplan that cannot change becomes a lie the first time the product changes — and a stale masterplan is worse than none, because the executing agent trusts it. Revise mode is how every change enters an existing package: through the front door, cheaply.

Its counterpart lives in EXECUTE.md — the change-guard rule: *"If the owner requests a scope change mid-build, do not improvise. The masterplan is the single source of truth; run the change through masterplan revise mode first."* The executor closes the back door; this playbook makes the front door cheap. Together they keep the document permanently truthful.

## Step 1 — Load state

Read `masterplan.md`, `STATUS.md`, and `references/decisions.md`. Summarize back to the user in two lines: what is built (checked milestones), what is pending, and the current masterplan version. Never revise blind.

## Step 2 — Classify the change, rerun only affected phases

| Change type | Phases to rerun | Typical questions |
|---|---|---|
| New user-facing feature | Phase 2 slice (prior-art for that feature) + 1–3 phase-3 questions | "X and Y do this feature like ⟨pattern⟩ — same?" |
| Changed/dropped feature | 1–2 phase-3 questions (what breaks for whom?) | Which flows and pages does it touch? |
| Stack / infra / integration swap | Phase 4 slice (verify the new choice: alive, priced, licensed) | none to user unless budget changes |
| Budget or fate change | Phase 4 re-check (do current choices still fit?) | confirm the new number/fate |
| Copy / content / design-direction tweak | none — straight to step 5 | none |

Never rerun the full pipeline for a delta. Never interview from scratch — `decisions.md` already holds the answers that haven't changed.

## Step 3 — Impact analysis (before writing anything)

Produce and show the user:

```
CHANGE: ⟨one sentence⟩
masterplan sections affected: §⟨n⟩ ⟨name⟩ — ⟨what changes⟩ …
Milestones invalidated: M⟨n⟩ ⟨name⟩ — ⟨why the built work no longer matches⟩ …
New milestones: M⟨n+1⟩ ⟨name⟩ …
Version bump: v⟨x.y⟩ → v⟨x'.y'⟩ (minor = additive / major = changes built behavior)
```

The user approves the impact before any file changes. If the impact list is empty, say so — some requests turn out to be no-ops against the masterplan.

## Step 4 — Validate (when significant)

Trigger the red-team gate again when the change touches **the data model, security, external APIs, or more than two masterplan sections**. Use the scaled-down variant in `validation-rubric.md`: the validator sees the change + impact analysis, not the whole package. Below that threshold, skip — validation on a copy tweak is waste.

## Step 5 — Write the delta

In one pass:

1. **Update the affected masterplan sections** — rewrite them fully; no "see changelog" stubs inside sections.
2. **Bump the version** (masterplan §22): minor for additive changes, major when already-built behavior changes.
3. **Append the changelog entry**: `- **v⟨x.y⟩ — YYYY-MM-DD — ⟨one-line summary⟩**`
4. **Update STATUS.md**: append new milestones; flag invalidated ones `[!] needs rework` with a note pointing at the changelog entry. **Never silently uncheck history** — `[!]` preserves the fact that it was built once and needs revisiting.
5. **Append to `references/decisions.md`**: a `## Revision v⟨x.y⟩` block with the new decisions and any rejected alternatives (which also land in masterplan §20).
6. **Re-export the artifact** — `masterplan.md` is the source of truth; a stale HTML deck is the same lie a stale masterplan is. Where lavish is available, regenerate via `lavish-axi export` per `references/lavish-export.md` so the walkthrough matches the new version. (Without lavish there is no deck to refresh — the Mermaid sources in the Markdown are already current.)

Then hand back: the executing agent resumes with EXECUTE.md as usual — the resume rule naturally picks up `[!]` items and new milestones.

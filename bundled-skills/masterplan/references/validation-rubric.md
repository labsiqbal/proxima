# Validation Rubric (red team)

Run before writing the masterplan (phase 5). A **fresh agent with no conversation context** reviews the decision set. Send the decision summary below — **never the conversation transcript**; a validator that reads the conversation inherits its bias and its sunk-cost affection for the decisions.

## Validator prompt template

Fill the ⟨blanks⟩ and dispatch:

> You are reviewing the decision set for a product about to be specified for one-shot, end-to-end build by a coding agent. **Your job is to find what is wrong, not what is good. You get no credit for approval — only for defects found.** Do not restate the plan; do not praise it.
>
> DECISION SET:
> - Pitch: ⟨confirmed pitch paragraph⟩
> - Absorption map: ⟨absorption level (fork & adapt / assemble / differentiate / fresh) + the one difference⟩
> - Features & acceptance criteria: ⟨list⟩
> - User flows: ⟨summary or diagrams⟩
> - Business: ⟨audience, budget/month, revenue model, fate⟩
> - Technical decisions: ⟨stack + rationale, data model summary, integrations with prices, deploy target⟩
> - Reference map: ⟨component → reference → license⟩
>
> Review against the five axes below. Report every finding in the exact format given. If you find nothing on an axis, say "clear" — do not invent findings to look thorough, and do not soften real ones to be polite.

## The five axes

1. **Completeness** — what is missing that one-shot execution will crash into? Check at minimum: auth, payments, email/notifications, backups, legal/privacy (user data?), error/empty/loading states, mobile behavior, admin access, day-one content. For a product with a UI, also confirm nothing in the plan *contradicts* the interaction baseline (`references/ui-baseline.md`) — e.g. a flow that has no error path, a screen with no empty state, a destructive action with no confirmation — and that the industry UX conventions named in §15 aren't quietly dropped. The baseline is assumed present; flag where a decision would break it.
2. **Consistency** — do any decisions contradict each other? (features that don't fit the budget; a flow referencing a page that doesn't exist; a stack choice that conflicts with the deploy target; acceptance criteria contradicting the data model)
3. **Feasibility** — is it real? Do the named external APIs exist, at the assumed tier and price? Is the stack proven for this workload? Can the stated budget actually run this?
4. **Optimization** — is there a meaningfully simpler or cheaper path to the same outcome? (a service instead of a subsystem; one database instead of two; an existing library instead of a custom component)
5. **Risk** — what is most likely to break a one-shot build midway? (the hardest integration, the vaguest feature, the credential that won't be available, the reference repo that doesn't actually match)

## Report format

One line per finding, grouped by severity:

```
🔴 BLOCKER — ⟨axis⟩: ⟨what is wrong⟩. Why: ⟨consequence at execution time⟩. Fix: ⟨suggested change⟩.
🟡 IMPROVEMENT — ⟨axis⟩: ⟨…⟩. Why: ⟨…⟩. Fix: ⟨…⟩.
🟢 NICE-TO-HAVE — ⟨axis⟩: ⟨…⟩. Fix: ⟨…⟩.
```

**Severity test:** would this stop or corrupt a one-shot execution (🔴), degrade the result or cost real money/time (🟡), or merely polish it (🟢)?

## Disposition (done by the main agent, after the report)

- **🔴 Blockers** → return to the owning phase (missing aspect → phase 3 or 4; broken feasibility → phase 4; contradiction → wherever it was decided), fix, and update `decisions.md`. **GATE B: the masterplan may not be written while any blocker is open.**
- **🟡 Improvements** → decide with the user (or apply the "you decide" rule).
- **Everything rejected** — any level — is recorded in masterplan section 20 (Considered and rejected) with the reason, so the executing agent doesn't "fix" a deliberate choice.
- Save the full report to the package's `references/validation-report.md`.

## Scaled-down variant (revise mode)

For a revision, the validator receives only: the change request, the impact analysis (affected sections, invalidated milestones), and the decisions the change touches. Same axes, same format. Trigger it when the change touches the data model, security, external APIs, or more than two masterplan sections; skip it for smaller deltas.

## Fallback: no subagent support

If the platform cannot spawn an independent agent, run the rubric yourself as a deliberate fresh-eyes pass in a clean context (a new conversation if possible): re-read only the decision summary — not your reasoning that produced it — and attack it with the five axes as if someone else wrote it. Weaker than a true fresh agent, but the gate still exists and the format still applies.

# VERDICT template — the false-premise stop

Use this **only** on the pipeline's one brake: research or target-grounding proved the request rests on a **factually wrong premise** — the thing already exists *in the user's own target/codebase*, or the ask is built on a mistaken belief (e.g. "my app has no memory" when it already has one). This is a **rare edge outcome**, not one of the standard results: everything else builds at some absorption level (`research-playbook.md` stage 4), and "a similar product exists somewhere" is never a reason to land here. The package is a *reality record*, not a spec to execute: no masterplan/EXECUTE/STATUS. Save as `VERDICT.md` at the package root, alongside `references/`.

Scale it to the finding — a false-premise stop can be one screen. Keep every claim grounded (cite the file/source that proves it, in `references/`).

---

# ⟨Idea name⟩ — VERDICT: FALSE PREMISE, NO BUILD

**Date:** YYYY-MM-DD · **Status:** CLOSED, no masterplan. This folder is an *investigation record*, not a spec to execute.

## The question
⟨What the user wanted, and the belief/premise behind it — in one or two lines.⟩

## What reality actually shows (verified — see `references/`)
- ⟨Grounded finding, with the file:line / product / source that proves it.⟩
- ⟨State the false premise plainly: "believed X; verified NOT X because …".⟩
- ⟨What *does* already exist — in the user's own target — that covers the need.⟩

## Why the premise fails the build
1. ⟨The real state of the target vs. what the ask assumed.⟩
2. ⟨What the user confirmed they don't need / don't do (with date).⟩
3. ⟨Any capability the build would add that is unwanted, premature, or the riskiest part.⟩

## Zero-build option (if the need shows up in a small way)
⟨The cheapest existing mechanism that covers a bit of the need with no build — name it, say how, note its limit. If none exists, say so.⟩

## Revisit triggers — build ONLY when the premise turns true (a trigger is actually felt)
| Trigger (a real, felt pain — not hypothetical) | Then build |
|---|---|
| ⟨Concrete pain the user will actually notice⟩ | **Tier 1 — ⟨smallest thing that resolves it⟩** |
| ⟨A bigger pain⟩ | **Tier 2 — ⟨next increment, on top of Tier 1⟩** |
| ⟨The pain that justifies the expensive version⟩ | **Tier 3 — ⟨the heavy build⟩ (premature until then)** |

Investigation (`references/decisions.md` + any audit/scan notes) is preserved so a future build resumes with zero re-analysis.

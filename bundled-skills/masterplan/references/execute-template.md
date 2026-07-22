# EXECUTE.md Template

Copy into the package's `EXECUTE.md`, filling the ⟨placeholders⟩. This file is the **single prompt**: the owner hands it (or its path) to any capable coding agent, and the build runs end-to-end. Keep it short — the masterplan carries the content; this file carries the rules of engagement.

---

# Execute: ⟨project name⟩

You are building this project end-to-end. Everything you need is in this folder.

## Rules

1. **Read `masterplan.md` fully before writing anything.** It contains every decision, already made. Do not re-litigate decisions or "improve" them — section 20 (Considered and rejected) explains the choices that might look like mistakes. If you find a genuine contradiction or impossibility in the masterplan, stop and report it; do not improvise around it. Decisions are recorded as prose and contracts, deliberately free of implementation detail — where a snippet does appear (a schema, a state machine, a type shape), it encodes a decision: honor what it decides, don't treat it as literal code to paste.

2. **Resume rule.** Before starting, read `STATUS.md`. If any milestones are checked, verify they actually work — run the app, run the checks, do not trust the checkmarks blindly — then continue from the first unchecked milestone. Treat any milestone marked `[!] needs rework` as unchecked: rebuild it according to its note before moving on. Never restart from zero when a partial build exists.

3. **Evidence rule.** Check a milestone off only with evidence: a passing test, a rendering page, a succeeding command — recorded in the milestone's note. "The code is written" is not evidence.

4. **Credentials rule.** Never invent or fake keys. Create `.env.example` early with every variable from masterplan section 17. When a milestone needs a real credential, stop and ask the owner for it — do not stub it and continue as if it worked.

5. **Change-guard rule.** If the owner (or anyone) requests a scope change mid-build — a new feature, a different stack, a dropped requirement — do not improvise it. The masterplan is the single source of truth for this build's entire life. Reply: *"That's a scope change — run it through masterplan revise mode so the masterplan is updated first, then I'll continue against the updated plan."*

6. **Build order.** Follow masterplan section 18 in sequence. The final milestone is always the full QA pass: every acceptance criterion in masterplan section 4, verified with evidence.

7. **Design-stack rule.** If this project has a UI, engage the engine's design skills **before writing any frontend code**: `impeccable` (or the platform's frontend-craft equivalent) for the static craft — layout, typography, color, register — plus `motion-library` for the motion layer. They compose; using only one is half a build. If the engine lacks these skills, carry the discipline manually: build to masterplan section 15 (design direction, mood, look references, must-not-look-like) and never ship default component-library styling. A UI that works and passes the interaction floor but ignores section 15 is not done.

8. **Interaction baseline rule.** If this project has a UI, `references/ui-baseline.md` is the non-negotiable floor — button/focus/disabled/loading states, loading/empty/error/populated data states, form feedback, keyboard operability, responsive, motion. It is not a suggestion and not optional polish: a surface that skips it is not done, no matter that it "works." Build to it as you go rather than bolting it on at the end, and satisfy its verification checklist in the final QA pass. Deliberate exceptions must already be listed in masterplan section 20; anything not listed there is required.

## Definition of done

All milestones in `STATUS.md` checked with evidence, including the final QA pass. For any UI project, that pass includes the `references/ui-baseline.md` verification walked and evidenced (states driven, not screenshotted). Nothing else counts as done.

Begin.

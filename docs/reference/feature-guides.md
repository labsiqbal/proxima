# Feature Guides — the per-feature "good by default" standard

> Every Proxima feature that produces creative work should ship with a **default
> quality bar** baked into the runner preamble, so *any* runner (Claude / Codex / Hermes)
> produces good output on the first try — even when the user gives minimal direction.

> **Current release:** image generation is always active. **Design Studio is enabled**
> (on by default in dev; off in the packaged install), so its guide is injected when the
> flag is on. **Video** stays disabled by default, so the Video guide is retained on disk
> but excluded from prompt composition while its flag is false. A guide is injected only
> while its server-owned feature flag is true.

This doc defines **how those guides are written** (the authoring standard) and **how they
are wired** (the registry). It is the source of truth for adding or upgrading a guide.

Guides are injected by `build_run_preamble()` (`apps/api/proxima_api/wiki_memory.py`)
into the **first prompt** of an agent's ACP session — see
[`architecture.md`](architecture.md) for the run flow.

---

## Why this exists

An agent with no baseline defaults to the mean: flat design, static "video", generic UI.
Proxima's job is to raise the floor. We don't retrain the model — we hand it the
taste bar as plain text, the same way `DESIGN_GUIDE` already does. The bars we trust
(`impeccable` for UI craft, `motion-library` for motion) are **distilled** into runner-
agnostic text here — we don't invent taste from scratch, we port a reference.

Design principle: **own the standard, rent the runner.** The guide is ours and permanent;
the model behind it changes constantly.

---

## The authoring standard — every guide has these 5 parts

Write each guide in this order. Keep it plain text, runner-agnostic, and tight
(≈150–350 words — it costs tokens on *every* run, so every line earns its place).

1. **Role & register** — who the agent should *be* for this feature, in one line.
   "Act like an art director," "a senior frontend engineer," "a motion designer." This
   sets the altitude. Never "a generator."

2. **Trigger & clarify** — when this feature is in play, and *when to ask first*. If key
   creative intent is missing, fire a compact `<question-form>` (see `QFORM_GUIDE`) with
   only high-impact questions. **Always offer a strong default** ("Use Proxima's art
   direction") so "no direction" never stalls — it just ships the default recipe.

3. **Quality bar (good vs slop)** — the heart. 3–8 concrete rules: what good looks like,
   and an explicit **anti-slop ban list** of what to never do. Distill from the reference
   skill and *cite it* (e.g. "// distilled from impeccable §color") so the guide stays
   maintainable as the reference evolves.

4. **Default recipe** — if the user gives minimal direction, the good default to ship
   right now. This is the "ready to run by default" promise: a concrete, opinionated
   starting composition — not a blank canvas, not a question loop.

5. **Output contract** — exactly where/how to write the artifact so Proxima renders it:
   folder, filename, schema, wrapper tags. Never invent file paths. This is what turns a
   good idea into a working artifact card.

### Meta-rules for the standard itself

- **Runner-agnostic.** No tool-specific assumptions (no "use the AskUserQuestion tool").
  Plain text that Claude, Codex, and Hermes all obey.
- **Reference-anchored (chimera).** Creative bars are ports of a trusted skill, not
  freehand opinion. Cite the source so upgrades are mechanical.
- **Tight.** If a line doesn't change the output, cut it. Tokens ship on every run.
- **Composable.** One registry entry, ordered, optionally conditional — see below.

---

## The registry — one list, one compose step

Today the guides are loose module constants (`QFORM_GUIDE`, `DESIGN_GUIDE`, `VIDEO_GUIDE`)
hand-concatenated inside `build_run_preamble()`. The registry replaces that with a single
ordered list so **adding a feature = appending one entry**, not editing concatenation logic.

```python
# apps/api/proxima_api/feature_guides.py  (proposed)
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class FeatureGuide:
    id: str                              # "qform" | "design" | "video" | "app-ui" | ...
    title: str                           # human name, for docs/debug
    text: str                            # the guide, authored to the 5-part standard
    order: int                           # position in the preamble (low = earlier)
    applies: Callable[[dict], bool] = lambda ctx: True   # when to include

FEATURE_GUIDES: list[FeatureGuide] = [
    FeatureGuide("qform",  "Asking the user questions", QFORM_GUIDE,  10),
    # Design and Video entries are registered only when their server flags are enabled.
    FeatureGuide("app-ui", "Build & Preview app (UI)",  APP_UI_GUIDE, 40),
    # add the next feature here — one line.
]

def compose_guides(ctx: dict) -> str:
    picked = [g for g in sorted(FEATURE_GUIDES, key=lambda g: g.order) if g.applies(ctx)]
    return "\n\n".join(g.text for g in picked)
```

`build_run_preamble()` then calls `compose_guides(ctx)` instead of listing constants by
hand. `ctx` carries what "applies" needs (session kind, whether the project can run apps,
feature flags). Start with everything `applies=True`; add conditions only when a real case
appears (YAGNI).

**Migration is behaviour-preserving:** the same three guides, same order, same text — just
moved into the list. Then we add `app-ui` as the first new entry.

---

## Worked example — the App/UI guide (authored to the standard)

The biggest current gap: **Build & Preview app** has no quality bar, so agent-built UIs
default to slop. Here is `APP_UI_GUIDE` written to the 5-part standard, distilling
`impeccable`. (Illustrative text — refine wording when we wire it in.)

```text
## Building app UIs (Build & Preview)
Act like a senior product designer + frontend engineer, not a code generator. A UI that
"works" but looks generic is not done.

When the user asks to build/change a page, app, landing, or component and the visual
direction is unstated, fire a compact <question-form> for only: purpose, audience,
brand/register (marketing vs product), and one reference vibe — with a strong default
"Use Proxima's house style". If direction is given, skip questions and build.

Quality bar — good vs slop (distilled from impeccable):
- One type scale, one spacing scale, one accent. Never mix ad-hoc sizes/margins.
- Real hierarchy: one clear focal point per view; supporting content recedes.
- Register-correct: marketing = expressive, confident type + motion; product = quiet,
  dense, legible. Don't cross them.
- Generous, consistent whitespace; align to a grid; nothing floats arbitrarily.
- Color with intent: a restrained palette + one accent doing the work; enough contrast
  for WCAG AA. No random gradients, no rainbow.
- Ban list: centered-everything, one-column-of-cards-forever, default-blue links,
  unstyled form controls, drop-shadow on everything, emoji as iconography, lorem ipsum
  in a shipped view.

Default recipe (minimal direction): a clean, responsive single-page layout — considered
header, one strong hero with a real headline + sub + primary CTA, 2–3 content sections
with genuine hierarchy, a quiet footer. Neutral surface + one accent, system-ish sans,
tasteful motion on entrance only. Pair motion choices with motion-library where it earns.

Output contract: write the app under the project's app workspace so Build & Preview picks
it up; keep source in the project's conventional structure; never invent paths. Proxima
surfaces it as a preview card.
```

Notice all five parts are present, it cites its reference, and the **default recipe** means
"just build me a page" yields something good with zero further questions.

---

## Adding or upgrading a guide — checklist

1. Write/upgrade the text to the **5-part standard** above; distill from the reference
   skill and cite it.
2. Add/adjust one `FeatureGuide(...)` entry in `feature_guides.py` (id, title, order,
   `applies`).
3. Keep it tight — re-read and cut any line that doesn't change output.
4. If it changed how a feature behaves, update [`CAPABILITIES.md`](../CAPABILITIES.md) and
   append to [`wiki/log.md`](../wiki/log.md) (docs contract).

---

## Guide inventory (status)

| id | Feature | Guide | State |
|----|---------|-------|-------|
| `general` | How to operate (all sessions) | `GENERAL_GUIDE` | ✅ **wired 2026-07-05** — project awareness, toolkit/skills, output routing, evidence-first, ask-vs-act, reporting; profile instructions override. [feature-guides/general.md](feature-guides/general.md) |
| `qform`  | Ask user questions      | `QFORM_GUIDE`  | ✅ solid (mechanical) |
| `design` | Design Studio           | `DESIGN_GUIDE` | injected when Design Studio is enabled (on by default in dev) |
| `video`  | Video Studio            | `VIDEO_GUIDE`  | retained; disabled by default and not injected |
| `app-ui` | Build & Preview app     | —              | ❌ missing — biggest gap (example above) |
| `wiki`   | Wiki / memory           | inline         | ✅ ok |

> Registry refactor (`feature_guides.py`) deliberately deferred until the next guide
> lands — one new entry alone doesn't justify the migration (YAGNI).

Fill the ❌/⚠️ rows over time; the standard + registry make each one a small, isolated add.

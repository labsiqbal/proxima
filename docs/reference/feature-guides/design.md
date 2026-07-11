# DESIGN_GUIDE v2 — the wired quality bar

> **Release status:** retained for future reactivation. Design Studio is disabled by
> default with `PROXIMA_FEATURE_DESIGN_STUDIO=0`; this guide must not be injected
> while the feature is disabled. Image generation remains active without Studio
> bridge actions.

> **Source status:** retained as `DESIGN_GUIDE` in
> `apps/api/proxima_api/wiki_memory.py` — v2 rules + carried-over output contract
> (including the gradient-background schema from the 29038bd design-studio batch, which
> superseded that commit's inline "Non-negotiable craft rules" paragraph). Keep this doc
> and the constant in sync: iterate here first, then mirror into the code.
>
> Written to the 5-part standard in [`../feature-guides.md`](../feature-guides.md).
> Sources: graphic-design canon (poster composition, CRAP, one-message advertising,
> editorial/Swiss layout) as the spine + transferable parts of `impeccable` (color
> strategy, contrast verification, type pairing, category-reflex slop test) + a static-
> graphics layer impeccable doesn't cover (gen-image prompt craft, platform fitness,
> carousel narrative).

## Why the old guide failed (observed)

Real failures from a live test (generic "make a design" prompt): white text on white
background, every element a rect, every carousel artboard the same templated layout.
Root cause: the old guide is adjectives ("sensible palette", "use the scene creatively"),
not procedure. Colors were decided per-layer mid-build; no verifiable rules; no example;
no self-check. v2 fixes that with: forced decision order (declare → build → self-check),
checkable rules with numbers, a hard ban list, and one few-shot fragment.

---

## The prompt text (v2 draft)

Everything between the markers is the literal text that would ship in the preamble.
The schema/output-contract section at the end is carried over from the current guide
unchanged (it already works) — marked below.

<!-- BEGIN PROMPT -->
```text
## Designs (Design Studio)
Act as an art director with a portfolio to protect, not a layout generator. The bar:
if a designer could glance at the result and say "AI made that", it failed.

If the user asks for a design and key creative intent is missing, ask a compact
<question-form> first (goal, audience, copy, mood, image needed, brand constraints —
only the high-impact ones for that brief). Always include a strong default option like
"Use your art direction". If the user already gives enough direction, skip questions
and build.

### Step 1 — Declare the direction BEFORE any layer
Decide and commit, in this order:
1. MESSAGE — one sentence: what must the viewer take away in 2 seconds? One message
   per artboard. If the user's copy is generic, sharpen it (a punchy concrete headline
   beats a safe slogan — copy is part of the design). Less text is better.
2. SCENE — one sentence: who sees this, where, in what mood. Then check: if your
   visual direction could be guessed from the category alone ("coffee shop → warm
   cream + brown"), that is the AI reflex — pick a different deliberate angle.
3. PALETTE — declare 4 color roles with hex values NOW: background / ink (text on
   light) / accent / muted. Pick a commitment level: restrained (neutrals + one accent),
   committed (one saturated color carries 30–60% of the surface), or drenched (the
   surface IS the color). Every layer color MUST come from these roles — never invent
   a color mid-layout. Don't default to restrained-pale; commit when the brief allows.
4. TYPE — one pairing on a contrast axis (serif + sans, or one family in 2–3 weights;
   never two similar sans). Max 3 text sizes per artboard; headline ≥ 3× body size.

### Step 2 — Compose
First pick a composition ARCHETYPE for the artboard — and vary it across designs, never
own just one: type-dominant poster, image-led full-bleed, split, off-grid collage,
framed/formal symmetric, extreme-crop close-up, pattern/texture field.
If this project already has designs (listed in this context): KEEP its established
palette + type system (brand cohesion — same campaign, same skin) unless the user asks
for a new direction, but VARY the composition archetype from the recent designs (don't
produce the same layout in new colors).
Then apply the floors. These prevent broken output — they are NOT a style; break one
deliberately when the concept demands it:
- ONE focal point per artboard: one element clearly dominant, everything else recedes.
- Keep roughly a third of the artboard empty. If every corner is filled, remove something.
- Create depth: something overlaps or bleeds off the edge — flat, all-aligned, nothing
  touching = template.
- All-centered-everything is a reflex, not a choice; center only when the archetype is
  formal/symmetric.
- Not everything is a rectangle: if every non-text layer is a rect, rework one into
  whatever the concept actually wants — image, path, ellipse, line. Do NOT reflex-add a
  decorative blob to pass this check.
- One system throughout: one corner-radius value, one shadow style, spacing in multiples
  of one base unit.

### Contrast (hard rules)
- Text sitting on the background uses the ink role; text on a dark panel or image uses
  the background/light role. NEVER fill text with a color in the same family as what
  is directly behind it.
- Text over an image always gets a scrim (translucent panel between image and text) or
  sits in a visually quiet area of the image.

### Imagery
- If the brief implies a visual subject, include an image layer with src:"gen:<prompt>".
  Write the gen prompt like a photographer's brief: subject + lighting + angle/lens +
  mood + style — e.g. "moody top-down iced latte, hard morning light, deep shadows,
  editorial food photography". Never a bare noun.
- Give the image a job: hero, full-bleed background (with scrim), or texture. Never a
  small decorative box floating in a corner.
- Never assemble a product packshot from primitive shapes (rects/ellipses read as a
  cartoon, not a product). If the product must appear, put it INSIDE the gen: prompt
  ("sunscreen tube resting on a gel swirl, …") or keep the design type + texture only.

### Carousel / deck (multiple artboards)
A carousel is a story, not a template. Artboard 1 = hook (oversized type or big visual,
minimal words). Middle artboards = content, each with a DIFFERENT layout (text-left,
full-bleed image, split, type-only). Final = CTA. Adjacent artboards must not share the
same layout structure. Cohesion comes from the palette + type system, not from repeating
the composition.

### Platform
Sizes: IG post 1080×1080, story/reel 1080×1920 (keep critical content out of the top
~250px and bottom ~340px UI zones), X 1600×900, poster 1080×1350, deck 1920×1080,
mobile 390×844, web 1440×1024. Thumbnail test: the headline must still read when the artboard is ~150px
wide in a phone feed.

### Step 3 — Self-check before writing scene.json
Audit every artboard: (a) does every text layer clearly contrast with what is DIRECTLY
behind it? (b) one focal point + visible empty space? (c) at least one non-rect element?
(d) carousel: does this artboard's layout differ from the previous one? (e) would a
designer put this in a portfolio? Fix failures, then write the file.

### Example of the standard (structure to imitate, not copy)
One artboard showing the rules in action — declared palette (#0E0F12 bg / #F5F2EC light /
#E8552F accent), committed level, focal hero, blob depth, scrim, 3-size type:
{ "id":"a1","width":1080,"height":1080,"background":"#0E0F12","layers":[
 {"id":"img","type":"image","x":430,"y":-60,"width":760,"height":760,"src":"gen:sculptural espresso pour, dramatic side light, dark backdrop, editorial product photography","cornerRadius":380},
 {"id":"blob","type":"path","x":-140,"y":620,"width":620,"height":540,"d":"M0.62 0.01C0.83 0.06 1 0.26 0.98 0.49C0.96 0.73 0.76 0.95 0.5 0.99C0.24 1.02 0.02 0.84 0 0.58C-0.02 0.33 0.4 -0.04 0.62 0.01Z","fill":"#E8552F","opacity":0.92},
 {"id":"eyebrow","type":"text","x":90,"y":180,"width":400,"text":"SINGLE ORIGIN","fontSize":26,"fontFamily":"Inter","fontStyle":"bold","fill":"#E8552F","letterSpacing":6},
 {"id":"head","type":"text","x":80,"y":240,"width":620,"text":"Bitter.\nBy design.","fontSize":128,"fontFamily":"Playfair Display","fontStyle":"bold","fill":"#F5F2EC","lineHeight":0.98},
 {"id":"body","type":"text","x":90,"y":600,"width":420,"text":"Roasted 40 hours before it hits your cup.","fontSize":30,"fontFamily":"Inter","fill":"#F5F2EC","opacity":0.85,"lineHeight":1.4},
 {"id":"cta","type":"rect","x":90,"y":880,"width":280,"height":76,"fill":"#E8552F","cornerRadius":38},
 {"id":"ctat","type":"text","x":90,"y":902,"width":280,"text":"Find a bag","fontSize":28,"fontFamily":"Inter","fontStyle":"bold","fill":"#0E0F12","align":"center"}
]}
Note what makes it work: dark committed surface, light ink, ONE accent doing all the
work, headline 4× body, image bleeding off-canvas, a non-rect blob, empty space kept.

A second, OPPOSITE direction — light, type-dominant, no image (images are not mandatory):
{ "id":"a1","width":1080,"height":1080,"background":"#F4F1EA","layers":[
 {"id":"head","type":"text","x":70,"y":140,"width":940,"text":"Loud ideas.\nQuiet rooms.","fontSize":150,"fontFamily":"Anton","fill":"#141310","lineHeight":0.95},
 {"id":"rule","type":"line","x":80,"y":660,"x2":520,"y2":660,"stroke":"#141310","strokeWidth":3},
 {"id":"body","type":"text","x":80,"y":700,"width":460,"text":"A coworking space for people who hate coworking spaces.","fontSize":32,"fontFamily":"Inter","fill":"#141310","lineHeight":1.45},
 {"id":"dot","type":"ellipse","x":880,"y":840,"width":120,"height":120,"fill":"#2447F2"},
 {"id":"cta","type":"text","x":80,"y":950,"width":500,"text":"Tour the space →","fontSize":30,"fontFamily":"Inter","fontStyle":"bold","fill":"#2447F2"}
]}
These two examples show the BAR (contrast, focal point, empty space, depth, commitment) —
NOT the style. Never reuse their palettes, moods, or layouts as defaults; Step 1 decides
those fresh for every brief.
```
<!-- END PROMPT — schema/output-contract section from the current DESIGN_GUIDE follows verbatim (scene.json location, layer schema, fonts, edit flow, result cards) -->

---

## What changed vs v1 (mapping)

| v1 problem | v2 mechanism |
|---|---|
| Colors decided per-layer → white-on-white | Step 1.3: palette roles declared with hex BEFORE layers; hard contrast rules |
| Everything rects | "≤ half of non-text layers may be rects" + "≥1 non-rect element" (checkable) |
| Templated carousel artboards | Role per artboard + "adjacent artboards must not share layout structure" |
| Adjectives, unverifiable | Numbers everywhere: ⅓ empty, 3× headline, 250px safe zones, 150px thumbnail |
| No example | One few-shot artboard fragment demonstrating the rules |
| No quality gate | Step 3 self-check (5-point audit) before writing the file |
| Generic copy accepted as-is | Step 1.1: sharpen copy; copy is part of the design |
| Weak gen prompts ("coffee") | Photographer's-brief formula for gen: prompts |
| Category-reflex styles | SCENE sentence + "guessable from category = AI reflex" test |
| Risk: v2's own rules become the new template | Floors framed as bans (not recipes), archetype menu, TWO opposite examples + "not the style" warning, "differ from this project's recent designs" check |

## Aspect coverage (from the 10-aspect bar)

1 message/copy → Step 1.1 · 2 art direction → Step 1.2 · 3 composition/negative space/depth → Step 2 · 4 typography → Step 1.4 · 5 color system → Step 1.3 + Contrast · 6 imagery/gen-craft → Imagery · 7 finish → Step 2 last bullet · 8 platform → Platform · 9 carousel narrative → Carousel · 10 slop test → Step 1.2 check + Step 3(e).

## Open iteration points (decide together, one by one)

> Resolved 2026-07-04 — **monoculture risk** ("won't hardcoded rules make every design
> the same?"): floors reframed as bans instead of recipes, composition archetype menu
> added, second opposite example added with a "bar not style" warning, and a
> differ-from-recent-project-designs check. Diversity comes from Step 1; Step 2 is a floor.

1. ~~Length/token cost~~ — **Resolved 2026-07-04.** Measured: v1 ≈ 1.1k tokens; v2 rules ≈ 1.9k + carried-over schema ≈ 0.6k → ≈ 2.5k total (~2.2×). Injected once per ACP session (first prompt), not per message; money cost is a fraction of a cent per session. Verdict: worth it — if a rule changes output it stays, if it doesn't it gets cut on evidence from live tests, not on size.
2. **The examples** — two JSON fragments (opposite directions) so the model imitates the standard, not one style. Trim to one + prose sketch only if live tests show attention dilution.
3. **Numbers tuning — protocol agreed 2026-07-04.** The numbers are ANCHORS that shift the model's defaults, not measured gates (the model estimates, it can't measure pixels). Provenance: ⅓ empty + 3× headline = standard poster practice (taste anchors, keep); story safe zones = platform fact (corrected: top ~250px, bottom ~340px); 150px thumbnail = IG profile grid. Tuning loop after wiring: fixed battery of ~5 briefs → inspect for too-loose (slop passes → tighten) vs too-tight (model contorts to satisfy the number → loosen) → adjust → re-run. Exact measurement belongs to the future lint gate (point 5), which CAN compute empty-space % and font ratios from scene.json coordinates.
4. **Ban list** — v2 encodes bans inside rules (no same-family text fill, no all-centered, no identical adjacent artboards). Add an explicit "NEVER do" block, or is inline enough?
5. **Server-side lint gate (later, code)** — contrast ratio, all-rect, twin-artboard detection are mechanically checkable on scene.json; prompt raises the floor, a lint gate would catch the rest. Deferred until the prompt is proven.

## Live test log

**Test 1 — 2026-07-04, Codex one-shot, brief: "IG feed post, sunscreen gel 'Sada' SPF 50, anak muda"** (deliberately a different domain than both examples).
- ✅ Followed Step 1 (declaration first: sharp copy "SPF 50 nggak berat.", anti-trope scene, committed palette, Bebas+Inter, 4.9× headline ratio). Contrast clean, non-rects present, image bleeds, asymmetric, breathing space. All three original failure modes (white-on-white, all-rects, templated) absent.
- ❌ Weakest element: product tube assembled from rects/ellipses → reads as cartoon. Fixed with a new Imagery rule (packshot goes inside the gen: prompt, never primitive shapes).
- ⚠️ Mild echo of the examples' eyebrow+rule+body+CTA scaffold — watch across future tests; if it repeats every time, vary the examples' scaffolds.

## Next guides in this folder (queue)

- `video.md` — Video Studio bar (motion taste from motion-library; currently the thinnest guide)
- `app-ui.md` — promote the worked example from `../feature-guides.md` into a real draft

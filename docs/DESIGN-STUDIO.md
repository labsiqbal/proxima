# Proxima Design Studio — Blueprint

AI-assisted, multi-surface design inside Proxima. The agent generates designs as
**editable layered structures** (not flat images), and the human refines them on a
canvas — direct manipulation (Figma-feel) + AI iteration (chat).

> **Status:** shipped feature, on by default behind a server-owned flag (standing
> decision 8). Owners can disable it with `PROXIMA_FEATURE_DESIGN_STUDIO=0` in
> `~/.config/proxima/proxima.env` and a restart (the flag is read at boot). While the
> flag is off, the backend answers 503 (`feature_disabled`) before any side effect and
> the frontend omits Design navigation, commands, provider checks, and bridge actions.
> Image generation is independent of this flag and always available.

## Why

AI image-gen bakes text into pixels → uneditable. Design Studio makes the agent
emit **per-element scenes** (text stays real, editable text; images/shapes are
separate layers) so a human can fix copy, tweak styles, and move things — then
export. The agent drafts; the human polishes; the agent iterates.

## Architecture: 2 engines cover 5 surfaces

| Surface | Shape | Engine |
|---|---|---|
| Graphic / social | 1 artboard, absolute layers | **Konva** |
| Slide deck | N artboards (slides) | **Konva** (multi-artboard) |
| Mobile app | device-sized artboards + frame | **Konva** |
| Video / hyperframe | scene + timeline/keyframes → encode | **Konva** + ffmpeg |
| Website | responsive HTML flow | **HTML** (GrapesJS / iframe) |

Konva (MIT, react-konva) powers 4/5 — they are all "layered scenes" differing only
by artboard count (deck), size (mobile), and the time axis (video). Web is a
separate HTML-flow engine.

### Shared shell (all surfaces)
- **Chat / AI iterate** — reuse the existing run loop; agent reads current doc, edits it.
- **Canvas frame** — the surface-specific renderer (Konva stage or HTML iframe).
- **Inspector (tweaks panel)** — properties of the selected element (text, font, color, spacing, size).
- **Storage** — design doc + assets in the project under `artifacts/design/<slug>/`.

## Scene schema (current)

A design is JSON saved at **`artifacts/design/<id>/scene.json`** — one folder per
design, `<id>` a short slug (e.g. `acme-ig-promo`). It then appears automatically
in **Design → Your designs**.

```jsonc
{
  "id": "<id>",                       // MUST equal the folder name
  "type": "graphic | deck | mobile | video",
  "title": "Human-readable title",
  "artboards": [
    {
      "id": "a1",
      "width": 1080, "height": 1080,  // IG post 1080², story/reel 1080×1920, X 1600×900,
      "background": "#ffffff",        //   poster 1080×1350, deck 1920×1080, mobile 390×844, web 1440×1024
      "layers": [ /* see layer types */ ]
    }
  ]                                    // deck/carousel = multiple artboards
}
```

Every layer has a UNIQUE `id`, plus `x`, `y`, and optional `rotation` (deg) + `opacity` (0..1):

- **text** — `{ type:"text", x,y, width, text, fontSize, fontFamily, fontStyle:"bold"|"italic"|"bold italic"|"normal", textDecoration:"underline"?, fill, align:"left"|"center"|"right", lineHeight, letterSpacing }`
- **rect** — `{ type:"rect", x,y, width, height, fill, cornerRadius?, stroke?, strokeWidth?, shadow?:boolean }`
- **ellipse / triangle / star** — `{ type:"ellipse", x,y, width, height, fill, stroke?, strokeWidth?, shadow? }` (star also takes `points?`)
- **line** — `{ type:"line", x,y, x2,y2, stroke, strokeWidth }`
- **path** — `{ type:"path", x,y, width, height, d:"<SVG path data>", fill }` — organic/blob shapes
- **image** — `{ type:"image", x,y, width, height, src, cornerRadius? }`
- **image frame (clip mask)** — any `rect`/`ellipse`/`triangle`/`star` can hold a clipped
  image (Canva-style) by adding `imageSrc` (a real path or `"gen:<prompt>"`) plus optional
  `imageCropX`/`imageCropY` (0–100 reposition) and `imageCropZoom` (1–4). The image is
  masked to the shape's outline. On the canvas, **dragging an image layer onto a shape**
  absorbs it into the shape as a frame; the inspector's *Image frame* section repositions,
  detaches (pops the image back out as a standalone layer), or removes it. Rendered with a
  Konva `Group clipFunc`; PNG export is automatic, HTML export uses `border-radius`
  (rect/ellipse) or `clip-path` polygon (triangle/star). Blobs (`path`) can't be frames yet.

No full-size background rectangle — the artboard's own `background` carries the colour.
Fonts: Inter, Poppins, Nunito, Merriweather, Playfair Display, Roboto Slab, JetBrains Mono, Oswald, Caveat, Lobster.

**Images:** keep text as real, editable text layers — NEVER bake text into an image.
For a photo/illustration, set an image layer's `src` to `"gen:<short prompt>"`
(e.g. `"gen:green plastic bottle, studio shot, soft shadow, white background"`); the
studio generates the real image via 9router when the design is opened. Don't invent file paths.

## Agent: create / edit a design from chat

The chat agent has filesystem access to the project, so it can author designs directly:

1. Pick a short `<id>` slug and a `title`.
2. Write valid JSON (schema above) to `artifacts/design/<id>/scene.json`.
3. To edit an existing design: read its `scene.json`, modify, write it back (keep the same `id`).
4. Tell the user to open **Design → Your designs** to see and refine it on the canvas.

Compose real layouts — eyebrow + headline + supporting copy + CTA/accent shapes,
with a sensible palette and type hierarchy — not a lone headline.

## In-studio AI contract

The Design Studio's own chat (and a `/design` draft from the main chat, which opens a
linked **design session**) sends the current scene + the selection, and the agent
replies with a `<design-scene>{...}</design-scene>` block that the canvas applies live.

**Contract boundary — the client owns the file in a design session.** In a design
session the agent must reply with the `<design-scene>` block *only*; it must NOT write
`scene.json` to disk itself (the "create/edit from chat" path above is for the **main
chat**, not design sessions). The server seeds the draft with a `runPendingId` marking
the scene as awaiting exactly that run; the client (`applyDesignReply`) is the sole
writer of the finished scene, clearing `runPendingId` and stamping `appliedRunId`. If
the agent also writes the file, it strips `runPendingId` and the studio's
recovery-on-open no longer auto-applies — the canvas hangs on "Designing…" until a
manual refresh. This is enforced by `DESIGN_SESSION_GUARDRAIL` (which tells the agent to
ignore the main-chat "write it to disk" phrasing) in `wiki_memory.py`.

Live reconcile is defence-in-depth: the run-stream terminal event applies the reply
instantly, and a watchdog (`DesignStudio.tsx`) polls the run's real status — immediately
on attach, then every 5s — so a dropped SSE/WS event still lands the design in seconds
without a refresh.

## Per-project brand guidelines (`design.md`)

Each project may carry a `design.md` at its **root** (`<project>/design.md`) — hand-written
or generated brand guidelines / design preferences (palette, type, tone, do/don't,
reference notes). On every design run (main-chat `/design`, in-studio chat, or the agent
authoring a design from chat), the backend reads it and injects the content into the
agent's first-turn preamble, right after the `DESIGN_GUIDE`, framed as the project's
DEFAULT brand direction that wins over generic instincts but yields to the user's explicit
request that turn. This makes the agent compose on-brand without a tool call — the same
always-in-context mechanism the wiki memory catalog uses.

- Reader + injection: `wiki_memory.read_design_guidelines()` (size-capped, best-effort) →
  `build_run_preamble(..., design_guidelines=...)`; wired in `run_prompting.py`, gated on
  `include_design_studio`.
- Content is only injected for design-enabled runs, never for plain agent/chat runs.

**Generate it** — the Design home has a *Brand guide (design.md)* action that opens a
modal (`BrandGuideModal`): give any reference **URLs** (brand site, Pinterest, any page),
upload reference **images**, and/or free-text **notes**. `POST
/api/projects/{slug}/design/brand-guide` crawls the URLs server-side
(`brand_extract.fetch_url_digest` — title/description/CSS colours/fonts/og-image/copy,
best-effort, never raises), then queues an agent run (kind `brand_guide`) whose prompt
carries those digests + the notes + the images as vision (`⟦VISION:…⟧`) and instructs the
agent to write `design.md` at the project root. The client polls `design.md` until it
lands and previews it. JS-rendered pages (Instagram) yield thin HTML — the digest says so
and points the user to uploading a screenshot instead.

## `/design` from the main chat

Typing `/design <brief>` in the main chat (`routes/chat.py`) seeds a shell scene, creates
a linked **design session**, kicks off the compose run, and returns a draft card.

- **Thin brief → clarify first.** If the brief is almost empty (no attached image, < 3
  words), the backend replies with a `<question-form>` in the main chat instead of drafting
  something generic. The form's `submit-as="/design"` makes answering re-issue `/design`
  with the answers as the brief, so the same path runs again — now on-brief. (Same
  mechanism for `/image`.) See CAPABILITIES §13.
- **Deep-open.** The draft card opens *that* design directly. `DesignStudio` defers
  `onOpened()` until `openDesign()` resolves so clearing the pending prop can't let the
  restore-last-design effect race in and drop the user on the start screen; a failed read
  falls back to the gallery, never a bare "home".
- **Chat continuity.** The design session's first user message is the brief (for the form
  flow, the formatted answers), and the compose reply is its assistant turn — so opening
  Design Studio shows the request context, mirroring the main-chat exchange rather than an
  empty chat. `hydrateChat` loads these from the session on open.

## Export
- graphic / mobile / deck-slide → PNG/JPG (`stage.toDataURL`).
- deck → multi-page PDF.
- video → frames → **ffmpeg** (ffmpeg.wasm client-side, or server-side ffmpeg for long clips).
- web → HTML/CSS.

## Roadmap (build order — one Konva engine unlocks four surfaces)

1. **Graphic POC (Konva)** — canvas, AI-generate layers, select + edit copy + tweaks panel, move/resize (Transformer), export PNG. *Proves the layered-scene model.*
2. **Deck + Mobile** — multi-artboard + device-sized artboards. Cheap extensions of #1.
3. **Video** — timeline + keyframes + ffmpeg export. Heaviest Konva surface.
4. **Web** — HTML engine (GrapesJS), reusing the shared shell + AI loop.

## POC (current) scope
Frontend `DesignStudio` view: Konva stage rendering a scene → click a layer to
select (Transformer handles) → Inspector edits text + key styles → move/resize on
canvas → export PNG → persist scene JSON to the project. Then wire AI generate
(agent → scene JSON → load).

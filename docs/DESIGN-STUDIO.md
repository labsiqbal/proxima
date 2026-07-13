# Proxima Design Studio — Blueprint

AI-assisted, multi-surface design inside Proxima. The agent generates designs as
**editable layered structures** (not flat images), and the human refines them on a
canvas — direct manipulation (Figma-feel) + AI iteration (chat).

> **Status:** active feature (no longer a disabled blueprint). Design Studio is **on by
> default in dev** (`scripts/dev` sets `PROXIMA_FEATURE_DESIGN_STUDIO=1`) and off by
> default in the packaged install — flip the server-owned flag to enable. When off, its
> navigation, commands, provider checks, bridge actions, and routes stay unavailable. The
> **Video** surface was dropped from Design Studio; the separate Video Studio remains
> retained-but-off (`PROXIMA_FEATURE_VIDEO=0`). Image generation is always available.

## Why

AI image-gen bakes text into pixels → uneditable. Design Studio makes the agent
emit **per-element scenes** (text stays real, editable text; images/shapes are
separate layers) so a human can fix copy, tweak styles, and move things — then
export. The agent drafts; the human polishes; the agent iterates.

## Architecture: 2 engines cover 4 surfaces

| Surface | Shape | Engine |
|---|---|---|
| Graphic / social | 1 artboard, absolute layers | **Konva** |
| Slide deck | N artboards (slides) | **Konva** (multi-artboard) |
| Mobile app | device-sized artboards + frame | **Konva** |
| Website | responsive HTML flow | **HTML** (GrapesJS / iframe) |

The four surfaces the studio actually offers are Graphic, Slide deck, Mobile app, and
Website (`SURFACES` in `components/design/templates.ts`). Konva (MIT, react-konva) powers
3/4 — they are all "layered scenes" differing only by artboard count (deck) and size
(mobile). Web is a separate HTML-flow engine. (A Video / hyperframe surface was scoped
here originally but has since been dropped from Design Studio.)

### Shared shell (all surfaces)
- **Chat / AI iterate** — reuse the existing run loop; the agent reads the current doc
  and edits it, replying with `<design-scene>` blocks the canvas applies live. The agent
  is asset-aware (knows the project's asset library), feature-aware, and
  **vision-capable** (relevant images are attached so a vision model can see them).
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
  "type": "graphic | deck | mobile",
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

No full-size background rectangle — the artboard's own `background` carries the colour.
Fonts: Inter, Poppins, Nunito, Merriweather, Playfair Display, Roboto Slab, JetBrains Mono, Oswald, Caveat, Lobster.

**Images:** keep text as real, editable text layers — NEVER bake text into an image.
For a photo/illustration, set an image layer's `src` to `"gen:<short prompt>"`
(e.g. `"gen:green plastic bottle, studio shot, soft shadow, white background"`); the
studio generates the real image via 9router when the design is opened. Don't invent file paths.

## Agent: create / edit a design from chat

From the main chat, `/design <brief>` (aliases `/image-studio`, `/design-studio`) or the
composer's ✨ Generate → **Design draft** seeds a linked design session that arrives
already designed — the draft is generated, not blank. The chat agent also has filesystem
access to the project, so it can author designs directly:

1. Pick a short `<id>` slug and a `title`.
2. Write valid JSON (schema above) to `artifacts/design/<id>/scene.json`.
3. To edit an existing design: read its `scene.json`, modify, write it back (keep the same `id`).
4. Tell the user to open **Design → Your designs** to see and refine it on the canvas.

Compose real layouts — eyebrow + headline + supporting copy + CTA/accent shapes,
with a sensible palette and type hierarchy — not a lone headline. The agent knows the
project's **asset library** and can place an existing asset directly by setting an image
layer's `src` to its exact project path (use `gen:` prompts only for images that don't
exist yet).

## In-studio AI contract

The Design Studio's own chat sends the current scene + the selection (and, when useful,
the project's asset library and relevant images as **vision** input), and the agent
replies with a `<design-scene>{...}</design-scene>` block that the canvas applies live.
Vision is capability-gated end to end: `buildDesignPrompt` (`components/design/scene.ts`)
appends a `⟦VISION:…⟧` marker, `run_prompting.extract_vision_images` reads it, and
`worker.py` sends the images as ACP image content blocks only when the runner advertises
`promptCapabilities.image` (else the run stays text-only).

**Multi-image edit / compose (Assets tab):** an input tray feeds images labelled
`@image1`, `@image2`, … that the prompt addresses by name, to edit one image or compose
several into one — gated on the image provider's `referenceImages` capability (the
`codex` provider supports it). AI image edit lives in the Assets tab (moved out of the
right inspector).

**Editing tools:** a custom color picker (`ColorInput`) everywhere with an **eyedropper**
(native `EyeDropper` API + a canvas-sampling fallback for non-secure hosts), an on-canvas
**gradient direction guide** (a draggable line), collapsible left/right panels,
restore-last-design on return, and crop preview.

## Export
- graphic / mobile / deck-slide → PNG/JPG (`stage.toDataURL`).
- deck → multi-page PDF.
- web → HTML/CSS.

## Roadmap (build order — one Konva engine unlocks three surfaces)

1. **Graphic POC (Konva)** — canvas, AI-generate layers, select + edit copy + tweaks panel, move/resize (Transformer), export PNG. *Proves the layered-scene model.*
2. **Deck + Mobile** — multi-artboard + device-sized artboards. Cheap extensions of #1.
3. **Web** — HTML engine (GrapesJS), reusing the shared shell + AI loop.

(A Video surface was originally step 3 here; it has been dropped from Design Studio.)

## POC (current) scope
Frontend `DesignStudio` view: Konva stage rendering a scene → click a layer to
select (Transformer handles) → Inspector edits text + key styles → move/resize on
canvas → export PNG → persist scene JSON to the project. Then wire AI generate
(agent → scene JSON → load).

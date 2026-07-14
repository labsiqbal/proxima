// Design Studio scene model (Konva surfaces: graphic/deck/mobile/video).
// A design doc is a typed set of artboards; each artboard holds absolute layers.

// Shared geometry/style fields. rotation (deg) + opacity apply to every layer;
// stroke/shadow apply to shapes/text/images.
type Base = { id: string; x: number; y: number; rotation?: number; opacity?: number; locked?: boolean; groupId?: string; groupName?: string; autoLayout?: boolean; layoutDirection?: 'horizontal' | 'vertical'; layoutGap?: number; layoutPadding?: number; layoutAlign?: 'start' | 'center' | 'end' }
export type LayerEffect = { id?: string; type: 'drop-shadow' | 'inner-shadow' | 'glow' | 'layer-blur' | 'background-blur'; color?: string; opacity?: number; blur?: number; spread?: number; offsetX?: number; offsetY?: number }
type WithEffects = { effects?: LayerEffect[] }
export type GradientStop = { id?: string; offset: number; color: string }

// The gradient's rendered colour stops: Color(fill) is always the 0% stop and To(fill2)
// the 100% stop, with gradientStops as the interior colours in between. This keeps both
// endpoint pickers meaningful — previously any stop silently overrode Color/To entirely.
export function gradientStopList(l: { fill: string; fill2?: string; gradientStops?: GradientStop[] }): { offset: number; color: string }[] {
  const interior = (l.gradientStops || []).filter(s => s.offset > 0.001 && s.offset < 0.999)
  return [{ offset: 0, color: l.fill }, ...interior, { offset: 1, color: l.fill2 || l.fill }].sort((a, b) => a.offset - b.offset)
}
export type FillStyle = { fill: string; fillType?: 'solid' | 'linear-gradient' | 'radial-gradient'; fill2?: string; gradientAngle?: number; gradientStartX?: number; gradientStartY?: number; gradientEndX?: number; gradientEndY?: number; gradientStops?: GradientStop[]; fillOpacity?: number; blendMode?: string }
// imageSrc turns a shape into a Canva-style "image frame": the image is clipped to the
// shape's outline (crop fields position it inside, same semantics as ImageLayer).
type Styled = FillStyle & { stroke?: string; strokeWidth?: number; strokeOpacity?: number; strokeDash?: number; strokeCap?: 'butt' | 'round' | 'square'; strokeJoin?: 'miter' | 'round' | 'bevel'; strokePosition?: 'center' | 'inside' | 'outside'; shadow?: boolean; imageSrc?: string; imageCropX?: number; imageCropY?: number; imageCropZoom?: number }

export type TextLayer = Base & WithEffects & {
  type: 'text'; width: number; height?: number; text: string; fontSize: number; fontFamily?: string; fontStyle?: string; textDecoration?: string; textTransform?: 'none' | 'uppercase' | 'lowercase' | 'capitalize'; listStyle?: 'none' | 'bullet' | 'number'; fill: string; fillType?: 'solid' | 'linear-gradient' | 'radial-gradient'; fill2?: string; gradientAngle?: number; gradientStartX?: number; gradientStartY?: number; gradientEndX?: number; gradientEndY?: number; gradientStops?: GradientStop[]; fillOpacity?: number; align?: 'left' | 'center' | 'right' | 'justify'; verticalAlign?: 'top' | 'middle' | 'bottom'; lineHeight?: number; letterSpacing?: number; textStroke?: string; textStrokeWidth?: number; textStrokeOpacity?: number
  shadow?: boolean; shadowColor?: string; shadowBlur?: number; shadowOffsetX?: number; shadowOffsetY?: number; shadowOpacity?: number
  glow?: boolean; glowColor?: string; glowBlur?: number; glowOpacity?: number
}
export type RectLayer = Base & WithEffects & Styled & { type: 'rect'; width: number; height: number; cornerRadius?: number; cornerRadiusTL?: number; cornerRadiusTR?: number; cornerRadiusBR?: number; cornerRadiusBL?: number }
export type EllipseLayer = Base & WithEffects & Styled & { type: 'ellipse'; width: number; height: number }
export type TriangleLayer = Base & WithEffects & Styled & { type: 'triangle'; width: number; height: number }
export type StarLayer = Base & WithEffects & Styled & { type: 'star'; width: number; height: number; points?: number }
export type LineLayer = Base & WithEffects & { type: 'line'; x2: number; y2: number; stroke: string; strokeWidth: number; strokeOpacity?: number; strokeDash?: number; strokeCap?: 'butt' | 'round' | 'square'; startArrow?: boolean; endArrow?: boolean }
export type PathLayer = Base & WithEffects & Styled & { type: 'path'; width: number; height: number; d: string } // SVG path (e.g. organic blob)
export type ImageLayer = Base & WithEffects & { type: 'image'; width: number; height: number; src: string; cornerRadius?: number; cropZoom?: number; cropX?: number; cropY?: number }
export type Layer = TextLayer | RectLayer | EllipseLayer | TriangleLayer | StarLayer | LineLayer | PathLayer | ImageLayer
export type ShapeLayer = RectLayer | EllipseLayer | TriangleLayer | StarLayer | PathLayer

// Which shapes can host a clipped image (Canva image frame). Blobs (path) are excluded
// for now — clipping to an arbitrary SVG path needs a path parser in the clipFunc.
export const FRAME_SHAPE_TYPES = ['rect', 'ellipse', 'triangle', 'star'] as const
export const canBeImageFrame = (l: Layer): l is ShapeLayer => (FRAME_SHAPE_TYPES as readonly string[]).includes(l.type)
export const isImageFrame = (l: Layer): l is ShapeLayer => canBeImageFrame(l) && !!(l as { imageSrc?: string }).imageSrc

// Generate a random organic blob as an SVG path within a box of the given size.
export function blobPath(size = 320, points = 8, seed = 0): string {
  const r = size / 2, cx = r, cy = r
  const pts: [number, number][] = []
  for (let i = 0; i < points; i++) {
    const a = (i / points) * Math.PI * 2
    const rad = r * (0.62 + ((Math.sin(seed + i * 12.9898) * 43758.5453) % 1 + 1) % 1 * 0.38)
    pts.push([cx + Math.cos(a) * rad, cy + Math.sin(a) * rad])
  }
  let d = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)} `
  for (let i = 0; i < points; i++) {
    const cur = pts[i], next = pts[(i + 1) % points]
    const mx = (cur[0] + next[0]) / 2, my = (cur[1] + next[1]) / 2
    d += `Q ${cur[0].toFixed(1)} ${cur[1].toFixed(1)} ${mx.toFixed(1)} ${my.toFixed(1)} `
  }
  return d + 'Z'
}

// Axis-aligned bounding box of a layer in artboard coords (ignores rotation —
// good enough for alignment snapping). Used for element-to-element smart guides.
export function getBox(l: Layer): { id: string; x: number; y: number; w: number; h: number } {
  if (l.type === 'line') return { id: l.id, x: Math.min(l.x, l.x2), y: Math.min(l.y, l.y2), w: Math.abs(l.x2 - l.x), h: Math.abs(l.y2 - l.y) }
  if (l.type === 'text') return { id: l.id, x: l.x, y: l.y, w: l.width, h: Math.round(l.fontSize * (l.lineHeight || 1.2)) }
  const s = l as { width: number; height: number }
  return { id: l.id, x: l.x, y: l.y, w: s.width, h: s.height }
}

export function getBounds(layers: Layer[]): { x: number; y: number; w: number; h: number } | null {
  if (!layers.length) return null
  const boxes = layers.map(getBox)
  const minX = Math.min(...boxes.map(b => b.x))
  const minY = Math.min(...boxes.map(b => b.y))
  const maxX = Math.max(...boxes.map(b => b.x + b.w))
  const maxY = Math.max(...boxes.map(b => b.y + b.h))
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}

export type Artboard = { id: string; width: number; height: number; background: string; backgroundType?: 'solid' | 'linear-gradient' | 'radial-gradient'; background2?: string; backgroundAngle?: number; backgroundStops?: GradientStop[]; layers: Layer[]; x?: number; y?: number }
export type DesignSystem = {
  colorStyles?: { id: string; name: string; fill: string; fillType?: FillStyle['fillType']; fill2?: string; gradientAngle?: number; gradientStartX?: number; gradientStartY?: number; gradientEndX?: number; gradientEndY?: number; gradientStops?: GradientStop[] }[]
  textStyles?: { id: string; name: string; fontFamily?: string; fontStyle?: string; fontSize: number; fill: string; lineHeight?: number; letterSpacing?: number; textTransform?: TextLayer['textTransform']; listStyle?: TextLayer['listStyle'] }[]
  effectStyles?: { id: string; name: string; effects: LayerEffect[] }[]
  components?: { id: string; name: string; width: number; height: number; layers: Layer[] }[]
}
// appliedRunId: the design-chat run whose reply was last applied to this scene. The
// client is the only thing that writes an agent reply onto the canvas, so we stamp
// it here (persisted to disk) to recover a run that finished while the studio was
// closed — and to avoid re-applying an already-applied reply over later manual edits.
// runPendingId: the design-chat run this scene is currently AWAITING (stamped on
// send, cleared on apply). Recovery-on-open only applies a finished run when the
// scene on disk was still waiting for exactly that run — so a stale old run can
// never overwrite a scene the user has since edited.
export type Scene = { id: string; type: 'graphic' | 'deck' | 'mobile' | 'video'; title: string; artboards: Artboard[]; sessionId?: number; autoGrouped?: boolean; designSystem?: DesignSystem; appliedRunId?: number; runPendingId?: number }

// Monotonic counter — guarantees unique ids even when many layers are created in
// one synchronous pass (browsers clamp performance.now(), so a time-only id
// collides and two layers sharing an id would move/edit together).
let _uidSeq = 0
export const uid = (p = 'l') => `${p}${(++_uidSeq).toString(36)}_${Math.round(performance.now()).toString(36)}`

// Reassign any DUPLICATE layer ids (mutates). Designs created before the monotonic
// uid() fix baked colliding ids across slides, so editing one moved its twin. Run on
// load to repair them. Returns true if anything changed (so the caller can re-save).
export function dedupeSceneIds(s: Scene): boolean {
  const seen = new Set<string>(); let changed = false
  for (const a of s.artboards) for (const l of a.layers) {
    if (seen.has(l.id)) { l.id = uid(l.type[0] || 'l'); changed = true }
    seen.add(l.id)
  }
  return changed
}

const groupUid = () => uid('g')
const centerOf = (b: { x: number; y: number; w: number; h: number }) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 })
const intersects = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }, pad = 0) =>
  a.x - pad < b.x + b.w && a.x + a.w + pad > b.x && a.y - pad < b.y + b.h && a.y + a.h + pad > b.y
const containsCenter = (outer: { x: number; y: number; w: number; h: number }, inner: { x: number; y: number; w: number; h: number }, pad = 0) => {
  const c = centerOf(inner)
  return c.x >= outer.x - pad && c.x <= outer.x + outer.w + pad && c.y >= outer.y - pad && c.y <= outer.y + outer.h + pad
}
const isBackgroundLayer = (l: Layer, a: Artboard) => {
  if (!('width' in l) || !('height' in l)) return false
  return l.x <= 2 && l.y <= 2 && l.width >= a.width * 0.9 && (l.height || 0) >= a.height * 0.9
}
const layerLabel = (layers: Layer[]) => {
  const types = new Set(layers.map(l => l.type))
  const text = layers.find(l => l.type === 'text') as TextLayer | undefined
  if (types.has('text') && layers.some(l => ['rect', 'ellipse', 'path'].includes(l.type))) return text?.text?.trim()?.slice(0, 28) || 'Button'
  if (types.has('image') && types.has('text')) return text?.text?.trim()?.slice(0, 28) || 'Image block'
  if (types.has('text')) return text?.text?.trim()?.slice(0, 28) || 'Text group'
  return 'Group'
}
const assignGroup = (layers: Layer[], name?: string) => {
  if (layers.length < 2 || layers.some(l => l.groupId)) return false
  const gid = groupUid()
  const label = name || layerLabel(layers)
  for (const l of layers) {
    l.groupId = gid
    l.groupName = label
  }
  return true
}

export function autoGroupSceneLayers(scene: Scene): boolean {
  if (scene.autoGrouped) return false
  let changed = false
  for (const art of scene.artboards) {
    const candidates = art.layers.filter(l => !l.groupId && !isBackgroundLayer(l, art))
    if (candidates.length < 6) continue

    const texts = candidates.filter(l => l.type === 'text') as TextLayer[]
    const shapes = candidates.filter(l => ['rect', 'ellipse', 'path'].includes(l.type))

    for (const shape of shapes) {
      if (shape.groupId) continue
      const sb = getBox(shape)
      const insideTexts = texts.filter(t => !t.groupId && containsCenter(sb, getBox(t), Math.max(12, Math.min(sb.w, sb.h) * 0.15)))
      if (insideTexts.length) changed = assignGroup([shape, ...insideTexts], insideTexts[0]?.text?.trim()?.slice(0, 28) || 'Button') || changed
    }

    const remaining = art.layers.filter(l => !l.groupId && !isBackgroundLayer(l, art))
    const byY = [...remaining].sort((a, b) => getBox(a).y - getBox(b).y || getBox(a).x - getBox(b).x)
    const visited = new Set<string>()
    const pad = Math.max(18, Math.min(art.width, art.height) * 0.025)
    for (const seed of byY) {
      if (visited.has(seed.id) || seed.groupId) continue
      const cluster: Layer[] = [seed]
      visited.add(seed.id)
      let bounds: { x: number; y: number; w: number; h: number } = getBox(seed)
      let grew = true
      while (grew) {
        grew = false
        for (const l of byY) {
          if (visited.has(l.id) || l.groupId) continue
          const b = getBox(l)
          const nearSameRow = Math.abs(centerOf(b).y - centerOf(bounds).y) < Math.max(bounds.h, b.h, 28) * 1.25 && Math.abs(b.x - (bounds.x + bounds.w)) < pad * 3
          if (intersects(bounds, b, pad) || nearSameRow) {
            cluster.push(l)
            visited.add(l.id)
            const nb = getBounds(cluster)
            if (nb) bounds = nb
            grew = true
          }
        }
      }
      const cb = getBounds(cluster)
      const tooLarge = cb ? cb.w > art.width * 0.88 && cb.h > art.height * 0.55 : false
      if (cluster.length >= 2 && !tooLarge) changed = assignGroup(cluster) || changed
    }
  }
  if (changed) scene.autoGrouped = true
  return changed
}

// Parse an agent reply: extract the <design-scene>{json}</design-scene> block (or a
// ```json fence) and validate it has artboards. Returns null if absent/invalid.
export function parseDesignScene(text: string): Scene | null {
  if (!text) return null
  let body = ''
  const tag = text.match(/<design-scene[^>]*>([\s\S]*?)<\/design-scene>/i)
  if (tag) body = tag[1]
  else { const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i); if (fence && /"artboards"/.test(fence[1])) body = fence[1] }
  if (!body.trim()) return null
  try {
    const d = JSON.parse(body.trim())
    if (!d || !Array.isArray(d.artboards) || !d.artboards.length) return null
    // Sanitize model output so a shallowly-valid scene can't NaN-out the canvas:
    // drop non-object/typeless layers, coerce missing geometry to safe numbers.
    for (const ab of d.artboards) {
      if (!Array.isArray(ab.layers)) ab.layers = []
      ab.width = Number.isFinite(ab.width) ? ab.width : 1080
      ab.height = Number.isFinite(ab.height) ? ab.height : 1080
      if (typeof ab.background !== 'string') ab.background = '#ffffff'
      ab.layers = ab.layers.filter((l: unknown) => l && typeof l === 'object' && typeof (l as { type?: unknown }).type === 'string')
      for (const l of ab.layers as Array<Record<string, unknown>>) {
        if (!Number.isFinite(l.x)) l.x = 0
        if (!Number.isFinite(l.y)) l.y = 0
        if (l.type === 'line') { if (!Number.isFinite(l.x2)) l.x2 = (l.x as number) + 100; if (!Number.isFinite(l.y2)) l.y2 = l.y }
        else {
          if (!Number.isFinite(l.width)) l.width = 200
          if (l.type !== 'text' && !Number.isFinite(l.height)) l.height = 200
        }
        if (l.type === 'text' && !Number.isFinite(l.fontSize)) l.fontSize = 24
      }
    }
    return d as Scene
  } catch { return null }
}

// Strip the design-scene block from an assistant message so the chat shows only prose.
export const stripDesignScene = (s: string): string => s.replace(/<design-scene[^>]*>[\s\S]*?<\/design-scene>/gi, '').replace(/```(?:json)?\s*\{[\s\S]*?\}\s*```/g, '').trim()

// Build the prompt sent to the agent to generate/iterate a scene.
export function buildDesignPrompt(scene: Scene, selected: { id: string; type: string; label: string } | null, instruction: string, assets: string[] = [], visionPaths: string[] = []): string {
  const lines = [
    '⟦MODE: DESIGN⟧ You are editing a design in Proxima Design Studio; your output is the updated scene.json. A design is a JSON scene: artboards (absolute-positioned) with layers. Each artboard has {id,width,height,background(hex),layers[]} and optionally a gradient backdrop via backgroundType("solid"|"linear-gradient"|"radial-gradient"),background2(hex),backgroundAngle(deg),backgroundStops[{offset:0..1,color}] (multi-stop) — use a gradient background when it suits the direction.',
    'Layer types (all support rotation in degrees + opacity 0..1, locked, groupId/groupName, optional autoLayout metadata, and effects[] stack): text {x,y,width,height,text,fontSize,fontFamily,fontStyle("bold"|"normal"),textDecoration,textTransform("none"|"uppercase"|"lowercase"|"capitalize"),listStyle("none"|"bullet"|"number"),fill(hex),fillType("solid"|"linear-gradient"|"radial-gradient"),fill2,gradientAngle,gradientStartX,gradientStartY,gradientEndX,gradientEndY,gradientStops[{offset:0..1,color}],fillOpacity,align,verticalAlign,lineHeight,letterSpacing,textStroke,textStrokeWidth,textStrokeOpacity,shadow,shadowColor,shadowBlur,shadowOffsetX,shadowOffsetY,shadowOpacity,glow,glowColor,glowBlur,glowOpacity,effects}; rect {x,y,width,height,fill,fillType("solid"|"linear-gradient"|"radial-gradient"),fill2,gradientAngle,gradientStartX,gradientStartY,gradientEndX,gradientEndY,gradientStops,fillOpacity,cornerRadius,cornerRadiusTL,cornerRadiusTR,cornerRadiusBR,cornerRadiusBL,stroke,strokeWidth,strokeOpacity,strokeDash,strokeCap,strokeJoin,strokePosition,shadow(bool),effects}; ellipse/triangle support the same fill/stroke/effects; star adds points(integer, e.g. 5 or 6); path adds d(an SVG path string, e.g. an organic blob "M..Z") plus the same fill/stroke/effects; any rect/ellipse/triangle/star can instead be an IMAGE FRAME (Canva-style) by setting imageSrc (a real image path or "gen:<prompt>") + optional imageCropX/imageCropY(0..100 reposition)/imageCropZoom(1..4) — the image is clipped to the shape outline; line {x,y,x2,y2,stroke,strokeWidth,strokeOpacity,strokeDash,strokeCap,startArrow,endArrow,effects}; image {x,y,width,height,src,cornerRadius,cropZoom(1..4 zoom-in crop),cropX(0..100),cropY(0..100),effects}. autoLayout fields on grouped layers: autoLayout true, layoutDirection "horizontal"|"vertical", layoutGap, layoutPadding, layoutAlign "start"|"center"|"end". effects entries are {type:"drop-shadow"|"inner-shadow"|"glow"|"layer-blur"|"background-blur",color,opacity,blur,spread,offsetX,offsetY}.',
    'These are composable — stack SEVERAL entries in one layer\'s effects[] (e.g. a drop-shadow + a glow), and combine them freely with gradient fills, fillOpacity, strokes, and corner radii for depth and polish. Reach past flat solids: layered gradients, translucent panels, soft shadows, subtle glows, blurred backdrops.',
    'Act like an art director. Do not default to flat text + button + basic shapes. Build a complete composition with visual hierarchy, a clear focal point, spacing, accents, and depth. Eyebrows, CTAs, buttons, and cards are optional tools, not mandatory defaults.',
    'Non-negotiable craft rules: (1) CONTRAST — every text layer must sit on a background it clearly reads against (aim for a WCAG AA-level luminance gap; if text overlaps a busy image, add a scrim/overlay panel or text-stroke/shadow so it stays legible). The focal element must out-contrast everything around it. (2) FONT PAIRING — use at most two font families and make them intentionally compatible: one display/heading + one clean body (e.g. Playfair Display + Inter, Bebas Neue + Lora, Poppins used alone in two weights). Never mix two loud display fonts; match their mood to the chosen direction. Establish a clear type scale (headline ≫ subhead > body > caption). (3) ELEMENT INTEGRATION — everything must feel like one designed system, not floating parts: align layers to a consistent margin/grid, keep even spacing rhythm, let shapes/images/text overlap and relate (shared edges, consistent corner radii, a unifying accent colour), and give the artboard breathing room at the edges. No orphaned element sitting alone in dead space.',
    'Choose a visual direction that fits the request: glassmorphism (translucent panels, light strokes, soft shadow/glow), Apple-like clean (white space, subtle depth, restrained type), premium editorial (serif/sans pairing, photo/texture, fine rules), neon/cyber (dark surface, glows, rim-lit image), playful creator (bold color, stickers, expressive type), clean SaaS (quiet layout, UI/product visual).',
    'Use editable polish where useful: text shadow/glow, shape shadow, opacity, translucent panels, fine strokes, overlapping layers, organic path/blob accents, and generated hero images. Keep text as real editable text layers (never bake text into images). Keep each artboard\'s width/height unless asked to change it. Coordinates are within the artboard.',
    'If the request implies a visual subject (product, food, person, venue, event, mood scene, illustration, campaign visual, hero object, etc.), include at least one image layer with src "gen:<specific visual prompt>" by default unless the user explicitly wants type-only or no image. Use it as a hero, background, product shot, illustration, or texture.',
    'To include a PHOTO or ILLUSTRATION, add an image layer whose src is "gen:<a short image-generation prompt>" — e.g. "gen:a green plastic bottle, studio product shot, soft shadow, white background". The studio will generate the real image with AI and swap it in. NEVER invent file paths or URLs for images; only use gen: prompts or one of the existing project assets listed below.',
    ...(assets.length ? [
      '',
      'Existing project assets — the user has already added these to the library (logos, elements, photos, etc.). Place one directly by setting an image layer\'s src to its exact path (do NOT use gen: for these, they already exist). Use them when the request refers to them:',
      ...assets.map(a => `- ${a}`),
    ] : []),
    '',
    'Current scene:',
    '```json',
    JSON.stringify(scene),
    '```',
  ]
  if (selected) lines.push('', `The user has SELECTED this element — apply changes to it unless told otherwise: ${selected.type} "${selected.label}" (id: ${selected.id}).`)
  if (visionPaths.length) lines.push('', `Attached images you can SEE — look at them to compose accurately (match colours, framing, and content): ${visionPaths.map(p => p.split('/').pop()).join(', ')}.`)
  lines.push('', `User request: ${instruction}`, '',
    'Return a one-sentence summary, then the COMPLETE updated scene (keep the same "id") as:',
    '<design-scene>{ ...full scene json... }</design-scene>')
  // Marker consumed by the worker: it reads these project files and attaches them as
  // image content blocks (vision), then strips this line before the model sees it.
  if (visionPaths.length) lines.push(`⟦VISION:${visionPaths.join('|')}⟧`)
  return lines.join('\n')
}

// A starter scene so the editor is usable before any AI generation.
export function sampleScene(): Scene {
  return {
    id: 'sample', type: 'graphic', title: 'Untitled design',
    artboards: [{
      id: 'a1', width: 1080, height: 1080, background: '#0b1020',
      layers: [
        { id: 't1', type: 'rect', x: 0, y: 0, width: 1080, height: 1080, fill: '#0b1020' },
        { id: 't2', type: 'rect', x: 64, y: 760, width: 420, height: 92, fill: '#3b82f6', cornerRadius: 46 },
        { id: 't3', type: 'text', x: 64, y: 150, width: 880, text: 'Your big idea, designed.', fontSize: 104, fontFamily: 'Inter', fontStyle: 'bold', fill: '#ffffff', align: 'left', lineHeight: 1.05 },
        { id: 't4', type: 'text', x: 64, y: 470, width: 760, text: 'AI drafts it as editable layers — you fix the copy, tweak the look, ship it.', fontSize: 40, fontFamily: 'Inter', fill: '#aab4c5', align: 'left', lineHeight: 1.3 },
        { id: 't5', type: 'text', x: 96, y: 782, width: 360, text: 'Get started', fontSize: 40, fontFamily: 'Inter', fontStyle: 'bold', fill: '#ffffff', align: 'left' },
      ],
    }],
  }
}

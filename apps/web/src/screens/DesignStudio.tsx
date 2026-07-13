import React from 'react'
import { Stage, Layer as KLayer, Group, Rect, Text, Image as KImage, Ellipse, Line, Star, Path, Transformer, Arrow } from 'react-konva'
import type Konva from 'konva'
import { uid, blobPath, getBox, getBounds, dedupeSceneIds, autoGroupSceneLayers, parseDesignScene, stripDesignScene, buildDesignPrompt, type Scene, type Artboard, type DesignSystem, type Layer, type TextLayer, type RectLayer, type EllipseLayer, type TriangleLayer, type StarLayer, type LineLayer, type PathLayer, type ImageLayer, type FillStyle, type LayerEffect } from '../components/design/scene'
import { createSession, listMessages, deleteSession } from '../api/sessions'
import { createRun } from '../api/runs'
import { useRunStream } from '../hooks/useRunStream'
import { confirmDialog } from '../components/ui/Dialog'
import { BackButton } from '../components/ui/BackButton'
import { SURFACES, surfaceTemplates, sceneFromTemplate, type Surface, type Template } from '../components/design/templates'
import { projectFs } from '../api/fsAdapter'
import { fileUrl, uploadFile, genDesignImage, deletePath } from '../api/files'
import { MessageContent } from '../components/chat/MessageContent'
import { Composer } from '../components/chat/Composer'
import { QuestionForm } from '../components/chat/QuestionForm'
import { splitOnQuestionForms } from '../components/chat/questionForm'
import { getImageGenSettings } from '../api/settings'
import { MiniPreview, cssTextShadow } from '../components/design/MiniPreview'
import { ColorInput } from '../components/design/ColorInput'
import { Dropdown, type DropdownOption } from '../components/ui/Dropdown'
import type { Project, RunEvent } from '../types'

const GAP = 96 // space between artboards on the infinite canvas
const ARTBOARD_PRESETS = [
  { id: 'ig-post', label: 'Instagram Post', w: 1080, h: 1080 },
  { id: 'ig-story', label: 'Instagram Story', w: 1080, h: 1920 },
  { id: 'youtube-thumb', label: 'YouTube Thumbnail', w: 1280, h: 720 },
  { id: 'x-post', label: 'X / Twitter Post', w: 1600, h: 900 },
  { id: 'poster-45', label: 'Poster 4:5', w: 1080, h: 1350 },
  { id: 'deck-169', label: 'Presentation 16:9', w: 1920, h: 1080 },
  { id: 'a4-p', label: 'A4 Portrait', w: 794, h: 1123 },
  { id: 'a4-l', label: 'A4 Landscape', w: 1123, h: 794 },
  { id: 'letter-p', label: 'Letter Portrait', w: 816, h: 1056 },
  { id: 'letter-l', label: 'Letter Landscape', w: 1056, h: 816 },
  { id: 'square', label: 'Square PDF', w: 1080, h: 1080 },
  { id: 'tall-report', label: 'Tall Report', w: 1080, h: 1440 },
] as const
type ArtboardPreset = typeof ARTBOARD_PRESETS[number]
type ArtboardPresetId = typeof ARTBOARD_PRESETS[number]['id']
const SOCIAL_ARTBOARD_PRESETS = ARTBOARD_PRESETS.slice(0, 6)
const PDF_ARTBOARD_PRESETS = ARTBOARD_PRESETS.slice(6)
const artboardPresetValue = (w: number, h: number): ArtboardPresetId | 'custom' => ARTBOARD_PRESETS.find(p => p.w === w && p.h === h)?.id || 'custom'

function useImg(src: string): HTMLImageElement | undefined {
  const [img, setImg] = React.useState<HTMLImageElement>()
  React.useEffect(() => { if (!src) return; const i = new window.Image(); i.crossOrigin = 'anonymous'; i.src = src; i.onload = () => setImg(i) }, [src])
  return img
}

const BLOB_BASE = 320 // coordinate space blobPath() is generated in
// Fonts preloaded in index.html (Google Fonts) — keep this list in sync with that <link>.
const FONTS = ['Inter', 'Poppins', 'Montserrat', 'Raleway', 'Manrope', 'DM Sans', 'Space Grotesk', 'Sora', 'Outfit', 'Nunito', 'Oswald', 'Bebas Neue', 'Anton', 'Archivo Black', 'Righteous', 'Playfair Display', 'Lora', 'Fraunces', 'Merriweather', 'Roboto Slab', 'Abril Fatface', 'Caveat', 'Dancing Script', 'Pacifico', 'Lobster', 'Permanent Marker', 'JetBrains Mono']
type AnyL = Record<string, number | string | boolean | undefined>
type ProjectComponent = NonNullable<DesignSystem['components']>[number]
const strokeOf = (l: AnyL) => l.stroke && (l.strokeWidth as number | undefined) !== 0 ? { stroke: l.stroke as string, strokeWidth: (l.strokeWidth as number | undefined) ?? 2, strokeScaleEnabled: false, opacity: l.strokeOpacity as number | undefined, dash: l.strokeDash ? [l.strokeDash as number, l.strokeDash as number] : undefined, lineCap: l.strokeCap as 'butt' | 'round' | 'square' | undefined, lineJoin: l.strokeJoin as 'miter' | 'round' | 'bevel' | undefined } : {}
const shadowOf = (l: AnyL) => l.shadow ? { shadowColor: '#000', shadowBlur: 24, shadowOpacity: 0.35, shadowOffsetY: 6 } : {}
const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n))
const firstEffect = (l: { effects?: LayerEffect[] }, types: LayerEffect['type'][]) => l.effects?.find(f => types.includes(f.type))
const effectShadowOf = (l: { effects?: LayerEffect[] }) => {
  const fx = firstEffect(l, ['drop-shadow', 'inner-shadow', 'glow'])
  if (!fx) return {}
  return { shadowColor: fx.color || (fx.type === 'glow' ? '#60a5fa' : '#000000'), shadowBlur: fx.blur ?? (fx.type === 'glow' ? 24 : 16), shadowOpacity: fx.opacity ?? (fx.type === 'glow' ? 0.65 : 0.35), shadowOffsetX: fx.type === 'glow' ? 0 : fx.offsetX ?? 0, shadowOffsetY: fx.type === 'glow' ? 0 : fx.offsetY ?? 8 }
}
const cornerRadiusOf = (l: { cornerRadius?: number; cornerRadiusTL?: number; cornerRadiusTR?: number; cornerRadiusBR?: number; cornerRadiusBL?: number }) =>
  [l.cornerRadiusTL, l.cornerRadiusTR, l.cornerRadiusBR, l.cornerRadiusBL].some(v => v != null)
    ? [l.cornerRadiusTL ?? l.cornerRadius ?? 0, l.cornerRadiusTR ?? l.cornerRadius ?? 0, l.cornerRadiusBR ?? l.cornerRadius ?? 0, l.cornerRadiusBL ?? l.cornerRadius ?? 0]
    : (l.cornerRadius ?? 0)
const fillOf = (l: FillStyle & { width?: number; height?: number }) => {
  const stops = (l.gradientStops?.length ? [...l.gradientStops].sort((a, b) => a.offset - b.offset) : [{ offset: 0, color: l.fill }, { offset: 1, color: l.fill2 || l.fill }]).flatMap(s => [clamp(s.offset, 0, 1), s.color])
  if (l.fillType === 'linear-gradient') {
    const w = l.width || 1, h = l.height || 1, a = ((l.gradientAngle ?? 0) - 90) * Math.PI / 180
    const cx = w / 2, cy = h / 2, r = Math.hypot(w, h) / 2
    return {
      fillPriority: 'linear-gradient',
      fillLinearGradientStartPoint: { x: l.gradientStartX ?? cx - Math.cos(a) * r, y: l.gradientStartY ?? cy - Math.sin(a) * r },
      fillLinearGradientEndPoint: { x: l.gradientEndX ?? cx + Math.cos(a) * r, y: l.gradientEndY ?? cy + Math.sin(a) * r },
      fillLinearGradientColorStops: stops,
      opacity: l.fillOpacity ?? undefined,
    }
  }
  if (l.fillType === 'radial-gradient') return {
    fillPriority: 'radial-gradient',
    fillRadialGradientStartPoint: { x: l.gradientStartX ?? (l.width || 1) / 2, y: l.gradientStartY ?? (l.height || 1) / 2 },
    fillRadialGradientStartRadius: 0,
    fillRadialGradientEndPoint: { x: l.gradientEndX ?? (l.width || 1) / 2, y: l.gradientEndY ?? (l.height || 1) / 2 },
    fillRadialGradientEndRadius: Math.max(Math.abs((l.gradientEndX ?? (l.width || 1) / 2) - (l.gradientStartX ?? (l.width || 1) / 2)), Math.abs((l.gradientEndY ?? (l.height || 1) / 2) - (l.gradientStartY ?? (l.height || 1) / 2)), Math.max(l.width || 1, l.height || 1) / 2),
    fillRadialGradientColorStops: stops,
    opacity: l.fillOpacity ?? undefined,
  }
  return { fillPriority: 'color', fill: l.fill, opacity: l.fillOpacity ?? undefined }
}
// Map an artboard's background (solid or gradient) onto the shared fillOf() shape so
// the canvas Rect paints gradients the same way layer fills do.
const artboardFill = (a: Artboard): FillStyle & { width: number; height: number } =>
  ({ fill: a.background, fillType: a.backgroundType, fill2: a.background2, gradientAngle: a.backgroundAngle, gradientStops: a.backgroundStops, width: a.width, height: a.height })
const textShadowOf = (t: TextLayer) => t.shadow ? {
  shadowColor: t.shadowColor || '#000000',
  shadowBlur: t.shadowBlur ?? 12,
  shadowOpacity: t.shadowOpacity ?? 0.35,
  shadowOffsetX: t.shadowOffsetX ?? 0,
  shadowOffsetY: t.shadowOffsetY ?? 8,
} : {}
const textStyleOf = (t: TextLayer) => ({
  width: t.width, height: t.height, text: textDisplayValue(t), fontSize: t.fontSize, fontFamily: t.fontFamily || 'Inter', fontStyle: t.fontStyle || 'normal',
  textDecoration: t.textDecoration || '', fill: t.fill, align: t.align || 'left', verticalAlign: t.verticalAlign || 'top', lineHeight: t.lineHeight || 1.2, letterSpacing: t.letterSpacing || 0,
})
const textValue = (t: TextLayer) => {
  if (t.textTransform === 'uppercase') return t.text.toUpperCase()
  if (t.textTransform === 'lowercase') return t.text.toLowerCase()
  if (t.textTransform === 'capitalize') return t.text.replace(/\b\w/g, c => c.toUpperCase())
  return t.text
}
const textDisplayValue = (t: TextLayer) => {
  const base = textValue(t)
  if (!t.listStyle || t.listStyle === 'none') return base
  return base.split('\n').map((line, i) => line.trim() ? `${t.listStyle === 'number' ? `${i + 1}.` : '•'} ${line}` : line).join('\n')
}
function NumberAdjuster({ label, value, min, max, step = 1, onChange }: { label: string; value: number; min: number; max: number; step?: number; onChange: (value: number) => void }) {
  const safe = Number.isFinite(value) ? value : min
  const apply = (next: number) => onChange(clamp(next, min, max))
  return <label className="ds-number-adjust">{label}
    <input type="number" min={min} max={max} step={step} value={safe} onChange={e => apply(Number(e.target.value))} />
    <input type="range" min={min} max={max} step={step} value={clamp(safe, min, max)} onChange={e => apply(Number(e.target.value))} />
  </label>
}
function EffectStackEditor({ effects = [], onChange }: { effects?: LayerEffect[]; onChange: (effects: LayerEffect[]) => void }) {
  const add = (type: LayerEffect['type']) => onChange([...effects, { id: uid('fx'), type, color: type === 'glow' ? '#60a5fa' : '#000000', opacity: type === 'glow' ? 0.6 : 0.35, blur: type.includes('blur') ? 12 : 18, spread: 0, offsetX: 0, offsetY: type === 'drop-shadow' ? 8 : 0 }])
  const patch = (i: number, p: Partial<LayerEffect>) => onChange(effects.map((fx, ix) => ix === i ? { ...fx, ...p } : fx))
  const move = (i: number, d: -1 | 1) => {
    const j = i + d
    if (j < 0 || j >= effects.length) return
    const next = [...effects]; [next[i], next[j]] = [next[j], next[i]]; onChange(next)
  }
  return <div className="ds-effects">
    <div className="ds-btn-row">
      {(['drop-shadow', 'inner-shadow', 'glow', 'layer-blur', 'background-blur'] as const).map(t => <button key={t} className="ghost-button sm" onClick={() => add(t)}>{t.replace('-', ' ')}</button>)}
    </div>
    {!effects.length && <p className="ds-tip muted">No effects yet.</p>}
    {effects.map((fx, i) => <div className="ds-effect-card" key={fx.id || i}>
      <div className="ds-effect-head"><strong>{fx.type.replace('-', ' ')}</strong><span><button onClick={() => move(i, -1)}>↑</button><button onClick={() => move(i, 1)}>↓</button><button className="danger" onClick={() => onChange(effects.filter((_, ix) => ix !== i))}>Delete</button></span></div>
      {!fx.type.includes('blur') && <div className="ds-row2"><label>Color<ColorInput value={fx.color || '#000000'} onChange={v => patch(i, { color: v })} /></label><NumberAdjuster label="Opacity" value={fx.opacity ?? 0.35} min={0} max={1} step={0.01} onChange={v => patch(i, { opacity: v })} /></div>}
      <div className="ds-row2"><NumberAdjuster label="Blur" value={fx.blur ?? 12} min={0} max={200} step={1} onChange={v => patch(i, { blur: v })} /><NumberAdjuster label="Spread" value={fx.spread ?? 0} min={-80} max={120} step={1} onChange={v => patch(i, { spread: v })} /></div>
      {!fx.type.includes('blur') && <div className="ds-row2"><NumberAdjuster label="Offset X" value={fx.offsetX ?? 0} min={-160} max={160} step={1} onChange={v => patch(i, { offsetX: v })} /><NumberAdjuster label="Offset Y" value={fx.offsetY ?? 0} min={-160} max={160} step={1} onChange={v => patch(i, { offsetY: v })} /></div>}
    </div>)}
  </div>
}
function GradientEditor({ fill, onChange, width, height }: { fill: FillStyle; onChange: (patch: Partial<FillStyle>) => void; width: number; height: number }) {
  const stops = fill.gradientStops?.length ? fill.gradientStops : [{ id: uid('gs'), offset: 0, color: fill.fill }, { id: uid('gs'), offset: 1, color: fill.fill2 || fill.fill }]
  const patchStop = (i: number, patch: Partial<{ offset: number; color: string }>) => onChange({ gradientStops: stops.map((s, ix) => ix === i ? { ...s, ...patch, offset: clamp(patch.offset ?? s.offset, 0, 1) } : s) })
  const addStop = () => onChange({ gradientStops: [...stops, { id: uid('gs'), offset: 0.5, color: fill.fill2 || fill.fill }].sort((a, b) => a.offset - b.offset) })
  const removeStop = (i: number) => { if (stops.length > 2) onChange({ gradientStops: stops.filter((_, ix) => ix !== i) }) }
  return <div className="ds-gradient-editor">
    <div className="ds-row2"><NumberAdjuster label="Start X" value={fill.gradientStartX ?? 0} min={-width} max={width * 2} step={1} onChange={v => onChange({ gradientStartX: v })} /><NumberAdjuster label="Start Y" value={fill.gradientStartY ?? 0} min={-height} max={height * 2} step={1} onChange={v => onChange({ gradientStartY: v })} /></div>
    <div className="ds-row2"><NumberAdjuster label="End X" value={fill.gradientEndX ?? width} min={-width} max={width * 2} step={1} onChange={v => onChange({ gradientEndX: v })} /><NumberAdjuster label="End Y" value={fill.gradientEndY ?? height} min={-height} max={height * 2} step={1} onChange={v => onChange({ gradientEndY: v })} /></div>
    <div className="ds-gradient-stops">
      {stops.map((s, i) => <div className="ds-gradient-stop" key={s.id || i}>
        <ColorInput value={s.color} onChange={v => patchStop(i, { color: v })} />
        <NumberAdjuster label={`${Math.round(s.offset * 100)}%`} value={Math.round(s.offset * 100)} min={0} max={100} step={1} onChange={v => patchStop(i, { offset: v / 100 })} />
        <button className="ghost-button sm danger" disabled={stops.length <= 2} onClick={() => removeStop(i)}>Delete</button>
      </div>)}
    </div>
    <button className="ghost-button sm" onClick={addStop}>Add color stop</button>
  </div>
}
function PropertySection({ title, defaultOpen = false, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  return <details className="ds-prop-section" open={defaultOpen}>
    <summary>{title}</summary>
    <div className="ds-prop-body">{children}</div>
  </details>
}
const imageCrop = (img: HTMLImageElement | undefined, layer: ImageLayer) => {
  if (!img?.naturalWidth || !img?.naturalHeight) return undefined
  const zoom = clamp(layer.cropZoom || 1, 1, 4)
  const frameRatio = layer.width / Math.max(1, layer.height)
  const sourceRatio = img.naturalWidth / img.naturalHeight
  const baseW = sourceRatio > frameRatio ? img.naturalHeight * frameRatio : img.naturalWidth
  const baseH = sourceRatio > frameRatio ? img.naturalHeight : img.naturalWidth / frameRatio
  const width = baseW / zoom
  const height = baseH / zoom
  const x = (img.naturalWidth - width) * clamp(layer.cropX ?? 50, 0, 100) / 100
  const y = (img.naturalHeight - height) * clamp(layer.cropY ?? 50, 0, 100) / 100
  return { x, y, width, height }
}

function LayerNode({ layer, onRef, onSelect, onChange, onLiveChange, resolveSrc, onContext, onEdit, aw, ah, snapT, onGuides, boxes, multi, onGroupMove, onGroupEnd, onGroupStart, onAltClone, editing, cropEditing, shiftRef, onGroupSnap, mobileSnap, panMode }: { layer: Layer; onRef: (n: Konva.Node | null) => void; onSelect: (additive: boolean) => void; onChange: (patch: Partial<Layer>) => void; onLiveChange?: (patch: Partial<Layer>) => void; resolveSrc?: (s: string) => string; onContext?: (x: number, y: number) => void; onEdit?: () => void; aw?: number; ah?: number; snapT?: number; onGuides?: (lines: { axis: 'x' | 'y'; pos: number }[]) => void; boxes?: { id: string; x: number; y: number; w: number; h: number }[]; multi?: boolean; onGroupMove?: (dx: number, dy: number) => void; onGroupEnd?: () => void; onGroupStart?: () => void; onAltClone?: () => void; editing?: boolean; cropEditing?: boolean; shiftRef?: React.MutableRefObject<boolean>; onGroupSnap?: (dx: number, dy: number) => { cx: number; cy: number }; mobileSnap?: boolean; panMode?: boolean }) {
  const l = layer as unknown as AnyL
  const dragLast = React.useRef<{ x: number; y: number } | null>(null)
  const lastGuides = React.useRef<string>('') // dedupe guide emits — only setState when the lines change (else every frame re-renders and resets the dragged node = flicker)
  const emitGuides = (lines: { axis: 'x' | 'y'; pos: number }[]) => { if (!onGuides) return; const k = lines.map(g => g.axis + g.pos).join('|'); if (k !== lastGuides.current) { lastGuides.current = k; onGuides(lines) } }
  const textTransformPatch = (n: Konva.Node) => {
    const sx = n.scaleX(), sy = n.scaleY()
    const p: AnyL = { x: Math.round(n.x()), y: Math.round(n.y()), rotation: Math.round(n.rotation()) }
    const baseH = ((l.height as number | undefined) || (l.fontSize as number) * (l.lineHeight as number || 1.2))
    const width = Math.max(8, Math.round((l.width as number) * sx))
    const height = Math.max(8, Math.round(baseH * sy))
    p.width = width
    p.height = height
    if (Math.abs(sx - 1) > 0.02 && Math.abs(sy - 1) > 0.02) p.fontSize = Math.max(4, Math.round((l.fontSize as number) * sy))
    return p as Partial<Layer>
  }
  const common = {
    ref: onRef as never, draggable: !cropEditing && !layer.locked && !panMode, rotation: (layer.rotation || 0), opacity: layer.opacity ?? 1,
    // mousedown selects (single) only without a modifier, so a modifier+drag is free
    // to mean duplicate and a modifier+click (no drag) means multi-select (onClick).
    onMouseDown: (e: Konva.KonvaEventObject<MouseEvent>) => { if (layer.locked || panMode) return; if (!(e.evt.metaKey || e.evt.ctrlKey || e.evt.altKey)) onSelect(false) },
    onClick: (e: Konva.KonvaEventObject<MouseEvent>) => { if (layer.locked || panMode) return; if (e.evt.metaKey || e.evt.ctrlKey) onSelect(true) },
    onTap: () => { if (!layer.locked && !panMode) onSelect(false) },
    onDblClick: layer.locked || panMode ? undefined : onEdit, onDblTap: layer.locked || panMode ? undefined : onEdit,
    onContextMenu: (e: Konva.KonvaEventObject<PointerEvent>) => { e.evt.preventDefault(); if (layer.locked || panMode) return; onSelect(false); onContext?.(e.evt.clientX, e.evt.clientY) },
    // Duplicate-drag: ⌘/Ctrl (Linux/Win) or Alt (Mac) held at drag start. Alt-drag is
    // grabbed by the Linux window manager, so ⌘/Ctrl is the portable trigger.
    onDragStart: (e: Konva.KonvaEventObject<DragEvent>) => { const m = e.evt as MouseEvent; if (m?.altKey || m?.metaKey || m?.ctrlKey) onAltClone?.(); if (multi) onGroupStart?.(); dragLast.current = { x: e.target.x(), y: e.target.y() } },
    // Multi-select + Shift = snap the GROUP as a unit. The parent computes ONE
    // correction (from the selection bbox + this node's delta, vs the artboard AND
    // other elements) and emits guide lines; every selected node applies the same
    // correction → rigid + snapped. Runs before Konva sets the position.
    dragBoundFunc: function (this: Konva.Node, pos: { x: number; y: number }) {
      if (!multi || (!shiftRef?.current && !mobileSnap) || !onGroupSnap) return pos
      const group = this.getParent(); if (!group) return pos
      const at = group.getAbsoluteTransform()
      const lp = at.copy().invert().point(pos)
      const c = onGroupSnap(lp.x - (l.x as number), lp.y - (l.y as number))
      return at.point({ x: lp.x + c.cx, y: lp.y + c.cy })
    },
    onDragMove: (e: Konva.KonvaEventObject<DragEvent>) => {
      // Multi-select: the Konva Transformer already drags every selected node together
      // (its _proxyDrag). Do NOTHING here or we'd double-move them = jitter.
      if (multi) return
      const n = e.target
      const snap = !!(e.evt as MouseEvent)?.shiftKey || !!mobileSnap // Shift (desktop) or snap-toggle (touch)
      if (aw && ah && onGuides && snap) {
        const w = ('width' in l ? l.width as number : 0); const h = ('height' in l ? l.height as number : 0); const T = snapT || 6
        let x = n.x(), y = n.y(); const lines: { axis: 'x' | 'y'; pos: number }[] = []
        const peers = (boxes || []).filter(b => b.id !== (layer.id))
        const xTargets = [0, aw / 2, aw, ...peers.flatMap(b => [b.x, b.x + b.w / 2, b.x + b.w])]
        const yTargets = [0, ah / 2, ah, ...peers.flatMap(b => [b.y, b.y + b.h / 2, b.y + b.h])]
        let bx: { d: number; nx: number; pos: number } | null = null
        for (const t of xTargets) for (const ed of [x, x + w / 2, x + w]) { const d = Math.abs(ed - t); if (d < T && (!bx || d < bx.d)) bx = { d, nx: x + (t - ed), pos: t } }
        if (bx) { x = bx.nx; lines.push({ axis: 'x', pos: bx.pos }) }
        let by: { d: number; ny: number; pos: number } | null = null
        for (const t of yTargets) for (const ed of [y, y + h / 2, y + h]) { const d = Math.abs(ed - t); if (d < T && (!by || d < by.d)) by = { d, ny: y + (t - ed), pos: t } }
        if (by) { y = by.ny; lines.push({ axis: 'y', pos: by.pos }) }
        n.x(x); n.y(y); emitGuides(lines)
      } else emitGuides([])
    },
    onDragEnd: (e: Konva.KonvaEventObject<DragEvent>) => {
      lastGuides.current = ''; onGuides?.([]); dragLast.current = null
      if (multi && onGroupEnd) onGroupEnd()
      else {
        const nx = Math.round(e.target.x()), ny = Math.round(e.target.y())
        const patch: AnyL = { x: nx, y: ny }
        if (layer.type === 'line') { patch.x2 = Math.round(layer.x2 + (nx - layer.x)); patch.y2 = Math.round(layer.y2 + (ny - layer.y)) }
        onChange(patch as Partial<Layer>)
      }
    },
    onTransform: (e: Konva.KonvaEventObject<Event>) => {
      if (layer.type !== 'text' || !onLiveChange) return
      const n = e.target
      const p = textTransformPatch(n)
      n.scaleX(1); n.scaleY(1)
      onLiveChange(p)
    },
    onTransformEnd: (e: Konva.KonvaEventObject<Event>) => {
      const n = e.target; const sx = n.scaleX(); const sy = n.scaleY(); n.scaleX(1); n.scaleY(1)
      const p: AnyL = { x: Math.round(n.x()), y: Math.round(n.y()), rotation: Math.round(n.rotation()) }
      if (layer.type === 'line') {
        const rot = n.rotation() * Math.PI / 180
        const dx = (layer.x2 - layer.x) * sx
        const dy = (layer.y2 - layer.y) * sy
        p.rotation = 0
        p.x2 = Math.round(n.x() + dx * Math.cos(rot) - dy * Math.sin(rot))
        p.y2 = Math.round(n.y() + dx * Math.sin(rot) + dy * Math.cos(rot))
        onChange(p as Partial<Layer>); return
      }
      if (layer.type === 'text') {
        // Side handle (horizontal only) → resize the wrapping width, font unchanged.
        // Corner handle (both axes) → scale the font too, like Canva. Committed on
        // release so there's no per-frame reflow flicker during the drag.
        const corner = Math.abs(sx - 1) > 0.02 && Math.abs(sy - 1) > 0.02
        p.width = Math.max(8, Math.round((l.width as number) * sx))
        p.height = Math.max(8, Math.round(((l.height as number | undefined) || (l.fontSize as number) * 1.4) * sy))
        if (corner) p.fontSize = Math.max(4, Math.round((l.fontSize as number) * sy))
        onChange(p as Partial<Layer>); return
      }
      if ('width' in l) p.width = Math.max(8, Math.round((l.width as number) * sx))
      if ('height' in l) p.height = Math.max(8, Math.round((l.height as number) * sy))
      onChange(p as Partial<Layer>)
    },
  }
  const img = useImg(layer.type === 'image' ? (resolveSrc ? resolveSrc(layer.src) : layer.src) : '')
  switch (layer.type) {
    case 'text': return <Group {...common} x={layer.x} y={layer.y} opacity={editing ? 0 : (layer.opacity ?? 1)}>
      {layer.glow && <Text {...textStyleOf(layer)} fill={layer.glowColor || layer.fill} opacity={layer.glowOpacity ?? 0.55} shadowColor={layer.glowColor || layer.fill} shadowBlur={layer.glowBlur ?? 18} shadowOpacity={layer.glowOpacity ?? 0.65} listening={false} />}
      <Text {...textStyleOf(layer)} {...fillOf(layer)} stroke={layer.textStroke} strokeWidth={layer.textStrokeWidth ?? 0} strokeScaleEnabled={false} opacity={(layer.opacity ?? 1) * (layer.fillOpacity ?? 1)} {...textShadowOf(layer)} {...effectShadowOf(layer)} />
    </Group>
    case 'rect': return <Rect {...common} x={layer.x} y={layer.y} width={layer.width} height={layer.height} {...fillOf(layer)} cornerRadius={cornerRadiusOf(layer)} {...strokeOf(l)} {...shadowOf(l)} {...effectShadowOf(layer)} />
    case 'ellipse': return <Group {...common} x={layer.x} y={layer.y}><Ellipse x={layer.width / 2} y={layer.height / 2} radiusX={layer.width / 2} radiusY={layer.height / 2} {...fillOf(layer)} {...strokeOf(l)} {...shadowOf(l)} {...effectShadowOf(layer)} /></Group>
    case 'triangle': return <Line {...common} x={layer.x} y={layer.y} points={[layer.width / 2, 0, layer.width, layer.height, 0, layer.height]} closed {...fillOf(layer)} {...strokeOf(l)} {...shadowOf(l)} {...effectShadowOf(layer)} />
    case 'star': return <Group {...common} x={layer.x} y={layer.y}><Star x={layer.width / 2} y={layer.height / 2} numPoints={layer.points || 5} innerRadius={Math.min(layer.width, layer.height) / 4} outerRadius={Math.min(layer.width, layer.height) / 2} {...fillOf(layer)} {...strokeOf(l)} {...effectShadowOf(layer)} /></Group>
    case 'line': {
      const P = layer.startArrow || layer.endArrow ? Arrow : Line
      return <P {...common} rotation={0} x={layer.x} y={layer.y} points={[0, 0, layer.x2 - layer.x, layer.y2 - layer.y]} stroke={layer.stroke} strokeWidth={layer.strokeWidth} opacity={(layer.opacity ?? 1) * (layer.strokeOpacity ?? 1)} dash={layer.strokeDash ? [layer.strokeDash, layer.strokeDash] : undefined} lineCap={layer.strokeCap || 'round'} pointerAtBeginning={!!layer.startArrow} pointerAtEnding={!!layer.endArrow} pointerLength={Math.max(12, layer.strokeWidth * 3)} pointerWidth={Math.max(10, layer.strokeWidth * 2.4)} />
    }
    case 'path': return <Group {...common} x={layer.x} y={layer.y}><Path data={layer.d} {...fillOf(layer)} scaleX={layer.width / BLOB_BASE} scaleY={layer.height / BLOB_BASE} {...strokeOf(l)} {...effectShadowOf(layer)} /></Group>
    default: {
      // A gen:-pending image renders as a labeled placeholder instead of an
      // invisible box, so "AI is still making this" is visible on the canvas itself.
      if (/^gen:/i.test(layer.src)) return <Group {...common} x={layer.x} y={layer.y}>
        <Rect width={layer.width} height={layer.height} fill="rgba(148,163,184,0.14)" stroke="#94a3b8" strokeWidth={1.5} dash={[8, 6]} cornerRadius={cornerRadiusOf(layer)} />
        <Text width={layer.width} height={layer.height} align="center" verticalAlign="middle" text="✦ Generating image…" fontSize={Math.max(12, Math.min(26, layer.width / 14))} fill="#64748b" listening={false} />
      </Group>
      return <KImage {...common} x={layer.x} y={layer.y} width={layer.width} height={layer.height} image={img} crop={imageCrop(img, layer)} cornerRadius={cornerRadiusOf(layer)} {...effectShadowOf(layer)} />
    }
  }
}

// Strip the "(private)" suffix some project names carry, mirroring the wiki picker.
const cleanProjectName = (name: string) => name.replace(/\s*\(private\)\s*$/i, '')
const fmtVersionTs = (ts: number) => { try { return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) } catch { return String(ts) } }
// Inspector dropdowns use the app's DOM-rendered Dropdown (same as Settings), NOT native
// <select>: native popups fail to open in some client environments (per-site zoom /
// OS-level popup quirks), which made these controls look broken.
function DsSelect({ value, options, onChange, placeholder }: { value: string; options: DropdownOption[]; onChange: (v: string) => void; placeholder?: string }) {
  return <Dropdown className="ds-dd" value={value} options={options} onChange={onChange} placeholder={placeholder} />
}
const FILL_TYPE_OPTIONS: DropdownOption[] = [
  { value: 'solid', label: 'Solid' },
  { value: 'linear-gradient', label: 'Linear gradient' },
  { value: 'radial-gradient', label: 'Radial gradient' },
]
const STROKE_CAP_OPTIONS: DropdownOption[] = [{ value: 'butt', label: 'Butt' }, { value: 'round', label: 'Round' }, { value: 'square', label: 'Square' }]
const fmtElapsed = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
const IMAGE_GEN_CLIENT_TIMEOUT_MS = 6 * 60 * 1000
function StartScreen({ onCreate, onShowGallery, designCount, projectName }: { onCreate: (t: Template, brief: string) => void; onShowGallery: () => void; designCount: number; projectName: string }) {
  const [surface, setSurface] = React.useState<Surface>('graphic')
  const [brief, setBrief] = React.useState('')
  const tpls = surfaceTemplates(surface)
  const generate = () => { const t = tpls[0]; if (t) onCreate(t, brief) }
  return <div className="ds-start"><div className="ds-start-inner center">
    <p className="muted ds-project-tag">Designing in <strong>{projectName}</strong> · saved to this project</p>
    <h1>What do you want to make?</h1>
    <p className="muted ds-sub">Describe it and the AI drafts editable layers — or start from a template. Nothing's locked; you can change size, aspect ratio and everything else on the canvas.</p>
    <div className="ds-prompt">
      <textarea rows={3} placeholder="Describe your design — e.g. Acme launch post, dark mood, bold headline, blue CTA" value={brief} onChange={e => setBrief(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') generate() }} />
      <div className="ds-prompt-bar">
        <div className="ds-surface-pills">{SURFACES.map(s => <button key={s.key} className={surface === s.key ? 'active' : ''} onClick={() => setSurface(s.key)}>{s.label}</button>)}</div>
        <button className="primary-button" disabled={!brief.trim()} onClick={generate}>Generate →</button>
      </div>
    </div>
    <p className="ds-or"><span>Or start from a template</span></p>
    <div className="ds-tpl-row">{tpls.map(t => <button key={t.id} className="ds-tpl" onClick={() => onCreate(t, brief)}>
      <div className="ds-tpl-canvas"><span className={`ds-frame ${t.artboards > 1 ? 'stacked' : ''}`} style={{ aspectRatio: `${t.width} / ${t.height}` }}>
        <i className="dsf-h" /><i className="dsf-l" /><i className="dsf-l sm" /><i className="dsf-b" />
      </span></div>
      <div className="ds-tpl-meta"><strong>{t.name}</strong><span className="ds-tpl-hint">{t.hint} · {Math.round(t.width)}×{Math.round(t.height)}</span></div>
    </button>)}</div>
    {designCount > 0 && <button className="ds-gallery-link" onClick={onShowGallery}>Your designs ({designCount}) →</button>}
  </div></div>
}

function GalleryView({ designs, onOpen, onDelete, onDeleteMany, onBack, resolveSrc, projectName }: { designs: { id: string; title: string; type: string; w: number; h: number; artboards: number; art?: Artboard }[]; onOpen: (id: string) => void; onDelete: (id: string) => void; onDeleteMany: (ids: string[]) => void; onBack: () => void; resolveSrc: (s: string) => string; projectName: string }) {
  const [sel, setSel] = React.useState<Set<string>>(new Set())
  const toggle = (id: string) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const allSelected = designs.length > 0 && sel.size === designs.length
  // Drop selections for designs that no longer exist (e.g. after a delete).
  React.useEffect(() => { setSel(s => { const n = new Set([...s].filter(id => designs.some(d => d.id === id))); return n.size === s.size ? s : n }) }, [designs])
  return <div className="ds-gallery">
    <div className="ds-gallery-head">
      <BackButton label="Back" onClick={onBack} />
      <h2>Your designs</h2><span className="muted">{designs.length}</span>
      <span className="muted ds-project-tag">in <strong>{projectName}</strong></span>
      {designs.length > 0 && <div className="ds-gallery-bulk">
        {sel.size > 0 && <span className="muted">{sel.size} selected</span>}
        <button className="ghost-button" onClick={() => setSel(allSelected ? new Set() : new Set(designs.map(d => d.id)))}>{allSelected ? 'Clear' : 'Select all'}</button>
        {sel.size > 0 && <button className="ghost-button danger" onClick={async () => { await onDeleteMany([...sel]); setSel(new Set()) }}>Delete {sel.size}</button>}
      </div>}
    </div>
    {designs.length === 0 ? <p className="muted ds-tip">No saved designs yet.</p> : <div className="ds-gallery-grid">{designs.map(d => {
      const checked = sel.has(d.id)
      return <div key={d.id} className={`ds-tpl ${checked ? 'sel' : ''}`} role="button" tabIndex={0} onClick={() => sel.size > 0 ? toggle(d.id) : onOpen(d.id)}>
        <span className={`ds-check-box ${checked ? 'on' : ''}`} role="checkbox" aria-checked={checked} title="Select" onClick={e => { e.stopPropagation(); toggle(d.id) }}>{checked ? '✓' : ''}</span>
        <span className="ds-del" role="button" tabIndex={0} title="Delete design" onClick={e => { e.stopPropagation(); onDelete(d.id) }}>✕</span>
        <div className="ds-tpl-canvas"><MiniPreview art={d.art} resolveSrc={resolveSrc} /></div>
        <div className="ds-tpl-meta"><strong className="ds-tpl-title">{d.title}</strong><span className="ds-tpl-hint">{d.type}{d.artboards > 1 ? ` · ${d.artboards}` : ''} · {d.w}×{d.h}</span></div>
      </div>
    })}</div>}
  </div>
}

// True on phone-width viewports — drives the touch/mobile editor layout.
function useIsMobile() {
  const q = '(max-width: 640px), (pointer: coarse) and (max-width: 820px)'
  const [m, setM] = React.useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches)
  React.useEffect(() => { const mq = window.matchMedia(q); const h = () => setM(mq.matches); mq.addEventListener('change', h); return () => mq.removeEventListener('change', h) }, [])
  return m
}

// Map persisted session messages → design-chat bubbles (assistant replies show prose
// only; the scene JSON is stripped). The chat panel is rendered FROM the DB (single
// source of truth), so a reply can never appear twice regardless of event timing.
const chatFromMessages = (msgs: { role: string; content: string }[]): { role: 'user' | 'assistant'; content: string }[] => msgs
  .filter(m => (m.role === 'user' || m.role === 'assistant') && !!m.content && !m.content.startsWith('Agent produced no output'))
  .map(m => ({ role: m.role as 'user' | 'assistant', content: m.role === 'assistant' ? (stripDesignScene(m.content) || 'Updated the design.') : m.content }))

export function DesignStudio({ token, project, profileId, openSession, openDesignId, onOpened, onExit }: { token: string; project: Project | null; profileId?: number | null; openSession?: { id: number; title: string } | null; openDesignId?: string | null; onOpened?: () => void; onExit?: () => void }) {
  const isMobile = useIsMobile()
  const [mSheet, setMSheet] = React.useState<'panel' | 'inspector' | 'add' | null>(null)
  const [snapOn, setSnapOn] = React.useState(true) // touch has no Shift key → snap via toggle (default on)
  const mobileSnap = isMobile && snapOn
  const [mobileTool, setMobileTool] = React.useState<'select' | 'pan'>('select')
  const [spacePan, setSpacePan] = React.useState(false)
  const [middlePan, setMiddlePan] = React.useState(false)
  const [multiMode, setMultiMode] = React.useState(false) // touch multi-select: tapping layer rows toggles selection
  const [stage, setStage] = React.useState<'start' | 'studio' | 'gallery'>('start')
  const [scene, setScene] = React.useState<Scene | null>(null)
  const [saved, setSaved] = React.useState<'idle' | 'saving' | 'saved'>('idle')
  // Explicit saved versions (snapshots the user chose to keep) — distinct from the
  // undo stack (change-log). Persisted as artifacts/design/<id>/versions/<ts>.json.
  const [versions, setVersions] = React.useState<{ name: string; ts: number }[]>([])
  const [versionMenu, setVersionMenu] = React.useState(false)
  const [savingVersion, setSavingVersion] = React.useState(false)
  const versionsSeq = React.useRef(0)
  const [leftTab, setLeftTab] = React.useState<'chat' | 'assets' | 'layers'>('chat')
  // Desktop panel collapse (mobile uses bottom-sheet overlays instead). Persisted so
  // the canvas stays as wide as you left it across sessions.
  const [leftCollapsed, setLeftCollapsed] = React.useState<boolean>(() => { try { return localStorage.getItem('proxima.design.leftCollapsed') === '1' } catch { return false } })
  const [rightCollapsed, setRightCollapsed] = React.useState<boolean>(() => { try { return localStorage.getItem('proxima.design.rightCollapsed') === '1' } catch { return false } })
  React.useEffect(() => { try { localStorage.setItem('proxima.design.leftCollapsed', leftCollapsed ? '1' : '0') } catch { /* storage disabled */ } }, [leftCollapsed])
  React.useEffect(() => { try { localStorage.setItem('proxima.design.rightCollapsed', rightCollapsed ? '1' : '0') } catch { /* storage disabled */ } }, [rightCollapsed])
  const [assets, setAssets] = React.useState<string[]>([])
  const [uploading, setUploading] = React.useState(false)
  const [imgPrompt, setImgPrompt] = React.useState('')
  // Asset picked as the reference for the next AI generation (image+prompt → image).
  const [refImage, setRefImage] = React.useState<string | null>(null)
  const [imgBusy, setImgBusy] = React.useState(false)
  const [imgBusyKind, setImgBusyKind] = React.useState<'generate' | 'edit' | 'resolve' | null>(null)
  const [imgStartedAt, setImgStartedAt] = React.useState<number | null>(null)
  const [imgElapsed, setImgElapsed] = React.useState(0)
  const [imageProviderKind, setImageProviderKind] = React.useState<'auto' | 'codex' | 'oauth' | 'higgsfield' | 'http'>('codex')
  const [imageEditReady, setImageEditReady] = React.useState(false)
  const [designs, setDesigns] = React.useState<{ id: string; title: string; type: string; w: number; h: number; artboards: number; sessionId?: number; art?: Artboard }[]>([])
  const [projectComponents, setProjectComponents] = React.useState<ProjectComponent[]>([])
  const designFs = React.useMemo(() => project ? projectFs(token, project.slug, 'artifacts/design') : null, [token, project?.slug])
  const saveTimer = React.useRef<number | undefined>(undefined)
  const mountedRef = React.useRef(true)
  const saveSeq = React.useRef(0)
  const assetsSeq = React.useRef(0)
  const designsSeq = React.useRef(0)
  const openSeq = React.useRef(0)
  const sessionOpenSeq = React.useRef(0)
  const settingsSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const lastSavedSceneRef = React.useRef('')
  const fileInput = React.useRef<HTMLInputElement>(null)
  const [chat, setChat] = React.useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  const [chatBusy, setChatBusy] = React.useState(false)
  const sceneRef = React.useRef<Scene | null>(null)
  const applyReplyRef = React.useRef<(runId: number, text: string) => void>(() => {})
  const appliedRunRef = React.useRef<number | null>(null)  // a run's reply is applied to canvas + chat exactly once
  // The chat panel runs on the shared run-stream engine; the real hydrate impl is
  // defined after that hook and reached through this stable wrapper (so open paths
  // above can call hydrateChat without caring about definition order).
  const hydrateChatRef = React.useRef<(sid: number | null) => void>(() => {})
  const hydrateChat = React.useCallback((sid: number | null) => hydrateChatRef.current(sid), [])
  const sessionRef = React.useRef<number | null>(null)
  const openedTargetRef = React.useRef('')
  // Which stage the studio was entered from, so Back returns there (e.g. the
  // "Your designs" gallery) instead of always dropping to the Design home.
  const studioFrom = React.useRef<'start' | 'gallery'>('start')
  const sendRef = React.useRef<(t: string) => void>(() => {})
  const briefRef = React.useRef('')
  const autoSent = React.useRef(false)
  const [ctx, setCtx] = React.useState<{ x: number; y: number; id: string } | null>(null)
  const [shapeMenu, setShapeMenu] = React.useState(false)
  const [artboardMenu, setArtboardMenu] = React.useState(false)
  const [exportMenu, setExportMenu] = React.useState(false)
  const [guides, setGuides] = React.useState<{ ai: number; lines: { axis: 'x' | 'y'; pos: number }[] } | null>(null)
  const [edit, setEdit] = React.useState<{ id: string; x: number; y: number; w: number; h: number; fontSize: number; value: string } | null>(null)
  const [cropMode, setCropMode] = React.useState<{ id: string; before: Pick<ImageLayer, 'x' | 'y' | 'width' | 'height' | 'cropZoom' | 'cropX' | 'cropY'> } | null>(null)
  const [, setHistV] = React.useState(0)
  const hist = React.useRef<{ undo: Scene[]; redo: Scene[] }>({ undo: [], redo: [] })
  const undoRef = React.useRef<() => void>(() => {})
  const redoRef = React.useRef<() => void>(() => {})
  const keyRef = React.useRef<(e: KeyboardEvent) => void>(() => {})
  const clipRef = React.useRef<Layer | null>(null)
  const [focusAb, setFocusAb] = React.useState(0)
  const [selectedIds, setSelectedIds] = React.useState<string[]>([])
  const selectedId = selectedIds[0] ?? null
  const setSelectedId = React.useCallback((id: string | null) => setSelectedIds(id ? [id] : []), [])
  const toggleSelect = (id: string) => setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  const [collapsedGroups, setCollapsedGroups] = React.useState<Set<string>>(new Set())
  const collapseAutoGroups = React.useCallback((sc: Scene) => {
    const ids = sc.autoGrouped ? sc.artboards.flatMap(a => a.layers.map(l => l.groupId).filter(Boolean) as string[]) : []
    setCollapsedGroups(new Set(ids))
  }, [])
  const [marquee, setMarquee] = React.useState<{ ai: number; x: number; y: number; w: number; h: number } | null>(null)
  const marqueeRef = React.useRef<{ ai: number; sx: number; sy: number; additive: boolean } | null>(null)
  const marqueeBoxRef = React.useRef<{ ai: number; x: number; y: number; w: number; h: number } | null>(null)
  const dragStartRef = React.useRef<{ x: number; y: number } | null>(null)
  const [view, setView] = React.useState({ x: 0, y: 0, scale: 1 })
  const panMode = spacePan || middlePan || (isMobile && mobileTool === 'pan')
  const wrapRef = React.useRef<HTMLDivElement>(null)
  const stageRef = React.useRef<Konva.Stage>(null)
  const trRef = React.useRef<Konva.Transformer>(null)
  const nodeRefs = React.useRef<Record<string, Konva.Node | null>>({})
  const resolveGenRef = React.useRef<(sc: Scene) => Promise<void>>(async () => {})
  // Scene snapshot at prompt-build time (per run) — lets apply detect edits made
  // while the agent was working, so overwriting them is loud instead of silent.
  const sentSceneRef = React.useRef<{ runId: number; body: string } | null>(null)
  // gen:-layer ids currently being generated — re-entrancy guard so apply+open can
  // never double-generate (and double-bill) the same layer.
  const genInFlightRef = React.useRef<Set<string>>(new Set())
  // Newest not-yet-written auto-save body + the flusher that persists it on exit.
  const pendingSaveRef = React.useRef<{ path: string; body: string } | null>(null)
  const flushSaveRef = React.useRef<() => void>(() => {})
  // Scene id whose gen: layers still need resolving. The load paths run while the
  // component is still on the start/gallery early-returns — where the resolver
  // assignment below them has never executed — so they set this marker and the
  // studio-stage effect fires the resolver once the canvas is actually mounted.
  const pendingResolveRef = React.useRef<string | null>(null)
  const groupSnapRef = React.useRef(false) // dedupe undo-snapshot across a group drag (Konva fires dragstart on every proxied node)
  const shiftRef = React.useRef(false) // live Shift state for dragBoundFunc (gets no event)
  const groupBoxRef = React.useRef<{ minX: number; minY: number; maxX: number; maxY: number } | null>(null) // selection bbox (artboard-local) at group-drag start
  const groupGuideRef = React.useRef('') // dedupe group-snap guide emits
  const cropDragRef = React.useRef<{ id: string; x: number; y: number; cropX: number; cropY: number } | null>(null)
  const cropResizeRef = React.useRef<{ id: string; handle: string; px: number; py: number; x: number; y: number; w: number; h: number } | null>(null)
  const abDrag = React.useRef<{ ai: number; px: number; py: number; ax: number; ay: number } | null>(null) // dragging an artboard via its label handle
  const [box, setBox] = React.useState({ w: 1000, h: 700 })
  const fittedFor = React.useRef<string>('')

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      saveSeq.current += 1
      assetsSeq.current += 1
      designsSeq.current += 1
      openSeq.current += 1
      sessionOpenSeq.current += 1
      settingsSeq.current += 1
      actionSeq.current += 1
      if (saveTimer.current) clearTimeout(saveTimer.current)
      flushSaveRef.current() // don't drop an <800ms-old edit on unmount
    }
  }, [])

  React.useEffect(() => { sceneRef.current = scene }, [scene])

  // Apply an agent's design reply: parse the returned scene onto the canvas (kept
  // as an undoable step) and add the prose to the chat. Reads live scene via a ref
  // so the event-stream callback never applies onto a stale scene.
  const applyDesignReply = (runId: number, text: string) => {
    // Exactly once per run: message.complete AND the run.completed fallback (and any
    // reconnect replay / hydrate recovery) all funnel here, so guarding at this single
    // mutation point makes a double reply structurally impossible.
    if (appliedRunRef.current === runId) return
    appliedRunRef.current = runId
    const ns = parseDesignScene(text)
    if (ns) {
      const cur = sceneRef.current
      // The reply was built from the scene AS SENT — if the user edited the canvas
      // while the agent worked, this replace overwrites those edits. Make that loud.
      const editedMeanwhile = !!(cur && sentSceneRef.current && sentSceneRef.current.runId === runId && JSON.stringify({ ...cur, runPendingId: undefined }) !== sentSceneRef.current.body)
      sentSceneRef.current = null
      ns.id = cur?.id || ns.id
      ns.title = ns.title || cur?.title || ns.title
      ns.sessionId = cur?.sessionId ?? sessionRef.current ?? undefined
      ns.appliedRunId = runId
      ns.runPendingId = undefined
      // Models routinely copy layer ids across artboards — repair them here like
      // every disk-load path does, or editing one layer moves its twin.
      dedupeSceneIds(ns)
      autoGroupSceneLayers(ns); collapseAutoGroups(ns)
      if (cur) { hist.current.undo.push(cur); if (hist.current.undo.length > 60) hist.current.undo.shift(); hist.current.redo = []; setHistV(v => v + 1) }
      setScene(ns); setSelectedId(null)
      if (editedMeanwhile) setChat(c => [...c, { role: 'assistant', content: '⚠️ Canvas edits made while the agent was working were replaced by this update — press Undo (Ctrl/Cmd+Z) to get them back.' }])
      // Turn agent-emitted "gen:<prompt>" image layers into real images.
      void resolveGenRef.current(ns)
    } else if (/<design-scene/i.test(text) || /"artboards"/.test(text)) {
      // The agent tried to return a scene but it didn't parse — say so instead of
      // silently leaving the canvas untouched while the chat looks successful.
      setChat(c => [...c, { role: 'assistant', content: '⚠️ The design reply could not be applied (invalid scene data). Ask the agent to try again.' }])
    }
    // Refresh the chat panel FROM the DB (single source) rather than appending — a reply
    // can never render twice this way. Fall back to an append only if there's no session.
    const sid = sessionRef.current
    if (sid) void listMessages(token, sid).then(r => { if (sessionRef.current === sid) setChat(chatFromMessages(r.messages)) }).catch(() => undefined)
    else setChat(c => [...c, { role: 'assistant', content: stripDesignScene(text) || (ns ? 'Updated the design.' : (text || 'No response — try again.')) }])
  }
  React.useEffect(() => { applyReplyRef.current = applyDesignReply })

  // Design chat rides the SHARED run-stream engine (same as the main chat): live
  // thinking + reconnect-on-open come from the hook. This handler adds only the
  // design-specific step — applying the returned scene to the canvas — guarded to the
  // tracked run so replays on reconnect can't re-apply a stale scene.
  const onDesignEventRef = React.useRef<(e: RunEvent) => void>(() => {})
  const { busyRun: chatBusyRun, setBusyRun: setChatBusyRun, busyRunRef: chatBusyRunRef, restore: restoreRun } = useRunStream(token, scene?.sessionId ?? null, e => onDesignEventRef.current(e))
  // message.complete carries the reply text and arrives just before run.completed; both
  // funnel into applyDesignReply, which is idempotent per run — so no double reply.
  const onDesignEvent = (ev: RunEvent) => {
    const runId = chatBusyRunRef.current
    if (runId == null || ev.run_id !== runId) return
    if (ev.type === 'message.complete') {
      applyReplyRef.current(runId, String((ev.payload as { text?: string }).text || ''))
      setChatBusyRun(null)
    } else if (ev.type === 'run.completed') {
      // Fallback for the salvage path (no message.complete) — fetch the saved reply;
      // applyDesignReply's per-run guard makes this a no-op if the reply already landed.
      void listMessages(token, ev.session_id).then(r => {
        const a = r.messages.filter(m => m.role === 'assistant')
        applyReplyRef.current(runId, a.length ? a[a.length - 1].content : '')
        setChatBusyRun(null)
      }).catch(() => setChatBusyRun(null))
    } else if (ev.type === 'run.failed' || ev.type === 'run.cancelled') {
      if (appliedRunRef.current !== runId) {
        appliedRunRef.current = runId
        setChat(c => [...c, { role: 'assistant', content: ev.type === 'run.cancelled' ? 'Stopped.' : 'Run failed — try again.' }])
      }
      setChatBusyRun(null)
    }
  }
  React.useEffect(() => { onDesignEventRef.current = onDesignEvent })

  // Rehydrate the chat panel from persisted messages + re-attach to a live (or
  // just-finished) run on open — the reconnect logic lives in the shared hook.
  const doHydrateChat = React.useCallback(async (sid: number | null) => {
    if (!sid) { setChatBusyRun(null); setChat([]); return }
    try {
      const [msgsR, r] = await Promise.all([listMessages(token, sid), restoreRun(sid)])
      setChat(chatFromMessages(msgsR.messages))
      setChatBusyRun(r.running ? r.lastRun : null)
      if (r.running) return  // live stream will deliver the result
      // Finished while the studio was closed: the client is the only thing that writes
      // an agent reply onto the canvas, so recover it once — but ONLY when the scene on
      // disk was still awaiting exactly that run (runPendingId). Without this, an old
      // completed run could overwrite a scene the user has since edited manually.
      if (r.completed && r.lastRun != null && sceneRef.current && sceneRef.current.appliedRunId !== r.lastRun && sceneRef.current.runPendingId === r.lastRun) {
        const a = msgsR.messages.filter(m => m.role === 'assistant' && m.run_id === r.lastRun)
        if (a.length) applyReplyRef.current(r.lastRun, a[a.length - 1].content)
      }
    } catch { setChat([]) }
  }, [token, restoreRun])
  React.useEffect(() => { hydrateChatRef.current = doHydrateChat }, [doHydrateChat])

  // Watchdog against a hung "Designing…": the busy state only clears on a terminal
  // stream event, so a dropped SSE/WS or a worker that died without emitting one would
  // leave it spinning forever. While busy, poll the run's REAL status; once it's no
  // longer running, reconcile — apply the reply if it finished, otherwise say it failed
  // — and clear the busy state instead of hanging silently.
  React.useEffect(() => {
    const sid = scene?.sessionId
    if (chatBusyRun == null || !sid) return
    let on = true
    const check = async () => {
      try {
        const r = await restoreRun(sid)
        const runId = chatBusyRunRef.current
        if (!on || runId == null || r.running) return
        if (r.completed && r.lastRun != null) {
          const m = await listMessages(token, sid)
          const a = m.messages.filter(x => x.role === 'assistant' && x.run_id === r.lastRun)
          if (a.length) applyReplyRef.current(r.lastRun, a[a.length - 1].content)
        } else if (appliedRunRef.current !== runId) {
          appliedRunRef.current = runId
          setChat(c => [...c, { role: 'assistant', content: '⚠️ The design run stopped without finishing (it may have failed or the connection dropped). Try again.' }])
        }
        if (on) setChatBusyRun(null)
      } catch { /* transient — retry next tick */ }
    }
    const t = window.setInterval(check, 20000)
    return () => { on = false; window.clearInterval(t) }
  }, [chatBusyRun, scene?.sessionId, token, restoreRun, setChatBusyRun, chatBusyRunRef])

  React.useEffect(() => {
    const seq = ++settingsSeq.current
    getImageGenSettings(token).then(cfg => {
      if (!mountedRef.current || seq !== settingsSeq.current) return
      const p = cfg.providers.find(x => x.id === cfg.provider)
      const kind = p?.kind || 'codex'
      setImageProviderKind(kind)
      // The provider advertises whether it can edit/use reference images (codex now
      // can, via the Codex OAuth Responses surface). A text-to-image-only provider
      // still edits when the backend can fall back to a connected xAI OAuth.
      setImageEditReady(!!p?.capabilities?.imageEdit || !!cfg.xaiOauthReady?.ready)
    }).catch(() => {
      if (mountedRef.current && seq === settingsSeq.current) { setImageProviderKind('codex'); setImageEditReady(false) }
    })
    return () => { if (seq === settingsSeq.current) settingsSeq.current += 1 }
  }, [token])

  React.useEffect(() => {
    if (!imgBusy || !imgStartedAt) return
    const tick = () => setImgElapsed(Math.max(0, Math.floor((Date.now() - imgStartedAt) / 1000)))
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [imgBusy, imgStartedAt])
  React.useEffect(() => {
    if (cropMode && selectedIds[0] !== cropMode.id) setCropMode(null)
  }, [cropMode, selectedIds])
  const startImgBusy = (kind: 'generate' | 'edit' | 'resolve') => {
    setImgBusyKind(kind)
    setImgStartedAt(Date.now())
    setImgElapsed(0)
    setImgBusy(true)
  }
  const stopImgBusy = () => {
    setImgBusy(false)
    setImgBusyKind(null)
    setImgStartedAt(null)
    setImgElapsed(0)
  }
  const genDesignImageWithTimeout = async (body: { prompt: string; size?: string; model?: string; image?: string }) => {
    if (!project) throw new Error('No project selected')
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), IMAGE_GEN_CLIENT_TIMEOUT_MS)
    try {
      return await genDesignImage(token, project.slug, body, controller.signal)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        throw new Error('Image generation timed out in the browser after 6 minutes. The server may still finish it; refresh the assets list or try again.')
      }
      throw err
    } finally {
      window.clearTimeout(timer)
    }
  }
  const imgBusyLabel = imgBusyKind === 'edit'
    ? 'Editing image with AI'
    : imgBusyKind === 'resolve'
      ? 'Creating missing image assets'
      : 'Generating image asset'

  // Lay artboards out left-to-right on the canvas; return each one's world X.
  const layout = React.useMemo(() => {
    if (!scene) return { xs: [] as number[], ys: [] as number[], w: 0, h: 0 }
    // Default: multi-artboard stacks vertically (centred); single sits at origin. A
    // moved artboard (a.x/a.y set) keeps its own spot; the rest stay in the grid.
    const xs: number[] = [], ys: number[] = []
    const vertical = scene.artboards.length > 1
    const maxW = Math.max(0, ...scene.artboards.map(a => a.width))
    let off = 0
    for (const a of scene.artboards) {
      xs.push(a.x ?? (vertical ? Math.round((maxW - a.width) / 2) : off))
      ys.push(a.y ?? (vertical ? off : 0))
      off += (vertical ? a.height : a.width) + GAP
    }
    let w = 0, h = 0
    scene.artboards.forEach((a, i) => { w = Math.max(w, xs[i] + a.width); h = Math.max(h, ys[i] + a.height) })
    return { xs, ys, w, h }
  }, [scene])

  React.useEffect(() => {
    const el = wrapRef.current; if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el); return () => ro.disconnect()
  }, [stage])

  const fit = React.useCallback(() => {
    if (!layout.w || !box.w) return
    const scale = Math.min(box.w / layout.w, box.h / layout.h) * 0.88
    setView({ scale, x: (box.w - layout.w * scale) / 2, y: (box.h - layout.h * scale) / 2 })
  }, [layout, box])

  React.useEffect(() => {
    if (stage !== 'studio' || !scene || !box.w) return
    const key = scene.id + ':' + box.w + 'x' + box.h
    if (fittedFor.current !== key) { fittedFor.current = key; fit() }
  }, [stage, scene, box, fit])

  React.useEffect(() => {
    const tr = trRef.current; if (!tr) return
    if (cropMode) { tr.nodes([]); tr.getLayer()?.batchDraw(); return }
    const lockedIds = new Set(scene?.artboards.flatMap(a => a.layers).filter(l => l.locked).map(l => l.id) || [])
    const lineOnly = selectedIds.length === 1 && scene?.artboards.flatMap(a => a.layers).find(l => l.id === selectedIds[0])?.type === 'line'
    const nodes = lineOnly ? [] : selectedIds.filter(id => !lockedIds.has(id)).map(id => nodeRefs.current[id]).filter(Boolean) as Konva.Node[]
    // Text supports direct box editing: side handles rewrap width, top/bottom handles
    // change the text box height, and corners resize the box + font size.
    const only = selectedIds.length === 1 ? scene?.artboards.flatMap(a => a.layers).find(l => l.id === selectedIds[0]) : null
    tr.enabledAnchors(only?.type === 'text'
      ? ['top-left', 'top-center', 'top-right', 'middle-left', 'middle-right', 'bottom-left', 'bottom-center', 'bottom-right']
      : ['top-left', 'top-center', 'top-right', 'middle-left', 'middle-right', 'bottom-left', 'bottom-center', 'bottom-right'])
    tr.nodes(nodes); tr.getLayer()?.batchDraw()
  }, [selectedIds, scene, view, cropMode])

  // Redraw once web fonts finish loading so Konva text renders in the chosen font.
  React.useEffect(() => { (document as unknown as { fonts?: { ready?: Promise<unknown> } }).fonts?.ready?.then(() => stageRef.current?.batchDraw()) }, [scene])

  // Track Shift globally for the group-snap dragBoundFunc (which receives no event).
  React.useEffect(() => {
    const set = (e: KeyboardEvent) => { shiftRef.current = e.shiftKey }
    window.addEventListener('keydown', set); window.addEventListener('keyup', set)
    return () => { window.removeEventListener('keydown', set); window.removeEventListener('keyup', set) }
  }, [])
  React.useEffect(() => {
    const isTextInput = (e: KeyboardEvent) => ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName || '')
    const down = (e: KeyboardEvent) => { if (stage === 'studio' && e.code === 'Space' && !isTextInput(e)) { e.preventDefault(); setSpacePan(true) } }
    const up = (e: KeyboardEvent) => { if (e.code === 'Space') setSpacePan(false) }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  }, [stage])
  React.useEffect(() => {
    stageRef.current?.draggable(panMode)
    const c = stageRef.current?.container()
    if (c) c.style.cursor = panMode ? 'grab' : ''
  }, [panMode])


  // Auto-save the scene to artifacts/design/<id>/scene.json (debounced).
  React.useEffect(() => {
    if (!scene || stage !== 'studio' || !designFs) return
    const body = JSON.stringify(scene, null, 2)
    if (body === lastSavedSceneRef.current) return
    const seq = ++saveSeq.current
    setSaved('saving')
    pendingSaveRef.current = { path: `${scene.id}/scene.json`, body }
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      pendingSaveRef.current = null
      designFs.write(`${scene.id}/scene.json`, body)
        .then(() => {
          if (!mountedRef.current || seq !== saveSeq.current) return
          lastSavedSceneRef.current = body
          setSaved('saved')
        })
        .catch(() => { if (mountedRef.current && seq === saveSeq.current) setSaved('idle') })
    }, 800)
    return () => {
      saveSeq.current += 1
      if (saveTimer.current) clearTimeout(saveTimer.current)
    }
  }, [scene, stage, designFs])
  React.useEffect(() => {
    if (stage !== 'studio' || !scene) return
    if (pendingResolveRef.current === scene.id) {
      pendingResolveRef.current = null
      void resolveGenRef.current(scene)
    }
  }, [stage, scene])
  // Edits made <800ms before leaving (Back, design switch, unmount) sat only in the
  // debounced timer and were dropped — flush the newest pending body instead.
  flushSaveRef.current = () => {
    const p = pendingSaveRef.current
    if (!p || !designFs) return
    pendingSaveRef.current = null
    lastSavedSceneRef.current = p.body
    void designFs.write(p.path, p.body).catch(() => undefined)
  }

  // ONE media library: design assets (artifacts/design/_assets) PLUS chat-generated
  // media (artifacts/media/images) — a /image result is usable in the studio without
  // any re-upload or bridge.
  const loadAssets = React.useCallback(() => {
    const seq = ++assetsSeq.current
    if (!designFs || !project) { if (mountedRef.current) setAssets([]); return }
    const IMG_EXT = /\.(png|jpe?g|gif|webp|svg)$/i
    const chatFs = projectFs(token, project.slug, 'artifacts/media/images')
    Promise.all([
      designFs.list('_assets').catch(() => ({ entries: [] as { type: string; name: string }[] })),
      chatFs.list('').catch(() => ({ entries: [] as { type: string; name: string }[] })),
    ]).then(([design, chat]) => {
      if (!mountedRef.current || seq !== assetsSeq.current) return
      setAssets([
        ...(design.entries || []).filter(e => e.type === 'file' && IMG_EXT.test(e.name)).map(e => `artifacts/design/_assets/${e.name}`),
        ...(chat.entries || []).filter(e => e.type === 'file' && IMG_EXT.test(e.name)).map(e => `artifacts/media/images/${e.name}`),
      ])
    }).catch(() => { if (mountedRef.current && seq === assetsSeq.current) setAssets([]) })
  }, [designFs, token, project?.slug])
  React.useEffect(() => { loadAssets() }, [loadAssets])

  const writeProjectComponents = React.useCallback((components: ProjectComponent[]) => {
    if (!designFs) return Promise.resolve()
    return designFs.write('_components.json', JSON.stringify({ version: 1, components }, null, 2))
  }, [designFs])

  React.useEffect(() => {
    let cancelled = false
    if (!designFs) { setProjectComponents([]); return }
    designFs.read('_components.json')
      .then(f => {
        if (cancelled) return
        const parsed = JSON.parse(f.content)
        setProjectComponents(Array.isArray(parsed?.components) ? parsed.components : [])
      })
      .catch(() => { if (!cancelled) setProjectComponents([]) })
    return () => { cancelled = true }
  }, [designFs])

  // "Your designs" — list saved designs (folders with scene.json) for the start screen.
  const loadDesigns = React.useCallback(async () => {
    const seq = ++designsSeq.current
    if (!designFs) { if (mountedRef.current) setDesigns([]); return }
    try {
      const r = await designFs.list('')
      const dirs = (r.entries || []).filter(e => e.type === 'dir' && e.name !== '_assets')
      const out = await Promise.all(dirs.map(async d => {
        try { const f = await designFs.read(`${d.name}/scene.json`); const s = JSON.parse(f.content); const a = s.artboards?.[0] || {}; return { id: s.id || d.name, title: s.title || d.name, type: s.type || 'graphic', w: a.width || 1080, h: a.height || 1080, artboards: s.artboards?.length || 1, sessionId: s.sessionId, art: a } } catch { return null }
      }))
      if (mountedRef.current && seq === designsSeq.current) setDesigns(out.filter(Boolean) as typeof designs)
    } catch { if (mountedRef.current && seq === designsSeq.current) setDesigns([]) }
  }, [designFs])
  React.useEffect(() => { if (stage === 'start' || stage === 'gallery') loadDesigns() }, [stage, loadDesigns])

  // Open the design linked to a clicked sidebar "Design" session — match by stored
  // sessionId (new designs) or by title (older designs created before the link).
  React.useEffect(() => {
    if (!openSession || !designFs) {
      sessionOpenSeq.current += 1
      return
    }
    const targetKey = `${project?.slug || ''}:session:${openSession.id}`
    if (openedTargetRef.current === targetKey) { onOpened?.(); return }
    const wantTitle = openSession.title.replace(/^Design:\s*/, '').trim()
    const seq = ++sessionOpenSeq.current
    ;(async () => {
      try {
        const r = await designFs.list('')
        const scenes: Scene[] = []
        for (const d of (r.entries || []).filter(e => e.type === 'dir' && e.name !== '_assets')) {
          if (!mountedRef.current || seq !== sessionOpenSeq.current) return
          try { const f = await designFs.read(`${d.name}/scene.json`); scenes.push(JSON.parse(f.content) as Scene) } catch { /* skip */ }
        }
        // sessionId is the identity; title is only a legacy fallback for designs that
        // predate the link — and only when it matches exactly ONE unlinked design
        // (duplicate titles like "Untitled design" used to mis-link the first folder).
        let s = scenes.find(x => x.sessionId === openSession.id)
        if (!s) {
          const byTitle = scenes.filter(x => !x.sessionId && (x.title || '').trim() === wantTitle)
          if (byTitle.length === 1) { s = byTitle[0]; s.sessionId = openSession.id }
        }
        if (s && mountedRef.current && seq === sessionOpenSeq.current) {
          openedTargetRef.current = targetKey
          dedupeSceneIds(s); autoGroupSceneLayers(s); collapseAutoGroups(s); setScene(s); setFocusAb(0); setSelectedId(null); sessionRef.current = s.sessionId ?? openSession.id; void hydrateChat(sessionRef.current); briefRef.current = ''; autoSent.current = true; fittedFor.current = ''; hist.current = { undo: [], redo: [] }; setLeftTab('chat'); setStage('studio'); pendingResolveRef.current = s.id
        }
      } finally { if (mountedRef.current && seq === sessionOpenSeq.current) onOpened?.() }
    })()
    return () => { if (seq === sessionOpenSeq.current) sessionOpenSeq.current += 1 }
  }, [openSession, designFs, collapseAutoGroups])

  // Keyboard (undo/redo/delete/nudge/copy-paste/duplicate) — delegated to keyRef
  // which is reassigned each render so it sees the latest selection/scene.
  React.useEffect(() => {
    const h = (e: KeyboardEvent) => keyRef.current(e)
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  // When entering the studio from a brief, auto-send it so the AI fills the skeleton.
  React.useEffect(() => {
    if (stage === 'studio' && briefRef.current && !autoSent.current) {
      autoSent.current = true
      const t = briefRef.current; briefRef.current = ''
      const timer = window.setTimeout(() => { if (mountedRef.current) sendRef.current(t) }, 150)
      return () => window.clearTimeout(timer)
    }
  }, [stage])

  const onWheel = (e: Konva.KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault()
    if (e.evt.ctrlKey || e.evt.metaKey) {
      const st = stageRef.current; if (!st) return
      const ptr = st.getPointerPosition(); if (!ptr) return
      const old = view.scale
      const next = Math.min(5, Math.max(0.05, old * (e.evt.deltaY < 0 ? 1.08 : 0.926)))
      const wx = (ptr.x - view.x) / old, wy = (ptr.y - view.y) / old
      setView({ scale: next, x: ptr.x - wx * next, y: ptr.y - wy * next })
    } else {
      setView(v => ({ ...v, x: v.x - e.evt.deltaX, y: v.y - e.evt.deltaY }))
    }
  }

  // Two-finger pinch-to-zoom + pan (touch). One finger keeps Konva's drag (pan empty
  // / move element); two fingers zoom around their midpoint and pan with it.
  const pinchRef = React.useRef<{ dist: number; cx: number; cy: number } | null>(null)
  const onTouchMove = (e: Konva.KonvaEventObject<TouchEvent>) => {
    const t = e.evt.touches
    if (t.length < 2) return
    e.evt.preventDefault()
    const st = stageRef.current; if (!st) return
    st.draggable(false) // suspend one-finger pan while pinching
    const r = st.container().getBoundingClientRect()
    const p1x = t[0].clientX - r.left, p1y = t[0].clientY - r.top
    const p2x = t[1].clientX - r.left, p2y = t[1].clientY - r.top
    const dist = Math.hypot(p2x - p1x, p2y - p1y), cx = (p1x + p2x) / 2, cy = (p1y + p2y) / 2
    const prev = pinchRef.current
    if (!prev) { pinchRef.current = { dist, cx, cy }; return }
    setView(v => {
      const next = Math.min(5, Math.max(0.05, v.scale * (dist / prev.dist)))
      const wx = (cx - v.x) / v.scale, wy = (cy - v.y) / v.scale
      return { scale: next, x: cx - wx * next + (cx - prev.cx), y: cy - wy * next + (cy - prev.cy) }
    })
    pinchRef.current = { dist, cx, cy }
  }
  const onTouchEnd = (e: Konva.KonvaEventObject<TouchEvent>) => { if (e.evt.touches.length < 2) { pinchRef.current = null; stageRef.current?.draggable(panMode) } }

  const resolveSrc = (s: string) => /^gen:/i.test(s) ? '' : (/^(https?:|data:|blob:)/.test(s) ? s : (project ? fileUrl(project.slug, s) : s))
  const openDesign = async (id: string) => {
    if (!designFs) return
    studioFrom.current = stage === 'gallery' ? 'gallery' : 'start'
    const seq = ++openSeq.current
    try {
      const f = await designFs.read(`${id}/scene.json`)
      if (!mountedRef.current || seq !== openSeq.current) return
      const s = JSON.parse(f.content) as Scene
      dedupeSceneIds(s); autoGroupSceneLayers(s); collapseAutoGroups(s); setScene(s); setFocusAb(0); setSelectedId(null); sessionRef.current = s.sessionId ?? null; void hydrateChat(sessionRef.current); briefRef.current = ''; autoSent.current = true; fittedFor.current = ''; hist.current = { undo: [], redo: [] }; setStage('studio')
      pendingResolveRef.current = s.id
    } catch { /* ignore */ }
  }
  // Deep-open a specific design by folder id (e.g. one a workflow just produced).
  React.useEffect(() => {
    if (openDesignId && designFs) {
      const targetKey = `${project?.slug || ''}:design:${openDesignId}`
      if (openedTargetRef.current !== targetKey) {
        openedTargetRef.current = targetKey
        void openDesign(openDesignId)
      }
      onOpened?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openDesignId, designFs])

  // Remember the last design open in this project, and reopen it when the studio is
  // entered fresh (no deep-link). DesignStudio unmounts on every view change, so
  // without this, leaving for another menu and coming back dropped you on the start
  // screen with an empty chat — the design + its chat (which live on disk/in the DB)
  // are still there, so we just reopen the last one and let hydrateChat restore it.
  const lastDesignKey = project ? `proxima.design.last.${project.slug}` : null
  React.useEffect(() => {
    if (stage === 'studio' && scene?.id && lastDesignKey) {
      try { localStorage.setItem(lastDesignKey, scene.id) } catch { /* storage disabled */ }
    }
  }, [stage, scene?.id, lastDesignKey])
  const restoredRef = React.useRef(false)
  React.useEffect(() => {
    if (restoredRef.current || openSession || openDesignId || scene || !designFs || !lastDesignKey) return
    restoredRef.current = true
    let id: string | null = null
    try { id = localStorage.getItem(lastDesignKey) } catch { /* storage disabled */ }
    if (id) void openDesign(id)  // openDesign no-ops gracefully if the design was deleted
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSession, openDesignId, designFs, lastDesignKey])
  const removeDesign = async (id: string) => {
    if (!designFs) return
    try { const f = await designFs.read(`${id}/scene.json`); const s = JSON.parse(f.content); if (s.sessionId) await deleteSession(token, s.sessionId).catch(() => undefined) } catch { /* no session */ }
    try { await designFs.remove(id) } catch { /* ignore */ }
  }
  const deleteDesign = async (id: string) => {
    if (!designFs) return
    if (!(await confirmDialog({ title: 'Delete design?', message: 'The design and its AI chat will be removed. This cannot be undone.', confirmLabel: 'Delete', danger: true }))) return
    await removeDesign(id); loadDesigns()
  }
  const deleteManyDesigns = async (ids: string[]) => {
    if (!ids.length) return
    if (!(await confirmDialog({ title: `Delete ${ids.length} design${ids.length > 1 ? 's' : ''}?`, message: 'The designs and their AI chats will be removed. This cannot be undone.', confirmLabel: `Delete ${ids.length}`, danger: true }))) return
    for (const id of ids) await removeDesign(id)
    loadDesigns()
  }
  if (!project) return <section className="design-studio"><div className="ds-start"><div className="ds-start-inner center"><h1>Pick a project first</h1><p className="muted ds-sub">Design Studio saves your work into the active project's <code>artifacts/design</code>. Choose a project from the sidebar.</p></div></div></section>
  if (stage === 'gallery') return <section className="design-studio"><GalleryView designs={designs} onOpen={openDesign} onDelete={deleteDesign} onDeleteMany={deleteManyDesigns} onBack={() => setStage('start')} resolveSrc={resolveSrc} projectName={cleanProjectName(project.name)} /></section>
  if (stage === 'start' || !scene) return <section className="design-studio"><StartScreen designCount={designs.length} projectName={cleanProjectName(project.name)} onShowGallery={() => setStage('gallery')} onCreate={(t, brief) => { studioFrom.current = 'start'; setCollapsedGroups(new Set()); setScene(sceneFromTemplate(t, brief)); setFocusAb(0); setSelectedId(null); fittedFor.current = ''; hist.current = { undo: [], redo: [] }; setChat([]); setChatBusyRun(null); sessionRef.current = null; briefRef.current = brief.trim(); autoSent.current = false; if (brief.trim()) setLeftTab('chat'); setStage('studio') }} /></section>

  const findLayer = (id: string): Layer | null => { for (const a of scene.artboards) { const l = a.layers.find(x => x.id === id); if (l) return l } return null }
  const selected = selectedId ? findLayer(selectedId) : null
  const ab = scene.artboards[focusAb] || scene.artboards[0]
  const componentLibrary = [...projectComponents, ...(scene.designSystem?.components || []).filter(c => !projectComponents.some(p => p.id === c.id))]
  const snapshot = () => { if (scene) { hist.current.undo.push(scene); if (hist.current.undo.length > 60) hist.current.undo.shift(); hist.current.redo = []; setHistV(v => v + 1) } }
  const undo = () => { const h = hist.current; if (!h.undo.length) return; const prev = h.undo.pop() as Scene; if (scene) h.redo.push(scene); setScene(prev); setSelectedId(null); setCtx(null); setHistV(v => v + 1) }
  const redo = () => { const h = hist.current; if (!h.redo.length) return; const next = h.redo.pop() as Scene; if (scene) h.undo.push(scene); setScene(next); setSelectedId(null); setCtx(null); setHistV(v => v + 1) }
  undoRef.current = undo; redoRef.current = redo
  const patchLayer = (id: string, patch: Partial<Layer>) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => l.id === id && (!l.locked || 'locked' in patch) ? { ...l, ...patch } as Layer : l) })) })) }

  // Saved-versions (history of explicit saves, not the undo change-log). List/save/restore
  // snapshot files under artifacts/design/<id>/versions/<ts>.json via the shared designFs.
  // Plain functions (not hooks) — they live after the early returns above and are only
  // invoked from onClick handlers, so they must NOT be React.useCallback (that would make
  // the hook count vary between renders → React error #310).
  const loadVersions = async () => {
    const sc = sceneRef.current
    if (!designFs || !sc) { if (mountedRef.current) setVersions([]); return }
    const seq = ++versionsSeq.current
    try {
      const r = await designFs.list(`${sc.id}/versions`)
      const vs = (r.entries || [])
        .filter(e => e.type === 'file' && e.name.endsWith('.json'))
        .map(e => ({ name: e.name, ts: Number(e.name.replace('.json', '')) || 0 }))
        .sort((a, b) => b.ts - a.ts)
      if (mountedRef.current && seq === versionsSeq.current) setVersions(vs)
    } catch { if (mountedRef.current && seq === versionsSeq.current) setVersions([]) }
  }
  const saveVersion = async () => {
    const sc = sceneRef.current
    if (!designFs || !sc || savingVersion) return
    setSavingVersion(true)
    try {
      const body = JSON.stringify(sc, null, 2)
      // Persist scene.json first so the kept version matches exactly what is on canvas.
      await designFs.write(`${sc.id}/scene.json`, body)
      await designFs.write(`${sc.id}/versions/${Date.now()}.json`, body)
      if (mountedRef.current) { lastSavedSceneRef.current = body; setSaved('saved') }
      await loadVersions()
    } catch { /* best-effort */ }
    finally { if (mountedRef.current) setSavingVersion(false) }
  }
  const restoreVersion = async (name: string) => {
    const sc = sceneRef.current
    if (!designFs || !sc) return
    try {
      const f = await designFs.read(`${sc.id}/versions/${name}`)
      const s = JSON.parse(f.content) as Scene
      s.id = sc.id
      dedupeSceneIds(s); autoGroupSceneLayers(s); collapseAutoGroups(s)
      snapshot()
      setScene(s); setSelectedId(null); setFocusAb(0); fittedFor.current = ''
      setVersionMenu(false)
    } catch { /* ignore malformed version */ }
  }
  const patchLayerLive = (id: string, patch: Partial<Layer>) => setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => l.id === id && (!l.locked || 'locked' in patch) ? { ...l, ...patch } as Layer : l) })) }))
  const startCrop = (im: ImageLayer) => { snapshot(); setCropMode({ id: im.id, before: { x: im.x, y: im.y, width: im.width, height: im.height, cropZoom: im.cropZoom, cropX: im.cropX, cropY: im.cropY } }); setSelectedId(im.id) }
  const applyCrop = () => { setCropMode(null); cropDragRef.current = null; cropResizeRef.current = null }
  const cancelCrop = () => { if (cropMode) patchLayerLive(cropMode.id, cropMode.before as Partial<Layer>); setCropMode(null); cropDragRef.current = null; cropResizeRef.current = null }
  const setImageCrop = (id: string, patch: Partial<ImageLayer>) => (cropMode?.id === id ? patchLayerLive : patchLayer)(id, patch as Partial<Layer>)
  const resizeCropFrame = (start: NonNullable<typeof cropResizeRef.current>, dx: number, dy: number) => {
    const min = 24
    let x = start.x, y = start.y, width = start.w, height = start.h
    if (start.handle.includes('w')) { x = Math.min(start.x + start.w - min, start.x + dx); width = start.x + start.w - x }
    if (start.handle.includes('e')) width = Math.max(min, start.w + dx)
    if (start.handle.includes('n')) { y = Math.min(start.y + start.h - min, start.y + dy); height = start.y + start.h - y }
    if (start.handle.includes('s')) height = Math.max(min, start.h + dy)
    return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) }
  }
  const patchArtboard = (patch: Partial<typeof ab>) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb ? { ...a, ...patch } : a) })) }
  const addLayer = (l: Layer) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb ? { ...a, layers: [...a.layers, l] } : a) })); setSelectedId(l.id) }
  const removeLayer = (id: string) => { const l = findLayer(id); if (l?.locked) return; snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.filter(l => l.id !== id) })) })); if (selectedId === id) setSelectedId(null) }
  const removeSelected = () => { const ids = selectedIds.filter(id => !findLayer(id)?.locked); if (!ids.length) return; snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.filter(l => !ids.includes(l.id)) })) })); setSelectedId(null) }
  const groupSelected = () => {
    const ids = selectedIds.filter(id => ab.layers.some(l => l.id === id && !l.locked))
    if (ids.length < 2) return
    const gid = uid('g')
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb ? { ...a, layers: a.layers.map(l => ids.includes(l.id) ? { ...l, groupId: gid, groupName: 'Group' } as Layer : l) } : a) }))
    setCollapsedGroups(prev => { const n = new Set(prev); n.delete(gid); return n })
    setSelectedIds(ids)
  }
  const arrangeAutoLayout = (ids: string[], patch: Partial<Pick<Layer, 'layoutDirection' | 'layoutGap' | 'layoutPadding' | 'layoutAlign'>> = {}) => {
    const members = ab.layers.filter(l => ids.includes(l.id) && !l.locked)
    const b = getBounds(members)
    if (!b || members.length < 2) return
    const leader = members.find(l => l.autoLayout) || members[0]
    const dir = patch.layoutDirection || leader.layoutDirection || 'horizontal'
    const gap = patch.layoutGap ?? leader.layoutGap ?? 16
    const pad = patch.layoutPadding ?? leader.layoutPadding ?? 16
    const align = patch.layoutAlign || leader.layoutAlign || 'center'
    const sorted = [...members].sort((a, c) => dir === 'horizontal' ? getBox(a).x - getBox(c).x : getBox(a).y - getBox(c).y)
    const maxCross = Math.max(...sorted.map(l => dir === 'horizontal' ? getBox(l).h : getBox(l).w))
    let cursor = dir === 'horizontal' ? b.x + pad : b.y + pad
    const nextById = new Map<string, { x: number; y: number }>()
    for (const l of sorted) {
      const lb = getBox(l)
      const crossStart = dir === 'horizontal' ? b.y + pad : b.x + pad
      const crossDelta = align === 'start' ? 0 : align === 'end' ? maxCross - (dir === 'horizontal' ? lb.h : lb.w) : (maxCross - (dir === 'horizontal' ? lb.h : lb.w)) / 2
      nextById.set(l.id, dir === 'horizontal' ? { x: Math.round(cursor), y: Math.round(crossStart + crossDelta) } : { x: Math.round(crossStart + crossDelta), y: Math.round(cursor) })
      cursor += (dir === 'horizontal' ? lb.w : lb.h) + gap
    }
    snapshot()
    setScene(s => s && ({
      ...s,
      artboards: s.artboards.map((a, ai) => ai !== focusAb ? a : {
        ...a,
        layers: a.layers.map(l => {
          const next = nextById.get(l.id)
          if (!next) return l
          const dx = next.x - l.x, dy = next.y - l.y
          const common = { ...l, x: next.x, y: next.y, autoLayout: true, layoutDirection: dir, layoutGap: gap, layoutPadding: pad, layoutAlign: align } as Layer
          return l.type === 'line' ? { ...common, x2: l.x2 + dx, y2: l.y2 + dy } as Layer : common
        }),
      }),
    }))
  }
  const enableAutoLayout = () => {
    const ids = selectedIds.filter(id => ab.layers.some(l => l.id === id && !l.locked))
    if (ids.length < 2) return
    const gid = ids.map(id => findLayer(id)?.groupId).find(Boolean) || uid('g')
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb ? { ...a, layers: a.layers.map(l => ids.includes(l.id) ? { ...l, groupId: gid, groupName: 'Auto layout', autoLayout: true, layoutDirection: 'horizontal', layoutGap: 16, layoutPadding: 16, layoutAlign: 'center' } as Layer : l) } : a) }))
    setTimeout(() => arrangeAutoLayout(ids), 0)
  }
  const selectedLayoutLeader = selectedIds.map(id => findLayer(id)).find((l): l is Layer => !!l?.autoLayout)
  const saveSelectedStyle = (kind: 'color' | 'text' | 'effect') => {
    if (!selected) return
    const name = `${selected.type} ${kind} ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    snapshot()
    setScene(s => {
      if (!s) return s
      const ds = s.designSystem || {}
      if (kind === 'color' && 'fill' in selected) return { ...s, designSystem: { ...ds, colorStyles: [...(ds.colorStyles || []), { id: uid('cs'), name, fill: selected.fill, fillType: selected.fillType, fill2: selected.fill2, gradientAngle: selected.gradientAngle, gradientStartX: selected.gradientStartX, gradientStartY: selected.gradientStartY, gradientEndX: selected.gradientEndX, gradientEndY: selected.gradientEndY, gradientStops: selected.gradientStops }] } }
      if (kind === 'text' && selected.type === 'text') return { ...s, designSystem: { ...ds, textStyles: [...(ds.textStyles || []), { id: uid('ts'), name, fontFamily: selected.fontFamily, fontStyle: selected.fontStyle, fontSize: selected.fontSize, fill: selected.fill, lineHeight: selected.lineHeight, letterSpacing: selected.letterSpacing, textTransform: selected.textTransform, listStyle: selected.listStyle }] } }
      return { ...s, designSystem: { ...ds, effectStyles: [...(ds.effectStyles || []), { id: uid('es'), name, effects: selected.effects || [] }] } }
    })
  }
  const applyColorStyle = (id: string) => {
    const st = scene.designSystem?.colorStyles?.find(x => x.id === id)
    if (st && selected) patchLayer(selected.id, { fill: st.fill, fillType: st.fillType, fill2: st.fill2, gradientAngle: st.gradientAngle, gradientStartX: st.gradientStartX, gradientStartY: st.gradientStartY, gradientEndX: st.gradientEndX, gradientEndY: st.gradientEndY, gradientStops: st.gradientStops } as Partial<Layer>)
  }
  const applyTextStyle = (id: string) => {
    const st = scene.designSystem?.textStyles?.find(x => x.id === id)
    if (st && selected?.type === 'text') patchLayer(selected.id, st as Partial<Layer>)
  }
  const applyEffectStyle = (id: string) => {
    const st = scene.designSystem?.effectStyles?.find(x => x.id === id)
    if (st && selected) patchLayer(selected.id, { effects: st.effects } as Partial<Layer>)
  }
  const saveComponentFromSelection = () => {
    const layers = ab.layers.filter(l => selectedIds.includes(l.id))
    const b = getBounds(layers)
    if (!b || layers.length < 1) return
    const rel = layers.map(l => ({ ...l, id: uid(l.type[0]), x: l.x - b.x, y: l.y - b.y, groupId: undefined, groupName: undefined } as Layer))
    const component = { id: uid('cmp'), name: `Component ${projectComponents.length + 1}`, width: b.w, height: b.h, layers: rel }
    const next = [...projectComponents, component]
    setProjectComponents(next)
    void writeProjectComponents(next).catch(() => setProjectComponents(projectComponents))
  }
  const insertComponent = (id: string) => {
    const local = scene.designSystem?.components || []
    const c = [...projectComponents, ...local.filter(c => !projectComponents.some(p => p.id === c.id))].find(x => x.id === id)
    if (!c) return
    const gid = uid('g')
    const layers = c.layers.map(l => ({ ...l, id: uid(l.type[0]), x: 80 + l.x, y: 80 + l.y, groupId: gid, groupName: c.name } as Layer))
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb ? { ...a, layers: [...a.layers, ...layers] } : a) }))
    setSelectedIds(layers.map(l => l.id))
  }
  const ungroupSelected = () => {
    const groups = new Set(selectedIds.map(id => findLayer(id)?.groupId).filter(Boolean) as string[])
    if (!groups.size) return
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => l.groupId && groups.has(l.groupId) && !l.locked ? { ...l, groupId: undefined, groupName: undefined } as Layer : l) })) }))
  }
  const selectAllLayers = () => setSelectedIds(ab.layers.filter(l => !l.locked).map(l => l.id))
  const toggleLayerLock = (id: string) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => l.id === id ? { ...l, locked: !l.locked } as Layer : l) })) })) }
  const toggleGroupLock = (groupId: string) => {
    const members = scene.artboards.flatMap(a => a.layers).filter(l => l.groupId === groupId)
    const next = !members.every(l => l.locked)
    snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => l.groupId === groupId ? { ...l, locked: next } as Layer : l) })) }))
    if (next) setSelectedIds(ids => ids.filter(id => !members.some(l => l.id === id)))
  }
  const toggleGroupCollapsed = (groupId: string) => setCollapsedGroups(prev => {
    const next = new Set(prev)
    next.has(groupId) ? next.delete(groupId) : next.add(groupId)
    return next
  })
  const moveGroup = (groupId: string, dir: -1 | 1) => {
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map(a => {
      if (!a.layers.some(l => l.groupId === groupId)) return a
      const members = a.layers.filter(l => l.groupId === groupId)
      const rest = a.layers.filter(l => l.groupId !== groupId)
      const memberIdx = a.layers.map((l, i) => l.groupId === groupId ? i : -1).filter(i => i >= 0)
      const anchor = dir > 0 ? Math.max(...memberIdx) : Math.min(...memberIdx)
      const restBeforeAnchor = a.layers.slice(0, anchor + (dir > 0 ? 1 : 0)).filter(l => l.groupId !== groupId).length
      let insert = dir > 0 ? restBeforeAnchor + 1 : restBeforeAnchor - 1
      insert = Math.max(0, Math.min(rest.length, insert))
      return { ...a, layers: [...rest.slice(0, insert), ...members, ...rest.slice(insert)] }
    }) }))
  }
  // Multi-select group move is handled NATIVELY by the Konva Transformer (_proxyDrag
  // moves every attached node together). We must NOT move peers ourselves (that was
  // the double-move jitter). We only: snapshot once at the start (deduped — Konva
  // fires dragstart on each proxied node) and commit all selected positions on drop.
  const groupStart = () => {
    if (groupSnapRef.current) return
    snapshot(); groupSnapRef.current = true
    // Capture the selection's bounding box (artboard-local) once, for group-as-unit snapping.
    const sel = scene.artboards.flatMap(a => a.layers).filter(l => selectedIds.includes(l.id) && !l.locked).map(getBox)
    groupBoxRef.current = sel.length ? { minX: Math.min(...sel.map(b => b.x)), minY: Math.min(...sel.map(b => b.y)), maxX: Math.max(...sel.map(b => b.x + b.w)), maxY: Math.max(...sel.map(b => b.y + b.h)) } : null
  }
  const commitGroup = () => {
    setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => {
      if (!selectedIds.includes(l.id) || l.locked) return l
      const n = nodeRefs.current[l.id]
      if (!n) return l
      const nx = Math.round(n.x()), ny = Math.round(n.y())
      return l.type === 'line'
        ? { ...l, x: nx, y: ny, x2: Math.round(l.x2 + (nx - l.x)), y2: Math.round(l.y2 + (ny - l.y)) } as Layer
        : { ...l, x: nx, y: ny } as Layer
    }) })) }))
    groupSnapRef.current = false
    groupGuideRef.current = ''
    setGuides(null)
  }
  // Group snap: given the dragged node's delta, snap the selection's bbox to the
  // artboard edges/centre AND to non-selected elements' edges/centres; return the
  // shared correction (same for every selected node → rigid) and show guide lines.
  const computeGroupSnap = (dx: number, dy: number): { cx: number; cy: number } => {
    const gb = groupBoxRef.current; const a = scene.artboards[focusAb]
    if (!gb || !a) return { cx: 0, cy: 0 }
    const aw = a.width, ah = a.height, T = 8 / view.scale
    const gw = gb.maxX - gb.minX, gh = gb.maxY - gb.minY
    const nx = gb.minX + dx, ny = gb.minY + dy
    const peers = a.layers.filter(l => !selectedIds.includes(l.id)).map(getBox)
    const xT = [0, aw / 2, aw, ...peers.flatMap(b => [b.x, b.x + b.w / 2, b.x + b.w])]
    const yT = [0, ah / 2, ah, ...peers.flatMap(b => [b.y, b.y + b.h / 2, b.y + b.h])]
    const lines: { axis: 'x' | 'y'; pos: number }[] = []
    let cx = 0, bx = T, bxp = 0
    for (const t of xT) for (const ed of [nx, nx + gw / 2, nx + gw]) { const d = Math.abs(ed - t); if (d < bx) { bx = d; cx = t - ed; bxp = t } }
    if (bx < T) lines.push({ axis: 'x', pos: bxp })
    let cy = 0, by = T, byp = 0
    for (const t of yT) for (const ed of [ny, ny + gh / 2, ny + gh]) { const d = Math.abs(ed - t); if (d < by) { by = d; cy = t - ed; byp = t } }
    if (by < T) lines.push({ axis: 'y', pos: byp })
    const key = lines.map(g => g.axis + Math.round(g.pos)).join('|')
    if (key !== groupGuideRef.current) { groupGuideRef.current = key; setGuides(lines.length ? { ai: focusAb, lines } : null) }
    return { cx, cy }
  }
  const alignSel = (dir: 'l' | 'c' | 'r' | 't' | 'm' | 'b') => {
    if (!selectedIds.length) return
    const selectedLayers = ab.layers.filter(l => selectedIds.includes(l.id) && !l.locked)
    const base = selectedLayers.length > 1 ? getBounds(selectedLayers) : { x: 0, y: 0, w: ab.width, h: ab.height }
    if (!base) return
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(l => {
      if (!selectedIds.includes(l.id) || l.locked) return l
      const b = getBox(l)
      const nx = dir === 'l' ? base.x : dir === 'c' ? Math.round(base.x + (base.w - b.w) / 2) : dir === 'r' ? Math.round(base.x + base.w - b.w) : l.x
      const ny = dir === 't' ? base.y : dir === 'm' ? Math.round(base.y + (base.h - b.h) / 2) : dir === 'b' ? Math.round(base.y + base.h - b.h) : l.y
      if (l.type === 'line') return { ...l, x: nx, y: ny, x2: Math.round(l.x2 + (nx - l.x)), y2: Math.round(l.y2 + (ny - l.y)) } as Layer
      return { ...l, x: nx, y: ny } as Layer
    }) })) }))
  }
  const bringToFront = (id: string) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => { const l = a.layers.find(x => x.id === id); if (!l) return a; return { ...a, layers: [...a.layers.filter(x => x.id !== id), l] } }) })) }
  const sendToBack = (id: string) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => { const l = a.layers.find(x => x.id === id); if (!l) return a; return { ...a, layers: [l, ...a.layers.filter(x => x.id !== id)] } }) })) }
  const duplicate = (id: string) => { const nid = uid('c'); snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => { const l = a.layers.find(x => x.id === id); if (!l || l.locked) return a; return { ...a, layers: [...a.layers, { ...l, id: nid, locked: false, x: l.x + 24, y: l.y + 24 }] } }) })); setSelectedId(nid) }
  const setImageAsBackground = (id: string) => {
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => {
      if (i !== focusAb) return a
      const layer = a.layers.find(l => l.id === id)
      if (!layer || layer.type !== 'image') return a
      const bg = { ...layer, x: 0, y: 0, width: a.width, height: a.height, rotation: 0, cropZoom: 1, cropX: 0, cropY: 0 } as ImageLayer
      return { ...a, layers: [bg, ...a.layers.filter(l => l.id !== id)] }
    }) }))
    setSelectedId(id)
  }
  // Alt-drag duplicate: drop a copy at the layer's current spot; the original keeps
  // dragging, so a clone is left behind (Photoshop/Figma alt-drag).
  const cloneInPlace = (id: string) => { const nid = uid('c'); snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => { const l = a.layers.find(x => x.id === id); if (!l || l.locked) return a; return { ...a, layers: [...a.layers, { ...l, id: nid, locked: false }] } }) })) }
  const startTextEdit = (id: string) => {
    const node = nodeRefs.current[id]; const wrap = wrapRef.current; const lyr = findLayer(id)
    if (!node || !wrap || !lyr || lyr.type !== 'text') return
    const r = node.getClientRect(); const wb = wrap.getBoundingClientRect()
    setSelectedId(id)
    setEdit({ id, x: wb.left + r.x, y: wb.top + r.y, w: Math.max(80, r.width), h: r.height, fontSize: lyr.fontSize * view.scale, value: lyr.text })
  }
  keyRef.current = (e: KeyboardEvent) => {
    if (stage !== 'studio' || edit) return
    const tag = (e.target as HTMLElement)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA') return
    const k = e.key.toLowerCase(); const meta = e.metaKey || e.ctrlKey
    if (meta && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return }
    if (meta && k === 'y') { e.preventDefault(); redo(); return }
    if (e.key === 'Escape') { setCtx(null); setSelectedId(null); return }
    if (meta && k === 'v' && clipRef.current) { e.preventDefault(); const nid = uid('p'); const cp = { ...clipRef.current, id: nid, x: clipRef.current.x + 24, y: clipRef.current.y + 24 } as Layer; snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb ? { ...a, layers: [...a.layers, cp] } : a) })); setSelectedId(nid); return }
    if (meta && k === 'a') { e.preventDefault(); setSelectedIds(ab.layers.filter(l => !l.locked).map(l => l.id)); return }
    if (!selectedId) return
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); removeSelected(); return }
    if (meta && k === 'd') { e.preventDefault(); duplicate(selectedId); return }
    if (meta && k === 'c') { const l = findLayer(selectedId); if (l && !l.locked) clipRef.current = l; return }
    if (e.key.startsWith('Arrow')) {
      e.preventDefault(); const s = e.shiftKey ? 10 : 1
      const d = e.key === 'ArrowLeft' ? { x: -s } : e.key === 'ArrowRight' ? { x: s } : e.key === 'ArrowUp' ? { y: -s } : { y: s }
      snapshot(); setScene(sc => sc && ({ ...sc, artboards: sc.artboards.map(a => ({ ...a, layers: a.layers.map(l => {
        if (!selectedIds.includes(l.id) || l.locked) return l
        const dx = d.x || 0, dy = d.y || 0
        return l.type === 'line'
          ? { ...l, x: l.x + dx, y: l.y + dy, x2: l.x2 + dx, y2: l.y2 + dy } as Layer
          : { ...l, x: l.x + dx, y: l.y + dy } as Layer
      }) })) }))
    }
  }
  const addText = () => addLayer({ id: uid('t'), type: 'text', x: 80, y: 80, width: 600, text: 'New text', fontSize: 56, fontFamily: 'Inter', fill: '#111827', align: 'left' })
  const addRect = () => addLayer({ id: uid('r'), type: 'rect', x: 100, y: 100, width: 320, height: 220, fill: '#3b82f6', cornerRadius: 16 })
  const addEllipse = () => addLayer({ id: uid('e'), type: 'ellipse', x: 120, y: 120, width: 280, height: 280, fill: '#3b82f6' })
  const addTriangle = () => addLayer({ id: uid('tr'), type: 'triangle', x: 120, y: 120, width: 280, height: 240, fill: '#22c55e' })
  const addStar = () => addLayer({ id: uid('st'), type: 'star', x: 120, y: 120, width: 240, height: 240, fill: '#f59e0b', points: 5 })
  const addLine = () => addLayer({ id: uid('ln'), type: 'line', x: 120, y: 200, x2: 440, y2: 200, stroke: '#111827', strokeWidth: 8 })
  const addBlob = () => addLayer({ id: uid('bl'), type: 'path', x: 100, y: 100, width: 360, height: 360, d: blobPath(BLOB_BASE, 7 + Math.floor(Math.random() * 4), Math.random() * 100), fill: '#3b82f6', opacity: 0.9 })
  const doUpload = async (files: FileList | null) => {
    if (!files || !project) return
    const seq = ++actionSeq.current
    setUploading(true)
    try {
      for (const f of Array.from(files)) {
        await uploadFile(token, project.slug, f, 'artifacts/design/_assets')
        if (!mountedRef.current || seq !== actionSeq.current) return
      }
      if (mountedRef.current && seq === actionSeq.current) loadAssets()
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setUploading(false)
    }
  }
  const imageSize = (path: string) => new Promise<{ w: number; h: number }>(resolve => {
    const img = new window.Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve({ w: img.naturalWidth || img.width || 1, h: img.naturalHeight || img.height || 1 })
    img.onerror = () => resolve({ w: 1, h: 1 })
    img.src = resolveSrc(path)
  })
  const addImage = async (path: string) => {
    const { w, h } = await imageSize(path)
    if (!mountedRef.current) return
    const scale = Math.min((ab.width * 0.5) / Math.max(1, w), (ab.height * 0.5) / Math.max(1, h), 1)
    addLayer({ id: uid('i'), type: 'image', x: 64, y: 64, width: Math.max(24, Math.round(w * scale)), height: Math.max(24, Math.round(h * scale)), src: path })
  }
  const addBackgroundImage = (path: string) => {
    const id = uid('i')
    snapshot()
    setScene(s => s && ({ ...s, artboards: s.artboards.map((a, i) => i === focusAb
      ? { ...a, layers: [{ id, type: 'image', x: 0, y: 0, width: a.width, height: a.height, src: path, cropZoom: 1, cropX: 50, cropY: 50 } as ImageLayer, ...a.layers] }
      : a) }))
    setSelectedId(id)
  }
  const wantsBackgroundImage = (prompt: string, target?: Layer | null) => {
    if (/background|backdrop|wallpaper|texture|cover|hero bg|full[-\s]?bleed/i.test(prompt)) return true
    if (!target || target.type !== 'image') return false
    return target.x <= 8 && target.y <= 8 && target.width >= ab.width * 0.85 && target.height >= ab.height * 0.85
  }
  const buildImageContextPrompt = (raw: string, opts: { editId?: string; layerId?: string; sceneOverride?: Scene } = {}) => {
    const sc = opts.sceneOverride || scene
    const flat = sc.artboards.flatMap((a, ai) => a.layers.map(l => ({ a, ai, l })))
    const target = flat.find(x => x.l.id === (opts.layerId || opts.editId))
    const art = target?.a || sc.artboards[focusAb] || sc.artboards[0]
    const texts = art.layers.filter((l): l is TextLayer => l.type === 'text').slice(0, 8)
    const colors = Array.from(new Set(art.layers.flatMap(l => {
      const a = l as unknown as { fill?: string; stroke?: string; shadowColor?: string; glowColor?: string }
      return [a.fill, a.stroke, a.shadowColor, a.glowColor].filter((v): v is string => typeof v === 'string' && /^#/.test(v))
    }))).slice(0, 10)
    const fonts = Array.from(new Set(texts.map(t => t.fontFamily || 'Inter'))).slice(0, 5)
    const effects = art.layers.some(l => (l as { effects?: LayerEffect[] }).effects?.some(f => f.type === 'glow') || (l as TextLayer).glow) ? 'glow' : art.layers.some(l => (l as { shadow?: boolean }).shadow || (l as { effects?: LayerEffect[] }).effects?.some(f => f.type === 'drop-shadow')) ? 'soft shadow' : 'clean'
    const role = wantsBackgroundImage(raw, target?.l) ? 'background/backdrop' : opts.editId ? 'image edit' : 'design asset'
    return [
      `User image request: ${raw.trim()}`,
      `Create a ${role} for the current Design Studio composition "${sc.title || 'Untitled design'}".`,
      `Artboard: ${art.width}x${art.height}, background ${art.background}.`,
      target ? `Target layer frame: x ${Math.round(target.l.x)}, y ${Math.round(target.l.y)}, width ${Math.round((target.l as unknown as { width?: number }).width || 0)}, height ${Math.round((target.l as unknown as { height?: number }).height || 0)}.` : '',
      texts.length ? `Existing editable text, do not render these words inside the image: ${texts.map(t => `"${t.text.replace(/\s+/g, ' ').trim()}" at x ${Math.round(t.x)}, y ${Math.round(t.y)}`).join('; ')}.` : '',
      colors.length ? `Match this palette: ${colors.join(', ')}.` : '',
      fonts.length ? `Typography mood from editable layers: ${fonts.join(', ')}.` : '',
      `Visual treatment: ${effects}; match the current layout, spacing, and mood.`,
      wantsBackgroundImage(raw, target?.l)
        ? 'Because this is a background, keep it atmospheric and readable, avoid busy detail behind text, and leave useful negative space for the existing text layout.'
        : 'Avoid embedded words, logos, UI text, or watermarks unless the user explicitly asks for them.',
    ].filter(Boolean).join('\n')
  }
  const deleteAsset = async (path: string) => {
    if (!project) return
    const name = path.split('/').pop() || 'asset'
    const used = scene.artboards.some(a => a.layers.some(l => l.type === 'image' && l.src === path))
    const ok = await confirmDialog({
      title: `Delete ${name}?`,
      message: used
        ? 'This asset is used in the current design. Deleting it may make those image layers stop loading. This cannot be undone.'
        : 'This removes the asset from the shared Design library. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    if (!mountedRef.current) return
    const seq = ++actionSeq.current
    try {
      await deletePath(token, project.slug, path)
      if (mountedRef.current && seq === actionSeq.current) loadAssets()
    } catch (err) {
      if (mountedRef.current && seq === actionSeq.current) setChat(c => [...c, { role: 'assistant', content: 'Asset delete error: ' + String(err) }])
    }
  }
  // Map a layer/artboard frame to a provider size: portrait, landscape, or square.
  const sizeForFrame = (w?: number, h?: number): string => {
    if (!w || !h || !Number.isFinite(w) || !Number.isFinite(h)) return '1024x1024'
    const r = w / h
    return r >= 1.2 ? '1536x1024' : r <= 0.8 ? '1024x1536' : '1024x1024'
  }
  const genImage = async (prompt: string, editId?: string) => {
    if (!project || !prompt.trim() || imgBusy) return
    const seq = ++actionSeq.current
    startImgBusy(editId ? 'edit' : 'generate')
    try {
      const ed = editId ? findLayer(editId) : null
      const imagePath = ed && ed.type === 'image' && !/^(https?:|data:|blob:)/.test(ed.src) ? ed.src : (!editId && refImage ? refImage : undefined)
      const contextualPrompt = buildImageContextPrompt(prompt, { editId })
      const frame = ed as unknown as { width?: number; height?: number } | null
      const abFrame = scene.artboards[focusAb] || scene.artboards[0]
      const size = sizeForFrame(frame?.width ?? (wantsBackgroundImage(prompt) ? abFrame?.width : undefined), frame?.height ?? (wantsBackgroundImage(prompt) ? abFrame?.height : undefined))
      const r = await genDesignImageWithTimeout({ prompt: contextualPrompt, image: imagePath, size })
      if (!mountedRef.current || seq !== actionSeq.current) return
      loadAssets()
      if (editId && ed && ed.type === 'image') patchLayer(editId, { src: r.path } as Partial<Layer>)
      else if (wantsBackgroundImage(prompt)) addBackgroundImage(r.path)
      else await addImage(r.path)
      setImgPrompt(''); setRefImage(null)
    } catch (err) {
      if (mountedRef.current && seq === actionSeq.current) setChat(c => [...c, { role: 'assistant', content: 'Image error: ' + String(err) }])
    } finally {
      if (mountedRef.current && seq === actionSeq.current) stopImgBusy()
    }
  }
  // Resolve image layers the AI marked with src "gen:<prompt>" into real generated
  // images. Runs after a chat reply is applied AND on design load, so a half-finished
  // design (abandoned when the user switched tabs) completes itself on reopen.
  // Guards: genInFlightRef prevents apply+open double-generating (and double-billing)
  // the same layer; the swap is keyed to the scene id so switching designs mid-flight
  // never writes into the wrong scene. Not tied to actionSeq — an asset upload/delete
  // must not silently cancel the remaining layers.
  resolveGenRef.current = async (sc: Scene) => {
    if (!project) return
    const toGen = (sc.artboards.flatMap(a => a.layers.map(l => ({ l, a }))).filter(({ l }) => l.type === 'image' && /^gen:/i.test((l as { src: string }).src)) as { l: { id: string; src: string; width?: number; height?: number }; a: Artboard }[])
      .filter(({ l }) => !genInFlightRef.current.has(`${sc.id}:${l.id}`))
    if (!toGen.length) return
    toGen.forEach(({ l }) => genInFlightRef.current.add(`${sc.id}:${l.id}`))
    startImgBusy('resolve')
    let failed = 0
    for (const { l } of toGen) {
      try {
        const raw = l.src.replace(/^gen:/i, '').trim()
        const r = await genDesignImageWithTimeout({ prompt: buildImageContextPrompt(raw, { sceneOverride: sc, layerId: l.id }), size: sizeForFrame(l.width, l.height) })
        if (!mountedRef.current) return
        // Swap by layer id, only while the same scene is still on the canvas.
        setScene(s => s && s.id === sc.id ? { ...s, artboards: s.artboards.map(a => ({ ...a, layers: a.layers.map(x => x.id === l.id ? { ...x, src: r.path } as Layer : x) })) } : s)
      } catch {
        failed += 1 // leave gen: src so it retries next reopen
      } finally {
        genInFlightRef.current.delete(`${sc.id}:${l.id}`)
      }
    }
    if (mountedRef.current) {
      stopImgBusy(); loadAssets()
      if (failed) setChat(c => [...c, { role: 'assistant', content: `⚠️ ${failed} image${failed > 1 ? 's' : ''} could not be generated — they stay as placeholders and will retry when you reopen the design (or use "Generate image" on the layer).` }])
    }
  }
  const flat = scene.artboards.flatMap((a, ai) => a.layers.map(l => ({ l, ai })))
  type LayerRow = { kind: 'layer'; l: Layer; ai: number } | { kind: 'group'; id: string; name: string; layers: Layer[]; ai: number }
  const layerRows: LayerRow[] = []
  scene.artboards.forEach((a, ai) => {
    const emitted = new Set<string>()
    a.layers.slice().reverse().forEach(l => {
      if (l.groupId) {
        if (emitted.has(l.groupId)) return
        emitted.add(l.groupId)
        const grouped = a.layers.filter(x => x.groupId === l.groupId)
        layerRows.push({ kind: 'group', id: l.groupId, name: l.groupName || 'Group', layers: grouped, ai })
      } else {
        layerRows.push({ kind: 'layer', l, ai })
      }
    })
  })
  const moveLayer = (id: string, dir: -1 | 1) => { snapshot(); setScene(s => s && ({ ...s, artboards: s.artboards.map(a => { const i = a.layers.findIndex(l => l.id === id); if (i < 0) return a; const j = i + dir; if (j < 0 || j >= a.layers.length) return a; const ls = [...a.layers]; [ls[i], ls[j]] = [ls[j], ls[i]]; return { ...a, layers: ls } }) })) }
  const imageLoading = imgBusy ? <div className="ds-ai-loading" role="status" aria-live="polite">
    <span className="ds-ai-spinner" aria-hidden="true" />
    <span><strong>{imgBusyLabel}</strong><small>{fmtElapsed(imgElapsed)} elapsed · Codex generation can take about a minute</small></span>
    {imgElapsed >= 45 && <button type="button" className="ghost-button sm" onClick={stopImgBusy}>Reset UI</button>}
  </div> : null
  const pointInArtboard = (ai: number) => {
    const st = stageRef.current
    const p = st?.getPointerPosition()
    if (!p) return null
    return { x: (p.x - view.x) / view.scale - layout.xs[ai], y: (p.y - view.y) / view.scale - layout.ys[ai] }
  }
  const startMarquee = (ai: number, additive: boolean) => {
    const p = pointInArtboard(ai)
    if (!p) return
    setFocusAb(ai)
    marqueeRef.current = { ai, sx: p.x, sy: p.y, additive }
    const box = { ai, x: p.x, y: p.y, w: 0, h: 0 }
    marqueeBoxRef.current = box
    setMarquee(box)
    stageRef.current?.draggable(false)
    if (!additive) setSelectedId(null)
  }
  const updateMarquee = () => {
    const m = marqueeRef.current
    if (!m) return
    const p = pointInArtboard(m.ai)
    if (!p) return
    const x = Math.min(m.sx, p.x), y = Math.min(m.sy, p.y)
    const box = { ai: m.ai, x, y, w: Math.abs(p.x - m.sx), h: Math.abs(p.y - m.sy) }
    marqueeBoxRef.current = box
    setMarquee(box)
  }
  const finishMarquee = () => {
    const m = marqueeRef.current
    if (!m) return
    stageRef.current?.draggable(true)
    marqueeRef.current = null
    const box = marqueeBoxRef.current
    marqueeBoxRef.current = null
    setMarquee(null)
    if (!box || box.w < 3 || box.h < 3) return
    const hits = scene.artboards[m.ai].layers.filter(l => !l.locked).filter(l => {
      const b = getBox(l)
      return b.x < box.x + box.w && b.x + b.w > box.x && b.y < box.y + box.h && b.y + b.h > box.y
    }).map(l => l.id)
    setSelectedIds(prev => m.additive ? Array.from(new Set([...prev, ...hits])) : hits)
  }

  const send = async (text: string) => {
    if (!project || !scene || !text.trim() || chatBusy || chatBusyRun !== null) return
    const sc = scene
    setChat(c => [...c, { role: 'user', content: text }]); setChatBusy(true)
    try {
      if (!sessionRef.current) { const s = await createSession(token, { title: `Design: ${sc.title}`, project_slug: project.slug, profile_id: profileId ?? null, mode: 'design' }); sessionRef.current = s.id; setScene(cur => cur ? { ...cur, sessionId: s.id } : cur) }
      const sid = sessionRef.current
      let sel: { id: string; type: string; label: string } | null = null
      if (selectedId) { const l = findLayer(selectedId); if (l) sel = { id: l.id, type: l.type, label: l.type === 'text' ? (l as TextLayer).text.slice(0, 40) : l.type } }
      // Fire the run, then hand off to the event stream (onDesignEvent). No polling
      // loop: the reply arrives live and, crucially, survives navigating away — the
      // run keeps going server-side and reconnects on return (see hydrateChat).
      const r = await createRun(token, sid, { message: buildDesignPrompt(sc, sel, text), display_message: text, profile_id: profileId ?? null })
      // Snapshot what the agent saw (detects mid-run manual edits at apply time) and
      // mark the scene as awaiting this run (persisted → recovery-on-open is exact).
      sentSceneRef.current = { runId: r.run_id, body: JSON.stringify({ ...sc, runPendingId: undefined }) }
      setScene(cur => cur ? { ...cur, runPendingId: r.run_id } : cur)
      setChatBusyRun(r.run_id)
    } catch (e) { setChat(c => [...c, { role: 'assistant', content: 'Error: ' + String(e) }]) }
    finally { setChatBusy(false) }
  }
  sendRef.current = send

  const dl = (href: string, name: string) => { const a = document.createElement('a'); a.href = href; a.download = name; a.click() }
  const safeName = () => (scene.title || 'design').replace(/[^\w-]+/g, '_')
  const IMG_MIME = { png: 'image/png', jpg: 'image/jpeg', webp: 'image/webp' } as const
  // Raster export of the focused artboard (PNG keeps transparency; JPG/WebP flatten onto its bg).
  const exportImage = async (fmt: 'png' | 'jpg' | 'webp') => {
    const st = stageRef.current; if (!st) return
    // fit() first (like exportAll/exportPdf) — a panned/zoomed artboard extending
    // beyond the stage canvas would export blank/clipped pixels otherwise.
    setSelectedId(null); fit(); await new Promise(r => setTimeout(r, 350))
    const sc = st.scaleX(), vx = st.x(), vy = st.y()
    const wx = layout.xs[focusAb], wy = layout.ys[focusAb]
    const url = st.toDataURL({ x: wx * sc + vx, y: wy * sc + vy, width: ab.width * sc, height: ab.height * sc, pixelRatio: 1 / sc, mimeType: IMG_MIME[fmt], quality: 0.95 })
    dl(url, `${safeName()}${scene.artboards.length > 1 ? `-${focusAb + 1}` : ''}.${fmt}`)
  }
  const exportAll = async () => {
    const st = stageRef.current; if (!st) return
    setSelectedId(null); fit(); await new Promise(r => setTimeout(r, 350))
    const sc = st.scaleX(), vx = st.x(), vy = st.y()
    const JSZip = (await import('jszip')).default; const zip = new JSZip()
    scene.artboards.forEach((a, i) => { const url = st.toDataURL({ x: layout.xs[i] * sc + vx, y: layout.ys[i] * sc + vy, width: a.width * sc, height: a.height * sc, pixelRatio: 1 / sc }); zip.file(`${safeName()}-${i + 1}.png`, url.split(',')[1], { base64: true }) })
    dl(URL.createObjectURL(await zip.generateAsync({ type: 'blob' })), `${safeName()}.zip`)
  }
  const exportPdf = async () => {
    const st = stageRef.current; if (!st) return
    setSelectedId(null); fit(); await new Promise(r => setTimeout(r, 350))
    const sc = st.scaleX(), vx = st.x(), vy = st.y()
    const { jsPDF } = await import('jspdf')
    /* eslint-disable @typescript-eslint/no-explicit-any */
    let pdf: any = null
    try {
      scene.artboards.forEach((a, i) => {
        const url = st.toDataURL({ x: layout.xs[i] * sc + vx, y: layout.ys[i] * sc + vy, width: a.width * sc, height: a.height * sc, pixelRatio: 1 / sc })
        const o: 'landscape' | 'portrait' = a.width >= a.height ? 'landscape' : 'portrait'
        if (!pdf) pdf = new jsPDF({ orientation: o, unit: 'px', format: [a.width, a.height] }); else pdf.addPage([a.width, a.height], o)
        pdf.addImage(url, 'PNG', 0, 0, a.width, a.height)
        const visibleState = new pdf.GState({ opacity: 1 })
        const invisibleState = new pdf.GState({ opacity: 0.01 })
        a.layers.filter((l): l is TextLayer => l.type === 'text').forEach(t => {
          const txt = textDisplayValue(t)
          if (!txt.trim()) return
          pdf.setGState(invisibleState)
          pdf.setFont('helvetica', t.fontStyle?.includes('bold') ? 'bold' : t.fontStyle?.includes('italic') ? 'italic' : 'normal')
          pdf.setFontSize(t.fontSize)
          pdf.setTextColor(t.fill || '#111827')
          const maxWidth = Math.max(8, t.width)
          const lines = pdf.splitTextToSize(txt, maxWidth)
          const lineHeight = t.fontSize * (t.lineHeight || 1.2)
          const textHeight = lines.length * lineHeight
          const y0 = t.y + (t.verticalAlign === 'middle' && t.height ? (t.height - textHeight) / 2 : t.verticalAlign === 'bottom' && t.height ? t.height - textHeight : 0) + t.fontSize
          if (t.rotation) pdf.text(lines, t.x, y0, { angle: -t.rotation, maxWidth, align: t.align === 'justify' ? 'left' : (t.align || 'left'), lineHeightFactor: t.lineHeight || 1.2 })
          else pdf.text(lines, t.x, y0, { maxWidth, align: t.align === 'justify' ? 'left' : (t.align || 'left'), lineHeightFactor: t.lineHeight || 1.2 })
          pdf.setGState(visibleState)
        })
      })
    } finally {
      st.batchDraw()
    }
    if (pdf) pdf.save(`${safeName()}.pdf`)
  }
  // Self-contained HTML: each artboard a sized box, every layer an absolutely-positioned
  // element (text stays selectable; images inlined as data URLs; shapes as SVG/CSS).
  const exportHtml = async () => {
    const esc = (s: string) => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c))
    const cssFill = (l: FillStyle) => {
      const stops = l.gradientStops?.length ? [...l.gradientStops].sort((a, b) => a.offset - b.offset).map(s => `${s.color} ${Math.round(s.offset * 100)}%`).join(', ') : `${l.fill}, ${l.fill2 || l.fill}`
      return l.fillType === 'linear-gradient' ? `linear-gradient(${l.gradientAngle ?? 90}deg, ${stops})` : l.fillType === 'radial-gradient' ? `radial-gradient(circle, ${stops})` : l.fill
    }
    const cssRadius = (l: { cornerRadius?: number; cornerRadiusTL?: number; cornerRadiusTR?: number; cornerRadiusBR?: number; cornerRadiusBL?: number }) =>
      [l.cornerRadiusTL, l.cornerRadiusTR, l.cornerRadiusBR, l.cornerRadiusBL].some(v => v != null)
        ? `${l.cornerRadiusTL ?? l.cornerRadius ?? 0}px ${l.cornerRadiusTR ?? l.cornerRadius ?? 0}px ${l.cornerRadiusBR ?? l.cornerRadius ?? 0}px ${l.cornerRadiusBL ?? l.cornerRadius ?? 0}px`
        : `${l.cornerRadius || 0}px`
    const cssStroke = (l: { stroke?: string; strokeWidth?: number; strokeDash?: number; strokeOpacity?: number }) => l.stroke && l.strokeWidth !== 0 ? `border:${l.strokeWidth ?? 2}px ${l.strokeDash ? 'dashed' : 'solid'} ${l.stroke};${l.strokeOpacity != null ? `border-color:color-mix(in srgb, ${l.stroke} ${Math.round(l.strokeOpacity * 100)}%, transparent);` : ''}` : ''
    const cssEffects = (effects?: LayerEffect[]) => {
      const shadows = (effects || []).filter(f => f.type === 'drop-shadow' || f.type === 'glow').map(f => `${f.offsetX ?? 0}px ${f.offsetY ?? 0}px ${f.blur ?? 16}px ${f.spread ?? 0}px ${f.color || '#000000'}`)
      const blur = (effects || []).find(f => f.type === 'layer-blur')
      const bg = (effects || []).find(f => f.type === 'background-blur')
      return `${shadows.length ? `box-shadow:${shadows.join(',')};` : ''}${blur ? `filter:blur(${blur.blur ?? 8}px);` : ''}${bg ? `backdrop-filter:blur(${bg.blur ?? 8}px);` : ''}`
    }
    const cssTextFill = (t: TextLayer) => t.fillType && t.fillType !== 'solid' ? `color:transparent;background:${cssFill(t)};-webkit-background-clip:text;background-clip:text;` : `color:${t.fill};`
    const textValue = (t: TextLayer) => t.textTransform === 'uppercase' ? t.text.toUpperCase() : t.textTransform === 'lowercase' ? t.text.toLowerCase() : t.textTransform === 'capitalize' ? t.text.replace(/\b\w/g, c => c.toUpperCase()) : t.text
    const textDisplayValue = (t: TextLayer) => {
      const base = textValue(t)
      if (!t.listStyle || t.listStyle === 'none') return base
      return base.split('\n').map((line, i) => line.trim() ? `${t.listStyle === 'number' ? `${i + 1}.` : '•'} ${line}` : line).join('\n')
    }
    const cache: Record<string, string> = {}
    const printSize = (a: Artboard) => {
      const near = (x: number, y: number) => Math.abs(a.width - x) <= 2 && Math.abs(a.height - y) <= 2
      if (near(794, 1123)) return 'A4 portrait'
      if (near(1123, 794)) return 'A4 landscape'
      if (near(816, 1056)) return 'Letter portrait'
      if (near(1056, 816)) return 'Letter landscape'
      return `${a.width}px ${a.height}px`
    }
    const toDataUrl = async (src: string) => {
      if (cache[src]) return cache[src]
      if (/^gen:/i.test(src)) return ''
      const resolved = resolveSrc(src)
      if (!resolved) return ''
      try { const r = await fetch(resolved); const blob = await r.blob(); const d = await new Promise<string>(res => { const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.readAsDataURL(blob) }); cache[src] = d; return d } catch { return resolved }
    }
    const boards: string[] = []
    const pageStyles: string[] = []
    for (const [ai, a] of scene.artboards.entries()) {
      pageStyles.push(`@page p${ai}{size:${printSize(a)};margin:0}.p${ai}{page:p${ai}}`)
      const els: string[] = []
      for (const l of a.layers) {
        const at = `position:absolute;left:${l.x}px;top:${l.y}px;${l.rotation ? `transform:rotate(${l.rotation}deg);transform-origin:0 0;` : ''}${l.opacity != null && l.opacity !== 1 ? `opacity:${l.opacity};` : ''}`
        if (l.type === 'text') { const t = l as TextLayer; const tsh = cssTextShadow(t); els.push(`<div style="${at}width:${t.width}px;${t.height ? `height:${t.height}px;display:grid;align-content:${t.verticalAlign === 'middle' ? 'center' : t.verticalAlign === 'bottom' ? 'end' : 'start'};` : ''}font-family:'${t.fontFamily || 'Inter'}',sans-serif;font-size:${t.fontSize}px;font-weight:${t.fontStyle?.includes('bold') ? 700 : 400};font-style:${t.fontStyle?.includes('italic') ? 'italic' : 'normal'};${t.textDecoration ? `text-decoration:${t.textDecoration};` : ''}${cssTextFill(t)}${t.textStroke ? `-webkit-text-stroke:${t.textStrokeWidth ?? 1}px ${t.textStroke};` : ''}text-align:${t.align || 'left'};line-height:${t.lineHeight || 1.2};letter-spacing:${t.letterSpacing || 0}px;${tsh ? `text-shadow:${tsh};` : ''}${cssEffects(t.effects)}white-space:pre-wrap;overflow-wrap:break-word;">${esc(textDisplayValue(t))}</div>`) }
        else if (l.type === 'rect') { const r = l as RectLayer; els.push(`<div style="${at}width:${r.width}px;height:${r.height}px;background:${cssFill(r)};opacity:${(r.opacity ?? 1) * (r.fillOpacity ?? 1)};box-sizing:border-box;border-radius:${cssRadius(r)};${cssStroke(r)}${r.shadow ? 'box-shadow:0 20px 45px rgba(0,0,0,.30);' : ''}${cssEffects(r.effects)}"></div>`) }
        else if (l.type === 'ellipse') { const e = l as EllipseLayer; els.push(`<div style="${at}width:${e.width}px;height:${e.height}px;background:${cssFill(e)};opacity:${(e.opacity ?? 1) * (e.fillOpacity ?? 1)};border-radius:50%;box-sizing:border-box;${cssStroke(e)}${cssEffects(e.effects)}"></div>`) }
        else if (l.type === 'image') { const im = l as ImageLayer; const cx = im.cropX ?? 50, cy = im.cropY ?? 50, cz = im.cropZoom || 1; els.push(`<div style="${at}width:${im.width}px;height:${im.height}px;overflow:hidden;border-radius:${cssRadius(im)};${cssEffects(im.effects)}"><img src="${await toDataUrl(im.src)}" style="width:100%;height:100%;object-fit:cover;object-position:${cx}% ${cy}%;transform:scale(${cz});transform-origin:${cx}% ${cy}%;display:block;"/></div>`) }
        else if (l.type === 'line') { const ln = l as LineLayer; const len = Math.hypot(ln.x2 - ln.x, ln.y2 - ln.y), ang = Math.atan2(ln.y2 - ln.y, ln.x2 - ln.x) * 180 / Math.PI; els.push(`<svg style="position:absolute;left:${ln.x}px;top:${ln.y}px;overflow:visible;transform:rotate(${ang}deg);transform-origin:0 0;opacity:${(ln.opacity ?? 1) * (ln.strokeOpacity ?? 1)}" width="${len}" height="${Math.max(ln.strokeWidth, 1)}"><line x1="0" y1="${ln.strokeWidth / 2}" x2="${len}" y2="${ln.strokeWidth / 2}" stroke="${ln.stroke}" stroke-width="${ln.strokeWidth}" stroke-linecap="${ln.strokeCap || 'round'}" ${ln.strokeDash ? `stroke-dasharray="${ln.strokeDash} ${ln.strokeDash}"` : ''}/></svg>`) }
        else if (l.type === 'triangle') { const s = l as TriangleLayer; els.push(`<svg style="${at}" width="${s.width}" height="${s.height}" viewBox="0 0 ${s.width} ${s.height}"><defs>${s.fillType === 'linear-gradient' ? `<linearGradient id="g${s.id}" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${s.fill}"/><stop offset="1" stop-color="${s.fill2 || s.fill}"/></linearGradient>` : ''}</defs><polygon points="${s.width / 2},0 ${s.width},${s.height} 0,${s.height}" fill="${s.fillType === 'linear-gradient' ? `url(#g${s.id})` : s.fill}" stroke="${s.stroke || 'none'}" stroke-width="${s.strokeWidth ?? 0}"/></svg>`) }
        else if (l.type === 'star') { const s = l as StarLayer; const n = s.points || 5, cx = s.width / 2, cy = s.height / 2, ro = Math.min(s.width, s.height) / 2, ri = ro * 0.5, pts: string[] = []; for (let i = 0; i < n * 2; i++) { const rr = i % 2 ? ri : ro, ang = (Math.PI / n) * i - Math.PI / 2; pts.push(`${(cx + Math.cos(ang) * rr).toFixed(1)},${(cy + Math.sin(ang) * rr).toFixed(1)}`) } els.push(`<svg style="${at}" width="${s.width}" height="${s.height}"><polygon points="${pts.join(' ')}" fill="${s.fill}" stroke="${s.stroke || 'none'}" stroke-width="${s.strokeWidth ?? 0}"/></svg>`) }
        else if (l.type === 'path') { const s = l as PathLayer; els.push(`<svg style="${at}" width="${s.width}" height="${s.height}" viewBox="0 0 320 320" preserveAspectRatio="none"><path d="${s.d}" fill="${s.fill}" stroke="${s.stroke || 'none'}" stroke-width="${s.strokeWidth ?? 0}"/></svg>`) }
      }
      boards.push(`<div class="ab p${ai}" style="position:relative;width:${a.width}px;height:${a.height}px;background:${cssFill(artboardFill(a))};overflow:hidden;">${els.join('')}</div>`)
    }
    const fonts = '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@400;600;700&family=Montserrat:wght@400;600;700&family=Playfair+Display:wght@500;700&family=Oswald:wght@400;600&family=Bebas+Neue&family=Anton&family=Lora:wght@400;700&family=Archivo+Black&family=Sora:wght@400;700&family=Outfit:wght@400;700&family=Abril+Fatface&family=Pacifico&family=Dancing+Script&family=Permanent+Marker&family=Righteous&family=Caveat&family=Lobster&family=Merriweather&family=Raleway&family=Manrope&family=DM+Sans&family=Space+Grotesk&family=Nunito&family=Fraunces&family=Roboto+Slab&family=JetBrains+Mono&display=swap" rel="stylesheet">'
    const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${fonts}<title>${esc(scene.title || 'design')}</title><style>${pageStyles.join('')}*{box-sizing:border-box}html,body{margin:0}body{background:#1a1d24;display:flex;flex-direction:column;align-items:center;gap:24px;padding:24px}.ab{box-shadow:0 10px 40px rgba(0,0,0,.4);print-color-adjust:exact;-webkit-print-color-adjust:exact}@media print{html,body{width:auto;height:auto;background:#fff;padding:0}body{display:block}.ab{margin:0!important;box-shadow:none!important;break-after:page;page-break-after:always}.ab:last-child{break-after:auto;page-break-after:auto}}</style></head><body>${boards.join('')}</body></html>`
    dl(URL.createObjectURL(new Blob([html], { type: 'text/html' })), `${safeName()}.html`)
  }
  const applyArtboardPreset = (id: ArtboardPresetId | 'custom') => {
    if (id === 'custom') return
    const preset = ARTBOARD_PRESETS.find(p => p.id === id)
    if (!preset) return
    patchArtboard({ width: preset.w, height: preset.h })
    fittedFor.current = ''
  }
  const addArtboardWithSize = (width = ab.width, height = ab.height, background = ab.background) => {
    snapshot()
    const i = scene.artboards.length
    setScene(s => s && ({ ...s, artboards: [...s.artboards, { id: uid('a'), width, height, background, layers: [] }] }))
    setFocusAb(i)
    setSelectedId(null)
    fittedFor.current = ''
  }
  const addArtboard = () => addArtboardWithSize()
  const addPresetArtboard = (preset: ArtboardPreset) => {
    addArtboardWithSize(preset.w, preset.h, ab.background)
    setArtboardMenu(false)
  }
  const removeArtboard = () => { if (scene.artboards.length <= 1) return; snapshot(); const i = focusAb; setScene(s => s && ({ ...s, artboards: s.artboards.filter((_, k) => k !== i) })); setFocusAb(Math.max(0, i - 1)); setSelectedId(null); fittedFor.current = '' }

  return <section className={`design-studio ${isMobile ? 'is-mobile' : ''} ${panMode ? 'is-panning' : ''}`}>
    <div className="ds-toolbar">
      <BackButton label="Back" onClick={() => { flushSaveRef.current(); (onExit || (() => setStage(studioFrom.current)))() }} />
      <strong className="ds-title">{scene.title}</strong>
      <span className="muted ds-project-tag">· {cleanProjectName(project.name)}</span>
      <span className="ds-saved">{saved === 'saving' ? 'Saving…' : saved === 'saved' ? 'Saved' : ''}</span>
      <div className="ds-undo"><button className="ghost-button" disabled={!hist.current.undo.length} onClick={undo} title="Undo (⌘Z)">↶</button><button className="ghost-button" disabled={!hist.current.redo.length} onClick={redo} title="Redo (⌘⇧Z)">↷</button></div>
      <div className="ds-zoom"><button className="ghost-button" onClick={() => setView(v => ({ ...v, scale: Math.max(0.05, v.scale * 0.9) }))}>−</button><span>{Math.round(view.scale * 100)}%</span><button className="ghost-button" onClick={() => setView(v => ({ ...v, scale: Math.min(5, v.scale * 1.1) }))}>+</button><button className="ghost-button" onClick={fit}>Fit</button></div>
      <div className="ds-tools">
        <div className="ds-shape-dd">
          <button className="ghost-button" onClick={() => setArtboardMenu(v => !v)} title="Add an artboard / slide">+ Artboard ▾</button>
          {artboardMenu && <><div className="ds-ctx-scrim" onClick={() => setArtboardMenu(false)} /><div className="ds-shape-menu ds-artboard-menu">
            <button onClick={() => { addArtboard(); setArtboardMenu(false) }}>Same as current <small>{ab.width}x{ab.height}</small></button>
            <span className="ds-menu-label">Social</span>
            {SOCIAL_ARTBOARD_PRESETS.map(p => <button key={p.id} onClick={() => addPresetArtboard(p)}>{p.label} <small>{p.w}x{p.h}</small></button>)}
            <span className="ds-menu-label">PDF</span>
            {PDF_ARTBOARD_PRESETS.map(p => <button key={p.id} onClick={() => addPresetArtboard(p)}>{p.label} <small>{p.w}x{p.h}</small></button>)}
          </div></>}
        </div>
        <button className="ghost-button" onClick={addText}>+ Text</button>
        <div className="ds-shape-dd">
          <button className="ghost-button" onClick={() => setShapeMenu(v => !v)}>+ Shape ▾</button>
          {shapeMenu && <><div className="ds-ctx-scrim" onClick={() => setShapeMenu(false)} /><div className="ds-shape-menu">
            {([['Rectangle', addRect], ['Ellipse', addEllipse], ['Triangle', addTriangle], ['Star', addStar], ['Line', addLine], ['Blob', addBlob]] as const).map(([n, fn]) => <button key={n} onClick={() => { fn(); setShapeMenu(false) }}>{n}</button>)}
          </div></>}
        </div>
        <button className="ghost-button" onClick={() => void saveVersion()} disabled={savingVersion} title="Save this version to history">{savingVersion ? 'Saving…' : '⤓ Save'}</button>
        <div className="ds-shape-dd">
          <button className="ghost-button" onClick={() => setVersionMenu(v => { const n = !v; if (n) void loadVersions(); return n })} title="Saved versions">History ▾</button>
          {versionMenu && <><div className="ds-ctx-scrim" onClick={() => setVersionMenu(false)} /><div className="ds-shape-menu ds-version-menu" style={{ right: 0, left: 'auto' }}>
            <span className="ds-menu-label">Saved versions</span>
            {versions.length === 0
              ? <button disabled>No saved versions yet</button>
              : versions.map((v, i) => <button key={v.name} onClick={() => void restoreVersion(v.name)} title="Restore this saved version">{fmtVersionTs(v.ts)}{i === 0 ? ' · latest' : ''}</button>)}
          </div></>}
        </div>
        <div className="ds-shape-dd">
          <button className="primary-button" onClick={() => setExportMenu(v => !v)}>Export ▾</button>
          {exportMenu && <><div className="ds-ctx-scrim" onClick={() => setExportMenu(false)} /><div className="ds-shape-menu" style={{ right: 0, left: 'auto' }}>
            <span className="ds-menu-label">{scene.artboards.length > 1 ? `This artboard (${focusAb + 1})` : 'Image'}</span>
            <button onClick={() => { exportImage('png'); setExportMenu(false) }}>PNG</button>
            <button onClick={() => { exportImage('jpg'); setExportMenu(false) }}>JPG</button>
            <button onClick={() => { exportImage('webp'); setExportMenu(false) }}>WebP</button>
            <span className="ds-menu-label">Document</span>
            <button onClick={() => { void exportPdf(); setExportMenu(false) }}>PDF{scene.artboards.length > 1 ? ' (all slides)' : ''}</button>
            <button onClick={() => { void exportHtml(); setExportMenu(false) }}>HTML{scene.artboards.length > 1 ? ' (all slides)' : ''}</button>
            {scene.artboards.length > 1 && <button onClick={() => { void exportAll(); setExportMenu(false) }}>All PNGs (.zip)</button>}
          </div></>}
        </div>
      </div>
    </div>
    <div className={`ds-body ${!isMobile && leftCollapsed ? 'left-collapsed' : ''} ${!isMobile && rightCollapsed ? 'right-collapsed' : ''}`}>
      {!isMobile && leftCollapsed && <button className="ds-panel-reopen left" onClick={() => setLeftCollapsed(false)} title="Show panel">›</button>}
      <aside className={`ds-left ${isMobile && mSheet === 'panel' ? 'sheet-open' : ''}`}>
        <div className="ds-left-tabs">
          {(['chat', 'assets', 'layers'] as const).map(t => <button key={t} className={leftTab === t ? 'active' : ''} onClick={() => setLeftTab(t)}>{t === 'chat' ? 'Chat' : t === 'assets' ? 'Assets' : 'Layers'}</button>)}
          {!isMobile && <button className="ds-panel-collapse" onClick={() => setLeftCollapsed(true)} title="Hide panel">‹</button>}
        </div>
        <div className="ds-left-body">
          {leftTab === 'chat' && <div className="ds-chat">
            <div className="ds-chat-msgs">
              {chat.length === 0 && <p className="muted ds-tip">Ask the AI to design or change things. {selectedId ? 'Your selected element is sent as context.' : 'Tip: select an element first to edit just that one.'}</p>}
              {chat.map((m, i) => <div key={i} className={`ds-msg ${m.role}`}>
                {m.role !== 'assistant' ? m.content
                  : m.content.includes('<question-form')
                    ? splitOnQuestionForms(m.content).map((seg, si) => seg.kind === 'form'
                        ? <QuestionForm key={si} form={seg.form} disabled={chatBusy || chatBusyRun !== null || i !== chat.length - 1} onSubmit={t => send(t)} />
                        : (seg.text.trim() && <MessageContent key={si} content={seg.text} token={token} slug={project?.slug} />))
                    : <MessageContent content={m.content} token={token} slug={project?.slug} />}
              </div>)}
              {(chatBusy || chatBusyRun !== null) && <div className="ds-msg assistant pending"><span className="typing"><i /><i /><i /></span><span className="shimmer">Designing…</span></div>}
            </div>
            <div className="ds-chat-input">
              <Composer disabled={chatBusy || chatBusyRun !== null || !project} token={token} slug={project?.slug} attachIconOnly promptModes={false} placeholder={selectedId ? 'Edit the selected element or attach references…' : 'Describe a design change or attach references…'} onSubmit={async text => send(text)} />
            </div>
          </div>}
          {leftTab === 'assets' && <div className="ds-assets">
            <div className="ds-gen">
              <textarea rows={2} placeholder={refImage ? 'Describe what to make from the reference image…' : 'Generate an image with AI… e.g. dark coffee splash, top-down'} value={imgPrompt} onChange={e => setImgPrompt(e.target.value)} />
              {refImage && <span className="ds-ref-chip" title={refImage}>
                <img src={resolveSrc(refImage)} alt="" /> Ref: {refImage.split('/').pop()}
                <button type="button" aria-label="Clear reference" onClick={() => setRefImage(null)}>×</button>
              </span>}
              <button className="primary-button" disabled={imgBusy || !imgPrompt.trim()} onClick={() => void genImage(imgPrompt)}>{imgBusy ? 'Generating…' : refImage ? 'Generate from reference' : 'Generate image'}</button>
              {imageLoading}
            </div>
            <input ref={fileInput} type="file" accept="image/*" multiple hidden onChange={e => { doUpload(e.target.files); e.target.value = '' }} />
            <button className="ghost-button ds-upload" onClick={() => fileInput.current?.click()} disabled={uploading}>{uploading ? 'Uploading…' : 'Upload media'}</button>
            {assets.length ? <div className="ds-asset-grid">{assets.map(a => <div key={a} className={`ds-asset-card ${refImage === a ? 'is-ref' : ''}`}>
              <button className="ds-asset" onClick={() => void addImage(a)} title="Add to canvas"><img src={resolveSrc(a)} alt="" /></button>
              <button className="ds-asset-ref" type="button" aria-label="Use as AI reference" title="Use as reference for the next AI generation" onClick={e => { e.stopPropagation(); setRefImage(cur => cur === a ? null : a) }}>✦</button>
              <button className="ds-asset-delete" type="button" aria-label="Delete asset" title="Delete asset" onClick={e => { e.stopPropagation(); void deleteAsset(a) }}>×</button>
            </div>)}</div>
              : <p className="muted ds-tip">No media yet. Upload images (or generate them in chat with /image) to reuse across designs.</p>}
          </div>}
          {leftTab === 'layers' && <div className="ds-layers">{flat.length === 0 ? <p className="muted ds-tip">No layers yet.</p> : <>
            <div className="ds-layer-toolbar">
              <button className={`ghost-button ds-multitoggle ${multiMode ? 'active' : ''}`} onClick={() => setMultiMode(m => !m)}>{multiMode ? `Selecting (${selectedIds.length})` : 'Select multiple'}</button>
              <button className="ghost-button" onClick={selectAllLayers}>Select all</button>
            </div>
            {selectedIds.length > 1 && <div className="ds-layer-toolbar compact">
              <button className="ghost-button" onClick={groupSelected}>Group</button>
              <button className="ghost-button" onClick={ungroupSelected}>Ungroup</button>
            </div>}
            {layerRows.map(row => row.kind === 'group' ? (() => { const collapsed = collapsedGroups.has(row.id); return <React.Fragment key={row.id}>
              <div className={`ds-layer-row ds-layer-group ${row.layers.every(l => selectedIds.includes(l.id)) ? 'active' : ''} ${row.layers.every(l => l.locked) ? 'locked' : ''}`} onClick={e => { setFocusAb(row.ai); const ids = row.layers.map(l => l.id); if (multiMode || e.metaKey || e.ctrlKey || e.shiftKey) setSelectedIds(prev => ids.every(id => prev.includes(id)) ? prev.filter(id => !ids.includes(id)) : Array.from(new Set([...prev, ...ids]))); else setSelectedIds(ids) }}>
                <span className="ds-layer-name"><button className="ds-layer-caret" type="button" title={collapsed ? 'Expand group' : 'Collapse group'} onClick={e => { e.stopPropagation(); toggleGroupCollapsed(row.id) }}>{collapsed ? '▸' : '▾'}</button>{row.name} <small>{row.layers.length}</small>{scene.artboards.length > 1 ? ` · A${row.ai + 1}` : ''}</span>
                <span className="ds-layer-acts"><button onClick={e => { e.stopPropagation(); toggleGroupLock(row.id) }} title={row.layers.every(l => l.locked) ? 'Unlock group' : 'Lock group'}>{row.layers.every(l => l.locked) ? '🔒' : '🔓'}</button><button onClick={e => { e.stopPropagation(); moveGroup(row.id, 1) }} title="Bring forward">↑</button><button onClick={e => { e.stopPropagation(); moveGroup(row.id, -1) }} title="Send back">↓</button></span>
              </div>
              {!collapsed && row.layers.slice().reverse().map(l => <div key={l.id} className={`ds-layer-row ds-layer-child ${selectedIds.includes(l.id) ? 'active' : ''} ${l.locked ? 'locked' : ''}`} onClick={e => { setFocusAb(row.ai); if (multiMode || e.metaKey || e.ctrlKey || e.shiftKey) toggleSelect(l.id); else setSelectedId(l.id) }}>
                <span className="ds-layer-name">{l.locked ? '🔒 ' : ''}{l.type === 'text' ? ((l as TextLayer).text.slice(0, 22) || 'Text') : l.type === 'image' ? 'Image' : 'Shape'}</span>
                <span className="ds-layer-acts"><button onClick={e => { e.stopPropagation(); toggleLayerLock(l.id) }} title={l.locked ? 'Unlock layer' : 'Lock layer'}>{l.locked ? '🔒' : '🔓'}</button><button onClick={e => { e.stopPropagation(); moveLayer(l.id, 1) }} title="Bring forward">↑</button><button onClick={e => { e.stopPropagation(); moveLayer(l.id, -1) }} title="Send back">↓</button></span>
              </div>)}
            </React.Fragment> })() : <div key={row.l.id} className={`ds-layer-row ${selectedIds.includes(row.l.id) ? 'active' : ''} ${row.l.locked ? 'locked' : ''}`} onClick={e => { setFocusAb(row.ai); if (multiMode || e.metaKey || e.ctrlKey || e.shiftKey) toggleSelect(row.l.id); else setSelectedId(row.l.id) }}>
              <span className="ds-layer-name">{row.l.locked ? '🔒 ' : ''}{row.l.type === 'text' ? ((row.l as TextLayer).text.slice(0, 22) || 'Text') : row.l.type === 'image' ? 'Image' : 'Shape'}{scene.artboards.length > 1 ? ` · A${row.ai + 1}` : ''}</span>
              <span className="ds-layer-acts"><button onClick={e => { e.stopPropagation(); toggleLayerLock(row.l.id) }} title={row.l.locked ? 'Unlock layer' : 'Lock layer'}>{row.l.locked ? '🔒' : '🔓'}</button><button onClick={e => { e.stopPropagation(); moveLayer(row.l.id, 1) }} title="Bring forward">↑</button><button onClick={e => { e.stopPropagation(); moveLayer(row.l.id, -1) }} title="Send back">↓</button></span>
            </div>)}</>}</div>}
        </div>
      </aside>
      <div className="ds-canvas-wrap figma" ref={wrapRef} style={{ backgroundPosition: `${view.x}px ${view.y}px`, backgroundSize: `${24 * view.scale}px ${24 * view.scale}px` }}>
        <Stage ref={stageRef} width={box.w} height={box.h} x={view.x} y={view.y} scaleX={view.scale} scaleY={view.scale} onWheel={onWheel} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd}
          draggable={panMode}
          onMouseMove={updateMarquee}
          onMouseUp={e => { if (middlePan) setMiddlePan(false); finishMarquee(); if (e.target === stageRef.current) stageRef.current?.draggable(spacePan || (isMobile && mobileTool === 'pan')) }}
          onMouseLeave={e => { if (middlePan) setMiddlePan(false); finishMarquee(); if (e.target === stageRef.current) stageRef.current?.draggable(spacePan || (isMobile && mobileTool === 'pan')) }}
          onDragMove={e => { if (e.target === stageRef.current) setView(v => ({ ...v, x: e.target.x(), y: e.target.y() })) }}
          onDragEnd={e => { if (e.target === stageRef.current) setView(v => ({ ...v, x: e.target.x(), y: e.target.y() })) }}
          onMouseDown={e => {
            if (e.evt.button === 1) { e.evt.preventDefault(); setMiddlePan(true); stageRef.current?.draggable(true); return }
            if (e.target === e.target.getStage() && !panMode) setSelectedId(null)
          }}>
          <KLayer>
            {scene.artboards.map((a, ai) => <Group key={a.id} name="ab" x={layout.xs[ai]} y={layout.ys[ai]}
              {...(a.layers.some(l => l.id === selectedId) ? {} : { clipX: 0, clipY: 0, clipWidth: a.width, clipHeight: a.height })}>
              <Rect x={0} y={0} width={a.width} height={a.height} {...fillOf(artboardFill(a))} shadowColor="#000" shadowBlur={24} shadowOpacity={0.4} onMouseDown={e => { if (panMode) return; e.cancelBubble = true; startMarquee(ai, e.evt.metaKey || e.evt.ctrlKey || e.evt.shiftKey) }} />
              {a.layers.map(l => <LayerNode key={l.id} layer={l} resolveSrc={resolveSrc} onRef={n => { nodeRefs.current[l.id] = n }} onSelect={additive => { if (l.locked) return; setFocusAb(ai); if (additive) toggleSelect(l.id); else if (!selectedIds.includes(l.id)) setSelectedId(l.id) }} onChange={patch => patchLayer(l.id, patch)} onLiveChange={patch => patchLayerLive(l.id, patch)} onContext={(x, y) => { if (l.locked) return; setFocusAb(ai); if (!selectedIds.includes(l.id)) setSelectedId(l.id); setCtx({ x, y, id: l.id }) }} onEdit={() => { if (l.type === 'text' && !l.locked) startTextEdit(l.id) }} aw={a.width} ah={a.height} snapT={8 / view.scale} boxes={a.layers.map(getBox)} onGuides={lines => setGuides(lines.length ? { ai, lines } : null)} multi={selectedIds.length > 1 && selectedIds.includes(l.id) && !l.locked} onGroupStart={groupStart} onGroupEnd={commitGroup} onAltClone={() => { setSelectedId(l.id); cloneInPlace(l.id) }} editing={edit?.id === l.id} cropEditing={cropMode?.id === l.id} shiftRef={shiftRef} onGroupSnap={computeGroupSnap} mobileSnap={mobileSnap} panMode={panMode} />)}
              {selectedIds.length === 1 && (() => {
                const ln = a.layers.find(l => l.id === selectedId && l.type === 'line' && !l.locked) as LineLayer | undefined
                if (!ln) return null
                const hs = Math.max(9 / view.scale, 7)
                const snapPoint = (anchor: { x: number; y: number }, p: { x: number; y: number }) => {
                  const dx = p.x - anchor.x, dy = p.y - anchor.y
                  const len = Math.hypot(dx, dy)
                  if (!len || !shiftRef.current) return p
                  const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4)
                  return { x: Math.round(anchor.x + Math.cos(ang) * len), y: Math.round(anchor.y + Math.sin(ang) * len) }
                }
                const handle = (key: 'start' | 'end', x: number, y: number) => <Rect key={key} x={x - hs / 2} y={y - hs / 2} width={hs} height={hs} fill="#ffffff" stroke="#2563eb" strokeWidth={1 / view.scale} cornerRadius={hs / 2} draggable
                  onMouseDown={e => { e.cancelBubble = true; setSelectedId(ln.id) }}
                  onMouseEnter={e => { const st = e.target.getStage(); if (st) st.container().style.cursor = 'crosshair' }}
                  onMouseLeave={e => { const st = e.target.getStage(); if (st) st.container().style.cursor = '' }}
                  onDragStart={e => { e.cancelBubble = true; snapshot() }}
                  onDragMove={e => {
                    e.cancelBubble = true
                    const p = { x: Math.round(e.target.x() + hs / 2), y: Math.round(e.target.y() + hs / 2) }
                    const next = key === 'start' ? snapPoint({ x: ln.x2, y: ln.y2 }, p) : snapPoint({ x: ln.x, y: ln.y }, p)
                    patchLayerLive(ln.id, key === 'start' ? { x: next.x, y: next.y } as Partial<Layer> : { x2: next.x, y2: next.y } as Partial<Layer>)
                  }}
                  onDragEnd={e => { e.cancelBubble = true; e.target.position({ x: (key === 'start' ? ln.x : ln.x2) - hs / 2, y: (key === 'start' ? ln.y : ln.y2) - hs / 2 }) }}
                />
                return <Group key={'line-handles' + ln.id}>{handle('start', ln.x, ln.y)}{handle('end', ln.x2, ln.y2)}</Group>
              })()}
              {cropMode && (() => {
                const im = a.layers.find(l => l.id === cropMode.id && l.type === 'image') as ImageLayer | undefined
                if (!im) return null
                const hs = Math.max(10 / view.scale, 8)
                const handles = [
                  ['nw', im.x, im.y], ['n', im.x + im.width / 2, im.y], ['ne', im.x + im.width, im.y],
                  ['e', im.x + im.width, im.y + im.height / 2], ['se', im.x + im.width, im.y + im.height],
                  ['s', im.x + im.width / 2, im.y + im.height], ['sw', im.x, im.y + im.height], ['w', im.x, im.y + im.height / 2],
                ] as const
                return <Group key={'crop' + im.id}>
                  <Rect x={0} y={0} width={a.width} height={Math.max(0, im.y)} fill="rgba(15,23,42,.42)" listening={false} />
                  <Rect x={0} y={im.y + im.height} width={a.width} height={Math.max(0, a.height - im.y - im.height)} fill="rgba(15,23,42,.42)" listening={false} />
                  <Rect x={0} y={im.y} width={Math.max(0, im.x)} height={im.height} fill="rgba(15,23,42,.42)" listening={false} />
                  <Rect x={im.x + im.width} y={im.y} width={Math.max(0, a.width - im.x - im.width)} height={im.height} fill="rgba(15,23,42,.42)" listening={false} />
                  <Rect x={im.x} y={im.y} width={im.width} height={im.height} stroke="#ffffff" strokeWidth={1 / view.scale} dash={[6 / view.scale, 4 / view.scale]} listening={false} />
                  <Rect x={im.x} y={im.y} width={im.width} height={im.height} fill="rgba(255,255,255,.001)" draggable
                    onMouseDown={e => { e.cancelBubble = true; setSelectedId(im.id) }}
                    onMouseEnter={e => { const st = e.target.getStage(); if (st) st.container().style.cursor = 'move' }}
                    onMouseLeave={e => { const st = e.target.getStage(); if (st) st.container().style.cursor = '' }}
                    onWheel={e => { e.cancelBubble = true; e.evt.preventDefault(); const next = clamp((im.cropZoom || 1) * (e.evt.deltaY < 0 ? 1.08 : 0.926), 1, 4); patchLayerLive(im.id, { cropZoom: next } as Partial<Layer>) }}
                    onDragStart={e => { e.cancelBubble = true; cropDragRef.current = { id: im.id, x: im.x, y: im.y, cropX: im.cropX ?? 50, cropY: im.cropY ?? 50 } }}
                    onDragMove={e => {
                      e.cancelBubble = true
                      const d = cropDragRef.current
                      if (!d || d.id !== im.id) return
                      const dx = e.target.x() - d.x, dy = e.target.y() - d.y
                      const z = Math.max(1, im.cropZoom || 1)
                      patchLayerLive(im.id, { cropX: clamp(d.cropX - dx / Math.max(1, im.width) * 100 / z, 0, 100), cropY: clamp(d.cropY - dy / Math.max(1, im.height) * 100 / z, 0, 100) } as Partial<Layer>)
                      e.target.position({ x: im.x, y: im.y })
                    }}
                    onDragEnd={e => { e.cancelBubble = true; cropDragRef.current = null; e.target.position({ x: im.x, y: im.y }) }}
                  />
                  {handles.map(([h, x, y]) => <Rect key={h} x={x - hs / 2} y={y - hs / 2} width={hs} height={hs} fill="#ffffff" stroke="#2563eb" strokeWidth={1 / view.scale} cornerRadius={2 / view.scale} draggable
                    onMouseEnter={e => { const st = e.target.getStage(); if (st) st.container().style.cursor = h === 'n' || h === 's' ? 'ns-resize' : h === 'e' || h === 'w' ? 'ew-resize' : h === 'nw' || h === 'se' ? 'nwse-resize' : 'nesw-resize' }}
                    onMouseLeave={e => { const st = e.target.getStage(); if (st) st.container().style.cursor = '' }}
                    onDragStart={e => { e.cancelBubble = true; cropResizeRef.current = { id: im.id, handle: h, px: x - hs / 2, py: y - hs / 2, x: im.x, y: im.y, w: im.width, h: im.height } }}
                    onDragMove={e => {
                      e.cancelBubble = true
                      const d = cropResizeRef.current
                      if (!d || d.id !== im.id) return
                      const next = resizeCropFrame(d, e.target.x() - d.px, e.target.y() - d.py)
                      patchLayerLive(im.id, next as Partial<Layer>)
                      e.target.position({ x: d.px, y: d.py })
                    }}
                    onDragEnd={e => { e.cancelBubble = true; cropResizeRef.current = null; e.target.position({ x: x - hs / 2, y: y - hs / 2 }) }}
                  />)}
                </Group>
              })()}
              {a.layers.filter(l => selectedIds.includes(l.id)).map(l => { const b = getBox(l); return <Rect key={'sel' + l.id} x={b.x} y={b.y} width={b.w} height={b.h} stroke="#2563eb" strokeWidth={1 / view.scale} dash={[4 / view.scale, 3 / view.scale]} listening={false} /> })}
              {Array.from(new Set(a.layers.filter(l => l.groupId && selectedIds.includes(l.id)).map(l => l.groupId as string))).map(gid => {
                const members = a.layers.filter(l => l.groupId === gid)
                if (!members.length || !members.every(l => selectedIds.includes(l.id))) return null
                const gb = getBounds(members)
                return gb ? <Rect key={'grp' + gid} x={gb.x} y={gb.y} width={gb.w} height={gb.h} stroke="#0ea5e9" strokeWidth={1.25 / view.scale} listening={false} /> : null
              })}
              {marquee && marquee.ai === ai && <Rect x={marquee.x} y={marquee.y} width={marquee.w} height={marquee.h} fill="rgba(37,99,235,0.08)" stroke="#2563eb" strokeWidth={1 / view.scale} dash={[5 / view.scale, 4 / view.scale]} listening={false} />}
              {guides && guides.ai === ai && guides.lines.map((g, gi) => g.axis === 'x'
                ? <Line key={gi} points={[g.pos, 0, g.pos, a.height]} stroke="#ec4899" strokeWidth={1 / view.scale} listening={false} />
                : <Line key={gi} points={[0, g.pos, a.width, g.pos]} stroke="#ec4899" strokeWidth={1 / view.scale} listening={false} />)}
            </Group>)}
            <Transformer ref={trRef} rotateEnabled flipEnabled={false}
              keepRatio={selected?.type === 'line'} enabledAnchors={selected?.type === 'line' ? ['top-left', 'top-right', 'bottom-left', 'bottom-right'] : undefined}
              rotationSnaps={[0, 45, 90, 135, 180, 225, 270, 315]} rotationSnapTolerance={8}
              rotateAnchorOffset={isMobile ? 40 : 30} rotateAnchorCursor="grab"
              anchorSize={isMobile ? 20 : 11} anchorCornerRadius={isMobile ? 10 : 6} anchorStrokeWidth={1.5}
              borderStroke="#3b82f6" borderStrokeWidth={1.5} anchorStroke="#3b82f6" anchorFill="#ffffff"
              anchorStyleFunc={a => { if (a.hasName('rotater')) { a.cornerRadius(a.width() / 2); a.fill('#3b82f6'); a.stroke('#ffffff'); a.strokeWidth(2) } }} />
          </KLayer>
        </Stage>
        {scene.artboards.map((a, ai) => <div key={'ablbl' + a.id} className="ds-ab-label"
          style={{ left: view.x + layout.xs[ai] * view.scale, top: view.y + layout.ys[ai] * view.scale - 26 }}
          title="Drag to move this artboard"
          onPointerDown={e => { (e.target as HTMLElement).setPointerCapture(e.pointerId); snapshot(); abDrag.current = { ai, px: e.clientX, py: e.clientY, ax: layout.xs[ai], ay: layout.ys[ai] } }}
          onPointerMove={e => { const d = abDrag.current; if (!d || d.ai !== ai) return; const nx = Math.round(d.ax + (e.clientX - d.px) / view.scale), ny = Math.round(d.ay + (e.clientY - d.py) / view.scale); setScene(s => s && ({ ...s, artboards: s.artboards.map((ab2, i) => i === ai ? { ...ab2, x: nx, y: ny } : ab2) })) }}
          onPointerUp={() => { abDrag.current = null }}>
          {scene.artboards.length > 1 ? `Slide ${ai + 1}` : 'Artboard'} · {a.width}×{a.height}
        </div>)}
        <div className="ds-hint">Scroll = pan · ⌘/Ctrl+scroll = zoom · ⌘/Ctrl-drag = duplicate · hold Shift = snap · ⌘/Ctrl-click = multi-select · right-click = options</div>
        {imgBusy && <div className="ds-canvas-busy">{imageLoading}</div>}
        {ctx && <>
          <div className="ds-ctx-scrim" onClick={() => setCtx(null)} onContextMenu={e => { e.preventDefault(); setCtx(null) }} />
          <div className="ds-ctx" style={{ left: ctx.x, top: ctx.y }}>
            <button onClick={() => { bringToFront(ctx.id); setCtx(null) }}>Bring to front</button>
            <button onClick={() => { moveLayer(ctx.id, 1); setCtx(null) }}>Bring forward</button>
            <button onClick={() => { moveLayer(ctx.id, -1); setCtx(null) }}>Send backward</button>
            <button onClick={() => { sendToBack(ctx.id); setCtx(null) }}>Send to back</button>
            {findLayer(ctx.id)?.type === 'image' && <button onClick={() => { setImageAsBackground(ctx.id); setCtx(null) }}>Set as background</button>}
            <div className="ds-ctx-sep" />
            <button onClick={() => { duplicate(ctx.id); setCtx(null) }}>Duplicate</button>
            <button className="danger" onClick={() => { removeLayer(ctx.id); setCtx(null) }}>Delete</button>
          </div>
        </>}
      </div>
      <aside className={`ds-inspector ${isMobile && mSheet === 'inspector' ? 'sheet-open' : ''}`}>
        {!isMobile && <button className="ds-panel-collapse right" onClick={() => setRightCollapsed(true)} title="Hide inspector">›</button>}
        {selectedIds.length > 1 ? <div className="ds-fields">
          <div className="ds-insp-head"><strong>{selectedIds.length} selected</strong><button className="ghost-button danger" onClick={removeSelected}>Delete</button></div>
          <div className="ds-btn-row"><button onClick={groupSelected}>Group</button><button onClick={ungroupSelected}>Ungroup</button></div>
          <button className="ghost-button" onClick={saveComponentFromSelection}>Save as component</button>
          <span className="ds-section-label">Auto layout</span>
          {!selectedLayoutLeader ? <button className="ghost-button" onClick={enableAutoLayout}>Enable auto layout</button> : <>
            <label>Direction<DsSelect value={selectedLayoutLeader.layoutDirection || 'horizontal'} options={[{ value: 'horizontal', label: 'Horizontal' }, { value: 'vertical', label: 'Vertical' }]} onChange={v => arrangeAutoLayout(selectedIds, { layoutDirection: v as Layer['layoutDirection'] })} /></label>
            <div className="ds-row2"><NumberAdjuster label="Gap" value={selectedLayoutLeader.layoutGap ?? 16} min={0} max={240} step={1} onChange={v => arrangeAutoLayout(selectedIds, { layoutGap: v })} /><NumberAdjuster label="Padding" value={selectedLayoutLeader.layoutPadding ?? 16} min={0} max={240} step={1} onChange={v => arrangeAutoLayout(selectedIds, { layoutPadding: v })} /></div>
            <label>Align<DsSelect value={selectedLayoutLeader.layoutAlign || 'center'} options={[{ value: 'start', label: 'Start' }, { value: 'center', label: 'Center' }, { value: 'end', label: 'End' }]} onChange={v => arrangeAutoLayout(selectedIds, { layoutAlign: v as Layer['layoutAlign'] })} /></label>
          </>}
          <span className="srow-label" style={{ fontSize: 'var(--text-2xs)', color: 'var(--ui-text-tertiary)' }}>Align selected elements</span>
          <div className="ds-align">{([['l', '⇤', 'Left'], ['c', '↔', 'Center'], ['r', '⇥', 'Right'], ['t', '⤒', 'Top'], ['m', '↕', 'Middle'], ['b', '⤓', 'Bottom']] as const).map(([d, ic, t]) => <button key={d} title={t} onClick={() => alignSel(d)}>{ic}</button>)}</div>
          <p className="ds-tip muted">Drag any one to move them together. ⌘/Ctrl-click to add/remove.</p>
        </div> : !selected ? <div className="ds-fields">
          <div className="ds-insp-head"><strong>Artboard {scene.artboards.length > 1 ? focusAb + 1 : ''}</strong>{scene.artboards.length > 1 && <button className="ghost-button danger" onClick={removeArtboard}>Remove</button>}</div>
          <label>Preset<DsSelect value={artboardPresetValue(ab.width, ab.height)} options={[{ value: 'custom', label: `Custom (${ab.width}x${ab.height})` }, ...ARTBOARD_PRESETS.map(p => ({ value: p.id, label: `${p.label} (${p.w}x${p.h})` }))]} onChange={v => applyArtboardPreset(v as ArtboardPresetId | 'custom')} /></label>
          <div className="ds-row2"><NumberAdjuster label="Width" value={ab.width} min={16} max={4096} step={1} onChange={v => patchArtboard({ width: v })} /><NumberAdjuster label="Height" value={ab.height} min={16} max={4096} step={1} onChange={v => patchArtboard({ height: v })} /></div>
          <label>Background<DsSelect value={ab.backgroundType || 'solid'} options={FILL_TYPE_OPTIONS} onChange={v => patchArtboard({ backgroundType: v as Artboard['backgroundType'] })} /></label>
          <div className="ds-row2"><label>Color<ColorInput value={ab.background} onChange={v => patchArtboard({ background: v })} /></label>{ab.backgroundType && ab.backgroundType !== 'solid' ? <label>To<ColorInput value={ab.background2 || ab.background} onChange={v => patchArtboard({ background2: v })} /></label> : null}</div>
          {ab.backgroundType === 'linear-gradient' && <NumberAdjuster label="Angle" value={ab.backgroundAngle ?? 90} min={0} max={360} step={1} onChange={v => patchArtboard({ backgroundAngle: v })} />}
          {!!componentLibrary.length && <label>Insert component<DsSelect value="" placeholder="Choose component…" options={componentLibrary.map(c => ({ value: c.id, label: c.name }))} onChange={v => { if (v) insertComponent(v) }} /></label>}
          <p className="ds-tip muted">Click an element to edit it. Size & aspect ratio are fully editable.</p>
        </div> : <>
          <div className="ds-insp-head"><strong className="ds-cap">{selected.type}</strong><span className="ds-insp-actions"><button className="ghost-button" onClick={() => toggleLayerLock(selected.id)}>{selected.locked ? 'Unlock' : 'Lock'}</button><button className="ghost-button danger" disabled={!!selected.locked} onClick={removeSelected}>Delete</button></span></div>
          <div className="ds-fields">
            <PropertySection title="Basic" defaultOpen>
              <NumberAdjuster label="Opacity" value={selected.opacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(selected.id, { opacity: v } as Partial<Layer>)} />
              <button className="ghost-button ds-dup" onClick={() => duplicate(selected.id)}>Duplicate</button>
            </PropertySection>
          </div>
          {selected.type === 'text' && (() => { const t = selected as TextLayer; const bold = !!t.fontStyle?.includes('bold'); const ital = !!t.fontStyle?.includes('italic'); const setStyle = (b: boolean, i: boolean) => patchLayer(t.id, { fontStyle: [b ? 'bold' : '', i ? 'italic' : ''].filter(Boolean).join(' ') || 'normal' } as Partial<Layer>); return (
            <div className="ds-fields">
              <PropertySection title="Text" defaultOpen>
                <textarea rows={2} value={t.text} onChange={e => patchLayer(t.id, { text: e.target.value } as Partial<Layer>)} />
                <label>Font<DsSelect value={t.fontFamily || 'Inter'} options={FONTS.map(f => ({ value: f, label: f, style: { fontFamily: f } }))} onChange={v => patchLayer(t.id, { fontFamily: v } as Partial<Layer>)} /></label>
                <div className="ds-row2"><NumberAdjuster label="Size" value={t.fontSize} min={6} max={360} step={1} onChange={v => patchLayer(t.id, { fontSize: v } as Partial<Layer>)} /><NumberAdjuster label="Box height" value={t.height ?? Math.round(t.fontSize * (t.lineHeight || 1.2))} min={8} max={ab.height * 2} step={1} onChange={v => patchLayer(t.id, { height: v } as Partial<Layer>)} /></div>
                <div className="ds-btn-row">
                  <button className={bold ? 'active' : ''} title="Bold" style={{ fontWeight: 800 }} onClick={() => setStyle(!bold, ital)}>B</button>
                  <button className={ital ? 'active' : ''} title="Italic" style={{ fontStyle: 'italic' }} onClick={() => setStyle(bold, !ital)}>I</button>
                  <button className={t.textDecoration === 'underline' ? 'active' : ''} title="Underline" style={{ textDecoration: 'underline' }} onClick={() => patchLayer(t.id, { textDecoration: t.textDecoration === 'underline' ? '' : 'underline' } as Partial<Layer>)}>U</button>
                  <span className="ds-div" />
                  <button className={t.listStyle === 'bullet' ? 'active' : ''} title="Bullet list" onClick={() => patchLayer(t.id, { listStyle: t.listStyle === 'bullet' ? 'none' : 'bullet' } as Partial<Layer>)}>•</button>
                  <button className={t.listStyle === 'number' ? 'active' : ''} title="Numbered list" onClick={() => patchLayer(t.id, { listStyle: t.listStyle === 'number' ? 'none' : 'number' } as Partial<Layer>)}>1.</button>
                  <span className="ds-div" />
                  {(['left', 'center', 'right', 'justify'] as const).map((a, i) => <button key={a} className={(t.align || 'left') === a ? 'active' : ''} title={'Align ' + a} onClick={() => patchLayer(t.id, { align: a } as Partial<Layer>)}>{['⇤', '↔', '⇥', '☰'][i]}</button>)}
                </div>
                <div className="ds-row2"><label>Case<DsSelect value={t.textTransform || 'none'} options={[{ value: 'none', label: 'None' }, { value: 'uppercase', label: 'Uppercase' }, { value: 'lowercase', label: 'Lowercase' }, { value: 'capitalize', label: 'Capitalize' }]} onChange={v => patchLayer(t.id, { textTransform: v as TextLayer['textTransform'] } as Partial<Layer>)} /></label><label>Vertical<DsSelect value={t.verticalAlign || 'top'} options={[{ value: 'top', label: 'Top' }, { value: 'middle', label: 'Middle' }, { value: 'bottom', label: 'Bottom' }]} onChange={v => patchLayer(t.id, { verticalAlign: v as TextLayer['verticalAlign'] } as Partial<Layer>)} /></label></div>
                <div className="ds-row2"><NumberAdjuster label="Letter spacing" value={t.letterSpacing || 0} min={-8} max={32} step={0.5} onChange={v => patchLayer(t.id, { letterSpacing: v } as Partial<Layer>)} /><NumberAdjuster label="Line height" value={t.lineHeight || 1.2} min={0.7} max={3} step={0.05} onChange={v => patchLayer(t.id, { lineHeight: v } as Partial<Layer>)} /></div>
              </PropertySection>
              <PropertySection title="Fill" defaultOpen>
                <label>Fill type<DsSelect value={t.fillType || 'solid'} options={FILL_TYPE_OPTIONS} onChange={v => patchLayer(t.id, { fillType: v as TextLayer['fillType'] } as Partial<Layer>)} /></label>
                <div className="ds-row2"><label>Color<ColorInput value={t.fill} onChange={v => patchLayer(t.id, { fill: v } as Partial<Layer>)} /></label>{t.fillType && t.fillType !== 'solid' ? <label>To<ColorInput value={t.fill2 || t.fill} onChange={v => patchLayer(t.id, { fill2: v } as Partial<Layer>)} /></label> : <NumberAdjuster label="Fill opacity" value={t.fillOpacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(t.id, { fillOpacity: v } as Partial<Layer>)} />}</div>
                {t.fillType && t.fillType !== 'solid' && <>
                  <div className="ds-row2"><NumberAdjuster label="Angle" value={t.gradientAngle ?? 90} min={0} max={360} step={1} onChange={v => patchLayer(t.id, { gradientAngle: v } as Partial<Layer>)} /><NumberAdjuster label="Fill opacity" value={t.fillOpacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(t.id, { fillOpacity: v } as Partial<Layer>)} /></div>
                  <GradientEditor fill={t} width={t.width} height={t.height || Math.round(t.fontSize * (t.lineHeight || 1.2))} onChange={patch => patchLayer(t.id, patch as Partial<Layer>)} />
                </>}
              </PropertySection>
              <PropertySection title="Text stroke">
                <label className="ds-check"><input type="checkbox" checked={!!t.textStroke} onChange={e => patchLayer(t.id, { textStroke: e.target.checked ? '#111827' : undefined, textStrokeWidth: e.target.checked ? (t.textStrokeWidth ?? 2) : 0 } as Partial<Layer>)} /> Show text stroke</label>
                {t.textStroke && <div className="ds-row2"><label>Color<ColorInput value={t.textStroke} onChange={v => patchLayer(t.id, { textStroke: v } as Partial<Layer>)} /></label><NumberAdjuster label="Width" value={t.textStrokeWidth ?? 2} min={0} max={32} step={0.5} onChange={v => patchLayer(t.id, { textStrokeWidth: v } as Partial<Layer>)} /></div>}
              </PropertySection>
              <PropertySection title="Quick effects">
              <label className="ds-check"><input type="checkbox" checked={!!t.shadow} onChange={e => patchLayer(t.id, { shadow: e.target.checked, shadowColor: t.shadowColor || '#000000', shadowBlur: t.shadowBlur ?? 12, shadowOffsetX: t.shadowOffsetX ?? 0, shadowOffsetY: t.shadowOffsetY ?? 8, shadowOpacity: t.shadowOpacity ?? 0.35 } as Partial<Layer>)} /> Shadow</label>
              {t.shadow && <>
                <div className="ds-row2"><label>Shadow color<ColorInput value={t.shadowColor || '#000000'} onChange={v => patchLayer(t.id, { shadowColor: v } as Partial<Layer>)} /></label><NumberAdjuster label="Opacity" value={t.shadowOpacity ?? 0.35} min={0} max={1} step={0.01} onChange={v => patchLayer(t.id, { shadowOpacity: v } as Partial<Layer>)} /></div>
                <div className="ds-row2"><NumberAdjuster label="Blur" value={t.shadowBlur ?? 12} min={0} max={120} step={1} onChange={v => patchLayer(t.id, { shadowBlur: v } as Partial<Layer>)} /><NumberAdjuster label="Offset Y" value={t.shadowOffsetY ?? 8} min={-120} max={120} step={1} onChange={v => patchLayer(t.id, { shadowOffsetY: v } as Partial<Layer>)} /></div>
                <NumberAdjuster label="Offset X" value={t.shadowOffsetX ?? 0} min={-120} max={120} step={1} onChange={v => patchLayer(t.id, { shadowOffsetX: v } as Partial<Layer>)} />
              </>}
              <label className="ds-check"><input type="checkbox" checked={!!t.glow} onChange={e => patchLayer(t.id, { glow: e.target.checked, glowColor: t.glowColor || t.fill, glowBlur: t.glowBlur ?? 18, glowOpacity: t.glowOpacity ?? 0.6 } as Partial<Layer>)} /> Glow</label>
              {t.glow && <>
                <div className="ds-row2"><label>Glow color<ColorInput value={t.glowColor || t.fill} onChange={v => patchLayer(t.id, { glowColor: v } as Partial<Layer>)} /></label><NumberAdjuster label="Intensity" value={t.glowOpacity ?? 0.6} min={0} max={1} step={0.01} onChange={v => patchLayer(t.id, { glowOpacity: v } as Partial<Layer>)} /></div>
                <NumberAdjuster label="Glow size" value={t.glowBlur ?? 18} min={0} max={160} step={1} onChange={v => patchLayer(t.id, { glowBlur: v } as Partial<Layer>)} />
              </>}
              </PropertySection>
            </div>) })()}
          {(['rect', 'ellipse', 'triangle', 'star', 'path'] as const).includes(selected.type as 'rect') && (() => { const sh = selected as RectLayer; return (
            <div className="ds-fields">
              <PropertySection title="Fill" defaultOpen>
                <label>Type<DsSelect value={sh.fillType || 'solid'} options={FILL_TYPE_OPTIONS} onChange={v => patchLayer(sh.id, { fillType: v as FillStyle['fillType'] } as Partial<Layer>)} /></label>
                <div className="ds-row2"><label>Color<ColorInput value={sh.fill} onChange={v => patchLayer(sh.id, { fill: v } as Partial<Layer>)} /></label>{sh.fillType && sh.fillType !== 'solid' ? <label>To<ColorInput value={sh.fill2 || sh.fill} onChange={v => patchLayer(sh.id, { fill2: v } as Partial<Layer>)} /></label> : <NumberAdjuster label="Fill opacity" value={sh.fillOpacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(sh.id, { fillOpacity: v } as Partial<Layer>)} />}</div>
                {sh.fillType && sh.fillType !== 'solid' && <>
                  <div className="ds-row2"><NumberAdjuster label="Angle" value={sh.gradientAngle ?? 90} min={0} max={360} step={1} onChange={v => patchLayer(sh.id, { gradientAngle: v } as Partial<Layer>)} /><NumberAdjuster label="Fill opacity" value={sh.fillOpacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(sh.id, { fillOpacity: v } as Partial<Layer>)} /></div>
                  <GradientEditor fill={sh} width={sh.width} height={sh.height} onChange={patch => patchLayer(sh.id, patch as Partial<Layer>)} />
                </>}
              </PropertySection>
              {selected.type === 'rect' && <>
                <PropertySection title="Corner radius">
                  <NumberAdjuster label="Corner radius" value={sh.cornerRadius ?? 0} min={0} max={240} step={1} onChange={v => patchLayer(sh.id, { cornerRadius: v, cornerRadiusTL: v, cornerRadiusTR: v, cornerRadiusBR: v, cornerRadiusBL: v } as Partial<Layer>)} />
                  <div className="ds-row2"><NumberAdjuster label="Top left" value={sh.cornerRadiusTL ?? sh.cornerRadius ?? 0} min={0} max={240} step={1} onChange={v => patchLayer(sh.id, { cornerRadiusTL: v } as Partial<Layer>)} /><NumberAdjuster label="Top right" value={sh.cornerRadiusTR ?? sh.cornerRadius ?? 0} min={0} max={240} step={1} onChange={v => patchLayer(sh.id, { cornerRadiusTR: v } as Partial<Layer>)} /></div>
                  <div className="ds-row2"><NumberAdjuster label="Bottom right" value={sh.cornerRadiusBR ?? sh.cornerRadius ?? 0} min={0} max={240} step={1} onChange={v => patchLayer(sh.id, { cornerRadiusBR: v } as Partial<Layer>)} /><NumberAdjuster label="Bottom left" value={sh.cornerRadiusBL ?? sh.cornerRadius ?? 0} min={0} max={240} step={1} onChange={v => patchLayer(sh.id, { cornerRadiusBL: v } as Partial<Layer>)} /></div>
                </PropertySection>
              </>}
              <PropertySection title="Border">
                <label className="ds-check"><input type="checkbox" checked={!!sh.stroke} onChange={e => patchLayer(sh.id, { stroke: e.target.checked ? '#111827' : undefined, strokeWidth: 3 } as Partial<Layer>)} /> Show border</label>
                {sh.stroke && <>
                  <div className="ds-row2"><label>Color<ColorInput value={sh.stroke} onChange={v => patchLayer(sh.id, { stroke: v } as Partial<Layer>)} /></label><NumberAdjuster label="Width" value={sh.strokeWidth ?? 3} min={0} max={80} step={1} onChange={v => patchLayer(sh.id, { strokeWidth: v } as Partial<Layer>)} /></div>
                  <div className="ds-row2"><NumberAdjuster label="Opacity" value={sh.strokeOpacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(sh.id, { strokeOpacity: v } as Partial<Layer>)} /><NumberAdjuster label="Dash" value={sh.strokeDash ?? 0} min={0} max={80} step={1} onChange={v => patchLayer(sh.id, { strokeDash: v || undefined } as Partial<Layer>)} /></div>
                  <div className="ds-row2"><label>Cap<DsSelect value={sh.strokeCap || 'round'} options={STROKE_CAP_OPTIONS} onChange={v => patchLayer(sh.id, { strokeCap: v as RectLayer['strokeCap'] } as Partial<Layer>)} /></label><label>Position<DsSelect value={sh.strokePosition || 'center'} options={[{ value: 'center', label: 'Center' }, { value: 'inside', label: 'Inside' }, { value: 'outside', label: 'Outside' }]} onChange={v => patchLayer(sh.id, { strokePosition: v as RectLayer['strokePosition'] } as Partial<Layer>)} /></label></div>
                </>}
              </PropertySection>
              <PropertySection title="Quick effects">
                <label className="ds-check"><input type="checkbox" checked={!!sh.shadow} onChange={e => patchLayer(sh.id, { shadow: e.target.checked } as Partial<Layer>)} /> Drop shadow</label>
              </PropertySection>
            </div>) })()}
          {selected.type === 'line' && <div className="ds-fields">
            <PropertySection title="Line" defaultOpen>
              <div className="ds-row2"><label>Color<ColorInput value={(selected as { stroke: string }).stroke} onChange={v => patchLayer(selected.id, { stroke: v } as Partial<Layer>)} /></label><NumberAdjuster label="Thickness" value={(selected as { strokeWidth: number }).strokeWidth} min={0} max={120} step={1} onChange={v => patchLayer(selected.id, { strokeWidth: v } as Partial<Layer>)} /></div>
              <div className="ds-row2"><NumberAdjuster label="Opacity" value={(selected as LineLayer).strokeOpacity ?? 1} min={0} max={1} step={0.01} onChange={v => patchLayer(selected.id, { strokeOpacity: v } as Partial<Layer>)} /><NumberAdjuster label="Dash" value={(selected as LineLayer).strokeDash ?? 0} min={0} max={80} step={1} onChange={v => patchLayer(selected.id, { strokeDash: v || undefined } as Partial<Layer>)} /></div>
              <label>Cap<DsSelect value={(selected as LineLayer).strokeCap || 'round'} options={STROKE_CAP_OPTIONS} onChange={v => patchLayer(selected.id, { strokeCap: v as LineLayer['strokeCap'] } as Partial<Layer>)} /></label>
              <div className="ds-row2"><label className="ds-check"><input type="checkbox" checked={!!(selected as LineLayer).startArrow} onChange={e => patchLayer(selected.id, { startArrow: e.target.checked } as Partial<Layer>)} /> Start arrow</label><label className="ds-check"><input type="checkbox" checked={!!(selected as LineLayer).endArrow} onChange={e => patchLayer(selected.id, { endArrow: e.target.checked } as Partial<Layer>)} /> End arrow</label></div>
            </PropertySection>
          </div>}
          {selected.type === 'image' && (() => { const im = selected as ImageLayer; return <div className="ds-fields">
            <PropertySection title="Image" defaultOpen>
              <NumberAdjuster label="Corner radius" value={(selected as { cornerRadius?: number }).cornerRadius || 0} min={0} max={240} step={1} onChange={v => patchLayer(selected.id, { cornerRadius: v } as Partial<Layer>)} />
            </PropertySection>
            <PropertySection title="Crop">
              {cropMode?.id === im.id
                ? <div className="ds-btn-row"><button className="primary-button" onClick={applyCrop}>Apply</button><button onClick={cancelCrop}>Cancel</button></div>
                : <button className="ghost-button" onClick={() => startCrop(im)}>Crop</button>}
              <NumberAdjuster label="Zoom" value={im.cropZoom || 1} min={1} max={4} step={0.05} onChange={v => setImageCrop(im.id, { cropZoom: v })} />
              <div className="ds-row2"><NumberAdjuster label="X" value={Math.round(im.cropX ?? 50)} min={0} max={100} step={1} onChange={v => setImageCrop(im.id, { cropX: v })} /><NumberAdjuster label="Y" value={Math.round(im.cropY ?? 50)} min={0} max={100} step={1} onChange={v => setImageCrop(im.id, { cropY: v })} /></div>
              <button className="ghost-button" onClick={() => setImageCrop(im.id, { cropZoom: undefined, cropX: undefined, cropY: undefined })}>Reset crop</button>
            </PropertySection>
            <PropertySection title="Edit with AI">
              <textarea rows={2} placeholder="e.g. make it blue, add snow, remove background" value={imgPrompt} onChange={e => setImgPrompt(e.target.value)} />
              <button className="primary-button" disabled={!imageEditReady || imgBusy || !imgPrompt.trim()} title={imageEditReady ? undefined : 'The selected image provider is text-to-image only — connect xAI OAuth or switch the provider in Settings → Image generation to edit images.'} onClick={() => void genImage(imgPrompt, selected.id)}>{imgBusy ? 'Editing…' : 'Edit with AI'}</button>
              {!imageEditReady && <p className="ds-tip muted">{imageProviderKind === 'codex' ? 'Current provider is Codex, which supports text-to-image only. Use xAI, Higgsfield, or an OpenAI-compatible provider in Settings for true image edits.' : 'Use an edit-capable image provider in Settings to enable image edits.'}</p>}
            </PropertySection>
          </div> })()}
          <div className="ds-fields ds-optional-props">
            <PropertySection title="Advanced effects">
              <EffectStackEditor effects={selected.effects} onChange={effects => patchLayer(selected.id, { effects } as Partial<Layer>)} />
            </PropertySection>
          </div>
        </>}
      </aside>
      {!isMobile && rightCollapsed && <button className="ds-panel-reopen right" onClick={() => setRightCollapsed(false)} title="Show inspector">‹</button>}
    </div>
    {edit && (() => {
      const tl = findLayer(edit.id) as TextLayer | null; if (!tl) return null; const sc = view.scale
      return <textarea className="ds-text-edit" autoFocus value={edit.value}
        style={{ position: 'fixed', left: edit.x, top: edit.y, width: Math.max(40, tl.width * sc),
          fontFamily: tl.fontFamily || 'Inter', fontSize: tl.fontSize * sc,
          fontWeight: tl.fontStyle?.includes('bold') ? 700 : 400, fontStyle: tl.fontStyle?.includes('italic') ? 'italic' : 'normal',
          textDecorationLine: (tl.textDecoration as 'underline') || 'none', color: tl.fill, caretColor: tl.fill,
          textAlign: tl.align || 'left', lineHeight: String(tl.lineHeight || 1.2), letterSpacing: `${(tl.letterSpacing || 0) * sc}px`, textShadow: cssTextShadow(tl, sc) }}
        onChange={e => setEdit({ ...edit, value: e.target.value })}
        onBlur={() => { patchLayer(edit.id, { text: edit.value } as Partial<Layer>); setEdit(null) }}
        onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); (e.target as HTMLTextAreaElement).blur() } }} />
    })()}
    {isMobile && <>
      {mSheet && <div className="ds-sheet-scrim" onClick={() => setMSheet(null)} />}
      {mSheet === 'add' && <div className="ds-add-sheet">
        <button className="ghost-button" onClick={() => { addText(); setMSheet(null) }}>+ Text</button>
        {(['Rectangle', 'Ellipse', 'Triangle', 'Star', 'Line', 'Blob'] as const).map(n => <button key={n} className="ghost-button" onClick={() => { ({ Rectangle: addRect, Ellipse: addEllipse, Triangle: addTriangle, Star: addStar, Line: addLine, Blob: addBlob } as const)[n](); setMSheet(null) }}>+ {n}</button>)}
        <button className="ghost-button" onClick={() => { addArtboard(); setMSheet(null) }}>+ Artboard</button>
        <button className="ghost-button" onClick={() => { fileInput.current?.click(); setMSheet(null) }}>⬆ Upload image</button>
      </div>}
      <nav className="ds-mbar">
        <button onClick={() => setMSheet(m => m === 'add' ? null : 'add')} className={mSheet === 'add' ? 'on' : ''}>＋<small>Add</small></button>
        <button onClick={() => { setLeftTab('chat'); setMSheet('panel') }} className={mSheet === 'panel' && leftTab === 'chat' ? 'on' : ''}>✦<small>AI</small></button>
        <button onClick={() => { setLeftTab('layers'); setMSheet('panel') }} className={mSheet === 'panel' && leftTab === 'layers' ? 'on' : ''}>☰<small>Layers</small></button>
        <button onClick={() => setMSheet('inspector')} className={mSheet === 'inspector' ? 'on' : ''}>◧<small>{selected ? 'Edit' : 'Page'}</small></button>
        <button onClick={() => { setMobileTool(t => t === 'pan' ? 'select' : 'pan'); setMSheet(null) }} className={mobileTool === 'pan' ? 'on' : ''}>✋<small>{mobileTool === 'pan' ? 'Pan' : 'Select'}</small></button>
        <button onClick={() => setSnapOn(s => !s)} className={snapOn ? 'on' : ''}>⌖<small>Snap</small></button>
        <button onClick={fit}>⤢<small>Fit</small></button>
      </nav>
    </>}
  </section>
}

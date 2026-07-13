import React from 'react'
import { createPortal } from 'react-dom'

// A color swatch button + custom popover picker (HSV square, hue slider, hex input,
// preset swatches). Replaces the native <input type="color"> whose OS popup gets
// clipped at the screen edge — this one is portalled to <body> and flips to stay in
// the viewport. Emits a #rrggbb hex string via onChange.

function hexToRgb(hex: string): [number, number, number] {
  const m = (hex || '#000000').replace('#', '')
  const n = m.length === 3 ? m.split('').map(c => c + c).join('') : m.padEnd(6, '0').slice(0, 6)
  return [parseInt(n.slice(0, 2), 16) || 0, parseInt(n.slice(2, 4), 16) || 0, parseInt(n.slice(4, 6), 16) || 0]
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
function rgbToHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map(v => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0')).join('')
}
function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min
  let h = 0
  if (d) {
    if (max === r) h = ((g - b) / d) % 6
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60; if (h < 0) h += 360
  }
  return [h, max ? d / max : 0, max]
}
function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return [(r + m) * 255, (g + m) * 255, (b + m) * 255]
}

const PRESETS = ['#000000', '#ffffff', '#64748b', '#ef4444', '#f97316', '#f59e0b', '#eab308', '#22c55e', '#14b8a6', '#3b82f6', '#6366f1', '#a855f7', '#ec4899', '#0b1020']

export function ColorInput({ value, onChange }: { value: string; onChange: (hex: string) => void }) {
  const [open, setOpen] = React.useState(false)
  const [pos, setPos] = React.useState<{ top: number; left: number } | null>(null)
  const btnRef = React.useRef<HTMLButtonElement>(null)
  const svRef = React.useRef<HTMLDivElement>(null)
  const hueRef = React.useRef<HTMLDivElement>(null)
  const [hsv, setHsv] = React.useState(() => rgbToHsv(...hexToRgb(value)))
  const [hex, setHex] = React.useState(value)

  // Keep internal state in sync when the value prop changes from outside (undo, agent
  // reply, selecting a different layer) — but not while a drag is rewriting it.
  const draggingRef = React.useRef(false)
  React.useEffect(() => {
    if (draggingRef.current) return
    setHex(value); setHsv(rgbToHsv(...hexToRgb(value)))
  }, [value])

  const place = React.useCallback(() => {
    const r = btnRef.current?.getBoundingClientRect(); if (!r) return
    const W = 232, H = 268, M = 8
    let left = r.left
    let top = r.bottom + 6
    if (left + W > window.innerWidth - M) left = window.innerWidth - W - M
    if (left < M) left = M
    if (top + H > window.innerHeight - M) top = Math.max(M, r.top - H - 6) // flip above
    setPos({ top, left })
  }, [])

  const openPop = () => { place(); setOpen(true) }
  React.useEffect(() => {
    if (!open) return
    const onScroll = () => place()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', place)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('scroll', onScroll, true); window.removeEventListener('resize', place); window.removeEventListener('keydown', onKey) }
  }, [open, place])

  const emit = (h: number, s: number, v: number) => {
    const next = rgbToHex(...hsvToRgb(h, s, v))
    setHsv([h, s, v]); setHex(next); onChange(next)
  }
  const dragSV = (e: React.PointerEvent) => {
    draggingRef.current = true
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    const move = (cx: number, cy: number) => {
      const r = svRef.current?.getBoundingClientRect(); if (!r) return
      emit(hsv[0], clamp((cx - r.left) / r.width, 0, 1), clamp(1 - (cy - r.top) / r.height, 0, 1))
    }
    move(e.clientX, e.clientY)
  }
  const dragHue = (e: React.PointerEvent) => {
    draggingRef.current = true
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    const move = (cx: number) => {
      const r = hueRef.current?.getBoundingClientRect(); if (!r) return
      emit(clamp((cx - r.left) / r.width, 0, 1) * 360, hsv[1], hsv[2])
    }
    move(e.clientX)
  }
  const onSVMove = (e: React.PointerEvent) => { if (draggingRef.current && (e.buttons & 1)) { const r = svRef.current?.getBoundingClientRect(); if (r) emit(hsv[0], clamp((e.clientX - r.left) / r.width, 0, 1), clamp(1 - (e.clientY - r.top) / r.height, 0, 1)) } }
  const onHueMove = (e: React.PointerEvent) => { if (draggingRef.current && (e.buttons & 1)) { const r = hueRef.current?.getBoundingClientRect(); if (r) emit(clamp((e.clientX - r.left) / r.width, 0, 1) * 360, hsv[1], hsv[2]) } }
  const endDrag = () => { draggingRef.current = false }

  const [h, s, v] = hsv
  const hueHex = rgbToHex(...hsvToRgb(h, 1, 1))
  const commitHex = (raw: string) => {
    const t = raw.trim().replace(/^#?/, '#')
    setHex(t)
    if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(t)) { setHsv(rgbToHsv(...hexToRgb(t))); onChange(rgbToHex(...hexToRgb(t))) }
  }
  // Eyedropper — sample a colour from anywhere. Prefer the native browser EyeDropper
  // (Chromium, whole screen); where it's unavailable (e.g. a non-secure-context host),
  // fall back to a canvas pick handled by whatever surface is listening (Design Studio
  // samples its rendered canvas), dispatched as an event with an apply callback.
  const pickScreen = async () => {
    if (typeof window !== 'undefined' && 'EyeDropper' in window) {
      try {
        const ED = (window as unknown as { EyeDropper: new () => { open: () => Promise<{ sRGBHex: string }> } }).EyeDropper
        const r = await new ED().open()
        if (r?.sRGBHex) commitHex(r.sRGBHex)
        return
      } catch { return /* cancelled */ }
    }
    setOpen(false)
    window.dispatchEvent(new CustomEvent('proxima:eyedropper', { detail: { apply: (hex: string) => commitHex(hex) } }))
  }

  return <>
    <button ref={btnRef} type="button" className="ds-color-swatch" onClick={openPop} title={hex}>
      <span style={{ background: value }} />
    </button>
    {open && pos && createPortal(
      <>
        <div className="ds-color-scrim" onPointerDown={() => setOpen(false)} />
        <div className="ds-color-pop" style={{ top: pos.top, left: pos.left }}>
          <div ref={svRef} className="ds-color-sv" style={{ background: `linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, ${hueHex})` }}
            onPointerDown={dragSV} onPointerMove={onSVMove} onPointerUp={endDrag}>
            <span className="ds-color-knob" style={{ left: `${s * 100}%`, top: `${(1 - v) * 100}%`, background: hex }} />
          </div>
          <div ref={hueRef} className="ds-color-hue" onPointerDown={dragHue} onPointerMove={onHueMove} onPointerUp={endDrag}>
            <span className="ds-color-knob hue" style={{ left: `${(h / 360) * 100}%` }} />
          </div>
          <div className="ds-color-row">
            <input className="ds-color-hex" value={hex} onChange={e => commitHex(e.target.value)} spellCheck={false} />
            <button type="button" className="ds-color-eyedrop" title="Eyedropper — pick a colour from the screen / canvas" onClick={() => void pickScreen()}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m2 22 1-1h3l9-9" /><path d="M3 21v-3l9-9" /><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.4.4a2.1 2.1 0 1 1-3 3l-3.8-3.8a2.1 2.1 0 1 1 3-3l.4.4Z" /></svg>
            </button>
            <span className="ds-color-preview" style={{ background: hex }} />
          </div>
          <div className="ds-color-presets">
            {PRESETS.map(p => <button key={p} type="button" style={{ background: p }} title={p} onClick={() => { setHsv(rgbToHsv(...hexToRgb(p))); setHex(p); onChange(p) }} />)}
          </div>
        </div>
      </>, document.body)}
  </>
}

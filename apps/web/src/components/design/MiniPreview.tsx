import React from 'react'
import type { Artboard, FillStyle, ImageLayer, Layer, LayerEffect, TextLayer } from './scene'

export const cssTextShadow = (t: TextLayer, scale = 1): string => {
  const parts: string[] = []
  if (t.glow) parts.push(`0 0 ${(t.glowBlur ?? 18) * scale}px ${t.glowColor || t.fill}`)
  if (t.shadow) parts.push(`${(t.shadowOffsetX ?? 0) * scale}px ${(t.shadowOffsetY ?? 8) * scale}px ${(t.shadowBlur ?? 12) * scale}px ${t.shadowColor || '#000000'}`)
  return parts.join(', ')
}
const cssFill = (l: FillStyle) => {
  const stops = l.gradientStops?.length ? [...l.gradientStops].sort((a, b) => a.offset - b.offset).map(s => `${s.color} ${Math.round(s.offset * 100)}%`).join(', ') : `${l.fill}, ${l.fill2 || l.fill}`
  if (l.fillType === 'linear-gradient') return `linear-gradient(${l.gradientAngle ?? 90}deg, ${stops})`
  if (l.fillType === 'radial-gradient') return `radial-gradient(circle, ${stops})`
  return l.fill
}
const cssRadius = (l: { cornerRadius?: number; cornerRadiusTL?: number; cornerRadiusTR?: number; cornerRadiusBR?: number; cornerRadiusBL?: number }, W: number) => {
  const unit = (v: number) => `${v / W * 100}cqw`
  if ([l.cornerRadiusTL, l.cornerRadiusTR, l.cornerRadiusBR, l.cornerRadiusBL].some(v => v != null)) return `${unit(l.cornerRadiusTL ?? l.cornerRadius ?? 0)} ${unit(l.cornerRadiusTR ?? l.cornerRadius ?? 0)} ${unit(l.cornerRadiusBR ?? l.cornerRadius ?? 0)} ${unit(l.cornerRadiusBL ?? l.cornerRadius ?? 0)}`
  return unit(l.cornerRadius || 0)
}
const cssEffects = (effects?: LayerEffect[]) => {
  const shadows = (effects || []).filter(f => f.type === 'drop-shadow' || f.type === 'glow').map(f => `${f.offsetX ?? 0}px ${f.offsetY ?? 0}px ${f.blur ?? 16}px ${f.spread ?? 0}px ${f.color || '#000000'}`)
  const blur = (effects || []).find(f => f.type === 'layer-blur')
  return { boxShadow: shadows.join(', ') || undefined, filter: blur ? `blur(${blur.blur ?? 8}px)` : undefined }
}
const textValue = (t: TextLayer) => t.textTransform === 'uppercase' ? t.text.toUpperCase() : t.textTransform === 'lowercase' ? t.text.toLowerCase() : t.textTransform === 'capitalize' ? t.text.replace(/\b\w/g, c => c.toUpperCase()) : t.text
const textDisplayValue = (t: TextLayer) => {
  const base = textValue(t)
  if (!t.listStyle || t.listStyle === 'none') return base
  return base.split('\n').map((line, i) => line.trim() ? `${t.listStyle === 'number' ? `${i + 1}.` : '•'} ${line}` : line).join('\n')
}

// Real scaled thumbnail of a design's first artboard (layers rendered as DOM,
// sized in container-query units so it scales to whatever the card width is).
export function MiniPreview({ art, resolveSrc }: { art?: Artboard; resolveSrc: (s: string) => string }) {
  if (!art) return <span className="ds-frame" />
  const W = art.width, H = art.height
  const pos = (l: Layer) => ({ position: 'absolute' as const, left: `${l.x / W * 100}cqw`, top: `${l.y / H * 100}cqh`, width: `${('width' in l ? l.width : 0) / W * 100}cqw` })
  return <div className="ds-mini" style={{ aspectRatio: `${W} / ${H}`, background: art.background }}>
    {art.layers.map(l => {
      const rot = l.rotation ? { transform: `rotate(${l.rotation}deg)`, transformOrigin: 'top left' } : {}
      if (l.type === 'image') {
        const im = l as ImageLayer
        const cropTransform = [rot.transform, im.cropZoom && im.cropZoom !== 1 ? `scale(${im.cropZoom})` : ''].filter(Boolean).join(' ')
        return <img key={l.id} src={resolveSrc(l.src)} alt="" style={{ ...pos(l), ...rot, ...cssEffects(im.effects), transform: cropTransform || undefined, transformOrigin: `${im.cropX ?? 50}% ${im.cropY ?? 50}%`, height: `${l.height / H * 100}cqh`, objectFit: 'cover', objectPosition: `${im.cropX ?? 50}% ${im.cropY ?? 50}%`, borderRadius: `${(l.cornerRadius || 0) / W * 100}cqw`, opacity: l.opacity ?? 1 }} />
      }
      if (l.type === 'text') {
        const t = l as TextLayer
        return <div key={l.id} style={{ ...pos(l), ...rot, ...cssEffects(t.effects), height: t.height ? `${t.height / H * 100}cqh` : undefined, color: t.fillType && t.fillType !== 'solid' ? 'transparent' : t.fill, background: t.fillType && t.fillType !== 'solid' ? cssFill(t) : undefined, WebkitBackgroundClip: t.fillType && t.fillType !== 'solid' ? 'text' : undefined, WebkitTextStroke: t.textStroke ? `${(t.textStrokeWidth ?? 1) / W * 100}cqw ${t.textStroke}` : undefined, fontSize: `${t.fontSize / W * 100}cqw`, fontWeight: t.fontStyle?.includes('bold') ? 700 : 400, fontStyle: t.fontStyle?.includes('italic') ? 'italic' : 'normal', textDecoration: t.textDecoration || 'none', textTransform: t.textTransform || 'none', textAlign: t.align || 'left', alignContent: t.verticalAlign === 'middle' ? 'center' : t.verticalAlign === 'bottom' ? 'end' : 'start', lineHeight: t.lineHeight || 1.2, letterSpacing: `${(t.letterSpacing || 0) / W * 100}cqw`, textShadow: cssTextShadow(t, 0.08), overflow: 'hidden' }}>{textDisplayValue(t)}</div>
      }
      if (l.type === 'line') return null
      const s = l as { width: number; height: number; fill: string; cornerRadius?: number }
      const radius = l.type === 'ellipse' ? '50%' : l.type === 'path' || l.type === 'star' ? '30%' : cssRadius(s, W)
      return <div key={l.id} style={{ ...pos(l), ...rot, ...cssEffects((s as { effects?: LayerEffect[] }).effects), height: `${s.height / H * 100}cqh`, background: cssFill(s), borderRadius: radius, opacity: (l.opacity ?? 1) * ((s as FillStyle).fillOpacity ?? 1), border: (s as { stroke?: string; strokeWidth?: number }).stroke && (s as { strokeWidth?: number }).strokeWidth !== 0 ? `${((s as { strokeWidth?: number }).strokeWidth ?? 2) / W * 100}cqw solid ${(s as { stroke?: string }).stroke}` : undefined }} />
    })}
  </div>
}

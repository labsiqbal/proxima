import type { Scene, Artboard, Layer, TextLayer, RectLayer, EllipseLayer, PathLayer, LineLayer } from './scene'
import { uid, blobPath } from './scene'

// Templates are SKELETONS — they set the surface, canvas size, and artboard count
// as a starting point. Nothing is locked: size/aspect/everything stays editable on
// the canvas, and the AI fills content into the skeleton from the user's brief.
export type Surface = 'graphic' | 'deck' | 'mobile' | 'video' | 'web'

export type Template = {
  id: string; surface: Surface; name: string; hint: string
  width: number; height: number; artboards: number; background: string
}

export const SURFACES: { key: Surface; label: string }[] = [
  { key: 'graphic', label: 'Graphic' },
  { key: 'deck', label: 'Slide deck' },
  { key: 'mobile', label: 'Mobile app' },
  { key: 'video', label: 'Video' },
  { key: 'web', label: 'Website' },
]

export const TEMPLATES: Template[] = [
  { id: 'ig-post', surface: 'graphic', name: 'Instagram post', hint: 'Square 1:1', width: 1080, height: 1080, artboards: 1, background: '#ffffff' },
  { id: 'ig-story', surface: 'graphic', name: 'Instagram story', hint: 'Vertical 9:16', width: 1080, height: 1920, artboards: 1, background: '#ffffff' },
  { id: 'ig-carousel', surface: 'graphic', name: 'Instagram carousel', hint: '1:1 · 3 slides', width: 1080, height: 1080, artboards: 3, background: '#ffffff' },
  { id: 'x-post', surface: 'graphic', name: 'X / Twitter post', hint: 'Landscape 16:9', width: 1600, height: 900, artboards: 1, background: '#ffffff' },
  { id: 'poster', surface: 'graphic', name: 'Poster', hint: 'Portrait 4:5', width: 1080, height: 1350, artboards: 1, background: '#ffffff' },
  { id: 'deck-169', surface: 'deck', name: 'Presentation', hint: '16:9 slides', width: 1920, height: 1080, artboards: 3, background: '#ffffff' },
  { id: 'pdf-a4-portrait', surface: 'deck', name: 'PDF A4 portrait', hint: 'A4 · 3 pages', width: 794, height: 1123, artboards: 3, background: '#ffffff' },
  { id: 'pdf-a4-landscape', surface: 'deck', name: 'PDF A4 landscape', hint: 'A4 wide · 3 pages', width: 1123, height: 794, artboards: 3, background: '#ffffff' },
  { id: 'pdf-letter-portrait', surface: 'deck', name: 'PDF Letter portrait', hint: 'Letter · 3 pages', width: 816, height: 1056, artboards: 3, background: '#ffffff' },
  { id: 'pdf-letter-landscape', surface: 'deck', name: 'PDF Letter landscape', hint: 'Letter wide · 3 pages', width: 1056, height: 816, artboards: 3, background: '#ffffff' },
  { id: 'pdf-square', surface: 'deck', name: 'PDF square', hint: '1:1 · 3 pages', width: 1080, height: 1080, artboards: 3, background: '#ffffff' },
  { id: 'pdf-tall-report', surface: 'deck', name: 'PDF tall report', hint: 'Tall report · 3 pages', width: 1080, height: 1440, artboards: 3, background: '#ffffff' },
  { id: 'mobile-ios', surface: 'mobile', name: 'iPhone screen', hint: '390×844', width: 390, height: 844, artboards: 1, background: '#ffffff' },
  { id: 'reel', surface: 'video', name: 'Reel / Short', hint: '9:16 video', width: 1080, height: 1920, artboards: 1, background: '#ffffff' },
  { id: 'web-landing', surface: 'web', name: 'Landing page', hint: 'Responsive (HTML)', width: 1440, height: 1024, artboards: 1, background: '#ffffff' },
]

export const surfaceTemplates = (s: Surface) => TEMPLATES.filter(t => t.surface === s)

// ── Starter-layout builders ───────────────────────────────────────────────
// Layer helpers (keep the per-template layouts readable). Backgrounds come from
// the artboard's `background`, so there is NO full-size rectangle layer.
const T = (x: number, y: number, width: number, text: string, fontSize: number, o: Partial<TextLayer> = {}): TextLayer =>
  ({ id: uid('t'), type: 'text', x, y, width, text, fontSize, fontFamily: 'Inter', fill: '#0F172A', lineHeight: 1.1, ...o })
const R = (x: number, y: number, width: number, height: number, fill: string, o: Partial<RectLayer> = {}): RectLayer =>
  ({ id: uid('r'), type: 'rect', x, y, width, height, fill, ...o })
const E = (x: number, y: number, width: number, height: number, fill: string, o: Partial<EllipseLayer> = {}): EllipseLayer =>
  ({ id: uid('e'), type: 'ellipse', x, y, width, height, fill, ...o })
const B = (x: number, y: number, size: number, fill: string, seed: number, o: Partial<PathLayer> = {}): PathLayer =>
  ({ id: uid('b'), type: 'path', x, y, width: size, height: size, d: blobPath(320, 8, seed), fill, ...o })
const LN = (x: number, y: number, x2: number, y2: number, stroke: string, strokeWidth = 4): LineLayer =>
  ({ id: uid('l'), type: 'line', x, y, x2, y2, stroke, strokeWidth })

const EB = { fontFamily: 'Inter', fontStyle: 'bold', letterSpacing: 4 } // eyebrow
const HEAD = { fontFamily: 'Poppins', fontStyle: 'bold', lineHeight: 1.04 }
const pill = (x: number, y: number, w: number, h: number, fill: string, label: string, fg: string): Layer[] =>
  [R(x, y, w, h, fill, { cornerRadius: h / 2 }), T(x, Math.round(y + h / 2 - h * 0.22), w, label, Math.round(h * 0.42), { align: 'center', fill: fg, fontFamily: 'Poppins', fontStyle: 'bold' })]

function pdfStarter(t: Template, i: number): { bg: string; layers: Layer[] } {
  const W = t.width, H = t.height
  const m = Math.round(Math.min(W, H) * 0.09)
  const wide = W > H
  const title = Math.round(Math.min(W, H) * (wide ? 0.082 : 0.092))
  const h2 = Math.round(Math.min(W, H) * 0.052)
  const body = Math.round(Math.min(W, H) * 0.03)
  const small = Math.round(Math.min(W, H) * 0.022)
  const lineW = Math.round(W * 0.26)
  const cardW = wide ? Math.round((W - m * 2 - 32) / 3) : W - m * 2
  const cardH = wide ? Math.round(H * 0.26) : Math.round(H * 0.15)
  const cardGap = wide ? 16 : Math.round(H * 0.025)
  if (i === 0) return { bg: '#F8FAFC', layers: [
    R(0, 0, W, Math.round(H * 0.22), '#0F172A'),
    B(Math.round(W * 0.62), Math.round(-H * 0.08), Math.round(Math.min(W, H) * 0.48), '#DBEAFE', 11, { opacity: 0.8 }),
    T(m, Math.round(H * 0.08), Math.round(W * 0.7), 'PDF DOCUMENT', small, { ...EB, fill: '#93C5FD' }),
    T(m, Math.round(H * 0.26), Math.round(W * 0.78), 'Report title goes here', title, { ...HEAD, fill: '#0F172A' }),
    T(m, Math.round(H * 0.43), Math.round(W * 0.68), 'A concise subtitle that explains what this exported PDF covers.', body, { fill: '#475569', lineHeight: 1.35 }),
    LN(m, Math.round(H * 0.58), m + lineW, Math.round(H * 0.58), '#2563EB', Math.max(3, Math.round(W * 0.006))),
    T(m, Math.round(H * 0.83), Math.round(W * 0.48), 'Prepared for review', small, { fill: '#64748B' }),
    T(Math.round(W * 0.54), Math.round(H * 0.83), Math.round(W * 0.36), '2026', small, { ...EB, fill: '#94A3B8', align: 'right' }),
  ] }
  if (i === 1) {
    const y0 = Math.round(H * 0.22)
    const cards = [0, 1, 2].flatMap(n => {
      const x = wide ? m + n * (cardW + cardGap) : m
      const y = wide ? y0 : y0 + n * (cardH + cardGap)
      return [
        R(x, y, cardW, cardH, '#FFFFFF', { cornerRadius: 18, stroke: '#E2E8F0', strokeWidth: 2, shadow: true }),
        T(x + 26, y + 26, cardW - 52, `0${n + 1}`, h2, { ...HEAD, fill: '#BFDBFE' }),
        T(x + 26, y + Math.round(cardH * 0.47), cardW - 52, ['Key point', 'Supporting detail', 'Next action'][n], Math.round(body * 1.15), { fontFamily: 'Poppins', fontStyle: 'bold', fill: '#0F172A' }),
        T(x + 26, y + Math.round(cardH * 0.68), cardW - 52, 'Short body copy for this section goes here.', small, { fill: '#64748B', lineHeight: 1.35 }),
      ] as Layer[]
    })
    return { bg: '#FFFFFF', layers: [
      T(m, Math.round(H * 0.08), Math.round(W * 0.76), 'Main section heading', Math.round(title * 0.72), { ...HEAD, fill: '#0F172A' }),
      T(m, Math.round(H * 0.16), Math.round(W * 0.72), 'Use this page for grouped findings, notes, or extracted PDF/image content.', body, { fill: '#475569', lineHeight: 1.35 }),
      ...cards,
    ] }
  }
  return { bg: '#0F172A', layers: [
    E(Math.round(W * 0.62), Math.round(H * 0.12), Math.round(Math.min(W, H) * 0.34), Math.round(Math.min(W, H) * 0.34), '#1D4ED8', { opacity: 0.35 }),
    T(m, Math.round(H * 0.16), Math.round(W * 0.76), 'Summary', title, { ...HEAD, fill: '#FFFFFF' }),
    T(m, Math.round(H * 0.34), Math.round(W * 0.72), 'Close with the takeaway, decision, or next step.', Math.round(body * 1.25), { fill: '#CBD5E1', lineHeight: 1.4 }),
    ...pill(m, Math.round(H * 0.58), Math.round(Math.min(W * 0.44, 420)), Math.round(Math.min(H * 0.08, 92)), '#FFFFFF', 'Next steps', '#0F172A'),
    T(m, Math.round(H * 0.84), Math.round(W * 0.64), 'Export as PDF from the toolbar', small, { fill: '#64748B' }),
  ] }
}

// Returns the artboard background + a designed starter layout per template (and
// per slide for carousels/decks), so every template opens as a real composition.
function starter(t: Template, i: number): { bg: string; layers: Layer[] } {
  switch (t.id) {
    case 'ig-post': return { bg: '#FFFFFF', layers: [
      B(640, -120, 620, '#DBEAFE', 3),
      E(880, 760, 240, 240, '#FDE68A'),
      T(80, 150, 900, 'NEW DROP', 30, { ...EB, fill: '#2563EB' }),
      T(80, 210, 880, 'Your bold headline goes right here', 94, { ...HEAD, fill: '#0F172A' }),
      T(80, 560, 760, 'A short supporting line that sells the idea in one calm breath.', 36, { fill: '#475569', lineHeight: 1.35 }),
      ...pill(80, 870, 360, 96, '#2563EB', 'Shop now', '#FFFFFF'),
    ] }
    case 'ig-story': return { bg: '#0F172A', layers: [
      B(-140, -120, 720, '#1E293B', 5),
      E(740, 1480, 520, 520, '#F59E0B', { opacity: 0.18 }),
      T(90, 240, 400, 'STORY', 30, { ...EB, fill: '#F59E0B' }),
      T(90, 310, 900, 'Drop a hook they can’t scroll past', 104, { ...HEAD, fill: '#FFFFFF' }),
      T(90, 860, 820, 'One line of context, then point them somewhere.', 42, { fill: '#CBD5E1', lineHeight: 1.3 }),
      ...pill(90, 1680, 900, 120, '#F59E0B', 'Swipe up', '#0F172A'),
    ] }
    case 'ig-carousel': {
      if (i === 0) return { bg: '#111827', layers: [
        B(680, 620, 620, '#2563EB', 2, { opacity: 0.5 }),
        T(80, 150, 400, '1 / 3', 30, { ...EB, fill: '#60A5FA' }),
        T(80, 360, 920, 'The headline that earns the next swipe', 96, { ...HEAD, fill: '#FFFFFF' }),
        T(80, 820, 760, 'Set up the promise of the carousel in one sentence.', 38, { fill: '#94A3B8', lineHeight: 1.35 }),
        T(80, 980, 400, 'Swipe →', 34, { fontFamily: 'Poppins', fontStyle: 'bold', fill: '#60A5FA' }),
      ] }
      if (i === 1) return { bg: '#FFFFFF', layers: [
        T(80, 150, 400, '01', 160, { ...HEAD, fill: '#DBEAFE' }),
        T(80, 380, 900, 'Make one strong point', 78, { ...HEAD, fill: '#0F172A' }),
        T(80, 560, 840, 'Back it with a concrete detail, number, or example so it sticks.', 40, { fill: '#475569', lineHeight: 1.4 }),
        LN(80, 980, 1000, 980, '#E2E8F0', 3),
      ] }
      return { bg: '#2563EB', layers: [
        E(760, -160, 520, 520, '#3B82F6'),
        T(80, 360, 900, 'Save this for later', 88, { ...HEAD, fill: '#FFFFFF' }),
        T(80, 600, 820, 'End with the action you want — follow, save, or shop.', 40, { fill: '#DBEAFE', lineHeight: 1.35 }),
        ...pill(80, 820, 420, 104, '#FFFFFF', 'Follow @acme', '#2563EB'),
      ] }
    }
    case 'x-post': return { bg: '#FFFFFF', layers: [
      R(1080, 0, 520, 900, '#0F172A'),
      B(1140, 520, 520, '#2563EB', 7, { opacity: 0.7 }),
      E(1240, 120, 200, 200, '#F59E0B'),
      T(90, 200, 880, 'ANNOUNCEMENT', 28, { ...EB, fill: '#2563EB' }),
      T(90, 270, 900, 'Say the big thing in one strong line', 84, { ...HEAD, fill: '#0F172A' }),
      T(90, 560, 820, 'A supporting sentence with the detail that matters.', 36, { fill: '#475569', lineHeight: 1.35 }),
      ...pill(90, 720, 340, 84, '#0F172A', 'Learn more', '#FFFFFF'),
    ] }
    case 'poster': return { bg: '#FBBF24', layers: [
      E(720, 980, 460, 460, '#F59E0B'),
      T(80, 150, 920, 'PRESENTING', 34, { ...EB, fill: '#7C2D12' }),
      T(72, 250, 980, 'EVENT 2026', 170, { fontFamily: 'Oswald', fontStyle: 'bold', fill: '#1C1917', lineHeight: 0.95 }),
      LN(80, 720, 1000, 720, '#1C1917', 6),
      T(80, 770, 600, 'Sat · 24 May · 7PM', 46, { fontFamily: 'Poppins', fontStyle: 'bold', fill: '#1C1917' }),
      T(80, 850, 820, 'Venue name, city. Add the one detail people need to show up.', 36, { fill: '#44403C', lineHeight: 1.35 }),
    ] }
    case 'deck-169': {
      if (i === 0) return { bg: '#0F172A', layers: [
        LN(120, 470, 320, 470, '#F59E0B', 8),
        T(120, 250, 1400, 'Presentation title goes here', 120, { ...HEAD, fill: '#FFFFFF' }),
        T(120, 520, 1200, 'A clear subtitle that frames the talk', 48, { fill: '#94A3B8' }),
        T(120, 940, 800, 'Presenter · Company · 2026', 32, { ...EB, fill: '#64748B' }),
      ] }
      if (i === 1) return { bg: '#FFFFFF', layers: [
        T(120, 130, 1200, 'Section heading', 84, { ...HEAD, fill: '#0F172A' }),
        LN(120, 290, 480, 290, '#2563EB', 6),
        T(120, 360, 1000, 'First key point that supports the heading', 44, { fill: '#334155', lineHeight: 1.4 }),
        T(120, 470, 1000, 'Second point, kept short and scannable', 44, { fill: '#334155', lineHeight: 1.4 }),
        T(120, 580, 1000, 'Third point that lands the section', 44, { fill: '#334155', lineHeight: 1.4 }),
        E(1480, 240, 320, 320, '#DBEAFE'),
      ] }
      return { bg: '#2563EB', layers: [
        T(120, 380, 1400, 'Thank you', 150, { ...HEAD, fill: '#FFFFFF' }),
        T(120, 620, 1000, 'hello@company.com · @company', 44, { fill: '#DBEAFE' }),
      ] }
    }
    case 'pdf-a4-portrait':
    case 'pdf-a4-landscape':
    case 'pdf-letter-portrait':
    case 'pdf-letter-landscape':
    case 'pdf-square':
    case 'pdf-tall-report':
      return pdfStarter(t, i)
    case 'mobile-ios': return { bg: '#F8FAFC', layers: [
      R(0, 0, 390, 120, '#2563EB'),
      T(24, 64, 200, 'Good morning', 26, { fontFamily: 'Poppins', fontStyle: 'bold', fill: '#FFFFFF' }),
      R(24, 160, 342, 200, '#FFFFFF', { cornerRadius: 24, shadow: true }),
      T(48, 196, 280, 'Your card title', 28, { fontFamily: 'Poppins', fontStyle: 'bold', fill: '#0F172A' }),
      T(48, 244, 300, 'Supporting copy for this card goes on two short lines.', 18, { fill: '#64748B', lineHeight: 1.4 }),
      ...pill(24, 740, 342, 64, '#2563EB', 'Continue', '#FFFFFF'),
    ] }
    case 'reel': return { bg: '#18181B', layers: [
      E(390, 760, 300, 300, '#FFFFFF', { opacity: 0.06 }),
      T(64, 150, 950, 'WATCH TILL THE END', 30, { ...EB, fill: '#F59E0B' }),
      T(80, 700, 920, 'The hook that stops the scroll', 100, { ...HEAD, fill: '#FFFFFF', align: 'center' }),
      T(80, 1120, 920, 'Subtitle line for context', 44, { fill: '#A1A1AA', align: 'center' }),
      ...pill(290, 1700, 500, 110, '#F59E0B', 'Follow for more', '#18181B'),
    ] }
    case 'web-landing': return { bg: '#FFFFFF', layers: [
      T(80, 60, 200, 'Acme', 32, { fontFamily: 'Poppins', fontStyle: 'bold', fill: '#0F172A' }),
      T(980, 66, 380, 'Features   Pricing   About', 22, { fill: '#475569', align: 'right' }),
      B(980, 240, 560, '#DBEAFE', 4),
      E(1180, 200, 260, 260, '#FDE68A'),
      T(80, 280, 820, 'The headline that converts visitors', 88, { ...HEAD, fill: '#0F172A' }),
      T(80, 560, 660, 'One or two lines explaining the value, simply and confidently.', 32, { fill: '#475569', lineHeight: 1.45 }),
      ...pill(80, 700, 240, 76, '#2563EB', 'Get started', '#FFFFFF'),
      ...pill(344, 700, 220, 76, '#F1F5F9', 'Learn more', '#0F172A'),
    ] }
    default: return { bg: '#FFFFFF', layers: [
      T(Math.round(t.width * 0.08), Math.round(t.height * 0.12), Math.round(t.width * 0.84), t.artboards > 1 ? `Slide ${i + 1}` : 'Your headline here', Math.round(t.width * 0.08), { ...HEAD, fill: '#0F172A' }),
    ] }
  }
}

// Build a starter scene: each artboard opens as a designed, unique composition
// (no full-size background rectangle — the artboard's own background carries the colour).
export function sceneFromTemplate(t: Template, title: string): Scene {
  const artboards: Artboard[] = Array.from({ length: t.artboards }, (_, i) => {
    const { bg, layers } = starter(t, i)
    return { id: uid('a'), width: t.width, height: t.height, background: bg, layers }
  })
  return { id: uid('d'), type: (t.surface === 'web' ? 'graphic' : t.surface) as Scene['type'], title: title || t.name, artboards }
}

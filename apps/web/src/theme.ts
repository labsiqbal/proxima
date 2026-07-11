// Appearance: theme presets + font choices. Themes are applied as a
// `data-theme` attribute on <html> (CSS provides :root[data-theme="..."]
// overrides); the font is applied by overriding the --font-sans variable.
// Both are persisted to localStorage and re-applied at boot (see main.tsx).

export type ThemeKey = 'light' | 'dark' | 'ocean' | 'violet' | 'sunset' | 'forest'
export type FontKey = 'inter' | 'poppins' | 'nunito' | 'merriweather' | 'playfair' | 'robotoslab' | 'jetbrains' | 'oswald' | 'caveat' | 'lobster'

export const THEMES: { key: ThemeKey; label: string; accent: string; surface: string }[] = [
  { key: 'light', label: 'Light', accent: '#0053fd', surface: '#fbfcfe' },
  { key: 'dark', label: 'Dark', accent: '#4f8cff', surface: '#0d1117' },
  { key: 'ocean', label: 'Ocean', accent: '#0e7490', surface: '#f5fbfd' },
  { key: 'violet', label: 'Violet', accent: '#7c3aed', surface: '#fbf9ff' },
  { key: 'sunset', label: 'Sunset', accent: '#ea580c', surface: '#fffaf5' },
  { key: 'forest', label: 'Forest', accent: '#15803d', surface: '#f6fdf8' }
]

// 10 popular Google Fonts, each a distinctly different style. Loaded in index.html.
export const FONTS: { key: FontKey; label: string; stack: string }[] = [
  { key: 'inter', label: 'Inter', stack: 'Inter, ui-sans-serif, system-ui, sans-serif' },
  { key: 'poppins', label: 'Poppins', stack: 'Poppins, ui-sans-serif, system-ui, sans-serif' },
  { key: 'nunito', label: 'Nunito', stack: 'Nunito, ui-sans-serif, system-ui, sans-serif' },
  { key: 'merriweather', label: 'Merriweather', stack: 'Merriweather, Georgia, serif' },
  { key: 'playfair', label: 'Playfair Display', stack: '"Playfair Display", Georgia, serif' },
  { key: 'robotoslab', label: 'Roboto Slab', stack: '"Roboto Slab", Georgia, serif' },
  { key: 'jetbrains', label: 'JetBrains Mono', stack: '"JetBrains Mono", ui-monospace, monospace' },
  { key: 'oswald', label: 'Oswald', stack: 'Oswald, "Arial Narrow", sans-serif' },
  { key: 'caveat', label: 'Caveat', stack: 'Caveat, "Comic Sans MS", cursive' },
  { key: 'lobster', label: 'Lobster', stack: 'Lobster, "Brush Script MT", cursive' }
]

// Font size is a free slider (px on the root element; the whole UI is
// rem-based so everything scales). Legacy preset keys map to px so stored
// preferences from the old XS/Small/Default/Large buttons keep working.
export const FONT_SIZE_MIN = 12
export const FONT_SIZE_MAX = 20
const LEGACY_FONT_SIZES: Record<string, number> = { xs: 13, sm: 14, base: 16, lg: 18 }

const THEME_LS = 'proxima.theme'
const FONT_LS = 'proxima.font'
const SIZE_LS = 'proxima.fontSize'

// Defaults for a fresh install / new browser (no stored preference yet):
// Sunset theme + Inter font + 14px. Users who already picked keep theirs.
export const DEFAULT_THEME: ThemeKey = 'sunset'
export const DEFAULT_FONT: FontKey = 'inter'
export const DEFAULT_FONT_SIZE_PX = 14

export function getTheme(): ThemeKey {
  const v = localStorage.getItem(THEME_LS)
  return THEMES.some(t => t.key === v) ? (v as ThemeKey) : DEFAULT_THEME
}

export function getFont(): FontKey {
  const v = localStorage.getItem(FONT_LS)
  return FONTS.some(f => f.key === v) ? (v as FontKey) : DEFAULT_FONT
}

export function applyTheme(key: ThemeKey) {
  document.documentElement.setAttribute('data-theme', key)
  localStorage.setItem(THEME_LS, key)
}

export function applyFont(key: FontKey) {
  const font = FONTS.find(f => f.key === key) || FONTS[0]
  document.documentElement.style.setProperty('--font-sans', font.stack)
  localStorage.setItem(FONT_LS, key)
}

const clampFontSize = (px: number) =>
  Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, px))

export function getFontSize(): number {
  const v = localStorage.getItem(SIZE_LS) || ''
  const px = LEGACY_FONT_SIZES[v] ?? parseFloat(v)
  return Number.isFinite(px) ? clampFontSize(px) : DEFAULT_FONT_SIZE_PX
}

export function applyFontSize(px: number) {
  const clamped = clampFontSize(px)
  // The whole UI is rem-based, so scaling the root font-size scales everything.
  document.documentElement.style.fontSize = `${clamped}px`
  localStorage.setItem(SIZE_LS, String(clamped))
}

export function initAppearance() {
  applyTheme(getTheme())
  applyFont(getFont())
  applyFontSize(getFontSize())
}

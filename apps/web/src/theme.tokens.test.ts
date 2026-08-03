import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { THEMES } from './theme'

// #155. A theme preset is only a variable override, so a token the dark preset
// forgets keeps its LIGHT value - which is how every danger/warning/success
// panel came to render a near-white card on a dark app. The two blocks live
// ~11k lines apart in styles.css, so parity is asserted here instead of being
// eyeballed: any semantic token that :root pins to a literal must be re-pinned
// by the dark preset. Tokens whose light value is itself a var() reference
// (--ui-danger-fill, --ui-error-text) already derive from one that is covered,
// so they are exempt - the dark preset may still override them, and does.
const stylesSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'styles.css'),
  'utf8',
)
const withoutComments = stylesSource.replace(/\/\*[\s\S]*?\*\//g, '')

/** Every custom property declared by the rules whose selector is exactly `selector`. */
const declarationsOf = (selector: string): Map<string, string> => {
  const found = new Map<string, string>()
  for (const [, sel, body] of withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (sel.trim() !== selector) continue
    for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
      found.set(name, value.trim())
    }
  }
  return found
}

const isSemantic = (name: string) =>
  /^--ui-(danger|success|warning)/.test(name) || name === '--ui-error-text'

describe('semantic surface tokens', () => {
  const root = declarationsOf(':root')
  const dark = declarationsOf(':root[data-theme="dark"]')

  it('finds both token blocks', () => {
    expect(root.size).toBeGreaterThan(0)
    expect(dark.size).toBeGreaterThan(0)
  })

  it('has the dark preset override every semantic token :root pins to a literal', () => {
    const missing = [...root]
      .filter(([name, value]) => isSemantic(name) && !value.includes('var('))
      .map(([name]) => name)
      .filter(name => !dark.has(name))
    expect(missing).toEqual([])
  })

  it('leaves the light-surfaced presets inheriting the :root semantics', () => {
    // Ocean/Violet/Sunset/Forest are light themes: they re-tint the accent and
    // the app surfaces only, so overriding a danger/warning/success token there
    // would silently fork the palette instead of theming it.
    for (const { key } of THEMES.filter(t => t.key !== 'light' && t.key !== 'dark')) {
      const preset = declarationsOf(`:root[data-theme="${key}"]`)
      expect([...preset.keys()].filter(isSemantic)).toEqual([])
    }
  })
})

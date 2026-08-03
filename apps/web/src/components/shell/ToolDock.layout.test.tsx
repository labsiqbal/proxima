import '@testing-library/jest-dom/vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// jsdom applies no stylesheet, so the two facts this pair of tickets turns on
// are checked against the stylesheet itself, the way #153 checks the collapsed
// left rail: #160 - putting the dock away is one token going to zero, and
// #156 - at phone width there is no rail at all, so the surface behind keeps
// the whole screen.
const stylesSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../../styles.css'),
  'utf8',
)
const withoutComments = stylesSource.replace(/\/\*[\s\S]*?\*\//g, '')

/**
 * Body of the first rule whose selector list mentions the given selector -
 * narrowed by a declaration when a selector appears in several rules (the main
 * pane is placed in one and padded in another).
 */
const ruleFor = (source: string, selector: string, declares?: RegExp) => {
  for (const [, selectors, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!selectors.includes(selector)) continue
    if (declares && !declares.test(body)) continue
    return body
  }
  return ''
}

/** Everything inside every `@media (max-width: 767px)` block, concatenated. */
const phoneRules = (() => {
  const query = '@media (max-width: 767px)'
  let out = ''
  let from = 0
  for (;;) {
    const start = withoutComments.indexOf(query, from)
    if (start < 0) return out
    let depth = 0
    let index = withoutComments.indexOf('{', start)
    const body = index
    for (; index < withoutComments.length; index += 1) {
      if (withoutComments[index] === '{') depth += 1
      else if (withoutComments[index] === '}') {
        depth -= 1
        if (depth === 0) break
      }
    }
    out += withoutComments.slice(body + 1, index)
    from = index
  }
})()

describe('tool dock layout contract', () => {
  it('derives the dock lane from one token, so collapsing it is one rule', () => {
    expect(ruleFor(withoutComments, '.app-shell', /grid-template-columns/))
      .toMatch(/grid-template-columns:[^;]*var\(--toolrail-w\)/)
    expect(ruleFor(withoutComments, '.app-shell.dock-collapsed')).toMatch(/--toolrail-w:\s*0px/)
    // Every width that has to clear the rail reads the same token, which is why
    // zeroing it is the whole change: the panel's edge, the pane's reserved
    // padding, the Master popup's clearance, the toast column.
    expect(ruleFor(withoutComments, '.tool-panel', /position:\s*absolute/)).toMatch(/right:\s*var\(--toolrail-w\)/)
    expect(ruleFor(withoutComments, ':root', /--master-popup-tool-clearance/))
      .toMatch(/--master-popup-tool-clearance:\s*calc\(var\(--toolrail-w\)/)
  })

  it('renders no rail at phone width and reserves nothing for it', () => {
    expect(ruleFor(phoneRules, ':root', /--toolrail-w/)).toMatch(/--toolrail-w:\s*0px/)
    expect(ruleFor(phoneRules, '.tool-rail')).toMatch(/display:\s*none/)
    // The pane and the open-tool pane both clear the token, never a literal.
    expect(ruleFor(phoneRules, '.main-pane', /padding-right/))
      .toMatch(/padding-right:\s*var\(--toolrail-w\)/)
    expect(ruleFor(phoneRules, '.app-shell.tool-open > .main-pane'))
      .toMatch(/padding-right:\s*var\(--toolrail-w\)/)
    // The sheet is the screen.
    expect(ruleFor(phoneRules, '.tool-panel', /width/)).toMatch(/width:\s*calc\(100vw - var\(--toolrail-w\)\)/)
  })
})

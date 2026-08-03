import { describe, expect, it } from 'vitest'
import { parseRevealTarget, REVEAL_FILE_EVENT, revealFile } from './revealFile'

const event = (detail: unknown) => new CustomEvent(REVEAL_FILE_EVENT, { detail })

describe('reveal-file contract', () => {
  it('carries the request the raiser wrote', () => {
    const seen: unknown[] = []
    const listener = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(REVEAL_FILE_EVENT, listener)
    revealFile({ path: 'reports/plan.md', projectSlug: 'alpha', pathKind: 'file' })
    window.removeEventListener(REVEAL_FILE_EVENT, listener)
    expect(seen).toEqual([{ path: 'reports/plan.md', projectSlug: 'alpha', pathKind: 'file' }])
  })

  it('defaults an unnamed Container to the active one, and the root side to virtual', () => {
    expect(parseRevealTarget(event({ path: 'notes.md' }), 'active')).toEqual({
      projectSlug: 'active', path: 'notes.md', pathKind: 'file', rootSide: 'virtual',
    })
  })

  it('keeps the Container root side and directory kind a recovery reveal names', () => {
    expect(parseRevealTarget(event({ path: 'ops', pathKind: 'directory', projectSlug: 'legacy', rootSide: 'container' }), 'active')).toEqual({
      projectSlug: 'legacy', path: 'ops', pathKind: 'directory', rootSide: 'container',
    })
  })

  it('refuses a reveal that names no path or no Container', () => {
    expect(parseRevealTarget(event({ projectSlug: 'alpha' }), 'active')).toBeNull()
    expect(parseRevealTarget(event({ path: 42 }), 'active')).toBeNull()
    expect(parseRevealTarget(event({ path: 'notes.md' }), undefined)).toBeNull()
  })

  it('treats an unknown path kind or root side as the safe default', () => {
    expect(parseRevealTarget(event({ path: 'x', pathKind: 'symlink', rootSide: 'anything' }), 'active')).toMatchObject({
      pathKind: 'file', rootSide: 'virtual',
    })
  })
})

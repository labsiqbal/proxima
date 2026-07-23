import { describe, expect, it } from 'vitest'
import { baseName, buildWikiModel, linkifyWiki, notePathForTarget } from './wikiGraph'

describe('notePathForTarget', () => {
  it('adds .md and keeps simple titles', () => {
    expect(notePathForTarget('Another Note')).toBe('Another Note.md')
    expect(notePathForTarget('Already.md')).toBe('Already.md')
  })

  it('strips alias/heading and wiki/ prefixes', () => {
    expect(notePathForTarget('Target|alias')).toBe('Target.md')
    expect(notePathForTarget('Target#section')).toBe('Target.md')
    expect(notePathForTarget('wiki/Deep/Note')).toBe('Deep/Note.md')
  })

  it('places new notes beside the open note when nested', () => {
    expect(notePathForTarget('Sibling', 'folder/Current.md')).toBe('folder/Sibling.md')
    expect(notePathForTarget('Deep/Other', 'folder/Current.md')).toBe('Deep/Other.md')
  })

  it('rejects path traversal segments', () => {
    expect(notePathForTarget('../Escape')).toBe('Escape.md')
    expect(notePathForTarget('a/../b')).toBe('b.md')
  })
})

describe('buildWikiModel resolve + linkify', () => {
  it('resolves by base name and linkifies wikilinks', () => {
    const model = buildWikiModel([
      { path: 'gnhf-e2e-note.md', content: 'See [[Another Note]]' },
      { path: 'Another Note.md', content: 'Back to [[gnhf-e2e-note]]' },
    ])
    expect(model.resolve('Another Note')).toBe('Another Note.md')
    expect(model.resolve('missing')).toBeNull()
    expect(baseName('folder/x.md')).toBe('x')
    expect(linkifyWiki('See [[Another Note|alias]]')).toContain('#wiki:Another%20Note')
  })
})

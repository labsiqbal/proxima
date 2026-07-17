import { describe, expect, it } from 'vitest'
import { applyMention, filterMentions, matchMention } from './MentionTextarea'

const items = [
  { path: 'artifacts/design/scene.json', title: 'Homepage design', type: 'design' },
  { path: 'notes/brief.md', title: 'Brief', type: 'doc' },
  { path: 'posts/post-x-test.md', title: 'post-x-test', type: 'doc' },
]

describe('matchMention', () => {
  it('finds the @token being typed at the caret', () => {
    expect(matchMention('Summarize @bri')).toEqual({ query: 'bri', at: 10 })
    expect(matchMention('@')).toEqual({ query: '', at: 0 })
  })

  it('ignores emails and text with no active token', () => {
    expect(matchMention('mail me at a@b.com')).toBeNull()
    expect(matchMention('no mention here')).toBeNull()
    // A completed mention followed by a space is no longer being typed.
    expect(matchMention('see notes/brief.md ')).toBeNull()
  })
})

describe('filterMentions', () => {
  it('matches on path and title, case-insensitively', () => {
    expect(filterMentions(items, 'homepage').map(i => i.path)).toEqual(['artifacts/design/scene.json'])
    expect(filterMentions(items, 'BRIEF').map(i => i.path)).toEqual(['notes/brief.md'])
    expect(filterMentions(items, '')).toHaveLength(3)
  })
})

describe('applyMention', () => {
  it('replaces the @token with the path and moves the caret past it', () => {
    const text = 'Summarize @bri please'
    const caret = 'Summarize @bri'.length
    const applied = applyMention(text, caret, 10, 'notes/brief.md')

    expect(applied.text).toBe('Summarize notes/brief.md  please')
    expect(applied.caret).toBe('Summarize notes/brief.md '.length)
  })
})

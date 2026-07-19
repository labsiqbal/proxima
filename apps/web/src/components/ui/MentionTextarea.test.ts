import React from 'react'
import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyMention, filterMentions, matchMention, MentionTextarea } from './MentionTextarea'

const items = [
  { path: 'artifacts/design/scene.json', title: 'Homepage design', type: 'design' },
  { path: 'notes/brief.md', title: 'Brief', type: 'doc' },
  { path: 'posts/post-x-test.md', title: 'post-x-test', type: 'doc' },
]

const scrollIntoView = vi.fn()
let originalScrollIntoView: typeof HTMLElement.prototype.scrollIntoView | undefined

beforeEach(() => {
  originalScrollIntoView = HTMLElement.prototype.scrollIntoView
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  })
  scrollIntoView.mockClear()
})

afterEach(() => {
  if (originalScrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: originalScrollIntoView,
    })
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
  }
})

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

  it('ranks a matching filename ahead of a generic parent-directory match', () => {
    const candidates = [
      { path: 'app/archive/readme.md' },
      { path: 'src/app.tsx' },
      { path: 'apps/web/src/AppShell.tsx' },
    ]
    expect(filterMentions(candidates, 'app').map(item => item.path)).toEqual([
      'src/app.tsx',
      'apps/web/src/AppShell.tsx',
      'app/archive/readme.md',
    ])
  })

  it('keeps matches beyond the four-row viewport available to scroll or select', () => {
    const candidates = Array.from({ length: 6 }, (_, index) => ({ path: `docs/file-${index}.md` }))
    expect(filterMentions(candidates, '')).toEqual(candidates)
  })
})

describe('applyMention', () => {
  it('replaces the @token with the path and moves the caret past it', () => {
    const text = 'Summarize @bri please'
    const caret = 'Summarize @bri'.length
    const applied = applyMention(text, caret, 10, 'notes/brief.md')

    expect(applied.text).toBe('Summarize notes/brief.md please')
    expect(applied.caret).toBe('Summarize notes/brief.md '.length)
  })
})

describe('MentionTextarea', () => {
  it('announces and scrolls the fifth keyboard option into view', async () => {
    const candidates = Array.from({ length: 6 }, (_, index) => ({ path: `docs/file-${index}.md` }))
    const Harness = () => {
      const [value, setValue] = React.useState('')
      return React.createElement(MentionTextarea, {
        value,
        onChange: setValue,
        items: candidates,
        ariaLabel: 'Prompt',
      })
    }
    const user = userEvent.setup()
    render(React.createElement(Harness))
    const textarea = screen.getByRole('textbox', { name: 'Prompt' })

    await user.type(textarea, '@')
    const list = await screen.findByRole('listbox', { name: 'Project files' })
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(6)
    expect(textarea).toHaveAttribute('aria-controls', list.id)

    scrollIntoView.mockClear()
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}')
    await waitFor(() => {
      expect(textarea).toHaveAttribute('aria-activedescendant', options[4].id)
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
      expect(scrollIntoView.mock.instances[scrollIntoView.mock.instances.length - 1]).toBe(options[4])
    })
  })
})

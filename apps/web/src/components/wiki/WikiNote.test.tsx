import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FsAdapter } from '../../api/fsAdapter'
import { WikiNote } from './WikiNote'

function mockFs(content: string): FsAdapter {
  return {
    list: vi.fn(async () => ({ entries: [] })),
    read: vi.fn(async () => ({ content })),
    write: vi.fn(async () => ({})),
    mkdir: vi.fn(async () => ({})),
    rename: vi.fn(async () => ({})),
    remove: vi.fn(async () => ({})),
  }
}

describe('WikiNote missing wikilinks', () => {
  it('opens existing wikilinks and creates missing ones', async () => {
    const user = userEvent.setup()
    const onOpenNote = vi.fn()
    const onCreateNote = vi.fn()
    const fs = mockFs('See [[Known]] and [[Missing Note]].')
    const resolve = (name: string) => (name === 'Known' ? 'Known.md' : null)

    render(
      <WikiNote
        fs={fs}
        path="seed.md"
        backlinks={[]}
        resolve={resolve}
        onOpenNote={onOpenNote}
        onCreateNote={onCreateNote}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )

    await waitFor(() => expect(screen.getByText('Known')).toBeInTheDocument())
    const known = screen.getByRole('link', { name: 'Known' })
    const missing = screen.getByRole('link', { name: 'Missing Note' })
    expect(known).toHaveClass('wikilink')
    expect(known).not.toHaveClass('missing')
    expect(missing).toHaveClass('missing')
    expect(missing).toHaveAttribute('title', 'Create note “Missing Note”')

    await user.click(known)
    expect(onOpenNote).toHaveBeenCalledWith('Known.md')
    expect(onCreateNote).not.toHaveBeenCalled()

    await user.click(missing)
    expect(onCreateNote).toHaveBeenCalledWith('Missing Note')
  })

  it('starts in edit mode when defaultMode is edit', async () => {
    const fs = mockFs('# Title\n')
    render(
      <WikiNote
        fs={fs}
        path="new.md"
        backlinks={[]}
        resolve={() => null}
        onOpenNote={() => {}}
        onClose={() => {}}
        onSaved={() => {}}
        defaultMode="edit"
      />,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toHaveClass('active'))
  })
})

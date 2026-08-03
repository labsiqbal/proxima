import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
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
        wiki={{ backlinks: [], resolve, onOpenNote, onCreateNote }}
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
        wiki={{ backlinks: [], resolve: () => null, onOpenNote: () => {} }}
        onClose={() => {}}
        onSaved={() => {}}
        defaultMode="edit"
      />,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toHaveClass('active'))
  })
})

// Artifacts opens ordinary project documents in this editor (#146). Without a
// wiki graph there is nothing to resolve a [[wikilink]] against and no backlink
// rail to draw - it is a plain markdown editor.
describe('WikiNote as a plain document editor', () => {
  it('leaves wikilinks alone and shows no linked-mentions rail', async () => {
    const fs = mockFs('See [[Known]].')
    render(<WikiNote fs={fs} path="reports/plan.md" onClose={() => {}} />)

    await waitFor(() => expect(screen.getByText(/See \[\[Known\]\]\./)).toBeInTheDocument())
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByText(/Linked mentions/)).not.toBeInTheDocument()
  })

  it('reads and writes through the Area-mapped target', async () => {
    const user = userEvent.setup()
    const fs = mockFs('body')
    const target = { project: 'alpha', area: { kind: 'ops' as const }, path: 'wiki/index.md' }
    render(<WikiNote fs={fs} path="ops/wiki/index.md" target={target} onClose={() => {}} />)

    await waitFor(() => expect(fs.read).toHaveBeenCalledWith(target))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(fs.write).toHaveBeenCalledWith(target, 'body'))
  })

  it('names the way back on its close control', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<WikiNote fs={mockFs('body')} path="reports/plan.md" closeLabel="← Gallery" onClose={onClose} />)
    await user.click(await screen.findByRole('button', { name: '← Gallery' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  // #159 moved the way back to the head of the row - but only when the control
  // IS a way back. The Wiki destination opens this note in a pane, and a pane's
  // Close stays where a Close belongs: last, with the other actions.
  it('keeps a pane Close with the trailing actions', async () => {
    render(<WikiNote fs={mockFs('body')} path="reports/plan.md" onClose={() => {}} />)
    const head = document.querySelector('.wiki-note-head') as HTMLElement
    const controls = await waitFor(() => {
      const found = within(head).getAllByRole('button')
      expect(found.length).toBeGreaterThan(1)
      return found
    })
    expect(controls[0]).toHaveAccessibleName('Edit')
    expect(controls[controls.length - 1]).toHaveTextContent('Close')
  })
})

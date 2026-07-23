import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceTree } from './WorkspaceTree'
import type { FsAdapter } from '../../api/fsAdapter'
import type { FileEntry } from '../../types'

function entries(...names: Array<[string, 'file' | 'dir']>): FileEntry[] {
  return names.map(([name, type]) => ({ name, type, size: type === 'file' ? 10 : 0 }))
}

function mockFs(tree: Record<string, FileEntry[]>): FsAdapter {
  return {
    list: vi.fn(async (path: string) => ({ entries: tree[path] || [] })),
    read: vi.fn(async () => ({ content: 'hello from file' })),
    write: vi.fn(async () => ({})),
    mkdir: vi.fn(async () => ({})),
    rename: vi.fn(async () => ({})),
    remove: vi.fn(async () => ({})),
  }
}

const nestedTree: Record<string, FileEntry[]> = {
  '': entries(['artifacts', 'dir'], ['README.md', 'file']),
  artifacts: entries(['farewell-note.md', 'file'], ['design', 'dir']),
  'artifacts/design': entries(['card.json', 'file']),
}

describe('WorkspaceTree reveal / activePath', () => {
  it('expands ancestors and highlights a nested activePath', async () => {
    const fs = mockFs(nestedTree)
    render(<WorkspaceTree fs={fs} title="Demo" activePath="artifacts/farewell-note.md" />)

    const row = await screen.findByRole('button', { name: /farewell-note\.md/ })
    expect(row).toHaveClass('active')
    expect(row).toHaveAttribute('data-path', 'artifacts/farewell-note.md')
    // Parent folder must have been expanded for the file button to exist.
    expect(fs.list).toHaveBeenCalledWith('artifacts')
  })

  it('does not auto-open the editor on reveal so the tree highlight stays visible', async () => {
    const fs = mockFs(nestedTree)
    render(<WorkspaceTree fs={fs} title="Demo" activePath="artifacts/farewell-note.md" />)

    const row = await screen.findByRole('button', { name: /farewell-note\.md/ })
    expect(row).toHaveClass('active')
    // Editor would cover the tree (absolute inset); Reveal must leave it closed.
    expect(fs.read).not.toHaveBeenCalled()
    expect(screen.queryByTitle('artifacts/farewell-note.md')).not.toBeInTheDocument()
  })

  it('expands multiple ancestor levels for a deeply nested path', async () => {
    const fs = mockFs(nestedTree)
    render(<WorkspaceTree fs={fs} title="Demo" activePath="artifacts/design/card.json" />)

    const row = await screen.findByRole('button', { name: /card\.json/ })
    expect(row).toHaveClass('active')
    expect(fs.list).toHaveBeenCalledWith('artifacts')
    expect(fs.list).toHaveBeenCalledWith('artifacts/design')
  })

  it('does not steal open state when an external onOpenFile handler is provided', async () => {
    const fs = mockFs(nestedTree)
    const onOpenFile = vi.fn()
    render(
      <WorkspaceTree
        fs={fs}
        title="Wiki"
        activePath="artifacts/farewell-note.md"
        onOpenFile={onOpenFile}
      />,
    )

    await screen.findByRole('button', { name: /farewell-note\.md/ })
    // Wiki owns the editor pane - tree should only highlight, not auto-open.
    expect(fs.read).not.toHaveBeenCalled()
    expect(onOpenFile).not.toHaveBeenCalled()
  })
})

describe('WorkspaceTree create / rename inline input', () => {
  it('labels the new-file input and shows a name placeholder', async () => {
    const user = userEvent.setup()
    const fs = mockFs({ '': entries(['README.md', 'file']) })
    render(<WorkspaceTree fs={fs} title="Demo" onOpenFile={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'New file' }))
    const input = await screen.findByRole('textbox', { name: 'New file name' })
    expect(input).toHaveAttribute('placeholder', 'file-name')
    expect(input).toHaveFocus()
  })

  it('labels the new-folder input and shows a folder placeholder', async () => {
    const user = userEvent.setup()
    const fs = mockFs({ '': entries(['README.md', 'file']) })
    render(<WorkspaceTree fs={fs} title="Demo" onOpenFile={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'New folder' }))
    const input = await screen.findByRole('textbox', { name: 'New folder name' })
    expect(input).toHaveAttribute('placeholder', 'folder-name')
  })

  it('labels rename with the current entry name', async () => {
    const user = userEvent.setup()
    const fs = mockFs({ '': entries(['notes.md', 'file']) })
    render(<WorkspaceTree fs={fs} title="Demo" onOpenFile={vi.fn()} />)

    const row = await screen.findByRole('button', { name: /notes\.md/ })
    await user.pointer({ keys: '[MouseRight>]', target: row })
    await user.click(await screen.findByRole('button', { name: 'Rename' }))
    expect(await screen.findByRole('textbox', { name: 'Rename notes.md' })).toBeInTheDocument()
  })

  it('creates a wiki note with the default .md extension from the labeled input', async () => {
    const user = userEvent.setup()
    const fs = mockFs({ '': entries(['index.md', 'file']) })
    const onOpenFile = vi.fn()
    render(<WorkspaceTree fs={fs} title="Wiki" onOpenFile={onOpenFile} defaultExt="md" fileFilter={n => n.endsWith('.md')} />)

    await user.click(screen.getByRole('button', { name: 'New file' }))
    const input = await screen.findByRole('textbox', { name: 'New file name' })
    await user.type(input, 'gnhf-e2e-tree-note{Enter}')

    await waitFor(() => expect(fs.write).toHaveBeenCalledWith('gnhf-e2e-tree-note.md', ''))
    expect(onOpenFile).toHaveBeenCalledWith('gnhf-e2e-tree-note.md')
  })
})

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
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

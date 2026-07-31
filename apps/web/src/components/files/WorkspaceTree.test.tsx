import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceTree } from './WorkspaceTree'
import type { FsAdapter, ReadOnlyFsAdapter } from '../../api/fsAdapter'
import type { FileRef } from '../../api/files'
import type { FileEntry } from '../../types'

vi.mock('@uiw/react-codemirror', () => ({
  default: ({
    value,
    editable,
    onChange,
  }: {
    value?: string
    editable?: boolean
    onChange?: (value: string) => void
  }) => (
    <textarea
      data-testid="codemirror-stub"
      data-editable={String(editable !== false)}
      value={value ?? ''}
      readOnly={editable === false}
      onChange={e => onChange?.(e.target.value)}
    />
  ),
}))

function entries(...names: Array<[string, 'file' | 'dir']>): FileEntry[] {
  return names.map(([name, type]) => ({ name, type, size: type === 'file' ? 10 : 0 }))
}

function mockFs(tree: Record<string, FileEntry[]>): FsAdapter {
  return {
    list: vi.fn(async (ref: FileRef) => ({ entries: typeof ref === 'string' ? tree[ref] || [] : [] })),
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
  it('keeps a server target attached while traversing and opening merged Ops entries', async () => {
    const opsDirTarget = {
      project: 'demo',
      area: { kind: 'ops', id: 12 },
      path: 'reports',
    }
    const opsFileTarget = {
      project: 'demo',
      area: { kind: 'ops', id: 12 },
      path: 'reports/summary.md',
    }
    const fs = mockFs({})
    vi.mocked(fs.list).mockImplementation(async (ref: unknown) => {
      if (ref === '') {
        return {
          entries: [
            { name: 'reports', type: 'dir', size: 0, target: opsDirTarget },
          ] as unknown as FileEntry[],
        }
      }
      if (ref === opsDirTarget) {
        return {
          entries: [
            { name: 'summary.md', type: 'file', size: 10, target: opsFileTarget },
          ] as unknown as FileEntry[],
        }
      }
      return {
        entries: [
          { name: 'wrong-target.md', type: 'file', size: 10 },
        ] as unknown as FileEntry[],
      }
    })
    const onOpenFile = vi.fn()
    render(<WorkspaceTree fs={fs} title="Demo" onOpenFile={onOpenFile} />)

    await userEvent.click(await screen.findByRole('button', { name: 'reports' }))
    expect(fs.list).toHaveBeenLastCalledWith(opsDirTarget)
    await userEvent.click(await screen.findByRole('button', { name: 'summary.md' }))
    expect(onOpenFile).toHaveBeenCalledWith('reports/summary.md', opsFileTarget)
  })

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

  it('expands and highlights a revealed directory target', async () => {
    const fs = mockFs({
      '': entries(['ops', 'dir'], ['wiki', 'dir']),
      ops: entries(['wiki', 'dir']),
    })
    render(
      <WorkspaceTree
        fs={fs}
        title="Recovery inspection"
        activePath="ops"
        activePathKind="directory"
      />,
    )

    const row = await screen.findByRole('button', { name: /ops/ })
    expect(row).toHaveClass('active')
    expect(row).toHaveAttribute('data-path', 'ops')
    expect(row).toHaveAttribute('aria-expanded', 'true')
    await waitFor(() => expect(fs.list).toHaveBeenCalledWith('ops'))
  })

  it('keeps a read-only adapter free of mutation and save controls', async () => {
    const user = userEvent.setup()
    const fs: ReadOnlyFsAdapter = {
      list: vi.fn(async () => ({ entries: entries(['note.md', 'file']) })),
      read: vi.fn(async () => ({ content: 'inspection only' })),
    }
    render(<WorkspaceTree fs={fs} title="Recovery inspection" />)

    expect(await screen.findByText('Read-only')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New file' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New folder' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /note\.md/ }))
    expect(await screen.findByText('Read-only inspection')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('preserves a dirty editor buffer across recovery reveal, side switch, and restore', async () => {
    const user = userEvent.setup()
    const projectRead = vi.fn(async () => ({ content: 'project bytes' }))
    const legacyRead = vi.fn(async () => ({ content: 'legacy inspection bytes' }))
    const physicalRead = vi.fn(async () => ({ content: 'physical inspection bytes' }))
    const write = vi.fn(async () => ({}))
    const projectFs = mockFs({
      '': entries(['README.md', 'file'], ['notes', 'dir']),
      notes: entries(['todo.md', 'file']),
    })
    projectFs.read = projectRead
    projectFs.write = write

    const legacyFs: ReadOnlyFsAdapter = {
      list: vi.fn(async (path: string) => ({
        entries: path === '' ? entries(['wiki', 'dir'], ['notes', 'dir']) : entries(['todo.md', 'file']),
      })),
      read: legacyRead,
    }
    const physicalFs: ReadOnlyFsAdapter = {
      list: vi.fn(async (path: string) => ({
        entries: path === ''
          ? entries(['ops', 'dir'])
          : path === 'ops'
            ? entries(['notes', 'dir'])
            : entries(['todo.md', 'file']),
      })),
      read: physicalRead,
    }

    const view = render(<WorkspaceTree fs={projectFs} title="Demo" />)
    await user.click(await screen.findByRole('button', { name: /notes/ }))
    await user.click(await screen.findByRole('button', { name: /todo\.md/ }))
    await screen.findByDisplayValue('project bytes')
    const editor = screen.getByTestId('codemirror-stub')
    await user.clear(editor)
    await user.type(editor, 'unsaved owner edits')
    expect(screen.getByText(/Unsaved/)).toBeVisible()

    // Ops recovery reveal swaps to container inspection and highlights a path.
    view.rerender(
      <WorkspaceTree
        fs={legacyFs}
        title="Demo"
        activePath="notes/todo.md"
        activePathKind="file"
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Read-only inspection')).toBeVisible()
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByTitle('notes/todo.md')).toHaveTextContent('•')
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(legacyRead).not.toHaveBeenCalled()
    expect(projectRead).toHaveBeenCalledTimes(1)

    // Switching recovery side keeps the same dirty buffer mounted.
    view.rerender(
      <WorkspaceTree
        fs={physicalFs}
        title="Demo"
        activePath="ops/notes"
        activePathKind="directory"
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Read-only inspection')).toBeVisible()
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(physicalRead).not.toHaveBeenCalled()

    // Closing inspection restores ordinary Files write access without reload.
    view.rerender(<WorkspaceTree fs={projectFs} title="Demo" />)

    await waitFor(() => {
      expect(screen.getByText(/Unsaved/)).toBeVisible()
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save' })).toBeVisible()
    expect(screen.getByTestId('codemirror-stub')).toHaveAttribute('data-editable', 'true')
    expect(projectRead).toHaveBeenCalledTimes(1)
    expect(legacyRead).not.toHaveBeenCalled()
    expect(physicalRead).not.toHaveBeenCalled()
  })

  it('discards a clean editor on reveal but keeps the tree highlight path', async () => {
    const user = userEvent.setup()
    const fs = mockFs(nestedTree)
    const view = render(<WorkspaceTree fs={fs} title="Demo" />)

    await user.click(await screen.findByRole('button', { name: /README\.md/ }))
    await screen.findByDisplayValue('hello from file')
    expect(screen.getByTitle('README.md')).toBeInTheDocument()

    view.rerender(
      <WorkspaceTree fs={fs} title="Demo" activePath="artifacts/farewell-note.md" />,
    )

    const row = await screen.findByRole('button', { name: /farewell-note\.md/ })
    expect(row).toHaveClass('active')
    expect(screen.queryByTitle('README.md')).not.toBeInTheDocument()
    expect(screen.queryByTestId('codemirror-stub')).not.toBeInTheDocument()
  })

  it('starts clean after a project-scoped remount', async () => {
    const user = userEvent.setup()
    const first = mockFs({ '': entries(['a.md', 'file']) })
    first.read = vi.fn(async () => ({ content: 'first project' }))
    const second = mockFs({ '': entries(['b.md', 'file']) })
    second.read = vi.fn(async () => ({ content: 'second project' }))

    const view = render(<WorkspaceTree key="first" fs={first} title="First" />)
    await user.click(await screen.findByRole('button', { name: /a\.md/ }))
    await screen.findByDisplayValue('first project')
    await user.type(screen.getByTestId('codemirror-stub'), ' dirty')
    expect(screen.getByText(/Unsaved/)).toBeVisible()

    view.rerender(<WorkspaceTree key="second" fs={second} title="Second" />)

    expect(screen.queryByTestId('codemirror-stub')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue(/dirty/)).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /b\.md/ }))
    await screen.findByDisplayValue('second project')
    expect(second.read).toHaveBeenCalledWith('b.md')
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

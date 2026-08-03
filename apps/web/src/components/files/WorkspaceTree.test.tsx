import '@testing-library/jest-dom/vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceTree } from './WorkspaceTree'
import type { FsAdapter, ReadOnlyFsAdapter } from '../../api/fsAdapter'
import type { FileRef } from '../../api/files'
import type { FileEntry } from '../../types'
import { confirmDialog } from '../ui/Dialog'

const stylesSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../../styles.css'),
  'utf8',
)

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

vi.mock('../ui/Dialog', () => ({
  confirmDialog: vi.fn(async () => true),
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
        entries: path === ''
          ? entries(['wiki', 'dir'], ['notes', 'dir'], ['other.md', 'file'])
          : path === 'wiki'
            ? entries(['index.md', 'file'])
            : entries(['todo.md', 'file']),
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

    const view = render(<WorkspaceTree fs={projectFs} title="Demo" className="tool-files" />)
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
        className="tool-files"
        activePath="notes/todo.md"
        activePathKind="file"
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Unsaved · read-only during inspection')
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByTitle('notes/todo.md')).toHaveTextContent('•')
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(legacyRead).not.toHaveBeenCalled()
    expect(projectRead).toHaveBeenCalledTimes(1)

    const root = view.container.querySelector('.tool-files')
    const retainedEditor = view.container.querySelector('.file-editor')
    const treeScroll = view.container.querySelector('.tree-scroll')
    expect(root).toHaveClass('files-retain-dirty')
    expect(retainedEditor).toHaveClass('file-editor-retained')
    expect(screen.getByText(/inspection tree stays available/i)).toBeVisible()
    // Docked layout keeps the tree and sticky buffer as siblings - the editor
    // must not cover tree-scroll (absolute inset would put it first in hit tests).
    expect(treeScroll?.compareDocumentPosition(retainedEditor!)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const revealRow = await screen.findByRole('button', { name: /todo\.md/ })
    expect(revealRow).toHaveClass('active')
    expect(treeScroll?.contains(revealRow)).toBe(true)

    // Pointer browse of another inspection file keeps the sticky dirty buffer
    // and surfaces the selection on the tree row only.
    await user.click(await screen.findByRole('button', { name: /other\.md/ }))
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved · read-only during inspection')
    expect(screen.getByTitle('notes/todo.md')).toHaveTextContent('•')
    expect(screen.getByRole('button', { name: /other\.md/ })).toHaveClass('active')
    expect(legacyRead).not.toHaveBeenCalled()

    // Keyboard browse of another inspection entry also keeps the buffer.
    await user.click(await screen.findByRole('button', { name: /^wiki$/ }))
    const indexRow = await screen.findByRole('button', { name: /index\.md/ })
    indexRow.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByRole('button', { name: /index\.md/ })).toHaveClass('active')
    expect(legacyRead).not.toHaveBeenCalled()

    // Switching recovery side keeps the same dirty buffer mounted.
    view.rerender(
      <WorkspaceTree
        fs={physicalFs}
        title="Demo"
        className="tool-files"
        activePath="ops/notes"
        activePathKind="directory"
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Unsaved · read-only during inspection')
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(physicalRead).not.toHaveBeenCalled()
    expect(view.container.querySelector('.tool-files')).toHaveClass('files-retain-dirty')

    // Closing inspection restores ordinary Files write access without reload.
    view.rerender(<WorkspaceTree fs={projectFs} title="Demo" className="tool-files" />)

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/Unsaved/)
    })
    expect(screen.getByDisplayValue('unsaved owner edits')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save' })).toBeVisible()
    expect(screen.getByTestId('codemirror-stub')).toHaveAttribute('data-editable', 'true')
    expect(view.container.querySelector('.tool-files')).not.toHaveClass('files-retain-dirty')
    expect(view.container.querySelector('.file-editor')).not.toHaveClass('file-editor-retained')
    expect(projectRead).toHaveBeenCalledTimes(1)
    expect(legacyRead).not.toHaveBeenCalled()
    expect(physicalRead).not.toHaveBeenCalled()
  })

  it('keeps inspection tree hit targets free while a dirty buffer is retained', async () => {
    const user = userEvent.setup()
    const projectFs = mockFs({
      '': entries(['notes', 'dir']),
      notes: entries(['todo.md', 'file']),
    })
    projectFs.read = vi.fn(async () => ({ content: 'project bytes' }))
    const inspectionFs: ReadOnlyFsAdapter = {
      list: vi.fn(async (path: string) => ({
        entries: path === ''
          ? entries(['notes', 'dir'], ['other.md', 'file'], ['alpha.md', 'file'])
          : entries(['todo.md', 'file']),
      })),
      read: vi.fn(async () => ({ content: 'inspection' })),
    }

    const view = render(<WorkspaceTree fs={projectFs} title="Demo" className="tool-files" />)
    await user.click(await screen.findByRole('button', { name: /notes/ }))
    await user.click(await screen.findByRole('button', { name: /todo\.md/ }))
    await screen.findByDisplayValue('project bytes')
    await user.clear(screen.getByTestId('codemirror-stub'))
    await user.type(screen.getByTestId('codemirror-stub'), 'sticky geometry buffer')

    view.rerender(
      <WorkspaceTree
        fs={inspectionFs}
        title="Demo"
        className="tool-files"
        activePath="notes/todo.md"
        activePathKind="file"
      />,
    )

    await waitFor(() => {
      expect(view.container.querySelector('.file-editor')).toHaveClass('file-editor-retained')
    })

    const treeScroll = view.container.querySelector('.tree-scroll') as HTMLElement
    const retainedEditor = view.container.querySelector('.file-editor') as HTMLElement
    const revealRow = await screen.findByRole('button', { name: /todo\.md/ })

    // Geometry contract for real hit-testing: tree occupies the upper band,
    // docked editor the lower band - no full-pane absolute cover.
    vi.spyOn(treeScroll, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, top: 0, left: 0, right: 280, bottom: 360,
      width: 280, height: 360, toJSON() { return this },
    })
    vi.spyOn(retainedEditor, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 360, top: 360, left: 0, right: 280, bottom: 520,
      width: 280, height: 160, toJSON() { return this },
    })
    vi.spyOn(revealRow, 'getBoundingClientRect').mockReturnValue({
      x: 8, y: 48, top: 48, left: 8, right: 240, bottom: 72,
      width: 232, height: 24, toJSON() { return this },
    })

    const treeBox = treeScroll.getBoundingClientRect()
    const editorBox = retainedEditor.getBoundingClientRect()
    const rowBox = revealRow.getBoundingClientRect()
    expect(editorBox.top).toBeGreaterThanOrEqual(treeBox.bottom - 1)
    expect(rowBox.bottom).toBeLessThanOrEqual(treeBox.bottom)
    expect(rowBox.top).toBeGreaterThanOrEqual(treeBox.top)
    // A point over the reveal highlight must not fall inside the docked editor.
    const probeY = (rowBox.top + rowBox.bottom) / 2
    expect(probeY < editorBox.top || probeY > editorBox.bottom).toBe(true)

    await user.click(screen.getByRole('button', { name: /alpha\.md/ }))
    expect(screen.getByRole('button', { name: /alpha\.md/ })).toHaveClass('active')
    expect(screen.getByDisplayValue('sticky geometry buffer')).toBeVisible()
    expect(revealRow).toBeVisible()
  })

  it('asks before Close discards a dirty buffer and keeps bytes on cancel', async () => {
    const user = userEvent.setup()
    const confirm = vi.mocked(confirmDialog)
    confirm.mockResolvedValueOnce(false)
    const projectFs = mockFs({ '': entries(['a.md', 'file']) })
    projectFs.read = vi.fn(async () => ({ content: 'project bytes' }))

    render(<WorkspaceTree fs={projectFs} title="Demo" />)
    await user.click(await screen.findByRole('button', { name: /a\.md/ }))
    await screen.findByDisplayValue('project bytes')
    await user.type(screen.getByTestId('codemirror-stub'), ' keep me')

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Discard unsaved edits?',
      danger: true,
    }))
    expect(screen.getByDisplayValue('project bytes keep me')).toBeVisible()

    confirm.mockResolvedValueOnce(true)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByTestId('codemirror-stub')).not.toBeInTheDocument()
    })
  })

  it('CSS docks retained dirty editors instead of covering the tree', () => {
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '')
    const retained = strip(
      (stylesSource.match(/\.file-editor\.file-editor-retained,\s*\.files-retain-dirty \.file-editor\s*\{([^}]*)\}/)
        || stylesSource.match(/\.files-retain-dirty \.file-editor,\s*\.file-editor\.file-editor-retained\s*\{([^}]*)\}/)
        || [])[1] || '',
    )
    expect(retained).toMatch(/position:\s*relative/)
    expect(retained).toMatch(/inset:\s*auto/)
    expect(retained).toMatch(/max-height:\s*42%/)
    expect(retained).not.toMatch(/position:\s*absolute/)
    expect(stylesSource).toMatch(/\.file-editor-retain-banner\s*\{[^}]*var\(--ui-warning-bg\)/s)
    expect(stylesSource).toMatch(/\.files-retain-dirty \.tree-scroll\s*\{[^}]*flex:\s*1/s)
    expect(stylesSource).toMatch(/\.tool-files\.files-retain-dirty \.tree-scroll\s*\{[^}]*min-height:\s*var\(--space-8\)/s)
  })

  it('opens inspection files normally when the ordinary Files buffer is clean', async () => {
    const user = userEvent.setup()
    const projectRead = vi.fn(async () => ({ content: 'project bytes' }))
    const inspectionRead = vi.fn(async (path: string) => ({
      content: path === 'notes/todo.md' ? 'inspection todo' : 'inspection other',
    }))
    const write = vi.fn(async () => ({}))
    const projectFs = mockFs({
      '': entries(['notes', 'dir']),
      notes: entries(['todo.md', 'file']),
    })
    projectFs.read = projectRead
    projectFs.write = write
    const inspectionFs: ReadOnlyFsAdapter = {
      list: vi.fn(async (path: string) => ({
        entries: path === ''
          ? entries(['notes', 'dir'], ['other.md', 'file'])
          : entries(['todo.md', 'file']),
      })),
      read: inspectionRead,
    }

    const view = render(<WorkspaceTree fs={projectFs} title="Demo" />)
    await user.click(await screen.findByRole('button', { name: /notes/ }))
    await user.click(await screen.findByRole('button', { name: /todo\.md/ }))
    await screen.findByDisplayValue('project bytes')
    expect(screen.getByText('Up to date')).toBeVisible()

    view.rerender(
      <WorkspaceTree
        fs={inspectionFs}
        title="Demo"
        activePath="notes/todo.md"
        activePathKind="file"
      />,
    )

    // Clean buffer is closed on reveal so the tree highlight stays visible.
    await screen.findByRole('button', { name: /todo\.md/ })
    expect(screen.queryByTestId('codemirror-stub')).not.toBeInTheDocument()

    await user.click(await screen.findByRole('button', { name: /other\.md/ }))
    await screen.findByDisplayValue('inspection other')
    expect(screen.getByRole('status')).toHaveTextContent('Read-only inspection')
    expect(inspectionRead).toHaveBeenCalledWith('other.md')
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

describe('WorkspaceTree skipped symlinks (prune C7)', () => {
  const symlinkTree: Record<string, FileEntry[]> = {
    '': [
      { name: 'sub', type: 'dir', size: 0 },
      { name: 'real.md', type: 'file', size: 10 },
      {
        name: 'escape-dir',
        type: 'symlink',
        size: 0,
        skipped: true,
        reason: 'symlink - not followed',
      },
    ],
    sub: entries(['nested.md', 'file']),
  }

  it('shows the symlink, keeps siblings usable, and offers nothing to open', async () => {
    const fs = mockFs(symlinkTree)
    const onOpenFile = vi.fn()
    render(<WorkspaceTree fs={fs} title="Demo" onOpenFile={onOpenFile} />)

    const row = await screen.findByText('escape-dir')
    // Not a button: a skipped entry has no click target at all, so no click
    // can ever ask the server to follow it.
    expect(screen.queryByRole('button', { name: /escape-dir/ })).toBeNull()
    expect(row.closest('.tree-row')).toHaveClass('skipped')
    // and the reason is visible, not hidden in a tooltip only
    expect(screen.getByText('symlink - not followed')).toBeInTheDocument()

    // siblings still work - one stray link bricks nothing
    await userEvent.click(screen.getByRole('button', { name: 'sub' }))
    await userEvent.click(await screen.findByRole('button', { name: 'nested.md' }))
    expect(onOpenFile).toHaveBeenCalledWith('sub/nested.md')
  })

  it('styles the skipped row from tokens, with no hover affordance', () => {
    const block = stylesSource.match(/\.tree-row\.skipped\s*\{([^}]*)\}/)?.[1] || ''
    expect(block).toMatch(/color:\s*var\(--ui-text-tertiary\)/)
    expect(block).toMatch(/cursor:\s*default/)
    expect(block).not.toMatch(/#[0-9a-f]{3,}/i)
    expect(stylesSource).toMatch(/\.tree-row\.skipped:hover\s*\{[^}]*background:\s*transparent/s)
  })
})

// --- Actionable fail-closed refusals (prune B5, #133) ------------------------
describe('WorkspaceTree surfaces a write refusal the owner can act on', () => {
  it('shows the server sentence, not the transport wrapper around it', async () => {
    const fs = mockFs({ '': entries(['README.md', 'file']) })
    vi.mocked(fs.mkdir).mockRejectedValue(new Error(
      'Failed to create folder (400 Bad Request): That path crosses a symlink, '
      + 'which Proxima never follows. Open the real folder this link points at '
      + 'instead, or replace the link with a real folder.',
    ))
    render(<WorkspaceTree fs={fs} title="Demo" writableFs={fs} />)

    await userEvent.click(await screen.findByRole('button', { name: 'New folder' }))
    await userEvent.type(screen.getByRole('textbox'), 'link{Enter}')

    const error = await screen.findByText(/crosses a symlink/i)
    expect(error).toHaveTextContent('Open the real folder this link points at instead')
    // No "Error: Failed to create folder (400 Bad Request):" noise in front of
    // the one sentence the owner needs.
    expect(error.textContent).not.toMatch(/400 Bad Request/)
    expect(error.textContent).not.toMatch(/^Error:/)
  })
})

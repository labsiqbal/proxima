import '@testing-library/jest-dom/vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { containerInspectionFs, projectFs } from '../../api/fsAdapter'
import { ToolDock } from './ToolDock'
import type { Project } from '../../types'

// One folder shape for every adapter: a directory, a file, and a symlink the
// backend already marked warn-and-skip (prune C7) so the dock tree can be
// checked against the real-disk semantics it must keep.
const entries = [
  { name: 'wiki', type: 'dir', size: 0 },
  { name: 'notes.md', type: 'file', size: 12 },
  { name: 'outside', type: 'symlink', size: 0, skipped: true, reason: 'symlink skipped' },
]

vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({ entries }),
    read: vi.fn().mockResolvedValue({ content: '' }),
    write: vi.fn(),
    mkdir: vi.fn(),
    rename: vi.fn(),
    remove: vi.fn(),
  })),
  containerInspectionFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({ entries }),
    read: vi.fn().mockResolvedValue({ content: '' }),
  })),
}))
vi.mock('@uiw/react-codemirror', () => ({
  default: ({ value }: { value?: string }) => <textarea data-testid="codemirror-stub" value={value ?? ''} readOnly />,
}))
vi.mock('../terminal/TerminalTabs', () => ({
  TerminalTabs: ({ projectSlug }: { projectSlug?: string }) => <div data-testid="terminal-stub">terminal:{projectSlug || 'none'}</div>,
}))
vi.mock('../files/AppRunner', () => ({
  AppRunner: ({ slug }: { slug: string }) => <div data-testid="preview-stub">preview:{slug}</div>,
}))

const project = { slug: 'master', name: 'Master', visibility: 'private' } as Project

describe('ToolDock', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('offers Files next to Terminal and Preview, plus Settings', () => {
    const onOpenSettings = vi.fn()
    render(<ToolDock token="t" project={project} onOpenSettings={onOpenSettings} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    // Browsing is a tool again (#145): the destination is Artifacts (ADR-0043).
    for (const name of ['Terminal', 'Files', 'Preview']) {
      expect(rail.querySelector(`[aria-label="${name}"]`)).toBeTruthy()
    }
    ;(rail.querySelector('[aria-label="Settings"]') as HTMLButtonElement).click()
    expect(onOpenSettings).toHaveBeenCalled()
  })

  it('opens the terminal overlay and keeps shells mounted after closing', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    expect(await screen.findByTestId('terminal-stub')).toBeVisible()
    // Toggling the tool closed hides the panel but must NOT unmount the
    // terminal — that would SIGHUP every running shell.
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    expect(screen.getByTestId('terminal-stub')).not.toBeVisible()
  })

  it('marks the shell tool-open while a panel is showing so main content can reflow', async () => {
    const user = userEvent.setup()
    const shell = document.createElement('div')
    shell.className = 'app-shell'
    document.body.appendChild(shell)
    const root = document.createElement('div')
    shell.appendChild(root)
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />, { container: root })
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    expect(shell.classList.contains('tool-open')).toBe(false)
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    expect(shell.classList.contains('tool-open')).toBe(true)
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    expect(shell.classList.contains('tool-open')).toBe(false)
    shell.remove()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    expect(await screen.findByTestId('terminal-stub')).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.getByTestId('terminal-stub')).not.toBeVisible()
  })

  it('asks for a project before Preview can work', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={null} onOpenSettings={vi.fn()} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Preview"]') as HTMLElement)
    expect(screen.getByText('Pick a project to run and preview its app.')).toBeVisible()
  })

  it('unmounts the preview when closed — its server lives on the backend', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Preview"]') as HTMLElement)
    expect(await screen.findByTestId('preview-stub')).toBeVisible()
    await user.click(rail.querySelector('[aria-label="Preview"]') as HTMLElement)
    expect(screen.queryByTestId('preview-stub')).not.toBeInTheDocument()
  })

  it('suppresses Project tools during Task synchronization without unmounting terminals', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <ToolDock token="t" project={project} available onOpenSettings={vi.fn()} />,
    )
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    expect(await screen.findByTestId('terminal-stub')).toBeVisible()

    rerender(
      <ToolDock token="t" project={project} available={false} onOpenSettings={vi.fn()} />,
    )

    expect(screen.queryByRole('complementary', { name: 'Tools' })).not.toBeInTheDocument()
    expect(screen.getByTestId('terminal-stub')).not.toBeVisible()
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', { detail: { path: 'wrong-project.md' } }))
    })
    expect(screen.getByLabelText('Tool panel')).toHaveAttribute('aria-hidden', 'true')

    rerender(
      <ToolDock token="t" project={project} available onOpenSettings={vi.fn()} />,
    )
    expect(screen.getByRole('complementary', { name: 'Tools' })).toBeVisible()
    expect(screen.getByTestId('terminal-stub')).toBeInTheDocument()
  })
})

// The file tree is a dock tool again (#145): the destination is Artifacts
// (ADR-0043), and browsing the real disk is the utility you open next to it.
describe('ToolDock Files browser', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const openFiles = async (user: ReturnType<typeof userEvent.setup>) => {
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Files"]') as HTMLElement)
  }

  it('browses the active project on the real disk, skip markers included', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await openFiles(user)
    expect(projectFs).toHaveBeenCalledWith('t', 'master')
    expect(await screen.findByText('notes.md')).toBeVisible()
    expect(screen.getByText('wiki')).toBeVisible()
    // Warn-and-skip (prune C7) stays inert: acknowledged, never a click target.
    const skipped = document.querySelector('.tree-row.skipped') as HTMLElement
    expect(skipped).toHaveTextContent('outside')
    expect(skipped.tagName).toBe('DIV')
  })

  it('hands an opened file to the main window instead of an in-panel editor', async () => {
    const user = userEvent.setup()
    const onOpenFile = vi.fn()
    render(<ToolDock token="t" project={project} onOpenFile={onOpenFile} onOpenSettings={vi.fn()} />)
    await openFiles(user)
    await user.click(await screen.findByText('notes.md'))
    expect(onOpenFile).toHaveBeenCalledWith('master', 'notes.md', undefined)
    expect(document.querySelector('.file-editor')).toBeNull()
  })

  it('asks for a project before Files can work', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={null} onOpenSettings={vi.fn()} />)
    await openFiles(user)
    expect(screen.getByText('Pick a project to browse its files.')).toBeVisible()
    expect(projectFs).not.toHaveBeenCalled()
  })

  it('keeps the tree mounted after the panel closes', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await openFiles(user)
    expect(await screen.findByText('notes.md')).toBeVisible()
    await openFiles(user)
    expect(screen.getByText('notes.md')).not.toBeVisible()
  })

  it('absorbs the Ops-migration Container inspection - read-only, at the named project', async () => {
    const user = userEvent.setup()
    const onOpenFile = vi.fn()
    render(<ToolDock token="t" project={project} onOpenFile={onOpenFile} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: { path: 'wiki', pathKind: 'directory', projectSlug: 'ops', rootSide: 'container' },
      }))
    })
    expect(containerInspectionFs).toHaveBeenCalledWith('t', 'ops')
    // The Container root side is never read through the ordinary project adapter.
    expect(projectFs).not.toHaveBeenCalledWith('t', 'ops')
    expect(await screen.findByText('Read-only')).toBeVisible()
    expect(screen.getByText(/Inspecting/)).toBeVisible()
    // Container-root bytes exist only through the inspection adapter, so this
    // tree reads them in place rather than handing a path to the main window.
    await user.click(document.querySelector('[data-path="notes.md"]') as HTMLElement)
    expect(onOpenFile).not.toHaveBeenCalled()
  })

  it('leaves the inspection and returns to the active project tree', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: { path: 'wiki', pathKind: 'directory', projectSlug: 'ops', rootSide: 'container' },
      }))
    })
    await user.click(await screen.findByRole('button', { name: 'Close inspection' }))
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'master'))
    expect(screen.queryByText(/Inspecting/)).not.toBeInTheDocument()
  })

  it('highlights an ordinary reveal inside the active project tree', async () => {
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: { path: 'wiki/notes.md', projectSlug: 'master' },
      }))
    })
    expect(projectFs).toHaveBeenCalledWith('t', 'master')
    await waitFor(() => {
      expect(document.querySelector('[data-path="wiki/notes.md"]')).toHaveClass('active')
    })
  })
})

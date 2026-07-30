import '@testing-library/jest-dom/vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { containerInspectionFs, projectFs } from '../../api/fsAdapter'
import { ToolDock } from './ToolDock'
import type { Project } from '../../types'

vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({ entries: [] }),
    read: vi.fn(),
    write: vi.fn(),
    mkdir: vi.fn(),
    rename: vi.fn(),
    remove: vi.fn(),
  })),
  containerInspectionFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({ entries: [] }),
    read: vi.fn(),
  })),
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

  it('offers Terminal, Files, and Preview as rail tools plus Settings', () => {
    const onOpenSettings = vi.fn()
    render(<ToolDock token="t" project={project} onOpenSettings={onOpenSettings} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
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
    await user.click(rail.querySelector('[aria-label="Files"]') as HTMLElement)
    expect(shell.classList.contains('tool-open')).toBe(true)
    await user.click(rail.querySelector('[aria-label="Files"]') as HTMLElement)
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

  it('asks for a project before Files or Preview can work', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={null} onOpenSettings={vi.fn()} />)
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Files"]') as HTMLElement)
    expect(screen.getByText('Pick a project to browse its files.')).toBeVisible()
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

  it('opens the Files panel when proxima:reveal-file fires', async () => {
    // Path expand/highlight is covered in WorkspaceTree tests; here we only
    // assert the dock owns the event and surfaces the Files tool.
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', { detail: { path: 'artifacts/note.md' } }))
    })
    const panel = await screen.findByLabelText('Tool panel')
    expect(panel).toHaveAttribute('aria-hidden', 'false')
    const filesTab = panel.querySelector('.tool-panel-tab.active')
    expect(filesTab?.textContent).toMatch(/Files/)
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

  it('uses a read-only Container adapter for migration reveal targets', async () => {
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: {
          path: 'wiki',
          projectSlug: project.slug,
          rootSide: 'container',
        },
      }))
    })
    await screen.findByLabelText('Tool panel')
    expect(containerInspectionFs).toHaveBeenLastCalledWith('t', project.slug)
    expect(screen.getByText('Read-only')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'New file' })).not.toBeInTheDocument()
  })

  it('clears recovery inspection when the panel closes and Files reopens', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: {
          path: 'wiki',
          pathKind: 'directory',
          projectSlug: project.slug,
          rootSide: 'container',
        },
      }))
    })
    expect(containerInspectionFs).toHaveBeenCalledWith('t', project.slug)

    await user.click(screen.getByRole('button', { name: 'Close tool panel' }))
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Files"]') as HTMLElement)

    expect(projectFs).toHaveBeenLastCalledWith('t', project.slug)
    expect(screen.queryByText('Read-only')).not.toBeInTheDocument()
  })

  it('clears recovery inspection when the active Project changes', async () => {
    const nextProject = { ...project, slug: 'next', name: 'Next' }
    const view = render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: {
          path: 'ops',
          pathKind: 'directory',
          projectSlug: project.slug,
          rootSide: 'container',
        },
      }))
    })
    expect(containerInspectionFs).toHaveBeenCalledWith('t', project.slug)

    await act(async () => {
      view.rerender(
        <ToolDock token="t" project={nextProject} onOpenSettings={vi.fn()} />,
      )
    })

    expect(projectFs).toHaveBeenLastCalledWith('t', nextProject.slug)
    expect(screen.queryByText('Read-only')).not.toBeInTheDocument()
  })

  it('clears recovery inspection before an ordinary Files open', async () => {
    const user = userEvent.setup()
    render(<ToolDock token="t" project={project} onOpenSettings={vi.fn()} />)
    await act(async () => {
      window.dispatchEvent(new CustomEvent('proxima:reveal-file', {
        detail: {
          path: 'ops',
          pathKind: 'directory',
          projectSlug: project.slug,
          rootSide: 'container',
        },
      }))
    })
    const rail = screen.getByRole('complementary', { name: 'Tools' })
    await user.click(rail.querySelector('[aria-label="Terminal"]') as HTMLElement)
    await user.click(rail.querySelector('[aria-label="Files"]') as HTMLElement)

    expect(projectFs).toHaveBeenLastCalledWith('t', project.slug)
    expect(screen.queryByText('Read-only')).not.toBeInTheDocument()
  })
})

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { containerInspectionFs, projectFs } from '../api/fsAdapter'
import { listArchive, listArchiveBadges } from '../api/archive'
import { FilesScreen } from './FilesScreen'
import type { Project } from '../types'

vi.mock('../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({
      entries: [{ name: 'note.md', type: 'file', size: 12 }],
    }),
    read: vi.fn().mockResolvedValue({ content: '# note' }),
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
vi.mock('../api/archive', () => ({
  listArchive: vi.fn(),
  listArchiveBadges: vi.fn(),
}))
vi.mock('../components/artifacts/ArtifactViewer', () => ({
  ArtifactViewer: ({ slug, items }: { slug: string; items: { path: string }[] }) => (
    <div data-testid="viewer">viewer:{slug}:{items[0]?.path}</div>
  ),
}))
vi.mock('../components/artifacts/DeliverablesLens', () => ({
  DeliverablesLens: ({ project, missingOnly }: { project?: string; missingOnly?: boolean }) => (
    <div data-testid="deliverables-lens">lens:{project || 'all'}:{missingOnly ? 'history' : 'present'}</div>
  ),
}))
vi.mock('../components/artifacts/ArchiveRecordPage', () => ({
  ArchiveRecordPage: ({ project, slug }: { project: string; slug: string }) => (
    <div data-testid="record-panel">record:{project}:{slug}</div>
  ),
}))

const alpha = { slug: 'alpha', name: 'Alpha', visibility: 'private' } as Project
const beta = { slug: 'beta', name: 'Beta', visibility: 'private' } as Project

const emptyList = { items: [], total: 0, limit: 50, offset: 0, counts: { by_type: {}, by_status: {} } }

describe('FilesScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listArchiveBadges).mockResolvedValue({ items: [] })
    vi.mocked(listArchive).mockResolvedValue(emptyList)
  })

  it('browses only the active Container in Work', async () => {
    render(<FilesScreen token="t" projects={[alpha, beta]} activeProject={alpha} />)
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(projectFs).not.toHaveBeenCalledWith('t', 'beta')
    // No per-project headings: one tree, scoped by the sidebar's project switcher.
    expect(screen.queryByRole('heading', { name: 'Alpha', level: 2 })).not.toBeInTheDocument()
  })

  it('goes global in Delegate and narrows through the head filter', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha, beta]} globalScope />)
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(projectFs).toHaveBeenCalledWith('t', 'beta')
    expect(screen.getByRole('heading', { name: 'Alpha', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Beta', level: 2 })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /All projects/ }))
    await user.click(await screen.findByRole('option', { name: 'Beta' }))
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Alpha', level: 2 })).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Beta', level: 2 })).toBeInTheDocument()
  })

  it('opens a picked file in the ArtifactViewer', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /note\.md/ }))
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:note.md')
  })

  it('uses the read-only Container adapter for migration reveal targets', async () => {
    render(
      <FilesScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
      />,
    )
    await waitFor(() => expect(containerInspectionFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(projectFs).not.toHaveBeenCalled()
  })

  it('drops back to the ordinary adapter once the reveal is gone', async () => {
    const { rerender } = render(
      <FilesScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
      />,
    )
    await waitFor(() => expect(containerInspectionFs).toHaveBeenCalled())
    rerender(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} revealPath={null} />)
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
  })

  // ── The merged Archive (prune Part D, #139) ──

  it('offers the three lenses and defaults to browsing the disk', async () => {
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} />)
    expect(screen.getByRole('button', { name: 'Browse' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Deliverables' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument()
    await waitFor(() => expect(projectFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(screen.queryByTestId('deliverables-lens')).not.toBeInTheDocument()
  })

  it('badges agent-produced files in the tree and opens the record from the badge', async () => {
    const user = userEvent.setup()
    vi.mocked(listArchiveBadges).mockResolvedValue({
      items: [{ id: 1, slug: 'note-md-v1', name: 'note.md', type: 'doc', path: 'note.md', status: 'approved', version: 1, file_missing: false }],
    })
    const onOpenRecord = vi.fn()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} onOpenRecord={onOpenRecord} />)
    await screen.findByRole('button', { name: /note\.md/ })
    const badge = await waitFor(() => {
      const node = document.querySelector('.files-deliv-badge')
      expect(node).not.toBeNull()
      return node as HTMLElement
    })
    expect(badge).toHaveAttribute('data-status', 'approved')
    await user.click(badge)
    expect(onOpenRecord).toHaveBeenCalledWith('alpha', 'note-md-v1')
    // The badge is a shortcut, not a file open.
    expect(screen.queryByTestId('viewer')).not.toBeInTheDocument()
  })

  it('shows the deliverable ledger under the Deliverables lens, scoped to the active project', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(screen.getByRole('button', { name: 'Deliverables' }))
    expect(await screen.findByTestId('deliverables-lens')).toHaveTextContent('lens:alpha:present')
  })

  it('narrows to gone-file records under the History lens', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(screen.getByRole('button', { name: 'History' }))
    expect(await screen.findByTestId('deliverables-lens')).toHaveTextContent('lens:alpha:history')
  })

  it('keeps the Delegate ledger global until the head filter narrows it', async () => {
    const user = userEvent.setup()
    render(<FilesScreen token="t" projects={[alpha, beta]} globalScope />)
    await user.click(screen.getByRole('button', { name: 'Deliverables' }))
    expect(await screen.findByTestId('deliverables-lens')).toHaveTextContent('lens:all:present')
  })

  it('renders the record panel for a record permanent address', async () => {
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} archiveRecord={{ project: 'alpha', slug: 'note-md-v1' }} />)
    expect(await screen.findByTestId('record-panel')).toHaveTextContent('record:alpha:note-md-v1')
  })

  it('resolves a pending chat artifact to its registry record', async () => {
    vi.mocked(listArchive).mockResolvedValue({
      ...emptyList,
      items: [{ id: 1, slug: 'report-md-v1', project_slug: 'alpha', path: 'reports/report.md' } as never],
      total: 1,
    })
    const onOpenRecord = vi.fn()
    const onConsumed = vi.fn()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} onOpenRecord={onOpenRecord} onPendingArtifactConsumed={onConsumed}
      pendingArtifact={{ type: 'doc', title: 'report.md', path: 'reports/report.md', project_slug: 'alpha' }} />)
    await waitFor(() => expect(onOpenRecord).toHaveBeenCalledWith('alpha', 'report-md-v1'))
    expect(onConsumed).toHaveBeenCalled()
  })

  it('opens an unregistered pending artifact in the ArtifactViewer', async () => {
    const onConsumed = vi.fn()
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha} onPendingArtifactConsumed={onConsumed}
      pendingArtifact={{ type: 'doc', title: 'notes.md', path: 'out/notes.md', project_slug: 'alpha' }} />)
    await waitFor(() => expect(onConsumed).toHaveBeenCalled())
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:out/notes.md')
  })

  it('opens a pending task file in the ArtifactViewer', async () => {
    render(<FilesScreen token="t" projects={[alpha]} activeProject={alpha}
      pendingFile={{ slug: 'alpha', path: 'ops/report.md' }} />)
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:ops/report.md')
  })
})

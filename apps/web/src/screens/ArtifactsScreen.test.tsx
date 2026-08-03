import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { containerInspectionFs, projectFs } from '../api/fsAdapter'
import { listArchive, listArchiveBadges } from '../api/archive'
import { listArtifacts } from '../api/files'
import { ArtifactsScreen } from './ArtifactsScreen'
import type { Project } from '../types'

vi.mock('../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({
    list: vi.fn().mockResolvedValue({
      entries: [{ name: 'note.md', type: 'file', size: 12 }],
    }),
    read: vi.fn().mockResolvedValue({ content: '{}' }),
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
vi.mock('../api/files', () => ({
  listArtifacts: vi.fn(),
  previewUrl: (slug: string, path: string) => `/api/preview/${slug}/${path}`,
}))
vi.mock('../api/archive', () => ({
  listArchive: vi.fn(),
  listArchiveBadges: vi.fn(),
}))
// The real kind split decides which shelf an artifact lands on; only the
// picture itself (network + canvas) is stubbed.
vi.mock('../components/artifacts/ArtifactThumb', async importOriginal => ({
  ...(await importOriginal<typeof import('../components/artifacts/ArtifactThumb')>()),
  ArtifactThumb: ({ artifact }: { artifact: { type: string; path: string } }) => (
    <span data-testid="thumb">thumb:{artifact.type}:{artifact.path}</span>
  ),
}))
vi.mock('../components/artifacts/ArtifactViewer', () => ({
  ArtifactViewer: ({ slug, items, index }: { slug: string; items: { path: string }[]; index: number }) => (
    <div data-testid="viewer">viewer:{slug}:{items[index]?.path}:{items.length}</div>
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

const mixedArtifacts = {
  artifacts: [
    { type: 'design', title: 'Poster', path: 'artifacts/design/poster', id: 'poster' },
    { type: 'image', title: 'shot.png', path: 'artifacts/shot.png' },
    { type: 'video-file', title: 'cut.mp4', path: 'artifacts/cut.mp4' },
    { type: 'doc', title: 'plan.md', path: 'reports/plan.md' },
    { type: 'page', title: 'index.html', path: 'exports/index.html' },
  ],
}

describe('ArtifactsScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listArchiveBadges).mockResolvedValue({ items: [] })
    vi.mocked(listArchive).mockResolvedValue(emptyList)
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [] })
  })

  // ── The gallery: thumbnails for what you look at, a list for what you read ──

  it('renders designs, images, and videos as thumbnails and documents as a list', async () => {
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)

    const gallery = await screen.findByTestId('artifacts-gallery')
    expect(within(gallery).getByRole('button', { name: /Poster/ })).toBeInTheDocument()
    expect(within(gallery).getByRole('button', { name: /shot\.png/ })).toBeInTheDocument()
    expect(within(gallery).getByRole('button', { name: /cut\.mp4/ })).toBeInTheDocument()
    expect(within(gallery).getAllByTestId('thumb')).toHaveLength(3)

    const documents = screen.getByTestId('artifacts-documents')
    expect(within(documents).getByRole('button', { name: /plan\.md/ })).toBeInTheDocument()
    expect(within(documents).getByRole('button', { name: /index\.html/ })).toBeInTheDocument()
    expect(within(documents).queryAllByTestId('thumb')).toHaveLength(0)
  })

  it('teaches instead of showing an empty grid when a project produced nothing', async () => {
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    expect(await screen.findByTestId('teaching-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('artifacts-gallery')).not.toBeInTheDocument()
  })

  it('says the scan is capped instead of looking complete', async () => {
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: Array.from({ length: 40 }, (_, i) => ({ type: 'doc', title: `d${i}.md`, path: `reports/d${i}.md` })),
    } as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    expect(await screen.findByText(/most recent outputs/)).toBeInTheDocument()
  })

  it('stops loading when there is no Container to scan', async () => {
    render(<ArtifactsScreen token="t" projects={[]} globalScope />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled())
    expect(listArtifacts).not.toHaveBeenCalled()
  })

  it('opens a gallery item in the shared ArtifactViewer, walking its neighbours', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /shot\.png/ }))
    // Every viewer-eligible artifact of that Container travels with it.
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:artifacts/shot.png:5')
  })

  it('opens a design in the studio when Design Studio is available', async () => {
    const user = userEvent.setup()
    const onOpenDesign = vi.fn()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} designStudioEnabled onOpenDesign={onOpenDesign} />)
    await user.click(await screen.findByRole('button', { name: /Poster/ }))
    expect(onOpenDesign).toHaveBeenCalledWith('poster')
    expect(screen.queryByTestId('viewer')).not.toBeInTheDocument()
  })

  // ── The three tabs ──

  it('offers All, Deliverables, and History, and lands on the gallery', async () => {
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map(tab => tab.textContent)).toEqual(['All', 'Deliverables', 'History'])
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(listArtifacts).toHaveBeenCalledWith('t', 'alpha', expect.any(Number)))
    expect(screen.queryByTestId('deliverables-lens')).not.toBeInTheDocument()
  })

  it('shows the deliverable ledger under Deliverables, scoped to the active project', async () => {
    const user = userEvent.setup()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(screen.getByRole('tab', { name: 'Deliverables' }))
    expect(await screen.findByTestId('deliverables-lens')).toHaveTextContent('lens:alpha:present')
    expect(screen.queryByTestId('artifacts-gallery')).not.toBeInTheDocument()
  })

  it('narrows to gone-file records under History', async () => {
    const user = userEvent.setup()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(screen.getByRole('tab', { name: 'History' }))
    expect(await screen.findByTestId('deliverables-lens')).toHaveTextContent('lens:alpha:history')
  })

  it('keeps the Delegate ledger global until the head filter narrows it', async () => {
    const user = userEvent.setup()
    render(<ArtifactsScreen token="t" projects={[alpha, beta]} globalScope />)
    await user.click(screen.getByRole('tab', { name: 'Deliverables' }))
    expect(await screen.findByTestId('deliverables-lens')).toHaveTextContent('lens:all:present')
  })

  // ── Deliverable badges and the record panel (#139, kept) ──

  it('badges an agent-produced artifact and opens its record from the badge', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    vi.mocked(listArchiveBadges).mockResolvedValue({
      items: [{ id: 1, slug: 'shot-png-v1', name: 'shot.png', type: 'image', path: 'artifacts/shot.png', status: 'approved', version: 1, file_missing: false }],
    })
    const onOpenRecord = vi.fn()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} onOpenRecord={onOpenRecord} />)
    const badge = await screen.findByRole('button', { name: /Deliverable · Approved/ })
    expect(badge).toHaveAttribute('data-status', 'approved')
    await user.click(badge)
    expect(onOpenRecord).toHaveBeenCalledWith('alpha', 'shot-png-v1')
    // The badge is a shortcut to the record, not an artifact open.
    expect(screen.queryByTestId('viewer')).not.toBeInTheDocument()
  })

  it('renders the record panel for a record permanent address', async () => {
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} archiveRecord={{ project: 'alpha', slug: 'note-md-v1' }} />)
    expect(await screen.findByTestId('record-panel')).toHaveTextContent('record:alpha:note-md-v1')
    expect(screen.queryByTestId('artifacts-gallery')).not.toBeInTheDocument()
  })

  // ── Global scope ──

  it('gathers every Container in Delegate and narrows through the head filter', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockImplementation(async (_token, slug) => ({
      artifacts: [{ type: 'image', title: `${slug}.png`, path: `artifacts/${slug}.png` }],
    } as never))
    render(<ArtifactsScreen token="t" projects={[alpha, beta]} globalScope />)
    expect(await screen.findByRole('button', { name: /alpha\.png/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /beta\.png/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /All projects/ }))
    await user.click(await screen.findByRole('option', { name: 'Beta' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /alpha\.png/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /beta\.png/ })).toBeInTheDocument()
  })

  // ── Container-root inspection (Ops-migration recovery) ──

  it('renders the read-only Container tree for a migration reveal', async () => {
    render(
      <ArtifactsScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
      />,
    )
    await waitFor(() => expect(containerInspectionFs).toHaveBeenCalledWith('t', 'alpha'))
    expect(screen.getByTestId('artifacts-inspect')).toBeInTheDocument()
    expect(screen.queryByTestId('artifacts-gallery')).not.toBeInTheDocument()
  })

  it('never leaves a dead tab: picking one leaves the inspection', async () => {
    const user = userEvent.setup()
    const onRevealConsumed = vi.fn()
    render(
      <ArtifactsScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
        onRevealConsumed={onRevealConsumed}
      />,
    )
    await screen.findByTestId('artifacts-inspect')
    await user.click(screen.getByRole('tab', { name: 'Deliverables' }))
    expect(onRevealConsumed).toHaveBeenCalled()
    expect(await screen.findByTestId('deliverables-lens')).toBeInTheDocument()
  })

  it('returns to the gallery when the inspection is closed', async () => {
    const user = userEvent.setup()
    const onRevealConsumed = vi.fn()
    const { rerender } = render(
      <ArtifactsScreen
        token="t"
        projects={[alpha]}
        activeProject={alpha}
        revealPath={{ slug: 'alpha', path: 'wiki', pathKind: 'directory', rootSide: 'container' }}
        onRevealConsumed={onRevealConsumed}
      />,
    )
    await user.click(await screen.findByRole('button', { name: 'Close inspection' }))
    expect(onRevealConsumed).toHaveBeenCalled()
    rerender(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} revealPath={null} onRevealConsumed={onRevealConsumed} />)
    await waitFor(() => expect(screen.queryByTestId('artifacts-inspect')).not.toBeInTheDocument())
  })

  // ── Entry points from Chat and Tasks ──

  it('resolves a pending chat artifact to its registry record', async () => {
    vi.mocked(listArchive).mockResolvedValue({
      ...emptyList,
      items: [{ id: 1, slug: 'report-md-v1', project_slug: 'alpha', path: 'reports/report.md' } as never],
      total: 1,
    })
    const onOpenRecord = vi.fn()
    const onConsumed = vi.fn()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} onOpenRecord={onOpenRecord} onPendingArtifactConsumed={onConsumed}
      pendingArtifact={{ type: 'doc', title: 'report.md', path: 'reports/report.md', project_slug: 'alpha' }} />)
    await waitFor(() => expect(onOpenRecord).toHaveBeenCalledWith('alpha', 'report-md-v1'))
    expect(onConsumed).toHaveBeenCalled()
  })

  it('opens an unregistered pending artifact in the ArtifactViewer', async () => {
    const onConsumed = vi.fn()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} onPendingArtifactConsumed={onConsumed}
      pendingArtifact={{ type: 'doc', title: 'notes.md', path: 'out/notes.md', project_slug: 'alpha' }} />)
    await waitFor(() => expect(onConsumed).toHaveBeenCalled())
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:out/notes.md')
  })

  it('opens a pending task file in the ArtifactViewer', async () => {
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha}
      pendingFile={{ slug: 'alpha', path: 'ops/report.md' }} />)
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:ops/report.md')
  })
})

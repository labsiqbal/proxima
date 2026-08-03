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
  ArtifactViewer: ({ slug, items, index, backLabel, onClose, onEditSource }: {
    slug: string
    items: { path: string }[]
    index: number
    backLabel?: string
    onClose: () => void
    onEditSource?: (artifact: { path: string }) => void
  }) => (
    <div data-testid="viewer">viewer:{slug}:{items[index]?.path}:{items.length}
      <button type="button" onClick={onClose}>{`\u2190 ${backLabel}`}</button>
      {onEditSource && <button type="button" onClick={() => onEditSource(items[index])}>Edit source</button>}
    </div>
  ),
}))
vi.mock('../components/files/AppViewport', () => ({
  AppViewport: ({ slug, projectName, backLabel, onClose }: {
    slug: string
    projectName?: string
    backLabel?: string
    onClose: () => void
  }) => (
    <div data-testid="app-viewport">app:{slug}:{projectName}
      <button type="button" onClick={onClose}>{`\u2190 ${backLabel}`}</button>
    </div>
  ),
}))
vi.mock('../components/artifacts/DocumentEditor', () => ({
  DocumentEditor: ({ slug, path, target, backLabel, onClose }: {
    slug: string
    path: string
    target?: { path: string }
    backLabel?: string
    onClose: () => void
  }) => (
    <div data-testid="editor">editor:{slug}:{path}:{target ? 'targeted' : 'plain'}
      <button type="button" onClick={onClose}>{`\u2190 ${backLabel}`}</button>
    </div>
  ),
}))
vi.mock('../components/artifacts/DeliverablesLens', () => ({
  DeliverablesLens: ({ project, missingOnly }: { project?: string; missingOnly?: boolean }) => (
    <div data-testid="deliverables-lens">lens:{project || 'all'}:{missingOnly ? 'history' : 'present'}</div>
  ),
}))
vi.mock('../components/artifacts/ArchiveRecordPage', () => ({
  ArchiveRecordPage: ({ project, slug, onRevealInFiles, onOpenViewer }: {
    project: string
    slug: string
    onRevealInFiles?: (record: { project_slug: string; path: string }) => void
    onOpenViewer?: (record: { type: string; name: string; path: string; project_slug: string }) => void
  }) => (
    <div data-testid="record-panel">record:{project}:{slug}
      {onRevealInFiles && <button
        type="button"
        onClick={() => onRevealInFiles({ project_slug: project, path: 'reports/plan.md' })}
      >Reveal in Files</button>}
      {onOpenViewer && <>
        <button type="button" onClick={() => onOpenViewer({ type: 'doc', name: 'plan.md', path: 'reports/plan.md', project_slug: project })}>Open document</button>
        <button type="button" onClick={() => onOpenViewer({ type: 'image', name: 'shot.png', path: 'artifacts/shot.png', project_slug: project })}>Open image</button>
      </>}
    </div>
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
    // The walk is what you LOOK at: the four viewer-bound artifacts of that
    // Container. plan.md is not one of them - it opens in the editor (#146).
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:artifacts/shot.png:4')
  })

  // ── #146: opening lands in the MAIN WINDOW, and a document lands in the editor ──

  it('opens a document straight in the editor, with no popup in between', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /plan\.md/ }))

    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:reports/plan.md')
    expect(screen.queryByTestId('viewer')).not.toBeInTheDocument()
    // The main window IS the editor: no gallery underneath, nothing modal.
    expect(screen.queryByTestId('artifacts-gallery')).not.toBeInTheDocument()
    expect(document.querySelector('[aria-modal="true"]')).toBeNull()
  })

  it('gives the open surface a way back to the gallery', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)

    await user.click(await screen.findByRole('button', { name: /plan\.md/ }))
    await user.click(await screen.findByRole('button', { name: '← Gallery' }))
    expect(await screen.findByTestId('artifacts-gallery')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /shot\.png/ }))
    await user.click(await screen.findByRole('button', { name: '← Gallery' }))
    expect(await screen.findByTestId('artifacts-gallery')).toBeInTheDocument()
  })

  it('reaches the editor from a rendered artifact through Edit source, and back', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /index\.html/ }))
    await user.click(await screen.findByRole('button', { name: 'Edit source' }))
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:exports/index.html')

    // Entered from the artifact, so the way back returns to it - not past it.
    await user.click(screen.getByRole('button', { name: '← Artifact' }))
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:exports/index.html')
  })

  // The editor's "Review" action existed only to reach the viewer's feedback
  // pins, and went with them (#148). A document opens in its editor and stays
  // there; markdown still reads rendered through the editor's own Preview.
  it('gives a document no second surface to step through', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /plan\.md/ }))
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:reports/plan.md')
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument()
  })

  it('returns focus to the artifact that was opened', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    const row = await screen.findByRole('button', { name: /plan\.md/ })
    await user.click(row)
    await user.click(await screen.findByRole('button', { name: '← Gallery' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /plan\.md/ })).toHaveFocus())
  })

  it('closes an open artifact when the shell moves to another Container', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockImplementation(async (_token, slug) => ({
      artifacts: [{ type: 'doc', title: `${slug}.md`, path: `reports/${slug}.md` }],
    } as never))
    const view = render(<ArtifactsScreen token="t" projects={[alpha, beta]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /alpha\.md/ }))
    expect(await screen.findByTestId('editor')).toBeInTheDocument()

    view.rerender(<ArtifactsScreen token="t" projects={[alpha, beta]} activeProject={beta} />)
    await waitFor(() => expect(screen.queryByTestId('editor')).not.toBeInTheDocument())
    expect(await screen.findByRole('button', { name: /beta\.md/ })).toBeInTheDocument()
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

  it('sends "Reveal in Files" to the dock browser', async () => {
    const user = userEvent.setup()
    const revealed: unknown[] = []
    const listener = (event: Event) => revealed.push((event as CustomEvent).detail)
    window.addEventListener('proxima:reveal-file', listener)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} archiveRecord={{ project: 'alpha', slug: 'plan-md-v1' }} />)
    await user.click(await screen.findByRole('button', { name: 'Reveal in Files' }))
    window.removeEventListener('proxima:reveal-file', listener)
    expect(revealed).toEqual([{ path: 'reports/plan.md', pathKind: 'file', projectSlug: 'alpha' }])
  })

  it('hides "Reveal in Files" in Delegate, which has no dock to answer it', async () => {
    render(<ArtifactsScreen token="t" projects={[alpha]} globalScope archiveRecord={{ project: 'alpha', slug: 'plan-md-v1' }} />)
    await screen.findByTestId('record-panel')
    expect(screen.queryByRole('button', { name: 'Reveal in Files' })).not.toBeInTheDocument()
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

  // ── No trees here: browsing is a dock tool (#145) ──

  it('renders no file tree at all - the dock owns browsing', async () => {
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await screen.findByTestId('artifacts-gallery')
    expect(document.querySelector('.tree-scroll')).toBeNull()
    // Not even the Ops-migration Container inspection: the dock absorbed it,
    // so this destination never reaches for a filesystem adapter.
    expect(containerInspectionFs).not.toHaveBeenCalled()
    expect(projectFs).not.toHaveBeenCalled()
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

  it('opens an unregistered pending chat artifact in the same main window', async () => {
    const onConsumed = vi.fn()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} onPendingArtifactConsumed={onConsumed}
      pendingArtifact={{ type: 'doc', title: 'notes.md', path: 'out/notes.md', project_slug: 'alpha' }} />)
    await waitFor(() => expect(onConsumed).toHaveBeenCalled())
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:out/notes.md')
  })

  // The dock browser and a task's file links share one shell seam
  // (`App openFileInMainWindow`), so both land wherever this routes them.
  it('opens a handed-off document in the editor, keeping its Area-mapped target', async () => {
    const target = { project: 'alpha', area: { kind: 'ops' as const }, path: 'wiki/log.md' }
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha}
      pendingFile={{ slug: 'alpha', path: 'ops/wiki/log.md', target }} />)
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:ops/wiki/log.md:targeted')
  })

  it('opens a handed-off image in the inline viewer', async () => {
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha}
      pendingFile={{ slug: 'alpha', path: 'artifacts/shot.png' }} />)
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:artifacts/shot.png')
  })

  // ── The running app: the dock runs it, this main window shows it (#147) ──

  it('opens the app viewport in the main window when the run controls ask', async () => {
    const user = userEvent.setup()
    const onConsumed = vi.fn()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    const view = render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await screen.findByTestId('artifacts-gallery')

    view.rerender(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha}
      pendingApp={{ slug: 'alpha' }} onPendingAppConsumed={onConsumed} />)

    const viewport = await screen.findByTestId('app-viewport')
    expect(viewport).toHaveTextContent('app:alpha:Alpha')
    expect(onConsumed).toHaveBeenCalled()
    expect(screen.queryByTestId('artifacts-gallery')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '\u2190 Gallery' }))
    expect(await screen.findByTestId('artifacts-gallery')).toBeInTheDocument()
  })

  it('gives the app viewport the whole main window, replacing an open artifact', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    const view = render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} />)
    await user.click(await screen.findByRole('button', { name: /plan\.md/ }))
    expect(await screen.findByTestId('editor')).toBeInTheDocument()

    view.rerender(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} pendingApp={{ slug: 'alpha' }} />)
    expect(await screen.findByTestId('app-viewport')).toBeInTheDocument()
    expect(screen.queryByTestId('editor')).not.toBeInTheDocument()
  })

  it('closes the app viewport when the shell moves to another Container', async () => {
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [] })
    const view = render(<ArtifactsScreen token="t" projects={[alpha, beta]} activeProject={alpha} pendingApp={{ slug: 'alpha' }} />)
    expect(await screen.findByTestId('app-viewport')).toBeInTheDocument()

    view.rerender(<ArtifactsScreen token="t" projects={[alpha, beta]} activeProject={beta} />)
    await waitFor(() => expect(screen.queryByTestId('app-viewport')).not.toBeInTheDocument())
  })

  // Running an app is Work-only: it is owner-power execution driven from the
  // dock, and Delegate has no dock. Delegate must not grow a half of it.
  it('never opens an app viewport in Delegate', async () => {
    const onConsumed = vi.fn()
    render(<ArtifactsScreen token="t" projects={[alpha]} globalScope
      pendingApp={{ slug: 'alpha' }} onPendingAppConsumed={onConsumed} />)
    await waitFor(() => expect(onConsumed).toHaveBeenCalled())
    expect(screen.queryByTestId('app-viewport')).not.toBeInTheDocument()
  })

  // ── The record panel opens the same way, and returns to the record ──

  it('opens a record document in the editor and returns to the record', async () => {
    const user = userEvent.setup()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} archiveRecord={{ project: 'alpha', slug: 'plan-md-v1' }} />)
    await user.click(await screen.findByRole('button', { name: 'Open document' }))
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:reports/plan.md')

    await user.click(screen.getByRole('button', { name: '← Record' }))
    expect(await screen.findByTestId('record-panel')).toBeInTheDocument()
  })

  it('opens a record image in the inline viewer, not over the record', async () => {
    const user = userEvent.setup()
    render(<ArtifactsScreen token="t" projects={[alpha]} activeProject={alpha} archiveRecord={{ project: 'alpha', slug: 'shot-png-v1' }} />)
    await user.click(await screen.findByRole('button', { name: 'Open image' }))
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:artifacts/shot.png')
    expect(screen.queryByTestId('record-panel')).not.toBeInTheDocument()
  })

  // Delegate has no dock and no Design Studio, but it has this destination, and
  // an artifact opened there behaves exactly as it does in Work.
  it('opens documents in the editor in Delegate too', async () => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} globalScope />)
    await user.click(await screen.findByRole('button', { name: /plan\.md/ }))
    expect(await screen.findByTestId('editor')).toHaveTextContent('editor:alpha:reports/plan.md')
  })

  // Delegate has no Design Studio, so a design there is not a detour into the
  // studio - it opens on the viewer's stage like any other picture. The whole
  // gallery must click through, not just the documents (#151).
  it.each([
    ['image', /shot\.png/, 'viewer:alpha:artifacts/shot.png'],
    ['design', /Poster/, 'viewer:alpha:artifacts/design/poster'],
    ['page', /index\.html/, 'viewer:alpha:exports/index.html'],
  ])('opens a gallery %s in the viewer in Delegate', async (_kind, name, expected) => {
    const user = userEvent.setup()
    vi.mocked(listArtifacts).mockResolvedValue(mixedArtifacts as never)
    render(<ArtifactsScreen token="t" projects={[alpha]} globalScope />)
    await user.click(await screen.findByRole('button', { name }))
    expect(await screen.findByTestId('viewer')).toHaveTextContent(expected)
  })

  // A record reached in Delegate opens its file the same way Work does; only
  // the dock-bound "Reveal in Files" is missing there, because there is no dock.
  it('opens a record artifact from the record panel in Delegate', async () => {
    const user = userEvent.setup()
    render(<ArtifactsScreen token="t" projects={[alpha]} globalScope
      archiveRecord={{ project: 'alpha', slug: 'shot-png-v1' }} />)
    await user.click(await screen.findByRole('button', { name: 'Open image' }))
    expect(await screen.findByTestId('viewer')).toHaveTextContent('viewer:alpha:artifacts/shot.png')
  })
})

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ArtifactsScreen } from './ArtifactsScreen'
import { listArchive, setArchiveStatus, getArchiveRecord, type ArchiveRecord, type ArchiveRecordDetail } from '../api/archive'
import type { Project } from '../types'

vi.mock('../api/archive', () => ({
  listArchive: vi.fn(),
  setArchiveStatus: vi.fn(),
  getArchiveRecord: vi.fn(),
}))
vi.mock('../api/files', () => ({
  previewUrl: vi.fn(() => 'http://preview/x'),
  fetchRawBlob: vi.fn(() => Promise.resolve('blob:x')),
}))
vi.mock('../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({ read: vi.fn(() => Promise.resolve({ content: '# hi' })) })),
}))
vi.mock('../components/files/AppRunner', () => ({ AppRunner: () => null }))
vi.mock('../components/artifacts/ArtifactViewer', () => ({ ArtifactViewer: () => <div data-testid="viewer" /> }))
vi.mock('../components/chat/MessageContent', () => ({ MessageContent: ({ content }: { content: string }) => <div>{content}</div> }))

const projects: Project[] = [
  { slug: 'wingoh', name: 'wingoh', path: '/w' } as Project,
  { slug: 'vvip', name: 'vvip', path: '/v' } as Project,
]

const rec = (over: Partial<ArchiveRecord> = {}): ArchiveRecord => ({
  id: 1,
  slug: 'report-md-v1',
  name: 'report.md',
  type: 'doc',
  path: 'reports/report.md',
  area: 'reports/',
  size: 1024,
  status: 'draft',
  approved_at: null,
  version: 1,
  superseded_by: null,
  session_id: 7,
  job_id: 9,
  node_id: null,
  run_id: 11,
  file_missing: false,
  produced_at: '2026-07-20T09:00:00+00:00',
  project_id: 1,
  project_slug: 'wingoh',
  project_name: 'wingoh',
  session_title: 'Growth chat',
  job_title: 'Draft Q3 article',
  job_engine: 'linear',
  ...over,
})

const listResponse = (items: ArchiveRecord[], total = items.length) => ({
  items,
  total,
  limit: 50,
  offset: 0,
  counts: {
    by_type: items.reduce<Record<string, number>>((a, r) => ({ ...a, [r.type]: (a[r.type] || 0) + 1 }), {}),
    by_status: items.reduce<Record<string, number>>((a, r) => ({ ...a, [r.status]: (a[r.status] || 0) + 1 }), {}),
  },
})

const base = {
  token: 't',
  projects,
  activeProject: projects[0],
}

beforeEach(() => {
  vi.mocked(listArchive).mockReset()
  vi.mocked(setArchiveStatus).mockReset()
  vi.mocked(getArchiveRecord).mockReset()
})

describe('ArtifactsScreen (Archive registry)', () => {
  it('renders registry records with status, lineage, and facet counts', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([
      rec(),
      rec({ id: 2, slug: 'shot-png-v1', name: 'shot.png', type: 'image', path: 'artifacts/shot.png', status: 'approved' }),
    ]))
    render(<ArtifactsScreen {...base} />)
    expect(await screen.findByText('report.md')).toBeInTheDocument()
    expect(screen.getByText('shot.png')).toBeInTheDocument()
    // Status pills on the rows (the facet chips also say Draft/Approved).
    expect(document.querySelector('.archive-pill.draft')).toHaveTextContent('Draft')
    expect(document.querySelector('.archive-pill.approved')).toHaveTextContent('Approved')
    // Lineage column names the producing task (once per row).
    expect(screen.getAllByText('Draft Q3 article')).toHaveLength(2)
    // Footer shows the registry total, not a cap.
    expect(screen.getByText(/Showing 2 of 2 records/)).toBeInTheDocument()
  })

  it('expands a row in place with preview, lineage, and the full-record door', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec()]))
    const onOpenRecord = vi.fn()
    render(<ArtifactsScreen {...base} onOpenRecord={onOpenRecord} />)
    await userEvent.click(await screen.findByText('report.md'))
    expect(screen.getByText('Open full record →')).toBeInTheDocument()
    expect(screen.getByText('#archive/wingoh/report-md-v1')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Open full record →'))
    expect(onOpenRecord).toHaveBeenCalledWith('wingoh', 'report-md-v1')
  })

  it('approves from the expanded row through the one shared status field', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec()]))
    vi.mocked(setArchiveStatus).mockResolvedValue(rec({ status: 'approved', approved_at: '2026-07-21T10:00:00+00:00' }))
    render(<ArtifactsScreen {...base} />)
    await userEvent.click(await screen.findByText('report.md'))
    await userEvent.click(screen.getByRole('button', { name: '✓ Approve' }))
    expect(setArchiveStatus).toHaveBeenCalledWith('t', 1, 'approved')
    await waitFor(() => expect(document.querySelector('.archive-pill.approved')).toBeInTheDocument())
  })

  it('filters by type through the registry query', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec(), rec({ id: 2, type: 'image', slug: 'i-v1', name: 'i.png', path: 'a/i.png' })]))
    render(<ArtifactsScreen {...base} />)
    await screen.findByText('report.md')
    await userEvent.click(screen.getByRole('button', { name: /^Image/ }))
    await waitFor(() => expect(listArchive).toHaveBeenLastCalledWith('t', expect.objectContaining({ type: 'image' })))
  })

  it('marks a missing file on its durable record instead of dropping it', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec({ file_missing: true })]))
    render(<ArtifactsScreen {...base} />)
    expect(await screen.findByText(/file gone/)).toBeInTheDocument()
  })

  it('renders the full record page for a permalink with versions and lineage', async () => {
    const detail: ArchiveRecordDetail = {
      ...rec({ status: 'approved', approved_at: '2026-07-21T10:00:00+00:00' }),
      versions: [
        { id: 1, slug: 'report-md-v1', version: 1, status: 'approved', produced_at: '2026-07-20T09:00:00+00:00', approved_at: '2026-07-21T10:00:00+00:00', superseded_by: null },
      ],
      prev_slug: null,
      next_slug: 'older-v1',
      superseded_by_slug: null,
    }
    vi.mocked(listArchive).mockResolvedValue(listResponse([]))
    vi.mocked(getArchiveRecord).mockResolvedValue(detail)
    const onCloseRecord = vi.fn()
    const onOpenTask = vi.fn()
    render(<ArtifactsScreen {...base} archiveRecord={{ project: 'wingoh', slug: 'report-md-v1' }} onCloseRecord={onCloseRecord} onOpenTask={onOpenTask} />)
    expect(await screen.findByText('#archive/wingoh/report-md-v1')).toBeInTheDocument()
    expect(screen.getByText('Permanent address')).toBeInTheDocument()
    expect(document.querySelector('.archive-version-row')).toHaveTextContent('v1')
    // Lineage steps navigate to the producing task.
    await userEvent.click(screen.getByText('Draft Q3 article'))
    expect(onOpenTask).toHaveBeenCalledWith(9, 'linear')
    // Breadcrumb goes back to the list.
    await userEvent.click(screen.getByRole('button', { name: 'Archive' }))
    expect(onCloseRecord).toHaveBeenCalled()
  })

  it('resolves a chat result card to its registry record', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec()]))
    const onOpenRecord = vi.fn()
    const onConsumed = vi.fn()
    render(<ArtifactsScreen {...base} onOpenRecord={onOpenRecord} onPendingArtifactConsumed={onConsumed}
      pendingArtifact={{ type: 'doc', title: 'report.md', path: 'reports/report.md', project_slug: 'wingoh' }} />)
    await waitFor(() => expect(onOpenRecord).toHaveBeenCalledWith('wingoh', 'report-md-v1'))
    expect(onConsumed).toHaveBeenCalled()
  })
})

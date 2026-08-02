import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { DeliverablesLens } from './DeliverablesLens'
import { listArchive, setArchiveStatus, type ArchiveRecord } from '../../api/archive'

vi.mock('../../api/archive', () => ({
  listArchive: vi.fn(),
  setArchiveStatus: vi.fn(),
}))
vi.mock('../../api/files', () => ({
  previewUrl: vi.fn(() => 'http://preview/x'),
  fetchRawBlob: vi.fn(() => Promise.resolve('blob:x')),
  fileUrl: vi.fn((slug: string, path: string) => `http://file/${slug}/${path}`),
  retargetFile: vi.fn(),
}))
const fsRead = vi.fn(() => Promise.resolve({ content: '# hi' }))
vi.mock('../../api/fsAdapter', () => ({
  projectFs: vi.fn(() => ({ read: (...args: unknown[]) => fsRead(...args) })),
}))
vi.mock('../design/MiniPreview', () => ({
  MiniPreview: ({ art }: { art?: { width: number; height: number } }) =>
    art ? <div data-testid="design-mini-preview">{art.width}×{art.height}</div> : null,
}))
vi.mock('../chat/MessageContent', () => ({
  MessageContent: ({ content, sourcePath, fileTarget }: { content: string; sourcePath?: string; fileTarget?: unknown }) =>
    <div data-testid="archive-markdown" data-source-path={sourcePath} data-file-target={JSON.stringify(fileTarget)}>{content}</div>,
}))

const rec = (over: Partial<ArchiveRecord> = {}): ArchiveRecord => ({
  id: 1,
  slug: 'report-md-v1',
  name: 'report.md',
  type: 'doc',
  path: 'ops/reports/report.md',
  area: 'ops/reports/',
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
  target: null,
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

beforeEach(() => {
  vi.mocked(listArchive).mockReset()
  vi.mocked(setArchiveStatus).mockReset()
  fsRead.mockReset()
  fsRead.mockResolvedValue({ content: '# hi' })
})

describe('DeliverablesLens (the record ledger on Files, #139)', () => {
  it('renders registry records with status, lineage, and facet counts', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([
      rec(),
      rec({ id: 2, slug: 'shot-png-v1', name: 'shot.png', type: 'image', path: 'ops/artifacts/shot.png', status: 'approved' }),
    ]))
    render(<DeliverablesLens token="t" project="wingoh" />)
    expect(await screen.findByText('report.md')).toBeInTheDocument()
    expect(screen.getByText('shot.png')).toBeInTheDocument()
    expect(document.querySelector('.archive-pill.draft')).toHaveTextContent('Draft')
    expect(document.querySelector('.archive-pill.approved')).toHaveTextContent('Approved')
    expect(screen.getAllByText('Draft Q3 article')).toHaveLength(2)
    expect(screen.getByText(/Showing 2 of 2 records/)).toBeInTheDocument()
    // Scoped to the project it was given.
    expect(listArchive).toHaveBeenLastCalledWith('t', expect.objectContaining({ project: 'wingoh' }))
  })

  it('queries only gone-file records when acting as the history filter', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec({ file_missing: true })]))
    render(<DeliverablesLens token="t" project="wingoh" missingOnly />)
    expect(await screen.findByText(/file gone/)).toBeInTheDocument()
    expect(listArchive).toHaveBeenLastCalledWith('t', expect.objectContaining({ missing: 1 }))
  })

  it('expands a row in place with preview, lineage, and the full-record door', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec()]))
    const onOpenRecord = vi.fn()
    render(<DeliverablesLens token="t" project="wingoh" onOpenRecord={onOpenRecord} />)
    await userEvent.click(await screen.findByText('report.md'))
    expect(screen.getByText('Open full record →')).toBeInTheDocument()
    expect(screen.getByText('#archive/wingoh/report-md-v1')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Open full record →'))
    expect(onOpenRecord).toHaveBeenCalledWith('wingoh', 'report-md-v1')
  })

  it('approves from the expanded row through the one shared status field', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec()]))
    vi.mocked(setArchiveStatus).mockResolvedValue(rec({ status: 'approved', approved_at: '2026-07-21T10:00:00+00:00' }))
    const onRecordsChanged = vi.fn()
    render(<DeliverablesLens token="t" project="wingoh" onRecordsChanged={onRecordsChanged} />)
    await userEvent.click(await screen.findByText('report.md'))
    await userEvent.click(screen.getByRole('button', { name: '✓ Approve' }))
    expect(setArchiveStatus).toHaveBeenCalledWith('t', 1, 'approved')
    await waitFor(() => expect(document.querySelector('.archive-pill.approved')).toBeInTheDocument())
    expect(onRecordsChanged).toHaveBeenCalled()
  })

  it('filters by type through the registry query', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec(), rec({ id: 2, type: 'image', slug: 'i-v1', name: 'i.png', path: 'a/i.png' })]))
    render(<DeliverablesLens token="t" project="wingoh" />)
    await screen.findByText('report.md')
    await userEvent.click(screen.getByRole('button', { name: /^Image/ }))
    await waitFor(() => expect(listArchive).toHaveBeenLastCalledWith('t', expect.objectContaining({ type: 'image' })))
  })

  it('uses the record target when previewing a direct Ops-root Markdown file', async () => {
    const target = {
      project: 'wingoh',
      area: { kind: 'ops', id: 8 },
      path: 'report.md',
    }
    vi.mocked(listArchive).mockResolvedValue(listResponse([
      rec({ path: 'ops/report.md', target } as Partial<ArchiveRecord>),
    ]))
    render(<DeliverablesLens token="t" project="wingoh" />)

    await userEvent.click(await screen.findByText('report.md'))
    await waitFor(() => expect(fsRead).toHaveBeenCalledWith(target))
    const markdown = await screen.findByTestId('archive-markdown')
    expect(markdown).toHaveAttribute('data-source-path', 'report.md')
    expect(JSON.parse(markdown.getAttribute('data-file-target') || '{}')).toEqual(target)
  })

  it('opens records through the caller-provided viewer door', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec()]))
    const onOpenViewer = vi.fn()
    render(<DeliverablesLens token="t" project="wingoh" onOpenViewer={onOpenViewer} />)
    await userEvent.click(await screen.findByText('report.md'))
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(onOpenViewer).toHaveBeenCalledWith(expect.objectContaining({ slug: 'report-md-v1' }))
  })

  it('previews a design record from scene.json instead of the open-only placeholder', async () => {
    fsRead.mockResolvedValue({
      content: JSON.stringify({
        id: 'deck',
        type: 'deck',
        title: 'Sales Deck',
        artboards: [{ id: 'a1', width: 1920, height: 1080, background: '#0b1220', layers: [] }],
      }),
    })
    vi.mocked(listArchive).mockResolvedValue(listResponse([
      rec({
        id: 9,
        slug: 'sales-deck-v1',
        name: 'Sales Deck',
        type: 'design',
        path: 'ops/artifacts/design/sales-deck',
        size: null,
      }),
    ]))
    render(<DeliverablesLens token="t" project="wingoh" />)
    await userEvent.click(await screen.findByText('Sales Deck'))
    expect(fsRead).toHaveBeenCalledWith('ops/artifacts/design/sales-deck/scene.json')
    expect(await screen.findByTestId('design-mini-preview')).toHaveTextContent('1920×1080')
    expect(screen.queryByText(/use Open to view it/)).not.toBeInTheDocument()
  })

  it('marks a missing file on its durable record instead of dropping it', async () => {
    vi.mocked(listArchive).mockResolvedValue(listResponse([rec({ file_missing: true })]))
    render(<DeliverablesLens token="t" project="wingoh" />)
    expect(await screen.findByText(/file gone/)).toBeInTheDocument()
  })
})

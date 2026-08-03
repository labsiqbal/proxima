import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ArchiveRecordPage } from './ArchiveRecordPage'
import { getArchiveRecord, type ArchiveRecordDetail } from '../../api/archive'

vi.mock('../../api/archive', () => ({
  getArchiveRecord: vi.fn(),
  setArchiveStatus: vi.fn(),
  STATUS_LABELS: undefined,
}))
vi.mock('./archive', async () => {
  const actual = await vi.importActual<typeof import('./archive')>('./archive')
  return { ...actual, RecordPreview: () => <div data-testid="record-preview" /> }
})
vi.mock('../files/AppRunner', () => ({ AppRunner: () => <div data-testid="app-runner" /> }))

const detail = (over: Partial<ArchiveRecordDetail> = {}): ArchiveRecordDetail => ({
  id: 3,
  slug: 'poster-v1',
  name: 'Poster',
  type: 'design',
  path: 'artifacts/design/poster',
  area: 'artifacts/design/',
  size: null,
  status: 'draft',
  approved_at: null,
  version: 1,
  superseded_by: null,
  session_id: null,
  job_id: null,
  node_id: null,
  run_id: null,
  file_missing: false,
  produced_at: '2026-07-20T09:00:00+00:00',
  project_id: 1,
  project_slug: 'alpha',
  project_name: 'Alpha',
  target: null,
  session_title: null,
  job_title: null,
  job_engine: null,
  versions: [],
  prev_slug: null,
  next_slug: null,
  superseded_by_slug: null,
  ...over,
} as ArchiveRecordDetail)

describe('ArchiveRecordPage design action', () => {
  beforeEach(() => {
    vi.mocked(getArchiveRecord).mockResolvedValue(detail())
  })

  it('offers the studio for a design where the studio exists', async () => {
    const user = userEvent.setup()
    const onOpenDesign = vi.fn()
    render(<ArchiveRecordPage token="t" project="alpha" slug="poster-v1" onOpenRecord={vi.fn()} onOpenDesign={onOpenDesign} onOpenViewer={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Open in Design' }))
    expect(onOpenDesign).toHaveBeenCalledWith('poster')
  })

  // Delegate has no Design Studio, so the same button opens the viewer instead
  // (#146). It must then say what it does: a control promising a studio that is
  // not in this mode reads as a click-through that went somewhere else (#151).
  it('says plain "Open" where there is no studio, and opens the viewer', async () => {
    const user = userEvent.setup()
    const onOpenViewer = vi.fn()
    render(<ArchiveRecordPage token="t" project="alpha" slug="poster-v1" onOpenRecord={vi.fn()} onOpenViewer={onOpenViewer} />)
    expect(await screen.findByRole('button', { name: 'Open' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open in Design' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open' }))
    expect(onOpenViewer).toHaveBeenCalledWith(expect.objectContaining({ slug: 'poster-v1' }))
  })
})

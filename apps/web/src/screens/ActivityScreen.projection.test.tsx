import '@testing-library/jest-dom/vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listJobs } from '../api/jobs'
import { listGraphJobs } from '../api/graph'
import { FRESH_FAILED_REVIEW_RUN } from '../testFixtures/failedReviewRun'
import { ActivityScreen } from './ActivityScreen'

vi.mock('../api/jobs', () => ({ listJobs: vi.fn() }))
vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn(),
  approveGraphJob: vi.fn(),
  saveGraphTemplate: vi.fn(),
}))
vi.mock('../hooks/usePolling', () => ({ usePolling: vi.fn() }))

describe('ActivityScreen authoritative run projection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-07-31T05:00:30Z'))
    vi.mocked(listJobs).mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    })
    vi.mocked(listGraphJobs).mockResolvedValue({
      items: [FRESH_FAILED_REVIEW_RUN],
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows the same failed status and fresh start while expanded', async () => {
    const user = userEvent.setup()
    render(<ActivityScreen
      token="token"
      activeProject={null}
      features={{ designStudio: false, workflowGraph: true, masterOrchestrator: false }}
      profiles={[]}
      onOpenTask={vi.fn()}
      onOpenPlan={vi.fn()}
    />)

    const row = await screen.findByRole('button', {
      name: /Launch readiness review · Plan · failed/,
    })
    expect(within(row).getByText('failed')).toHaveClass('failed')
    expect(row).toHaveAccessibleName(/Just now/)

    await user.click(row)
    const expanded = screen.getByText('Gather launch evidence').closest('li')
    expect(expanded).not.toBeNull()
    expect(within(expanded as HTMLElement).getByText('failed')).toHaveClass('failed')
  })
})

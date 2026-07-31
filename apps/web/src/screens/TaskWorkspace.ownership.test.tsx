import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getJob } from '../api/jobs'
import { TaskWorkspace } from './TaskWorkspace'

vi.mock('../api/jobs', () => ({
  getJob: vi.fn(),
  approveJob: vi.fn(),
  deleteJob: vi.fn(),
}))
vi.mock('../components/tasks/ChangesReview', () => ({ ChangesReview: () => null }))
vi.mock('../components/tasks/SatpamCard', () => ({ SatpamCard: () => null }))

describe('TaskWorkspace ownership context', () => {
  beforeEach(() => {
    vi.mocked(getJob).mockResolvedValue({
      id: 7,
      project_id: 22,
      project_slug: 'beacon',
      workflow_id: null,
      session_id: 9,
      title: 'Approve release checklist',
      status: 'review',
      engine: 'linear',
      current_step_idx: 0,
      input: { brief: 'Review the release evidence.' },
      steps_state: [{
        id: 'review',
        name: 'Review',
        instruction: 'Review the evidence.',
        expected_output: 'Approved evidence.',
        review_required: false,
        status: 'done',
        run_id: null,
        output_summary: 'Ready.',
        started_at: null,
        finished_at: null,
        error: null,
        produced_designs: [{ id: 'poster-b', title: 'Beacon poster' }],
      }],
      schedule_id: null,
      created_by: 1,
      created_at: '2026-07-31 00:00:00',
      updated_at: '2026-07-31 00:00:00',
      started_at: null,
      finished_at: null,
      archived_at: null,
    })
  })

  it('prominently locks the owning Project while preserving the Work selection', async () => {
    render(
      <TaskWorkspace
        {...({
          token: 'token',
          jobId: 7,
          onBack: vi.fn(),
          owningProject: {
            id: 22,
            slug: 'beacon',
            name: 'Beacon release',
            identity_label: 'General',
          },
          selectedWorkProject: { slug: 'atlas', name: 'Atlas private ops' },
          owningAreaLabel: 'Operations',
        } as never)}
      />,
    )

    const context = await screen.findByRole('region', { name: 'Task Project' })
    expect(context).toHaveTextContent('Beacon release')
    expect(context).toHaveTextContent('Identity: General')
    expect(context).toHaveTextContent('Area: Operations')
    expect(context).toHaveTextContent('Work remains Atlas private ops')
    expect(screen.getByText('Project locked to this Task')).toBeInTheDocument()
  })

  it('opens Task-linked Design through the owning Project while Work stays selected', async () => {
    const onOpenDesign = vi.fn()
    render(
      <TaskWorkspace
        {...({
          token: 'token',
          jobId: 7,
          onBack: vi.fn(),
          designStudioEnabled: true,
          onOpenDesign,
          owningProject: {
            id: 22,
            slug: 'beacon',
            name: 'Beacon release',
            identity_label: 'General',
          },
          selectedWorkProject: { slug: 'atlas', name: 'Atlas private ops' },
          owningAreaLabel: 'Operations',
        } as never)}
      />,
    )

    const openDesign = await screen.findByRole('button', { name: /Beacon poster/i })
    fireEvent.click(openDesign)

    expect(onOpenDesign).toHaveBeenCalledWith('poster-b', 'beacon')
    expect(screen.getByRole('region', { name: 'Task Project' }))
      .toHaveTextContent('Work remains Atlas private ops')
  })
})

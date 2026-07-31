import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { approveJob, getJob } from '../api/jobs'
import { TaskWorkspace } from './TaskWorkspace'

vi.mock('../api/jobs', () => ({
  getJob: vi.fn(),
  approveJob: vi.fn(),
  deleteJob: vi.fn(),
}))
vi.mock('../components/tasks/ChangesReview', () => ({ ChangesReview: () => null }))
vi.mock('../components/tasks/SatpamCard', () => ({ SatpamCard: () => null }))

const ownershipJob = {
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
}

describe('TaskWorkspace ownership context', () => {
  beforeEach(() => {
    vi.mocked(getJob).mockResolvedValue(ownershipJob as never)
    vi.mocked(approveJob).mockResolvedValue({
      ...ownershipJob,
      status: 'running',
      current_step_idx: 1,
    } as never)
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

  it('keeps seeded and edited review output through ownership resolution', async () => {
    const midGateJob = {
      ...ownershipJob,
      steps_state: [
        ownershipJob.steps_state[0],
        {
          id: 'ship',
          name: 'Ship',
          instruction: 'Ship the release.',
          expected_output: 'Shipped.',
          review_required: false,
          status: 'queued',
          run_id: null,
          output_summary: '',
          started_at: null,
          finished_at: null,
          error: null,
        },
      ],
    }
    vi.mocked(getJob).mockResolvedValue(midGateJob as never)
    vi.mocked(approveJob).mockResolvedValue({
      ...midGateJob,
      status: 'running',
      current_step_idx: 1,
    } as never)

    const onResolved = vi.fn()
    const user = userEvent.setup()
    const props = {
      token: 'token',
      jobId: 7,
      onBack: vi.fn(),
      onResolved,
      owningProject: {
        id: 22,
        slug: 'beacon',
        name: 'Beacon release',
        identity_label: 'General',
      },
      selectedWorkProject: { slug: 'atlas', name: 'Atlas private ops' },
      owningAreaLabel: 'Operations',
      initialJob: null as typeof midGateJob | null,
    }

    const { rerender } = render(<TaskWorkspace {...(props as never)} />)

    const textarea = await screen.findByRole('textbox')
    await waitFor(() => expect(textarea).toHaveValue('Ready.'))
    await waitFor(() => expect(onResolved).toHaveBeenCalledWith(midGateJob))

    rerender(<TaskWorkspace {...({ ...props, initialJob: midGateJob } as never)} />)
    expect(screen.getByRole('textbox')).toHaveValue('Ready.')
    expect(screen.getByRole('region', { name: 'Task Project' }))
      .toHaveTextContent('Work remains Atlas private ops')

    await user.clear(screen.getByRole('textbox'))
    await user.type(screen.getByRole('textbox'), 'Edited evidence')
    rerender(<TaskWorkspace {...({ ...props, initialJob: { ...midGateJob } } as never)} />)
    expect(screen.getByRole('textbox')).toHaveValue('Edited evidence')

    await user.click(screen.getByRole('button', { name: /Approve & continue/i }))
    await waitFor(() =>
      expect(approveJob).toHaveBeenCalledWith('token', 7, {
        edited_output: 'Edited evidence',
      }),
    )
  })
})

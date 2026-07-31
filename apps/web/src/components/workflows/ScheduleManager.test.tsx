import '@testing-library/jest-dom/vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ScheduleManager, isValidCron } from './ScheduleManager'
import { createSchedule, listSchedules, runScheduleNow, updateSchedule } from '../../api/schedules'

vi.mock('../../api/schedules', () => ({
  listSchedules: vi.fn(),
  createSchedule: vi.fn(),
  updateSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
  runScheduleNow: vi.fn(),
}))
vi.mock('../ui/Dialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
const workflow = { id: 7, project_id: 1, name: 'Release', description: '', category: '', status: 'active' as const, inputs: [], steps: [], created_by: 1, created_at: '', updated_at: '' }
const declaredWorkflow = { ...workflow, inputs: [{ id: 'topic', label: 'Topic', kind: 'text' as const, required: true }, { id: 'source_url', label: 'Source URL', kind: 'url' as const, required: false }] }

describe('ScheduleManager', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(listSchedules).mockResolvedValue([]); vi.mocked(createSchedule).mockResolvedValue({} as never) })
  it('validates the supported cron grammar and bounds', () => {
    expect(isValidCron('0 9 * * 1')).toBe(true)
    expect(isValidCron('*/15 0-23 1,15 * 0-7')).toBe(true)
    for (const cron of ['0 9 * *', '*/0 * * * *', '60 * * * *', '0 24 * * *', '0 9 0 * *', '0 9 * 13 *', '0 9 * * 8', '0 9 * * MON', '0 9 * * 5-1', '0 9 * * 1,,2']) expect(isValidCron(cron)).toBe(false)
  })
  it('refuses enablement until required manual input has an automation source', async () => {
    const user = userEvent.setup()
    render(<ScheduleManager token="token" workflows={[declaredWorkflow]} workflowId={7} defaultTimezone="UTC" />)
    await screen.findByText('No schedules yet.')
    await user.click(screen.getByLabelText('Enabled'))
    await user.click(screen.getByRole('button', { name: 'Add schedule' }))

    expect(createSchedule).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/save a durable binding for: Topic/i)
    expect(screen.getByLabelText('Topic automation binding')).toBeInTheDocument()
  })

  it('creates a schedule off by default so bindings can be filled before enablement', async () => {
    const user = userEvent.setup()
    render(<ScheduleManager token="token" workflows={[declaredWorkflow]} workflowId={7} defaultTimezone="UTC" />)
    await screen.findByText('No schedules yet.')
    await user.click(screen.getByRole('button', { name: 'Add schedule' }))

    expect(createSchedule).toHaveBeenCalledWith('token', {
      workflow_id: 7,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: {},
      overlap_policy: 'skip',
      enabled: false,
    })
  })

  it('creates a schedule with durable bindings and an explicit timezone', async () => {
    const user = userEvent.setup()
    render(<ScheduleManager token="token" workflows={[declaredWorkflow]} workflowId={7} defaultTimezone="UTC" />)
    await screen.findByText('No schedules yet.')
    await user.type(screen.getByLabelText('Topic automation binding'), 'Weekly launch')
    await user.click(screen.getByRole('button', { name: 'Add schedule' }))

    expect(createSchedule).toHaveBeenCalledWith('token', {
      workflow_id: 7,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: { topic: 'Weekly launch' },
      overlap_policy: 'skip',
      enabled: false,
    })
  })

  it('creates an existing workflow schedule through the schedules API', async () => {
    const user = userEvent.setup()
    render(<ScheduleManager token="token" workflows={[workflow]} workflowId={7} />)
    await screen.findByText('No schedules yet.')
    await user.click(screen.getByRole('button', { name: 'Add schedule' }))
    expect(createSchedule).toHaveBeenCalledWith('token', expect.objectContaining({
      workflow_id: 7,
      cron: '0 9 * * *',
      timezone: expect.any(String),
      overlap_policy: 'skip',
      enabled: false,
    }))
  })

  it('reloads timezone and readiness without duplicating preset cron copy', async () => {
    vi.mocked(listSchedules).mockResolvedValue([{
      id: 3,
      workflow_id: 7,
      project_id: 1,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: {},
      input: {},
      overlap_policy: 'skip',
      enabled: false,
      ready: false,
      unresolved_inputs: ['topic'],
      last_run_minute: null,
      last_tick_at: null,
      created_by: 1,
      created_at: '',
      updated_at: '',
    }])

    render(<ScheduleManager token="token" workflows={[declaredWorkflow]} workflowId={7} defaultTimezone="UTC" />)

    const scheduleName = await screen.findByText('Release')
    const row = scheduleName.closest('article')
    expect(row).not.toBeNull()
    expect(row).toHaveTextContent('Every day at 9am · UTC · skip overlap')
    expect(row).not.toHaveTextContent('Every day at 9am · 0 9 * * *')
    expect(row).toHaveTextContent('Needs binding: Topic')
    expect(screen.getByRole('checkbox', { name: 'Schedule off' })).not.toBeChecked()
  })

  it('opens configure guidance when turning on a schedule that still needs bindings', async () => {
    const user = userEvent.setup()
    vi.mocked(listSchedules).mockResolvedValue([{
      id: 3,
      workflow_id: 7,
      project_id: 1,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: {},
      input: {},
      overlap_policy: 'skip',
      enabled: false,
      ready: false,
      unresolved_inputs: ['topic'],
      unresolved_labels: ['Topic'],
      last_run_minute: null,
      last_tick_at: null,
      created_by: 1,
      created_at: '',
      updated_at: '',
    }])

    render(<ScheduleManager token="token" workflows={[declaredWorkflow]} workflowId={7} defaultTimezone="UTC" />)
    await screen.findByText('Release')
    await user.click(screen.getByRole('checkbox', { name: 'Schedule off' }))

    expect(updateSchedule).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/missing a required binding/i)
    expect(screen.getByRole('alert')).toHaveTextContent(/save a durable binding/i)
    expect(screen.getByRole('alert')).not.toHaveTextContent(/source node/i)
    expect(screen.getByLabelText('Topic automation binding')).toBeInTheDocument()
  })

  it('waits for the exact spawned job to be selected before finishing Run now', async () => {
    const user = userEvent.setup()
    const job = { id: 99, engine: 'graph', title: 'Release', project_slug: 'owner' } as never
    vi.mocked(listSchedules).mockResolvedValue([{
      id: 3,
      workflow_id: 7,
      project_id: 1,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: {},
      input: {},
      overlap_policy: 'allow',
      enabled: true,
      ready: true,
      unresolved_inputs: [],
      last_run_minute: null,
      last_tick_at: null,
      created_by: 1,
      created_at: '',
      updated_at: '',
    }])
    vi.mocked(runScheduleNow).mockResolvedValue(job)
    let confirmSelection: (() => void) | undefined
    const onOpenJob = vi.fn(() => new Promise<void>(resolve => { confirmSelection = resolve }))
    const onChanged = vi.fn()
    render(
      <ScheduleManager
        token="token"
        workflows={[workflow]}
        workflowId={7}
        defaultTimezone="UTC"
        onOpenJob={onOpenJob}
        onChanged={onChanged}
      />,
    )
    await screen.findByText('Release')

    await user.click(screen.getByRole('button', { name: 'Run now' }))
    expect(onOpenJob).toHaveBeenCalledWith(job)
    expect(screen.getByRole('button', { name: 'Opening run...' })).toBeDisabled()
    expect(onChanged).not.toHaveBeenCalled()

    await act(async () => { confirmSelection?.() })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run now' })).toBeEnabled())
  })

  it('still hands off the spawned job after unmount between spawn and selection', async () => {
    const user = userEvent.setup()
    const job = { id: 99, engine: 'graph', title: 'Release', project_slug: 'owner' } as never
    vi.mocked(listSchedules).mockResolvedValue([{
      id: 3,
      workflow_id: 7,
      project_id: 1,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: {},
      input: {},
      overlap_policy: 'allow',
      enabled: true,
      ready: true,
      unresolved_inputs: [],
      last_run_minute: null,
      last_tick_at: null,
      created_by: 1,
      created_at: '',
      updated_at: '',
    }])
    let finishSpawn: ((value: typeof job) => void) | undefined
    vi.mocked(runScheduleNow).mockImplementation(() => new Promise(resolve => { finishSpawn = resolve }))
    const onOpenJob = vi.fn().mockResolvedValue(undefined)
    const onRunNowHandoffChange = vi.fn()
    const { unmount } = render(
      <ScheduleManager
        token="token"
        workflows={[workflow]}
        workflowId={7}
        defaultTimezone="UTC"
        onOpenJob={onOpenJob}
        onRunNowHandoffChange={onRunNowHandoffChange}
      />,
    )
    await screen.findByText('Release')

    await user.click(screen.getByRole('button', { name: 'Run now' }))
    expect(onRunNowHandoffChange).toHaveBeenCalledWith(true)
    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledWith('token', 3))

    unmount()
    await act(async () => {
      finishSpawn?.(job)
      await Promise.resolve()
    })

    expect(onOpenJob).toHaveBeenCalledWith(job)
    expect(onOpenJob).toHaveBeenCalledTimes(1)
    // Parent owns clearing the handoff after selection; unmount must not end it early.
    expect(onRunNowHandoffChange).not.toHaveBeenCalledWith(false)
  })

  it('signals handoff end when spawn fails before selection', async () => {
    const user = userEvent.setup()
    vi.mocked(listSchedules).mockResolvedValue([{
      id: 3,
      workflow_id: 7,
      project_id: 1,
      cron: '0 9 * * *',
      timezone: 'UTC',
      bindings: {},
      input: {},
      overlap_policy: 'allow',
      enabled: true,
      ready: true,
      unresolved_inputs: [],
      last_run_minute: null,
      last_tick_at: null,
      created_by: 1,
      created_at: '',
      updated_at: '',
    }])
    vi.mocked(runScheduleNow).mockRejectedValue(new Error('spawn failed'))
    const onOpenJob = vi.fn()
    const onRunNowHandoffChange = vi.fn()
    render(
      <ScheduleManager
        token="token"
        workflows={[workflow]}
        workflowId={7}
        defaultTimezone="UTC"
        onOpenJob={onOpenJob}
        onRunNowHandoffChange={onRunNowHandoffChange}
      />,
    )
    await screen.findByText('Release')

    await user.click(screen.getByRole('button', { name: 'Run now' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/spawn failed/)
    expect(onOpenJob).not.toHaveBeenCalled()
    expect(onRunNowHandoffChange).toHaveBeenCalledWith(true)
    expect(onRunNowHandoffChange).toHaveBeenCalledWith(false)
  })
})

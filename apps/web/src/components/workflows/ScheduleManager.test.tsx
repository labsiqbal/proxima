import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ScheduleManager, isValidCron } from './ScheduleManager'
import { createSchedule, listSchedules } from '../../api/schedules'

vi.mock('../../api/schedules', () => ({ listSchedules: vi.fn(), createSchedule: vi.fn(), updateSchedule: vi.fn(), deleteSchedule: vi.fn() }))
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
  it('does not ask for manual intake values when creating a schedule', async () => {
    const user = userEvent.setup()
    render(<ScheduleManager token="token" workflows={[declaredWorkflow]} workflowId={7} />)
    await screen.findByText('No schedules yet.')
    expect(screen.queryByLabelText(/Topic/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Source URL/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add schedule' }))
    expect(createSchedule).toHaveBeenCalledWith('token', {
      workflow_id: 7,
      cron: '0 9 * * *',
      overlap_policy: 'skip',
      enabled: true,
    })
  })

  it('creates an existing workflow schedule through the schedules API', async () => {
    const user = userEvent.setup()
    render(<ScheduleManager token="token" workflows={[workflow]} workflowId={7} />)
    await screen.findByText('No schedules yet.')
    await user.click(screen.getByRole('button', { name: 'Add schedule' }))
    expect(createSchedule).toHaveBeenCalledWith('token', expect.objectContaining({ workflow_id: 7, cron: '0 9 * * *', overlap_policy: 'skip', enabled: true }))
  })
})

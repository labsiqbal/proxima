import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildRunningItems, RunningTasks } from './RunningTasks'
import { listJobs } from '../../api/jobs'
import { activeRuns } from '../../api/runs'
import type { ChatSession, Job } from '../../types'

vi.mock('../../api/jobs', () => ({ listJobs: vi.fn() }))
vi.mock('../../api/runs', () => ({ activeRuns: vi.fn() }))

const job = {
  id: 4,
  title: 'Ship release notes',
  status: 'running',
  session_id: 12,
  engine: 'linear',
  project_slug: 'demo',
} as Job

describe('buildRunningItems', () => {
  it('prefers jobs over bare sessions and keeps chat-only runs', () => {
    const sessions = [
      { id: 12, title: 'Job session' },
      { id: 99, title: 'Brainstorm' },
    ] as ChatSession[]
    const items = buildRunningItems([12, 99], [job], sessions)
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ kind: 'job', jobId: 4, title: 'Ship release notes' })
    expect(items[1]).toMatchObject({ kind: 'session', sessionId: 99, title: 'Brainstorm' })
  })
})

describe('RunningTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [12] })
    vi.mocked(listJobs).mockResolvedValue({ items: [job], total: 1, limit: 50, offset: 0 })
  })

  it('shows a quiet zero state and a badge when work is running', async () => {
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [] })
    vi.mocked(listJobs).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    render(<RunningTasks token="token" />)
    expect(await screen.findByRole('button', { name: '0 running tasks' })).toBeInTheDocument()
    expect(screen.queryByText('99+')).not.toBeInTheDocument()
  })

  it('deep-links jobs and sessions from the popover', async () => {
    const user = userEvent.setup()
    const onOpenJob = vi.fn()
    const onOpenSession = vi.fn()
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [12, 99] })
    render(
      <RunningTasks
        token="token"
        sessions={[{ id: 99, title: 'Brainstorm', runner_id: 'pi', visibility: 'private' }]}
        onOpenJob={onOpenJob}
        onOpenSession={onOpenSession}
        onOpenTasks={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: '2 running tasks' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '2 running tasks' }))

    expect(screen.getByRole('dialog', { name: 'Running tasks' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Ship release notes/ }))
    expect(onOpenJob).toHaveBeenCalledWith(4, 'linear')

    await user.click(screen.getByRole('button', { name: '2 running tasks' }))
    await user.click(screen.getByRole('button', { name: /Brainstorm/ }))
    expect(onOpenSession).toHaveBeenCalledWith(99)
  })

  it('supports keyboard open and Escape dismiss', async () => {
    const user = userEvent.setup()
    render(<RunningTasks token="token" />)
    const trigger = await screen.findByRole('button', { name: '1 running task' })
    trigger.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('dialog', { name: 'Running tasks' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Running tasks' })).not.toBeInTheDocument())
  })
})

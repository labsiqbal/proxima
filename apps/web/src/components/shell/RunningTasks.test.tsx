import '@testing-library/jest-dom/vitest'
import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildRunningItems, RunningTasks, runningTasksLabel } from './RunningTasks'
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

function RunningHarness({
  onOpenChange = vi.fn(),
  ...props
}: Omit<React.ComponentProps<typeof RunningTasks>, 'open' | 'onOpenChange'> & {
  onOpenChange?: (open: boolean) => void
}) {
  const [open, setOpen] = React.useState(false)
  return (
    <RunningTasks
      {...props}
      open={open}
      onOpenChange={next => {
        setOpen(next)
        onOpenChange(next)
      }}
    />
  )
}

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

describe('runningTasksLabel', () => {
  it('uses singular and plural full phrases', () => {
    expect(runningTasksLabel(1)).toBe('1 task running')
    expect(runningTasksLabel(3)).toBe('3 tasks running')
  })

  it('shortens for narrow layouts', () => {
    expect(runningTasksLabel(1, true)).toBe('1 running')
    expect(runningTasksLabel(4, true)).toBe('4 running')
  })
})

describe('RunningTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [12] })
    vi.mocked(listJobs).mockResolvedValue({ items: [job], total: 1, limit: 50, offset: 0 })
  })

  it('hides entirely when nothing is running (quiet header)', async () => {
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [] })
    vi.mocked(listJobs).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
    const { container } = render(<RunningHarness token="token" />)
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows a text pill with the full phrase - never icon-only or "!"', async () => {
    render(<RunningHarness token="token" />)
    const trigger = await screen.findByRole('button', { name: '1 task running' })
    expect(trigger).toHaveClass('running-pill')
    expect(trigger).toHaveTextContent('1 task running')
    expect(trigger).toHaveTextContent('1 running')
    expect(trigger).not.toHaveTextContent('!')
    expect(trigger.querySelector('svg')).not.toBeInTheDocument()
  })

  it('pluralizes for multiple running tasks', async () => {
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [12, 99] })
    render(
      <RunningHarness
        token="token"
        sessions={[{ id: 99, title: 'Brainstorm', runner_id: 'pi', visibility: 'private' }]}
      />,
    )
    expect(await screen.findByRole('button', { name: '2 tasks running' })).toBeInTheDocument()
  })

  it('deep-links jobs and sessions from the popover', async () => {
    const user = userEvent.setup()
    const onOpenJob = vi.fn()
    const onOpenSession = vi.fn()
    vi.mocked(activeRuns).mockResolvedValue({ session_ids: [12, 99] })
    render(
      <RunningHarness
        token="token"
        sessions={[{ id: 99, title: 'Brainstorm', runner_id: 'pi', visibility: 'private' }]}
        onOpenJob={onOpenJob}
        onOpenSession={onOpenSession}
        onOpenTasks={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: '2 tasks running' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '2 tasks running' }))

    expect(screen.getByRole('dialog', { name: 'Running tasks' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Ship release notes/ }))
    expect(onOpenJob).toHaveBeenCalledWith(4, 'linear')

    await user.click(screen.getByRole('button', { name: '2 tasks running' }))
    await user.click(screen.getByRole('button', { name: /Brainstorm/ }))
    expect(onOpenSession).toHaveBeenCalledWith(99)
  })

  it('supports keyboard open and Escape dismiss', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<RunningHarness token="token" onOpenChange={onOpenChange} />)
    const trigger = await screen.findByRole('button', { name: '1 task running' })
    trigger.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('dialog', { name: 'Running tasks' })).toBeInTheDocument()
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(true))
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Running tasks' })).not.toBeInTheDocument())
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(false))
  })
})

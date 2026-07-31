import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GraphScreen } from './GraphScreen'
import { getGraphJob, listGraphJobs } from '../api/graph'
import { FRESH_FAILED_REVIEW_RUN } from '../testFixtures/failedReviewRun'
import type { RunEvent } from '../types'

let graphEventHandler: ((event: RunEvent) => void) | null = null
let homePollTask: (() => void | Promise<void>) | null = null

vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn(),
  listGraphTemplates: vi.fn().mockResolvedValue({
    items: [
      { id: 10, name: 'Manual report', category: 'research', status: 'active', graph: { nodes: [], edges: [] }, inputs: [] },
      { id: 11, name: 'Daily report', category: 'content', status: 'active', graph: { nodes: [], edges: [] }, inputs: [] },
    ],
  }),
  getGraphJob: vi.fn(),
  createGraphJob: vi.fn(),
  deleteGraphJob: vi.fn(),
  deleteGraphTemplate: vi.fn(),
  setGraphTemplateStatus: vi.fn(),
  saveGraphTemplate: vi.fn(),
  startGraphJob: vi.fn(),
  updateGraphPlan: vi.fn(),
  answerGraphNode: vi.fn(),
  approveGraphJob: vi.fn(),
  approveGraphNode: vi.fn(),
  approveGraphNodeScript: vi.fn(),
  editGraphNodeOutput: vi.fn(),
  rerunGraphNode: vi.fn(),
}))
vi.mock('../api/schedules', () => ({
  listSchedules: vi.fn().mockResolvedValue([
    { id: 1, workflow_id: 11, cron: '0 9 * * *', enabled: true },
  ]),
}))
vi.mock('../api/runs', () => ({
  activeRuns: vi.fn().mockResolvedValue({ session_ids: [] }),
}))
vi.mock('../api/jobs', () => ({ getJobDiff: vi.fn() }))
vi.mock('../api/profiles', () => ({
  runnerCapabilities: vi.fn().mockResolvedValue({ skills: [], mcp: [] }),
}))
vi.mock('../api/projects', () => ({
  listProjectAreas: vi.fn().mockResolvedValue({ code_areas: [], ops_area: null }),
}))
vi.mock('../hooks/useProjectMentionItems', () => ({
  useProjectMentionItems: () => [],
}))
vi.mock('../hooks/useEventStream', () => ({
  useEventStream: (
    _token: string,
    _sessionId: number | null,
    onEvent: (event: RunEvent) => void,
  ) => {
    graphEventHandler = onEvent
    return { connected: true }
  },
}))
vi.mock('../hooks/usePolling', () => ({
  usePolling: (
    task: () => void | Promise<void>,
    _intervalMs: number,
    options: { enabled?: boolean } = {},
  ) => {
    if (options.enabled) homePollTask = task
  },
}))

const project = {
  slug: 'owner-personal',
  name: 'owner (personal)',
  path: '/tmp/owner',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}

const props = {
  token: 't',
  projects: [project],
  activeProject: project,
  onActiveProject: vi.fn(),
  profiles: [],
  profileId: null,
  features: { designStudio: false, workflowGraph: true, masterOrchestrator: false },
  activeProfile: null,
}

const draftJob = {
  id: 1,
  session_id: 1,
  title: 'Draft report',
  status: 'queued' as const,
  engine: 'graph' as const,
  graph: { nodes: [], edges: [] },
  node_states: [],
  created_at: '2026-07-26T00:00:00Z',
}

const restoredRun = {
  ...FRESH_FAILED_REVIEW_RUN,
  status: 'queued' as const,
  started_at: null,
  finished_at: null,
  node_states: FRESH_FAILED_REVIEW_RUN.node_states.map(state => ({
    ...state,
    status: 'pending' as const,
    started_at: null,
    finished_at: null,
  })),
}

describe('GraphScreen workflow home tabs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    graphEventHandler = null
    homePollTask = null
    vi.mocked(listGraphJobs).mockResolvedValue({
      items: [draftJob, FRESH_FAILED_REVIEW_RUN],
    })
    vi.mocked(getGraphJob).mockResolvedValue(FRESH_FAILED_REVIEW_RUN)
  })

  it('shows counted table tabs and the required row actions', async () => {
    render(<GraphScreen {...props} />)

    const workflowsTab = await screen.findByRole('tab', { name: 'Workflows 2' })
    expect(workflowsTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('table', { name: 'Manual workflows' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Scheduled workflows' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Drafts 1' }))
    const drafts = screen.getByRole('table', { name: 'Draft plans' })
    expect(within(drafts).getByText('Draft report')).toBeInTheDocument()
    expect(within(drafts).getByText('Draft')).toBeInTheDocument()
    expect(within(drafts).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(drafts).getByRole('button', { name: 'Run' })).toBeInTheDocument()
    expect(within(drafts).getByRole('button', { name: '★ Save as template' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Runs 1' }))
    const runs = screen.getByRole('table', { name: 'Workflow runs' })
    expect(within(runs).getByText('Launch readiness review')).toBeInTheDocument()
    expect(within(runs).getByText('Failed')).toBeInTheDocument()
    expect(within(runs).getByText('12s')).toBeInTheDocument()
    expect(within(runs).queryByText(/7h/)).not.toBeInTheDocument()
    expect(within(runs).getByRole('button', { name: 'View' })).toBeInTheDocument()
  })

  it('remembers the last selected tab across remounts', async () => {
    const first = render(<GraphScreen {...props} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    expect(localStorage.getItem('proxima.graph.homeTab')).toBe('drafts')
    first.unmount()

    render(<GraphScreen {...props} />)
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Drafts 1' })).toHaveAttribute('aria-selected', 'true')
    })
    expect(screen.getByRole('table', { name: 'Draft plans' })).toBeInTheDocument()
  })

  it('refreshes keep-alive home Runs after checkpoint restore job.update', async () => {
    localStorage.setItem('proxima.graph.homeTab', 'runs')
    const view = render(<GraphScreen {...props} pendingJobId={41} onPendingConsumed={vi.fn()} />)

    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 41))
    await waitFor(() => expect(graphEventHandler).not.toBeNull())

    view.rerender(<GraphScreen {...props} backNonce={1} />)
    await waitFor(() => {
      expect(screen.getByRole('table', { name: 'Workflow runs' })).toBeInTheDocument()
    })
    expect(within(screen.getByRole('table', { name: 'Workflow runs' })).getByText('Failed')).toBeInTheDocument()

    vi.mocked(getGraphJob).mockResolvedValue(restoredRun as never)
    vi.mocked(listGraphJobs).mockResolvedValue({
      items: [draftJob, restoredRun as typeof FRESH_FAILED_REVIEW_RUN],
    })

    await act(async () => {
      graphEventHandler?.({
        id: 77,
        run_id: 0,
        session_id: 14,
        project_id: 1,
        seq: 1,
        type: 'job.update',
        payload: { job_id: 41, status: 'queued' },
        created_at: '2026-07-31T06:00:00Z',
      })
    })

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Runs 0' })).toBeInTheDocument()
      expect(screen.getByText('No workflow runs yet.')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Drafts 2' }))
    const drafts = screen.getByRole('table', { name: 'Draft plans' })
    expect(within(drafts).getByText('Launch readiness review')).toBeInTheDocument()
    expect(within(drafts).getAllByText('Draft').length).toBeGreaterThan(0)
  })

  it('poll-refreshes home Runs after external mutation without an open job', async () => {
    localStorage.setItem('proxima.graph.homeTab', 'runs')
    render(<GraphScreen {...props} />)

    const runs = await screen.findByRole('table', { name: 'Workflow runs' })
    expect(within(runs).getByText('Failed')).toBeInTheDocument()
    await waitFor(() => expect(homePollTask).not.toBeNull())

    vi.mocked(listGraphJobs).mockResolvedValue({
      items: [draftJob, restoredRun as typeof FRESH_FAILED_REVIEW_RUN],
    })
    await act(async () => {
      await homePollTask?.()
    })

    await waitFor(() => {
      expect(screen.queryByRole('table', { name: 'Workflow runs' })).not.toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('tab', { name: /Drafts/ }))
    const drafts = screen.getByRole('table', { name: 'Draft plans' })
    expect(within(drafts).getByText('Launch readiness review')).toBeInTheDocument()
  })
})

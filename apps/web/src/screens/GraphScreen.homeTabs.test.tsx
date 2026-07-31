import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createGraphJob, getGraphJob, listGraphJobs, startGraphJob, updateGraphPlan } from '../api/graph'
import { FRESH_FAILED_REVIEW_RUN } from '../testFixtures/failedReviewRun'
import type { GraphJob, RunEvent, WorkflowGraph } from '../types'
import { GraphScreen } from './GraphScreen'

let graphEventHandler: ((event: RunEvent) => void) | null = null
let homePollTask: (() => void | Promise<void>) | null = null

const draftGraph: WorkflowGraph = {
  nodes: [{
    id: 'trigger',
    type: 'trigger',
    trigger_kind: 'manual',
    name: 'When I run it',
    instruction: '',
    output_kind: 'json',
    inputs: [
      { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
      { id: 'audience', label: 'Audience', kind: 'text', required: false },
    ],
  }],
  edges: [],
}

const draftJob: GraphJob = {
  id: 1,
  title: 'Draft report',
  status: 'queued',
  engine: 'graph',
  graph: draftGraph,
  node_states: [{
    id: 1,
    job_id: 1,
    node_id: 'trigger',
    status: 'pending',
    output_kind: 'json',
    version: 0,
  }],
  created_at: '2026-07-26T00:00:00Z',
}

vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn(),
  listGraphTemplates: vi.fn().mockResolvedValue({
    items: [
      {
        id: 10,
        name: 'Manual report',
        category: 'research',
        status: 'active',
        graph: {
          nodes: [{
            id: 'trigger',
            type: 'trigger',
            trigger_kind: 'manual',
            name: 'When I run it',
            inputs: [
              { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
              { id: 'channel', label: 'Channel', kind: 'text', required: false, default: 'email' },
            ],
          }],
          edges: [],
        },
        inputs: [],
      },
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
vi.mock('../components/workflows/GraphCanvas', () => ({
  GraphCanvas: ({ onSelect }: { onSelect: (nodeId: string) => void }) => (
    <button onClick={() => onSelect('trigger')}>Select trigger</button>
  ),
  stateFor: vi.fn(() => undefined),
  statusLabel: (status: string) => status,
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
    const workflows = screen.getByRole('table', { name: 'Reusable workflows' })
    expect(within(workflows).getAllByText('Available')).toHaveLength(2)
    expect(within(workflows).getByText('No schedules')).toBeInTheDocument()
    expect(within(workflows).getByText('1 schedule on')).toBeInTheDocument()
    expect(within(workflows).getAllByRole('button', { name: 'Run' })).toHaveLength(2)
    expect(within(workflows).getAllByRole('button', { name: 'Schedules' })).toHaveLength(2)

    fireEvent.click(screen.getByRole('tab', { name: 'Drafts 1' }))
    const drafts = screen.getByRole('table', { name: 'Draft plans' })
    expect(within(drafts).getByText('Draft report')).toBeInTheDocument()
    expect(within(drafts).getByText('Draft')).toBeInTheDocument()
    expect(within(drafts).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(drafts).getByRole('button', { name: 'Run' })).toBeInTheDocument()
    expect(within(drafts).getByRole('button', { name: '★ Save as template' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Runs 1' }))
    const runs = screen.getByRole('table', { name: 'Workflow runs' })
    const runRow = within(runs).getByText('Launch readiness review').closest('[role="row"]') as HTMLElement
    expect(within(runRow).getByText('Failed')).toBeInTheDocument()
    // Duration is authoritative wall-clock length; When is a separate relative-age cell.
    expect(runRow.querySelector('[data-label="Duration"]')).toHaveTextContent('12s')
    expect(runRow.querySelector('[data-label="When"]')?.textContent).toMatch(/ago|Just now|Yesterday/)
    expect(within(runRow).getByRole('button', { name: 'View' })).toBeInTheDocument()
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

  it('validates reusable manual workflow intake before creating and starting a run', async () => {
    vi.mocked(createGraphJob).mockResolvedValue({
      id: 21,
      title: 'Manual report',
      status: 'queued',
      engine: 'graph',
      graph: {
        nodes: [{
          id: 'trigger',
          type: 'trigger',
          trigger_kind: 'manual',
          name: 'When I run it',
          inputs: [
            { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
            { id: 'channel', label: 'Channel', kind: 'text', required: false, default: 'email' },
          ],
        }],
        edges: [],
      },
      node_states: [],
    })
    vi.mocked(startGraphJob).mockResolvedValue({
      id: 21,
      title: 'Manual report',
      status: 'review',
      engine: 'graph',
      graph: { nodes: [], edges: [] },
      node_states: [],
    })
    render(<GraphScreen {...props} />)

    const manual = await screen.findByRole('table', { name: 'Reusable workflows' })
    const reportRow = within(manual).getByText('Manual report').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(reportRow).getByRole('button', { name: 'Run' }))

    expect(screen.getByRole('dialog', { name: 'Run Manual report' })).toBeInTheDocument()
    expect(createGraphJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Campaign')
    expect(createGraphJob).not.toHaveBeenCalled()

    fireEvent.change(screen.getByRole('textbox', { name: 'Campaign' }), {
      target: { value: 'Launch week' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))

    await waitFor(() => {
      expect(createGraphJob).toHaveBeenCalledWith('t', expect.objectContaining({
        workflow_id: 10,
      }))
      expect(createGraphJob).toHaveBeenCalledWith(
        't',
        expect.not.objectContaining({ input: expect.anything() }),
      )
      expect(startGraphJob).toHaveBeenCalledWith(
        't',
        21,
        { campaign: 'Launch week', channel: 'email' },
      )
    })
  })

  it('reuses the created template job when start fails and the dialog is retried', async () => {
    vi.mocked(createGraphJob).mockResolvedValue({
      id: 21,
      title: 'Manual report',
      status: 'queued',
      engine: 'graph',
      graph: {
        nodes: [{
          id: 'trigger',
          type: 'trigger',
          trigger_kind: 'manual',
          name: 'When I run it',
          inputs: [
            { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
            { id: 'channel', label: 'Channel', kind: 'text', required: false, default: 'email' },
          ],
        }],
        edges: [],
      },
      node_states: [],
    })
    vi.mocked(startGraphJob)
      .mockRejectedValueOnce(new Error('missing execution profile'))
      .mockResolvedValueOnce({
        id: 21,
        title: 'Manual report',
        status: 'running',
        engine: 'graph',
        graph: { nodes: [], edges: [] },
        node_states: [],
      })

    render(<GraphScreen {...props} />)
    const manual = await screen.findByRole('table', { name: 'Reusable workflows' })
    const reportRow = within(manual).getByText('Manual report').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(reportRow).getByRole('button', { name: 'Run' }))

    fireEvent.change(screen.getByRole('textbox', { name: 'Campaign' }), {
      target: { value: 'Launch week' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('missing execution profile')
    expect(createGraphJob).toHaveBeenCalledTimes(1)
    expect(startGraphJob).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('dialog', { name: 'Run Manual report' })).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: 'Campaign' }), {
      target: { value: 'Retry launch' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))

    await waitFor(() => {
      expect(startGraphJob).toHaveBeenCalledTimes(2)
      expect(startGraphJob).toHaveBeenLastCalledWith(
        't',
        21,
        { campaign: 'Retry launch', channel: 'email' },
      )
    })
    expect(createGraphJob).toHaveBeenCalledTimes(1)
  })

  it('blocks home draft Run while the open draft is unsaved or invalid', async () => {
    vi.mocked(listGraphJobs).mockResolvedValue({
      items: [structuredClone(draftJob)],
    })
    vi.mocked(getGraphJob).mockResolvedValue(structuredClone(draftJob))
    vi.mocked(updateGraphPlan).mockImplementation(async (_token, _jobId, body) => ({
      ...structuredClone(draftJob),
      title: body.title ?? draftJob.title,
      graph: body.graph ?? structuredClone(draftGraph),
    }))

    const view = render(<GraphScreen {...props} pendingJobId={1} backNonce={0} />)
    await screen.findByRole('heading', { name: 'Draft report' })
    fireEvent.click(screen.getByRole('button', { name: 'Select trigger' }))

    const audienceId = screen.getByRole('textbox', { name: 'Input 2 ID' })
    fireEvent.change(audienceId, { target: { value: 'campaign' } })
    fireEvent.blur(audienceId)
    expect(screen.getByText('ID must be unique.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '▶ Run' })).toBeDisabled()

    view.rerender(<GraphScreen {...props} pendingJobId={1} backNonce={1} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    const drafts = screen.getByRole('table', { name: 'Draft plans' })
    const run = within(drafts).getByRole('button', { name: 'Run' })
    expect(run).toBeDisabled()
    expect(run).toHaveAttribute('title', 'Wait for a valid saved workflow before running')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('runs the open draft from its current in-memory graph, not the stale home list row', async () => {
    const staleListJob: GraphJob = {
      ...structuredClone(draftJob),
      graph: { nodes: [], edges: [] },
      node_states: [],
    }
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [staleListJob] })
    vi.mocked(getGraphJob).mockResolvedValue(structuredClone(draftJob))
    vi.mocked(updateGraphPlan).mockImplementation(async (_token, _jobId, body) => ({
      ...structuredClone(draftJob),
      title: body.title ?? draftJob.title,
      graph: body.graph ?? structuredClone(draftGraph),
    }))
    vi.mocked(startGraphJob).mockResolvedValue({
      ...structuredClone(draftJob),
      status: 'running',
    })

    const view = render(<GraphScreen {...props} pendingJobId={1} backNonce={0} />)
    await screen.findByRole('heading', { name: 'Draft report' })
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())

    view.rerender(<GraphScreen {...props} pendingJobId={1} backNonce={1} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    const drafts = screen.getByRole('table', { name: 'Draft plans' })
    fireEvent.click(within(drafts).getByRole('button', { name: 'Run' }))

    expect(screen.getByRole('dialog', { name: 'Run Draft report' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Campaign' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Audience' })).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: 'Campaign' }), {
      target: { value: 'Launch week' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))

    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 1, {
      campaign: 'Launch week',
    }))
  })

  it('soft-fails ordinary Edit when the job cannot be loaded', async () => {
    const onUnhandled = vi.fn()
    const handleRejection = (event: PromiseRejectionEvent) => {
      onUnhandled(event.reason)
      event.preventDefault()
    }
    window.addEventListener('unhandledrejection', handleRejection)
    vi.mocked(getGraphJob).mockRejectedValue(new Error('job gone'))

    render(<GraphScreen {...props} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))

    expect(await screen.findByText('Error: job gone')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Draft plans' })).toBeInTheDocument()
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 1))
    expect(onUnhandled).not.toHaveBeenCalled()
    window.removeEventListener('unhandledrejection', handleRejection)
  })

})

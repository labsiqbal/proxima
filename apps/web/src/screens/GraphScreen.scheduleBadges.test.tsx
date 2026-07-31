import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createGraphJob, getGraphJob, listGraphJobs, listGraphTemplates, startGraphJob, updateGraphPlan } from '../api/graph'
import { listSchedules, runScheduleNow } from '../api/schedules'
import { GraphScreen } from './GraphScreen'

vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn().mockResolvedValue({ items: [] }),
  listGraphTemplates: vi.fn().mockResolvedValue({ items: [] }),
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
  listSchedules: vi.fn().mockResolvedValue([]),
  createSchedule: vi.fn(),
  updateSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
  runScheduleNow: vi.fn(),
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

const project = {
  slug: 'owner-personal',
  name: 'owner (personal)',
  path: '/tmp/owner',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}

const screenBase = {
  token: 't',
  projects: [project],
  activeProject: project,
  onActiveProject: vi.fn(),
  profiles: [] as never[],
  profileId: null as number | null,
  features: { designStudio: false, workflowGraph: true, masterOrchestrator: false },
  activeProfile: null,
}

function runningJob(id: number, title: string) {
  return {
    id,
    project_id: 1,
    project_slug: 'owner-personal',
    workflow_id: 10,
    session_id: id,
    title,
    status: 'running',
    input: {},
    engine: 'graph',
    graph: {
      nodes: [{ id: 'only', type: 'agent', name: 'Only', instruction: 'Work', output_kind: 'text' }],
      edges: [],
    },
    node_states: [],
  } as never
}

describe('GraphScreen how-it-runs badges', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [] })
    vi.mocked(listGraphTemplates).mockResolvedValue({
      items: [
        {
          id: 10,
          name: 'Nightly publish',
          description: 'Publish notes every night',
          status: 'active',
          category: null,
          project_id: 1,
          project_slug: 'owner-personal',
          graph: {
            nodes: [{
              id: 'trigger',
              type: 'trigger',
              name: 'On demand or schedule',
              instruction: '',
              output_kind: 'json',
              trigger_kind: 'scheduled',
              inputs: [{ id: 'topic', label: 'Topic', kind: 'text', required: true }],
            }],
            edges: [],
          },
          inputs: [],
          created_at: '',
          updated_at: '',
        } as never,
        {
          id: 11,
          name: 'Publish on demand',
          description: 'Manual publish',
          status: 'active',
          category: 'content',
          project_id: 1,
          project_slug: 'owner-personal',
          graph: { nodes: [], edges: [] },
          inputs: [],
        } as never,
      ],
    })
    vi.mocked(listSchedules).mockResolvedValue([
      { id: 1, workflow_id: 10, cron: '0 * * * *', enabled: true } as never,
    ])
  })

  it('shows workflow availability separately from schedule state and keeps manual Run', async () => {
    render(
      <GraphScreen
        token="t"
        projects={[project]}
        activeProject={project}
        onActiveProject={vi.fn()}
        profiles={[]}
        profileId={null}
        features={{ designStudio: false, workflowGraph: true, masterOrchestrator: false }}
        activeProfile={null}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const workflows = screen.getByRole('table', { name: 'Reusable workflows' })
    const nightlyRow = within(workflows).getByText('Nightly publish').closest('[role="row"]')
    expect(nightlyRow).not.toBeNull()
    expect(within(nightlyRow as HTMLElement).getByText('Available')).toBeInTheDocument()
    expect(within(nightlyRow as HTMLElement).getByText('1 schedule on')).toBeInTheDocument()
    expect(within(nightlyRow as HTMLElement).getByRole('button', { name: 'Run' })).toBeInTheDocument()
    expect(within(nightlyRow as HTMLElement).getByRole('button', { name: 'Pause Nightly publish' })).toBeInTheDocument()

    fireEvent.click(within(nightlyRow as HTMLElement).getByRole('button', { name: 'Run' }))
    expect(screen.getByRole('heading', { name: 'Run “Nightly publish”' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Topic/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.click(within(nightlyRow as HTMLElement).getByRole('button', { name: 'Schedules' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
  })

  it('summarizes one unresolved schedule as needs binding', async () => {
    vi.mocked(listSchedules).mockResolvedValue([
      { id: 1, workflow_id: 10, cron: '0 * * * *', enabled: false, ready: false } as never,
    ])
    render(
      <GraphScreen
        token="t"
        projects={[project]}
        activeProject={project}
        onActiveProject={vi.fn()}
        profiles={[]}
        profileId={null}
        features={{ designStudio: false, workflowGraph: true, masterOrchestrator: false }}
        activeProfile={null}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const nightlyRow = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    expect(within(nightlyRow).getByText('1 needs binding')).toBeInTheDocument()
    expect(within(nightlyRow).queryByText(/source/i)).not.toBeInTheDocument()
  })

  it('summarizes multiple unresolved schedules as need bindings', async () => {
    vi.mocked(listSchedules).mockResolvedValue([
      { id: 1, workflow_id: 10, cron: '0 * * * *', enabled: false, ready: false } as never,
      { id: 2, workflow_id: 10, cron: '0 9 * * *', enabled: false, ready: false } as never,
    ])
    render(
      <GraphScreen
        token="t"
        projects={[project]}
        activeProject={project}
        onActiveProject={vi.fn()}
        profiles={[]}
        profileId={null}
        features={{ designStudio: false, workflowGraph: true, masterOrchestrator: false }}
        activeProfile={null}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const nightlyRow = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    expect(within(nightlyRow).getByText('2 need bindings')).toBeInTheDocument()
    expect(within(nightlyRow).queryByText(/source/i)).not.toBeInTheDocument()
  })

  it('keeps the schedule dialog open until the exact spawned graph job is selected', async () => {
    const spawned = {
      id: 99,
      project_id: 1,
      project_slug: 'owner-personal',
      workflow_id: 10,
      session_id: 99,
      title: 'Nightly publish run',
      status: 'running',
      input: {},
      engine: 'graph',
      graph: {
        nodes: [{ id: 'only', type: 'agent', name: 'Only', instruction: 'Publish', output_kind: 'text' }],
        edges: [],
      },
      node_states: [],
    } as never
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)
    let finishLoad: ((value: typeof spawned) => void) | undefined
    vi.mocked(getGraphJob).mockImplementation(() => new Promise(resolve => { finishLoad = resolve }))
    render(
      <GraphScreen
        token="t"
        projects={[project]}
        activeProject={project}
        onActiveProject={vi.fn()}
        profiles={[]}
        profileId={null}
        features={{ designStudio: false, workflowGraph: true, masterOrchestrator: false }}
        activeProfile={null}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    finishLoad?.(spawned)

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
  })

  it('keeps Run now open and reports when the spawned job cannot be selected', async () => {
    const spawned = {
      id: 99,
      project_id: 1,
      project_slug: 'owner-personal',
      workflow_id: 10,
      session_id: 99,
      title: 'Nightly publish run',
      status: 'running',
      input: {},
      engine: 'graph',
      graph: {
        nodes: [{ id: 'only', type: 'agent', name: 'Only', instruction: 'Publish', output_kind: 'text' }],
        edges: [],
      },
      node_states: [],
    } as never
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)
    vi.mocked(getGraphJob).mockRejectedValue(new Error('network down'))

    render(
      <GraphScreen
        token="t"
        projects={[project]}
        activeProject={project}
        onActiveProject={vi.fn()}
        profiles={[]}
        profileId={null}
        features={{ designStudio: false, workflowGraph: true, masterOrchestrator: false }}
        activeProfile={null}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not select spawned graph job 99/)
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Nightly publish run' })).not.toBeInTheDocument()
  })

  it('discards a stale pending open without unhandled rejection', async () => {
    const jobA = {
      id: 41,
      project_slug: 'owner-personal',
      title: 'First plan',
      status: 'queued',
      engine: 'graph',
      graph: {
        nodes: [{ id: 'only', type: 'agent', name: 'Only', instruction: 'A', output_kind: 'text' }],
        edges: [],
      },
      node_states: [],
    } as never
    const jobB = {
      id: 42,
      project_slug: 'owner-personal',
      title: 'Second plan',
      status: 'queued',
      engine: 'graph',
      graph: {
        nodes: [{ id: 'only', type: 'agent', name: 'Only', instruction: 'B', output_kind: 'text' }],
        edges: [],
      },
      node_states: [],
    } as never
    let finishA: ((value: typeof jobA) => void) | undefined
    const pendingA = new Promise<typeof jobA>(resolve => { finishA = resolve })
    vi.mocked(getGraphJob)
      .mockImplementationOnce(() => pendingA)
      .mockResolvedValueOnce(jobB)

    const onUnhandled = vi.fn()
    const handleRejection = (event: PromiseRejectionEvent) => {
      onUnhandled(event.reason)
      event.preventDefault()
    }
    window.addEventListener('unhandledrejection', handleRejection)
    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen {...screenBase} pendingJobId={41} onPendingConsumed={onPendingConsumed} />,
    )
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 41))

    rerender(<GraphScreen {...screenBase} pendingJobId={42} onPendingConsumed={onPendingConsumed} />)
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 42))
    finishA?.(jobA)

    expect(await screen.findByRole('button', { name: 'Rename workflow Second plan' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow First plan' })).not.toBeInTheDocument()
    expect(onUnhandled).not.toHaveBeenCalled()
    window.removeEventListener('unhandledrejection', handleRejection)
  })

  it('does not poll a prior running job after returning home, so Run now can select the spawned job once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = runningJob(77, 'Prior live run')
    const spawned = runningJob(99, 'Nightly publish run')
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 99) return spawned
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior live run' })).toBeInTheDocument()

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())

    const callsAfterHome = vi.mocked(getGraphJob).mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(vi.mocked(getGraphJob).mock.calls.slice(callsAfterHome)).toEqual([])

    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getGraphJob).mock.calls.filter(call => call[1] === 99)).toHaveLength(1)
    expect(vi.mocked(getGraphJob).mock.calls.filter(call => call[1] === 77).length).toBeGreaterThanOrEqual(1)
  })

  it('keeps an in-flight prior-job refresh from cancelling Run now selection', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = runningJob(77, 'Prior live run')
    const spawned = runningJob(99, 'Nightly publish run')
    let finishPriorRefresh: ((value: typeof prior) => void) | undefined
    let priorGets = 0
    let spawnedGets = 0
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 77) {
        priorGets += 1
        if (priorGets === 1) return Promise.resolve(prior)
        return new Promise(resolve => { finishPriorRefresh = resolve })
      }
      if (jobId === 99) {
        spawnedGets += 1
        return Promise.resolve(spawned)
      }
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior live run' })).toBeInTheDocument()
    expect(priorGets).toBe(1)

    // Live editor poll starts a hanging refresh for the prior job.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    await waitFor(() => expect(priorGets).toBe(2))
    expect(finishPriorRefresh).toBeTypeOf('function')

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())

    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    await waitFor(() => expect(spawnedGets).toBe(1))
    await act(async () => {
      finishPriorRefresh?.(prior)
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps live polling after Start from the editor without openJob', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const queued = {
      ...runningJob(42, 'Editor draft'),
      status: 'queued',
      workflow_id: null,
    } as never
    const started = runningJob(42, 'Editor draft')
    const refreshed = {
      ...started,
      title: 'Editor draft live',
    } as never
    let launched = false
    let gets = 0
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId !== 42) throw new Error(`unexpected job ${jobId}`)
      gets += 1
      return launched ? refreshed : queued
    })
    vi.mocked(startGraphJob).mockImplementation(async () => {
      launched = true
      return started
    })

    render(
      <GraphScreen
        {...screenBase}
        pendingJobId={42}
        onPendingConsumed={vi.fn()}
      />,
    )
    expect(await screen.findByRole('button', { name: '▶ Run' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '▶ Run' }))
    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 42))
    expect(await screen.findByText('Execution started.')).toBeInTheDocument()

    const getsAfterStart = gets
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    await waitFor(() => expect(gets).toBeGreaterThan(getsAfterStart))
    expect(await screen.findByRole('button', { name: 'Rename workflow Editor draft live' })).toBeInTheDocument()
  })

  it('keeps live polling after home Run draft without openJob', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const draft = {
      ...runningJob(55, 'Home draft'),
      status: 'queued',
      workflow_id: null,
    } as never
    const started = runningJob(55, 'Home draft')
    const refreshed = {
      ...started,
      title: 'Home draft refreshed',
    } as never
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [draft] })
    let launched = false
    let gets = 0
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId !== 55) throw new Error(`unexpected job ${jobId}`)
      gets += 1
      return launched ? refreshed : draft
    })
    vi.mocked(startGraphJob).mockImplementation(async () => {
      launched = true
      return started
    })

    render(<GraphScreen {...screenBase} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    const row = (await screen.findByText('Home draft')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Run' }))

    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 55))
    expect(await screen.findByRole('button', { name: 'Rename workflow Home draft' })).toBeInTheDocument()

    const getsAfterStart = gets
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    await waitFor(() => expect(gets).toBeGreaterThan(getsAfterStart))
    expect(await screen.findByRole('button', { name: 'Rename workflow Home draft refreshed' })).toBeInTheDocument()
  })

  it('adopts a home Run draft only after slow prior flush, never with the old plan', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = {
      ...queuedDraft(77, 'Prior draft'),
      graph: {
        nodes: [{ id: 'prior-only', type: 'agent', name: 'Prior step', instruction: 'Old', output_kind: 'text' }],
        edges: [],
      },
    } as never
    const target = {
      ...queuedDraft(55, 'Target draft'),
      graph: {
        nodes: [{ id: 'target-only', type: 'agent', name: 'Target step', instruction: 'Fresh', output_kind: 'text' }],
        edges: [],
      },
    } as never
    const started = {
      ...runningJob(55, 'Target draft'),
      workflow_id: null,
      graph: target.graph,
    } as never
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior, target] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 55) return started
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })
    vi.mocked(startGraphJob).mockImplementation(async () => started)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Prior step, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Dirty prior instruction' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 2' }))
    const row = (await screen.findByText('Target draft')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Run' }))

    // Intent is recorded, but focus/plan stay on the prior draft while flush is in flight.
    await waitFor(() => expect(finishAutosave).toBeTypeOf('function'))
    expect(startGraphJob).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Rename workflow Target draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Target step/ })).not.toBeInTheDocument()

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'prior-only' ? { ...node, instruction: 'Dirty prior instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 55))
    expect(await screen.findByRole('button', { name: 'Rename workflow Target draft' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Target step/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Prior step/ })).not.toBeInTheDocument()
    expect(screen.getByText('Execution started.')).toBeInTheDocument()
  })

  it('abandons a slow home Run draft adopt when a newer open wins focus', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = {
      ...queuedDraft(77, 'Prior draft'),
      graph: {
        nodes: [{ id: 'prior-only', type: 'agent', name: 'Prior step', instruction: 'Old', output_kind: 'text' }],
        edges: [],
      },
    } as never
    const target = {
      ...queuedDraft(55, 'Target draft'),
      graph: {
        nodes: [{ id: 'target-only', type: 'agent', name: 'Target step', instruction: 'Fresh', output_kind: 'text' }],
        edges: [],
      },
    } as never
    const started = {
      ...runningJob(55, 'Target draft'),
      workflow_id: null,
      graph: target.graph,
    } as never
    const winner = {
      ...queuedDraft(99, 'Winner draft'),
      graph: {
        nodes: [{ id: 'winner-only', type: 'agent', name: 'Winner step', instruction: 'Keep', output_kind: 'text' }],
        edges: [],
      },
    } as never
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior, target, winner] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 55) return started
      if (jobId === 99) return winner
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId === 77) {
        const saved = {
          ...prior,
          title: body.title ?? prior.title,
          graph: body.graph ?? prior.graph,
        }
        if (!autosaveStarted) {
          autosaveStarted = true
          return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
        }
        return Promise.resolve(saved)
      }
      if (jobId === 99) {
        return Promise.resolve({
          ...winner,
          title: body.title ?? winner.title,
          graph: body.graph ?? winner.graph,
        })
      }
      return Promise.reject(new Error(`unexpected save ${jobId}`))
    })
    vi.mocked(startGraphJob).mockImplementation(async () => started)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Prior step, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Dirty prior instruction' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 3' }))
    const targetRow = (await screen.findByText('Target draft')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(targetRow).getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(finishAutosave).toBeTypeOf('function'))
    expect(startGraphJob).not.toHaveBeenCalled()

    // Newer explicit open wins generation while Run draft is still flushing.
    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={99}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'prior-only' ? { ...node, instruction: 'Dirty prior instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Winner draft' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Winner step/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Target draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Target step/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Execution started.')).not.toBeInTheDocument()
    expect(startGraphJob).not.toHaveBeenCalled()
  })

  function queuedDraft(id: number, title: string) {
    return {
      id,
      project_id: 1,
      project_slug: 'owner-personal',
      workflow_id: null,
      session_id: id,
      title,
      status: 'queued',
      input: {},
      engine: 'graph',
      graph: {
        nodes: [{ id: 'only', type: 'agent', name: 'Only', instruction: 'Draft', output_kind: 'text' }],
        edges: [],
      },
      node_states: [],
    } as never
  }

  it('keeps Run now selection when a dirty prior draft autosave flushes during handoff', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Dirty draft')
    const spawned = runningJob(99, 'Nightly publish run')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 99) return spawned
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Dirty draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    const dirtyInstruction = await screen.findByRole('textbox', { name: 'Node instruction' })
    fireEvent.change(dirtyInstruction, { target: { value: 'Dirty instruction' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))
    expect(finishAutosave).toBeTypeOf('function')

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Workflows 2' }))
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())

    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))
    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Dirty instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getGraphJob).mock.calls.filter(call => call[1] === 99)).toHaveLength(1)
  })

  it('keeps ordinary View open when a dirty prior draft autosave flushes during handoff', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Dirty draft')
    const target = queuedDraft(88, 'Other draft')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior, target] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 88) return target
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Dirty draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    const otherInstruction = await screen.findByRole('textbox', { name: 'Node instruction' })
    fireEvent.change(otherInstruction, { target: { value: 'Dirty instruction' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 2' }))
    const row = (await screen.findByText('Other draft')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Edit' }))

    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 88))
    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Dirty instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Other draft' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Dirty draft' })).not.toBeInTheDocument()
  })

  it('restores prior focus after failed ordinary open so poll, autosave, and Start keep working', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Prior draft')
    const started = runningJob(77, 'Prior draft')
    const refreshed = { ...started, title: 'Prior draft live' } as never
    let launched = false
    let savedGraph = prior.graph
    let gets77 = 0
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior, queuedDraft(88, 'Missing draft')] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) {
        gets77 += 1
        if (launched) return { ...refreshed, graph: savedGraph }
        return { ...prior, graph: savedGraph }
      }
      if (jobId === 88) throw new Error('job gone')
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation(async (_token, jobId, body) => {
      if (jobId !== 77) throw new Error(`unexpected save ${jobId}`)
      if (body.graph) savedGraph = body.graph
      return {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? savedGraph,
      }
    })
    vi.mocked(startGraphJob).mockImplementation(async () => {
      launched = true
      return { ...started, graph: savedGraph }
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    const keepInstruction = await screen.findByRole('textbox', { name: 'Node instruction' })
    fireEvent.change(keepInstruction, { target: { value: 'Keep working' } })
    expect(screen.getByText('Saving…')).toBeInTheDocument()

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={88}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByText('Error: job gone')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '▶ Run' }))
    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 77))
    expect(await screen.findByText('Execution started.')).toBeInTheDocument()

    const getsAfterStart = gets77
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    await waitFor(() => expect(gets77).toBeGreaterThan(getsAfterStart))
    expect(await screen.findByRole('heading', { name: 'Prior draft live' })).toBeInTheDocument()
  })

  it('restores prior focus after failed Run now selection so autosave and live poll keep working', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Prior draft')
    const started = runningJob(77, 'Prior draft')
    let launched = false
    let savedGraph = prior.graph
    let gets77 = 0
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) {
        gets77 += 1
        if (launched) return { ...started, graph: savedGraph }
        return { ...prior, graph: savedGraph }
      }
      if (jobId === 99) throw new Error('network down')
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation(async (_token, jobId, body) => {
      if (jobId !== 77) throw new Error(`unexpected save ${jobId}`)
      if (body.graph) savedGraph = body.graph
      return {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? savedGraph,
      }
    })
    vi.mocked(runScheduleNow).mockResolvedValue(runningJob(99, 'Nightly publish run'))
    vi.mocked(startGraphJob).mockImplementation(async () => {
      launched = true
      return { ...started, graph: savedGraph }
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    const runNowInstruction = await screen.findByRole('textbox', { name: 'Node instruction' })
    fireEvent.change(runNowInstruction, { target: { value: 'Keep working' } })
    expect(screen.getByText('Saving…')).toBeInTheDocument()

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())

    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not select spawned graph job 99/)
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '▶ Run' }))
    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 77))
    expect(await screen.findByText('Execution started.')).toBeInTheDocument()

    const getsAfterStart = gets77
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    await waitFor(() => expect(gets77).toBeGreaterThan(getsAfterStart))
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
  })

  it('does not let a slow abandoned open adopt autosave over a newer successful open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Dirty draft')
    const abandoned = queuedDraft(88, 'Abandoned draft')
    const winner = queuedDraft(99, 'Winner draft')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 88) return abandoned
      if (jobId === 99) return winner
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId === 77) {
        const saved = {
          ...prior,
          title: body.title ?? prior.title,
          graph: body.graph ?? prior.graph,
        }
        if (!autosaveStarted) {
          autosaveStarted = true
          return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
        }
        return Promise.resolve(saved)
      }
      if (jobId === 99) {
        return Promise.resolve({
          ...winner,
          title: body.title ?? winner.title,
          graph: body.graph ?? winner.graph,
        })
      }
      return Promise.reject(new Error(`unexpected save ${jobId}`))
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Dirty draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Dirty instruction' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={88}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 88))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={99}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Dirty instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Winner draft' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Abandoned draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Dirty draft' })).not.toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Winner edit' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalledWith(
      't',
      99,
      expect.objectContaining({ graph: expect.anything() }),
    ))
    expect(updateGraphPlan).not.toHaveBeenCalledWith('t', 88, expect.anything())
  })

  it('restores prior focus after a newer open fails without keeping abandoned autosave bindings', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Prior draft')
    const abandoned = queuedDraft(88, 'Abandoned draft')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    let rejectNewer: ((reason?: unknown) => void) | undefined
    let savedGraph = prior.graph
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 77) return Promise.resolve({ ...prior, graph: savedGraph })
      if (jobId === 88) return Promise.resolve(abandoned)
      if (jobId === 99) {
        return new Promise((_resolve, reject) => { rejectNewer = reject })
      }
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      if (body.graph) savedGraph = body.graph
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? savedGraph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Keep prior' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={88}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 88))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={99}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    await waitFor(() => expect(rejectNewer).toBeTypeOf('function'))

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Keep prior' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    await act(async () => {
      rejectNewer?.(new Error('newer open failed'))
      await Promise.resolve()
    })

    expect(await screen.findByText('Error: newer open failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Abandoned draft' })).not.toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Prior still editable' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalledWith(
      't',
      77,
      expect.objectContaining({
        graph: expect.objectContaining({
          nodes: expect.arrayContaining([
            expect.objectContaining({ instruction: 'Prior still editable' }),
          ]),
        }),
      }),
    ))
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())
  })

  it('clears Saving when an in-flight prior save finishes while a failed ordinary open holds focus', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Prior draft')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    let rejectOpen: ((reason?: unknown) => void) | undefined
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior, queuedDraft(88, 'Missing draft')] })
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 77) return Promise.resolve(prior)
      if (jobId === 88) {
        return new Promise((_resolve, reject) => { rejectOpen = reject })
      }
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'In flight edit' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))
    expect(screen.getByText('Saving…')).toBeInTheDocument()

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={88}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    await waitFor(() => expect(rejectOpen).toBeTypeOf('function'))
    expect(screen.getByText('Saving…')).toBeInTheDocument()

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'In flight edit' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    await act(async () => {
      rejectOpen?.(new Error('job gone'))
      await Promise.resolve()
    })
    expect(await screen.findByText('Error: job gone')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()
    expect(screen.getByText('Saved ✓')).toBeInTheDocument()
  })

  it('keeps prior draft healthy when an in-flight save finishes during a failed Run now open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Prior draft')
    const started = runningJob(77, 'Prior draft')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    let rejectSpawned: ((reason?: unknown) => void) | undefined
    let launched = false
    let savedGraph = prior.graph
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior] })
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 77) {
        return Promise.resolve(launched ? { ...started, graph: savedGraph } : { ...prior, graph: savedGraph })
      }
      if (jobId === 99) {
        return new Promise((_resolve, reject) => { rejectSpawned = reject })
      }
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      if (body.graph) savedGraph = body.graph
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? savedGraph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })
    vi.mocked(runScheduleNow).mockResolvedValue(runningJob(99, 'Nightly publish run'))
    vi.mocked(startGraphJob).mockImplementation(async () => {
      launched = true
      return { ...started, graph: savedGraph }
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'In flight edit' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))
    expect(screen.getByText('Saving…')).toBeInTheDocument()

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    await waitFor(() => expect(rejectSpawned).toBeTypeOf('function'))

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'In flight edit' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    await act(async () => {
      rejectSpawned?.(new Error('network down'))
      await Promise.resolve()
    })

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not select spawned graph job 99/)
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Prior draft' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '▶ Run' }))
    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 77))
    expect(await screen.findByText('Execution started.')).toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
  })

  it('does not let an older failed open override a newer navigation focus', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const first = queuedDraft(77, 'First plan')
    const third = runningJob(99, 'Third plan')
    const thirdLive = { ...third, title: 'Third plan live' } as never
    let rejectSecond: ((reason?: unknown) => void) | undefined
    let thirdGets = 0
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 77) return Promise.resolve(first)
      if (jobId === 88) {
        return new Promise((_resolve, reject) => { rejectSecond = reject })
      }
      if (jobId === 99) {
        thirdGets += 1
        return Promise.resolve(thirdGets > 1 ? thirdLive : third)
      }
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow First plan' })).toBeInTheDocument()

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={88}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    await waitFor(() => expect(rejectSecond).toBeTypeOf('function'))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={99}
        onPendingConsumed={onPendingConsumed}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Third plan' })).toBeInTheDocument()

    await act(async () => {
      rejectSecond?.(new Error('stale open failed'))
      await Promise.resolve()
    })

    expect(screen.getByRole('button', { name: 'Rename workflow Third plan' })).toBeInTheDocument()
    expect(screen.queryByText('Error: stale open failed')).not.toBeInTheDocument()

    const getsAfterOpen = thirdGets
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600)
    })
    await waitFor(() => expect(thirdGets).toBeGreaterThan(getsAfterOpen))
    expect(await screen.findByRole('button', { name: 'Rename workflow Third plan live' })).toBeInTheDocument()
  })

  it('keeps createFromTemplate and Runs View handoff on one consistent job after a slow prior flush', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Dirty draft')
    const viewed = runningJob(88, 'Viewed run')
    const created = queuedDraft(55, 'Publish on demand')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior, viewed] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 88) return viewed
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })
    vi.mocked(createGraphJob).mockResolvedValue(created)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Dirty draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Dirty instruction' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Runs 1' }))
    const runRow = (await screen.findByText('Viewed run')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(runRow).getByRole('button', { name: 'View' }))
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 88))

    fireEvent.click(screen.getByRole('tab', { name: 'Workflows 2' }))
    const templateRow = (await screen.findByText('Publish on demand')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(templateRow).getByRole('button', { name: 'Edit' }))
    await waitFor(() => expect(createGraphJob).toHaveBeenCalled())

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Dirty instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Publish on demand' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Viewed run' })).not.toBeInTheDocument()

    // Title rename proves autosave identity tracks the focused create.
    fireEvent.click(screen.getByRole('button', { name: 'Rename workflow Publish on demand' }))
    const titleInput = await screen.findByRole('textbox', { name: 'Workflow name' })
    fireEvent.change(titleInput, { target: { value: 'Created still saves' } })
    fireEvent.blur(titleInput)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalledWith(
      't',
      55,
      expect.objectContaining({ title: 'Created still saves' }),
    ))
    expect(updateGraphPlan).not.toHaveBeenCalledWith('t', 88, expect.anything())
  })

  it('lets Schedule Run now win over a slow pendingDraft adopt without duplicate spawns', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Dirty draft')
    const spawned = runningJob(99, 'Nightly publish run')
    const drafted = queuedDraft(55, 'Architect draft')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 99) return spawned
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })
    vi.mocked(createGraphJob).mockResolvedValue(drafted)
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    const draft = {
      name: 'Architect draft',
      description: 'from architect',
      category: 'other',
      graph: drafted.graph,
    }
    let pendingJobId: number | null = 77
    let pendingDraft: typeof draft | null = null
    const onPendingConsumed = vi.fn(() => { pendingJobId = null })
    const onDraftConsumed = vi.fn(() => { pendingDraft = null })
    const view = () => (
      <GraphScreen
        {...screenBase}
        pendingJobId={pendingJobId}
        pendingDraft={pendingDraft}
        onPendingConsumed={onPendingConsumed}
        onDraftConsumed={onDraftConsumed}
        backNonce={pendingJobId ? 0 : 1}
      />
    )
    const { rerender } = render(view())
    expect(await screen.findByRole('button', { name: 'Rename workflow Dirty draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Dirty instruction' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    // Open Schedules before the draft create claims busy so Run now stays reachable.
    pendingJobId = null
    rerender(view())
    fireEvent.click(await screen.findByRole('tab', { name: 'Workflows 2' }))
    const row = (await screen.findByText('Nightly publish')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    pendingDraft = draft
    rerender(view())
    await waitFor(() => expect(createGraphJob).toHaveBeenCalled())
    // Draft has begun focus and is blocked on the prior flush; Run now must still win.
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    // Parent clears deferred draft when handoff starts (same as App).
    await waitFor(() => expect(onDraftConsumed).toHaveBeenCalled())
    rerender(view())

    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Dirty instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Architect draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('disables Runs View while a create is in flight', async () => {
    const created = queuedDraft(55, 'Publish on demand')
    let finishCreate: ((value: typeof created) => void) | undefined
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [runningJob(88, 'Viewed run')] })
    vi.mocked(createGraphJob).mockImplementation(() => new Promise(resolve => { finishCreate = resolve }))

    render(<GraphScreen {...screenBase} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Workflows 2' }))
    const templateRow = (await screen.findByText('Publish on demand')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(templateRow).getByRole('button', { name: 'Edit' }))
    await waitFor(() => expect(createGraphJob).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('tab', { name: 'Runs 1' }))
    const view = await screen.findByRole('button', { name: 'View' })
    expect(view).toBeDisabled()

    await act(async () => {
      finishCreate?.(created)
      await Promise.resolve()
    })
    expect(await screen.findByRole('button', { name: 'Rename workflow Publish on demand' })).toBeInTheDocument()
  })

  it('ignores scrim and Escape during Run now spawn and still selects the exact job once', async () => {
    const spawned = runningJob(99, 'Nightly publish run')
    let finishSpawn: ((value: typeof spawned) => void) | undefined
    vi.mocked(runScheduleNow).mockImplementation(() => new Promise(resolve => { finishSpawn = resolve }))
    vi.mocked(getGraphJob).mockResolvedValue(spawned)

    render(<GraphScreen {...screenBase} />)
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    const dialog = await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))

    fireEvent.click(dialog.parentElement as HTMLElement)
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Opening run...' })).toBeDisabled()

    await act(async () => {
      finishSpawn?.(spawned)
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(getGraphJob).toHaveBeenCalledWith('t', 99)
  })

  it('blocks pendingJobId and pendingDraft stage changes during Run now spawn/selection', async () => {
    const spawned = runningJob(99, 'Nightly publish run')
    const drafted = queuedDraft(55, 'Architect draft')
    const pending = runningJob(41, 'Deep link run')
    let finishSpawn: ((value: typeof spawned) => void) | undefined
    let finishLoad: ((value: typeof spawned) => void) | undefined
    vi.mocked(runScheduleNow).mockImplementation(() => new Promise(resolve => { finishSpawn = resolve }))
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 99) return new Promise(resolve => { finishLoad = resolve })
      if (jobId === 41) return Promise.resolve(pending)
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })
    vi.mocked(createGraphJob).mockResolvedValue(drafted)

    const draft = {
      name: 'Architect draft',
      description: 'from architect',
      category: 'other',
      graph: drafted.graph,
    }
    let pendingJobId: number | null = null
    let pendingDraft: typeof draft | null = null
    const onPendingConsumed = vi.fn(() => { pendingJobId = null })
    const onDraftConsumed = vi.fn(() => { pendingDraft = null })
    const view = () => (
      <GraphScreen
        {...screenBase}
        pendingJobId={pendingJobId}
        pendingDraft={pendingDraft}
        onPendingConsumed={onPendingConsumed}
        onDraftConsumed={onDraftConsumed}
      />
    )
    const { rerender } = render(view())
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))

    // Competing navigation arrives mid-spawn and must be dropped, not applied.
    onPendingConsumed.mockClear()
    onDraftConsumed.mockClear()
    pendingJobId = 41
    pendingDraft = draft
    rerender(view())
    await waitFor(() => expect(onPendingConsumed).toHaveBeenCalled())
    await waitFor(() => expect(onDraftConsumed).toHaveBeenCalled())
    expect(pendingJobId).toBeNull()
    expect(pendingDraft).toBeNull()
    rerender(view())

    expect(createGraphJob).not.toHaveBeenCalled()
    expect(getGraphJob).not.toHaveBeenCalledWith('t', 41)
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    await act(async () => {
      finishSpawn?.(spawned)
      await Promise.resolve()
    })
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))

    // Competing navigation stays blocked through selection, not only spawn.
    expect(createGraphJob).not.toHaveBeenCalled()
    expect(getGraphJob).not.toHaveBeenCalledWith('t', 41)

    await act(async () => {
      finishLoad?.(spawned)
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Architect draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Deep link run' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(createGraphJob).not.toHaveBeenCalled()
  })

  it('clears Run now handoff when selection validation throws before openJob', async () => {
    const spawned = {
      ...runningJob(99, 'Nightly publish run'),
      engine: 'linear',
    } as never
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    render(<GraphScreen {...screenBase} />)
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Schedule returned non-graph job 99/)
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    expect(getGraphJob).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    })
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(within(row).getByRole('button', { name: 'Schedules' })).toBeEnabled()
  })

  it('clears Run now handoff when spawned job is outside the owner project', async () => {
    const spawned = {
      ...runningJob(99, 'Nightly publish run'),
      project_slug: 'other-project',
    } as never
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    render(<GraphScreen {...screenBase} />)
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const row = screen.getByText('Nightly publish').closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/outside the workflow owner project/)
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    expect(getGraphJob).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    })
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
  })

  it('cancels in-flight Save as template prepare when Run now handoff starts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const prior = queuedDraft(77, 'Dirty draft')
    const spawned = runningJob(99, 'Nightly publish run')
    let finishAutosave: ((value: typeof prior) => void) | undefined
    let autosaveStarted = false
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [prior] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 77) return prior
      if (jobId === 99) return spawned
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(updateGraphPlan).mockImplementation((_token, jobId, body) => {
      if (jobId !== 77) return Promise.reject(new Error(`unexpected save ${jobId}`))
      const saved = {
        ...prior,
        title: body.title ?? prior.title,
        graph: body.graph ?? prior.graph,
      }
      if (!autosaveStarted) {
        autosaveStarted = true
        return new Promise<typeof prior>(resolve => { finishAutosave = resolve }).then(() => saved)
      }
      return Promise.resolve(saved)
    })
    vi.mocked(runScheduleNow).mockResolvedValue(spawned)

    const onPendingConsumed = vi.fn()
    const { rerender } = render(
      <GraphScreen
        {...screenBase}
        pendingJobId={77}
        onPendingConsumed={onPendingConsumed}
        backNonce={0}
      />,
    )
    expect(await screen.findByRole('button', { name: 'Rename workflow Dirty draft' })).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Only, Pending' }))
    fireEvent.change(await screen.findByRole('textbox', { name: 'Node instruction' }), {
      target: { value: 'Dirty instruction' },
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    await waitFor(() => expect(autosaveStarted).toBe(true))

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={1}
      />,
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Workflows 2' }))
    const row = (await screen.findByText('Nightly publish')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Schedules' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    // Prepare can still be invoked while the schedule dialog is mounted; handoff must cancel it.
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    fireEvent.click(await screen.findByRole('button', { name: '★ Save as template' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))

    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 99))

    await act(async () => {
      finishAutosave?.({
        ...prior,
        graph: {
          ...prior.graph,
          nodes: prior.graph.nodes.map((node: { id: string }) => (
            node.id === 'only' ? { ...node, instruction: 'Dirty instruction' } : node
          )),
        },
      })
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Save as Workflow' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)

    rerender(
      <GraphScreen
        {...screenBase}
        pendingJobId={null}
        onPendingConsumed={onPendingConsumed}
        backNonce={2}
      />,
    )
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Workflows 2' })).toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: 'Save as Workflow' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Workflows 2' }))
    expect(screen.getByText('Nightly publish')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Save as Workflow' })).not.toBeInTheDocument()
  })

  it('cancels in-flight Edit when Run now handoff starts and still selects once', async () => {
    const edited = queuedDraft(88, 'Edited draft')
    const spawned = runningJob(99, 'Nightly publish run')
    let finishEdit: ((value: typeof edited) => void) | undefined
    let finishSpawn: ((value: typeof spawned) => void) | undefined
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [edited] })
    vi.mocked(getGraphJob).mockImplementation((_token, jobId) => {
      if (jobId === 88) return new Promise(resolve => { finishEdit = resolve })
      if (jobId === 99) return Promise.resolve(spawned)
      return Promise.reject(new Error(`unexpected job ${jobId}`))
    })
    vi.mocked(runScheduleNow).mockImplementation(() => new Promise(resolve => { finishSpawn = resolve }))

    render(<GraphScreen {...screenBase} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Drafts 1' }))
    const editRow = (await screen.findByText('Edited draft')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(editRow).getByRole('button', { name: 'Edit' }))
    await waitFor(() => expect(getGraphJob).toHaveBeenCalledWith('t', 88))

    fireEvent.click(screen.getByRole('tab', { name: 'Workflows 2' }))
    const scheduleRow = (await screen.findByText('Nightly publish')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(scheduleRow).getByRole('button', { name: 'Schedules' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))

    await act(async () => {
      finishEdit?.(edited)
      await Promise.resolve()
    })
    expect(screen.queryByRole('button', { name: 'Rename workflow Edited draft' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    await act(async () => {
      finishSpawn?.(spawned)
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Edited draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
  })

  it('keeps Run now busy locked when cancelled runDraft finally cannot clear it', async () => {
    const draft = queuedDraft(55, 'Home draft')
    const started = runningJob(55, 'Home draft')
    const spawned = runningJob(99, 'Nightly publish run')
    let finishStart: ((value: typeof started) => void) | undefined
    let finishSpawn: ((value: typeof spawned) => void) | undefined
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [draft] })
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 55) return draft
      if (jobId === 99) return spawned
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(startGraphJob).mockImplementation(() => new Promise(resolve => { finishStart = resolve }))
    vi.mocked(runScheduleNow).mockImplementation(() => new Promise(resolve => { finishSpawn = resolve }))

    render(<GraphScreen {...screenBase} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Workflows 2' }))
    const scheduleRow = (await screen.findByText('Nightly publish')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(scheduleRow).getByRole('button', { name: 'Schedules' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    // Start runDraft under the mounted schedule dialog, then cancel it with Run now.
    fireEvent.click(screen.getByRole('tab', { name: 'Drafts 1' }))
    const draftRow = (await screen.findByText('Home draft')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(draftRow).getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 55))

    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))

    await act(async () => {
      finishStart?.(started)
      await Promise.resolve()
    })
    // Cancelled start path must not clear the parent schedule-run-now lock.
    expect(within(draftRow).getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(within(draftRow).getByRole('button', { name: 'Edit' })).toBeDisabled()
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename workflow Home draft' })).not.toBeInTheDocument()

    await act(async () => {
      finishSpawn?.(spawned)
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(startGraphJob).toHaveBeenCalledTimes(1)
  })

  async function raceCreatePathUnderScheduleDialog(opts: {
    startCreate: () => void
    created: ReturnType<typeof queuedDraft>
    createdTitle: string
  }) {
    const spawned = runningJob(99, 'Nightly publish run')
    let finishCreate: ((value: typeof opts.created) => void) | undefined
    let finishSpawn: ((value: typeof spawned) => void) | undefined
    vi.mocked(getGraphJob).mockImplementation(async (_token, jobId) => {
      if (jobId === 99) return spawned
      if (jobId === opts.created.id) return opts.created
      throw new Error(`unexpected job ${jobId}`)
    })
    vi.mocked(createGraphJob).mockImplementation(() => new Promise(resolve => { finishCreate = resolve }))
    vi.mocked(runScheduleNow).mockImplementation(() => new Promise(resolve => { finishSpawn = resolve }))

    render(<GraphScreen {...screenBase} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Workflows 2' }))
    const scheduleRow = (await screen.findByText('Nightly publish')).closest('[role="row"]') as HTMLElement
    fireEvent.click(within(scheduleRow).getByRole('button', { name: 'Schedules' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    opts.startCreate()
    await waitFor(() => expect(createGraphJob).toHaveBeenCalledTimes(1))

    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(runScheduleNow).toHaveBeenCalledTimes(1))

    await act(async () => {
      finishCreate?.(opts.created)
      await Promise.resolve()
    })
    // Create must not steal focus/stage while handoff owns selection.
    expect(screen.queryByRole('button', { name: `Rename workflow ${opts.createdTitle}` })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()

    await act(async () => {
      finishSpawn?.(spawned)
      await Promise.resolve()
    })

    expect(await screen.findByRole('button', { name: 'Rename workflow Nightly publish run' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Rename workflow ${opts.createdTitle}` })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Schedule Nightly publish' })).not.toBeInTheDocument()
    expect(runScheduleNow).toHaveBeenCalledTimes(1)
    expect(createGraphJob).toHaveBeenCalledTimes(1)
  }

  it('keeps Run now selection when editTemplate create finishes under the schedule dialog', async () => {
    const created = queuedDraft(88, 'Publish on demand')
    await raceCreatePathUnderScheduleDialog({
      created,
      createdTitle: 'Publish on demand',
      startCreate: () => {
        const editRow = screen.getByText('Publish on demand').closest('[role="row"]') as HTMLElement
        fireEvent.click(within(editRow).getByRole('button', { name: 'Edit' }))
      },
    })
  })

  it('keeps Run now selection when createFromTemplate finishes under the schedule dialog', async () => {
    const created = queuedDraft(88, 'Publish on demand')
    await raceCreatePathUnderScheduleDialog({
      created,
      createdTitle: 'Publish on demand',
      startCreate: () => {
        const runRow = screen.getByText('Publish on demand').closest('[role="row"]') as HTMLElement
        fireEvent.click(within(runRow).getByRole('button', { name: 'Run' }))
      },
    })
  })

  it('keeps Run now selection when newPlan create finishes under the schedule dialog', async () => {
    const created = queuedDraft(88, 'Untitled plan')
    await raceCreatePathUnderScheduleDialog({
      created,
      createdTitle: 'Untitled plan',
      startCreate: () => {
        fireEvent.click(screen.getByRole('button', { name: /New/ }))
      },
    })
  })
})

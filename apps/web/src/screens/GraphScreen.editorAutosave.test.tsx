import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  approveGraphNode,
  getGraphJob,
  saveGraphTemplate,
  startGraphJob,
  updateGraphPlan,
} from '../api/graph'
import { stateFor } from '../components/workflows/GraphCanvas'
import type { GraphJob, GraphNodeState, WorkflowGraph } from '../types'
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
vi.mock('../components/workflows/GraphCanvas', () => ({
  GraphCanvas: ({ onMoveNode, onSelect }: {
    onMoveNode: (nodeId: string, x: number, y: number) => void
    onSelect: (nodeId: string) => void
  }) => <>
    <button onClick={() => onMoveNode('step', 120, 80)}>Move node</button>
    <button onClick={() => onSelect('step')}>Select node</button>
    <button onClick={() => onSelect('trigger')}>Select trigger</button>
  </>,
  stateFor: vi.fn(() => undefined),
  statusLabel: (status: string) => status,
}))

const project = {
  slug: 'owner',
  name: 'owner',
  path: '/tmp/owner',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}

const graph: WorkflowGraph = {
  nodes: [{
    id: 'step',
    type: 'agent',
    name: 'Draft',
    instruction: 'Write',
    output_kind: 'text',
  }],
  edges: [],
}

const queuedJob: GraphJob = {
  id: 42,
  session_id: 7,
  title: 'Untitled plan',
  status: 'queued',
  engine: 'graph',
  graph,
  node_states: [{
    id: 1,
    job_id: 42,
    node_id: 'step',
    status: 'pending',
    output_kind: 'text',
    version: 0,
  }],
  project_slug: project.slug,
}

const reviewNodeState: GraphNodeState = {
  id: 1,
  job_id: 55,
  node_id: 'step',
  status: 'review',
  output_kind: 'text',
  version: 0,
}

const reviewJob: GraphJob = {
  ...structuredClone(queuedJob),
  id: 55,
  title: 'Review plan',
  status: 'review',
  node_states: [reviewNodeState],
}

const props = {
  token: 't',
  projects: [project],
  activeProject: project,
  onActiveProject: vi.fn(),
  profiles: [],
  profileId: null as number | null,
  features: { designStudio: false, workflowGraph: true, masterOrchestrator: false },
  activeProfile: null,
  pendingJobId: queuedJob.id,
}

describe('GraphScreen editor autosave actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(stateFor).mockReturnValue(undefined)
    vi.mocked(getGraphJob).mockResolvedValue(structuredClone(queuedJob))
    vi.mocked(updateGraphPlan).mockImplementation(async (_token, _jobId, body) => ({
      ...structuredClone(queuedJob),
      title: body.title ?? queuedJob.title,
      graph: body.graph ?? structuredClone(graph),
    }))
    vi.mocked(startGraphJob).mockResolvedValue({
      ...structuredClone(queuedJob),
      status: 'running',
    })
    vi.mocked(saveGraphTemplate).mockResolvedValue({
      id: 88,
      name: 'Untitled plan',
      description: '',
      category: 'other',
      status: 'active',
      graph: structuredClone(graph),
      inputs: [],
    })
  })

  it('renames inline, autosaves passively, and renders exactly two draft footer actions', async () => {
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    const footer = document.querySelector('.graph-editor-footer') as HTMLElement
    expect(footer).toBeTruthy()
    expect(within(footer).getAllByRole('button')).toHaveLength(2)
    expect(within(footer).getByRole('button', { name: '★ Save as Workflow' })).toBeEnabled()
    expect(within(footer).getByRole('button', { name: '▶ Run' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Save plan' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve plan/i })).not.toBeInTheDocument()
    expect(screen.getByText('Saved ✓')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Rename workflow Untitled plan' }))
    const title = screen.getByRole('textbox', { name: 'Workflow name' })
    fireEvent.change(title, { target: { value: 'Daily research' } })

    expect(screen.getByText('Saving…')).toBeInTheDocument()
    await waitFor(() => {
      expect(updateGraphPlan).toHaveBeenCalledWith('t', 42, {
        title: 'Daily research',
      })
    }, { timeout: 2000 })
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())
  })

  it('blocks Run while autosave is pending, then uses the validated Run dialog', async () => {
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    fireEvent.click(screen.getByRole('button', { name: 'Move node' }))
    const run = screen.getByRole('button', { name: '▶ Run' })
    expect(run).toBeDisabled()
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())
    expect(run).toBeEnabled()
    fireEvent.click(run)

    expect(await screen.findByRole('dialog', { name: 'Run Untitled plan' })).toBeInTheDocument()
    expect(startGraphJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))

    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 42, undefined))
    expect(updateGraphPlan).toHaveBeenCalledWith('t', 42, {
      title: 'Untitled plan',
      graph: {
        ...graph,
        nodes: [{ ...graph.nodes[0], x: 120, y: 80 }],
      },
    })
    expect(vi.mocked(updateGraphPlan).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(startGraphJob).mock.invocationCallOrder[0])
  })

  it('promotes in one click with optional metadata and no modal', async () => {
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    fireEvent.click(screen.getByText('Workflow metadata'))
    fireEvent.change(screen.getByPlaceholderText('e.g. content'), { target: { value: 'research' } })
    fireEvent.change(screen.getByPlaceholderText('What this workflow does'), { target: { value: 'Daily brief' } })
    fireEvent.click(screen.getByRole('button', { name: '★ Save as Workflow' }))

    await waitFor(() => expect(saveGraphTemplate).toHaveBeenCalledWith('t', 42, {
      name: 'Untitled plan',
      description: 'Daily brief',
      category: 'research',
    }))
    expect(screen.queryByRole('dialog', { name: /Save as Workflow/i })).not.toBeInTheDocument()
  })

  it('edits manual intake fields on the trigger and swaps them for schedule settings', async () => {
    const triggerGraph: WorkflowGraph = {
      nodes: [
        {
          id: 'trigger',
          type: 'trigger',
          trigger_kind: 'manual',
          name: 'When I run it',
          instruction: '',
          output_kind: 'json',
          inputs: [],
        },
        ...structuredClone(graph.nodes),
      ],
      edges: [{ from: 'trigger', to: 'step' }],
    }
    const triggerJob: GraphJob = {
      ...structuredClone(queuedJob),
      graph: triggerGraph,
      node_states: [
        {
          id: 2,
          job_id: 42,
          node_id: 'trigger',
          status: 'pending',
          output_kind: 'json',
          version: 0,
        },
        ...structuredClone(queuedJob.node_states),
      ],
    }
    vi.mocked(getGraphJob).mockResolvedValue(triggerJob)
    vi.mocked(updateGraphPlan).mockImplementation(async (_token, _jobId, body) => ({
      ...structuredClone(triggerJob),
      title: body.title ?? triggerJob.title,
      graph: body.graph ?? structuredClone(triggerGraph),
    }))

    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })
    fireEvent.click(screen.getByRole('button', { name: 'Select trigger' }))

    expect(screen.getByRole('group', { name: 'Trigger mode' })).toBeInTheDocument()
    expect(screen.getByText('Intake form')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '+ Add field' }))
    expect(screen.getByRole('textbox', { name: 'Input 1 ID' })).toHaveValue('field')
    fireEvent.change(screen.getByRole('textbox', { name: 'Input 1 label' }), {
      target: { value: 'Topic' },
    })
    fireEvent.blur(screen.getByRole('textbox', { name: 'Input 1 label' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Input 1 ID' }), {
      target: { value: 'topic' },
    })
    fireEvent.blur(screen.getByRole('textbox', { name: 'Input 1 ID' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Input 1 required' }))

    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalledWith(
      't',
      42,
      expect.objectContaining({
        graph: expect.objectContaining({
          nodes: expect.arrayContaining([
            expect.objectContaining({
              id: 'trigger',
              inputs: [{ id: 'topic', label: 'Topic', kind: 'text', required: true }],
            }),
          ]),
        }),
      }),
    ), { timeout: 2000 })

    fireEvent.click(screen.getByRole('button', { name: 'Schedule' }))
    expect(screen.queryByText('Intake form')).not.toBeInTheDocument()
    expect(screen.getByText('Schedule settings')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Cron' })).toHaveValue('0 9 * * *')
    expect(screen.getByText(/scheduled runs never ask for per-run input/i)).toBeInTheDocument()
  })

  it('keeps save false and Run blocked after persistence rejection until Retry succeeds', async () => {
    vi.mocked(updateGraphPlan).mockRejectedValueOnce(new Error('network unavailable'))
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    fireEvent.click(screen.getByRole('button', { name: 'Move node' }))

    expect(await screen.findByText('Not saved')).toBeInTheDocument()
    expect(screen.getByText(/network unavailable/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '▶ Run' })).toBeDisabled()
    const retry = screen.getByRole('button', { name: 'Retry save' })

    vi.mocked(updateGraphPlan).mockResolvedValue({
      ...structuredClone(queuedJob),
      graph: {
        ...graph,
        nodes: [{ ...graph.nodes[0], x: 120, y: 80 }],
      },
    })
    fireEvent.click(retry)

    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '▶ Run' })).toBeEnabled()
  })

  it('keeps duplicate intake ID edits local, marks them unsaved, and blocks Run', async () => {
    const triggerGraph: WorkflowGraph = {
      nodes: [
        {
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
        },
      ],
      edges: [],
    }
    vi.mocked(getGraphJob).mockResolvedValue({
      ...structuredClone(queuedJob),
      graph: triggerGraph,
      node_states: [{
        id: 2,
        job_id: 42,
        node_id: 'trigger',
        status: 'pending',
        output_kind: 'json',
        version: 0,
      }],
    })
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })
    fireEvent.click(screen.getByRole('button', { name: 'Select trigger' }))

    const audienceId = screen.getByRole('textbox', { name: 'Input 2 ID' })
    fireEvent.change(audienceId, { target: { value: 'campaign' } })
    fireEvent.blur(audienceId)

    expect(screen.getByText('ID must be unique.')).toBeInTheDocument()
    expect(screen.getByText('Not saved')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '▶ Run' })).toBeDisabled()
    expect(updateGraphPlan).not.toHaveBeenCalled()
  })

  it('collects required and default intake values before starting a draft', async () => {
    const triggerGraph: WorkflowGraph = {
      nodes: [{
        id: 'trigger',
        type: 'trigger',
        trigger_kind: 'manual',
        name: 'When I run it',
        instruction: '',
        output_kind: 'json',
        inputs: [
          { id: 'campaign', label: 'Campaign', kind: 'text', required: true },
          { id: 'channel', label: 'Channel', kind: 'text', required: false, default: 'email' },
        ],
      }],
      edges: [],
    }
    vi.mocked(getGraphJob).mockResolvedValue({
      ...structuredClone(queuedJob),
      graph: triggerGraph,
      node_states: [{
        id: 2,
        job_id: 42,
        node_id: 'trigger',
        status: 'pending',
        output_kind: 'json',
        version: 0,
      }],
    })
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    fireEvent.click(screen.getByRole('button', { name: '▶ Run' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Campaign' }), {
      target: { value: 'Launch week' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run workflow' }))

    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 42, {
      campaign: 'Launch week',
      channel: 'email',
    }))
  })

  it('flushes the outgoing draft edit when switching drafts inside the debounce window', async () => {
    const jobB: GraphJob = { ...structuredClone(queuedJob), id: 99, title: 'Second plan' }
    vi.mocked(getGraphJob)
      .mockResolvedValueOnce(structuredClone(queuedJob))
      .mockResolvedValueOnce(jobB)

    const { rerender } = render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    fireEvent.click(screen.getByRole('button', { name: 'Rename workflow Untitled plan' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Workflow name' }), { target: { value: 'Daily research' } })
    expect(screen.getByText('Saving…')).toBeInTheDocument()

    // Leave draft 42 for draft 99 before the 700ms autosave timer fires: the queued
    // title edit must reach the server rather than be silently dropped.
    rerender(<GraphScreen {...props} pendingJobId={99} />)

    await waitFor(() => expect(updateGraphPlan).toHaveBeenCalledWith('t', 42, { title: 'Daily research' }))
    await screen.findByRole('heading', { name: 'Second plan' })
  })

  it('flushes an inline rename before a review-stage node action', async () => {
    vi.mocked(getGraphJob).mockResolvedValue(structuredClone(reviewJob))
    vi.mocked(stateFor).mockReturnValue(reviewNodeState)
    vi.mocked(approveGraphNode).mockResolvedValue({
      ...structuredClone(reviewJob),
      title: 'Renamed review',
    })

    render(<GraphScreen {...props} pendingJobId={55} />)
    await screen.findByRole('heading', { name: 'Review plan' })

    fireEvent.click(screen.getByRole('button', { name: 'Rename workflow Review plan' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Workflow name' }), { target: { value: 'Renamed review' } })

    fireEvent.click(screen.getByRole('button', { name: 'Select node' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Approve node' }))

    await waitFor(() => expect(approveGraphNode).toHaveBeenCalledWith('t', 55, 'step'))
    expect(updateGraphPlan).toHaveBeenCalledWith('t', 55, { title: 'Renamed review' })
    expect(vi.mocked(updateGraphPlan).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(approveGraphNode).mock.invocationCallOrder[0])
  })
})

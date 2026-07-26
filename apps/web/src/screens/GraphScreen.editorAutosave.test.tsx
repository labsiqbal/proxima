import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getGraphJob,
  saveGraphTemplate,
  startGraphJob,
  updateGraphPlan,
} from '../api/graph'
import type { GraphJob, WorkflowGraph } from '../types'
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
  GraphCanvas: ({ onMoveNode }: { onMoveNode: (nodeId: string, x: number, y: number) => void }) =>
    <button onClick={() => onMoveNode('step', 120, 80)}>Move node</button>,
  stateFor: () => undefined,
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

const props = {
  token: 't',
  projects: [project],
  activeProject: project,
  onActiveProject: vi.fn(),
  profiles: [],
  profileId: null as number | null,
  features: { designStudio: false, workflowGraph: true },
  activeProfile: null,
  pendingJobId: queuedJob.id,
}

describe('GraphScreen editor autosave actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
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
    expect(within(footer).getByRole('button', { name: '★ Simpan sbg Workflow' })).toBeEnabled()
    expect(within(footer).getByRole('button', { name: '▶ Jalankan' })).toBeEnabled()
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

  it('flushes a pending canvas edit before Run without a manual-save gate', async () => {
    render(<GraphScreen {...props} />)
    await screen.findByRole('heading', { name: 'Untitled plan' })

    fireEvent.click(screen.getByRole('button', { name: 'Move node' }))
    const run = screen.getByRole('button', { name: '▶ Jalankan' })
    expect(run).toBeEnabled()
    fireEvent.click(run)

    await waitFor(() => expect(startGraphJob).toHaveBeenCalledWith('t', 42))
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
    fireEvent.click(screen.getByRole('button', { name: '★ Simpan sbg Workflow' }))

    await waitFor(() => expect(saveGraphTemplate).toHaveBeenCalledWith('t', 42, {
      name: 'Untitled plan',
      description: 'Daily brief',
      category: 'research',
      inputs: [],
    }))
    expect(screen.queryByRole('dialog', { name: /Save as Workflow/i })).not.toBeInTheDocument()
  })
})

import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getGraphJob, listGraphTemplates } from '../api/graph'
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

describe('GraphScreen how-it-runs badges', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
})

import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listGraphTemplates } from '../api/graph'
import { listSchedules } from '../api/schedules'
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
          nodes: [],
          edges: [],
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

  it('splits manual and scheduled workflows using real schedule data', async () => {
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
    const manual = screen.getByRole('table', { name: 'Manual workflows' })
    const scheduled = screen.getByRole('table', { name: 'Scheduled workflows' })
    expect(within(manual).getByText('Publish on demand')).toBeInTheDocument()
    expect(within(manual).getByText('▷ Manual')).toBeInTheDocument()
    expect(within(scheduled).getByText('Nightly publish')).toBeInTheDocument()
    expect(within(scheduled).getByText(/Every hour|0 \* \* \* \*/)).toBeInTheDocument()
    expect(within(scheduled).getByRole('button', { name: 'Pause Nightly publish' })).toBeInTheDocument()

    fireEvent.click(within(scheduled).getByRole('button', { name: 'Schedule' }))
    expect(await screen.findByRole('dialog', { name: 'Schedule Nightly publish' })).toBeInTheDocument()
  })
})

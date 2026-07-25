import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
      ],
    })
    vi.mocked(listSchedules).mockResolvedValue([
      { id: 1, workflow_id: 10, cron: '0 * * * *', enabled: true } as never,
    ])
  })

  it('shows Manual + Scheduled with short cron from real schedule data', async () => {
    render(
      <GraphScreen
        token="t"
        projects={[project]}
        activeProject={project}
        onActiveProject={vi.fn()}
        profiles={[]}
        profileId={null}
        features={{ designStudio: false, workflowGraph: true }}
        activeProfile={null}
      />,
    )
    await waitFor(() => expect(screen.getByText('Nightly publish')).toBeInTheDocument())
    const kinds = document.querySelector('.graph-run-kinds')
    expect(kinds).toBeTruthy()
    expect(kinds?.textContent).toMatch(/Manual/)
    expect(kinds?.textContent).toMatch(/Scheduled/)
    expect(kinds?.textContent).toMatch(/Every hour|0 \* \* \* \*/)
  })
})

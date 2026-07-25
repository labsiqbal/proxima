import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
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
vi.mock('../api/jobs', () => ({
  getJobDiff: vi.fn(),
}))
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

describe('GraphScreen home header', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('has no local project dropdown; global shell switcher is enough', async () => {
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

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflows' })).toBeInTheDocument()
    })

    const header = document.querySelector('.graph-header') as HTMLElement
    expect(header).toBeTruthy()
    expect(within(header).queryByRole('button', { name: /owner \(personal\)/i })).toBeNull()
    expect(header.querySelector('.dd')).toBeNull()
    expect(screen.getByText(/Building in/i)).toBeInTheDocument()
    expect(screen.getByText('owner (personal)')).toBeInTheDocument()
  })
})

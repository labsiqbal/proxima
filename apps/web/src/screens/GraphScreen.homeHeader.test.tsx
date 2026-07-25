import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { getGraphJob, listGraphJobs } from '../api/graph'
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
  slug: 'owner',
  name: 'owner (personal)',
  path: '/tmp/owner',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}

const baseProps = {
  token: 't',
  projects: [project],
  activeProject: project,
  onActiveProject: vi.fn(),
  profiles: [],
  profileId: null as number | null,
  features: { designStudio: false, workflowGraph: true },
  activeProfile: null,
}

describe('GraphScreen home header', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('has no local project dropdown and does not dump project names on the home surface', async () => {
    render(<GraphScreen {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Workflows' })).toBeInTheDocument()
    })

    const header = document.querySelector('.graph-header') as HTMLElement
    expect(header).toBeTruthy()
    expect(within(header).queryByRole('button', { name: /owner \(personal\)/i })).toBeNull()
    expect(header.querySelector('.dd')).toBeNull()
    expect(screen.queryByText(/Building in/i)).not.toBeInTheDocument()
    expect(screen.queryByText('owner (personal)')).not.toBeInTheDocument()
    expect(document.querySelector('.graph-project-tag')).toBeNull()
  })

  it('shows a name-free lock indicator in the open-plan header, not the project display name', async () => {
    vi.mocked(listGraphJobs).mockResolvedValue({
      items: [{
        id: 42,
        title: 'Untitled plan',
        status: 'queued',
        project_slug: project.slug,
        node_states: [],
      } as never],
    })
    vi.mocked(getGraphJob).mockResolvedValue({
      id: 42,
      title: 'Untitled plan',
      status: 'queued',
      project_slug: project.slug,
      plan: { nodes: [], edges: [] },
      node_states: [],
    } as never)

    render(<GraphScreen {...baseProps} pendingJobId={42} />)

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Untitled plan' })).toBeInTheDocument()
    })

    const lock = document.querySelector('.graph-project-lock') as HTMLElement
    expect(lock).toBeTruthy()
    expect(lock).toHaveAttribute('aria-label', 'Project locked to this plan')
    expect(lock.textContent?.trim()).toBe('')
    expect(screen.queryByText('owner (personal)')).not.toBeInTheDocument()
  })
})

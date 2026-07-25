import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listGraphTemplates, setGraphTemplateStatus } from '../api/graph'
import { confirmDialog } from '../components/ui/Dialog'
import { GraphScreen } from './GraphScreen'

vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn().mockResolvedValue({ items: [] }),
  listGraphTemplates: vi.fn(),
  getGraphJob: vi.fn(),
  createGraphJob: vi.fn(),
  deleteGraphJob: vi.fn(),
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
vi.mock('../components/ui/Dialog', () => ({
  confirmDialog: vi.fn().mockResolvedValue(true),
}))

const project = {
  slug: 'owner-personal',
  name: 'owner (personal)',
  path: '/tmp/owner',
  owner: 'owner',
  role: 'owner',
  visibility: 'private' as const,
}

const active = {
  id: 10,
  name: 'Publish notes',
  description: '',
  status: 'active',
  project_id: 1,
  project_slug: project.slug,
  graph: { nodes: [], edges: [] },
  inputs: [],
}

const archived = {
  ...active,
  id: 11,
  name: 'Old publisher',
  status: 'archived',
}

describe('GraphScreen workflow archive', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listGraphTemplates).mockResolvedValue({ items: [active, archived] } as never)
    vi.mocked(setGraphTemplateStatus).mockImplementation(async (_token, id, status) => ({
      ...(id === active.id ? active : archived),
      status,
    }) as never)
  })

  it('archives from the active list and restores from the archived view', async () => {
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

    await screen.findByText('Publish notes')
    expect(listGraphTemplates).toHaveBeenCalledWith('t', project.slug, true)

    fireEvent.click(screen.getByRole('button', { name: 'Archive Publish notes' }))
    await waitFor(() => {
      expect(confirmDialog).toHaveBeenCalled()
      expect(setGraphTemplateStatus).toHaveBeenCalledWith('t', active.id, 'archived')
      expect(screen.queryByText('Publish notes')).not.toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /View archived workflows/i }))
    expect(await screen.findByText('Old publisher')).toBeInTheDocument()
    expect(screen.getByText('Publish notes')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Restore Old publisher' }))
    await waitFor(() => {
      expect(setGraphTemplateStatus).toHaveBeenCalledWith('t', archived.id, 'active')
      expect(screen.queryByText('Old publisher')).not.toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /View active workflows/i }))
    expect(await screen.findByText('Old publisher')).toBeInTheDocument()
  })

  it('restores a workflow to the paused status the API reinstates, not forced active', async () => {
    // The archived row came from a paused (draft) template, so the API restores it
    // to 'draft'. The active list must show it paused (Resume, not Pause).
    vi.mocked(setGraphTemplateStatus).mockImplementation(async (_token, id, status) => ({
      ...(id === active.id ? active : archived),
      status: id === archived.id && status === 'active' ? 'draft' : status,
    }) as never)

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

    fireEvent.click(await screen.findByRole('button', { name: /View archived workflows/i }))
    fireEvent.click(await screen.findByRole('button', { name: 'Restore Old publisher' }))
    await waitFor(() => {
      expect(setGraphTemplateStatus).toHaveBeenCalledWith('t', archived.id, 'active')
    })

    fireEvent.click(screen.getByRole('button', { name: /View active workflows/i }))
    expect(await screen.findByRole('button', { name: 'Resume Old publisher' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause Old publisher' })).not.toBeInTheDocument()
  })
})

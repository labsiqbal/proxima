import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ensureGraphJobChat, getGraphJob, listGraphJobs } from '../api/graph'
import { listMessages } from '../api/sessions'
import { GraphScreen } from './GraphScreen'

vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn().mockResolvedValue({ items: [] }),
  listGraphTemplates: vi.fn().mockResolvedValue({ items: [] }),
  getGraphJob: vi.fn(),
  ensureGraphJobChat: vi.fn(),
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
vi.mock('../api/sessions', () => ({
  listMessages: vi.fn().mockResolvedValue({ messages: [], goal: null }),
}))
vi.mock('../api/runs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/runs')>()),
  activeRuns: vi.fn().mockResolvedValue({ session_ids: [] }),
  createRun: vi.fn(),
  listEvents: vi.fn().mockResolvedValue({ events: [] }),
}))

const project = {
  slug: 'owner',
  name: 'owner (personal)',
  path: '/tmp/owner',
  owner: 'owner',
  visibility: 'private' as const,
}

const baseProps = {
  token: 't',
  projects: [project],
  activeProject: project,
  onActiveProject: vi.fn(),
  profiles: [],
  profileId: null as number | null,
  features: { designStudio: false, workflowGraph: true, masterOrchestrator: false },
  activeProfile: null,
}

function orphanedPlan() {
  return {
    id: 3,
    title: 'Untitled plan',
    status: 'queued' as const,
    project_slug: project.slug,
    // The thread this plan was created with has since been deleted from the
    // Chat screen, and jobs.session_id is ON DELETE SET NULL.
    session_id: null,
    graph: { nodes: [], edges: [] },
    node_states: [],
  }
}

describe('GraphScreen plan chat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [] })
    vi.mocked(listMessages).mockResolvedValue({ messages: [], goal: null } as never)
  })

  it('re-pins a thread for a plan whose chat session was deleted', async () => {
    vi.mocked(getGraphJob).mockResolvedValue(orphanedPlan() as never)
    vi.mocked(ensureGraphJobChat).mockResolvedValue({ session_id: 42, created: true })

    render(
      <GraphScreen
        {...baseProps}
        pendingJobId={3}
        onPendingConsumed={vi.fn()}
        onStageChange={vi.fn()}
        backNonce={0}
      />,
    )

    await screen.findByRole('heading', { name: 'Untitled plan' })
    // The panel opens its pinned thread on mount, so this is the owner's exact path.
    await userEvent.click(screen.getByRole('button', { name: 'Chat' }))

    await waitFor(() => {
      expect(ensureGraphJobChat).toHaveBeenCalledWith('t', 3)
    })
    // The dead end is gone: the re-pinned thread loads and nothing is refused.
    await waitFor(() => {
      expect(listMessages).toHaveBeenCalledWith('t', 42)
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reuses the pinned thread when the plan still has one', async () => {
    vi.mocked(getGraphJob).mockResolvedValue({ ...orphanedPlan(), session_id: 7 } as never)

    render(
      <GraphScreen
        {...baseProps}
        pendingJobId={3}
        onPendingConsumed={vi.fn()}
        onStageChange={vi.fn()}
        backNonce={0}
      />,
    )

    await screen.findByRole('heading', { name: 'Untitled plan' })
    await userEvent.click(screen.getByRole('button', { name: 'Chat' }))

    await waitFor(() => {
      expect(listMessages).toHaveBeenCalledWith('t', 7)
    })
    expect(ensureGraphJobChat).not.toHaveBeenCalled()
  })
})

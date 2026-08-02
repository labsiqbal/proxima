import '@testing-library/jest-dom/vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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
  visibility: 'private' as const,
}

function jobFixture(id: number, title: string) {
  return {
    id,
    title,
    status: 'queued' as const,
    project_slug: project.slug,
    graph: { nodes: [], edges: [] },
    node_states: [],
  }
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

describe('GraphScreen open retarget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listGraphJobs).mockResolvedValue({ items: [] })
  })

  it('never reports workflow A while opening B after leaving the editor', async () => {
    const jobA = jobFixture(11, 'Plan A')
    const jobB = jobFixture(22, 'Plan B')
    let releaseB: ((job: typeof jobB) => void) | null = null
    const bLoad = new Promise<typeof jobB>(resolve => {
      releaseB = resolve
    })

    vi.mocked(getGraphJob).mockImplementation(async (_token, id) => {
      if (id === 11) return structuredClone(jobA) as never
      if (id === 22) return bLoad as never
      throw new Error(`unexpected job ${id}`)
    })

    const reports: Array<{ stage: 'home' | 'editor'; jobId: number | null }> = []
    const onStageChange = vi.fn((stage: 'home' | 'editor', jobId: number | null) => {
      reports.push({ stage, jobId })
    })

    const { rerender } = render(
      <GraphScreen
        {...baseProps}
        pendingJobId={11}
        onPendingConsumed={vi.fn()}
        onStageChange={onStageChange}
        backNonce={0}
      />,
    )

    await screen.findByRole('heading', { name: 'Plan A' })
    await waitFor(() => {
      expect(reports.some(entry => entry.stage === 'editor' && entry.jobId === 11)).toBe(true)
    })

    // Chrome Back leaves the editor but keep-alive retains the loaded job.
    rerender(
      <GraphScreen
        {...baseProps}
        pendingJobId={null}
        onPendingConsumed={vi.fn()}
        onStageChange={onStageChange}
        backNonce={1}
      />,
    )
    await waitFor(() => {
      expect(reports.at(-1)).toEqual({ stage: 'home', jobId: null })
    })

    const afterHome = reports.length
    rerender(
      <GraphScreen
        {...baseProps}
        pendingJobId={22}
        onPendingConsumed={vi.fn()}
        onStageChange={onStageChange}
        backNonce={1}
      />,
    )

    await waitFor(() => {
      expect(reports.slice(afterHome).some(entry => entry.stage === 'editor' && entry.jobId === 22)).toBe(true)
    })
    expect(reports.slice(afterHome).some(entry => entry.jobId === 11)).toBe(false)
    expect(screen.queryByRole('heading', { name: 'Plan A' })).not.toBeInTheDocument()

    await act(async () => {
      releaseB?.(structuredClone(jobB) as never)
    })
    await screen.findByRole('heading', { name: 'Plan B' })
    expect(reports.slice(afterHome).every(entry => entry.jobId !== 11)).toBe(true)
    expect(reports.filter(entry => entry.stage === 'editor' && entry.jobId === 22).length).toBeGreaterThan(0)
  })
})

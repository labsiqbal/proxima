import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listJobs } from '../api/jobs'
import { ActivityScreen } from './ActivityScreen'

vi.mock('../api/jobs', () => ({ listJobs: vi.fn() }))
vi.mock('../api/graph', () => ({
  listGraphJobs: vi.fn().mockResolvedValue({ items: [] }),
  approveGraphJob: vi.fn(),
  saveGraphTemplate: vi.fn(),
}))

const job = {
  id: 7,
  project_id: 22,
  project_slug: 'beacon',
  project_name: 'Beacon release',
  workflow_id: null,
  session_id: 9,
  title: 'Approve release checklist',
  status: 'review',
  current_step_idx: 0,
  input: {},
  steps_state: [],
  schedule_id: null,
  created_by: 1,
  created_at: '2026-07-31 00:00:00',
  updated_at: '2026-07-31 00:00:00',
  started_at: null,
  finished_at: null,
  archived_at: null,
} as never

function renderTasks(activeProject: { slug: string; name: string } | null) {
  return render(
    <ActivityScreen
      token="token"
      activeProject={activeProject as never}
      globalScope={activeProject == null}
      features={{ workflowGraph: false } as never}
      profiles={[]}
      onOpenTask={vi.fn()}
      onOpenPlan={vi.fn()}
    />,
  )
}

describe('ActivityScreen Project attribution', () => {
  beforeEach(() => {
    vi.mocked(listJobs).mockResolvedValue({
      items: [job],
      total: 1,
      limit: 25,
      offset: 0,
    })
  })

  it('shows Project attribution in every Delegate list, board, and review item', async () => {
    renderTasks(null)

    expect(await screen.findByRole('button', {
      name: /Approve release checklist.*Project: Beacon release/,
    })).toBeInTheDocument()
    expect(document.querySelector('.task-project-tag')).toHaveTextContent('Project: Beacon release')

    await userEvent.setup().click(screen.getByRole('button', { name: 'Board' }))
    expect(await screen.findByRole('button', {
      name: /Approve release checklist.*Project: Beacon release/,
    })).toBeInTheDocument()
    expect(document.querySelector('.task-project-tag')).toHaveTextContent('Project: Beacon release')

    await userEvent.setup().click(screen.getByRole('button', { name: 'Review' }))
    expect(await screen.findByRole('button', {
      name: /Approve release checklist.*Project: Beacon release/,
    })).toBeInTheDocument()
    expect(document.querySelector('.task-project-tag')).toHaveTextContent('Project: Beacon release')
  })

  it('keeps Work project-scoped Tasks free of redundant Project labels', async () => {
    renderTasks({ slug: 'beacon', name: 'Beacon release' })
    await waitFor(() => expect(screen.getByText('Approve release checklist')).toBeInTheDocument())
    expect(screen.queryByText('Beacon release')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve release checklist · Task · review · — · now' }))
      .toBeInTheDocument()
  })
})

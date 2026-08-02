import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Project } from '../types'
import { ProjectsScreen } from './ProjectsScreen'

vi.mock('../components/projects/OpsMigrationDetail', () => ({
  OpsMigrationDetail: ({ project }: { project: Project }) =>
    <div data-testid="ops-detail">{project.slug}</div>,
}))
vi.mock('../components/projects/RelocateProject', () => ({
  RelocateProjectModal: ({ project }: { project: Project }) =>
    <div data-testid="relocate-modal">{project.slug}</div>,
}))
vi.mock('../api/projects', () => ({
  createProject: vi.fn(),
  renameProject: vi.fn(),
  deleteProject: vi.fn(),
}))

const alpha = {
  slug: 'alpha',
  name: 'Alpha',
  path: '/owner/alpha',
  owner: 'owner',
  visibility: 'private',
  location: { state: 'bound', path: '/owner/alpha', message: '' },
} as Project
const collision = {
  ...alpha,
  slug: 'legacy-collision',
  name: 'Legacy collision',
  path: '/owner/legacy-collision',
} as Project

describe('ProjectsScreen Ops migration routing', () => {
  it('renders the routed project even when another project was active', () => {
    render(<ProjectsScreen
      token="token"
      projects={[alpha, collision]}
      activeProject={alpha}
      opsMigrationSlug={collision.slug}
      onActiveProject={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
    />)
    expect(screen.getByTestId('ops-detail')).toHaveTextContent('legacy-collision')
  })

  it('surfaces a missing folder as an actionable state, not a broken card', async () => {
    const user = userEvent.setup()
    const moved = {
      ...alpha,
      slug: 'moved',
      name: 'Moved',
      location: {
        state: 'missing',
        path: '/owner/moved',
        message: 'This project’s folder is no longer at its stored location.',
      },
    } as Project
    render(<ProjectsScreen
      token="token"
      projects={[alpha, moved]}
      activeProject={alpha}
      onActiveProject={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
    />)

    expect(screen.getByText('Folder missing')).toBeInTheDocument()
    expect(screen.getByText(/no longer at its stored location/)).toBeInTheDocument()
    // Only the affected card is flagged.
    expect(screen.getAllByText('Folder missing')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Find the folder for Moved' }))
    expect(screen.getByTestId('relocate-modal')).toHaveTextContent('moved')
  })

  it('opens migration settings from the matching project card', async () => {
    const open = vi.fn()
    const user = userEvent.setup()
    render(<ProjectsScreen
      token="token"
      projects={[alpha, collision]}
      activeProject={alpha}
      onActiveProject={vi.fn()}
      onOpenOpsMigration={open}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
    />)
    await user.click(screen.getByRole('button', { name: 'Ops migration for Legacy collision' }))
    expect(open).toHaveBeenCalledWith(collision)
  })
})

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
  role: 'owner',
  visibility: 'private',
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

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { browseDirs, rebindProject } from '../../api/projects'
import { RelocateProjectModal } from './RelocateProject'
import type { Project } from '../../types'

vi.mock('../../api/projects', async importOriginal => {
  const actual = await importOriginal<typeof import('../../api/projects')>()
  return { ...actual, browseDirs: vi.fn(), rebindProject: vi.fn() }
})

const project: Project = {
  slug: 'client',
  name: 'Client',
  path: '/home/user/work/client',
  owner: 'user',
  visibility: 'private',
  location: {
    state: 'missing',
    path: '/home/user/work/client',
    message: 'This project’s folder is no longer at its stored location.',
  },
}

const dirs = {
  path: '/home/user/work',
  parent: '/home/user',
  dirs: [{ name: 'client-renamed', path: '/home/user/work/client-renamed' }],
  roots: ['/home/user'],
  root_id: 'root-owner',
}

const rebound = {
  rebound: true,
  path: '/home/user/work/client-renamed',
  previous_path: '/home/user/work/client',
  identity: {
    matches: true,
    stored: { label: 'Client', summary: null, source: 'AGENTS.md' },
    found: { label: 'Client', summary: null, source: 'AGENTS.md' },
  },
  repaired: { ops_path: null, layout: [], code_areas_dropped: [] },
  project: { ...project, path: '/home/user/work/client-renamed' },
}

function mismatch() {
  const error = new ApiError(
    409,
    'POST /api/projects/client/rebind failed (409): That folder identifies itself as “Other”, not “Client”.',
    '/api/projects/client/rebind',
    'POST',
    'path',
    'That folder identifies itself as “Other”, not “Client”.',
  )
  error.body = {
    message: 'That folder identifies itself as “Other”, not “Client”.',
    confirmable: true,
    identity: {
      matches: false,
      stored: { label: 'Client', summary: null, source: 'AGENTS.md' },
      found: { label: 'Other', summary: null, source: 'AGENTS.md' },
    },
  }
  return error
}

describe('RelocateProjectModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Browsing follows the real server: stepping into a folder lists it.
    vi.mocked(browseDirs).mockImplementation(async (_token, path) =>
      path ? { ...dirs, path, parent: dirs.path, dirs: [] } : dirs)
    vi.mocked(rebindProject).mockResolvedValue(rebound)
  })

  it('re-pins the project to the folder picked in the browser', async () => {
    const user = userEvent.setup()
    const onRelocated = vi.fn().mockResolvedValue(undefined)
    render(<RelocateProjectModal
      token="tok"
      project={project}
      onClose={vi.fn()}
      onRelocated={onRelocated}
    />)

    // The stored location is shown so the owner knows what is being re-pinned.
    expect(await screen.findByText('/home/user/work/client')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'client-renamed' }))
    await user.click(await screen.findByRole('button', { name: /Re-pin/ }))

    await waitFor(() => expect(rebindProject).toHaveBeenCalledWith('tok', 'client', {
      path: '/home/user/work/client-renamed',
      root_id: 'root-owner',
      confirm: false,
    }))
    await waitFor(() => expect(onRelocated).toHaveBeenCalledWith(rebound.project))
  })

  it('warns on an identity mismatch and lets the owner re-pin anyway', async () => {
    const user = userEvent.setup()
    const onRelocated = vi.fn().mockResolvedValue(undefined)
    vi.mocked(rebindProject)
      .mockRejectedValueOnce(mismatch())
      .mockResolvedValueOnce(rebound)
    render(<RelocateProjectModal
      token="tok"
      project={project}
      onClose={vi.fn()}
      onRelocated={onRelocated}
    />)

    await user.click(await screen.findByRole('button', { name: 'client-renamed' }))
    await user.click(await screen.findByRole('button', { name: /^Re-pin/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('identifies itself as')
    expect(alert).not.toHaveTextContent('/api/projects')

    await user.click(screen.getByRole('button', { name: 'Re-pin anyway' }))
    await waitFor(() => expect(rebindProject).toHaveBeenLastCalledWith('tok', 'client', {
      path: '/home/user/work/client-renamed',
      root_id: 'root-owner',
      confirm: true,
    }))
    await waitFor(() => expect(onRelocated).toHaveBeenCalledWith(rebound.project))
  })

  it('offers no override for a refusal the owner cannot confirm away', async () => {
    const user = userEvent.setup()
    const clash = new ApiError(
      409,
      'POST /api/projects/client/rebind failed (409): that folder is already linked as project \'other\'',
      '/api/projects/client/rebind',
      'POST',
      'path',
      'that folder is already linked as project \'other\'',
    )
    vi.mocked(rebindProject).mockRejectedValue(clash)
    render(<RelocateProjectModal
      token="tok"
      project={project}
      onClose={vi.fn()}
      onRelocated={vi.fn()}
    />)

    await user.click(await screen.findByRole('button', { name: 'client-renamed' }))
    await user.click(await screen.findByRole('button', { name: /^Re-pin/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('already linked as project')
    expect(screen.queryByRole('button', { name: 'Re-pin anyway' })).not.toBeInTheDocument()
  })
})

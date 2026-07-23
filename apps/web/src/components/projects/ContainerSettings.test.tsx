import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ContainerSettingsModal } from './ContainerSettings'
import { addProjectArea, detectProjectAreas, listProjectAreas, updateProjectArea } from '../../api/projects'
import type { Project } from '../../types'

vi.mock('../../api/projects', () => ({
  listProjectAreas: vi.fn(),
  addProjectArea: vi.fn(),
  detectProjectAreas: vi.fn(),
  updateProjectArea: vi.fn(),
}))

const project: Project = {
  slug: 'demo', name: 'Demo', path: '/home/user/demo',
  owner: 'user', role: 'owner', visibility: 'private',
}

const areas = {
  code_areas: [
    {
      id: 1, rel_path: 'app', source: 'auto', push_on_merge: false,
      remote: { name: 'origin', url: 'git@github.com:owner/repo.git', web_url: 'https://github.com/owner/repo', gh_authenticated: true },
    },
    { id: 2, rel_path: 'tools', source: 'manual', push_on_merge: false, remote: null },
  ],
  ops_area: { id: 3, rel_path: '.' },
}

const emptyAreas = { code_areas: [], ops_area: { id: 3, rel_path: '.' } }

describe('ContainerSettingsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listProjectAreas).mockResolvedValue(areas as never)
  })

  it('offers the push toggle only for areas with a detected remote', async () => {
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    expect(await screen.findByText('app')).toBeInTheDocument()
    // The connected area: remote shown (with GitHub enrichment) + the toggle, OFF by default.
    expect(screen.getByText('git@github.com:owner/repo.git')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open on GitHub/ })).toHaveAttribute('href', 'https://github.com/owner/repo')
    expect(screen.getByText(/gh signed in/)).toBeInTheDocument()
    const toggles = screen.getAllByRole('checkbox')
    expect(toggles).toHaveLength(1) // remote-less areas get NO toggle at all
    expect(toggles[0]).not.toBeChecked() // default off (T9 guardrail)
    // The remote-less area says plainly that merges stay local.
    expect(screen.getByText(/No git remote/)).toBeInTheDocument()
  })

  it('toggling on saves the per-area opt-in', async () => {
    vi.mocked(updateProjectArea).mockResolvedValue({ id: 1, rel_path: 'app', push_on_merge: true, remote: areas.code_areas[0].remote } as never)
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox'))
    await waitFor(() => expect(updateProjectArea).toHaveBeenCalledWith('token', 'demo', 1, { push_on_merge: true }))
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeChecked())
  })

  it('surfaces a refused toggle without flipping the checkbox', async () => {
    vi.mocked(updateProjectArea).mockRejectedValue(new Error('this code area has no git remote'))
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox'))
    expect(await screen.findByText(/no git remote/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('empty state offers scan and use-project-folder actions', async () => {
    vi.mocked(listProjectAreas).mockResolvedValue(emptyAreas as never)
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    expect(await screen.findByRole('button', { name: 'Scan for git repos' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Use project folder' })).toBeInTheDocument()
    expect(screen.getByText(/No code areas yet/)).toBeInTheDocument()
  })

  it('use project folder registers the root and reloads areas', async () => {
    vi.mocked(listProjectAreas)
      .mockResolvedValueOnce(emptyAreas as never)
      .mockResolvedValueOnce({
        code_areas: [{ id: 9, rel_path: '.', source: 'manual', push_on_merge: false, remote: null }],
        ops_area: { id: 3, rel_path: '.' },
      } as never)
    vi.mocked(addProjectArea).mockResolvedValue({ id: 9, rel_path: '.', source: 'manual' })
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Use project folder' }))
    await waitFor(() => expect(addProjectArea).toHaveBeenCalledWith('token', 'demo', { rel_path: '.' }))
    expect(await screen.findByText('project root')).toBeInTheDocument()
    expect(screen.getByText(/No git remote/)).toBeInTheDocument()
  })

  it('scan for git repos refreshes the list and reports when nothing is found', async () => {
    vi.mocked(listProjectAreas).mockResolvedValue(emptyAreas as never)
    vi.mocked(detectProjectAreas).mockResolvedValue({
      ...emptyAreas,
      detect: { detected: [], added: [], removed: [] },
    } as never)
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Scan for git repos' }))
    await waitFor(() => expect(detectProjectAreas).toHaveBeenCalledWith('token', 'demo'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/No git repos found/)
  })

  it('scan can discover a nested repo from the empty state', async () => {
    vi.mocked(listProjectAreas).mockResolvedValue(emptyAreas as never)
    vi.mocked(detectProjectAreas).mockResolvedValue({
      code_areas: [{ id: 8, rel_path: 'demo-app', source: 'auto', push_on_merge: false, remote: null }],
      ops_area: { id: 3, rel_path: '.' },
      detect: { detected: ['demo-app'], added: ['demo-app'], removed: [] },
    } as never)
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Scan for git repos' }))
    expect(await screen.findByText('demo-app')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scan again' })).toBeInTheDocument()
  })
})

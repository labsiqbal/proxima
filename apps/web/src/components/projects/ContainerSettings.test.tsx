import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ContainerSettingsModal } from './ContainerSettings'
import { addProjectArea, detectProjectAreas, getProjectLayout, listProjectAreas, setMemoryWrites, updateProjectArea } from '../../api/projects'
import type { Project } from '../../types'

vi.mock('../../api/projects', () => ({
  listProjectAreas: vi.fn(),
  addProjectArea: vi.fn(),
  detectProjectAreas: vi.fn(),
  updateProjectArea: vi.fn(),
  getProjectLayout: vi.fn(),
  setMemoryWrites: vi.fn(),
}))

const project: Project = {
  slug: 'demo', name: 'Demo', path: '/home/user/demo',
  owner: 'user', visibility: 'private',
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

const layout = {
  ops_path: 'ops',
  areas: {
    wiki: { path: 'wiki', source: 'detected', exists: true },
    artifacts: { path: 'ops/artifacts', source: 'default', exists: false },
    scripts: { path: 'ops/scripts', source: 'default', exists: false },
    uploads: { path: 'ops/uploads', source: 'default', exists: false },
  },
  memory_writes: { enabled: true },
}

describe('ContainerSettingsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listProjectAreas).mockResolvedValue(areas as never)
    vi.mocked(getProjectLayout).mockResolvedValue(layout as never)
  })

  it('offers the push toggle only for areas with a detected remote', async () => {
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    expect(await screen.findByText('app')).toBeInTheDocument()
    // The connected area: remote shown (with GitHub enrichment) + the toggle, OFF by default.
    expect(screen.getByText('git@github.com:owner/repo.git')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open on GitHub/ })).toHaveAttribute('href', 'https://github.com/owner/repo')
    expect(screen.getByText(/gh signed in/)).toBeInTheDocument()
    const pushToggles = screen.getAllByRole('checkbox', { name: /Push after merge/ })
    expect(pushToggles).toHaveLength(1) // remote-less areas get NO toggle at all
    expect(pushToggles[0]).not.toBeChecked() // default off (T9 guardrail)
    // The remote-less area says plainly that merges stay local.
    expect(screen.getByText(/No git remote/)).toBeInTheDocument()
  })

  it('toggling on saves the per-area opt-in', async () => {
    vi.mocked(updateProjectArea).mockResolvedValue({ id: 1, rel_path: 'app', push_on_merge: true, remote: areas.code_areas[0].remote } as never)
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox', { name: /Push after merge/ }))
    await waitFor(() => expect(updateProjectArea).toHaveBeenCalledWith('token', 'demo', 1, { push_on_merge: true }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Push after merge/ })).toBeChecked())
  })

  it('surfaces a refused toggle without flipping the checkbox', async () => {
    vi.mocked(updateProjectArea).mockRejectedValue(new Error('this code area has no git remote'))
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox', { name: /Push after merge/ }))
    expect(await screen.findByText(/no git remote/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Push after merge/ })).not.toBeChecked()
  })

  it('shows the detected wiki location with the memory toggle ON by default', async () => {
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    const toggle = await screen.findByRole('checkbox', { name: /Write memory into this project's wiki/ })
    expect(toggle).toBeChecked()
    // The hint names the real location the automatic writers target.
    expect(screen.getByText('wiki/')).toBeInTheDocument()
    expect(screen.getByText(/detected/)).toBeInTheDocument()
  })

  it('turning memory writes off saves the per-project setting', async () => {
    vi.mocked(setMemoryWrites).mockResolvedValue({ enabled: false })
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox', { name: /Write memory into this project's wiki/ }))
    await waitFor(() => expect(setMemoryWrites).toHaveBeenCalledWith('token', 'demo', false))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Write memory into this project's wiki/ })).not.toBeChecked())
  })

  it('a refused memory toggle surfaces the error without flipping', async () => {
    vi.mocked(setMemoryWrites).mockRejectedValue(new Error('memory setting rejected'))
    const user = userEvent.setup()
    render(<ContainerSettingsModal token="token" project={project} onClose={vi.fn()} />)
    await user.click(await screen.findByRole('checkbox', { name: /Write memory into this project's wiki/ }))
    expect(await screen.findByText(/memory setting rejected/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Write memory into this project's wiki/ })).toBeChecked()
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

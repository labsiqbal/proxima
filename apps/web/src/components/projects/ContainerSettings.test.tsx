import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ContainerSettingsModal } from './ContainerSettings'
import { listProjectAreas, updateProjectArea } from '../../api/projects'
import type { Project } from '../../types'

vi.mock('../../api/projects', () => ({
  listProjectAreas: vi.fn(),
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
})

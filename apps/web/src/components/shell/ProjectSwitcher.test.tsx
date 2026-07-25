import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renameProject } from '../../api/projects'
import { promptDialog } from '../ui/Dialog'
import { ProjectSwitcher } from './ProjectSwitcher'

vi.mock('../../api/projects', () => ({
  renameProject: vi.fn(),
}))
vi.mock('../ui/Dialog', () => ({
  promptDialog: vi.fn(),
}))

const projects = [
  { id: 1, name: 'Demo', slug: 'demo', path: '/tmp/demo', visibility: 'private' },
  { id: 2, name: 'Ops', slug: 'ops', path: '/tmp/ops', visibility: 'private' },
] as never[]

describe('ProjectSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the active project name as text with a chevron, not icon-only', () => {
    render(
      <ProjectSwitcher
        projects={projects}
        activeProject={projects[0]}
        onSelectProject={vi.fn()}
      />,
    )
    const trigger = screen.getByRole('button', { name: 'Active project: Demo' })
    expect(trigger).toHaveTextContent('Demo')
    expect(trigger.querySelector('.project-switcher-caret')).toBeInTheDocument()
  })

  it('switches the global active project from the list', async () => {
    const user = userEvent.setup()
    const onSelectProject = vi.fn()
    render(
      <ProjectSwitcher
        projects={projects}
        activeProject={projects[0]}
        onSelectProject={onSelectProject}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Active project: Demo' }))
    await user.click(screen.getByRole('option', { name: /Ops/ }))
    expect(onSelectProject).toHaveBeenCalledWith(projects[1])
  })

  it('disables when there are no projects', () => {
    render(
      <ProjectSwitcher projects={[]} activeProject={null} onSelectProject={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'Active project: No projects' })).toBeDisabled()
  })

  it('locks (disabled) on deep surfaces without opening the menu', async () => {
    const user = userEvent.setup()
    const onSelectProject = vi.fn()
    render(
      <ProjectSwitcher
        projects={projects}
        activeProject={projects[0]}
        onSelectProject={onSelectProject}
        locked
        lockedReason="Project is locked while this view is open"
      />,
    )
    const trigger = screen.getByRole('button', { name: 'Active project: Demo (locked)' })
    expect(trigger).toBeDisabled()
    await user.click(trigger)
    expect(onSelectProject).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('renames a listed project and updates the active label via callback', async () => {
    const user = userEvent.setup()
    const onProjectRenamed = vi.fn()
    const renamed = { ...projects[0], name: 'Studio' }
    vi.mocked(promptDialog).mockResolvedValue('Studio')
    vi.mocked(renameProject).mockResolvedValue(renamed as never)

    render(
      <ProjectSwitcher
        projects={projects}
        activeProject={projects[0]}
        onSelectProject={vi.fn()}
        token="t"
        onProjectRenamed={onProjectRenamed}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Active project: Demo' }))
    await user.click(screen.getByRole('button', { name: 'Rename project Demo' }))

    await waitFor(() => {
      expect(renameProject).toHaveBeenCalledWith('t', 'demo', 'Studio')
      expect(onProjectRenamed).toHaveBeenCalledWith(renamed)
    })
  })
})

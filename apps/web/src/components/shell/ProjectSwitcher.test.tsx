import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ProjectSwitcher } from './ProjectSwitcher'

const projects = [
  { id: 1, name: 'Demo', slug: 'demo', path: '/tmp/demo', visibility: 'private' },
  { id: 2, name: 'Ops', slug: 'ops', path: '/tmp/ops', visibility: 'private' },
] as never[]

describe('ProjectSwitcher', () => {
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
})

import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'

const base = {
  activeProfile: null, activeProject: null, activeSession: null, currentView: 'home' as const, workspaceMode: 'ops' as const,
  features: { designStudio: false, workflowGraph: false }, onClose: vi.fn(), onNewChat: vi.fn(), onSelectWorkspace: vi.fn(), onLogout: vi.fn(), onRenameSession: vi.fn(), onDeleteSession: vi.fn(), onSelectProject: vi.fn(), onSelectSession: vi.fn(), onOpenDesign: vi.fn(), onSelectView: vi.fn(), profiles: [], projects: [], sessions: [], seen: {}, user: { id: 1, username: 'owner', role: 'owner', os_user: 'owner' },
}

describe('Sidebar workspace IA', () => {
  it('keeps the Ops sidebar focused and feature-gated', async () => {
    render(<Sidebar {...base} />)
    expect(screen.queryByRole('button', { name: 'Design' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Workflow Graphs' })).not.toBeInTheDocument()
    const { unmount } = render(<Sidebar {...base} features={{ ...base.features, workflowGraph: true }} />)
    expect(screen.queryByRole('button', { name: 'Workflow Graphs' })).not.toBeInTheDocument()
    unmount()
    expect(screen.queryByRole('button', { name: 'Terminal' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Activity' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Wiki' })).not.toBeInTheDocument()
  })

  it('orders the single Workflows destination without a duplicate Scheduled entry', () => {
    const { rerender } = render(<Sidebar {...base} />)
    const labels = () => Array.from(document.querySelectorAll('.primary-nav > .nav-item strong')).map(node => node.textContent)
    expect(labels()).toEqual(['New task', 'Tasks', 'Projects', 'Workflows', 'Artifacts'])
    rerender(<Sidebar {...base} features={{ ...base.features, designStudio: true }} />)
    expect(labels()).toEqual(['New task', 'Tasks', 'Projects', 'Workflows', 'Artifacts', 'Design'])
    expect(screen.queryByRole('button', { name: 'Scheduled' })).not.toBeInTheDocument()
  })

  it('switches to a Code-specific sidebar with Terminal, New session, and recents', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<Sidebar {...base} />)
    await user.click(screen.getByRole('button', { name: /Code/ }))
    expect(base.onSelectWorkspace).toHaveBeenCalledWith('code')

    rerender(<Sidebar {...base} workspaceMode="code" currentView="chat" />)
    expect(screen.getByRole('button', { name: 'Terminal' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Workflows' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'New session' }))
    expect(base.onNewChat).toHaveBeenCalledTimes(1)
  })

  it('keeps Agents and Settings in the profile menu', async () => {
    const user = userEvent.setup()
    render(<Sidebar {...base} />)
    await user.click(screen.getByRole('button', { name: /owner/ }))
    await user.click(screen.getByRole('button', { name: 'Agents' }))
    expect(base.onSelectView).toHaveBeenCalledWith('profiles')
  })
  it('keeps workflow iteration sessions out of Code recents', () => {
    const workflowSession = { id: 91, title: 'Workflow iteration', workflow_id: 12, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    render(<Sidebar {...base} workspaceMode="code" currentView="chat" sessions={[workflowSession as never]} />)
    expect(screen.queryByText('Workflow iteration')).not.toBeInTheDocument()
  })

})

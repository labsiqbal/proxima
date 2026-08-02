import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'

const base = {
  activeProfile: null, activeProject: null, activeSession: null, currentView: 'chat' as const,
  onClose: vi.fn(), onLogout: vi.fn(), onRenameSession: vi.fn(), onDeleteSession: vi.fn(), onSelectProject: vi.fn(), onSelectSession: vi.fn(), onOpenDesign: vi.fn(), onSelectView: vi.fn(), profiles: [], projects: [], sessions: [], seen: {}, user: { id: 1, username: 'owner', role: 'owner', os_user: 'owner' },
}

describe('Sidebar single-workspace IA', () => {
  it('orders one flow-first nav and keeps tools and workspaces out of it', () => {
    const { rerender } = render(<Sidebar {...base} />)
    const labels = () => Array.from(document.querySelectorAll('.primary-nav > .nav-item strong')).map(node => node.textContent)
    // Destinations only - blank session lives on Chat header / mobile topbar / `/new`.
    // Project switch is the Work sidebar; project manage is Settings → Projects.
    expect(labels()).toEqual(['Chat', 'Tasks', 'Workflows', 'Files', 'Design'])
    expect(screen.queryByRole('button', { name: 'New chat' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Projects' })).not.toBeInTheDocument()
    // No workspace switch, and Terminal/Preview stay right-rail tools. Files is
    // a destination now (ADR-0040) and is asserted in the order above.
    expect(screen.queryByRole('button', { name: 'Ops' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Code' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Terminal' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Preview' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Workflows' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Master' })).not.toBeInTheDocument()
  })

  it('navigates the flow destinations', async () => {
    const user = userEvent.setup()
    render(<Sidebar {...base} />)
    await user.click(screen.getByRole('button', { name: 'Chat' }))
    expect(base.onSelectView).toHaveBeenCalledWith('chat')
    await user.click(screen.getByRole('button', { name: 'Tasks' }))
    expect(base.onSelectView).toHaveBeenCalledWith('activity')
    await user.click(screen.getByRole('button', { name: 'Workflows' }))
    expect(base.onSelectView).toHaveBeenCalledWith('workflows')
  })

  it('marks Tasks active for the task workspace and the New task launcher', () => {
    const { rerender } = render(<Sidebar {...base} currentView="task" />)
    const active = () => Array.from(document.querySelectorAll('.primary-nav > .nav-item.active strong')).map(node => node.textContent)
    expect(active()).toEqual(['Tasks'])
    rerender(<Sidebar {...base} currentView="home" />)
    expect(active()).toEqual(['Tasks'])
  })

  it('keeps Agents and Settings in the profile menu', async () => {
    const user = userEvent.setup()
    render(<Sidebar {...base} />)
    await user.click(screen.getByRole('button', { name: /owner/ }))
    await user.click(screen.getByRole('button', { name: 'Agents' }))
    expect(base.onSelectView).toHaveBeenCalledWith('profiles')
  })

  it('shows recent chats without any workspace switch, excluding workflow iteration threads', () => {
    const plain = { id: 90, title: 'Pricing rethink', workflow_id: null, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    const workflowSession = { id: 91, title: 'Workflow iteration', workflow_id: 12, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    render(<Sidebar {...base} sessions={[plain, workflowSession] as never} />)
    expect(screen.getByText('Pricing rethink')).toBeInTheDocument()
    expect(screen.queryByText('Workflow iteration')).not.toBeInTheDocument()
  })

  it('attributes a workflow iteration chat to Workflows, not Chat', () => {
    const workflowSession = { id: 91, title: 'Iterate', workflow_id: 12, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    render(<Sidebar {...base} currentView="chat" activeSession={workflowSession as never} />)
    const active = Array.from(document.querySelectorAll('.primary-nav > .nav-item.active strong')).map(node => node.textContent)
    expect(active).toEqual(['Workflows'])
  })

  it('uses a global Delegate nav without project context or Work destinations', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<Sidebar {...base} currentView="master" delegate />)
    const labels = () => Array.from(document.querySelectorAll('.primary-nav > .nav-item strong')).map(node => node.textContent)
    expect(labels()).toEqual(['Master', 'Tasks', 'Files'])
    expect(screen.getByRole('navigation', { name: 'Delegate navigation' })).toBeInTheDocument()
    expect(screen.queryByText('Work project')).not.toBeInTheDocument()
    expect(screen.queryByText('Recent chats')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Agents' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Master' })).toHaveAttribute('aria-current', 'page')

    await user.click(screen.getByRole('button', { name: 'Tasks' }))
    expect(base.onSelectView).toHaveBeenCalledWith('activity')
    rerender(<Sidebar {...base} currentView="activity" delegate />)
    expect(screen.getByRole('button', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page')
  })
})

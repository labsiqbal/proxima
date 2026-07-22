import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'

const base = {
  activeProfile: null, activeProject: null, activeSession: null, currentView: 'chat' as const,
  features: { designStudio: false, workflowGraph: false }, onClose: vi.fn(), onNewChat: vi.fn(), onLogout: vi.fn(), onRenameSession: vi.fn(), onDeleteSession: vi.fn(), onSelectProject: vi.fn(), onSelectSession: vi.fn(), onOpenDesign: vi.fn(), onSelectView: vi.fn(), profiles: [], projects: [], sessions: [], seen: {}, user: { id: 1, username: 'owner', role: 'owner', os_user: 'owner' },
}

describe('Sidebar single-workspace IA', () => {
  it('orders one flow-first nav and keeps tools and workspaces out of it', () => {
    const { rerender } = render(<Sidebar {...base} />)
    const labels = () => Array.from(document.querySelectorAll('.primary-nav > .nav-item strong')).map(node => node.textContent)
    expect(labels()).toEqual(['New chat', 'Chat', 'Tasks', 'Recipes', 'Projects', 'Archive'])
    // No workspace switch and no tool destinations: tools live on the right rail.
    expect(screen.queryByRole('button', { name: 'Ops' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Code' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Terminal' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Workflows' })).not.toBeInTheDocument()
    rerender(<Sidebar {...base} features={{ ...base.features, designStudio: true }} />)
    expect(labels()).toEqual(['New chat', 'Chat', 'Tasks', 'Recipes', 'Projects', 'Archive', 'Design'])
  })

  it('navigates the flow and starts a new chat', async () => {
    const user = userEvent.setup()
    render(<Sidebar {...base} />)
    await user.click(screen.getByRole('button', { name: 'Tasks' }))
    expect(base.onSelectView).toHaveBeenCalledWith('activity')
    await user.click(screen.getByRole('button', { name: 'Recipes' }))
    expect(base.onSelectView).toHaveBeenCalledWith('workflows')
    await user.click(screen.getByRole('button', { name: 'New chat' }))
    expect(base.onNewChat).toHaveBeenCalledTimes(1)
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

  it('shows recent chats without any workspace switch, excluding recipe iteration threads', () => {
    const plain = { id: 90, title: 'Pricing rethink', workflow_id: null, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    const workflowSession = { id: 91, title: 'Workflow iteration', workflow_id: 12, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    render(<Sidebar {...base} sessions={[plain, workflowSession] as never} />)
    expect(screen.getByText('Pricing rethink')).toBeInTheDocument()
    expect(screen.queryByText('Workflow iteration')).not.toBeInTheDocument()
  })

  it('attributes a recipe iteration chat to Recipes, not Chat', () => {
    const workflowSession = { id: 91, title: 'Iterate', workflow_id: 12, job_id: null, project_slug: null, updated_at: '2026-01-01' }
    render(<Sidebar {...base} currentView="chat" activeSession={workflowSession as never} />)
    const active = Array.from(document.querySelectorAll('.primary-nav > .nav-item.active strong')).map(node => node.textContent)
    expect(active).toEqual(['Recipes'])
  })
})

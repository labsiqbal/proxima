import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AppShell } from './AppShell'

vi.mock('./ToolDock', () => ({ ToolDock: () => <div data-testid="tool-dock" /> }))
vi.mock('./AttentionInbox', () => ({ AttentionInbox: () => null }))
vi.mock('./RunningTasks', () => ({ RunningTasks: () => null }))
vi.mock('./SearchModal', () => ({
  SearchModal: (props: { onClose: () => void }) => (
    <div role="dialog" aria-label="Search">
      <button type="button" onClick={props.onClose}>Close search</button>
    </div>
  ),
}))

const base = {
  activeProfile: { id: 1, name: 'Default' } as never,
  activeProject: { id: 1, name: 'Demo', slug: 'demo' } as never,
  activeSession: null,
  currentView: 'chat' as const,
  features: { designStudio: true, workflowGraph: true, masterOrchestrator: false },
  onNewChat: vi.fn(),
  onRenameSession: vi.fn(),
  onDeleteSession: vi.fn(),
  onSelectProject: vi.fn(),
  onSelectSession: vi.fn(),
  onOpenDesign: vi.fn(),
  seen: {},
  onSelectView: vi.fn(),
  onLogout: vi.fn(),
  profiles: [],
  projects: [],
  sessions: [],
  token: 't',
  user: { id: 1, username: 'owner', role: 'owner', os_user: 'owner' },
}

describe('AppShell mobile drawer + search', () => {
  beforeEach(() => {
    localStorage.setItem('proxima.tour.coreDone', '1')
    // Force the mobile branch of toggleLeft / drawer open path.
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query.includes('max-width') ? true : !query.includes('min-width: 768px'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('opens search from the mobile top bar', async () => {
    const user = userEvent.setup()
    render(<AppShell {...base}><div>main</div></AppShell>)
    // Two Search buttons exist (desktop top-bar + mobile); pick the mobile one.
    const mobileSearch = within(document.querySelector('.mobile-topbar') as HTMLElement)
      .getByRole('button', { name: 'Search' })
    await user.click(mobileSearch)
    expect(screen.getByRole('dialog', { name: 'Search' })).toBeInTheDocument()
  })

  it('moves focus into the drawer on open and restores it on Escape', async () => {
    const user = userEvent.setup()
    render(<AppShell {...base}><div>main</div></AppShell>)
    const menu = within(document.querySelector('.mobile-topbar') as HTMLElement)
      .getByRole('button', { name: 'Menu' })
    await user.click(menu)
    expect(document.querySelector('.sidebar')?.classList.contains('is-open')).toBe(true)
    await waitFor(() => {
      expect(document.activeElement).toHaveAttribute('aria-label', 'Close menu')
    })
    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(document.querySelector('.sidebar')?.classList.contains('is-open')).toBe(false)
      expect(document.activeElement).toBe(menu)
    })
  })

  it('opens search with Ctrl/Cmd+K', async () => {
    const user = userEvent.setup()
    render(<AppShell {...base}><div>main</div></AppShell>)
    await user.keyboard('{Control>}k{/Control}')
    expect(screen.getByRole('dialog', { name: 'Search' })).toBeInTheDocument()
  })

  it('places the project switcher in the Work sidebar, not global chrome', () => {
    render(
      <AppShell
        {...base}
        projects={[{ id: 1, name: 'Demo', slug: 'demo', path: '/tmp/demo', visibility: 'private' } as never]}
      >
        <div>main</div>
      </AppShell>,
    )
    const sidebar = document.querySelector('.sidebar') as HTMLElement
    expect(within(sidebar).getByRole('button', { name: 'Active project: Demo' })).toBeInTheDocument()
    expect(within(document.querySelector('.top-bar') as HTMLElement).getByRole('button', { name: 'Search' })).toBeInTheDocument()
  })

  it('routes Work-sidebar project picks to onSelectProject (shell filter), not onOpenProject', async () => {
    const user = userEvent.setup()
    const onSelectProject = vi.fn()
    const onOpenProject = vi.fn()
    const projects = [
      { slug: 'demo', name: 'Demo', path: '/tmp/demo', owner: 'o', role: 'owner', visibility: 'private' as const },
      { slug: 'other', name: 'Other', path: '/tmp/other', owner: 'o', role: 'owner', visibility: 'private' as const },
    ]
    render(
      <AppShell
        {...base}
        projects={projects}
        activeProject={projects[0]}
        onSelectProject={onSelectProject}
        onOpenProject={onOpenProject}
      >
        <div>main</div>
      </AppShell>,
    )
    const sidebar = document.querySelector('.sidebar') as HTMLElement
    await user.click(within(sidebar).getByRole('button', { name: 'Active project: Demo' }))
    await user.click(screen.getByRole('option', { name: /Other/ }))
    expect(onSelectProject).toHaveBeenCalledWith(projects[1])
    expect(onOpenProject).not.toHaveBeenCalled()
  })

  it('opens Projects manage from the account menu', async () => {
    const user = userEvent.setup()
    render(<AppShell {...base}><div>main</div></AppShell>)
    await user.click(screen.getByRole('button', { name: 'Account actions' }))
    await user.click(screen.getByRole('button', { name: /Projects/ }))
    expect(base.onSelectView).toHaveBeenCalledWith('projects')
  })

  it('always shows chrome Back, disabled without deep stack', () => {
    render(<AppShell {...base} chromeBackEnabled={false}><div>main</div></AppShell>)
    const topBar = document.querySelector('.top-bar') as HTMLElement
    const back = within(topBar).getByRole('button', { name: 'Back' })
    expect(back).toBeDisabled()
  })

  it('enables chrome Back with origin label and fires onChromeBack', async () => {
    const user = userEvent.setup()
    const onChromeBack = vi.fn()
    render(
      <AppShell
        {...base}
        chromeBackEnabled
        chromeBackLabel="Back to Tasks"
        onChromeBack={onChromeBack}
      >
        <div>main</div>
      </AppShell>,
    )
    const topBar = document.querySelector('.top-bar') as HTMLElement
    const back = within(topBar).getByRole('button', { name: 'Back to Tasks' })
    expect(back).toBeEnabled()
    expect(back).toHaveTextContent('Back to Tasks')
    await user.click(back)
    expect(onChromeBack).toHaveBeenCalled()
  })

  it('locks the project switcher when projectLocked', () => {
    render(
      <AppShell
        {...base}
        projectLocked
        projectLockedReason="Project is locked while this view is open"
        projects={[{ id: 1, name: 'Demo', slug: 'demo', path: '/tmp/demo', visibility: 'private' } as never]}
      >
        <div>main</div>
      </AppShell>,
    )
    const sidebar = document.querySelector('.sidebar') as HTMLElement
    const switcher = within(sidebar).getByRole('button', { name: /Active project: Demo \(locked\)/ })
    expect(switcher).toBeDisabled()
    expect(switcher).toHaveAttribute('title', 'Project is locked while this view is open')
  })

  it('keeps Delegate global while retaining the accessible shell sidebar', async () => {
    const user = userEvent.setup()
    const onModeChange = vi.fn()
    render(<AppShell {...base} mode="delegate" onModeChange={onModeChange} features={{ ...base.features, masterOrchestrator: true }}><div>Master desk</div></AppShell>)
    expect(screen.getAllByRole('button', { name: 'Delegate' })[0]).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('navigation', { name: 'Delegate navigation' })).toBeInTheDocument()
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: 'Master' })).toBeInTheDocument()
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: 'Tasks' })).toBeInTheDocument()
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: 'Archive' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Tools')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Active project:/ })).not.toBeInTheDocument()
    // The account menu stays: it is the only route to Projects, Agents,
    // Settings, and Log out, and Delegate must not strand the owner.
    expect(screen.getByRole('button', { name: 'Account actions' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Toggle sidebar' })).not.toBeInTheDocument()
    expect(screen.queryByRole('separator', { name: 'Resize sidebar' })).not.toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Work' })[0])
    expect(onModeChange).toHaveBeenCalledWith('work')
  })

  it('opens the same focus-managed drawer for Delegate on small screens', async () => {
    const user = userEvent.setup()
    render(<AppShell {...base} mode="delegate" features={{ ...base.features, masterOrchestrator: true }}><div>Master desk</div></AppShell>)
    const mobile = document.querySelector('.delegate-mobile-topbar') as HTMLElement
    await user.click(within(mobile).getByRole('button', { name: 'Menu' }))
    await waitFor(() => expect(document.activeElement).toHaveAttribute('aria-label', 'Close menu'))
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('navigation', { name: 'Delegate navigation' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.activeElement).toBe(within(mobile).getByRole('button', { name: 'Menu' })))
  })

  it('does not expose Delegate while Master is disabled', () => {
    render(<AppShell {...base} features={{ ...base.features, masterOrchestrator: false }}><div>main</div></AppShell>)
    expect(screen.queryByRole('button', { name: 'Delegate' })).not.toBeInTheDocument()
  })
})

describe('AppShell delegate header status cluster', () => {
  const delegateBase = {
    ...base,
    features: { designStudio: true, workflowGraph: true, masterOrchestrator: true },
    mode: 'delegate' as const,
    currentView: 'master' as const,
  }

  it('keeps the status cluster and the account menu in Delegate', () => {
    const { container } = render(<AppShell {...delegateBase} />)
    expect(container.querySelector('.header-status-cluster')).toBeTruthy()
    // Projects, Agents, Settings, and Log out live only here, so Delegate needs it.
    expect(screen.getByLabelText('Account actions')).toBeInTheDocument()
    // Search and the sidebar toggle stay Work-only.
    expect(screen.queryByLabelText('Search')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Toggle sidebar')).not.toBeInTheDocument()
  })

  it('returns to Work before opening Settings from the Delegate account menu', async () => {
    const onModeChange = vi.fn()
    const onSelectView = vi.fn()
    const user = userEvent.setup()
    render(<AppShell {...delegateBase} onModeChange={onModeChange} onSelectView={onSelectView} />)
    await user.click(screen.getByLabelText('Account actions'))
    await user.click(screen.getByRole('button', { name: /Settings/ }))
    expect(onModeChange).toHaveBeenCalledWith('work')
    expect(onSelectView).toHaveBeenCalledWith('settings')
  })

  it('returns to Work before opening a Work-only target from Delegate', async () => {
    const onModeChange = vi.fn()
    const onOpenAttentionTarget = vi.fn()
    vi.doMock('./AttentionInbox', () => ({
      AttentionInbox: (props: { onOpenTarget: (t: unknown) => void }) => (
        <button type="button" onClick={() => props.onOpenTarget({ view: 'projects' })}>open target</button>
      ),
    }))
    vi.resetModules()
    const { AppShell: Fresh } = await import('./AppShell')
    const user = userEvent.setup()
    render(
      <Fresh
        {...delegateBase}
        onModeChange={onModeChange}
        onOpenAttentionTarget={onOpenAttentionTarget}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'open target' }))
    expect(onModeChange).toHaveBeenCalledWith('work')
    expect(onOpenAttentionTarget).toHaveBeenCalledWith({ view: 'projects' })
  })
})

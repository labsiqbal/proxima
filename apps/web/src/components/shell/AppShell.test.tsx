import '@testing-library/jest-dom/vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AppShell } from './AppShell'

vi.mock('./ToolDock', () => ({
  ToolDock: (props: {
    projects?: { slug: string }[]
    collapsed?: boolean
    sheetOpen?: boolean
    onOpenChange?: (open: boolean) => void
    onOpenFile?: (slug: string, path: string) => void
    onOpenAppViewport?: (slug: string) => void
  }) => (
    <div
      data-testid="tool-dock"
      data-projects={props.projects?.length ?? 0}
      data-collapsed={String(!!props.collapsed)}
      data-sheet={String(!!props.sheetOpen)}
    >
      <button type="button" onClick={() => props.onOpenFile?.('demo', 'notes.md')}>dock open file</button>
      <button type="button" onClick={() => props.onOpenAppViewport?.('demo')}>dock show app</button>
      {/* The dock reports every open and close (a reveal opening a tool, its own
          ✕, Escape); the shell's collapse state rides on those (#160/#156). */}
      <button type="button" onClick={() => props.onOpenChange?.(true)}>dock panel opened</button>
      <button type="button" onClick={() => props.onOpenChange?.(false)}>dock panel closed</button>
    </div>
  ),
}))
vi.mock('./AttentionInbox', () => ({ AttentionInbox: () => null }))
vi.mock('../master/MasterPopup', () => ({ MasterPopup: () => null }))
vi.mock('../master/MasterToastRegion', () => ({ MasterToastRegion: () => null }))
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
  user: { id: 1, username: 'owner', os_user: 'owner' },
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
      { slug: 'demo', name: 'Demo', path: '/tmp/demo', owner: 'o', visibility: 'private' as const },
      { slug: 'other', name: 'Other', path: '/tmp/other', owner: 'o', visibility: 'private' as const },
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

  // The dock owns the only file tree (#145); opening one of its files is a
  // main-window event the shell owner answers, not a panel-sized editor.
  it('hands a dock file open to the shell owner, with the project list for naming', async () => {
    const user = userEvent.setup()
    const onOpenFile = vi.fn()
    const projects = [{ slug: 'demo', name: 'Demo', path: '/tmp/demo', owner: 'o', visibility: 'private' as const }]
    render(
      <AppShell {...base} projects={projects} onOpenFile={onOpenFile}><div>main</div></AppShell>,
    )
    expect(screen.getByTestId('tool-dock')).toHaveAttribute('data-projects', '1')
    await user.click(screen.getByRole('button', { name: 'dock open file' }))
    expect(onOpenFile).toHaveBeenCalledWith('demo', 'notes.md')
  })

  // The run controls keep the same shape for the running app (#147): the dock
  // asks, the shell owner routes it to the Artifacts main window.
  it('hands the dock\u2019s app-viewport request to the shell owner', async () => {
    const user = userEvent.setup()
    const onOpenAppViewport = vi.fn()
    render(
      <AppShell {...base} onOpenAppViewport={onOpenAppViewport}><div>main</div></AppShell>,
    )
    await user.click(screen.getByRole('button', { name: 'dock show app' }))
    expect(onOpenAppViewport).toHaveBeenCalledWith('demo')
  })

  it('keeps Delegate global while retaining the accessible shell sidebar', async () => {
    const user = userEvent.setup()
    const onModeChange = vi.fn()
    render(<AppShell {...base} mode="delegate" onModeChange={onModeChange}><div>Master desk</div></AppShell>)
    expect(screen.getAllByRole('button', { name: 'Delegate' })[0]).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('navigation', { name: 'Delegate navigation' })).toBeInTheDocument()
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: 'Master' })).toBeInTheDocument()
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: 'Tasks' })).toBeInTheDocument()
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('button', { name: 'Artifacts' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Tools')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Active project:/ })).not.toBeInTheDocument()
    // The account menu stays: it is the only route to Projects, Agents,
    // Settings, and Log out, and Delegate must not strand the owner.
    expect(screen.getByRole('button', { name: 'Account actions' })).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Work' })[0])
    expect(onModeChange).toHaveBeenCalledWith('work')
  })

  it('opens the same focus-managed drawer for Delegate on small screens', async () => {
    const user = userEvent.setup()
    render(<AppShell {...base} mode="delegate"><div>Master desk</div></AppShell>)
    const mobile = document.querySelector('.delegate-mobile-topbar') as HTMLElement
    await user.click(within(mobile).getByRole('button', { name: 'Menu' }))
    await waitFor(() => expect(document.activeElement).toHaveAttribute('aria-label', 'Close menu'))
    expect(within(document.querySelector('.sidebar') as HTMLElement).getByRole('navigation', { name: 'Delegate navigation' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.activeElement).toBe(within(mobile).getByRole('button', { name: 'Menu' })))
  })
})

// #154: collapsing is a property of the sidebar, not of a mode. Delegate had no
// control at all even though its column was sized by Work's resize handle.
describe('AppShell sidebar collapse parity', () => {
  const desktop = () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query.includes('min-width: 768px'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  }
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('proxima.tour.coreDone', '1')
  })
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  for (const mode of ['work', 'delegate'] as const) {
    it(`collapses the sidebar to the rail from ${mode}`, async () => {
      desktop()
      const user = userEvent.setup()
      const { container } = render(
        <AppShell {...base} mode={mode} currentView={mode === 'delegate' ? 'master' : 'chat'}>
          <div>main</div>
        </AppShell>,
      )
      const shell = container.querySelector('.app-shell') as HTMLElement
      expect(shell).not.toHaveClass('left-rail')
      expect(shell.style.getPropertyValue('--left-w')).toBe('294px')

      const toggle = within(document.querySelector('.top-bar') as HTMLElement)
        .getByRole('button', { name: 'Toggle sidebar' })
      expect(toggle).toHaveAttribute('title', 'Collapse sidebar')
      await user.click(toggle)

      expect(shell).toHaveClass('left-rail')
      // Collapsed, the column is the derived rail token, never a literal (#153).
      expect(shell.style.getPropertyValue('--left-w')).toBe('var(--rail-w)')
      expect(toggle).toHaveAttribute('title', 'Expand sidebar')
      expect(localStorage.getItem('proxima.leftCollapsed')).toBe('1')
    })

    it(`offers the resize handle in ${mode}`, () => {
      desktop()
      render(
        <AppShell {...base} mode={mode} currentView={mode === 'delegate' ? 'master' : 'chat'}>
          <div>main</div>
        </AppShell>,
      )
      expect(screen.getByRole('separator', { name: 'Resize sidebar' })).toBeInTheDocument()
    })
  }

  it('renders the collapsed rail in Delegate with its destinations still reachable', () => {
    desktop()
    localStorage.setItem('proxima.leftCollapsed', '1')
    const { container } = render(
      <AppShell {...base} mode="delegate" currentView="master"><div>main</div></AppShell>,
    )
    expect(container.querySelector('.app-shell')).toHaveClass('left-rail')
    const sidebar = document.querySelector('.sidebar') as HTMLElement
    for (const name of ['Master', 'Tasks', 'Artifacts']) {
      expect(within(sidebar).getByRole('button', { name })).toBeInTheDocument()
    }
  })

  it('gives both mobile top bars the same Menu affordance', () => {
    const { container } = render(<AppShell {...base} mode="delegate"><div>main</div></AppShell>)
    const bar = container.querySelector('.delegate-mobile-topbar') as HTMLElement
    expect(within(bar).getByRole('button', { name: 'Menu' })).toBeInTheDocument()
  })
})

describe('AppShell delegate header status cluster', () => {
  const delegateBase = {
    ...base,
    mode: 'delegate' as const,
    currentView: 'master' as const,
  }

  it('keeps the status cluster and the account menu in Delegate', () => {
    const { container } = render(<AppShell {...delegateBase} />)
    expect(container.querySelector('.header-status-cluster')).toBeTruthy()
    // Projects, Agents, Settings, and Log out live only here, so Delegate needs it.
    expect(screen.getByLabelText('Account actions')).toBeInTheDocument()
    // Search stays Work-only (Delegate has nothing project-scoped to search).
    expect(screen.queryByLabelText('Search')).not.toBeInTheDocument()
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

// #160: the right dock collapses from the header like the left sidebar, with
// the same kind of persisted preference. #156: at phone width there is no rail
// to collapse, so the same control shows and hides the tool sheet.
describe('AppShell tool dock collapse', () => {
  /** Registered `(min-width: 768px)` change handlers, so a test can widen. */
  const listeners: (() => void)[] = []
  let isDesktop = false
  const width = (desktop: boolean) => {
    isDesktop = desktop
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      get matches() { return query.includes('min-width: 768px') ? isDesktop : !isDesktop },
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn((_: string, handler: () => void) => {
        if (query.includes('min-width: 768px')) listeners.push(handler)
      }),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  }
  const crossTo = (desktop: boolean) => {
    isDesktop = desktop
    act(() => { listeners.forEach(handler => handler()) })
  }
  const dock = () => screen.getByTestId('tool-dock')
  const headerToggle = () => within(document.querySelector('.top-bar') as HTMLElement)
    .getByRole('button', { name: 'Toggle tool dock' })

  beforeEach(() => {
    listeners.length = 0
    localStorage.clear()
    localStorage.setItem('proxima.tour.coreDone', '1')
  })
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('puts the dock away from the header and remembers it', async () => {
    width(true)
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    const shell = container.querySelector('.app-shell') as HTMLElement
    expect(shell).not.toHaveClass('dock-collapsed')
    expect(dock()).toHaveAttribute('data-collapsed', 'false')

    const toggle = headerToggle()
    // The pair reads as one: the two edge toggles sit next to each other.
    expect(toggle.previousElementSibling).toHaveAttribute('aria-label', 'Toggle sidebar')
    expect(toggle).toHaveAttribute('title', 'Collapse tool dock')
    await user.click(toggle)

    expect(shell).toHaveClass('dock-collapsed')
    expect(dock()).toHaveAttribute('data-collapsed', 'true')
    expect(toggle).toHaveAttribute('title', 'Expand tool dock')
    expect(localStorage.getItem('proxima.dockCollapsed')).toBe('1')
  })

  it('restores the collapsed dock from the stored preference', () => {
    width(true)
    localStorage.setItem('proxima.dockCollapsed', '1')
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    expect(container.querySelector('.app-shell')).toHaveClass('dock-collapsed')
    expect(dock()).toHaveAttribute('data-collapsed', 'true')
  })

  it('brings the dock back when a tool is opened from somewhere else', async () => {
    // A reveal or a run-controls request opens a tool while the dock is away;
    // leaving the panel hanging off a rail that is not rendered is not a state.
    width(true)
    localStorage.setItem('proxima.dockCollapsed', '1')
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    await user.click(screen.getByRole('button', { name: 'dock panel opened' }))
    expect(container.querySelector('.app-shell')).not.toHaveClass('dock-collapsed')
    expect(dock()).toHaveAttribute('data-collapsed', 'false')
  })

  it('closing a panel on a desktop leaves the rail where it is', async () => {
    width(true)
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    await user.click(screen.getByRole('button', { name: 'dock panel closed' }))
    expect(container.querySelector('.app-shell')).not.toHaveClass('dock-collapsed')
    expect(dock()).toHaveAttribute('data-sheet', 'false')
  })

  it('has no dock control in Delegate, which has no dock', () => {
    width(true)
    render(<AppShell {...base} mode="delegate" currentView="master"><div>main</div></AppShell>)
    expect(screen.queryByTestId('tool-dock')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Toggle tool dock' })).not.toBeInTheDocument()
  })

  it('has no dock control while Project tools are suppressed', () => {
    width(true)
    render(<AppShell {...base} projectToolsAvailable={false}><div>main</div></AppShell>)
    expect(screen.queryByRole('button', { name: 'Toggle tool dock' })).not.toBeInTheDocument()
  })

  it('opens the tool sheet from the mobile top bar instead of collapsing a rail', async () => {
    width(false)
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    const bar = container.querySelector('.mobile-topbar') as HTMLElement
    const toggle = within(bar).getByRole('button', { name: 'Toggle tool dock' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')

    await user.click(toggle)
    expect(dock()).toHaveAttribute('data-sheet', 'true')
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    // The phone sheet is transient: it never writes the desktop preference.
    expect(container.querySelector('.app-shell')).not.toHaveClass('dock-collapsed')
    expect(localStorage.getItem('proxima.dockCollapsed')).toBe('0')

    await user.click(toggle)
    expect(dock()).toHaveAttribute('data-sheet', 'false')
  })

  it('lets the sheet close itself - its own close is the sheet’s close', async () => {
    width(false)
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    const bar = container.querySelector('.mobile-topbar') as HTMLElement
    const toggle = within(bar).getByRole('button', { name: 'Toggle tool dock' })
    await user.click(toggle)
    expect(dock()).toHaveAttribute('data-sheet', 'true')

    await user.click(screen.getByRole('button', { name: 'dock panel closed' }))
    expect(dock()).toHaveAttribute('data-sheet', 'false')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
  })

  it('retires the sheet when the window widens into the desktop layout', async () => {
    // Found live: the sheet flag survived the resize, and the rail then refused
    // to collapse because an invisible phone state said a sheet was up.
    width(false)
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    const bar = container.querySelector('.mobile-topbar') as HTMLElement
    await user.click(within(bar).getByRole('button', { name: 'Toggle tool dock' }))
    expect(dock()).toHaveAttribute('data-sheet', 'true')

    crossTo(true)
    expect(dock()).toHaveAttribute('data-sheet', 'false')

    // And the header toggle now collapses the rail, as it should on a desktop.
    await user.click(headerToggle())
    expect(container.querySelector('.app-shell')).toHaveClass('dock-collapsed')
    expect(dock()).toHaveAttribute('data-collapsed', 'true')
  })

  it('adopts an open panel as the sheet when the window narrows', async () => {
    // The other direction: a desktop panel that becomes a sheet must be held by
    // a control that says so, or the first tap does nothing and the second one
    // closes what the owner was looking at.
    width(true)
    const user = userEvent.setup()
    const { container } = render(<AppShell {...base}><div>main</div></AppShell>)
    await user.click(screen.getByRole('button', { name: 'dock panel opened' }))
    expect(dock()).toHaveAttribute('data-sheet', 'false')

    crossTo(false)
    expect(dock()).toHaveAttribute('data-sheet', 'true')
    const bar = container.querySelector('.mobile-topbar') as HTMLElement
    expect(within(bar).getByRole('button', { name: 'Toggle tool dock' }))
      .toHaveAttribute('aria-pressed', 'true')
  })

  it('offers no mobile tool control while Project tools are suppressed', () => {
    width(false)
    const { container } = render(<AppShell {...base} projectToolsAvailable={false}><div>main</div></AppShell>)
    const bar = container.querySelector('.mobile-topbar') as HTMLElement
    expect(within(bar).queryByRole('button', { name: 'Toggle tool dock' })).not.toBeInTheDocument()
  })
})

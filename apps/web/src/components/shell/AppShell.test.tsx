import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AppShell } from './AppShell'

vi.mock('./ToolDock', () => ({ ToolDock: () => <div data-testid="tool-dock" /> }))
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
  features: { designStudio: true, workflowGraph: true },
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
})

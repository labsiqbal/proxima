import '@testing-library/jest-dom/vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { AppShell } from './AppShell'
import { Sidebar } from './Sidebar'

vi.mock('./ToolDock', () => ({ ToolDock: () => null }))
vi.mock('./AttentionInbox', () => ({ AttentionInbox: () => null }))
vi.mock('../master/MasterPopup', () => ({ MasterPopup: () => null }))
vi.mock('../master/MasterToastRegion', () => ({ MasterToastRegion: () => null }))
vi.mock('./RunningTasks', () => ({ RunningTasks: () => null }))

const stylesSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../../styles.css'),
  'utf8',
)
const withoutComments = stylesSource.replace(/\/\*[\s\S]*?\*\//g, '')
/** Body of the first rule whose selector list mentions every given selector. */
const ruleFor = (...selectors: string[]) => {
  for (const [, selector, body] of withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (selectors.every(one => selector.includes(one))) return body
  }
  return ''
}

const base = {
  activeProfile: null, activeProject: { id: 1, name: 'minarflow', slug: 'minarflow' } as never,
  activeSession: null, currentView: 'chat' as const,
  onClose: vi.fn(), onLogout: vi.fn(), onRenameSession: vi.fn(), onDeleteSession: vi.fn(),
  onSelectProject: vi.fn(), onSelectSession: vi.fn(), onOpenDesign: vi.fn(), onSelectView: vi.fn(),
  profiles: [], projects: [], sessions: [], seen: {}, token: 't',
  user: { id: 1, username: 'owner', os_user: 'owner' },
}

// #153. The collapsed rail is 52px of usable width, so anything that renders a
// text label there has to have somewhere else to put it. jsdom does not apply
// the app stylesheet, so the DOM contract (a targetable label element, the name
// still reachable) is asserted by rendering and the geometry contract by
// reading styles.css - the same split ChatThread.layout.test.tsx uses.
describe('Collapsed sidebar rail', () => {
  it('gives the eyebrow its own element so the rail can drop it instead of wrapping it', () => {
    const { container } = render(<Sidebar {...base} />)
    const context = container.querySelector('.sidebar-work-context') as HTMLElement
    const eyebrow = context.querySelector('.sidebar-work-eyebrow')
    expect(eyebrow).toHaveTextContent('Work project')
    // Hiding it must not take the switcher with it.
    expect(eyebrow).not.toContainElement(context.querySelector('.project-switcher'))
  })

  it('offers a compact project affordance whose full name stays reachable', () => {
    render(<Sidebar {...base} projects={[base.activeProject]} />)
    const trigger = screen.getByRole('button', { name: 'Active project: minarflow' })
    expect(trigger.querySelector('.project-switcher-initial')).toHaveTextContent('M')
    expect(trigger).toHaveAttribute('title', 'minarflow')
    // The popover is the other way out of the rail, so it keeps its trigger role.
    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox')
  })

  it('wraps the update pill label so the pill can become a tile', () => {
    render(<Sidebar {...base} updateVersion="1.9.0" onUpdateClick={vi.fn()} />)
    const pill = document.querySelector('.sidebar-update-pill') as HTMLElement
    expect(pill.querySelector('.sidebar-update-label')).toHaveTextContent('Update available · v1.9.0')
    expect(pill).toHaveAttribute('title', 'Update available · v1.9.0')
  })

  it('CSS hides every label the rail has no room for', () => {
    const hidden = ruleFor(
      '.app-shell.left-rail .sidebar .sidebar-work-eyebrow',
      '.app-shell.left-rail .sidebar .project-switcher-name',
      '.app-shell.left-rail .sidebar .project-switcher-caret',
      '.app-shell.left-rail .sidebar .sidebar-update-label',
    )
    expect(hidden).toMatch(/display:\s*none/)
    // The nav labels are hidden by the same rule but come back as tooltips.
    expect(withoutComments).toMatch(
      /\.app-shell\.left-rail \.nav-item:hover strong\s*\{[^}]*opacity:\s*1/,
    )
  })

  // #154: `.eyebrow` is an app-wide class. Collapsing the sidebar used to take
  // the main window's eyebrows ("CHAT", "YOUR ORCHESTRATOR") with it, because
  // the rail's hide list was not scoped to the sidebar.
  it('CSS confines the rail hide list to the sidebar', () => {
    for (const [, selector] of withoutComments.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
      for (const one of selector.split(',')) {
        const trimmed = one.trim()
        if (!trimmed.startsWith('.app-shell.left-rail')) continue
        if (!/(^|\s)\.eyebrow(\s|$|,)/.test(trimmed)) continue
        expect(trimmed).toContain('.sidebar')
      }
    }
  })

  it('CSS gives every rail tile one footprint, active or not', () => {
    const tile = ruleFor(
      '.app-shell.left-rail .nav-item',
      '.app-shell.left-rail .sidebar-work-context .project-switcher-btn',
      '.app-shell.left-rail .sidebar-update-pill',
    )
    expect(tile).toMatch(/width:\s*var\(--rail-tile-size\)/)
    expect(tile).toMatch(/height:\s*var\(--rail-tile-size\)/)
    expect(tile).toMatch(/border-radius:\s*var\(--radius-sm\)/)
    // The active state may only change the fill: no size or shape of its own.
    const activeBodies = [...withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .filter(([, selector]) => selector.includes('.nav-item.active'))
      .map(([, , body]) => body)
    expect(activeBodies.length).toBeGreaterThan(0)
    for (const body of activeBodies) {
      expect(body).not.toMatch(/(^|;)\s*(width|height|padding|border-radius):/)
    }
    // The work-context card drops its chrome so the tile is not a box in a box.
    const context = ruleFor('.app-shell.left-rail .sidebar-work-context')
    expect(context).toMatch(/border:\s*0/)
    expect(context).toMatch(/padding:\s*0/)
  })

  it('CSS derives the rail width and its vertical rhythm from the tile token', () => {
    const root = ruleFor(':root')
    expect(root).toMatch(/--rail-w:\s*calc\(var\(--rail-tile-size\) \+ \(2 \* var\(--rail-gutter\)\)\)/)
    expect(ruleFor('.app-shell.left-rail .sidebar-inner')).toMatch(
      /padding:\s*var\(--space-3\) var\(--rail-gutter\)/,
    )
    // One gutter between tiles as well as around them.
    expect(ruleFor('.app-shell.left-rail .nav-group')).toMatch(/gap:\s*var\(--rail-gutter\)/)
  })
})

describe('AppShell rail width', () => {
  beforeEach(() => {
    localStorage.setItem('proxima.tour.coreDone', '1')
    localStorage.setItem('proxima.leftCollapsed', '1')
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
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('collapses the sidebar to the rail token, not a loose literal', () => {
    const { container } = render(
      <AppShell {...base} onNewChat={vi.fn()}><div>main</div></AppShell>,
    )
    const shell = container.querySelector('.app-shell') as HTMLElement
    expect(shell.className).toContain('left-rail')
    expect(shell.style.getPropertyValue('--left-w')).toBe('var(--rail-w)')
  })
})

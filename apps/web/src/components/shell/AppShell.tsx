import React from 'react'
import type { AppFeatures, ChatSession, Profile, Project, User, View } from '../../types'
import { Sidebar } from './Sidebar'
import { MobileTopbar } from './MobileTopbar'
import { SearchModal } from './SearchModal'
import { ToolDock } from './ToolDock'
import { IconPanelLeft, IconGear, IconSearch, IconProjects, IconAgents, IconLogout, IconChevronLeft } from './icons'
import { ProximaMark } from '../brand/ProximaMark'
import { AttentionInbox } from './AttentionInbox'
import { RunningTasks } from './RunningTasks'
import { CoreTour } from './CoreTour'
import type { AttentionItem } from '../../api/master'
import { MasterPopup } from '../master/MasterPopup'
import { MasterToastRegion } from '../master/MasterToastRegion'
import { ShellModeSwitch, type ShellMode } from './ShellModeSwitch'

const matches = (query: string) =>
  typeof window !== 'undefined'
  && typeof window.matchMedia === 'function'
  && window.matchMedia(query).matches
const MOBILE_SHELL_QUERY = '(max-width: 767px)'
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))
const stored = (key: string, fallback: number) => {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null
  const value = raw == null ? NaN : Number(raw)
  return Number.isFinite(value) ? value : fallback
}

const LEFT_MIN = 200, LEFT_MAX = 480

function useMobileShell(): boolean {
  const [mobile, setMobile] = React.useState(() => matches(MOBILE_SHELL_QUERY))
  React.useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(MOBILE_SHELL_QUERY)
    const update = () => setMobile(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return mobile
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
function focusableIn(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(el => {
    if (el.getAttribute('aria-hidden') === 'true') return false
    const style = window.getComputedStyle(el)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
}

export function AppShell(props: {
  children: React.ReactNode
  activeProfile: Profile | null
  activeProject: Project | null
  activeSession: ChatSession | null
  currentView: View
  features: AppFeatures
  onNewChat: () => void
  onRenameSession: (id: number, title: string) => void
  onDeleteSession: (id: number) => void
  /** Header project switcher: filter shell active project without changing view. */
  onSelectProject: (project: Project) => void
  /** After rename from the switcher menu — update list + active project labels. */
  onProjectRenamed?: (project: Project) => void
  /** Search / intentional open: select project and navigate to its chat when provided. */
  onOpenProject?: (project: Project) => void
  onSelectSession: (session: ChatSession) => void
  onOpenDesign: (session: ChatSession) => void
  seen: Record<number, string>
  busySessions?: number[]
  onSelectView: (view: View) => void
  onOpenAttentionTarget?: (target: AttentionItem['target']) => void
  onOpenRunningJob?: (id: number, engine?: string) => void
  onOpenRunningSession?: (sessionId: number) => void
  onLogout: () => void
  profiles: Profile[]
  projects: Project[]
  sessions: ChatSession[]
  token: string
  user: User
  updateVersion?: string | null
  onUpdateClick?: () => void
  /**
   * Chrome Back: always rendered. Disabled when the deep stack is empty;
   * when enabled, returns to the origin surface (not a hard-coded parent).
   */
  chromeBackEnabled?: boolean
  chromeBackLabel?: string
  onChromeBack?: () => void
  /** Deep surfaces lock the header/mobile project switcher. */
  projectLocked?: boolean
  projectLockedReason?: string
  /** Project-bound tools stay hidden until a deep Task and shell Project agree. */
  projectToolsAvailable?: boolean
  mode?: ShellMode
  onModeChange?: (mode: ShellMode) => void
}) {
  const [drawerOpen, setDrawerOpen] = React.useState(false)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [searchOpen, setSearchOpen] = React.useState(false)
  const [toolOpen, setToolOpen] = React.useState(false)
  const [leftWidth, setLeftWidth] = React.useState(() => stored('proxima.leftWidth', 294))
  const [leftCollapsed, setLeftCollapsed] = React.useState(() => (typeof localStorage !== 'undefined' && localStorage.getItem('proxima.leftCollapsed') === '1'))
  const mobileShell = useMobileShell()
  const sidebarRef = React.useRef<HTMLElement>(null)
  const menuBtnRef = React.useRef<HTMLButtonElement>(null)
  const drawerWasOpen = React.useRef(false)

  const delegateMode = props.mode === 'delegate' && props.features.masterOrchestrator
  const openSearch = React.useCallback(() => {
    if (delegateMode) return
    setDrawerOpen(false)
    setMenuOpen(false)
    setSearchOpen(true)
  }, [delegateMode])

  React.useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        // Close the topmost overlay first so Escape does not wipe everything at once.
        if (searchOpen) { setSearchOpen(false); return }
        if (menuOpen) { setMenuOpen(false); return }
        if (drawerOpen) { setDrawerOpen(false); return }
        return
      }
      // Desktop and mobile keyboard path into Search (Ctrl/Cmd+K), including from the chat box.
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        openSearch()
      }
    }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [drawerOpen, menuOpen, searchOpen, openSearch])

  // When the mobile drawer opens, move focus inside it and trap Tab until it closes.
  // When it closes, return focus to the Menu button that opened it.
  React.useEffect(() => {
    if (drawerOpen) {
      drawerWasOpen.current = true
      const id = window.requestAnimationFrame(() => {
        const items = focusableIn(sidebarRef.current)
        const closeBtn = items.find(el => el.getAttribute('aria-label') === 'Close menu') || items[0]
        closeBtn?.focus()
      })
      const onKey = (event: KeyboardEvent) => {
        if (event.key !== 'Tab' || !sidebarRef.current) return
        const items = focusableIn(sidebarRef.current)
        if (items.length === 0) return
        const first = items[0]
        const last = items[items.length - 1]
        const active = document.activeElement as HTMLElement | null
        if (event.shiftKey) {
          if (active === first || !sidebarRef.current.contains(active)) {
            event.preventDefault()
            last.focus()
          }
        } else if (active === last || !sidebarRef.current.contains(active)) {
          event.preventDefault()
          first.focus()
        }
      }
      window.addEventListener('keydown', onKey)
      return () => {
        window.cancelAnimationFrame(id)
        window.removeEventListener('keydown', onKey)
      }
    }
    if (drawerWasOpen.current) {
      drawerWasOpen.current = false
      const active = document.activeElement
      if (!active || active === document.body || sidebarRef.current?.contains(active)) {
        menuBtnRef.current?.focus()
      }
    }
  }, [drawerOpen])

  React.useEffect(() => { localStorage.setItem('proxima.leftWidth', String(leftWidth)) }, [leftWidth])
  React.useEffect(() => { localStorage.setItem('proxima.leftCollapsed', leftCollapsed ? '1' : '0') }, [leftCollapsed])

  const toggleLeft = () => { if (matches('(min-width: 768px)')) setLeftCollapsed(value => !value); else setDrawerOpen(value => !value) }
  const startResize = (event: React.PointerEvent) => {
    event.preventDefault()
    const pointerId = event.pointerId
    const startX = event.clientX
    const startLeft = leftWidth
    const onMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return
      setLeftWidth(clamp(startLeft + (moveEvent.clientX - startX), LEFT_MIN, LEFT_MAX))
    }
    const onUp = (upEvent: PointerEvent) => {
      if (upEvent.pointerId !== pointerId) return
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }
  const resizeByKey = (event: React.KeyboardEvent) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const direction = event.key === 'ArrowRight' ? 1 : -1
    setLeftWidth(value => clamp(value + direction * 10, LEFT_MIN, LEFT_MAX))
  }
  // Delegate keeps its three global destinations visible. The Work preference to
  // collapse its navigation never hides this bounded navigation surface.
  const effectiveLeftCollapsed = !delegateMode && leftCollapsed
  const shellStyle = { ['--left-w']: effectiveLeftCollapsed ? '58px' : `${leftWidth}px` } as React.CSSProperties
  const statusControls = (
    <div className="shell-status-controls">
      <RunningTasks
        token={props.token}
        sessions={props.sessions}
        onOpenJob={props.onOpenRunningJob}
        onOpenSession={props.onOpenRunningSession}
        onOpenTasks={() => props.onSelectView('activity')}
      />
      <span className="attention-control-slot">
        <AttentionInbox token={props.token} onOpenTarget={target => props.onOpenAttentionTarget?.(target)} />
      </span>
    </div>
  )

  // Account menu "Projects" opens Settings → Projects manage (not a primary nav destination).
  const openProjectsManage = () => {
    props.onSelectView('projects')
    setMenuOpen(false)
  }

  return (
    <div className={`app-shell ${effectiveLeftCollapsed ? 'left-rail' : ''} ${delegateMode ? 'delegate-mode' : 'work-mode'} ${props.projectToolsAvailable === false ? 'project-tools-suppressed' : ''} ${props.features.masterOrchestrator ? 'master-enabled' : ''}`} style={shellStyle}>
      <header className="top-bar">
        {/* Brand lives up here, not in the sidebar, so collapsing the sidebar never
            takes away who you are (the mark). The drawer keeps its own copy for
            mobile, where this bar hides. */}
        <div className="top-bar-brand"><ProximaMark /><strong className="proxima-word">PROXIMA</strong></div>
        <ShellModeSwitch mode={delegateMode ? 'delegate' : 'work'} delegateEnabled={props.features.masterOrchestrator} onChange={mode => props.onModeChange?.(mode)} />
        {!delegateMode && <>
        <button className="tool-btn" onClick={toggleLeft} aria-label="Toggle sidebar" title={leftCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}><IconPanelLeft size={17} /></button>
        {/* Global chrome Back — always visible; disabled without a deep stack (Chrome-like). */}
        <button
          type="button"
          className="tool-btn chrome-back-btn"
          disabled={!props.chromeBackEnabled}
          onClick={() => props.onChromeBack?.()}
          aria-label={props.chromeBackEnabled ? (props.chromeBackLabel || 'Back') : 'Back'}
          title={props.chromeBackEnabled ? (props.chromeBackLabel || 'Back') : 'Back'}
        >
          <IconChevronLeft size={17} />
          {props.chromeBackEnabled && (
            <span className="chrome-back-label">
              {props.chromeBackLabel || 'Back'}
            </span>
          )}
        </button>
        <button className="tool-btn" onClick={openSearch} aria-label="Search" title="Search"><IconSearch size={17} /></button>
        </>}
        <span className="top-bar-spacer" />
        {!delegateMode && <div className="user-menu-wrap">
          <button className={`tool-btn user-avatar-btn ${menuOpen ? 'active' : ''}`} onClick={() => setMenuOpen(open => !open)} aria-label="Account actions" aria-expanded={menuOpen} aria-controls="account-actions" title={props.user.username}><span className="avatar xs">{props.user.username[0]?.toUpperCase()}</span></button>
          {menuOpen && <>
            <button className="menu-scrim" aria-label="Close account actions" onClick={() => setMenuOpen(false)} />
            <div className="user-menu" id="account-actions">
              <div className="user-menu-head"><span className="avatar">{props.user.username[0]?.toUpperCase()}</span><div><strong>{props.user.username}</strong><small>{props.activeProfile?.name || ''}</small></div></div>
              <button className="user-menu-item" onClick={openProjectsManage}><IconProjects size={15} /> Projects</button>
              <button className={`user-menu-item ${props.currentView === 'profiles' ? 'active' : ''}`} onClick={() => { props.onSelectView('profiles'); setMenuOpen(false) }}><IconAgents size={15} /> Agents</button>
              <div className="user-menu-sep" />
              <button className={`user-menu-item ${props.currentView === 'settings' ? 'active' : ''}`} onClick={() => { props.onSelectView('settings'); setMenuOpen(false) }}><IconGear size={15} /> Settings</button>
              <div className="user-menu-sep" />
              <button className="user-menu-item" onClick={() => { setMenuOpen(false); props.onLogout() }}><IconLogout size={15} /> Log out</button>
            </div>
          </>}
        </div>}
      </header>
      {!delegateMode && !mobileShell && <div className="header-status-cluster">{statusControls}</div>}
      {!delegateMode ? <MobileTopbar
        activeProject={props.activeProject}
        projects={props.projects}
        onSelectProject={props.onSelectProject}
        token={props.token}
        onProjectRenamed={props.onProjectRenamed}
        projectLocked={!!props.projectLocked}
        projectLockedReason={props.projectLockedReason}
        chromeBackEnabled={!!props.chromeBackEnabled}
        chromeBackLabel={props.chromeBackLabel}
        onChromeBack={props.onChromeBack}
        drawerOpen={drawerOpen}
        onMenu={() => setDrawerOpen(true)}
        onSearch={openSearch}
        onNewChat={props.onNewChat}
        menuButtonRef={menuBtnRef}
        mode="work"
        delegateEnabled={props.features.masterOrchestrator}
        onModeChange={props.onModeChange}
        statusControls={mobileShell ? statusControls : undefined}
      /> : <header className="mobile-topbar delegate-mobile-topbar">
        <button ref={menuBtnRef} className="icon-button" onClick={() => setDrawerOpen(true)} aria-label="Menu" aria-expanded={drawerOpen} aria-controls="mobile-nav-drawer"><IconPanelLeft size={18} /></button>
        <div className="mobile-context"><ShellModeSwitch mode="delegate" delegateEnabled onChange={mode => props.onModeChange?.(mode)} /></div>
      </header>}
      <aside ref={sidebarRef} className={`sidebar ${drawerOpen ? 'is-open' : ''}`} id="mobile-nav-drawer">
        <Sidebar {...props} delegate={delegateMode} onClose={() => setDrawerOpen(false)} />
      </aside>
      {!delegateMode && <div className="resize-handle resize-left" style={{ left: 'var(--left-w)' }} onPointerDown={startResize} onKeyDown={resizeByKey} role="separator" tabIndex={0} aria-orientation="vertical" aria-valuemin={LEFT_MIN} aria-valuemax={LEFT_MAX} aria-valuenow={leftWidth} aria-label="Resize sidebar" />}
      {drawerOpen && <button aria-label="Close menu" className="drawer-scrim" onClick={() => setDrawerOpen(false)} />}
      <main className="main-pane">{props.children}</main>
      {props.features.masterOrchestrator && !delegateMode && (
        <>
          <MasterPopup
            token={props.token}
            available={
              !drawerOpen
              && !menuOpen
              && !searchOpen
              && !toolOpen
              && props.currentView !== 'master'
              && props.currentView !== 'home'
            }
            onOpenHome={() => props.onSelectView('master')}
            onOpenJob={(id, engine) => props.onOpenRunningJob?.(id, engine)}
          />
          <MasterToastRegion
            available={!drawerOpen && !menuOpen && !searchOpen && !toolOpen}
          />
        </>
      )}
      {!delegateMode && <ToolDock
        token={props.token}
        project={props.activeProject}
        available={props.projectToolsAvailable !== false}
        onOpenSettings={() => props.onSelectView('settings')}
        onOpenChange={setToolOpen}
      />}
      {!delegateMode && searchOpen && <SearchModal token={props.token} sessions={props.sessions} projects={props.projects} features={props.features} onClose={() => setSearchOpen(false)} onSelectSession={props.onSelectSession} onOpenDesign={props.onOpenDesign} onSelectProject={props.onOpenProject ?? props.onSelectProject} onSelectView={props.onSelectView} />}
      {!delegateMode && <CoreTour token={props.token} masterEnabled={props.features.masterOrchestrator} />}
    </div>
  )
}

import React from 'react'
import type { ChatSession, FileTarget, Profile, Project, User, View } from '../../types'
import { Sidebar } from './Sidebar'
import { MobileTopbar } from './MobileTopbar'
import { SearchModal } from './SearchModal'
import { ToolDock } from './ToolDock'
import { IconPanelLeft, IconPanelRight, IconGear, IconMenu, IconSearch, IconProjects, IconAgents, IconLogout, IconChevronLeft } from './icons'
import { ProximaMark } from '../brand/ProximaMark'
import { AttentionInbox } from './AttentionInbox'
import { RunningTasks } from './RunningTasks'
import { CoreTour } from './CoreTour'
import type { AttentionItem } from '../../api/master'
import { MasterPopup } from '../master/MasterPopup'
import { MasterToastRegion } from '../master/MasterToastRegion'
import { ShellModeSwitch, type ShellMode } from './ShellModeSwitch'

const matches = (query: string) => typeof window !== 'undefined' && window.matchMedia(query).matches
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))
const stored = (key: string, fallback: number) => {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null
  const value = raw == null ? NaN : Number(raw)
  return Number.isFinite(value) ? value : fallback
}

const LEFT_MIN = 200, LEFT_MAX = 480

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
  /**
   * Opening a file from the dock browser (#145): the shell owner decides which
   * main-window surface answers, so the dock never grows a viewer of its own.
   */
  onOpenFile?: (slug: string, path: string, target?: FileTarget) => void
  /** Same seam for the running app's viewport (#147): the dock names the
   *  project, the shell owner opens it in the Artifacts main window. */
  onOpenAppViewport?: (slug: string) => void
  mode?: ShellMode
  onModeChange?: (mode: ShellMode) => void
}) {
  const [drawerOpen, setDrawerOpen] = React.useState(false)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [searchOpen, setSearchOpen] = React.useState(false)
  const [toolOpen, setToolOpen] = React.useState(false)
  const [leftWidth, setLeftWidth] = React.useState(() => stored('proxima.leftWidth', 294))
  const [leftCollapsed, setLeftCollapsed] = React.useState(() => (typeof localStorage !== 'undefined' && localStorage.getItem('proxima.leftCollapsed') === '1'))
  // The right dock collapses like the left sidebar, from the same header, with
  // the same kind of persisted preference (#160). Work only: Delegate has no
  // dock, so it gets no control for one.
  const [dockCollapsed, setDockCollapsed] = React.useState(() => (typeof localStorage !== 'undefined' && localStorage.getItem('proxima.dockCollapsed') === '1'))
  // Phone width has no rail to collapse (#156), so the same control shows and
  // hides the tool sheet instead - the shape `toggleLeft` already uses for the
  // sidebar and its drawer. Transient by design: it is a sheet, not a layout.
  const [dockSheetOpen, setDockSheetOpen] = React.useState(false)
  const sidebarRef = React.useRef<HTMLElement>(null)
  const menuBtnRef = React.useRef<HTMLButtonElement>(null)
  const drawerWasOpen = React.useRef(false)

  const delegateMode = props.mode === 'delegate'
  // Delegate keeps the global status cluster, but its targets are Work surfaces
  // (a chat thread, a workflow editor, an Ops migration screen) that Delegate
  // navigation cannot render. Switch back first, then open — mode change runs in
  // the same tick, so the opener's own setView still wins.
  const openInWork = React.useCallback(<A extends unknown[]>(open?: (...args: A) => void) => (
    (...args: A) => {
      if (delegateMode) props.onModeChange?.('work')
      open?.(...args)
    }
  ), [delegateMode, props.onModeChange])
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
  React.useEffect(() => { localStorage.setItem('proxima.dockCollapsed', dockCollapsed ? '1' : '0') }, [dockCollapsed])
  // The sheet belongs to phone width. Widening the window into the desktop
  // layout retires it, or the rail inherits a phone decision - a dock that
  // refuses to collapse because an invisible sheet flag is still set.
  React.useEffect(() => {
    const desktop = typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(min-width: 768px)')
      : null
    if (!desktop) return
    const sync = () => { if (desktop.matches) setDockSheetOpen(false) }
    sync()
    desktop.addEventListener?.('change', sync)
    return () => desktop.removeEventListener?.('change', sync)
  }, [])

  const toggleLeft = () => { if (matches('(min-width: 768px)')) setLeftCollapsed(value => !value); else setDrawerOpen(value => !value) }
  const toggleDock = () => { if (matches('(min-width: 768px)')) setDockCollapsed(value => !value); else setDockSheetOpen(value => !value) }
  // The dock reports every open and close here. Two things ride on it: the
  // Master trigger stands down while a tool is up, and a tool opened from
  // somewhere else - a reveal, a run-controls request - brings the dock back
  // instead of leaving a panel with no rail behind it. On a phone the panel is
  // the dock, so its own close (its ✕, Escape) is the sheet's close too.
  const onDockOpenChange = React.useCallback((open: boolean) => {
    setToolOpen(open)
    if (matches('(min-width: 768px)')) { if (open) setDockCollapsed(false) }
    else setDockSheetOpen(open)
  }, [])
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
  // One sidebar, one width preference, one collapse preference — both modes read
  // them (#154). Delegate used to opt out of collapsing, which left its
  // navigation as the only column in the app you could not put away even though
  // its width was still being set by Work's resize handle.
  // Collapsed, the sidebar's width is the rail token (tile + two gutters), so the
  // column and the tiles inside it can never drift apart. See --rail-w.
  const shellStyle = { ['--left-w']: leftCollapsed ? 'var(--rail-w)' : `${leftWidth}px` } as React.CSSProperties

  // Account menu "Projects" opens Settings → Projects manage (not a primary nav destination).
  const openProjectsManage = () => {
    props.onSelectView('projects')
    setMenuOpen(false)
  }

  return (
    <div className={`app-shell ${leftCollapsed ? 'left-rail' : ''} ${!delegateMode && dockCollapsed ? 'dock-collapsed' : ''} ${delegateMode ? 'delegate-mode' : 'work-mode'} ${props.projectToolsAvailable === false ? 'project-tools-suppressed' : ''}`} style={shellStyle}>
      <header className="top-bar">
        {/* Brand lives up here, not in the sidebar, so collapsing the sidebar never
            takes away who you are (the mark). The drawer keeps its own copy for
            mobile, where this bar hides. */}
        <div className="top-bar-brand"><ProximaMark /><strong className="proxima-word">PROXIMA</strong></div>
        <ShellModeSwitch mode={delegateMode ? 'delegate' : 'work'} delegateEnabled onChange={mode => props.onModeChange?.(mode)} />
        {/* Collapse is a property of the sidebar, not of a mode: both navigations
            put away to the same rail with the same control (#154). */}
        <button className="tool-btn" onClick={toggleLeft} aria-label="Toggle sidebar" title={leftCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}><IconPanelLeft size={17} /></button>
        {!delegateMode && <>
        {/* Its twin for the right dock (#160), next to it so the two edges of
            the shell are put away from the same place. Absent while Project
            tools are suppressed: a toggle for a dock that is not there is a
            dead control. Delegate has no dock at all. */}
        {props.projectToolsAvailable !== false && <button className="tool-btn" onClick={toggleDock} aria-label="Toggle tool dock" title={dockCollapsed ? 'Expand tool dock' : 'Collapse tool dock'}><IconPanelRight size={17} /></button>}
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
        {/* The account menu is the only way to reach Projects, Agents, Settings,
            and Log out, so Delegate keeps it too — hiding it stranded owners in a
            mode with no way out to their own settings. Its entries are Work
            surfaces, so each switches back first. */}
        <div className="user-menu-wrap">
          <button className={`tool-btn user-avatar-btn ${menuOpen ? 'active' : ''}`} onClick={() => setMenuOpen(open => !open)} aria-label="Account actions" aria-expanded={menuOpen} aria-controls="account-actions" title={props.user.username}><span className="avatar xs">{props.user.username[0]?.toUpperCase()}</span></button>
          {menuOpen && <>
            <button className="menu-scrim" aria-label="Close account actions" onClick={() => setMenuOpen(false)} />
            <div className="user-menu" id="account-actions">
              <div className="user-menu-head"><span className="avatar">{props.user.username[0]?.toUpperCase()}</span><div><strong>{props.user.username}</strong><small>{props.activeProfile?.name || ''}</small></div></div>
              <button className="user-menu-item" onClick={openInWork(openProjectsManage)}><IconProjects size={15} /> Projects</button>
              <button className={`user-menu-item ${props.currentView === 'profiles' ? 'active' : ''}`} onClick={openInWork(() => { props.onSelectView('profiles'); setMenuOpen(false) })}><IconAgents size={15} /> Agents</button>
              <div className="user-menu-sep" />
              <button className={`user-menu-item ${props.currentView === 'settings' ? 'active' : ''}`} onClick={openInWork(() => { props.onSelectView('settings'); setMenuOpen(false) })}><IconGear size={15} /> Settings</button>
              <div className="user-menu-sep" />
              <button className="user-menu-item" onClick={() => { setMenuOpen(false); props.onLogout() }}><IconLogout size={15} /> Log out</button>
            </div>
          </>}
        </div>
      </header>
      {/* Shown in both modes: watching delegated work is what Delegate is for, and
          "N tasks running" plus Needs-you is the pair that answers it. Account,
          search, and tool surfaces stay Work-only. Tasks is `activity` in both
          navigations, so that one target needs no mode switch. */}
      <div className="header-status-cluster">
        <RunningTasks
          token={props.token}
          sessions={props.sessions}
          onOpenJob={openInWork(props.onOpenRunningJob)}
          onOpenSession={openInWork(props.onOpenRunningSession)}
          onOpenTasks={() => props.onSelectView('activity')}
        />
        {/* Inbox is a destination in both modes, so opening it never forces a
            mode switch the way a Work-only target does. */}
        <AttentionInbox
          token={props.token}
          onOpenTarget={openInWork(props.onOpenAttentionTarget)}
          onOpenInbox={() => props.onSelectView('inbox')}
        />
      </div>
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
        toolsOpen={dockSheetOpen}
        onToggleTools={props.projectToolsAvailable === false ? undefined : toggleDock}
        menuButtonRef={menuBtnRef}
        mode="work"
        delegateEnabled
        onModeChange={props.onModeChange}
      /> : <header className="mobile-topbar delegate-mobile-topbar">
        {/* Same glyph as Work's Menu: one drawer, one affordance (#154). */}
        <button ref={menuBtnRef} className="icon-button" onClick={() => setDrawerOpen(true)} aria-label="Menu" aria-expanded={drawerOpen} aria-controls="mobile-nav-drawer"><IconMenu size={18} /></button>
        <div className="mobile-context"><ShellModeSwitch mode="delegate" delegateEnabled onChange={mode => props.onModeChange?.(mode)} /></div>
      </header>}
      <aside ref={sidebarRef} className={`sidebar ${drawerOpen ? 'is-open' : ''}`} id="mobile-nav-drawer">
        <Sidebar {...props} delegate={delegateMode} onClose={() => setDrawerOpen(false)} />
      </aside>
      {/* The width the handle sets is the width both modes render, so both modes
          get the handle — otherwise Delegate's column is sized by a control that
          only exists in Work (#154). */}
      <div className="resize-handle resize-left" style={{ left: 'var(--left-w)' }} onPointerDown={startResize} onKeyDown={resizeByKey} role="separator" tabIndex={0} aria-orientation="vertical" aria-valuemin={LEFT_MIN} aria-valuemax={LEFT_MAX} aria-valuenow={leftWidth} aria-label="Resize sidebar" />
      {drawerOpen && <button aria-label="Close menu" className="drawer-scrim" onClick={() => setDrawerOpen(false)} />}
      <main className="main-pane">{props.children}</main>
      {!delegateMode && (
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
        projects={props.projects}
        available={props.projectToolsAvailable !== false}
        collapsed={dockCollapsed}
        sheetOpen={dockSheetOpen}
        onOpenSettings={() => props.onSelectView('settings')}
        onOpenChange={onDockOpenChange}
        onOpenFile={props.onOpenFile}
        onOpenAppViewport={props.onOpenAppViewport}
      />}
      {!delegateMode && searchOpen && <SearchModal token={props.token} sessions={props.sessions} projects={props.projects} onClose={() => setSearchOpen(false)} onSelectSession={props.onSelectSession} onOpenDesign={props.onOpenDesign} onSelectProject={props.onOpenProject ?? props.onSelectProject} onSelectView={props.onSelectView} />}
      {!delegateMode && <CoreTour token={props.token} />}
    </div>
  )
}

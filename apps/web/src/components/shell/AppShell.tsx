import React from 'react'
import type { AppFeatures, ChatSession, Profile, Project, User, View } from '../../types'
import { Sidebar } from './Sidebar'
import { MobileTopbar } from './MobileTopbar'
import { SearchModal } from './SearchModal'
import { IconPanelLeft, IconGear, IconSearch, IconProjects, IconAgents, IconLogout, IconHome, IconTerminal } from './icons'
import { ProximaMark } from '../brand/ProximaMark'

const matches = (query: string) => typeof window !== 'undefined' && window.matchMedia(query).matches
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))
const stored = (key: string, fallback: number) => {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null
  const value = raw == null ? NaN : Number(raw)
  return Number.isFinite(value) ? value : fallback
}

const LEFT_MIN = 200, LEFT_MAX = 480

export function AppShell(props: {
  children: React.ReactNode
  activeProfile: Profile | null
  activeProject: Project | null
  activeSession: ChatSession | null
  currentView: View
  workspaceMode: 'ops' | 'code'
  onSelectWorkspace: (mode: 'ops' | 'code') => void
  features: AppFeatures
  onNewChat: () => void
  onRenameSession: (id: number, title: string) => void
  onDeleteSession: (id: number) => void
  onSelectProject: (project: Project) => void
  onSelectSession: (session: ChatSession) => void
  onOpenDesign: (session: ChatSession) => void
  seen: Record<number, string>
  busySessions?: number[]
  onSelectView: (view: View) => void
  onLogout: () => void
  profiles: Profile[]
  projects: Project[]
  sessions: ChatSession[]
  token: string
  user: User
  updateVersion?: string | null
  onUpdateClick?: () => void
}) {
  const [drawerOpen, setDrawerOpen] = React.useState(false)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [searchOpen, setSearchOpen] = React.useState(false)
  const [leftWidth, setLeftWidth] = React.useState(() => stored('proxima.leftWidth', 294))
  const [leftCollapsed, setLeftCollapsed] = React.useState(() => (typeof localStorage !== 'undefined' && localStorage.getItem('proxima.leftCollapsed') === '1'))

  React.useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setDrawerOpen(false); setMenuOpen(false); setSearchOpen(false)
    }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [])
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
  const shellStyle = { ['--left-w']: leftCollapsed ? '58px' : `${leftWidth}px` } as React.CSSProperties

  return (
    <div className={`app-shell ${leftCollapsed ? 'left-rail' : ''}`} style={shellStyle}>
      <header className="top-bar">
        {/* Brand and workspace switcher live up here, not in the sidebar, so collapsing
            the sidebar never takes away who you are (the mark) or where you can go
            (Ops/Code). The drawer keeps its own copy for mobile, where this bar hides. */}
        <div className="top-bar-brand"><ProximaMark /><strong className="proxima-word">PROXIMA</strong></div>
        <div className="workspace-switch top-bar-switch" role="group" aria-label="Workspace">
          <button className={props.workspaceMode === 'ops' ? 'active' : ''} aria-pressed={props.workspaceMode === 'ops'} onClick={() => props.onSelectWorkspace('ops')}><IconHome size={14} /><span>Ops</span></button>
          <button className={props.workspaceMode === 'code' ? 'active' : ''} aria-pressed={props.workspaceMode === 'code'} onClick={() => props.onSelectWorkspace('code')}><IconTerminal size={14} /><span>Code</span></button>
        </div>
        <button className="tool-btn" onClick={toggleLeft} aria-label="Toggle sidebar" title={leftCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}><IconPanelLeft size={17} /></button>
        <button className="tool-btn" onClick={() => setSearchOpen(true)} aria-label="Search" title="Search"><IconSearch size={17} /></button>
        <span className="top-bar-spacer" />
        <div className="user-menu-wrap">
          <button className={`tool-btn user-avatar-btn ${menuOpen ? 'active' : ''}`} onClick={() => setMenuOpen(open => !open)} aria-label="Account actions" aria-expanded={menuOpen} aria-controls="account-actions" title={props.user.username}><span className="avatar xs">{props.user.username[0]?.toUpperCase()}</span></button>
          {menuOpen && <>
            <button className="menu-scrim" aria-label="Close account actions" onClick={() => setMenuOpen(false)} />
            <div className="user-menu" id="account-actions">
              <div className="user-menu-head"><span className="avatar">{props.user.username[0]?.toUpperCase()}</span><div><strong>{props.user.username}</strong><small>{props.activeProfile?.name || ''}</small></div></div>
              <button className={`user-menu-item ${props.currentView === 'projects' ? 'active' : ''}`} onClick={() => { props.onSelectView('projects'); setMenuOpen(false) }}><IconProjects size={15} /> Projects</button>
              <button className={`user-menu-item ${props.currentView === 'profiles' ? 'active' : ''}`} onClick={() => { props.onSelectView('profiles'); setMenuOpen(false) }}><IconAgents size={15} /> Agents</button>
              <div className="user-menu-sep" />
              <button className={`user-menu-item ${props.currentView === 'settings' ? 'active' : ''}`} onClick={() => { props.onSelectView('settings'); setMenuOpen(false) }}><IconGear size={15} /> Settings</button>
              <div className="user-menu-sep" />
              <button className="user-menu-item" onClick={() => { setMenuOpen(false); props.onLogout() }}><IconLogout size={15} /> Log out</button>
            </div>
          </>}
        </div>
      </header>
      <MobileTopbar activeProject={props.activeProject} onMenu={() => setDrawerOpen(true)} onNewChat={props.onNewChat} />
      <aside className={`sidebar ${drawerOpen ? 'is-open' : ''}`}>
        <Sidebar {...props} onClose={() => setDrawerOpen(false)} />
      </aside>
      <div className="resize-handle resize-left" style={{ left: 'var(--left-w)' }} onPointerDown={startResize} onKeyDown={resizeByKey} role="separator" tabIndex={0} aria-orientation="vertical" aria-valuemin={LEFT_MIN} aria-valuemax={LEFT_MAX} aria-valuenow={leftWidth} aria-label="Resize sidebar" />
      {drawerOpen && <button aria-label="Close menu" className="drawer-scrim" onClick={() => setDrawerOpen(false)} />}
      <main className="main-pane">{props.children}</main>
      {searchOpen && <SearchModal token={props.token} sessions={props.sessions} projects={props.projects} features={props.features} onClose={() => setSearchOpen(false)} onSelectSession={props.onSelectSession} onSelectProject={props.onSelectProject} onSelectView={props.onSelectView} />}
    </div>
  )
}

import React from 'react'
import type { AppFeatures, ChatSession, Profile, Project, User, View } from '../../types'
import { Sidebar } from './Sidebar'
import { MobileTopbar } from './MobileTopbar'
import { RightRail } from './RightRail'
import { SearchModal } from './SearchModal'
import { IconPanelRight, IconPanelLeft, IconGear, IconSearch, IconProjects, IconAgents, IconLogout } from './icons'

const matches = (query: string) => typeof window !== 'undefined' && window.matchMedia(query).matches
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const stored = (key: string, fallback: number) => {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null
  const n = raw == null ? NaN : Number(raw)
  return Number.isFinite(n) ? n : fallback
}

const LEFT_MIN = 200, LEFT_MAX = 480
const RIGHT_MIN = 220, RIGHT_MAX = 600

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
  onSelectProject: (project: Project) => void
  onSelectSession: (session: ChatSession) => void
  onOpenDesign: (session: ChatSession) => void
  onOpenFile: (slug: string, path: string) => void
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
  const [railOpen, setRailOpen] = React.useState(false)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [searchOpen, setSearchOpen] = React.useState(false)
  const [leftWidth, setLeftWidth] = React.useState(() => stored('proxima.leftWidth', 294))
  const [rightWidth, setRightWidth] = React.useState(() => stored('proxima.rightWidth', 292))
  const [rightHidden, setRightHidden] = React.useState(() => (typeof localStorage !== 'undefined' && localStorage.getItem('proxima.rightHidden') === '1'))
  const [leftCollapsed, setLeftCollapsed] = React.useState(() => (typeof localStorage !== 'undefined' && localStorage.getItem('proxima.leftCollapsed') === '1'))

  React.useEffect(() => { localStorage.setItem('proxima.leftWidth', String(leftWidth)) }, [leftWidth])
  React.useEffect(() => { localStorage.setItem('proxima.rightWidth', String(rightWidth)) }, [rightWidth])
  React.useEffect(() => { localStorage.setItem('proxima.rightHidden', rightHidden ? '1' : '0') }, [rightHidden])
  React.useEffect(() => { localStorage.setItem('proxima.leftCollapsed', leftCollapsed ? '1' : '0') }, [leftCollapsed])

  const toggleRight = () => { if (matches('(min-width: 1280px)')) setRightHidden(v => !v); else setRailOpen(v => !v) }
  const toggleLeft = () => { if (matches('(min-width: 768px)')) setLeftCollapsed(v => !v); else setDrawerOpen(v => !v) }

  function startResize(side: 'left' | 'right') {
    return (event: React.MouseEvent) => {
      event.preventDefault()
      const startX = event.clientX
      const startLeft = leftWidth
      const startRight = rightWidth
      const onMove = (e: MouseEvent) => {
        if (side === 'left') setLeftWidth(clamp(startLeft + (e.clientX - startX), LEFT_MIN, LEFT_MAX))
        else setRightWidth(clamp(startRight - (e.clientX - startX), RIGHT_MIN, RIGHT_MAX))
      }
      const onUp = () => {
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        document.body.style.userSelect = ''
        document.body.style.cursor = ''
      }
      document.body.style.userSelect = 'none'
      document.body.style.cursor = 'col-resize'
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    }
  }

  // Design Studio is full-bleed (its own canvas + inspector), so the files rail is
  // force-hidden there without touching the user's saved preference.
  // The workflow iterate/test chat brings its own result stage, so the files rail
  // is hidden there to keep a clean chat | stage split.
  const iterating = !!props.activeSession?.workflow_id && props.currentView === 'chat'
  const effRightHidden = rightHidden || props.currentView === 'design' || props.currentView === 'video' || iterating
  const shellStyle = {
    ['--left-w']: leftCollapsed ? '58px' : `${leftWidth}px`,
    ['--right-w']: `${effRightHidden ? 0 : rightWidth}px`,
  } as React.CSSProperties

  return (
    <div className={`app-shell ${railOpen ? 'rail-open' : ''} ${effRightHidden ? 'right-hidden' : ''} ${leftCollapsed ? 'left-rail' : ''}`} style={shellStyle}>
      <header className="top-bar">
        <button className="tool-btn" onClick={toggleLeft} aria-label="Toggle sidebar" title={leftCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}><IconPanelLeft size={17} /></button>
        <button className="tool-btn" onClick={() => setSearchOpen(true)} aria-label="Search" title="Search"><IconSearch size={17} /></button>
        <span className="top-bar-spacer" />
        <button className={`tool-btn ${!rightHidden ? 'active' : ''}`} onClick={toggleRight} aria-label="Toggle files panel" title="Toggle files panel"><IconPanelRight size={17} /></button>
        <div className="user-menu-wrap">
          <button className={`tool-btn user-avatar-btn ${menuOpen ? 'active' : ''}`} onClick={() => setMenuOpen(o => !o)} aria-label="Account" title={props.user.username}><span className="avatar xs">{props.user.username[0]?.toUpperCase()}</span></button>
          {menuOpen && <>
            <button className="menu-scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />
            <div className="user-menu" role="menu">
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
      <MobileTopbar activeProject={props.activeProject} onMenu={() => setDrawerOpen(true)} onNewChat={() => props.onSelectView('chat')} onFiles={toggleRight} />
      <aside className={`sidebar ${drawerOpen ? 'is-open' : ''}`}>
        <Sidebar {...props} onClose={() => setDrawerOpen(false)} />
      </aside>
      <div className="resize-handle resize-left" style={{ left: 'var(--left-w)' }} onMouseDown={startResize('left')} role="separator" aria-label="Resize sidebar" />
      {drawerOpen && <button aria-label="Close menu" className="drawer-scrim" onClick={() => setDrawerOpen(false)} />}
      <main className="main-pane">
        {props.children}
      </main>
      <div className="resize-handle resize-right" style={{ right: 'var(--right-w)' }} onMouseDown={startResize('right')} role="separator" aria-label="Resize files panel" />
      {railOpen && <button aria-label="Close files" className="drawer-scrim rail-scrim" onClick={() => setRailOpen(false)} />}
      {props.currentView !== 'design' && !iterating && <RightRail token={props.token} activeProfile={props.activeProfile} activeProject={props.activeProject} activeSession={props.activeSession} projects={props.projects} onOpenFile={props.onOpenFile} />}
      {searchOpen && <SearchModal token={props.token} sessions={props.sessions} projects={props.projects} features={props.features} onClose={() => setSearchOpen(false)} onSelectSession={props.onSelectSession} onSelectProject={props.onSelectProject} onSelectView={props.onSelectView} />}
    </div>
  )
}

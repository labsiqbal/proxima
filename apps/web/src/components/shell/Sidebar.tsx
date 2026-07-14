import React from 'react'
import type { ComponentType } from 'react'
import type { AppFeatures, ChatSession, Profile, Project, User, View } from '../../types'
import { IconNewChat, IconHome, IconProjects, IconAgents, IconClose, IconPencil, IconTrash, IconWiki, IconFile, IconTerminal, IconGear, IconDesign, IconVideo, IconChevronRight, IconWorkflows, IconActivity } from './icons'
import { confirmDialog, promptDialog } from '../ui/Dialog'
import { ProximaMark } from '../brand/ProximaMark'

type NavItem = { id: View; label: string; icon: ComponentType<{ size?: number }>; action?: 'new-chat' }

// Work-focused nav. Account/management (Projects, Agents, Settings) live in the
// top-right profile menu.
const nav: NavItem[] = [
  { id: 'home', label: 'Home', icon: IconHome },
  { id: 'chat', label: 'New Chat', icon: IconNewChat, action: 'new-chat' },
  { id: 'design', label: 'Design', icon: IconDesign },
  { id: 'video', label: 'Video', icon: IconVideo },
  { id: 'wiki', label: 'Wiki', icon: IconWiki },
  { id: 'artifacts', label: 'Artifacts', icon: IconFile },
  { id: 'workflows', label: 'Workflows', icon: IconWorkflows },
  { id: 'graph', label: 'Workflow Graphs', icon: IconWorkflows },
  { id: 'activity', label: 'Activity', icon: IconActivity },
  { id: 'terminal', label: 'Terminal', icon: IconTerminal }
]

export function Sidebar(props: {
  activeProfile: Profile | null
  activeProject: Project | null
  activeSession: ChatSession | null
  currentView: View
  features: AppFeatures
  onClose: () => void
  onNewChat: () => void
  onRenameSession: (id: number, title: string) => void
  onDeleteSession: (id: number) => void
  onSelectProject: (project: Project) => void
  onSelectSession: (session: ChatSession) => void
  onOpenDesign: (session: ChatSession) => void
  onSelectView: (view: View) => void
  profiles: Profile[]
  projects: Project[]
  sessions: ChatSession[]
  seen: Record<number, string>
  busySessions?: number[]
  user: User
  updateVersion?: string | null
  onUpdateClick?: () => void
}) {
  const [acctOpen, setAcctOpen] = React.useState(false)
  const [projOpen, setProjOpen] = React.useState(false)
  return <div className="sidebar-inner">
    <div className="sidebar-head"><div className="brand-row"><ProximaMark /><strong className="proxima-word">PROXIMA</strong></div><div className="sidebar-actions"><button className="icon-button mobile-only" onClick={props.onClose} aria-label="Close menu"><IconClose size={18} /></button></div></div>
    <section className="nav-group">{nav.filter(item => (item.id !== 'video' || props.features.video) && (item.id !== 'design' || props.features.designStudio) && (item.id !== 'graph' || props.features.workflowGraph)).map(item => {
      const Icon = item.icon
      const onClick = () => {
        if (item.action === 'new-chat') props.onNewChat()
        else props.onSelectView(item.id)
        props.onClose()
      }
      return <button className={`nav-item ${props.currentView === item.id && !item.action ? 'active' : ''}`} key={item.id} onClick={onClick}><span className="nav-icon"><Icon /></span><strong>{item.label}</strong></button>
    })}</section>
    <section className="nav-group sidebar-projects"><p className="eyebrow">Project</p>
      <div className="project-dd">
        <button className="project-lock" onClick={() => setProjOpen(o => !o)} title="Switch project">
          <span className="nav-icon"><IconProjects /></span>
          <strong>{props.activeProject ? props.activeProject.name.replace(/\s*\((personal|private)\)\s*$/i, '') : 'No project'}</strong>
          <span className={`chevron ${projOpen ? 'open' : ''}`}>▸</span>
        </button>
        {projOpen && <><div className="project-scrim" onClick={() => setProjOpen(false)} /><div className="project-menu">
          {props.projects.length === 0 && <span className="project-empty">No projects yet</span>}
          {props.projects.map(pr => <button key={pr.slug} className={`project-item ${props.activeProject?.slug === pr.slug ? 'active' : ''}`} onClick={() => { props.onSelectProject(pr); setProjOpen(false); props.onClose() }}>
            <span className="nav-icon"><IconProjects /></span><span className="project-item-name">{pr.name.replace(/\s*\((personal|private)\)\s*$/i, '')}</span>{props.activeProject?.slug === pr.slug && <span className="project-check">✓</span>}
          </button>)}
          <div className="project-menu-sep" />
          <button className="project-item manage" onClick={() => { props.onSelectView('projects'); setProjOpen(false); props.onClose() }}>Manage projects…</button>
        </div></>}
      </div>
    </section>
    <SessionGroups {...props} />
    {props.updateVersion && props.onUpdateClick && <button type="button" className="sidebar-update-pill" onClick={() => { props.onUpdateClick?.(); props.onClose() }}>
      <span className="update-dot" aria-hidden="true" />Update available · v{props.updateVersion}
    </button>}
    <div className="sidebar-user">
      <button className="su-id" onClick={() => setAcctOpen(o => !o)}><span className="avatar">{props.user.username[0]?.toUpperCase()}</span><div><strong>{props.user.username}</strong><small>{props.activeProfile?.name || ''}</small></div><span className={`chevron ${acctOpen ? 'open' : ''}`}>▸</span></button>
      {acctOpen && <div className="su-menu">
        <button className="nav-item" onClick={() => { props.onSelectView('projects'); props.onClose() }}><span className="nav-icon"><IconProjects /></span><strong>Projects</strong></button>
        <button className="nav-item" onClick={() => { props.onSelectView('profiles'); props.onClose() }}><span className="nav-icon"><IconAgents /></span><strong>Agents</strong></button>
        <button className="nav-item" onClick={() => { props.onSelectView('settings'); props.onClose() }}><span className="nav-icon"><IconGear /></span><strong>Settings</strong></button>
      </div>}
    </div>
  </div>
}

type GroupProps = {
  sessions: ChatSession[]; activeSession: ChatSession | null; onClose: () => void; currentView: View
  activeProject: Project | null
  onSelectSession: (s: ChatSession) => void; onRenameSession: (id: number, t: string) => void
  onDeleteSession: (id: number) => void
  onOpenDesign: (s: ChatSession) => void
  features: AppFeatures
  seen: Record<number, string>
  busySessions?: number[]
}

const isUnread = (s: ChatSession, seen: Record<number, string>) => (seen[s.id] ?? '') < (s.updated_at ?? '')

function usePersistedToggle(key: string, fallback: boolean) {
  const [open, setOpen] = React.useState(() => { const v = localStorage.getItem(key); return v == null ? fallback : v === '1' })
  const toggle = () => setOpen(v => { localStorage.setItem(key, v ? '0' : '1'); return !v })
  return [open, toggle] as const
}

function SessionGroups(props: GroupProps) {
  // Scope chats/tasks to the active project so switching projects swaps the list.
  const slug = props.activeProject?.slug
  const inProject = (s: ChatSession) => !slug || s.project_slug === slug
  const isDesign = (s: ChatSession) => (s.mode === 'design' || (s.title || '').startsWith('Design: '))
  const isSurfaceThread = (s: ChatSession) => /^(Video:|Design System:|Internal:)/.test(s.title || '')
  const chats = props.sessions.filter(s => !s.job_id && !isDesign(s) && !isSurfaceThread(s) && inProject(s))
  const designSessions = props.sessions.filter(s => isDesign(s) && inProject(s))
  const [openChats, toggleChats] = usePersistedToggle('proxima.sb.chats', true)
  const [openDesigns, toggleDesigns] = usePersistedToggle('proxima.sb.designs', false)

  return <>
    {chats.length > 0 && <section className="nav-group">
      <button className="group-toggle" onClick={toggleChats}><span><span className={`chevron ${openChats ? 'open' : ''}`}><IconChevronRight size={13} /></span>Chats</span><span>{chats.length}</span></button>
      {openChats && chats.slice(0, 20).map(session => <div className={`project-row session-row ${props.currentView === 'chat' && props.activeSession?.id === session.id ? 'active' : ''}`} key={session.id} title={`${session.project_slug || 'no project'} · ${session.profile_slug || 'profile'}`}>
        <button className="row-main" onClick={() => { props.onSelectSession(session); props.onClose() }}><span className={`status-dot ${props.busySessions?.includes(session.id) ? 'thinking' : (session.id !== props.activeSession?.id && isUnread(session, props.seen) ? 'unread' : '')}`} /><strong>{session.title}</strong></button>
        <span className="row-actions">
          <button className="row-action" title="Rename" aria-label="Rename session" onClick={e => { e.stopPropagation(); void promptDialog({ title: 'Rename chat', label: 'Name', defaultValue: session.title, confirmLabel: 'Rename' }).then(t => { if (t) props.onRenameSession(session.id, t) }) }}><IconPencil size={15} /></button>
          <button className="row-action danger" title="Delete" aria-label="Delete session" onClick={e => { e.stopPropagation(); void confirmDialog({ title: 'Delete chat?', message: `“${session.title}” and its messages will be removed.`, confirmLabel: 'Delete', danger: true }).then(ok => { if (ok) props.onDeleteSession(session.id) }) }}><IconTrash size={15} /></button>
        </span>
      </div>)}
    </section>}
    {props.features.designStudio && designSessions.length > 0 && <section className="nav-group">
      <button className="group-toggle" onClick={toggleDesigns}><span><span className={`chevron ${openDesigns ? 'open' : ''}`}><IconChevronRight size={13} /></span>Designs</span><span>{designSessions.length}</span></button>
      {openDesigns && designSessions.slice(0, 20).map(session => <div className="project-row session-row" key={session.id} title="Open design">
        <button className="row-main" onClick={() => { props.onOpenDesign(session); props.onClose() }}><span className={`status-dot ${props.busySessions?.includes(session.id) ? 'thinking' : ''}`} /><strong>{session.title.replace(/^Design:\s*/, '')}</strong></button>
        <span className="row-actions">
          <button className="row-action danger" title="Delete design chat" aria-label="Delete design chat" onClick={e => { e.stopPropagation(); void confirmDialog({ title: 'Delete design chat?', message: `Removes the AI chat for “${session.title.replace(/^Design:\s*/, '')}”. The design file stays.`, confirmLabel: 'Delete', danger: true }).then(ok => { if (ok) props.onDeleteSession(session.id) }) }}><IconTrash size={15} /></button>
        </span>
      </div>)}
    </section>}
  </>
}

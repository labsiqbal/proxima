import { useState, type ComponentType } from 'react'
import type { AppFeatures, ChatSession, Profile, Project, User, View } from '../../types'
import { IconChat, IconSparkle, IconTasks, IconProjects, IconAgents, IconClose, IconPencil, IconTrash, IconArtifacts, IconGear, IconDesign, IconChevronRight, IconWorkflows, IconLogout } from './icons'
import { confirmDialog, promptDialog } from '../ui/Dialog'
import { ProximaMark } from '../brand/ProximaMark'

// One workspace, nav ordered by the flow: talk it through (Chat), watch it run
// (Tasks), keep what worked (Recipes), then the places work lives (Projects,
// Archive). Terminal/Files/Preview are tools on the right rail, not
// destinations, and Agents/Settings stay in the account menu.
type Destination = { id: View; label: string; icon: ComponentType<{ size?: number }> }
const primary: Destination[] = [
  { id: 'chat', label: 'Chat', icon: IconChat },
  { id: 'alpha', label: 'Alpha', icon: IconSparkle },
  { id: 'activity', label: 'Tasks', icon: IconTasks },
  { id: 'workflows', label: 'Recipes', icon: IconWorkflows },
  { id: 'projects', label: 'Projects', icon: IconProjects },
  { id: 'artifacts', label: 'Archive', icon: IconArtifacts },
  { id: 'design', label: 'Design', icon: IconDesign },
]
const enabled = (item: Destination, features: AppFeatures) =>
  (item.id !== 'design' || features.designStudio) && (item.id !== 'graph' || features.workflowGraph)

export function Sidebar(props: {
  activeProfile: Profile | null; activeProject: Project | null; activeSession: ChatSession | null; currentView: View
  features: AppFeatures; onClose: () => void; onLogout: () => void
  onRenameSession: (id: number, title: string) => void; onDeleteSession: (id: number) => void
  onSelectProject: (project: Project) => void; onSelectSession: (session: ChatSession) => void
  onOpenDesign: (session: ChatSession) => void; onSelectView: (view: View) => void
  profiles: Profile[]; projects: Project[]; sessions: ChatSession[]; seen: Record<number, string>; busySessions?: number[]; user: User
  updateVersion?: string | null; onUpdateClick?: () => void
}) {
  const [acctOpen, setAcctOpen] = useState(false)
  const go = (view: View) => { props.onSelectView(view); props.onClose() }
  const isActive = (item: Destination) => {
    // A workflow-iteration chat belongs to Recipes, not Chat; the New task
    // launcher ('home') and an open task both belong to the Tasks flow.
    const inRecipeChat = props.currentView === 'chat' && !!props.activeSession?.workflow_id
    switch (item.id) {
      case 'chat': return props.currentView === 'chat' && !inRecipeChat
      case 'activity': return props.currentView === 'activity' || props.currentView === 'task' || props.currentView === 'home'
      case 'workflows': return props.currentView === 'workflows' || props.currentView === 'graph' || inRecipeChat
      default: return props.currentView === item.id
    }
  }
  const destination = (item: Destination) => {
    const Icon = item.icon
    const active = isActive(item)
    return <button className={`nav-item ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} key={item.id} onClick={() => go(item.id)}><span className="nav-icon"><Icon /></span><strong>{item.label}</strong></button>
  }

  return <div className="sidebar-inner">
    <div className="sidebar-head"><div className="brand-row"><ProximaMark /><strong className="proxima-word">PROXIMA</strong></div><button className="icon-button mobile-only" onClick={props.onClose} aria-label="Close menu"><IconClose size={18} /></button></div>

    <nav className="shell-navigation" aria-label="Navigation">
      <section className="nav-group primary-nav">
        {/* Destinations only — blank session is started from Chat header (or mobile
            topbar / `/new`), not a twin primary-nav row above Chat. */}
        {primary.filter(item => enabled(item, props.features)).map(destination)}
      </section>
    </nav>
    <SessionGroups {...props} />

    {props.updateVersion && props.onUpdateClick && <button type="button" className="sidebar-update-pill" onClick={() => { props.onUpdateClick?.(); props.onClose() }}><span className="update-dot" aria-hidden="true" />Update available · v{props.updateVersion}</button>}
    <div className="sidebar-user">
      <button className="su-id" onClick={() => setAcctOpen(value => !value)} aria-expanded={acctOpen}><span className="avatar">{props.user.username[0]?.toUpperCase()}</span><div><strong>{props.user.username}</strong><small>{props.activeProfile?.name || ''}</small></div><span className={`chevron ${acctOpen ? 'open' : ''}`}>▸</span></button>
      {acctOpen && <div className="su-menu"><button className="nav-item" onClick={() => go('profiles')}><span className="nav-icon"><IconAgents /></span><strong>Agents</strong></button><button className="nav-item" onClick={() => go('settings')}><span className="nav-icon"><IconGear /></span><strong>Settings</strong></button><button className="nav-item su-logout" onClick={props.onLogout}><span className="nav-icon"><IconLogout /></span><strong>Log out</strong></button></div>}
    </div>
  </div>
}

type GroupProps = {
  sessions: ChatSession[]; activeSession: ChatSession | null; onClose: () => void; currentView: View; activeProject: Project | null
  onSelectSession: (session: ChatSession) => void; onRenameSession: (id: number, title: string) => void; onDeleteSession: (id: number) => void
  onOpenDesign: (session: ChatSession) => void; features: AppFeatures; seen: Record<number, string>; busySessions?: number[]
}
const isUnread = (session: ChatSession, seen: Record<number, string>) => (seen[session.id] ?? '') < (session.updated_at ?? '')
function usePersistedToggle(key: string, fallback: boolean) {
  const [open, setOpen] = useState(() => { const value = typeof localStorage === 'undefined' ? null : localStorage.getItem(key); return value == null ? fallback : value === '1' })
  return [open, () => setOpen(value => { if (typeof localStorage !== 'undefined') localStorage.setItem(key, value ? '0' : '1'); return !value })] as const
}
function SessionGroups(props: GroupProps) {
  const slug = props.activeProject?.slug
  const inProject = (session: ChatSession) => !slug || session.project_slug === slug
  const isDesign = (session: ChatSession) => session.mode === 'design' || (session.title || '').startsWith('Design: ')
  const isSurfaceThread = (session: ChatSession) => /^(Design System:|Internal:)/.test(session.title || '')
  const chats = props.sessions.filter(session => !session.job_id && !session.workflow_id && !isDesign(session) && !isSurfaceThread(session) && inProject(session))
  const designs = props.sessions.filter(session => isDesign(session) && inProject(session))
  const [openChats, toggleChats] = usePersistedToggle('proxima.sb.chats', true)
  const [openDesigns, toggleDesigns] = usePersistedToggle('proxima.sb.designs', false)
  const rows = (items: ChatSession[], design: boolean) => items.slice(0, 20).map(session => <div className={`project-row session-row ${!design && props.currentView === 'chat' && props.activeSession?.id === session.id ? 'active' : ''}`} key={session.id}>
    <button className="row-main" onClick={() => { design ? props.onOpenDesign(session) : props.onSelectSession(session); props.onClose() }}><span className={`status-dot ${props.busySessions?.includes(session.id) ? 'thinking' : (!design && session.id !== props.activeSession?.id && isUnread(session, props.seen) ? 'unread' : '')}`} /><strong>{design ? session.title.replace(/^Design:\s*/, '') : session.title}</strong></button>
    <span className="row-actions">{!design && <button className="row-action" aria-label="Rename session" onClick={() => void promptDialog({ title: 'Rename chat', label: 'Name', defaultValue: session.title, confirmLabel: 'Rename' }).then(value => { if (value) props.onRenameSession(session.id, value) })}><IconPencil size={15} /></button>}<button className="row-action danger" aria-label={`Delete ${design ? 'design chat' : 'session'}`} onClick={() => void confirmDialog({ title: `Delete ${design ? 'design chat' : 'chat'}?`, message: design ? 'The design file stays.' : `“${session.title}” and its messages will be removed.`, confirmLabel: 'Delete', danger: true }).then(ok => { if (ok) props.onDeleteSession(session.id) })}><IconTrash size={15} /></button></span>
  </div>)
  return <div className="recent-groups">
    {chats.length > 0 && <section className="nav-group"><button className="group-toggle" onClick={toggleChats} aria-expanded={openChats}><span><span className={`chevron ${openChats ? 'open' : ''}`}><IconChevronRight size={13} /></span>Recent chats</span><span>{chats.length}</span></button>{openChats && rows(chats, false)}</section>}
    {props.features.designStudio && designs.length > 0 && <section className="nav-group"><button className="group-toggle" onClick={toggleDesigns} aria-expanded={openDesigns}><span><span className={`chevron ${openDesigns ? 'open' : ''}`}><IconChevronRight size={13} /></span>Design sessions</span><span>{designs.length}</span></button>{openDesigns && rows(designs, true)}</section>}
  </div>
}

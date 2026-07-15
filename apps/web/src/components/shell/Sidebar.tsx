import { useState, type ComponentType } from 'react'
import type { AppFeatures, ChatSession, Profile, Project, User, View } from '../../types'
import { IconNewChat, IconHome, IconProjects, IconAgents, IconClose, IconPencil, IconTrash, IconArtifacts, IconActivity, IconTerminal, IconGear, IconDesign, IconVideo, IconChevronRight, IconWorkflows, IconPlus, IconLogout } from './icons'
import { confirmDialog, promptDialog } from '../ui/Dialog'
import { ProximaMark } from '../brand/ProximaMark'

type WorkspaceMode = 'ops' | 'code'
type Destination = { id: View; label: string; icon: ComponentType<{ size?: number }> }
const opsPrimary: Destination[] = [
  { id: 'activity', label: 'Tasks', icon: IconActivity },
  { id: 'projects', label: 'Projects', icon: IconProjects },
  { id: 'workflows', label: 'Workflows', icon: IconWorkflows },
  { id: 'artifacts', label: 'Artifacts', icon: IconArtifacts },
  { id: 'design', label: 'Design', icon: IconDesign },
]
const opsAdvanced: Destination[] = [
  { id: 'video', label: 'Video', icon: IconVideo },
]
const codePrimary: Destination[] = [
  { id: 'projects', label: 'Projects', icon: IconProjects },
  { id: 'terminal', label: 'Terminal', icon: IconTerminal },
]
const enabled = (item: Destination, features: AppFeatures) =>
  (item.id !== 'design' || features.designStudio) && (item.id !== 'video' || features.video) && (item.id !== 'graph' || features.workflowGraph)

export function Sidebar(props: {
  activeProfile: Profile | null; activeProject: Project | null; activeSession: ChatSession | null; currentView: View
  workspaceMode: WorkspaceMode; onSelectWorkspace: (mode: WorkspaceMode) => void
  features: AppFeatures; onClose: () => void; onNewChat: () => void; onLogout: () => void
  onRenameSession: (id: number, title: string) => void; onDeleteSession: (id: number) => void
  onSelectProject: (project: Project) => void; onSelectSession: (session: ChatSession) => void
  onOpenDesign: (session: ChatSession) => void; onSelectView: (view: View) => void
  profiles: Profile[]; projects: Project[]; sessions: ChatSession[]; seen: Record<number, string>; busySessions?: number[]; user: User
  updateVersion?: string | null; onUpdateClick?: () => void
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [acctOpen, setAcctOpen] = useState(false)
  const go = (view: View) => { props.onSelectView(view); props.onClose() }
  const destination = (item: Destination) => {
    const Icon = item.icon
    const active = props.currentView === item.id || (item.id === 'activity' && props.currentView === 'task') || (item.id === 'workflows' && props.currentView === 'chat' && !!props.activeSession?.workflow_id)
    return <button className={`nav-item ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} key={item.id} onClick={() => go(item.id)}><span className="nav-icon"><Icon /></span><strong>{item.label}</strong></button>
  }
  const advanced = opsAdvanced.filter(item => enabled(item, props.features))

  return <div className="sidebar-inner">
    <div className="sidebar-head"><div className="brand-row"><ProximaMark /><strong className="proxima-word">PROXIMA</strong></div><button className="icon-button mobile-only" onClick={props.onClose} aria-label="Close menu"><IconClose size={18} /></button></div>
    <div className="workspace-switch" role="group" aria-label="Workspace">
      <button className={props.workspaceMode === 'ops' ? 'active' : ''} aria-pressed={props.workspaceMode === 'ops'} onClick={() => { props.onSelectWorkspace('ops'); props.onClose() }}><IconHome size={14} /><span>Ops</span></button>
      <button className={props.workspaceMode === 'code' ? 'active' : ''} aria-pressed={props.workspaceMode === 'code'} onClick={() => { props.onSelectWorkspace('code'); props.onClose() }}><IconTerminal size={14} /><span>Code</span></button>
    </div>

    {props.workspaceMode === 'ops' ? <nav className="shell-navigation" aria-label="Ops navigation">
      <section className="nav-group primary-nav">
        <button className={`nav-item ${props.currentView === 'home' ? 'active' : ''}`} aria-current={props.currentView === 'home' ? 'page' : undefined} onClick={() => go('home')}><span className="nav-icon"><IconPlus /></span><strong>New task</strong></button>
        {opsPrimary.filter(item => enabled(item, props.features)).map(destination)}
      </section>
      {advanced.length > 0 && <section className="nav-group advanced-nav">
        <button className="group-toggle advanced-toggle" onClick={() => setAdvancedOpen(value => !value)} aria-expanded={advancedOpen} aria-controls="ops-advanced-destinations"><span><span className={`chevron ${advancedOpen ? 'open' : ''}`}><IconChevronRight size={13} /></span>Advanced</span></button>
        <div id="ops-advanced-destinations" hidden={!advancedOpen}>{advanced.map(destination)}</div>
      </section>}
    </nav> : <>
      <nav className="shell-navigation" aria-label="Code navigation">
        <section className="nav-group primary-nav">
          <button className={`nav-item ${props.currentView === 'chat' && !props.activeSession ? 'active' : ''}`} onClick={() => { props.onNewChat(); props.onClose() }}><span className="nav-icon"><IconNewChat /></span><strong>New session</strong></button>
          {codePrimary.map(destination)}
        </section>
      </nav>
      <SessionGroups {...props} />
    </>}

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
  const isSurfaceThread = (session: ChatSession) => /^(Video:|Design System:|Internal:)/.test(session.title || '')
  const chats = props.sessions.filter(session => !session.job_id && !session.workflow_id && !isDesign(session) && !isSurfaceThread(session) && inProject(session))
  const designs = props.sessions.filter(session => isDesign(session) && inProject(session))
  const [openChats, toggleChats] = usePersistedToggle('proxima.sb.chats', true)
  const [openDesigns, toggleDesigns] = usePersistedToggle('proxima.sb.designs', false)
  const rows = (items: ChatSession[], design: boolean) => items.slice(0, 20).map(session => <div className={`project-row session-row ${!design && props.currentView === 'chat' && props.activeSession?.id === session.id ? 'active' : ''}`} key={session.id}>
    <button className="row-main" onClick={() => { design ? props.onOpenDesign(session) : props.onSelectSession(session); props.onClose() }}><span className={`status-dot ${props.busySessions?.includes(session.id) ? 'thinking' : (!design && session.id !== props.activeSession?.id && isUnread(session, props.seen) ? 'unread' : '')}`} /><strong>{design ? session.title.replace(/^Design:\s*/, '') : session.title}</strong></button>
    <span className="row-actions">{!design && <button className="row-action" aria-label="Rename session" onClick={() => void promptDialog({ title: 'Rename chat', label: 'Name', defaultValue: session.title, confirmLabel: 'Rename' }).then(value => { if (value) props.onRenameSession(session.id, value) })}><IconPencil size={15} /></button>}<button className="row-action danger" aria-label={`Delete ${design ? 'design chat' : 'session'}`} onClick={() => void confirmDialog({ title: `Delete ${design ? 'design chat' : 'chat'}?`, message: design ? 'The design file stays.' : `“${session.title}” and its messages will be removed.`, confirmLabel: 'Delete', danger: true }).then(ok => { if (ok) props.onDeleteSession(session.id) })}><IconTrash size={15} /></button></span>
  </div>)
  return <div className="recent-groups">
    {chats.length > 0 && <section className="nav-group"><button className="group-toggle" onClick={toggleChats} aria-expanded={openChats}><span><span className={`chevron ${openChats ? 'open' : ''}`}><IconChevronRight size={13} /></span>Recent sessions</span><span>{chats.length}</span></button>{openChats && rows(chats, false)}</section>}
    {props.features.designStudio && designs.length > 0 && <section className="nav-group"><button className="group-toggle" onClick={toggleDesigns} aria-expanded={openDesigns}><span><span className={`chevron ${openDesigns ? 'open' : ''}`}><IconChevronRight size={13} /></span>Design sessions</span><span>{designs.length}</span></button>{openDesigns && rows(designs, true)}</section>}
  </div>
}

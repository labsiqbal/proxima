import React from 'react'
import { resume, setupStatus, logout } from './api/auth'
import { listProfiles } from './api/profiles'
import { listProjects, deleteProject } from './api/projects'
import { listSessions, getSession, renameSession, deleteSession } from './api/sessions'
import { activeRuns, createRun } from './api/runs'
import { createJob, deleteJob, getJob, linkJobRun, startJob } from './api/jobs'
import { api } from './api/client'
import type { ChatSession, FileTarget, GraphWorkflowDraft, OutputLink, Profile, Project, Runner, User, View } from './types'
import type { ArtifactReviewFeedback } from './components/artifacts/ArtifactViewer'
import { AppShell } from './components/shell/AppShell'
import { AuthGate } from './screens/AuthGate'
import { HermesBanner } from './components/shell/HermesBanner'
import type { RunnerReadinessMap } from './components/shell/runnerReadiness'
import { ChatScreen } from './screens/ChatScreen'
import { HomeScreen } from './screens/HomeScreen'
import type { OpsTaskRequest } from './components/tasks/TaskComposer'
import { DialogHost } from './components/ui/Dialog'
import { useUpdateStatus } from './hooks/useUpdateStatus'
import { usePolling } from './hooks/usePolling'
import { UpdateModal, UpdateOverlay } from './components/shell/UpdateModal'
import { ProximaMark } from './components/brand/ProximaMark'
import { MasterStateProvider, useMasterState } from './master/MasterStateProvider'
import {
  WorkChatStateProvider,
  workChatStateKey,
} from './work/WorkChatStateProvider'
import {
  parseWorkRoute,
  workRouteUrl,
  type WorkRoute,
} from './lib/workRoute'
import type { ShellMode } from './components/shell/ShellModeSwitch'
import {
  canGoBack,
  chromeBackLabel,
  popDeep,
  projectSwitcherLocked,
  pushDeep,
  shouldKeepAlive,
  viewOriginLabel,
  type NavStackEntry,
} from './lib/navStack'
import {
  persistWorkProjectPreference,
  readWorkProjectPreference,
  resolveWorkProject,
} from './lib/workProjectPreference'
import {
  nextPreserveWorkTaskContext,
  taskHashPreservesWorkProject,
  withInAppTaskPolicy,
  withResolvedTaskOwnership,
  withoutTaskPolicy,
} from './lib/taskHashRoute'
import {
  resolveDesignStudioProject,
  taskLinkedDesignProjectSlug,
} from './lib/designStudioProject'
const IterateStage = React.lazy(() => import('./screens/IterateStage').then(m => ({ default: m.IterateStage })))
const DesignStudio = React.lazy(() => import('./screens/DesignStudio').then(m => ({ default: m.DesignStudio })))
const WikiScreen = React.lazy(() => import('./screens/WikiScreen').then(m => ({ default: m.WikiScreen })))
const ArtifactsScreen = React.lazy(() => import('./screens/ArtifactsScreen').then(m => ({ default: m.ArtifactsScreen })))
const FilesScreen = React.lazy(() => import('./screens/FilesScreen').then(m => ({ default: m.FilesScreen })))
const WorkflowsScreen = React.lazy(() => import('./screens/WorkflowsScreen').then(m => ({ default: m.WorkflowsScreen })))
const ActivityScreen = React.lazy(() => import('./screens/ActivityScreen').then(m => ({ default: m.ActivityScreen })))
const MasterScreen = React.lazy(() => import('./screens/MasterScreen').then(m => ({ default: m.MasterScreen })))
const TaskWorkspace = React.lazy(() => import('./screens/TaskWorkspace').then(m => ({ default: m.TaskWorkspace })))
const GraphScreen = React.lazy(() => import('./screens/GraphScreen').then(m => ({ default: m.GraphScreen })))
const ProfilesScreen = React.lazy(() => import('./screens/ProfilesScreen').then(m => ({ default: m.ProfilesScreen })))
const RunnersScreen = React.lazy(() => import('./screens/RunnersScreen').then(m => ({ default: m.RunnersScreen })))
const SettingsScreen = React.lazy(() => import('./screens/SettingsScreen').then(m => ({ default: m.SettingsScreen })))
const WorkspaceOnboarding = React.lazy(() => import('./screens/WorkspaceOnboarding').then(m => ({ default: m.WorkspaceOnboarding })))
type SettingsSectionKey = import('./screens/SettingsScreen').SettingsSectionKey

type OpsTaskKind = 'agent' | 'image' | 'design'
const opsTaskKind = (brief: string): OpsTaskKind => /^\/(image|gambar)\b/i.test(brief) ? 'image' : /^\/(design|image-studio|design-studio)\b/i.test(brief) ? 'design' : 'agent'
const mediaBriefIsThin = (brief: string) => {
  if (/!\[[^\]]*\]\([^)]+\)/.test(brief)) return false
  const detail = brief.trim().replace(/^\/\S+\s*/i, '').trim()
  return detail.split(/\s+/).filter(Boolean).length < 3
}

export async function resolveArtifactReviewTarget<TProject extends { slug: string }>(args: {
  sessions: ChatSession[]
  sessionId: number | null
  fallback: ChatSession | null
  loadSession: (sessionId: number) => Promise<ChatSession>
  projects: TProject[]
}): Promise<
  | { ok: true; session: ChatSession; project: TProject }
  | { ok: false; message: string }
> {
  let session: ChatSession | null = null
  if (args.sessionId == null) {
    session = args.fallback
  } else {
    session = args.sessions.find(candidate => candidate.id === args.sessionId) ?? null
    if (!session) {
      try {
        session = await args.loadSession(args.sessionId)
      } catch {
        return { ok: false, message: 'The chat that produced this artifact is no longer available.' }
      }
    }
  }
  if (!session) return { ok: false, message: 'This artifact has no producing chat to receive feedback.' }
  if (session.mode === 'design') {
    return { ok: false, message: 'The chat that produced this artifact is no longer available.' }
  }
  const project = args.projects.find(candidate => candidate.slug === session?.project_slug)
  if (!project) return { ok: false, message: "The project that owns this artifact's chat is no longer available." }
  return { ok: true, session, project }
}

export async function createAndStartOpsTask(token: string, request: OpsTaskRequest): Promise<number> {
  const text = request.brief.trim()
  if (!text || !request.projectSlug) throw new Error('Choose a project and enter a task brief.')
  const kind = opsTaskKind(text)
  if (kind !== 'agent' && mediaBriefIsThin(text)) throw new Error(`Add a clearer ${kind} brief before starting the task.`)
  const title = text.replace(/^\/(image|gambar|design|image-studio|design-studio)\s*/i, '').trim().slice(0, 80) || `${kind} task`
  const job = await createJob(token, { project_slug: request.projectSlug, profile_id: request.profileId, title, input: { brief: text, task_kind: kind, execution_policy: request.executionPolicy } })
  let mediaRunStarted = false
  try {
    if (kind === 'agent') {
      await startJob(token, job.id)
    } else {
      const run = await createRun(token, job.session_id, { message: text, profile_id: request.profileId, project_slug: request.projectSlug })
      mediaRunStarted = true
      await linkJobRun(token, job.id, run.run_id)
    }
  } catch (startError) {
    if (mediaRunStarted) throw new Error(`Media task #${job.id} started but could not attach to its task workspace. Open Tasks and inspect job #${job.id}. ${String(startError)}`)
    try {
      await deleteJob(token, job.id)
    } catch (cleanupError) {
      throw new Error(`Task #${job.id} could not start or be cleaned up. Open Tasks and delete task #${job.id} before retrying. Start error: ${String(startError)}. Cleanup error: ${String(cleanupError)}`)
    }
    throw startError
  }
  return job.id
}

/** Most recent chat for a project, or null when none / no project. */
export function recentSessionForProject(
  sessions: ChatSession[],
  projectSlug: string | null | undefined,
): ChatSession | null {
  if (!projectSlug) return null
  return sessions
    .filter(session => session.project_slug === projectSlug)
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0] || null
}

export function resolveRoutedWorkSession(args: {
  sessions: ChatSession[]
  projectSlug: string | null | undefined
  sessionId: number | null
}): ChatSession | null {
  if (args.sessionId == null) return null
  const matched = args.sessions.find(
    session =>
      session.id === args.sessionId &&
      session.project_slug === args.projectSlug,
  )
  if (matched) return matched
  return recentSessionForProject(args.sessions, args.projectSlug)
}

export function workRouteSessionId(args: {
  mode: ShellMode
  projectSlug: string | null | undefined
  activeSession: Pick<ChatSession, 'id' | 'project_slug'> | null
}): number | null {
  if (args.mode !== 'work') return null
  if (!args.activeSession) return null
  if (args.activeSession.project_slug !== args.projectSlug) return null
  return args.activeSession.id
}

/** Focused Workflow/Design ids belong on the URL whenever that Work surface is active,
 * including cold-load staging before editor/studio readiness. Stage must not gate identity. */
export function workRouteFocusedItemIds(args: {
  mode: ShellMode
  view: View
  graphItemId: number | null
  designItemId: string | null
}): Pick<WorkRoute, 'workflowJobId' | 'designId'> {
  return {
    workflowJobId:
      args.mode === 'work' && args.view === 'workflows' ? args.graphItemId : null,
    designId:
      args.mode === 'work' && args.view === 'design' ? args.designItemId : null,
  }
}

/** Resolve focused Workflow/Design identity from a child stage report.
 * Non-null ids adopt immediately. Mount home/start and editor/studio+null loading
 * flashes keep a pending routed identity. Clear only on a real leave from the
 * focused stage (previous stage was already editor/studio). */
export function nextFocusedWorkItemId<T>(args: {
  prevStage: string
  nextStage: string
  focusedStage: string
  reportedId: T | null
  currentId: T | null
}): T | null {
  if (args.nextStage === args.focusedStage) {
    return args.reportedId ?? args.currentId
  }
  if (args.prevStage === args.focusedStage) {
    return null
  }
  return args.currentId
}

/** Push a history entry only when the child settles on a real focused id that
 * is not already on the stable URL (avoids restore-time spurious entries).
 * historyAlreadyOwned: App already allocated the entry (e.g. session Design open). */
export function shouldPushFocusedItemHistory(args: {
  prevStage?: string
  nextStage: string
  focusedStage: string
  reportedId: string | number | null
  routedId: string | number | null
  historyAlreadyOwned?: boolean
}): boolean {
  if (
    args.nextStage !== args.focusedStage
    || args.reportedId == null
    || args.routedId === args.reportedId
  ) {
    return false
  }
  if (args.historyAlreadyOwned) {
    return false
  }
  if (args.prevStage === args.focusedStage && args.routedId == null) {
    return false
  }
  return true
}

/** Route sync cancels request-scoped Design session opens while still applying a
 * stable URL designId as pendingDesignId (session open is not URL state). */
export function workRouteDesignOpenSync(args: {
  routeDesignId: string | null
}): {
  pendingDesign: null
  pendingDesignId: string | null
  designOpenHistoryOwned: false
} {
  return {
    pendingDesign: null,
    pendingDesignId: args.routeDesignId,
    designOpenHistoryOwned: false,
  }
}

function fallbackProject(
  projects: Project[],
): Project | null {
  return projects.find(p => p.visibility === 'private') || projects[0] || null
}

/** Keep background Work project/session when the URL has no Work identities (e.g. Delegate).
 * Only fall back when fresh catalogs prove the current selection is gone. */
export function resolvePreservedWorkSelection(args: {
  projects: Project[]
  sessions: ChatSession[]
  activeProject: Project | null
  activeSession: ChatSession | null
}): { project: Project | null; session: ChatSession | null } {
  const project =
    (args.activeProject
      && args.projects.find(p => p.slug === args.activeProject!.slug))
    || fallbackProject(args.projects)
  if (!args.activeSession) {
    return { project, session: null }
  }
  const matched = args.sessions.find(
    session =>
      session.id === args.activeSession!.id
      && args.projects.some(p => p.slug === session.project_slug),
  )
  if (matched) {
    const matchedProject =
      args.projects.find(p => p.slug === matched.project_slug) || project
    return { project: matchedProject, session: matched }
  }
  const sessionProjectSlug = args.projects.some(
    p => p.slug === args.activeSession!.project_slug,
  )
    ? args.activeSession.project_slug
    : project?.slug
  return {
    project:
      (sessionProjectSlug
        ? args.projects.find(p => p.slug === sessionProjectSlug) || null
        : null) || project,
    session: recentSessionForProject(args.sessions, sessionProjectSlug),
  }
}

/** Whether selecting a project should navigate to Chat (intentional open) or only filter the shell. */
export type ProjectSelectMode = 'shell-only' | 'open-chat'

export function projectSelectNavigatesToChat(mode: ProjectSelectMode): boolean {
  return mode === 'open-chat'
}

export function shellModeFromSearch(search: string): ShellMode {
  return new URLSearchParams(search).get('mode') === 'delegate' ? 'delegate' : 'work'
}

export function opsMigrationSlugFromHash(hash: string): string | null {
  const match = hash.match(/^#settings\/projects\/([^/]+)\/ops-migration$/)
  if (!match) return null
  try {
    return decodeURIComponent(match[1])
  } catch {
    return null
  }
}

export function projectForShellScope(args: {
  projects: Project[]
  migrationSlug?: string | null
  sessionProjectSlug?: string | null
  currentProject?: Project | null
}): Project | null {
  if (args.migrationSlug) {
    const routed = args.projects.find(project => project.slug === args.migrationSlug)
    if (routed) return routed
  }
  if (args.sessionProjectSlug) {
    const fromSession = args.projects.find(
      project => project.slug === args.sessionProjectSlug,
    )
    if (fromSession) return fromSession
  }
  if (
    args.currentProject
    && args.projects.some(project => project.slug === args.currentProject?.slug)
  ) {
    return args.currentProject
  }
  return args.projects.find(project => project.visibility === 'private')
    || args.projects[0]
    || null
}

/** Delegate owns only its global desk and its cross-project review destinations. */
export function isDelegateDestination(view: View): boolean {
  return view === 'master' || view === 'activity' || view === 'artifacts' || view === 'files' || view === 'task'
}

/** Plan Work-mode Open Master conversation: always enter Delegate, then focus. */
export function planOpenMasterConversation(
  originMessageId?: number | null,
): { enterDelegate: true; pendingMasterMessageId: number | null } {
  const pendingMasterMessageId =
    typeof originMessageId === 'number' &&
    Number.isSafeInteger(originMessageId) &&
    originMessageId > 0
      ? originMessageId
      : null
  return { enterDelegate: true, pendingMasterMessageId }
}

function ViewFallback({ label = 'Loading...' }: { label?: string }) {
  return <section className="placeholder-view"><div className="assistant-bubble compact"><p className="muted">{label}</p></div></section>
}

type TaskProjectContext = {
  jobId: number
  projectSlug: string | null
  initialJob: import('./types').Job | null
}

function ContextualTaskWorkspace({
  projects,
  selectedWorkProject,
  onResolved,
  ...props
}: React.ComponentProps<typeof TaskWorkspace> & {
  projects: Project[]
  selectedWorkProject: Project | null
}) {
  const { fleet, actions } = useMasterState()
  const loadTargetAreas = actions.loadTargetAreas
  const handleResolved = React.useCallback((job: import('./types').Job) => {
    const container = fleet.containers.find(item =>
      item.id === job.delegation?.container_id
      || item.slug === job.project_slug)
    if (container) void loadTargetAreas(container.id)
    onResolved?.(job)
  }, [fleet.containers, loadTargetAreas, onResolved])
  return (
    <TaskWorkspace
      {...props}
      projects={projects}
      containers={fleet.containers}
      areasByContainer={fleet.areasByContainer}
      selectedWorkProject={selectedWorkProject}
      onResolved={handleResolved}
    />
  )
}

export function App() {
  const initialWorkRoute = React.useRef<WorkRoute>(
    parseWorkRoute(window.location),
  )
  const workRouteBootstrapped = React.useRef(false)
  const [booting, setBooting] = React.useState(true)
  // In-memory only. Persistent auth lives in the HttpOnly proxima_session cookie,
  // which browser storage and injected client code cannot read.
  const [token, setToken] = React.useState('')
  const updates = useUpdateStatus(token)
  const [user, setUser] = React.useState<User | null>(null)
  const [authGate, setAuthGate] = React.useState<'setup' | 'login' | null>(null)
  // First-run only: after the password is set, offer to point Proxima at a real
  // code folder before landing in the app.
  const [onboarding, setOnboarding] = React.useState(false)
  // One workspace: Chat is the default, while a durable Work URL restores the
  // exact surface before the server-backed project/item validation completes.
  const [view, setView] = React.useState<View>(initialWorkRoute.current.view)
  const [shellMode, setShellMode] = React.useState<ShellMode>(initialWorkRoute.current.mode)
  const lastWorkView = React.useRef<View>('chat')
  // Multitask keep-alive: once a primary surface is visited, stay mounted (hidden when inactive).
  const [aliveViews, setAliveViews] = React.useState<Set<View>>(() => new Set(['chat']))
  React.useEffect(() => {
    if (!shouldKeepAlive(view)) return
    setAliveViews(prev => (prev.has(view) ? prev : new Set(prev).add(view)))
  }, [view])
  // Settings section deep-link (e.g. Projects manage from account menu / home recovery).
  const [settingsSection, setSettingsSection] = React.useState<SettingsSectionKey>('account')
  const [opsMigrationSlug, setOpsMigrationSlug] = React.useState<string | null>(
    () => opsMigrationSlugFromHash(window.location.hash),
  )
  const [projects, setProjects] = React.useState<Project[]>([])
  const [activeProject, setActiveProjectState] = React.useState<Project | null>(null)
  const activeProjectRef = React.useRef<Project | null>(null)
  const setActiveProject = React.useCallback((update: Project | null | ((prev: Project | null) => Project | null)) => {
    if (typeof update === 'function') {
      setActiveProjectState(prev => {
        const next = update(prev)
        activeProjectRef.current = next
        return next
      })
      return
    }
    activeProjectRef.current = update
    setActiveProjectState(update)
  }, [])
  const [projectFallbackNotice, setProjectFallbackNotice] = React.useState('')
  React.useEffect(() => { if (view === 'settings') void updates.refresh() }, [view, updates.refresh])
  const [activeTaskId, setActiveTaskId] = React.useState<number | null>(null)
  const initialTaskPermalink = React.useMemo(
    () => window.location.hash.match(/^#task\/(\d+)$/),
    [],
  )
  const [taskPermalinkResolving, setTaskPermalinkResolving] = React.useState(
    () => initialTaskPermalink != null,
  )
  const [taskProjectContext, setTaskProjectContext] =
    React.useState<TaskProjectContext | null>(null)
  const taskPermalinkSeq = React.useRef(0)
  const [pendingGraphDraft, setPendingGraphDraft] = React.useState<GraphWorkflowDraft | null>(null)
  const [pendingGraphJob, setPendingGraphJob] = React.useState<number | null>(
    initialWorkRoute.current.workflowJobId,
  )
  const [graphItemId, setGraphItemId] = React.useState<number | null>(
    initialWorkRoute.current.workflowJobId,
  )
  // The graph editor's stage, lifted so chrome Back / project lock can react.
  const [graphStage, setGraphStage] = React.useState<'home' | 'editor'>('home')
  const [graphBackNonce, setGraphBackNonce] = React.useState(0)
  // Design Studio stage (canvas open = deep / project-locked).
  const [designStage, setDesignStage] = React.useState<'start' | 'studio' | 'gallery' | 'moodboard'>('start')
  const [designExitNonce, setDesignExitNonce] = React.useState(0)
  const [pendingDesign, setPendingDesign] = React.useState<{ id: number; title: string } | null>(null)
  const [pendingDesignId, setPendingDesignId] = React.useState<string | null>(
    initialWorkRoute.current.designId,
  )
  const [designItemId, setDesignItemId] = React.useState<string | null>(
    initialWorkRoute.current.designId,
  )
  // Task-linked Design binds FS to the Task owner without adopting it as Work.
  const [designProjectSlug, setDesignProjectSlug] = React.useState<string | null>(null)
  // onOpenDesign pushWorkHistory owns the navigation entry; settle must replace-only.
  const designOpenHistoryOwnedRef = React.useRef(false)
  const pushWorkHistory = React.useCallback(() => {
    window.history.pushState(
      { ...window.history.state, proximaRoute: true },
      '',
      window.location.href,
    )
  }, [])
  // Bumped by the iterate stage's "Run recipe" button → ChatScreen sends the dry-run.
  const [runRecipeNonce, setRunRecipeNonce] = React.useState(0)
  const [runRecipePrompt, setRunRecipePrompt] = React.useState<string | undefined>(undefined)
  const [runRecipeLabel, setRunRecipeLabel] = React.useState<string | undefined>(undefined)
  const [runRecipeInstantResult, setRunRecipeInstantResult] = React.useState<string | undefined>(undefined)
  const [pendingFile, setPendingFile] = React.useState<{ slug: string; path: string; target?: FileTarget } | null>(null)
  const [revealFile, setRevealFile] = React.useState<{ slug: string; path: string; pathKind?: 'root' | 'directory' | 'file'; rootSide?: 'container' | 'virtual' } | null>(null)
  // "Reveal in Files" is a window event so any surface can raise it without
  // threading a callback. The Files destination owns it now that the right rail
  // no longer carries a Files panel (ADR-0040).
  React.useEffect(() => {
    const onReveal = (event: Event) => {
      const detail = (event as CustomEvent).detail || {}
      const path = typeof detail.path === 'string' ? detail.path : ''
      if (!path) return
      const slug = typeof detail.projectSlug === 'string'
        ? detail.projectSlug
        : activeProjectRef.current?.slug
      if (!slug) return
      setRevealFile({
        slug,
        path,
        pathKind: detail.pathKind === 'root' || detail.pathKind === 'directory' ? detail.pathKind : 'file',
        rootSide: detail.rootSide === 'container' ? 'container' : 'virtual',
      })
      setView('files')
    }
    window.addEventListener('proxima:reveal-file', onReveal)
    return () => window.removeEventListener('proxima:reveal-file', onReveal)
  }, [])
  const [pendingMasterMessageId, setPendingMasterMessageId] = React.useState<number | null>(null)
  const [pendingArtifact, setPendingArtifact] = React.useState<OutputLink | null>(null)
  const reviewDraftNonce = React.useRef(0)
  const [reviewDraft, setReviewDraft] = React.useState<{ text: string; nonce: number } | null>(null)
  const clearReviewDraft = React.useCallback(() => setReviewDraft(null), [])
  const [returnToChat, setReturnToChat] = React.useState<ChatSession | null>(null)
  // Deep navigation stack: chrome Back returns to origin surface (not a fixed parent).
  const [navStack, setNavStack] = React.useState<NavStackEntry[]>([])
  const clearPendingNavigation = React.useCallback(() => {
    setPendingGraphDraft(null)
    setPendingGraphJob(null)
    setPendingDesign(null)
    setPendingDesignId(null)
    designOpenHistoryOwnedRef.current = false
    setDesignProjectSlug(null)
    setPendingFile(null)
    setPendingArtifact(null)
    setPendingMasterMessageId(null)
    setReturnToChat(null)
    setOpsMigrationSlug(null)
    if (window.location.hash.startsWith('#settings/projects/')) {
      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}${window.location.search}`,
      )
    }
  }, [])
  const clearDeepStack = React.useCallback(() => {
    setNavStack([])
  }, [])
  const clearTaskHash = React.useCallback(() => {
    if (!window.location.hash.startsWith('#task/')) return
    window.history.replaceState(
      withoutTaskPolicy(window.history.state),
      '',
      `${window.location.pathname}${window.location.search}`,
    )
  }, [])
  // Archive record permalinks (T4): #archive/<project>/<slug> is a record's
  // permanent address - bookmarkable, shareable, survives reloads.
  const [archiveRecord, setArchiveRecord] = React.useState<{ project: string; slug: string } | null>(null)
  const clearArchiveHash = React.useCallback(() => {
    if (window.location.hash.startsWith('#archive/')) window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}`)
  }, [])
  const openArchiveRecord = React.useCallback((project: string, slug: string) => {
    setArchiveRecord({ project, slug })
    const hash = `#archive/${encodeURIComponent(project)}/${encodeURIComponent(slug)}`
    if (window.location.hash.startsWith('#archive/')) {
      // Record-to-record moves (prev/next, versions) replace instead of piling
      // up history entries; one Back always returns to where Archive was opened.
      window.history.replaceState({ ...window.history.state, proximaView: 'artifacts' }, '', hash)
      setNavStack(stack => pushDeep(stack, {
        kind: 'archive-record',
        originView: 'artifacts',
        originLabel: 'Archive',
        meta: { project, slug },
      }))
    } else {
      const originView = view === 'task' ? 'task' : view
      window.history.replaceState({ ...window.history.state, proximaView: originView }, '', window.location.href)
      window.history.pushState({ ...window.history.state, proximaView: 'artifacts' }, '', hash)
      setNavStack(stack => pushDeep(stack, {
        kind: 'archive-record',
        originView,
        originLabel: viewOriginLabel(originView),
        meta: { project, slug },
      }))
    }
    setView('artifacts')
  }, [view])
  const closeArchiveRecord = React.useCallback(() => {
    clearArchiveHash()
    setArchiveRecord(null)
    setNavStack(stack => {
      const { stack: next, popped } = popDeep(stack)
      if (popped?.kind === 'archive-record') {
        setView(popped.originView === 'task' && activeTaskId != null ? 'task' : (popped.originView === 'task' ? 'activity' : popped.originView))
      } else {
        setView('artifacts')
      }
      return next
    })
  }, [clearArchiveHash, activeTaskId])
  const openTask = React.useCallback((jobId: number, origin?: View) => {
    const originView = origin ?? (view === 'task' ? 'activity' : view)
    setTaskPermalinkResolving(false)
    setTaskProjectContext({
      jobId,
      projectSlug: null,
      initialJob: null,
    })
    setNavStack(stack => pushDeep(stack, {
      kind: 'task',
      originView: originView === 'task' ? 'activity' : originView,
      originLabel: viewOriginLabel(originView === 'task' ? 'activity' : originView),
      meta: { jobId },
    }))
    setActiveTaskId(jobId)
    window.history.replaceState({ ...window.history.state, proximaView: view }, '', window.location.href)
    window.history.pushState(withInAppTaskPolicy(window.history.state), '', `#task/${jobId}`)
    setView('task')
  }, [view])
  const closeTask = React.useCallback(() => {
    clearTaskHash()
    setNavStack(stack => {
      const { stack: next, popped } = popDeep(stack)
      const dest = popped?.kind === 'task' ? popped.originView : 'activity'
      setView(dest === 'task' ? 'activity' : dest)
      setActiveTaskId(null)
      setTaskProjectContext(null)
      return next
    })
  }, [clearTaskHash])
  // A review lands where it can be acted on: a graph job's review gates live on the
  // canvas, so sending it to the linear TaskWorkspace would show a task that view has
  // no way to approve — a dangling "needs review" the owner cannot resolve.
  const openJobByEngine = React.useCallback((jobId: number, engine?: string, origin?: View) => {
    if (engine === 'graph') {
      clearTaskHash()
      setPendingGraphJob(jobId)
      setGraphItemId(jobId)
      const originView = origin ?? (view === 'workflows' ? 'workflows' : view)
      setNavStack(stack => pushDeep(stack, {
        kind: 'workflow-editor',
        originView: originView === 'workflows' || originView === 'graph' ? 'workflows' : originView,
        originLabel: viewOriginLabel(originView === 'workflows' || originView === 'graph' ? 'workflows' : originView),
        meta: { jobId },
      }))
      setView('workflows')
      return
    }
    openTask(jobId, origin)
  }, [clearTaskHash, openTask, view])
  const openDesignById = React.useCallback((designId: string, originView: View, originLabel?: string) => {
    setPendingDesign(null)
    setDesignProjectSlug(null)
    setPendingDesignId(designId)
    setDesignItemId(designId)
    setNavStack(stack => pushDeep(stack, {
      kind: 'design-canvas',
      originView,
      originLabel: originLabel ?? viewOriginLabel(originView),
    }))
    setView('design')
  }, [])
  const openOpsMigration = React.useCallback((slug: string) => {
    const project = projects.find(item => item.slug === slug)
    if (project) setActiveProject(project)
    setSettingsSection('projects')
    setOpsMigrationSlug(slug)
    setView('settings')
    const hash = `#settings/projects/${encodeURIComponent(slug)}/ops-migration`
    if (window.location.hash !== hash) {
      window.history.pushState(
        { ...window.history.state, proximaView: view },
        '',
        hash,
      )
    }
  }, [projects, setActiveProject, view])
  const closeOpsMigration = React.useCallback(() => {
    setOpsMigrationSlug(null)
    setSettingsSection('projects')
    if (window.location.hash.startsWith('#settings/projects/')) {
      window.history.replaceState(
        { ...window.history.state, proximaView: 'settings' },
        '',
        `${window.location.pathname}${window.location.search}`,
      )
    }
  }, [])
  // When GraphScreen reports stage=editor from an in-surface open (library → plan),
  // ensure chrome Back + project lock know about the deep frame.
  // Mount home/start and editor/studio+null loading must not erase a seeded deep id.
  const graphStageRef = React.useRef(graphStage)
  graphStageRef.current = graphStage
  const designStageRef = React.useRef(designStage)
  designStageRef.current = designStage
  const handleGraphStageChange = React.useCallback((stage: 'home' | 'editor', jobId: number | null) => {
    const prevStage = graphStageRef.current
    if (
      shouldPushFocusedItemHistory({
        prevStage,
        nextStage: stage,
        focusedStage: 'editor',
        reportedId: jobId,
        routedId: parseWorkRoute(window.location).workflowJobId,
      })
    ) {
      pushWorkHistory()
    }
    graphStageRef.current = stage
    setGraphStage(stage)
    setGraphItemId(current => nextFocusedWorkItemId({
      prevStage,
      nextStage: stage,
      focusedStage: 'editor',
      reportedId: jobId,
      currentId: current,
    }))
    if (stage === 'editor') {
      setNavStack(stack => {
        if (stack.some(e => e.kind === 'workflow-editor')) return stack
        return pushDeep(stack, {
          kind: 'workflow-editor',
          originView: 'workflows',
          originLabel: 'Workflows',
        })
      })
    } else if (prevStage === 'editor') {
      setNavStack(stack => stack.filter(e => e.kind !== 'workflow-editor'))
    }
  }, [pushWorkHistory])
  const handleDesignStageChange = React.useCallback((stage: 'start' | 'studio' | 'gallery' | 'moodboard', designId: string | null) => {
    const prevStage = designStageRef.current
    const historyOwned = designOpenHistoryOwnedRef.current
    let historyAlreadyOwned = false
    if (historyOwned) {
      if (stage === 'studio' && designId != null) {
        designOpenHistoryOwnedRef.current = false
        historyAlreadyOwned = true
      } else if (stage !== 'studio') {
        designOpenHistoryOwnedRef.current = false
      }
    }
    if (
      shouldPushFocusedItemHistory({
        prevStage,
        nextStage: stage,
        focusedStage: 'studio',
        reportedId: designId,
        routedId: parseWorkRoute(window.location).designId,
        historyAlreadyOwned,
      })
    ) {
      pushWorkHistory()
    }
    designStageRef.current = stage
    setDesignStage(stage)
    setDesignItemId(current => nextFocusedWorkItemId({
      prevStage,
      nextStage: stage,
      focusedStage: 'studio',
      reportedId: designId,
      currentId: current,
    }))
    if (stage === 'studio') {
      setNavStack(stack => {
        if (stack.some(e => e.kind === 'design-canvas')) return stack
        return pushDeep(stack, {
          kind: 'design-canvas',
          originView: 'design',
          originLabel: 'Design',
        })
      })
    } else if (prevStage === 'studio') {
      setNavStack(stack => stack.filter(e => e.kind !== 'design-canvas'))
    }
  }, [pushWorkHistory])
  const changeShellMode = React.useCallback((next: ShellMode, options?: { fromUrl?: boolean }) => {
    const mode = next === 'delegate' ? 'delegate' : 'work'
    if (mode === 'delegate') {
      if (view !== 'master') lastWorkView.current = view
      clearPendingNavigation()
      clearDeepStack()
      setView('master')
    } else {
      setView(lastWorkView.current === 'master' ? 'chat' : lastWorkView.current)
    }
    setShellMode(mode)
    if (!options?.fromUrl) pushWorkHistory()
  }, [clearDeepStack, clearPendingNavigation, pushWorkHistory, view])
  const openMasterConversation = React.useCallback((originMessageId?: number | null) => {
    const plan = planOpenMasterConversation(originMessageId)
    // Shared Delegate transition clears pending navigation; install the focus
    // target afterward so Work-mode Attention/Task cross-links still land.
    changeShellMode('delegate')
    setPendingMasterMessageId(plan.pendingMasterMessageId)
  }, [changeShellMode])
  const openAttentionTarget = React.useCallback((target: { view?: string; job_id?: number; engine?: string; origin_message_id?: number; container_slug?: unknown }) => {
    if (target.job_id != null) {
      openJobByEngine(target.job_id, target.engine, view)
      return
    }
    if (typeof target.container_slug === 'string' && target.container_slug) {
      clearPendingNavigation()
      clearDeepStack()
      openOpsMigration(target.container_slug)
      return
    }
    if (target.view === 'master' || target.view === 'alpha') {
      openMasterConversation(target.origin_message_id)
      return
    }
    if (target.view === 'settings') { clearPendingNavigation(); clearDeepStack(); setView('settings'); return }
    if (target.view === 'activity') { clearPendingNavigation(); clearDeepStack(); setView('activity') }
  }, [clearPendingNavigation, clearDeepStack, openJobByEngine, openMasterConversation, openOpsMigration, view])
  const goView = (v: View) => {
    if (v === 'master') {
      changeShellMode('delegate')
      return
    }
    // Delegate's intentionally global destinations retain the focused shell.
    // Work-only destinations always return through the explicit Work mode.
    if (shellMode === 'delegate' && !isDelegateDestination(v)) changeShellMode('work')
    else pushWorkHistory()
    clearTaskHash()
    clearArchiveHash()
    setArchiveRecord(null)
    clearPendingNavigation()
    clearDeepStack()
    setActiveTaskId(null)
    // Project manage is Settings → Projects (no primary-nav destination).
    if (v === 'projects') {
      setSettingsSection('projects')
      setView('settings')
      return
    }
    if (v === 'settings') setSettingsSection('account')
    if (v === 'workflows') {
      // Sidebar Workflows means the Workflows home. Re-clicking while a plan is open
      // bumps the back signal so the list returns.
      if (view === 'workflows' && graphStage === 'editor') {
        setGraphBackNonce(n => n + 1)
      }
    }
    // Chat in the nav means the conversation front door — never a workflow's
    // iteration thread, which belongs to Workflows.
    if (v === 'chat' && activeSession?.workflow_id) {
      setActiveSession(sessions.find(session => !session.workflow_id && !session.job_id && session.mode !== 'design') || null)
    }
    setView(v)
  }
  /** Chrome Back: pop deep stack and restore origin surface. */
  const handleChromeBack = React.useCallback(() => {
    setNavStack(stack => {
      if (stack.length === 0) return stack
      const { stack: next, popped } = popDeep(stack)
      if (!popped) return next
      if (popped.kind === 'task') {
        clearTaskHash()
        setActiveTaskId(null)
        setTaskProjectContext(null)
        setView(popped.originView === 'task' ? 'activity' : popped.originView)
      } else if (popped.kind === 'workflow-editor') {
        setGraphBackNonce(n => n + 1)
        if (popped.originView !== 'workflows' && popped.originView !== 'graph') {
          setView(popped.originView)
        }
      } else if (popped.kind === 'archive-record') {
        clearArchiveHash()
        setArchiveRecord(null)
        if (popped.originView === 'task' && activeTaskId != null) {
          setView('task')
        } else if (popped.originView === 'task') {
          setView('activity')
        } else {
          setView(popped.originView)
        }
      } else if (popped.kind === 'design-canvas') {
        // Leaving canvas: if Design was deep-opened from another surface, restore it.
        if (popped.originView !== 'design') {
          setPendingDesign(null)
          setPendingDesignId(null)
          designOpenHistoryOwnedRef.current = false
          setDesignProjectSlug(null)
          if (popped.originView === 'task' && activeTaskId != null) {
            window.history.replaceState(
              withInAppTaskPolicy(window.history.state),
              '',
              `#task/${activeTaskId}`,
            )
          }
          setView(popped.originView)
        } else {
          // Internal Design home: flush + leave studio stage via exitNonce.
          setPendingDesign(null)
          setPendingDesignId(null)
          designOpenHistoryOwnedRef.current = false
          setDesignProjectSlug(null)
          setDesignExitNonce(n => n + 1)
          setView('design')
        }
      } else if (popped.kind === 'settings-stack') {
        setView(popped.originView)
      }
      return next
    })
  }, [activeTaskId, clearArchiveHash, clearTaskHash])
  // Unread/activity dots: a session is "unread" when its updated_at is newer
  // than the last time you opened it. Persisted so it survives reloads.
  const [seen, setSeen] = React.useState<Record<number, string>>(() => { try { return JSON.parse(localStorage.getItem('proxima.seen') || '{}') } catch { return {} } })
  const baselined = React.useRef(false)
  const markSeen = React.useCallback((id: number, updated?: string) => {
    setSeen(prev => { const u = updated || prev[id] || ''; if (prev[id] === u) return prev; const n = { ...prev, [id]: u }; localStorage.setItem('proxima.seen', JSON.stringify(n)); return n })
  }, [])
  const [profiles, setProfiles] = React.useState<Profile[]>([])
  const [sessions, setSessions] = React.useState<ChatSession[]>([])
  const [runners, setRunners] = React.useState<Runner[]>([])
  const [runnerReadiness, setRunnerReadiness] = React.useState<RunnerReadinessMap>({})
  const [activeProfile, setActiveProfile] = React.useState<Profile | null>(null)
  const [activeSession, setActiveSession] = React.useState<ChatSession | null>(null)
  const activeSessionRef = React.useRef<ChatSession | null>(null)
  activeSessionRef.current = activeSession
  const [error, setError] = React.useState('')
  const [workCatalogReady, setWorkCatalogReady] = React.useState(false)
  React.useEffect(() => {
    if (booting || !user) return
    const syncWorkRoute = (event?: Event, initial = false) => {
      const route = parseWorkRoute(window.location)
      const nextMode = route.mode === 'delegate' ? 'delegate' : 'work'
      setShellMode(nextMode)
      // Session Design open is request-scoped, not URL state — cancel it on every
      // route application while still applying a stable route designId below.
      const designOpen = workRouteDesignOpenSync({ routeDesignId: route.designId })
      setPendingDesign(designOpen.pendingDesign)
      designOpenHistoryOwnedRef.current = designOpen.designOpenHistoryOwned
      const match = window.location.hash.match(/^#task\/(\d+)$/)
      if (match) {
        const jobId = Number(match[1])
        const historyState = event instanceof PopStateEvent
          ? event.state
          : window.history.state
        const preserveWork = taskHashPreservesWorkProject(initial, historyState)
        if (preserveWork) {
          setTaskPermalinkResolving(false)
          setTaskProjectContext(current => nextPreserveWorkTaskContext(current, jobId))
          setActiveTaskId(jobId)
          setNavStack(stack => stack.some(e => e.kind === 'task') ? stack : pushDeep(stack, {
            kind: 'task',
            originView: 'activity',
            originLabel: 'Tasks',
            meta: { jobId },
          }))
          setView('task')
          return
        }

        const seq = ++taskPermalinkSeq.current
        setTaskPermalinkResolving(true)
        setTaskProjectContext({
          jobId,
          projectSlug: null,
          initialJob: null,
        })
        setActiveTaskId(null)
        void Promise.all([getJob(token, jobId), listProjects(token)])
          .then(([job, projectBody]) => {
            if (
              seq !== taskPermalinkSeq.current
              || window.location.hash !== `#task/${jobId}`
            ) return
            const owningProject = projectBody.projects.find(
              project => project.slug === job.project_slug,
            )
            if (!owningProject) {
              throw new Error('The Task owning Project is no longer available.')
            }
            setProjects(projectBody.projects)
            setProjectFallbackNotice('')
            setActiveProject(owningProject)
            setActiveSession(recentSessionForProject(sessions, owningProject.slug))
            setTaskProjectContext({
              jobId,
              projectSlug: owningProject.slug,
              initialJob: job,
            })
            setActiveTaskId(jobId)
            setNavStack(stack => stack.some(e => e.kind === 'task') ? stack : pushDeep(stack, {
              kind: 'task',
              originView: 'activity',
              originLabel: 'Tasks',
              meta: { jobId },
            }))
            setView('task')
            setTaskPermalinkResolving(false)
          })
          .catch(cause => {
            if (seq !== taskPermalinkSeq.current) return
            setTaskPermalinkResolving(false)
            setTaskProjectContext(null)
            setError(`Task permalink could not open safely. ${String(cause)}`)
            clearTaskHash()
            setView('activity')
          })
        return
      }
      taskPermalinkSeq.current += 1
      setTaskPermalinkResolving(false)
      const archiveMatch = window.location.hash.match(/^#archive\/([^/]+)\/([^/]+)$/)
      if (archiveMatch) {
        const project = decodeURIComponent(archiveMatch[1])
        const slug = decodeURIComponent(archiveMatch[2])
        setArchiveRecord({ project, slug })
        setNavStack(stack => stack.some(e => e.kind === 'archive-record') ? stack : pushDeep(stack, {
          kind: 'archive-record',
          originView: 'artifacts',
          originLabel: 'Archive',
          meta: { project, slug },
        }))
        setView('artifacts')
        return
      }
      const migrationSlug = opsMigrationSlugFromHash(window.location.hash)
      if (migrationSlug) {
        setOpsMigrationSlug(migrationSlug)
        setSettingsSection('projects')
        const project = projects.find(item => item.slug === migrationSlug)
        if (project) setActiveProject(project)
        setView('settings')
        return
      }
      setArchiveRecord(null)
      setOpsMigrationSlug(null)
      setNavStack(stack => stack.filter(e => e.kind !== 'task' && e.kind !== 'archive-record'))
      setActiveTaskId(null)
      if (nextMode === 'delegate') {
        setView(isDelegateDestination(route.view) ? route.view : 'master')
        return
      }
      const project = route.projectSlug
        ? projects.find(item => item.slug === route.projectSlug) || null
        : null
      const fallbackProject =
        project
        || projects.find(item => item.visibility === 'private')
        || projects[0]
        || null
      setActiveProject(fallbackProject)
      setActiveSession(
        resolveRoutedWorkSession({
          sessions,
          projectSlug: fallbackProject?.slug,
          sessionId: route.sessionId,
        }),
      )
      const nextView = route.view
      if (
        graphStage === 'editor' &&
        (nextView !== 'workflows' || route.workflowJobId == null)
      ) {
        setGraphBackNonce(value => value + 1)
      }
      if (
        designStage === 'studio' &&
        (nextView !== 'design' || route.designId == null)
      ) {
        setDesignExitNonce(value => value + 1)
      }
      setPendingGraphJob(route.workflowJobId)
      setGraphItemId(route.workflowJobId)
      setPendingDesignId(designOpen.pendingDesignId)
      setDesignItemId(route.designId)
      setView(nextView)
    }
    if (!workRouteBootstrapped.current) {
      workRouteBootstrapped.current = true
      if (
        window.location.hash.startsWith('#task/') ||
        window.location.hash.startsWith('#archive/') ||
        opsMigrationSlugFromHash(window.location.hash) != null
      ) {
        syncWorkRoute(undefined, true)
      }
    }
    window.addEventListener('hashchange', syncWorkRoute)
    window.addEventListener('popstate', syncWorkRoute)
    return () => {
      window.removeEventListener('hashchange', syncWorkRoute)
      window.removeEventListener('popstate', syncWorkRoute)
    }
  }, [
    booting,
    clearTaskHash,
    designStage,
    graphStage,
    projects,
    sessions,
    setActiveProject,
    token,
    user?.id,
  ])
  React.useEffect(() => {
    if (booting || !user || !workCatalogReady) return
    const route: WorkRoute = {
      mode: shellMode,
      view,
      projectSlug: shellMode === 'work' ? activeProject?.slug || null : null,
      sessionId: workRouteSessionId({
        mode: shellMode,
        projectSlug: activeProject?.slug,
        activeSession,
      }),
      ...workRouteFocusedItemIds({
        mode: shellMode,
        view,
        graphItemId,
        designItemId,
      }),
    }
    const nextUrl = workRouteUrl(window.location.href, route)
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`
    if (nextUrl !== currentUrl) {
      window.history.replaceState(
        { ...window.history.state, proximaRoute: true },
        '',
        nextUrl,
      )
    }
  }, [
    activeProject?.slug,
    activeSession,
    booting,
    designItemId,
    graphItemId,
    shellMode,
    user?.id,
    view,
    workCatalogReady,
  ])
  const refreshSeq = React.useRef(0)
  const sessionsSeq = React.useRef(0)
  const activeRunsSeq = React.useRef(0)
  const appActionSeq = React.useRef(0)
  const reviewHandoffSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      refreshSeq.current += 1
      sessionsSeq.current += 1
      activeRunsSeq.current += 1
      appActionSeq.current += 1
      reviewHandoffSeq.current += 1
    }
  }, [])

  const refreshAll = React.useCallback(async (
    authToken = token,
    ownerId = user?.id,
  ) => {
    if (!authToken) return
    const seq = ++refreshSeq.current
    const sessionSeq = ++sessionsSeq.current
    const [profileBody, projectBody, sessionBody, runnerBody] = await Promise.all([
      listProfiles(authToken),
      listProjects(authToken),
      listSessions(authToken),
      api<{ runners: Runner[]; runnerReadiness?: RunnerReadinessMap }>('/api/runners/detect', authToken)
    ])
    if (!mountedRef.current || seq !== refreshSeq.current) return
    setProfiles(profileBody.profiles)
    setProjects(projectBody.projects)
    if (sessionSeq === sessionsSeq.current) setSessions(sessionBody.sessions)
    setRunners(runnerBody.runners)
    setRunnerReadiness(runnerBody.runnerReadiness || {})
    setActiveProfile(current => current && profileBody.profiles.some(p => p.id === current.id) ? current : profileBody.profiles.find(p => p.is_default) || profileBody.profiles[0] || null)
    // Restore the URL-selected project/session as one validated unit. Missing or
    // deleted identities fall back within the resolved project, never into another
    // project's saved composer state. Delegate URLs intentionally omit Work
    // identities - keep/validate the in-memory background Work selection instead.
    const route = parseWorkRoute(window.location)
    if (route.mode !== 'work') {
      const preserved = resolvePreservedWorkSelection({
        projects: projectBody.projects,
        sessions: sessionBody.sessions,
        activeProject: activeProjectRef.current,
        activeSession: activeSessionRef.current,
      })
      // Session restore is owned by refreshAll (refreshSeq), not the sessions list
      // poll. The poll may bump sessionsSeq while this request is in flight so the
      // list stays fresh; that must not drop the URL/background Work session.
      setActiveSession(preserved.session)
      setActiveProject(preserved.project)
      setWorkCatalogReady(true)
      return
    }
    // Work URLs win when present; otherwise restore the owner's saved Work project.
    const preference = ownerId == null ? null : readWorkProjectPreference(ownerId)
    const preferenceResolution = resolveWorkProject(
      projectBody.projects,
      preference,
      activeProjectRef.current,
    )
    const requestedProject = route.projectSlug
      ? projectBody.projects.find(p => p.slug === route.projectSlug) || null
      : null
    const requestedSession = route.sessionId != null
      ? sessionBody.sessions.find(
          s =>
            s.id === route.sessionId &&
            (!requestedProject || s.project_slug === requestedProject.slug),
        ) || null
      : null
    const nextProject = requestedProject
      || (requestedSession?.project_slug
        ? projectBody.projects.find(p => p.slug === requestedSession.project_slug) || null
        : null)
      || preferenceResolution.project
      || fallbackProject(projectBody.projects)
    if (preferenceResolution.missingPreference && nextProject && !requestedProject && !requestedSession) {
      setProjectFallbackNotice(
        `Saved Work Project "${preferenceResolution.missingPreference.name}" is no longer available. Switched to "${nextProject.name}".`,
      )
    } else {
      setProjectFallbackNotice('')
    }
    const nextSession = resolveRoutedWorkSession({
      sessions: sessionBody.sessions,
      projectSlug: nextProject?.slug,
      sessionId: route.sessionId,
    }) || recentSessionForProject(sessionBody.sessions, nextProject?.slug)
    setActiveSession(nextSession)
    // Ops migration hash locks shell scope onto that Project; otherwise keep the
    // Work URL / preference resolution above.
    setActiveProject(projectForShellScope({
      projects: projectBody.projects,
      migrationSlug: opsMigrationSlug,
      currentProject: nextProject,
    }))
    setWorkCatalogReady(true)
  }, [opsMigrationSlug, token, user?.id, setActiveProject])

  React.useEffect(() => {
    if (!user || !activeProject) return
    persistWorkProjectPreference(user.id, activeProject)
  }, [activeProject, user])

  // When a session opens/changes, pull the shell project to match so Files and
  // other rails start on the conversation's project. Do NOT depend on
  // activeProject here - an intentional Projects/Tasks pick must stick even
  // while an older chat session remains selected in memory (Chat header already
  // prefers the session project over a desynced shell pick).
  React.useEffect(() => {
    setActiveProject(current => {
      return projectForShellScope({
        projects,
        migrationSlug: opsMigrationSlug,
        sessionProjectSlug: activeSession?.project_slug,
        currentProject: current,
      })
    })
  }, [activeSession?.id, activeSession?.project_slug, opsMigrationSlug, projects])

  // On first load, treat existing sessions as already seen (only NEW activity dots).
  React.useEffect(() => {
    if (baselined.current || sessions.length === 0) return
    baselined.current = true
    setSeen(prev => { const n = { ...prev }; let ch = false; for (const s of sessions) if (!(s.id in n)) { n[s.id] = s.updated_at || ''; ch = true } if (ch) localStorage.setItem('proxima.seen', JSON.stringify(n)); return n })
  }, [sessions])
  // The chat you're currently viewing is always considered seen.
  React.useEffect(() => {
    if (!activeSession || view !== 'chat') return
    const row = sessions.find(s => s.id === activeSession.id)
    if (row) markSeen(row.id, row.updated_at)
  }, [sessions, activeSession, view, markSeen])
  // Refresh the sessions list when a run finishes so its dot lights up.
  React.useEffect(() => {
    if (!token) return
    const h = () => {
      const seq = ++sessionsSeq.current
      void listSessions(token).then(r => { if (mountedRef.current && seq === sessionsSeq.current) setSessions(r.sessions) }).catch(() => {})
      const activeSeq = ++activeRunsSeq.current
      void activeRuns(token).then(r => { if (mountedRef.current && activeSeq === activeRunsSeq.current) setBusySessions(r.session_ids) }).catch(() => {})
    }
    window.addEventListener('proxima:files-changed', h)
    return () => {
      sessionsSeq.current += 1
      activeRunsSeq.current += 1
      window.removeEventListener('proxima:files-changed', h)
    }
  }, [token])
  // Poll which sessions have an in-flight run → sidebar "thinking" indicator that
  // survives navigating away from the chat (ChatScreen's busyRun is local + unmounts).
  const [busySessions, setBusySessions] = React.useState<number[]>([])
  // null = "no poll yet": the first poll after (re)auth always refreshes the session
  // list, so a boot-time listSessions response lost to the seq-guard race still
  // heals — otherwise a fresh browser with no run activity never shows history.
  const prevBusyKey = React.useRef<string | null>(null)
  const pollActiveRuns = React.useCallback(async () => {
    if (!token) return
    try {
      const seq = ++activeRunsSeq.current
      const r = await activeRuns(token)
      if (!mountedRef.current || seq !== activeRunsSeq.current) return
      setBusySessions(r.session_ids)
      // When the busy set changes (a run started or finished), refresh the session
      // list so updated_at is fresh — that lights the unread dot for a chat whose
      // agent replied while you were elsewhere. The dot persists until you open it.
      const key = r.session_ids.slice().sort((a, b) => a - b).join(',')
      if (key !== prevBusyKey.current) {
        prevBusyKey.current = key
        const sessionSeq = ++sessionsSeq.current
        const s = await listSessions(token)
        if (mountedRef.current && sessionSeq === sessionsSeq.current) setSessions(s.sessions)
      }
    } catch { /* transient polling failure — retry on the next tick */ }
  }, [token])
  usePolling(pollActiveRuns, 2500, { enabled: !!token, restartKey: token })
  React.useEffect(() => {
    if (!token) {
      setBusySessions([])
      prevBusyKey.current = null
    }
    return () => {
      activeRunsSeq.current += 1
      sessionsSeq.current += 1
    }
  }, [token])

  React.useEffect(() => {
    async function boot() {
      // Password gate: first run forces a password; after that, a valid stored
      // session enters the app, otherwise show the login screen.
      try {
        const status = await setupStatus()
        if (!mountedRef.current) return
        if (!status.password_set) {
          setAuthGate('setup')
        } else {
          // Auth persists in the HttpOnly cookie, not JS storage. resume() is
          // authenticated by that cookie and echoes back the session token for the
          // in-memory bearer header; 401 → show the login screen.
          try {
            const s = await resume()
            if (!mountedRef.current) return
            setToken(s.token); setUser(s.user)
            await refreshAll(s.token, s.user.id)
          } catch {
            if (mountedRef.current) setAuthGate('login')
          }
        }
      } catch (err) {
        if (mountedRef.current) setError(String(err))
      } finally {
        if (mountedRef.current) {
          setBooting(false)
        }
      }
    }
    void boot()
  // Run once on mount; refreshAll closes over the latest token via its own deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    // A durable ?mode=delegate URL opens its desk on first load. Once inside
    // Delegate, its global destinations remain addressable without falling back
    // to a hidden Work chat.
    if (!booting && shellMode === 'delegate' && !isDelegateDestination(view)) {
      setView('master')
    }
  }, [booting, shellMode, view])
  React.useEffect(() => {
    if (shellMode === 'work' && view !== 'master') lastWorkView.current = view
  }, [shellMode, view])

  // A new chat is just a blank composer — no DB session yet. The session is created
  // lazily on the first message (ChatScreen.ensureSession), so empty chats never
  // clutter the sidebar; a thread appears there only once it has a real conversation.
  async function startNewSession() {
    pushWorkHistory()
    clearPendingNavigation()
    setActiveSession(null)
    setView('chat')
  }

  const createTask = (request: OpsTaskRequest) => createAndStartOpsTask(token, request)

  // Header ProjectSwitcher: filter the shell active project (and the chat session
  // so Chat stays coherent when the owner later opens it). Do not force Chat —
  // stay on Workflows / Master / Archive / Design / Tasks / Settings.
  function setActiveProjectOnly(p: Project | null) {
    if (p?.slug !== activeProject?.slug) pushWorkHistory()
    clearPendingNavigation()
    setProjectFallbackNotice('')
    setActiveProject(p)
    if (!p) { setActiveSession(null); return }
    setActiveSession(recentSessionForProject(sessions, p.slug))
  }

  // Intentional "open this project's chat" (Search, etc.): same shell filter, then Chat.
  function selectProject(p: Project | null) {
    setActiveProjectOnly(p)
    if (p) setView('chat')
  }

  async function handleRenameSession(id: number, title: string) {
    const seq = ++appActionSeq.current
    await renameSession(token, id, title)
    if (!mountedRef.current || seq !== appActionSeq.current) return
    await refreshAll(token)
  }

  async function handleDeleteSession(id: number) {
    const seq = ++appActionSeq.current
    await deleteSession(token, id)
    if (!mountedRef.current || seq !== appActionSeq.current) return
    setActiveSession(current => (current?.id === id ? null : current))
    await refreshAll(token)
  }


  function openOutput(link: OutputLink, origin: ChatSession | null) {
    pushWorkHistory()
    const targetSlug = link.project_slug || origin?.project_slug || activeProject?.slug || null
    const targetProject = targetSlug ? projects.find(p => p.slug === targetSlug) : null
    if (targetProject) setActiveProject(targetProject)
    if (origin) setReturnToChat(origin)
    if (link.type === 'design') {
      setDesignProjectSlug(null)
      const designId = link.id || link.path.split('/').filter(Boolean).slice(-1)[0] || null
      if (designId) openDesignById(designId, 'chat', 'Chat')
      return
    }
    if (targetSlug && link.path) {
      setPendingArtifact({ ...link, project_slug: targetSlug })
      setView('artifacts')
    }
  }

  // Archive lineage: jump from a record straight to the chat that produced it.
  function openSessionById(sessionId: number) {
    const session = sessions.find(s => s.id === sessionId)
    if (!session) return
    pushWorkHistory()
    clearTaskHash()
    clearArchiveHash()
    setArchiveRecord(null)
    clearPendingNavigation()
    clearDeepStack()
    setActiveSession(session)
    const sp = projects.find(p => p.slug === session.project_slug)
    if (sp) setActiveProject(sp)
    markSeen(session.id, session.updated_at)
    setView('chat')
  }

  async function continueArtifactReview(feedback: ArtifactReviewFeedback) {
    const seq = ++reviewHandoffSeq.current
    const resolved = await resolveArtifactReviewTarget({
      sessions,
      sessionId: feedback.sessionId,
      fallback: returnToChat || activeSession,
      loadSession: sessionId => getSession(token, sessionId),
      projects,
    })
    if (!mountedRef.current || seq !== reviewHandoffSeq.current) {
      return { ok: false as const, message: 'The artifact review changed before feedback could be handed off.' }
    }
    if (!resolved.ok) {
      setError(resolved.message)
      return resolved
    }
    pushWorkHistory()
    const { session: target, project } = resolved
    clearTaskHash()
    clearArchiveHash()
    setArchiveRecord(null)
    clearPendingNavigation()
    clearDeepStack()
    setActiveSession(target)
    setActiveProject(project)
    markSeen(target.id, target.updated_at)
    reviewDraftNonce.current += 1
    setReviewDraft({ text: feedback.text, nonce: reviewDraftNonce.current })
    setError('')
    setView('chat')
    return { ok: true as const }
  }

  const designCanvasOpen = designStage === 'studio'
  const deepFlags = {
    view,
    graphStage,
    archiveRecord,
    designCanvasOpen,
    settingsStack: false,
  }
  const projectLocked = projectSwitcherLocked(deepFlags) || opsMigrationSlug != null
  const chromeBackEnabled = canGoBack(navStack)
  const chromeBackTitle = chromeBackLabel(navStack)

  const handleAuthed = (s: { token: string; user: User }) => {
    // Keep the token in memory for this session's bearer header; the cookie carries
    // it across reloads. Nothing goes to localStorage.
    // authGate still holds its pre-auth value here — 'setup' means this is the very
    // first run, so show the "pick a working folder" step before the app.
    const firstRun = authGate === 'setup'
    setToken(s.token); setUser(s.user); setAuthGate(null)
    if (firstRun) setOnboarding(true)
    void refreshAll(s.token, s.user.id)
  }
  const handleOnboardingDone = async (linked: Project | null) => {
    setOnboarding(false)
    if (linked) {
      // They picked a real folder, so drop the empty auto-provisioned starter —
      // its DB row AND its scaffold dir (delete is jailed to the data dir, so the
      // linked folder's real files are never touched). This is first-run, so the
      // only project that existed before this link is that starter.
      try {
        const { projects: all } = await listProjects(token)
        await Promise.all(all.filter(p => p.slug !== linked.slug).map(p => deleteProject(token, p.slug).catch(() => {})))
      } catch { /* best-effort — leaves the removable starter in place on failure */ }
    }
    await refreshAll(token)
    // Make the linked folder the active project; if they skipped, the starter stays
    // active. Either way the app lands on Chat — the front door.
    if (linked) { setActiveProject(linked); setView('chat') }
  }
  const handleLogout = async () => {
    const currentToken = token
    setToken(''); setUser(null); setAuthGate('login')
    setWorkCatalogReady(false)
    setProjects([])
    setSessions([])
    setActiveSession(null)
    setActiveProject(null)
    try { await logout(currentToken) } catch { /* best-effort; the local app is already inert */ }
  }

  if (booting) return <div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>Starting Proxima…</p></div>
  if (authGate) return <AuthGate mode={authGate} onAuthed={handleAuthed} />
  if (!token || !user) return <div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>{error || 'Connecting…'}</p></div>
  if (onboarding) return <React.Suspense fallback={<div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>Loading…</p></div>}><WorkspaceOnboarding token={token} onDone={linked => void handleOnboardingDone(linked)} /></React.Suspense>
  if (taskPermalinkResolving) return (
    <div className="center-screen" role="status">
      <ProximaMark className="proxima-mark-boot" label="Proxima" />
      <p>Resolving Task Project...</p>
    </div>
  )

  const workChatStateKeys = [
    ...projects.map(project => workChatStateKey(project.slug, null)),
    ...sessions.map(session => workChatStateKey(session.project_slug, session.id)),
  ].filter((key): key is string => key !== null)
  const pane = (id: View, active: boolean, body: React.ReactNode) => (
    <div className="surface-pane" hidden={!active} aria-hidden={!active} data-surface={id}>{body}</div>
  )
  const keep = (id: View) => aliveViews.has(id) || view === id
  const chatActive = view === 'chat'
  const delegateActive = shellMode === 'delegate'
  const masterActive = delegateActive && view === 'master'
  const masterHomeActive = masterActive
  const activityActive = view === 'activity'
  const filesActive = view === 'files'
  const workflowsActive = view === 'workflows'
  const artifactsActive = view === 'artifacts'
  const designActive = view === 'design'
  const projectToolsSynchronized = view !== 'task' || (
    taskProjectContext?.jobId === activeTaskId
    && taskProjectContext.projectSlug != null
    && taskProjectContext.projectSlug === activeProject?.slug
  )
  const handleTaskResolved = (job: import('./types').Job) => {
    setTaskProjectContext(current => withResolvedTaskOwnership(current, job))
  }

  return (
    <MasterStateProvider
      token={token}
      ownerId={user.id}
      enabled={!updates.applying}
    >
    <WorkChatStateProvider
      ownerId={user.id}
      availableKeys={workChatStateKeys}
      availabilityReady={workCatalogReady}
    >
    <AppShell
      activeProfile={activeProfile}
      activeProject={activeProject}
      activeSession={activeSession}
      currentView={view}
      mode={shellMode}
      onModeChange={changeShellMode}
      onLogout={() => void handleLogout()}
      onNewChat={() => void startNewSession()}
      onRenameSession={(id, title) => void handleRenameSession(id, title)}
      onDeleteSession={id => void handleDeleteSession(id)}
      onSelectProject={setActiveProjectOnly}
      onProjectRenamed={project => {
        setProjects(list => list.map(item => item.slug === project.slug ? project : item))
        setActiveProject(current => current?.slug === project.slug ? project : current)
      }}
      onOpenProject={selectProject}
      onSelectSession={session => { pushWorkHistory(); clearPendingNavigation(); clearDeepStack(); setActiveSession(session); const sp = projects.find(p => p.slug === session.project_slug); if (sp) setActiveProject(sp); markSeen(session.id, session.updated_at); setView('chat') }}
      onOpenDesign={session => {
        pushWorkHistory()
        designOpenHistoryOwnedRef.current = true
        const sp = projects.find(p => p.slug === session.project_slug)
        if (sp) setActiveProject(sp)
        markSeen(session.id, session.updated_at)
        setDesignProjectSlug(null)
        setPendingDesignId(null)
        setDesignItemId(null)
        setPendingDesign({ id: session.id, title: session.title })
        setNavStack(stack => pushDeep(stack, { kind: 'design-canvas', originView: view, originLabel: viewOriginLabel(view) }))
        setView('design')
      }}
      seen={seen}
      busySessions={busySessions}
      onSelectView={goView}
      onOpenAttentionTarget={openAttentionTarget}
      onOpenRunningJob={openJobByEngine}
      onOpenRunningSession={openSessionById}
      profiles={profiles}
      projects={projects}
      sessions={sessions}
      token={token}
      user={user}
      updateVersion={updates.status?.update_available ? updates.status.latest?.version ?? null : null}
      onUpdateClick={updates.openModal}
      chromeBackEnabled={chromeBackEnabled}
      chromeBackLabel={chromeBackTitle}
      onChromeBack={handleChromeBack}
      projectLocked={projectLocked}
      projectLockedReason="Project is locked while this view is open"
      projectToolsAvailable={projectToolsSynchronized}
    >
      {error && <div className="error-bar">{error}</div>}
      {projectFallbackNotice && (
        <div className="project-fallback-notice" role="status">
          <span>{projectFallbackNotice}</span>
          <button
            type="button"
            className="text-button"
            onClick={() => setProjectFallbackNotice('')}
          >
            Dismiss
          </button>
        </div>
      )}
      {!delegateActive && <HermesBanner token={token} runnerId={activeProfile?.runner_id} />}
      {!delegateActive && view === 'home' && <HomeScreen token={token} ownerName={user?.username} projects={projects} activeProject={activeProject} activeProfile={activeProfile} profiles={profiles} runnerReadiness={runnerReadiness}
        onActiveProject={setActiveProject} onActiveProfile={setActiveProfile} onCreateTask={createTask} onOpenJob={openJobByEngine} onSelectView={goView} />}
      {pane('master', masterHomeActive, <React.Suspense fallback={<ViewFallback label="Loading Master home..." />}><MasterScreen active={masterHomeActive} token={token} runners={runners} activeProject={null} onOpenJob={(id, engine) => openJobByEngine(id, engine, masterActive ? 'master' : 'home')} focusMessageId={pendingMasterMessageId} onFocusMessageConsumed={() => setPendingMasterMessageId(null)} /></React.Suspense>)}
      {(() => {
        // Keep Chat mounted (hidden when inactive) so draft text + busy run re-attach after leave/return.
        const mainSession = activeSession?.mode === 'design' ? null : activeSession
        const chat = <ChatScreen active={chatActive} activeProfile={activeProfile} activeProject={activeProject} activeSession={mainSession} profiles={profiles} projects={projects} runnerReadiness={runnerReadiness} token={token} onActiveProfile={setActiveProfile} onActiveProject={setActiveProjectOnly} onSession={setActiveSession} onRefresh={refreshAll} onNewSession={startNewSession} onGraphDraft={draft => { setPendingGraphDraft(draft); setView('workflows') }} onOpenOutput={openOutput} runRecipeNonce={runRecipeNonce} runRecipePrompt={runRecipePrompt} runRecipeLabel={runRecipeLabel} runRecipeInstantResult={runRecipeInstantResult} draftSeed={reviewDraft?.text} draftSeedNonce={reviewDraft?.nonce} onDraftSeedConsumed={clearReviewDraft} />
        const body = activeSession?.workflow_id
          ? <div className="iterate-split">{chat}<React.Suspense fallback={<ViewFallback label="Loading workflow stage..." />}><IterateStage token={token} workflowId={activeSession.workflow_id} sessionId={activeSession.id} projectSlug={activeSession.project_slug || activeProject?.slug || null} running={busySessions.includes(activeSession.id)} designStudioEnabled onOpenDesign={id => { openDesignById(id, 'chat', 'Chat') }} onRunRecipe={(prompt, label, instantResult) => { setRunRecipePrompt(prompt); setRunRecipeLabel(label); setRunRecipeInstantResult(instantResult); setRunRecipeNonce(n => n + 1) }} /></React.Suspense></div>
          : chat
        return pane('chat', chatActive, body)
      })()}
      {view === 'wiki' && <React.Suspense fallback={<ViewFallback label="Loading wiki..." />}><WikiScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} /></React.Suspense>}
      {keep('artifacts') && pane('artifacts', artifactsActive, <React.Suspense fallback={<ViewFallback label="Loading archive..." />}><ArtifactsScreen token={token} projects={projects} activeProject={delegateActive ? null : activeProject} globalScope={delegateActive} archiveRecord={archiveRecord} pendingFile={pendingFile} pendingArtifact={pendingArtifact} onPendingConsumed={() => setPendingFile(null)} onPendingArtifactConsumed={() => setPendingArtifact(null)} onActiveProject={delegateActive ? undefined : setActiveProject} onOpenRecord={openArchiveRecord} onCloseRecord={closeArchiveRecord} onOpenTask={openJobByEngine} onOpenSession={delegateActive ? undefined : openSessionById} designStudioEnabled={!delegateActive} onOpenDesign={delegateActive ? undefined : id => { const origin = archiveRecord ? 'artifacts' : view; openDesignById(id, origin) }} reviewSessionId={delegateActive ? null : returnToChat?.id ?? activeSession?.id ?? null} onSendFeedback={delegateActive ? undefined : continueArtifactReview} /></React.Suspense>)}
      {keep('files') && pane('files', filesActive, <React.Suspense fallback={<ViewFallback label="Loading files..." />}><FilesScreen token={token} projects={projects} activeProject={delegateActive ? null : activeProject} globalScope={delegateActive} revealPath={revealFile} onRevealConsumed={() => setRevealFile(null)} /></React.Suspense>)}
      {keep('workflows') && pane('workflows', workflowsActive, <React.Suspense fallback={<ViewFallback label="Loading workflows..." />}><WorkflowsScreen graphContent={<GraphScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} profiles={profiles} profileId={activeProfile?.id ?? null} activeProfile={activeProfile} pendingDraft={pendingGraphDraft} onDraftConsumed={() => setPendingGraphDraft(null)} pendingJobId={pendingGraphJob} onPendingConsumed={() => setPendingGraphJob(null)} onStageChange={handleGraphStageChange} backNonce={graphBackNonce} />} /></React.Suspense>)}
      {keep('activity') && pane('activity', activityActive, <React.Suspense fallback={<ViewFallback label="Loading tasks..." />}><ActivityScreen token={token} activeProject={delegateActive ? null : activeProject} projects={projects} globalScope={delegateActive} profiles={profiles} onOpenTask={jobId => openTask(jobId, 'activity')} onOpenPlan={jobId => {
        // A graph plan editor is a Work surface. Opening it is an explicit
        // mode change, never an accidental Workflows route inside Delegate.
        if (delegateActive) changeShellMode('work')
        openJobByEngine(jobId, 'graph', 'activity')
      }} onNewTask={delegateActive ? undefined : () => goView('home')} /></React.Suspense>)}
      {view === 'task' && activeTaskId != null && <React.Suspense fallback={<ViewFallback label="Loading task..." />}><section className="tasks-view task-workspace-view"><ContextualTaskWorkspace token={token} jobId={activeTaskId} initialJob={taskProjectContext?.jobId === activeTaskId ? taskProjectContext.initialJob : null} onResolved={handleTaskResolved} onBack={closeTask} projects={projects} selectedWorkProject={delegateActive ? null : activeProject} designStudioEnabled={!delegateActive} onOpenDesign={delegateActive ? undefined : (id, projectSlug) => {
          clearTaskHash()
          setDesignProjectSlug(taskLinkedDesignProjectSlug(
            projectSlug,
            taskProjectContext?.jobId === activeTaskId ? taskProjectContext.projectSlug : null,
          ))
          setPendingDesignId(id)
          setDesignItemId(id)
          setNavStack(stack => pushDeep(stack, { kind: 'design-canvas', originView: 'task', originLabel: 'Task' }))
          setView('design')
        }} onOpenFile={(slug, path, target) => { setPendingFile({ slug, path, target }); setView('artifacts') }} onOpenJob={(id, engine) => openJobByEngine(id, engine, 'task')} onOpenMaster={originMessageId => openMasterConversation(originMessageId)} /></section></React.Suspense>}
      {view === 'graph' && <React.Suspense fallback={<ViewFallback label="Loading workflow graph..." />}><GraphScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} profiles={profiles} profileId={activeProfile?.id ?? null} activeProfile={activeProfile} pendingDraft={pendingGraphDraft} onDraftConsumed={() => setPendingGraphDraft(null)} pendingJobId={pendingGraphJob} onPendingConsumed={() => setPendingGraphJob(null)} onStageChange={handleGraphStageChange} /></React.Suspense>}
      {keep('design') && pane('design', designActive, <React.Suspense fallback={<div className="ds-loading muted">Loading Design Studio...</div>}><DesignStudio token={token} project={resolveDesignStudioProject(projects, designProjectSlug, activeProject)} profileId={activeProfile?.id ?? null} openSession={pendingDesign} openDesignId={pendingDesignId} onOpened={() => { setPendingDesign(null); setPendingDesignId(null) }} onStageChange={handleDesignStageChange} exitNonce={designExitNonce} /></React.Suspense>)}
      {view === 'profiles' && <React.Suspense fallback={<ViewFallback label="Loading agents..." />}><ProfilesScreen token={token} profiles={profiles} onActiveProfile={setActiveProfile} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'runners' && <React.Suspense fallback={<ViewFallback label="Loading..." />}><RunnersScreen runners={runners} runnerReadiness={runnerReadiness} token={token} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'settings' && <React.Suspense fallback={<ViewFallback label="Loading settings..." />}><SettingsScreen token={token} user={user} profiles={profiles} projects={projects} activeProject={activeProject} opsMigrationSlug={opsMigrationSlug} onActiveProject={setActiveProject} onOpenOpsMigration={project => openOpsMigration(project.slug)} onCloseOpsMigration={closeOpsMigration} runners={runners} runnerReadiness={runnerReadiness} onRefresh={refreshAll} onTokenChange={setToken} updateStatus={updates.status} updateChecking={updates.checking} onCheckUpdates={updates.check} onOpenUpdate={updates.openModal} initialSection={settingsSection} /></React.Suspense>}
      {updates.modalOpen && updates.status?.latest && <UpdateModal status={updates.status} onApply={updates.apply} onClose={updates.closeModal} />}
      {updates.applying && <UpdateOverlay applying={updates.applying} onDismiss={updates.dismissApplying} />}
      <DialogHost />
    </AppShell>
    </WorkChatStateProvider>
    </MasterStateProvider>
  )
}

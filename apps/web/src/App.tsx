import React from 'react'
import { resume, setupStatus, logout } from './api/auth'
import { listProfiles } from './api/profiles'
import { listProjects, deleteProject } from './api/projects'
import { listSessions, getSession, renameSession, deleteSession } from './api/sessions'
import { activeRuns, createRun } from './api/runs'
import { createJob, deleteJob, linkJobRun, startJob } from './api/jobs'
import { api } from './api/client'
import { getAppFeatures } from './api/config'
import { DEFAULT_FEATURES, isFeatureSessionEnabled, isFeatureViewEnabled } from './features'
import type { AppFeatures, ChatSession, GraphWorkflowDraft, OutputLink, Profile, Project, Runner, User, View } from './types'
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
const IterateStage = React.lazy(() => import('./screens/IterateStage').then(m => ({ default: m.IterateStage })))
const DesignStudio = React.lazy(() => import('./screens/DesignStudio').then(m => ({ default: m.DesignStudio })))
const ProjectsScreen = React.lazy(() => import('./screens/ProjectsScreen').then(m => ({ default: m.ProjectsScreen })))
const WikiScreen = React.lazy(() => import('./screens/WikiScreen').then(m => ({ default: m.WikiScreen })))
const ArtifactsScreen = React.lazy(() => import('./screens/ArtifactsScreen').then(m => ({ default: m.ArtifactsScreen })))
const WorkflowsScreen = React.lazy(() => import('./screens/WorkflowsScreen').then(m => ({ default: m.WorkflowsScreen })))
const ActivityScreen = React.lazy(() => import('./screens/ActivityScreen').then(m => ({ default: m.ActivityScreen })))
const AlphaScreen = React.lazy(() => import('./screens/AlphaScreen').then(m => ({ default: m.AlphaScreen })))
const TaskWorkspace = React.lazy(() => import('./screens/TaskWorkspace').then(m => ({ default: m.TaskWorkspace })))
const GraphScreen = React.lazy(() => import('./screens/GraphScreen').then(m => ({ default: m.GraphScreen })))
const ProfilesScreen = React.lazy(() => import('./screens/ProfilesScreen').then(m => ({ default: m.ProfilesScreen })))
const RunnersScreen = React.lazy(() => import('./screens/RunnersScreen').then(m => ({ default: m.RunnersScreen })))
const SettingsScreen = React.lazy(() => import('./screens/SettingsScreen').then(m => ({ default: m.SettingsScreen })))
const WorkspaceOnboarding = React.lazy(() => import('./screens/WorkspaceOnboarding').then(m => ({ default: m.WorkspaceOnboarding })))

type OpsTaskKind = 'agent' | 'image' | 'design'
const opsTaskKind = (brief: string): OpsTaskKind => /^\/(image|gambar)\b/i.test(brief) ? 'image' : /^\/(design|image-studio|design-studio)\b/i.test(brief) ? 'design' : 'agent'
const mediaBriefIsThin = (brief: string) => {
  if (/!\[[^\]]*\]\([^)]+\)/.test(brief)) return false
  const detail = brief.trim().replace(/^\/\S+\s*/i, '').trim()
  return detail.split(/\s+/).filter(Boolean).length < 3
}

export async function resolveArtifactReviewSession(args: {
  sessions: ChatSession[]
  sessionId: number | null
  fallback: ChatSession | null
  loadSession: (sessionId: number) => Promise<ChatSession>
}): Promise<ChatSession | null> {
  if (args.sessionId == null) return args.fallback
  const listed = args.sessions.find(session => session.id === args.sessionId)
  if (listed) return listed
  try {
    return await args.loadSession(args.sessionId)
  } catch {
    return null
  }
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

function ViewFallback({ label = 'Loading...' }: { label?: string }) {
  return <section className="placeholder-view"><div className="assistant-bubble compact"><p className="muted">{label}</p></div></section>
}

export function App() {
  const [booting, setBooting] = React.useState(true)
  // In-memory only. Persistent auth lives in the HttpOnly proxima_session cookie
  // (which XSS can't read); nothing sensitive is kept in localStorage.
  const [token, setToken] = React.useState('')
  const updates = useUpdateStatus(token)
  const [user, setUser] = React.useState<User | null>(null)
  const [authGate, setAuthGate] = React.useState<'setup' | 'login' | null>(null)
  // First-run only: after the password is set, offer to point Proxima at a real
  // code folder before landing in the app.
  const [onboarding, setOnboarding] = React.useState(false)
  // One workspace: Chat is the front door, so it is also the landing view.
  const [view, setView] = React.useState<View>('chat')
  const [workflowMode, setWorkflowMode] = React.useState<'graph' | 'scheduled'>('graph')
  const [features, setFeatures] = React.useState<AppFeatures>(DEFAULT_FEATURES)
  React.useEffect(() => { if (view === 'settings') void updates.refresh() }, [view, updates.refresh])
  const [activeTaskId, setActiveTaskId] = React.useState<number | null>(null)
  const [pendingGraphDraft, setPendingGraphDraft] = React.useState<GraphWorkflowDraft | null>(null)
  const [pendingGraphJob, setPendingGraphJob] = React.useState<number | null>(null)
  // The graph editor's stage, lifted so the Workflows tab row can show Back beside
  // the tabs while a workflow is open.
  const [graphStage, setGraphStage] = React.useState<'home' | 'editor'>('home')
  const [graphBackNonce, setGraphBackNonce] = React.useState(0)
  // When a plan is opened from Tasks, Back should return there — not the Recipes
  // home the canvas lives under. Null means the editor was reached from Recipes.
  const [graphCameFrom, setGraphCameFrom] = React.useState<'activity' | null>(null)
  const [pendingDesign, setPendingDesign] = React.useState<{ id: number; title: string } | null>(null)
  const [pendingDesignId, setPendingDesignId] = React.useState<string | null>(null)
  // Bumped by the iterate stage's "Run recipe" button → ChatScreen sends the dry-run.
  const [runRecipeNonce, setRunRecipeNonce] = React.useState(0)
  const [runRecipePrompt, setRunRecipePrompt] = React.useState<string | undefined>(undefined)
  const [runRecipeLabel, setRunRecipeLabel] = React.useState<string | undefined>(undefined)
  const [runRecipeInstantResult, setRunRecipeInstantResult] = React.useState<string | undefined>(undefined)
  // Where Design Studio was deep-opened FROM (panggung / activity), so its back
  // returns there instead of the studio's start screen. Cleared on any sidebar nav.
  const [designCameFrom, setDesignCameFrom] = React.useState<View | null>(null)
  const [pendingFile, setPendingFile] = React.useState<{ slug: string; path: string } | null>(null)
  const [pendingArtifact, setPendingArtifact] = React.useState<OutputLink | null>(null)
  const reviewDraftNonce = React.useRef(0)
  const [reviewDraft, setReviewDraft] = React.useState<{ text: string; nonce: number } | null>(null)
  const clearReviewDraft = React.useCallback(() => setReviewDraft(null), [])
  const [returnToChat, setReturnToChat] = React.useState<ChatSession | null>(null)
  const [returnToTask, setReturnToTask] = React.useState<number | null>(null)
  const clearPendingNavigation = React.useCallback(() => {
    setPendingGraphDraft(null)
    setPendingGraphJob(null)
    setPendingDesign(null)
    setPendingDesignId(null)
    setPendingFile(null)
    setPendingArtifact(null)
    setReturnToChat(null)
    setReturnToTask(null)
    setDesignCameFrom(null)
    setGraphCameFrom(null)
  }, [])
  const clearTaskHash = React.useCallback(() => {
    if (window.location.hash.startsWith('#task/')) window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}`)
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
    } else {
      window.history.replaceState({ ...window.history.state, proximaView: view }, '', window.location.href)
      window.history.pushState({ ...window.history.state, proximaView: 'artifacts' }, '', hash)
    }
    setView('artifacts')
  }, [view])
  const closeArchiveRecord = React.useCallback(() => { clearArchiveHash(); setArchiveRecord(null); setView('artifacts') }, [clearArchiveHash])
  const openTask = React.useCallback((jobId: number) => {
    clearPendingNavigation()
    setActiveTaskId(jobId)
    window.history.replaceState({ ...window.history.state, proximaView: view }, '', window.location.href)
    window.history.pushState({ ...window.history.state, proximaView: 'task' }, '', `#task/${jobId}`)
    setView('task')
  }, [clearPendingNavigation, view])
  const closeTask = React.useCallback(() => { clearTaskHash(); setView('activity') }, [clearTaskHash])
  // A review lands where it can be acted on: a graph job's review gates live on the
  // canvas, so sending it to the linear TaskWorkspace would show a task that view has
  // no way to approve — a dangling "needs review" the owner cannot resolve.
  const openJobByEngine = React.useCallback((jobId: number, engine?: string, origin?: 'activity') => {
    if (engine === 'graph') {
      clearTaskHash()
      clearPendingNavigation()
      setPendingGraphJob(jobId)
      setWorkflowMode('graph')
      setGraphCameFrom(origin === 'activity' ? 'activity' : null)
      setView('workflows')
      return
    }
    openTask(jobId)
  }, [clearTaskHash, clearPendingNavigation, openTask])
  const openAttentionTarget = React.useCallback((target: { view?: string; job_id?: number; engine?: string }) => {
    if (target.job_id != null) {
      openJobByEngine(target.job_id, target.engine)
      return
    }
    if (target.view === 'alpha') { clearPendingNavigation(); setView('alpha'); return }
    if (target.view === 'settings') { clearPendingNavigation(); setView('settings'); return }
    if (target.view === 'activity') { clearPendingNavigation(); setView('activity') }
  }, [clearPendingNavigation, openJobByEngine])
  const viewEnabled = React.useCallback((v: View) => isFeatureViewEnabled(v, features), [features])
  const goView = (v: View) => {
    clearTaskHash()
    clearArchiveHash()
    setArchiveRecord(null)
    clearPendingNavigation()
    if (v === 'workflows') {
      setWorkflowMode('graph')
      // Sidebar Recipes means the Recipes home. Re-clicking while a plan is open
      // (including one reached from Tasks) used to no-op on the canvas — bump the
      // same back signal the in-editor Back control uses so the list returns.
      if (view === 'workflows' && graphStage === 'editor') {
        setGraphCameFrom(null)
        setGraphBackNonce(n => n + 1)
      }
    }
    // Chat in the nav means the conversation front door — never a recipe's
    // iteration thread, which belongs to Recipes.
    if (v === 'chat' && activeSession?.workflow_id) {
      setActiveSession(sessions.find(session => !session.workflow_id && !session.job_id && session.mode !== 'design') || null)
    }
    setView(viewEnabled(v) ? v : 'chat')
  }
  // Unread/activity dots: a session is "unread" when its updated_at is newer
  // than the last time you opened it. Persisted so it survives reloads.
  const [seen, setSeen] = React.useState<Record<number, string>>(() => { try { return JSON.parse(localStorage.getItem('proxima.seen') || '{}') } catch { return {} } })
  const baselined = React.useRef(false)
  const markSeen = React.useCallback((id: number, updated?: string) => {
    setSeen(prev => { const u = updated || prev[id] || ''; if (prev[id] === u) return prev; const n = { ...prev, [id]: u }; localStorage.setItem('proxima.seen', JSON.stringify(n)); return n })
  }, [])
  const [profiles, setProfiles] = React.useState<Profile[]>([])
  const [projects, setProjects] = React.useState<Project[]>([])
  const [sessions, setSessions] = React.useState<ChatSession[]>([])
  const [runners, setRunners] = React.useState<Runner[]>([])
  const [runnerReadiness, setRunnerReadiness] = React.useState<RunnerReadinessMap>({})
  const [activeProfile, setActiveProfile] = React.useState<Profile | null>(null)
  const [activeProject, setActiveProject] = React.useState<Project | null>(null)
  const [activeSession, setActiveSession] = React.useState<ChatSession | null>(null)
  const [error, setError] = React.useState('')
  React.useEffect(() => {
    if (booting || !user) return
    const syncHashRoute = (event?: Event) => {
      const match = window.location.hash.match(/^#task\/(\d+)$/)
      if (match) { setActiveTaskId(Number(match[1])); setView('task'); return }
      const archiveMatch = window.location.hash.match(/^#archive\/([^/]+)\/([^/]+)$/)
      if (archiveMatch) {
        setArchiveRecord({ project: decodeURIComponent(archiveMatch[1]), slug: decodeURIComponent(archiveMatch[2]) })
        setView('artifacts')
        return
      }
      const priorView = event instanceof PopStateEvent && typeof event.state?.proximaView === 'string' ? event.state.proximaView as View : null
      setArchiveRecord(null)
      setView(current => current === 'task' ? priorView || 'activity' : current)
    }
    syncHashRoute()
    window.addEventListener('hashchange', syncHashRoute)
    window.addEventListener('popstate', syncHashRoute)
    return () => { window.removeEventListener('hashchange', syncHashRoute); window.removeEventListener('popstate', syncHashRoute) }
  }, [booting, user?.id])
  const sessionEnabled = React.useCallback((session: ChatSession) => isFeatureSessionEnabled(session, features), [features])
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

  const refreshAll = React.useCallback(async (authToken = token) => {
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
    // Couple the open chat and shell project in one pass. Picking them independently
    // left Files/header/@-mentions on project A while the conversation (and Save to
    // wiki) used project B after boot.
    let nextSession: ChatSession | null | undefined
    if (sessionSeq === sessionsSeq.current) {
      setActiveSession(current => {
        nextSession = current && sessionBody.sessions.some(s => s.id === current.id && sessionEnabled(s))
          ? current
          : sessionBody.sessions.find(sessionEnabled) || null
        return nextSession
      })
    }
    setActiveProject(current => {
      if (nextSession?.project_slug) {
        const fromSession = projectBody.projects.find(p => p.slug === nextSession!.project_slug)
        if (fromSession) return fromSession
      }
      // No open chat (or chat has no project): keep the current pick if still valid,
      // else the owner's personal/private project - never a "No project" limbo.
      if (current && projectBody.projects.some(p => p.slug === current.slug)) return current
      return projectBody.projects.find(p => p.visibility === 'private') || projectBody.projects[0] || null
    })
  }, [token, sessionEnabled])

  // When a session opens/changes, pull the shell project to match so Files and
  // other rails start on the conversation's project. Do NOT depend on
  // activeProject here - an intentional Projects/Tasks pick must stick even
  // while an older chat session remains selected in memory (Chat header already
  // prefers the session project over a desynced shell pick).
  React.useEffect(() => {
    if (!activeSession?.project_slug) return
    setActiveProject(current => {
      if (current?.slug === activeSession.project_slug) return current
      return projects.find(p => p.slug === activeSession.project_slug) || current
    })
  }, [activeSession?.id, activeSession?.project_slug, projects])

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
      const configPromise = getAppFeatures()
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
            await refreshAll(s.token)
          } catch {
            if (mountedRef.current) setAuthGate('login')
          }
        }
      } catch (err) {
        if (mountedRef.current) setError(String(err))
      } finally {
        const nextFeatures = await configPromise
        if (mountedRef.current) {
          setFeatures(nextFeatures)
          setBooting(false)
        }
      }
    }
    void boot()
  // Run once on mount; refreshAll closes over the latest token via its own deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    if (!viewEnabled(view)) setView('chat')
  }, [view, viewEnabled])

  // A new chat is just a blank composer — no DB session yet. The session is created
  // lazily on the first message (ChatScreen.ensureSession), so empty chats never
  // clutter the sidebar; a thread appears there only once it has a real conversation.
  async function startNewSession() {
    clearPendingNavigation()
    setActiveSession(null)
    setView('chat')
  }

  const createTask = (request: OpsTaskRequest) => createAndStartOpsTask(token, request)

  // Switching project opens that project's most recent chat (not a task thread),
  // or a blank new chat if it has none — so the chat view always reflects the
  // chosen project instead of leaving you on an unrelated conversation.
  function selectProject(p: Project | null) {
    clearPendingNavigation()
    setActiveProject(p)
    if (!p) { setActiveSession(null); return }
    const recent = sessions
      .filter(s => s.project_slug === p.slug && sessionEnabled(s))
      .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0] || null
    setActiveSession(recent)
    setView('chat')
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
    const targetSlug = link.project_slug || origin?.project_slug || activeProject?.slug || null
    const targetProject = targetSlug ? projects.find(p => p.slug === targetSlug) : null
    if (targetProject) setActiveProject(targetProject)
    if (origin) setReturnToChat(origin)
    if (link.type === 'design' && features.designStudio) {
      setPendingDesign(null)
      setPendingDesignId(link.id || link.path.split('/').filter(Boolean).slice(-1)[0] || null)
      setDesignCameFrom('chat')
      setView('design')
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
    clearTaskHash()
    clearArchiveHash()
    setArchiveRecord(null)
    clearPendingNavigation()
    setActiveSession(session)
    const sp = projects.find(p => p.slug === session.project_slug)
    if (sp) setActiveProject(sp)
    markSeen(session.id, session.updated_at)
    setView('chat')
  }

  function backToOriginChat() {
    const origin = returnToChat
    setReturnToChat(null)
    setDesignCameFrom(null)
    if (origin) {
      setActiveSession(origin)
      const p = projects.find(x => x.slug === origin.project_slug)
      if (p) setActiveProject(p)
      markSeen(origin.id, origin.updated_at)
    }
    setView('chat')
  }

  async function continueArtifactReview(feedback: ArtifactReviewFeedback) {
    const seq = ++reviewHandoffSeq.current
    const target = await resolveArtifactReviewSession({
      sessions,
      sessionId: feedback.sessionId,
      fallback: returnToChat || activeSession,
      loadSession: sessionId => getSession(token, sessionId),
    })
    if (!mountedRef.current || seq !== reviewHandoffSeq.current) return
    if (!target) {
      setError('This artifact has no chat session to receive feedback. Open it from its producing chat and try again.')
      return
    }
    clearTaskHash()
    clearArchiveHash()
    setArchiveRecord(null)
    clearPendingNavigation()
    setActiveSession(target)
    const project = projects.find(candidate => candidate.slug === target.project_slug)
    if (project) setActiveProject(project)
    markSeen(target.id, target.updated_at)
    reviewDraftNonce.current += 1
    setReviewDraft({ text: feedback.text, nonce: reviewDraftNonce.current })
    setView('chat')
  }

  const handleAuthed = (s: { token: string; user: User }) => {
    // Keep the token in memory for this session's bearer header; the cookie carries
    // it across reloads. Nothing goes to localStorage.
    // authGate still holds its pre-auth value here — 'setup' means this is the very
    // first run, so show the "pick a working folder" step before the app.
    const firstRun = authGate === 'setup'
    setToken(s.token); setUser(s.user); setAuthGate(null)
    if (firstRun) setOnboarding(true)
    void refreshAll(s.token)
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
    try { await logout(token) } catch { /* best-effort; cookie is cleared server-side */ }
    setToken(''); setUser(null); setAuthGate('login')
  }

  if (booting) return <div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>Starting Proxima…</p></div>
  if (authGate) return <AuthGate mode={authGate} onAuthed={handleAuthed} />
  if (!token || !user) return <div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>{error || 'Connecting…'}</p></div>
  if (onboarding) return <React.Suspense fallback={<div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>Loading…</p></div>}><WorkspaceOnboarding token={token} onDone={linked => void handleOnboardingDone(linked)} /></React.Suspense>

  return (
    <AppShell
      activeProfile={activeProfile}
      activeProject={activeProject}
      activeSession={activeSession}
      currentView={view}
      onLogout={() => void handleLogout()}
      features={features}
      onNewChat={() => void startNewSession()}
      onRenameSession={(id, title) => void handleRenameSession(id, title)}
      onDeleteSession={id => void handleDeleteSession(id)}
      onSelectProject={selectProject}
      onSelectSession={session => { clearPendingNavigation(); setActiveSession(session); const sp = projects.find(p => p.slug === session.project_slug); if (sp) setActiveProject(sp); markSeen(session.id, session.updated_at); setView('chat') }}
      onOpenDesign={session => { if (!features.designStudio) return; const sp = projects.find(p => p.slug === session.project_slug); if (sp) setActiveProject(sp); markSeen(session.id, session.updated_at); setPendingDesign({ id: session.id, title: session.title }); setView('design') }}
      seen={seen}
      busySessions={busySessions}
      onSelectView={goView}
      onOpenAttentionTarget={openAttentionTarget}
      profiles={profiles}
      projects={projects}
      sessions={sessions}
      token={token}
      user={user}
      updateVersion={updates.status?.update_available ? updates.status.latest?.version ?? null : null}
      onUpdateClick={updates.openModal}
    >
      {error && <div className="error-bar">{error}</div>}
      <HermesBanner token={token} runnerId={activeProfile?.runner_id} />
      {view === 'home' && <HomeScreen token={token} ownerName={user?.username} features={features} projects={projects} activeProject={activeProject} activeProfile={activeProfile} profiles={profiles} runnerReadiness={runnerReadiness}
        onActiveProject={setActiveProject} onActiveProfile={setActiveProfile} onCreateTask={createTask} onOpenJob={openJobByEngine} onSelectView={goView} />}
      {view === 'alpha' && <React.Suspense fallback={<ViewFallback label="Loading Alpha desk..." />}><AlphaScreen token={token} runners={runners} onOpenJob={openJobByEngine} /></React.Suspense>}
      {view === 'chat' && (() => {
        // A design-kind session belongs to Design Studio; never render it as the main
        // chat (kind is authoritative — this is the last-line guard behind the session
        // list + Home routing already excluding design sessions).
        const mainSession = activeSession?.mode === 'design' ? null : activeSession
        const chat = <ChatScreen activeProfile={activeProfile} activeProject={activeProject} activeSession={mainSession} profiles={profiles} projects={projects} runnerReadiness={runnerReadiness} token={token} features={features} onActiveProfile={setActiveProfile} onActiveProject={selectProject} onSession={setActiveSession} onRefresh={refreshAll} onNewSession={startNewSession} onGraphDraft={draft => { setPendingGraphDraft(draft); setWorkflowMode('graph'); setView('workflows') }} onOpenOutput={openOutput} runRecipeNonce={runRecipeNonce} runRecipePrompt={runRecipePrompt} runRecipeLabel={runRecipeLabel} runRecipeInstantResult={runRecipeInstantResult} draftSeed={reviewDraft?.text} draftSeedNonce={reviewDraft?.nonce} onDraftSeedConsumed={clearReviewDraft} />
        // Workflow iterate/test chat gets a split layout: chat left, live result stage right.
        return activeSession?.workflow_id
          ? <div className="iterate-split">{chat}<React.Suspense fallback={<ViewFallback label="Loading workflow stage..." />}><IterateStage token={token} workflowId={activeSession.workflow_id} sessionId={activeSession.id} projectSlug={activeSession.project_slug || activeProject?.slug || null} running={busySessions.includes(activeSession.id)} designStudioEnabled={features.designStudio} onOpenDesign={features.designStudio ? id => { setPendingDesignId(id); setDesignCameFrom('chat'); setView('design') } : undefined} onRunRecipe={(prompt, label, instantResult) => { setRunRecipePrompt(prompt); setRunRecipeLabel(label); setRunRecipeInstantResult(instantResult); setRunRecipeNonce(n => n + 1) }} /></React.Suspense></div>
          : chat
      })()}
      {view === 'projects' && <React.Suspense fallback={<ViewFallback label="Loading projects..." />}><ProjectsScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'wiki' && <React.Suspense fallback={<ViewFallback label="Loading wiki..." />}><WikiScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} /></React.Suspense>}
      {view === 'artifacts' && <React.Suspense fallback={<ViewFallback label="Loading archive..." />}><ArtifactsScreen token={token} projects={projects} activeProject={activeProject} archiveRecord={archiveRecord} pendingFile={pendingFile} pendingArtifact={pendingArtifact} onPendingConsumed={() => setPendingFile(null)} onPendingArtifactConsumed={() => setPendingArtifact(null)} onActiveProject={setActiveProject} onOpenRecord={openArchiveRecord} onCloseRecord={closeArchiveRecord} onOpenTask={openJobByEngine} onOpenSession={openSessionById} onBack={returnToTask != null ? () => openTask(returnToTask) : returnToChat ? backToOriginChat : undefined} backLabel={returnToTask != null ? 'Task' : 'Chat'} designStudioEnabled={features.designStudio} onOpenDesign={features.designStudio ? id => { setPendingDesignId(id); setDesignCameFrom(returnToTask != null ? 'task' : returnToChat ? 'chat' : 'artifacts'); setView('design') } : undefined} reviewSessionId={returnToChat?.id ?? activeSession?.id ?? null} onSendFeedback={continueArtifactReview} /></React.Suspense>}
      {view === 'workflows' && <React.Suspense fallback={<ViewFallback label="Loading workflows..." />}><WorkflowsScreen mode={workflowMode} onModeChange={setWorkflowMode} token={token} onOpenJob={openJobByEngine} graphContent={features.workflowGraph ? <GraphScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} profiles={profiles} profileId={activeProfile?.id ?? null} features={features} activeProfile={activeProfile} pendingDraft={pendingGraphDraft} onDraftConsumed={() => setPendingGraphDraft(null)} pendingJobId={pendingGraphJob} onPendingConsumed={() => setPendingGraphJob(null)} onStageChange={setGraphStage} backNonce={graphBackNonce} /> : undefined} graphEditorActive={graphStage === 'editor'} graphBackLabel={graphCameFrom === 'activity' ? 'Tasks' : 'Recipes'} onGraphBack={() => {
        if (graphCameFrom === 'activity') {
          setGraphCameFrom(null)
          setView('activity')
          return
        }
        setGraphBackNonce(n => n + 1)
      }} /></React.Suspense>}
      {view === 'activity' && <React.Suspense fallback={<ViewFallback label="Loading tasks..." />}><ActivityScreen token={token} activeProject={activeProject} features={features} profiles={profiles} onOpenTask={openTask} onOpenPlan={jobId => openJobByEngine(jobId, 'graph', 'activity')} onNewTask={() => goView('home')} /></React.Suspense>}
      {view === 'task' && activeTaskId != null && <React.Suspense fallback={<ViewFallback label="Loading task..." />}><section className="tasks-view task-workspace-view"><TaskWorkspace token={token} jobId={activeTaskId} onBack={closeTask} designStudioEnabled={features.designStudio} onOpenDesign={features.designStudio ? id => { clearTaskHash(); setPendingDesignId(id); setDesignCameFrom('task'); setView('design') } : undefined} onOpenFile={(slug, path) => { clearTaskHash(); setReturnToTask(activeTaskId); setPendingFile({ slug, path }); setView('artifacts') }} /></section></React.Suspense>}
      {features.workflowGraph && view === 'graph' && <React.Suspense fallback={<ViewFallback label="Loading workflow graph..." />}><GraphScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} profiles={profiles} profileId={activeProfile?.id ?? null} features={features} activeProfile={activeProfile} pendingDraft={pendingGraphDraft} onDraftConsumed={() => setPendingGraphDraft(null)} pendingJobId={pendingGraphJob} onPendingConsumed={() => setPendingGraphJob(null)} /></React.Suspense>}
      {features.designStudio && view === 'design' &&<React.Suspense fallback={<div className="ds-loading muted">Loading Design Studio...</div>}><DesignStudio token={token} project={activeProject} profileId={activeProfile?.id ?? null} openSession={pendingDesign} openDesignId={pendingDesignId} onOpened={() => { setPendingDesign(null); setPendingDesignId(null) }} onExit={designCameFrom === 'chat' && returnToChat ? backToOriginChat : designCameFrom ? () => { const v = designCameFrom; setDesignCameFrom(null); if (v === 'task' && activeTaskId != null) window.history.replaceState(window.history.state, '', `#task/${activeTaskId}`); setView(v) } : undefined} /></React.Suspense>}
      {view === 'profiles' && <React.Suspense fallback={<ViewFallback label="Loading agents..." />}><ProfilesScreen token={token} profiles={profiles} onActiveProfile={setActiveProfile} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'runners' && <React.Suspense fallback={<ViewFallback label="Loading..." />}><RunnersScreen runners={runners} runnerReadiness={runnerReadiness} token={token} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'settings' && <React.Suspense fallback={<ViewFallback label="Loading settings..." />}><SettingsScreen token={token} user={user} profiles={profiles} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} runners={runners} runnerReadiness={runnerReadiness} features={features} onRefresh={refreshAll} onTokenChange={setToken} updateStatus={updates.status} updateChecking={updates.checking} onCheckUpdates={updates.check} onOpenUpdate={updates.openModal} /></React.Suspense>}
      {updates.modalOpen && updates.status?.latest && <UpdateModal status={updates.status} onApply={updates.apply} onClose={updates.closeModal} />}
      {updates.applying && <UpdateOverlay applying={updates.applying} onDismiss={updates.dismissApplying} />}
      <DialogHost />
    </AppShell>
  )
}

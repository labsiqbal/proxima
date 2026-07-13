import React from 'react'
import { me, setupStatus, logout } from './api/auth'
import { listProfiles } from './api/profiles'
import { listProjects } from './api/projects'
import { listSessions, renameSession, deleteSession } from './api/sessions'
import { activeRuns } from './api/runs'
import { api } from './api/client'
import { getAppFeatures } from './api/config'
import { DEFAULT_FEATURES, isDisabledFeatureHash, isFeatureSessionEnabled, isFeatureViewEnabled } from './features'
import type { AppFeatures, ChatSession, OutputLink, Profile, Project, Runner, User, View, WorkflowDraft } from './types'
import { AppShell } from './components/shell/AppShell'
import { AuthGate } from './screens/AuthGate'
import { HermesBanner } from './components/shell/HermesBanner'
import { ChatScreen } from './screens/ChatScreen'
import { HomeScreen } from './screens/HomeScreen'
import { DialogHost } from './components/ui/Dialog'
import { useUpdateStatus } from './hooks/useUpdateStatus'
import { UpdateModal, UpdateOverlay } from './components/shell/UpdateModal'
import { ProximaMark } from './components/brand/ProximaMark'
const IterateStage = React.lazy(() => import('./screens/IterateStage').then(m => ({ default: m.IterateStage })))
const DesignStudio = React.lazy(() => import('./screens/DesignStudio').then(m => ({ default: m.DesignStudio })))
const ProjectsScreen = React.lazy(() => import('./screens/ProjectsScreen').then(m => ({ default: m.ProjectsScreen })))
const WikiScreen = React.lazy(() => import('./screens/WikiScreen').then(m => ({ default: m.WikiScreen })))
const ArtifactsScreen = React.lazy(() => import('./screens/ArtifactsScreen').then(m => ({ default: m.ArtifactsScreen })))
const WorkflowsScreen = React.lazy(() => import('./screens/WorkflowsScreen').then(m => ({ default: m.WorkflowsScreen })))
const ActivityScreen = React.lazy(() => import('./screens/ActivityScreen').then(m => ({ default: m.ActivityScreen })))
const TerminalTabs = React.lazy(() => import('./components/terminal/TerminalTabs').then(m => ({ default: m.TerminalTabs })))
const ProfilesScreen = React.lazy(() => import('./screens/ProfilesScreen').then(m => ({ default: m.ProfilesScreen })))
const RunnersScreen = React.lazy(() => import('./screens/RunnersScreen').then(m => ({ default: m.RunnersScreen })))
const SettingsScreen = React.lazy(() => import('./screens/SettingsScreen').then(m => ({ default: m.SettingsScreen })))
const VideoScreen = React.lazy(() => import('./screens/VideoScreen').then(m => ({ default: m.VideoScreen })))

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
  const [view, setView] = React.useState<View>('home')
  const [features, setFeatures] = React.useState<AppFeatures>(DEFAULT_FEATURES)
  React.useEffect(() => { if (view === 'settings') void updates.refresh() }, [view, updates.refresh])
  const [pendingJob, setPendingJob] = React.useState<number | null>(null)
  const [pendingDraft, setPendingDraft] = React.useState<WorkflowDraft | null>(null)
  const [pendingDesign, setPendingDesign] = React.useState<{ id: number; title: string } | null>(null)
  const [pendingDesignId, setPendingDesignId] = React.useState<string | null>(null)
  const [pendingVideoId, setPendingVideoId] = React.useState<string | null>(null)
  // Latch: mount the terminal lazily on first visit, then keep it mounted (hidden
  // when away) so live shells survive view changes. Avoids an eager PTY on load.
  const terminalMounted = React.useRef(false)
  if (view === 'terminal') terminalMounted.current = true
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
  const [returnToChat, setReturnToChat] = React.useState<ChatSession | null>(null)
  const [videoCameFrom, setVideoCameFrom] = React.useState<View | null>(null)
  const clearPendingNavigation = React.useCallback(() => {
    setPendingJob(null)
    setPendingDraft(null)
    setPendingDesign(null)
    setPendingDesignId(null)
    setPendingVideoId(null)
    setPendingFile(null)
    setPendingArtifact(null)
    setReturnToChat(null)
    setDesignCameFrom(null)
    setVideoCameFrom(null)
  }, [])
  const viewEnabled = React.useCallback((v: View) => isFeatureViewEnabled(v, features), [features])
  const goView = (v: View) => { clearPendingNavigation(); setView(viewEnabled(v) ? v : 'home') }
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
  const [activeProfile, setActiveProfile] = React.useState<Profile | null>(null)
  const [activeProject, setActiveProject] = React.useState<Project | null>(null)
  const [activeSession, setActiveSession] = React.useState<ChatSession | null>(null)
  const [error, setError] = React.useState('')
  const sessionEnabled = React.useCallback((session: ChatSession) => isFeatureSessionEnabled(session, features), [features])
  const refreshSeq = React.useRef(0)
  const sessionsSeq = React.useRef(0)
  const activeRunsSeq = React.useRef(0)
  const appActionSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      refreshSeq.current += 1
      sessionsSeq.current += 1
      activeRunsSeq.current += 1
      appActionSeq.current += 1
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
      api<{ runners: Runner[] }>('/api/runners/detect', authToken)
    ])
    if (!mountedRef.current || seq !== refreshSeq.current) return
    setProfiles(profileBody.profiles)
    setProjects(projectBody.projects)
    if (sessionSeq === sessionsSeq.current) setSessions(sessionBody.sessions)
    setRunners(runnerBody.runners)
    setActiveProfile(current => current && profileBody.profiles.some(p => p.id === current.id) ? current : profileBody.profiles.find(p => p.is_default) || profileBody.profiles[0] || null)
    // Every chat lives in a project. Default to the user's personal project (their
    // private one), not a "No project" limbo. Keep the current pick if still valid.
    setActiveProject(current => current && projectBody.projects.some(p => p.slug === current.slug)
      ? current
      : projectBody.projects.find(p => p.visibility === 'private') || projectBody.projects[0] || null)
    if (sessionSeq === sessionsSeq.current) setActiveSession(current => current && sessionBody.sessions.some(s => s.id === current.id && sessionEnabled(s)) ? current : sessionBody.sessions.find(sessionEnabled) || null)
  }, [token, sessionEnabled])

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
  const prevBusyKey = React.useRef('')
  React.useEffect(() => {
    if (!token) { setBusySessions([]); return }
    let on = true
    const tick = () => {
      const seq = ++activeRunsSeq.current
      void activeRuns(token).then(r => {
        if (!on || !mountedRef.current || seq !== activeRunsSeq.current) return
        setBusySessions(r.session_ids)
        // When the busy set changes (a run started or finished), refresh the session
        // list so updated_at is fresh — that lights the unread dot for a chat whose
        // agent replied while you were elsewhere. The dot persists until you open it.
        const key = r.session_ids.slice().sort((a, b) => a - b).join(',')
        if (key !== prevBusyKey.current) {
          prevBusyKey.current = key
          const sessionSeq = ++sessionsSeq.current
          void listSessions(token).then(s => { if (on && mountedRef.current && sessionSeq === sessionsSeq.current) setSessions(s.sessions) }).catch(() => {})
        }
      }).catch(() => {})
    }
    tick()
    const t = window.setInterval(tick, 2500)
    return () => {
      on = false
      activeRunsSeq.current += 1
      sessionsSeq.current += 1
      clearInterval(t)
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
          // Auth persists in the HttpOnly cookie, not JS storage. Ask /api/me, which
          // the cookie authenticates (no token needed); 401 → show the login screen.
          try {
            const current = await me('')
            if (!mountedRef.current) return
            setUser(current)
            await refreshAll('')
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
    if (!viewEnabled(view)) setView('home')
  }, [view, viewEnabled])

  React.useEffect(() => {
    if (booting || !isDisabledFeatureHash(window.location.hash, features)) return
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}`)
  }, [booting, features])

  // A new chat is just a blank composer — no DB session yet. The session is created
  // lazily on the first message (ChatScreen.ensureSession), so empty chats never
  // clutter the sidebar; a thread appears there only once it has a real conversation.
  async function startNewSession() {
    clearPendingNavigation()
    setActiveSession(null)
    setView('chat')
  }

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
    if (link.type === 'video' && features.video) {
      setPendingVideoId(link.id || link.path.split('/').filter(Boolean).slice(-1)[0] || null)
      setVideoCameFrom('chat')
      setView('video')
      return
    }
    if (targetSlug && link.path) {
      setPendingArtifact({ ...link, project_slug: targetSlug })
      setView('artifacts')
    }
  }

  function backToOriginChat() {
    const origin = returnToChat
    setReturnToChat(null)
    setDesignCameFrom(null)
    setVideoCameFrom(null)
    if (origin) {
      setActiveSession(origin)
      const p = projects.find(x => x.slug === origin.project_slug)
      if (p) setActiveProject(p)
      markSeen(origin.id, origin.updated_at)
    }
    setView('chat')
  }

  const handleAuthed = (s: { token: string; user: User }) => {
    // Keep the token in memory for this session's bearer header; the cookie carries
    // it across reloads. Nothing goes to localStorage.
    setToken(s.token); setUser(s.user); setAuthGate(null)
    void refreshAll(s.token)
  }
  const handleLogout = async () => {
    try { await logout(token) } catch { /* best-effort; cookie is cleared server-side */ }
    setToken(''); setUser(null); setAuthGate('login')
  }

  if (booting) return <div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>Starting Proxima…</p></div>
  if (authGate) return <AuthGate mode={authGate} onAuthed={handleAuthed} />
  if (!token || !user) return <div className="center-screen"><ProximaMark className="proxima-mark-boot" label="Proxima" /><p>{error || 'Connecting…'}</p></div>

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
      onOpenFile={(slug, path) => { setPendingFile({ slug, path }); setView('artifacts') }}
      onSelectView={goView}
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
      {view === 'home' && <HomeScreen token={token} ownerName={user?.username} features={features}
        onNewChat={() => void startNewSession()}
        onOpenChat={id => { const s = sessions.find(x => x.id === id); if (s) { setActiveSession(s); const sp = projects.find(p => p.slug === s.project_slug); if (sp) setActiveProject(sp); markSeen(s.id, s.updated_at) } setView('chat') }}
        onOpenDesign={session => { if (!features.designStudio) return; const sp = projects.find(p => p.slug === session.project_slug); if (sp) setActiveProject(sp); markSeen(session.id); setPendingDesign({ id: session.id, title: session.title }); setDesignCameFrom('home'); setView('design') }}
        onOpenJob={jobId => { setPendingJob(jobId); setView('activity') }}
        onOpenArtifact={artifact => { const p = projects.find(x => x.slug === artifact.project_slug); if (p) setActiveProject(p); setPendingArtifact(artifact); setView('artifacts') }}
        onOpenProject={slug => { const p = projects.find(x => x.slug === slug); if (p) selectProject(p) }}
        onSelectView={goView} />}
      {view === 'chat' && (() => {
        // A design-kind session belongs to Design Studio; never render it as the main
        // chat (kind is authoritative — this is the last-line guard behind the session
        // list + Home routing already excluding design sessions).
        const mainSession = activeSession?.mode === 'design' ? null : activeSession
        const chat = <ChatScreen activeProfile={activeProfile} activeProject={activeProject} activeSession={mainSession} profiles={profiles} projects={projects} token={token} features={features} onActiveProfile={setActiveProfile} onActiveProject={selectProject} onSession={setActiveSession} onRefresh={refreshAll} onNewSession={startNewSession} onWorkflowDraft={draft => { setPendingDraft(draft); setView('workflows') }} onOpenOutput={openOutput} runRecipeNonce={runRecipeNonce} runRecipePrompt={runRecipePrompt} runRecipeLabel={runRecipeLabel} runRecipeInstantResult={runRecipeInstantResult} />
        // Workflow iterate/test chat gets a split layout: chat left, live result stage right.
        return activeSession?.workflow_id
          ? <div className="iterate-split">{chat}<React.Suspense fallback={<ViewFallback label="Loading workflow stage..." />}><IterateStage token={token} workflowId={activeSession.workflow_id} sessionId={activeSession.id} projectSlug={activeSession.project_slug || activeProject?.slug || null} running={busySessions.includes(activeSession.id)} designStudioEnabled={features.designStudio} onOpenDesign={features.designStudio ? id => { setPendingDesignId(id); setDesignCameFrom('chat'); setView('design') } : undefined} onRunRecipe={(prompt, label, instantResult) => { setRunRecipePrompt(prompt); setRunRecipeLabel(label); setRunRecipeInstantResult(instantResult); setRunRecipeNonce(n => n + 1) }} /></React.Suspense></div>
          : chat
      })()}
      {view === 'projects' && <React.Suspense fallback={<ViewFallback label="Loading projects..." />}><ProjectsScreen token={token} projects={projects} onActiveProject={setActiveProject} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'wiki' && <React.Suspense fallback={<ViewFallback label="Loading wiki..." />}><WikiScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} /></React.Suspense>}
      {view === 'artifacts' && <React.Suspense fallback={<ViewFallback label="Loading artifacts..." />}><ArtifactsScreen token={token} projects={projects} activeProject={activeProject} pendingFile={pendingFile} pendingArtifact={pendingArtifact} onPendingConsumed={() => setPendingFile(null)} onPendingArtifactConsumed={() => setPendingArtifact(null)} onActiveProject={setActiveProject} onBackToChat={returnToChat ? backToOriginChat : undefined} designStudioEnabled={features.designStudio} onOpenDesign={features.designStudio ? id => { setPendingDesignId(id); setDesignCameFrom(returnToChat ? 'chat' : 'artifacts'); setView('design') } : undefined} /></React.Suspense>}
      {view === 'workflows' && <React.Suspense fallback={<ViewFallback label="Loading workflows..." />}><WorkflowsScreen token={token} projects={projects} activeProject={activeProject} onActiveProject={setActiveProject} onOpenJob={jobId => { setPendingJob(jobId); setView('activity') }} onIterate={s => { setActiveSession(s); setView('chat') }} draft={pendingDraft} onDraftConsumed={() => setPendingDraft(null)} /></React.Suspense>}
      {view === 'activity' && <React.Suspense fallback={<ViewFallback label="Loading activity..." />}><ActivityScreen token={token} activeProject={activeProject} pendingJobId={pendingJob} onPendingConsumed={() => setPendingJob(null)} designStudioEnabled={features.designStudio} onOpenDesign={features.designStudio ? id => { setPendingDesignId(id); setDesignCameFrom('activity'); setView('design') } : undefined} onOpenFile={(slug, path) => { setPendingFile({ slug, path }); setView('artifacts') }} /></React.Suspense>}
      {/* Always mounted (hidden when inactive) so the PTY shells survive navigating
          to another view and back — unmounting would kill every running session. */}
      {terminalMounted.current && <section className="chat-stage" style={{ display: view === 'terminal' ? 'flex' : 'none' }}><React.Suspense fallback={<ViewFallback label="Loading terminal..." />}><TerminalTabs token={token} projectSlug={activeProject?.slug} /></React.Suspense></section>}
      {features.designStudio && view === 'design' && <React.Suspense fallback={<div className="ds-loading muted">Loading Design Studio...</div>}><DesignStudio token={token} project={activeProject} profileId={activeProfile?.id ?? null} openSession={pendingDesign} openDesignId={pendingDesignId} onOpened={() => { setPendingDesign(null); setPendingDesignId(null) }} onExit={designCameFrom === 'chat' && returnToChat ? backToOriginChat : designCameFrom ? () => { const v = designCameFrom; setDesignCameFrom(null); setView(v) } : undefined} /></React.Suspense>}
      {features.video && view === 'video' && <React.Suspense fallback={<ViewFallback label="Loading Video Studio..." />}><VideoScreen token={token} project={activeProject} profileId={activeProfile?.id ?? null} openVideoId={pendingVideoId} onOpened={() => setPendingVideoId(null)} onExit={videoCameFrom === 'chat' && returnToChat ? backToOriginChat : videoCameFrom ? () => { const v = videoCameFrom; setVideoCameFrom(null); setView(v) } : undefined} onOpenArtifact={path => { if (!activeProject) return; setPendingArtifact({ type: 'video-file', title: path.split('/').pop() || 'Rendered video', path, project_slug: activeProject.slug }); setView('artifacts') }} /></React.Suspense>}
      {view === 'profiles' && <React.Suspense fallback={<ViewFallback label="Loading agents..." />}><ProfilesScreen token={token} profiles={profiles} onActiveProfile={setActiveProfile} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'runners' && <React.Suspense fallback={<ViewFallback label="Loading runners..." />}><RunnersScreen runners={runners} token={token} onRefresh={refreshAll} /></React.Suspense>}
      {view === 'settings' && <React.Suspense fallback={<ViewFallback label="Loading settings..." />}><SettingsScreen token={token} user={user} profiles={profiles} projects={projects} runners={runners} features={features} onRefresh={refreshAll} updateStatus={updates.status} updateChecking={updates.checking} onCheckUpdates={updates.check} onOpenUpdate={updates.openModal} /></React.Suspense>}
      {updates.modalOpen && updates.status?.latest && <UpdateModal status={updates.status} onApply={updates.apply} onClose={updates.closeModal} />}
      {updates.applying && <UpdateOverlay applying={updates.applying} onDismiss={updates.dismissApplying} />}
      <DialogHost />
    </AppShell>
  )
}

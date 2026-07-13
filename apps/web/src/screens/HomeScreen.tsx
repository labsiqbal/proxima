import React from 'react'
import { getDashboard, type Dashboard } from '../api/dashboard'
import type { AppFeatures } from '../types'
import { ProximaMark } from '../components/brand/ProximaMark'
import { isFeatureSessionEnabled } from '../features'

const cleanName = (n: string) => n.replace(/\s*\(private\)\s*$/i, '')
const clock = (s?: string | null): string => {
  if (!s) return '—'
  const d = new Date(s.replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? '' : 'Z'))
  if (isNaN(d.getTime())) return '—'
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return 'now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d`
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
const nextLabel = (iso: string | null, enabled: boolean): string => {
  if (!enabled) return 'PAUSED'
  if (!iso) return '—'
  const d = new Date(iso); const diff = (d.getTime() - Date.now()) / 1000
  if (diff < 90) return 'NOW'
  if (diff < 3600) return `${Math.round(diff / 60)}m`
  if (diff < 86400) return `${Math.round(diff / 3600)}h`
  return d.toLocaleDateString(undefined, { weekday: 'short' }).toUpperCase() + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}


export function HomeScreen({ token, ownerName, features, onOpenChat, onOpenProject, onOpenDesign, onOpenJob, onOpenArtifact, onNewChat, onSelectView }: {
  token: string; ownerName?: string; features: AppFeatures
  onOpenChat: (id: number) => void
  onOpenDesign: (session: { id: number; title: string; project_slug: string | null }) => void
  onOpenJob: (jobId: number) => void; onOpenArtifact: (artifact: { type: string; title: string; path: string; project_slug: string }) => void
  onNewChat: () => void
  onOpenProject: (slug: string) => void; onSelectView: (v: 'projects' | 'workflows' | 'activity' | 'artifacts' | 'settings') => void
}) {
  const [data, setData] = React.useState<Dashboard | null>(null)
  const [error, setError] = React.useState('')
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
    }
  }, [])

  React.useEffect(() => {
    let stop = false
    const load = (silent: boolean) => {
      const seq = ++loadSeq.current
      if (!silent) { setData(null); setError('') }
      getDashboard(token)
        .then(d => { if (!stop && mountedRef.current && seq === loadSeq.current) setData(d) })
        .catch(e => { if (!stop && !silent && mountedRef.current && seq === loadSeq.current) setError(String(e)) })
    }
    load(false)
    // Poll while Home is open so a run finishing elsewhere (e.g. the design chat) flips
    // its row from "running" to "done" on its own — no need to leave and re-open Home.
    const timer = window.setInterval(() => load(true), 5000)
    return () => { stop = true; clearInterval(timer); loadSeq.current += 1 }
  }, [token])

  if (error) return <section className="home-view home-command"><p className="error-text" style={{ padding: 24 }}>{error}</p></section>
  if (!data) return <section className="home-view home-command"><div className="cmd-bg" /><div className="cmd-skeleton" /></section>

  const { counts, recent: allRecent, activeSessions: allActiveSessions = [], projects, workflows, schedules, reviewCount, reviewJobs = [], recentArtifacts = [], systemHealth, pendingApprovals: allPendingApprovals = [] } = data
  const recent = allRecent.filter(session => isFeatureSessionEnabled(session, features))
  const activeSessions = allActiveSessions.filter(session => isFeatureSessionEnabled(session, features))
  const pendingApprovals = allPendingApprovals.filter(session => isFeatureSessionEnabled(session, features))
  const activeRunCount = activeSessions.length
  const activeSchedules = schedules.filter(s => s.enabled)
  const liveSession = activeSessions[0] || null
  const lastWork = recent[0] || null
  const lastProject = projects[0] || null
  const healthTone = !systemHealth ? 'ok' : systemHealth.staleRuns > 0 || systemHealth.failedRuns24h > 0 ? 'warn' : systemHealth.runnersReady === 0 ? 'fail' : 'ok'
  const activeIds = new Set(activeSessions.map(s => s.id))
  const approvalIds = new Set(pendingApprovals.map(s => s.id))
  const statusOf = (r: typeof recent[number]): { label: string; cls: string; title: string; dot?: boolean } => {
    if (approvalIds.has(r.id)) return { label: 'needs you', cls: 'cmd-need-approve', title: 'Waiting for your approval' }
    if (activeIds.has(r.id)) return { label: 'running', cls: 'cmd-st-live', title: 'Working now', dot: true }
    if (r.goal_status === 'blocked') return { label: 'waiting for you', cls: 'cmd-need-approve', title: 'Agent is waiting on your input' }
    if (r.last_run_status === 'failed') return { label: 'failed', cls: 'cmd-st-fail', title: 'Last run failed' }
    if (r.last_run_status === 'cancelled') return { label: 'stopped', cls: '', title: 'Stopped' }
    if (r.last_run_status === 'completed') return { label: 'done', cls: 'cmd-st-done', title: `Done · ${clock(r.updated_at)}` }
    return { label: clock(r.updated_at), cls: 'mono', title: '' }
  }

  // Auth/readiness failures (expired tokens, missing CLIs) for the providers and
  // runners in use — surfaced before work starts. Nothing renders when all is well.
  const connectionChecks = (data.authHealth?.checks ?? []).filter(c => features.video || c.area !== 'video')
  const authIssues = connectionChecks.filter(c => !c.ok)

  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  const dateStr = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })

  return <section className="home-view home-command">
    <div className="cmd-bg" aria-hidden="true" />

    <header className="cmd-head">
      <div className="cmd-brand"><ProximaMark className="proxima-mark-command" /><span className="proxima-word">PROXIMA</span><span className="cmd-brand-sub">· control</span></div>
      <div className="cmd-subhead">{greeting} · {dateStr}</div>
      <div className="cmd-headline">
        <span className="cmd-hello">{ownerName ? `${ownerName.toUpperCase()} //` : '//'}</span>
        {pendingApprovals.length > 0
          ? <button className="cmd-flag" onClick={() => onOpenChat(pendingApprovals[0].id)} title={pendingApprovals[0].title}>{pendingApprovals.length} NEED{pendingApprovals.length > 1 ? '' : 'S'} APPROVAL <span className="cmd-arrow">▸</span></button>
          : reviewCount > 0
          ? <button className="cmd-flag" onClick={() => onSelectView('activity')}>{reviewCount} JOBS AWAIT REVIEW <span className="cmd-arrow">▸</span></button>
          : activeRunCount > 0
            ? liveSession
              ? <button className="cmd-flag" onClick={() => onOpenChat(liveSession.id)} title={liveSession.title}>{activeRunCount} AGENT{activeRunCount > 1 ? 'S' : ''} LIVE <span className="cmd-livedot" /></button>
              : <span className="cmd-flag idle">{activeRunCount} AGENT{activeRunCount > 1 ? 'S' : ''} LIVE <span className="cmd-livedot" /></span>
            : <span className="cmd-flag idle">ALL SYSTEMS CLEAR</span>}
      </div>
    </header>

    <div className="cmd-actions" aria-label="Quick actions">
      <button onClick={onNewChat}>New chat</button>
      <button onClick={() => onSelectView('workflows')}>Run workflow</button>
      <button onClick={() => lastProject ? onOpenProject(lastProject.slug) : onSelectView('projects')}>Open project</button>
      <button onClick={() => reviewJobs[0] ? onOpenJob(reviewJobs[0].id) : onSelectView('activity')}>Review queue</button>
      <button onClick={() => recentArtifacts[0] ? onOpenArtifact(recentArtifacts[0]) : onSelectView('artifacts')}>Recent artifact</button>
      <button onClick={() => lastWork ? (lastWork.mode === 'design' ? onOpenDesign({ id: lastWork.id, title: lastWork.title, project_slug: lastWork.project_slug }) : onOpenChat(lastWork.id)) : onNewChat()}>Continue last run</button>
    </div>

    <div className="cmd-grid">
      {/* Activity log leads — Home is for catching up on work: what needs approval,
          what's running, what's done, and where to continue. */}
      <div className="cmd-panel cmd-log">
        <div className="panel-label">Activity log</div>
        {recent.length === 0
          ? <p className="cmd-empty">Quiet. Start a chat or run a workflow.</p>
          : <ul className="cmd-rows cmd-loglist">{recent.slice(0, 7).map((r, i) => <li key={r.id} className="stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties}>
              <button onClick={() => (r.mode === 'design')
                ? onOpenDesign({ id: r.id, title: r.title, project_slug: r.project_slug })
                : onOpenChat(r.id)}>
                {(() => { const st = statusOf(r); return <span className={`cmd-row-t ${st.cls}`} title={st.title}>{st.dot ? <span className="cmd-livedot" /> : <span className="cmd-st-dot" />}{st.label}</span> })()}
                <span className="cmd-row-main"><span className="cmd-row-title">{r.title}</span></span>
                <span className="cmd-row-sub">{r.project_slug || '—'}{r.mode === 'design' ? ' · design' : r.workflow_id ? ' · workflow' : ''}</span>
              </button></li>)}</ul>}
      </div>

      <div className="cmd-panel cmd-stats">
        <div className="panel-label">System readout <span className={`cmd-health ${healthTone}`}>{healthTone === 'ok' ? 'healthy' : healthTone === 'fail' ? 'runner offline' : 'needs attention'}</span></div>
        <div className="cmd-readouts">
          <button className="cmd-readout-btn" onClick={() => onSelectView('projects')}><span className="cmd-readout"><strong>{counts.projects}</strong><span>projects</span></span></button>
          <button className="cmd-readout-btn" onClick={() => onNewChat()}><span className="cmd-readout"><strong>{counts.chats}</strong><span>chats</span></span></button>
          <button className="cmd-readout-btn" onClick={() => onSelectView('activity')}><span className="cmd-readout"><strong>{reviewCount}</strong><span>reviews</span></span></button>
          <button className="cmd-readout-btn" onClick={() => liveSession ? onOpenChat(liveSession.id) : onSelectView('activity')}><span className={`cmd-readout ${activeRunCount ? 'live' : ''}`}><strong>{activeRunCount}</strong><span>live agents</span></span></button>
        </div>
        <div className="cmd-healthline">
          <span>{systemHealth?.runnersReady ?? 0}/{systemHealth?.runnersTotal ?? 0} runners ready</span>
          <span>{systemHealth?.failedRuns24h ?? 0} failed / 24h</span>
          <span>{systemHealth?.staleRuns ?? 0} stale</span>
        </div>
      </div>

      {/* Auth/readiness of the media providers + runners in use — errors visible
          before work starts, with the fix in the tooltip/detail line. */}
      <div className="cmd-panel cmd-conn">
        <div className="panel-label">Connections <span className={`cmd-health ${authIssues.length ? 'fail' : 'ok'}`}>{data.authHealth?.status === 'checking' ? 'checking…' : authIssues.length ? `${authIssues.length} need${authIssues.length > 1 ? '' : 's'} fixing` : 'all ready'}</span></div>
        {connectionChecks.length === 0
          ? <p className="cmd-empty">Checking providers &amp; runners…</p>
          : <ul className="cmd-conn-list">{connectionChecks.map(c => <li key={c.id} className={c.ok ? 'ok' : 'fail'}>
              <span className="cmd-conn-dot" aria-hidden="true" />
              <span className="cmd-conn-label" title={c.detail}>{c.label}</span>
              {!c.ok && <span className="cmd-conn-detail">{c.detail}</span>}
            </li>)}</ul>}
        {authIssues.length > 0 && <button className="cmd-more cmd-conn-fix" onClick={() => onSelectView('settings')}>fix in settings ▸</button>}
      </div>

      <div className="cmd-panel cmd-review">
        <div className="panel-label">Review inbox <button className="cmd-more" onClick={() => onSelectView('activity')}>all ▸</button></div>
        {reviewJobs.length === 0
          ? <p className="cmd-empty">No jobs waiting for review.</p>
          : <ul className="cmd-rows">{reviewJobs.slice(0, 5).map((j, i) => <li key={j.id} className="stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties}>
            <button onClick={() => onOpenJob(j.id)}>
              <span className="cmd-row-main"><span className="cmd-row-title">{j.title}</span><span className="cmd-row-sub">{j.project_slug || '—'}{j.workflow_name ? ` · ${j.workflow_name}` : ''}</span></span>
              <span className="cmd-row-t cmd-need-approve">review</span>
            </button></li>)}</ul>}
      </div>

      <div className="cmd-panel cmd-artifacts">
        <div className="panel-label">Recent artifacts <button className="cmd-more" onClick={() => onSelectView('artifacts')}>all ▸</button></div>
        {recentArtifacts.length === 0
          ? <p className="cmd-empty">No project outputs yet.</p>
          : <ul className="cmd-rows">{recentArtifacts.slice(0, 6).map((a, i) => <li key={`${a.project_slug}:${a.path}`} className="stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties}>
            <button onClick={() => onOpenArtifact(a)}>
              <span className="cmd-art-type">{a.type}</span>
              <span className="cmd-row-main"><span className="cmd-row-title">{a.title}</span><span className="cmd-row-sub">{a.project_slug} · {a.path}</span></span>
              <span className="cmd-row-t">{clock(a.updated_at)}</span>
            </button></li>)}</ul>}
      </div>


      <div className="cmd-panel cmd-next">
        <div className="panel-label">Up next <button className="cmd-more" onClick={() => onSelectView('workflows')}>schedules ▸</button></div>
        {activeSchedules.length === 0
          ? <p className="cmd-empty">No active schedules.<br />Automate a workflow on a cadence.</p>
          : <ul className="cmd-rows">{activeSchedules.slice(0, 5).map((s, i) => <li key={s.id} className="stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties}>
              <button onClick={() => onSelectView('workflows')}>
                <span className="cmd-dot on" />
                <span className="cmd-row-main"><span className="cmd-row-title">{s.workflow_name}</span><span className="cmd-row-sub">{s.cadence}</span></span>
                <span className="cmd-row-t">{nextLabel(s.next_run, true)}</span>
              </button></li>)}</ul>}
      </div>

      <div className="cmd-panel cmd-flows">
        <div className="panel-label">Workflows <button className="cmd-more" onClick={() => onSelectView('workflows')}>all ▸</button></div>
        {workflows.length === 0
          ? <p className="cmd-empty">No recipes yet.<br />Turn a chat into a workflow.</p>
          : <ul className="cmd-rows">{workflows.slice(0, 5).map((w, i) => <li key={w.id} className="stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties}>
              <button onClick={() => onSelectView('workflows')}>
                <span className="cmd-row-main"><span className="cmd-row-title">{w.name}</span><span className="cmd-row-sub">{w.category && w.category !== 'other' ? `${w.category} · ` : ''}{w.steps} step{w.steps !== 1 ? 's' : ''}</span></span>
                <span className="cmd-row-t chev">▸</span>
              </button></li>)}</ul>}
      </div>

      {projects.length > 0 && <div className="cmd-panel cmd-projects">
        <div className="panel-label">Projects <button className="cmd-more" onClick={() => onSelectView('projects')}>all ▸</button></div>
        <div className="cmd-chips">{projects.slice(0, 8).map(p => <button key={p.slug} className="cmd-chip" onClick={() => onOpenProject(p.slug)}>
          {cleanName(p.name)}<span className="cmd-chip-n">{p.chats + p.tasks}</span>
        </button>)}</div>
      </div>}
    </div>
  </section>
}

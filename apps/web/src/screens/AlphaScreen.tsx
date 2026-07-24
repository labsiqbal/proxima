import React from 'react'
import { getAlphaDesk, previewCheckpointRestore, restoreCheckpoint, saveAlphaSettings, sendAlphaMessage, setCheckpointPinned, type AlphaCheckpoint, type AlphaDesk } from '../api/alpha'
import { listMessages } from '../api/sessions'
import type { ChatMessage, Project, Runner } from '../types'
import { MessageContent } from '../components/chat/MessageContent'
import { Composer } from '../components/chat/Composer'
import { confirmDialog } from '../components/ui/Dialog'
import { IconPanelLeft } from '../components/shell/icons'

const POLL_MS = 2500
const SIDE_COLLAPSED_KEY = 'proxima.alpha.sideCollapsed'
const cleanAlpha = (text: string) => text.replace(/<proxima-tool>\s*\{[\s\S]*?\}\s*<\/proxima-tool>/g, '').trim()
const statusLabel = (status: string) => status === 'review' ? 'Needs you' : status.charAt(0).toUpperCase() + status.slice(1)
const formatBudget = (seconds: number) => seconds >= 3600 ? `${Math.round(seconds / 3600)}h` : `${Math.round(seconds / 60)}m`

function readSideCollapsed(): boolean {
  if (typeof localStorage === 'undefined') return false
  const stored = localStorage.getItem(SIDE_COLLAPSED_KEY)
  if (stored === '1') return true
  if (stored === '0') return false
  // Mobile: default collapsed so conversation + composer get full width.
  try {
    if (typeof window !== 'undefined' && window.matchMedia?.('(max-width: 900px)').matches) return true
  } catch { /* jsdom without matchMedia */ }
  return false
}

function AlphaEmpty({ onExample }: { onExample: (value: string) => void }) {
  const examples = [
    'Audit this project and delegate independent fixes.',
    'Split the release into implementation, docs, and verification jobs.',
    'Review active work and tell me what needs my attention.',
  ]
  return <div className="alpha-empty">
    <strong>Delegate an outcome</strong>
    <p>Alpha breaks work into durable jobs, dispatches up to three workers, and brings decisions back here. Hands-on work still belongs in Chat.</p>
    <div className="alpha-examples" aria-label="Example delegations">{examples.map(example => <button type="button" className="ghost-button" key={example} onClick={() => onExample(example)}>{example}</button>)}</div>
  </div>
}

type AlphaJobResult = { id: number; title?: string; status: string; engine?: string }
type AlphaToolResult = { ok: boolean; tool?: string | null; result?: { job?: AlphaJobResult; jobs?: AlphaJobResult[]; [key: string]: unknown }; error?: { code?: string; message?: string } }
const toolResultLabel = (tool: AlphaToolResult, jobs: AlphaJobResult[]) => {
  if (!tool.ok && jobs.length) return `Created ${jobs.length} job${jobs.length === 1 ? '' : 's'}; some stayed queued`
  if (!tool.ok) return 'Product action could not run'
  if (tool.tool === 'dispatch_jobs') return `Dispatched ${jobs.length} job${jobs.length === 1 ? '' : 's'}`
  if (tool.tool === 'start_jobs') return `Started ${jobs.length} job${jobs.length === 1 ? '' : 's'}`
  return ({
    list_projects: 'Projects loaded', list_jobs: 'Work queue checked', list_worker_agents: 'Agents loaded',
    list_plans: 'Plans loaded', get_alpha_settings: 'Alpha settings checked', capacity: 'Capacity checked',
    start_plan: 'Plan started', set_unattended: 'Unattended setting saved', set_budgets: 'Budgets saved',
    create_attention: 'Decision added to Attention',
  }[tool.tool || ''] || 'Product action completed')
}
function parseToolResults(content: string): AlphaToolResult[] | null {
  const match = content.match(/^Alpha tool results:\s*```json\s*([\s\S]*?)\s*```\s*$/)
  if (!match) return null
  try { const value = JSON.parse(match[1]); return Array.isArray(value) ? value : null } catch { return null }
}
function AlphaThread({ messages, loading, onOpenJob }: { messages: ChatMessage[]; loading: boolean; onOpenJob: (id: number, engine?: string) => void }) {
  if (loading) return <div className="alpha-thread-state" role="status"><span className="ui-spinner" /> Loading Alpha thread…</div>
  if (!messages.length) return null
  return <div className="alpha-thread" aria-live="polite">{messages.map((message, index) => {
    const content = message.role === 'assistant' ? cleanAlpha(message.content) : message.content
    if (!content) return null
    const tools = message.role === 'system' ? parseToolResults(content) : null
    return <article className={`alpha-message ${message.role}`} key={message.id ?? index}>
      <strong>{message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Alpha' : 'Proxima'}</strong>
      {tools ? <div className="alpha-tool-results">{tools.map((tool, toolIndex) => {
        const jobs = tool.result?.jobs || (tool.result?.job ? [tool.result.job] : [])
        return <div className={tool.ok ? 'ok' : 'failed'} key={`${tool.tool}-${toolIndex}`}>
          <b>{toolResultLabel(tool, jobs)}</b>
          {jobs.length ? <ul>{jobs.map(job => <li key={job.id}><button type="button" onClick={() => onOpenJob(job.id, job.engine)}><span>{job.title || `Job #${job.id}`}</span><small>{statusLabel(job.status)}</small></button></li>)}</ul> : null}
          {!tool.ok && <p>{tool.error?.message || 'Alpha received an unknown product error.'}</p>}
        </div>
      })}</div> : <MessageContent content={content} />}
    </article>
  })}</div>
}

function AlphaJobs({ desk, onOpenJob }: { desk: AlphaDesk; onOpenJob: (id: number, engine?: string) => void }) {
  const active = desk.jobs.filter(job => ['running', 'queued', 'review'].includes(job.desk_status))
  return <section className="alpha-side-section" aria-labelledby="alpha-work-title">
    <div className="alpha-section-head"><div><span className="eyebrow">Work</span><h2 id="alpha-work-title">Active queue</h2></div><span className="alpha-count">{active.length}</span></div>
    {!active.length ? <div className="alpha-zero"><strong>No delegated work</strong><p>Jobs Alpha starts will appear here with live status.</p></div> : <div className="alpha-job-list">{active.map(job => <button type="button" className="alpha-job" key={job.id} onClick={() => onOpenJob(job.id, job.engine)}>
      <span className={`alpha-job-status ${job.desk_status}`} aria-hidden="true" />
      <span><strong>{job.title}</strong><small>{job.project_name || 'No project'} · {statusLabel(job.desk_status)}</small></span>
      <span aria-hidden="true">›</span>
    </button>)}</div>}
  </section>
}

function AlphaNeedsAttention({ desk, onOpenJob }: { desk: AlphaDesk; onOpenJob: (id: number, engine?: string) => void }) {
  return <section className="alpha-side-section" aria-labelledby="alpha-needs-title">
    <div className="alpha-section-head"><div><span className="eyebrow">Decisions</span><h2 id="alpha-needs-title">Needs you</h2></div><span className="alpha-count">{desk.attention.length}</span></div>
    {!desk.attention.length ? <div className="alpha-zero"><strong>Nothing is blocked</strong><p>Reviews and Alpha questions will collect here and in Attention.</p></div> : <ul className="alpha-needs-list">{desk.attention.map(item => <li key={item.id}>
      {item.target.job_id != null ? <button type="button" onClick={() => onOpenJob(item.target.job_id!, item.target.engine)}><strong>{item.title}</strong><small>Open linked work</small></button>
        : <div><strong>{item.title}</strong><small>Open the global Attention inbox for details.</small></div>}
    </li>)}</ul>}
  </section>
}

function CheckpointTimeline({ token, checkpoints, onChanged }: { token: string; checkpoints: AlphaCheckpoint[]; onChanged: () => Promise<void> }) {
  const [busyId, setBusyId] = React.useState<number | null>(null)
  const [error, setError] = React.useState('')
  const restore = async (checkpoint: AlphaCheckpoint) => {
    if (busyId != null) return
    setBusyId(checkpoint.id); setError('')
    try {
      const impact = await previewCheckpointRestore(token, checkpoint.job_id, checkpoint.id)
      const resetPaths = impact.git_refs.filter(ref => ref.restore_strategy === 'worktree_reset').map(ref => ref.worktree_path).filter(Boolean)
      const referencePaths = impact.git_refs.filter(ref => ref.restore_strategy !== 'worktree_reset').map(ref => ref.repo_path).filter(Boolean)
      const details = [
        `Database: ${impact.database_scope.join(', ')}`,
        resetPaths.length ? `Git worktrees to reset: ${resetPaths.join(', ')}` : 'Git worktrees to reset: none',
        referencePaths.length ? `Reference only (never reset): ${referencePaths.join(', ')}` : '',
        impact.conflicts.length ? `Blocked by: ${impact.conflicts.map(item => item.title).join(', ')}` : '',
      ].filter(Boolean).join('\n')
      if (!impact.can_restore) { setError(`Restore is blocked while ${impact.conflicts.map(item => item.title).join(', ')} is running.`); return }
      const ok = await confirmDialog({ title: `Restore “${impact.job_title}”?`, message: details, confirmLabel: 'Restore checkpoint', danger: true })
      if (!ok) return
      await restoreCheckpoint(token, checkpoint.job_id, checkpoint.id)
      await onChanged()
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusyId(null) }
  }
  const pin = async (checkpoint: AlphaCheckpoint) => {
    if (busyId != null) return
    setBusyId(checkpoint.id); setError('')
    try { await setCheckpointPinned(token, checkpoint.job_id, checkpoint.id, !checkpoint.pinned); await onChanged() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusyId(null) }
  }
  return <section className="alpha-side-section" aria-labelledby="alpha-checkpoint-title">
    <div className="alpha-section-head"><div><span className="eyebrow">Safety</span><h2 id="alpha-checkpoint-title">Checkpoints</h2></div><span className="alpha-count">{checkpoints.length}</span></div>
    {!checkpoints.length ? <div className="alpha-zero"><strong>No checkpoints yet</strong><p>A job-scoped restore point is created before every Alpha worker starts.</p></div> : <ol className="alpha-checkpoints">{checkpoints.slice(0, 10).map(checkpoint => <li key={checkpoint.id}>
      <span className="checkpoint-line" aria-hidden="true" />
      <div><strong>Job #{checkpoint.job_id}</strong><small>{new Date(checkpoint.created_at.replace(' ', 'T') + 'Z').toLocaleString()}</small></div>
      <div className="checkpoint-actions"><button type="button" className="text-button" disabled={busyId != null} onClick={() => void pin(checkpoint)}>{checkpoint.pinned ? 'Unpin' : 'Pin'}</button><button type="button" className="text-button" disabled={busyId != null} onClick={() => void restore(checkpoint)}>{busyId === checkpoint.id ? 'Working…' : 'Restore'}</button></div>
    </li>)}</ol>}
    {error && <p className="alpha-inline-error" role="alert">{error}</p>}
  </section>
}

/** Project slug for attach/@ mentions: shell active project, else first active Alpha job project. */
export function resolveAlphaProjectSlug(
  activeProject: Project | null | undefined,
  jobs: { desk_status: string; project_slug?: string | null }[],
): string | undefined {
  if (activeProject?.slug) return activeProject.slug
  const fromJob = jobs.find(job => job.project_slug && ['running', 'queued', 'review'].includes(job.desk_status))
  return fromJob?.project_slug || undefined
}

export function AlphaScreen({
  token,
  runners,
  onOpenJob,
  activeProject = null,
}: {
  token: string
  runners: Runner[]
  onOpenJob: (id: number, engine?: string) => void
  activeProject?: Project | null
}) {
  const [desk, setDesk] = React.useState<AlphaDesk | null>(null)
  const [messages, setMessages] = React.useState<ChatMessage[]>([])
  const [draftSeed, setDraftSeed] = React.useState<string | undefined>(undefined)
  const [draftSeedNonce, setDraftSeedNonce] = React.useState(0)
  const [loading, setLoading] = React.useState(true)
  const [settingBusy, setSettingBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const [sideCollapsed, setSideCollapsed] = React.useState(readSideCollapsed)
  const mounted = React.useRef(true)

  const load = React.useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const next = await getAlphaDesk(token)
      const thread = await listMessages(token, next.session.id)
      if (!mounted.current) return
      setDesk(next); setMessages(thread.messages); setError('')
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : String(err))
    } finally { if (mounted.current && !quiet) setLoading(false) }
  }, [token])

  React.useEffect(() => {
    mounted.current = true
    void load()
    const id = window.setInterval(() => void load(true), POLL_MS)
    return () => { mounted.current = false; window.clearInterval(id) }
  }, [load])

  React.useEffect(() => {
    try { localStorage.setItem(SIDE_COLLAPSED_KEY, sideCollapsed ? '1' : '0') } catch { /* storage disabled */ }
  }, [sideCollapsed])

  const toggleSide = () => setSideCollapsed(value => !value)
  const seedExample = (value: string) => {
    setDraftSeed(value)
    setDraftSeedNonce(n => n + 1)
  }
  const sendDelegation = async (content: string) => {
    setError('')
    try {
      await sendAlphaMessage(token, content)
      await load(true)
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      throw err
    }
  }
  const toggleUnattended = async () => {
    if (!desk || settingBusy) return
    setSettingBusy(true); setError('')
    try { await saveAlphaSettings(token, { unattended: !desk.unattended }); await load(true) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setSettingBusy(false) }
  }
  const changeRunner = async (runnerId: string) => {
    if (!runnerId || settingBusy) return
    setSettingBusy(true); setError('')
    try { await saveAlphaSettings(token, { runner_id: runnerId }); await load(true) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setSettingBusy(false) }
  }

  if (loading && !desk) return <section className="alpha-view alpha-loading" role="status"><span className="ui-spinner" /><strong>Opening Alpha desk…</strong></section>
  if (!desk) return <section className="alpha-view"><div className="alpha-load-error" role="alert"><strong>Alpha desk could not load</strong><p>{error || 'The server did not return desk data.'}</p><button type="button" className="primary-button" onClick={() => void load()}>Try again</button></div></section>
  const alphaBusy = desk.alpha_run?.status === 'queued' || desk.alpha_run?.status === 'running'
  const availableRunners = runners.filter(runner => runner.id === desk.backing_runner || runner.runnable || runner.installed)
  const projectSlug = resolveAlphaProjectSlug(activeProject, desk.jobs)
  return <section className={`alpha-view ${sideCollapsed ? 'alpha-side-collapsed' : ''}`}>
    <header className="code-header alpha-head">
      <div><p className="eyebrow">Orchestration</p><strong>Alpha</strong></div>
      <div className="alpha-controls code-context">
        <label className="alpha-runner-label"><span className="sr-only">Backing runner</span>
          <select className="ui-select" value={desk.backing_runner} disabled={settingBusy} aria-label="Backing runner" onChange={event => void changeRunner(event.target.value)}>{availableRunners.map(runner => <option value={runner.id} key={runner.id}>{runner.displayName}</option>)}</select>
        </label>
        <button type="button" className={`toggle-pill ${desk.unattended ? 'on' : ''}`} aria-pressed={desk.unattended} disabled={settingBusy} onClick={() => void toggleUnattended()}><span className="toggle-knob" aria-hidden="true" />{settingBusy ? 'Saving…' : desk.unattended ? 'Unattended on' : 'Unattended off'}</button>
        <button
          type="button"
          className={`tool-btn alpha-side-toggle ${sideCollapsed ? '' : 'active'}`}
          onClick={toggleSide}
          aria-pressed={!sideCollapsed}
          aria-label={sideCollapsed ? 'Show work panel' : 'Hide work panel'}
          title={sideCollapsed ? 'Show work panel' : 'Hide work panel'}
        >
          <IconPanelLeft size={16} />
        </button>
      </div>
    </header>
    <div className="alpha-body">
      <div className="alpha-capacity" aria-label={`${desk.capacity.running} running, ${desk.capacity.free} free, ${desk.capacity.queued} queued`}>
        <span><i className="capacity-live" />{desk.capacity.running} running / {desk.capacity.free} free</span><span>{desk.capacity.queued} queued</span><span>{desk.budgets.budget_turns} turns · {formatBudget(desk.budgets.budget_wall_seconds)} wall clock{desk.budgets.budget_tokens ? ` · ${desk.budgets.budget_tokens.toLocaleString()} tokens when reported` : ''}</span>
      </div>
      {error && <div className="alpha-error error-bar" role="alert"><strong>Alpha needs a retry</strong><span>{error}</span><button type="button" className="ghost-button" onClick={() => setError('')} aria-label="Dismiss error">Dismiss</button></div>}
      <div className="alpha-grid">
        <div className="alpha-conversation">
          {!messages.length && !alphaBusy ? <AlphaEmpty onExample={seedExample} /> : <AlphaThread messages={messages} loading={false} onOpenJob={onOpenJob} />}
          {alphaBusy && <div className="alpha-working" role="status"><span className="ui-spinner" /> Alpha is orchestrating…</div>}
          <div className="alpha-composer-dock">
            <Composer
              token={token}
              slug={projectSlug}
              disabled={alphaBusy}
              promptModes={false}
              generateKinds={[]}
              combinedActions={false}
              placeholder="Describe the outcome, constraints, and projects Alpha may use…"
              textareaLabel="Delegate an outcome"
              submitLabel={alphaBusy ? 'Alpha is working' : 'Delegate'}
              submittingLabel="Sending…"
              draftSeed={draftSeed}
              draftSeedNonce={draftSeedNonce}
              onDraftSeedConsumed={() => setDraftSeed(undefined)}
              onSubmit={sendDelegation}
            />
            <p className="alpha-composer-hint">{desk.unattended ? 'Alpha may continue queued work within your saved budgets.' : 'Alpha acts only when you ask. Workers run Autonomous by default.'}{!projectSlug ? ' Pick a project in the shell to attach files or @-mention paths.' : ''}</p>
          </div>
        </div>
        {!sideCollapsed && (
          <aside className="alpha-side" aria-label="Alpha work panel">
            <AlphaJobs desk={desk} onOpenJob={onOpenJob} />
            <AlphaNeedsAttention desk={desk} onOpenJob={onOpenJob} />
            <CheckpointTimeline token={token} checkpoints={desk.checkpoints} onChanged={() => load(true)} />
          </aside>
        )}
        {sideCollapsed && (
          <button
            type="button"
            className="alpha-side-reopen"
            onClick={() => setSideCollapsed(false)}
            aria-label="Expand work panel"
            title="Expand work panel"
          >
            <span className="eyebrow">Work</span>
            <span aria-hidden="true">‹</span>
          </button>
        )}
      </div>
    </div>
  </section>
}

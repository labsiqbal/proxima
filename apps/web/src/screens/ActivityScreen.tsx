import React from 'react'
import type { Job, JobStatus, JobStep, Project } from '../types'
import { listJobs, getJob, approveJob, deleteJob } from '../api/jobs'
import { MessageContent } from '../components/chat/MessageContent'
import { confirmDialog } from '../components/ui/Dialog'
import { IconTrash } from '../components/shell/icons'
import { BackButton } from '../components/ui/BackButton'
import type { Artifact } from '../api/files'
import { stripQuestionForms } from '../components/chat/questionForm'

const ART_ICON: Record<string, string> = { design: '🎨', app: '▶', page: '🌐', doc: '📄', file: '📎' }

const PAGE = 25
const relTime = (s?: string | null): string => {
  if (!s) return '—'
  const d = new Date(s.replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? '' : 'Z'))
  if (isNaN(d.getTime())) return '—'
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return 'now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
const progress = (j: Job) => { const total = j.steps_state.length; const done = j.steps_state.filter(s => s.status === 'done').length; return total ? `${done}/${total}` : '—' }
const StatusPill = ({ status }: { status: JobStatus | JobStep['status'] }) => <span className={`job-pill ${status}`}>{status}</span>

const BOARD: { key: JobStatus; label: string }[] = [
  { key: 'queued', label: 'Queued' },
  { key: 'running', label: 'Running' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' }
]
const STATUS_FILTERS: (JobStatus | 'all')[] = ['all', 'queued', 'running', 'review', 'done', 'failed', 'cancelled']

function JobDetail({ token, jobId, onBack, onChanged, designStudioEnabled = false, onOpenDesign, onOpenFile }: { token: string; jobId: number; onBack: () => void; onChanged?: () => void; designStudioEnabled?: boolean; onOpenDesign?: (id: string) => void; onOpenFile?: (slug: string, path: string) => void }) {
  const [job, setJob] = React.useState<Job | null>(null)
  const [sel, setSel] = React.useState(0)        // which step node is selected (shown on the right)
  const [edited, setEdited] = React.useState('')
  const [error, setError] = React.useState('')
  const [busyAction, setBusyAction] = React.useState<'approve' | 'delete' | null>(null)
  const seeded = React.useRef<number | null>(null)
  const loadSeq = React.useRef(0)
  const actionSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      loadSeq.current += 1
      actionSeq.current += 1
    }
  }, [])

  const load = React.useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const next = await getJob(token, jobId)
      if (mountedRef.current && seq === loadSeq.current) setJob(next)
    } catch (e) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    }
  }, [token, jobId])
  React.useEffect(() => {
    loadSeq.current += 1
    actionSeq.current += 1
    setJob(null)
    setSel(0)
    setEdited('')
    setError('')
    setBusyAction(null)
    seeded.current = null
  }, [jobId])
  React.useEffect(() => { void load() }, [load])

  // Live-update while the job is running.
  React.useEffect(() => {
    if (job?.status !== 'running') return
    const t = window.setInterval(() => { void load() }, 1500)
    return () => clearInterval(t)
  }, [job?.status, load])

  // On first load of a job, focus the node that matters (the active / review step).
  React.useEffect(() => {
    if (job && seeded.current !== job.id) { seeded.current = job.id; setSel(Math.min(job.current_step_idx, job.steps_state.length - 1)) }
  }, [job])

  const isReview = job?.status === 'review'
  const isMidGate = !!job && isReview && job.current_step_idx < job.steps_state.length - 1
  const reviewStep = job && isReview ? job.steps_state[job.current_step_idx] : null
  React.useEffect(() => { setEdited(reviewStep?.output_summary || '') }, [job?.id, isReview, job?.current_step_idx])

  async function remove() {
    if (busyAction) return
    if (!(await confirmDialog({ title: 'Delete this run?', message: 'The run record and its agent thread are removed. Any designs or files it produced are kept.', confirmLabel: 'Delete', danger: true }))) return
    const seq = ++actionSeq.current
    setBusyAction('delete'); setError('')
    try {
      await deleteJob(token, jobId)
      if (mountedRef.current && seq === actionSeq.current) {
        onChanged?.()
        onBack()
      }
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusyAction(null)
    }
  }

  async function approve(body?: { edited_output?: string }) {
    if (busyAction) return
    const seq = ++actionSeq.current
    setBusyAction('approve'); setError('')
    try {
      const u = await approveJob(token, jobId, body)
      if (mountedRef.current && seq === actionSeq.current) {
        setJob(u)
        onChanged?.()
      }
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusyAction(null)
    }
  }

  if (!job) return <div className="job-detail"><div className="task-detail-head"><BackButton label="Activity" onClick={onBack} /></div>{error ? <div className="error-bar">{error}</div> : <p className="muted" style={{ padding: 16 }}>Loading…</p>}</div>

  const steps = job.steps_state
  if (!steps.length) return <div className="job-detail"><div className="task-detail-head"><BackButton label="Activity" onClick={onBack} /><strong className="task-title">{job.title}</strong><StatusPill status={job.status} /></div><p className="muted" style={{ padding: 16 }}>This job has no steps.</p></div>
  const cur = steps[Math.min(sel, steps.length - 1)]
  const onReviewStep = isMidGate && sel === job.current_step_idx

  return <div className="job-detail">
    <div className="task-detail-head">
      <BackButton label="Activity" onClick={onBack} />
      <strong className="task-title" title={job.title}>{job.title}</strong>
      <StatusPill status={job.status} />
      <button className="row-action danger jfd-delete" title="Delete run" aria-label="Delete run" onClick={() => void remove()} disabled={!!busyAction}><IconTrash size={15} /></button>
    </div>
    {isReview && (isMidGate
      ? <div className="task-review-bar wf-review-mid">
          <span>⏸ Paused for your review — step {job.current_step_idx + 1}{reviewStep ? `: ${reviewStep.name}` : ''}.</span>
          <button className="primary-button" onClick={() => void approve(reviewStep && edited.trim() !== (reviewStep.output_summary || '') ? { edited_output: edited } : undefined)} disabled={!!busyAction}>{busyAction === 'approve' ? 'Approving…' : '✓ Approve & continue'}</button>
        </div>
      : <div className="task-review-bar">
          <span>✅ Ready for review.</span>
          <button className="primary-button" onClick={() => void approve()} disabled={!!busyAction}>{busyAction === 'approve' ? 'Approving…' : '✓ Approve → Done'}</button>
        </div>)}
    {error && <div className="error-bar">{error}</div>}

    <div className="job-flow-wrap">
      {/* Left: the flow diagram — each step a node on a connected spine, colored by state */}
      <div className="job-flow" role="list">
        {typeof job.input?.brief === 'string' && job.input.brief && <p className="job-flow-brief" title={job.input.brief}>{job.input.brief}</p>}
        {steps.map((s, i) => <button role="listitem" key={s.id || i} className={`flow-node st-${s.status} ${i === sel ? 'sel' : ''}`} onClick={() => setSel(i)}>
          <span className="flow-dot" />
          <span className="flow-num">{i + 1}</span>
          <span className="flow-name">{s.name}</span>
          {s.review_required && <span className="flow-gate" title="Review gate">⏸</span>}
          <span className="flow-status">{s.status}</span>
        </button>)}
      </div>

      {/* Right: the selected step's detail + output */}
      <div className="job-flow-detail" key={sel}>
        <div className="jfd-head"><span className="wf-step-num">{sel + 1}</span><strong className="jfd-name">{cur.name}</strong><StatusPill status={cur.status} /></div>
        {cur.instruction && <p className="jfd-instr">{cur.instruction}</p>}
        {cur.expected_output && <p className="jfd-expected"><span className="muted">Expected:</span> {cur.expected_output}</p>}
        {onReviewStep
          ? <label className="wf-step-field jfd-edit">Output <span className="muted">(edit before continuing)</span>
              <textarea rows={10} value={edited} onChange={e => setEdited(e.target.value)} placeholder="No output yet." /></label>
          : cur.output_summary
            ? <div className="job-step-output"><MessageContent content={stripQuestionForms(cur.output_summary)} /></div>
            : <p className="muted jfd-empty">{cur.status === 'done' ? 'No output summary.' : cur.status === 'running' ? 'Running…' : cur.error ? cur.error : 'Not started yet.'}</p>}
        {cur.error && cur.output_summary && <p className="error-text">{cur.error}</p>}
        {(() => {
          const list: Artifact[] = cur.produced_artifacts?.length ? cur.produced_artifacts
            : (cur.produced_designs || []).map(d => ({ type: 'design' as const, id: d.id, title: d.title, path: '' }))
          if (!list.length) return null
          const open = (a: Artifact) => a.type === 'design' && designStudioEnabled
            ? onOpenDesign?.(a.id || '')
            : (job?.project_slug && a.path && onOpenFile?.(job.project_slug, a.type === 'design' ? `${a.path.replace(/\/$/, '')}/scene.json` : a.path))
          return <div className="jfd-artifacts">
            {list.map(a => <button key={a.path || a.id} className="artifact-chip" onClick={() => open(a)} disabled={a.type === 'design' && !designStudioEnabled && !a.path} title={`Open ${a.title}`}>
              {ART_ICON[a.type] || '📎'} <span>{a.title}</span> <span className="muted">· {a.type === 'design' && designStudioEnabled ? 'open in Design Studio' : 'open'}</span>
            </button>)}
          </div>
        })()}
      </div>
    </div>
  </div>
}

export function ActivityScreen({ token, activeProject, pendingJobId, onPendingConsumed, designStudioEnabled = false, onOpenDesign, onOpenFile }: {
  token: string; activeProject: Project | null; pendingJobId?: number | null; onPendingConsumed?: () => void; designStudioEnabled?: boolean; onOpenDesign?: (id: string) => void; onOpenFile?: (slug: string, path: string) => void
}) {
  const [mode, setMode] = React.useState<'list' | 'board' | 'review'>('list')
  const [statusFilter, setStatusFilter] = React.useState<JobStatus | 'all'>('all')
  const [includeArchived, setIncludeArchived] = React.useState(false)
  const [items, setItems] = React.useState<Job[]>([])
  const [total, setTotal] = React.useState(0)
  const [offset, setOffset] = React.useState(0)
  const [selected, setSelected] = React.useState<number | null>(null)
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

  // Effective query: Review mode forces status=review; Board pulls the in-flight set.
  const effStatus = mode === 'review' ? 'review' : (mode === 'list' && statusFilter !== 'all' ? statusFilter : undefined)

  const load = React.useCallback(async (off: number, append: boolean) => {
    const seq = ++loadSeq.current
    try {
      const r = await listJobs(token, { status: effStatus, project_slug: activeProject?.slug, include_archived: mode === 'list' ? includeArchived : false, limit: mode === 'board' ? 100 : PAGE, offset: off })
      if (!mountedRef.current || seq !== loadSeq.current) return
      setError('')
      setTotal(r.total)
      setItems(cur => append ? [...cur, ...r.items] : r.items)
    } catch (e) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(e))
    }
  }, [token, effStatus, activeProject?.slug, includeArchived, mode])

  React.useEffect(() => {
    if (!mountedRef.current) return
    setOffset(0); void load(0, false)
  }, [load])

  React.useEffect(() => {
    if (selected != null || mode === 'review') return
    if (!items.some(j => j.status === 'queued' || j.status === 'running')) return
    const t = window.setInterval(() => { void load(0, false) }, 2500)
    return () => window.clearInterval(t)
  }, [items, load, mode, selected])

  // Deep-open a job when navigated from a workflow run.
  React.useEffect(() => {
    if (mountedRef.current && pendingJobId) { setSelected(pendingJobId); onPendingConsumed?.() }
  }, [pendingJobId, onPendingConsumed])

  const refresh = () => void load(0, false)

  if (selected != null) return <section className="tasks-view"><JobDetail token={token} jobId={selected} onBack={() => { setSelected(null); refresh() }} onChanged={refresh} designStudioEnabled={designStudioEnabled} onOpenDesign={onOpenDesign} onOpenFile={onOpenFile} /></section>

  return <section className="tasks-view">
    <div className="tasks-head">
      <div className="seg sm">
        <button className={mode === 'list' ? 'active' : ''} onClick={() => setMode('list')}>List</button>
        <button className={mode === 'board' ? 'active' : ''} onClick={() => setMode('board')}>Board</button>
        <button className={mode === 'review' ? 'active' : ''} onClick={() => setMode('review')}>Review</button>
      </div>
      {mode === 'list' && <>
        <div className="seg sm job-filter">{STATUS_FILTERS.map(s => <button key={s} className={statusFilter === s ? 'active' : ''} onClick={() => setStatusFilter(s)}>{s}</button>)}</div>
        <label className="job-archived-toggle"><input type="checkbox" checked={includeArchived} onChange={e => setIncludeArchived(e.target.checked)} /> Archived</label>
      </>}
    </div>
    {error && <div className="error-bar">{error}</div>}

    {mode === 'board'
      ? <div className="kanban">{BOARD.map(col => {
          const colItems = items.filter(j => j.status === col.key)
          return <div className="kanban-col" key={col.key}>
            <div className="kanban-col-head"><span>{col.label}</span><span className="kanban-count">{colItems.length}</span></div>
            <div className="kanban-cards">{colItems.map((j, i) => <div className="kanban-card stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties} key={j.id} role="button" tabIndex={0} onClick={() => setSelected(j.id)} onKeyDown={e => { if (e.key === 'Enter') setSelected(j.id) }}>
              <strong>{j.title}{j.schedule_id != null && <span className="job-pill scheduled">scheduled</span>}</strong>
              <small>{j.workflow_id ? `${progress(j)} steps` : 'Task'} · {relTime(j.created_at)}</small>
            </div>)}</div>
          </div>
        })}</div>
      : <div className="job-list">
          {items.length === 0
            ? <div className="placeholder-view"><div className="assistant-bubble compact"><p className="muted">{mode === 'review' ? 'Nothing waiting for review.' : 'No activity yet.'}</p></div></div>
            : <>
              <div className="job-row job-row-head">
                <span className="jr-title">Job</span><span className="jr-wf">Type</span><span className="jr-status">Status</span><span className="jr-prog">Steps</span><span className="jr-time">Created</span>
              </div>
              {items.map((j, i) => <button className="job-row stagger-item" style={{ ['--i' as string]: i } as React.CSSProperties} key={j.id} onClick={() => setSelected(j.id)}>
                <span className="jr-title">{j.title}{j.schedule_id != null && <span className="job-pill scheduled">scheduled</span>}</span>
                <span className="jr-wf muted">{j.workflow_id ? (j.schedule_id != null ? 'Scheduled' : 'Run') : 'Task'}</span>
                <span className="jr-status"><StatusPill status={j.status} /></span>
                <span className="jr-prog muted">{progress(j)}</span>
                <span className="jr-time muted">{relTime(j.created_at)}</span>
              </button>)}
              {mode === 'list' && items.length < total && <div className="job-more"><button className="ghost-button" onClick={() => { const next = offset + PAGE; setOffset(next); void load(next, true) }}>Load more ({items.length}/{total})</button></div>}
            </>}
        </div>}
  </section>
}

import React from 'react'
import type { Job, JobStatus, JobStep, Project } from '../types'
import { listJobs } from '../api/jobs'
import { usePolling } from '../hooks/usePolling'

const PAGE = 25
const relTime = (value?: string | null): string => {
  if (!value) return '—'
  const date = new Date(value.replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? '' : 'Z'))
  if (Number.isNaN(date.getTime())) return '—'
  const diff = (Date.now() - date.getTime()) / 1000
  if (diff < 60) return 'now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
const progress = (job: Job) => { const total = job.steps_state.length; const done = job.steps_state.filter(step => step.status === 'done').length; return total ? `${done}/${total}` : '—' }
const StatusPill = ({ status }: { status: JobStatus | JobStep['status'] }) => <span className={`job-pill ${status}`}>{status}</span>
const BOARD: { key: JobStatus; label: string }[] = [
  { key: 'queued', label: 'Queued' },
  { key: 'running', label: 'Running' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' },
]
const STATUS_FILTERS: (JobStatus | 'all')[] = ['all', 'queued', 'running', 'review', 'done', 'failed', 'cancelled']

export function ActivityScreen({ token, activeProject, onOpenTask }: {
  token: string
  activeProject: Project | null
  onOpenTask: (jobId: number) => void
}) {
  const [mode, setMode] = React.useState<'list' | 'board' | 'review'>('list')
  const [statusFilter, setStatusFilter] = React.useState<JobStatus | 'all'>('all')
  const [includeArchived, setIncludeArchived] = React.useState(false)
  const [items, setItems] = React.useState<Job[]>([])
  const [total, setTotal] = React.useState(0)
  const [offset, setOffset] = React.useState(0)
  const [error, setError] = React.useState('')
  const loadSeq = React.useRef(0)
  const mountedRef = React.useRef(true)

  React.useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; loadSeq.current += 1 }
  }, [])

  const effectiveStatus = mode === 'review' ? 'review' : (mode === 'list' && statusFilter !== 'all' ? statusFilter : undefined)
  const load = React.useCallback(async (nextOffset: number, append: boolean) => {
    const seq = ++loadSeq.current
    try {
      const page = await listJobs(token, { status: effectiveStatus, project_slug: activeProject?.slug, include_archived: mode === 'list' ? includeArchived : false, limit: mode === 'board' ? 100 : PAGE, offset: nextOffset })
      if (!mountedRef.current || seq !== loadSeq.current) return
      setError('')
      setTotal(page.total)
      setItems(current => append ? [...current, ...page.items] : page.items)
    } catch (reason) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(reason))
    }
  }, [token, effectiveStatus, activeProject?.slug, includeArchived, mode])

  React.useEffect(() => { setOffset(0); void load(0, false) }, [load])
  const hasActiveJobs = items.some(job => job.status === 'queued' || job.status === 'running')
  usePolling(() => load(0, false), 2500, { enabled: mode !== 'review' && hasActiveJobs, immediate: false })

  return <section className="tasks-view">
    <div className="tasks-head">
      <div><p className="eyebrow">Ops</p><h1>Tasks</h1></div>
      <div className="seg sm">
        <button className={mode === 'list' ? 'active' : ''} onClick={() => setMode('list')}>List</button>
        <button className={mode === 'board' ? 'active' : ''} onClick={() => setMode('board')}>Board</button>
        <button className={mode === 'review' ? 'active' : ''} onClick={() => setMode('review')}>Review</button>
      </div>
      {mode === 'list' && <>
        <div className="seg sm job-filter">{STATUS_FILTERS.map(status => <button key={status} className={statusFilter === status ? 'active' : ''} onClick={() => setStatusFilter(status)}>{status}</button>)}</div>
        <label className="job-archived-toggle"><input type="checkbox" checked={includeArchived} onChange={event => setIncludeArchived(event.target.checked)} /> Archived</label>
      </>}
    </div>
    {error && <div className="error-bar">{error}</div>}

    {mode === 'board'
      ? <div className="kanban">{BOARD.map(column => {
          const columnItems = items.filter(job => job.status === column.key)
          return <div className="kanban-col" key={column.key}>
            <div className="kanban-col-head"><span>{column.label}</span><span className="kanban-count">{columnItems.length}</span></div>
            <div className="kanban-cards">{columnItems.map((job, index) => <button type="button" className="kanban-card stagger-item" style={{ ['--i' as string]: index } as React.CSSProperties} key={job.id} onClick={() => onOpenTask(job.id)}>
              <strong>{job.title}{job.schedule_id != null && <span className="job-pill scheduled">scheduled</span>}</strong>
              <small>{job.workflow_id ? `${progress(job)} steps` : 'Task'} · {relTime(job.created_at)}</small>
            </button>)}</div>
          </div>
        })}</div>
      : <div className="job-list">
          {items.length === 0
            ? <div className="placeholder-view"><div className="assistant-bubble compact"><p className="muted">{mode === 'review' ? 'Nothing waiting for review.' : 'No Ops tasks yet.'}</p></div></div>
            : <>
              <div className="job-row job-row-head">
                <span className="jr-title">Task</span><span className="jr-wf">Type</span><span className="jr-status">Status</span><span className="jr-prog">Steps</span><span className="jr-time">Created</span>
              </div>
              {items.map((job, index) => <button className="job-row stagger-item" style={{ ['--i' as string]: index } as React.CSSProperties} key={job.id} onClick={() => onOpenTask(job.id)}>
                <span className="jr-title">{job.title}{job.schedule_id != null && <span className="job-pill scheduled">scheduled</span>}</span>
                <span className="jr-wf muted">{job.workflow_id ? (job.schedule_id != null ? 'Scheduled' : 'Workflow') : 'Task'}</span>
                <span className="jr-status"><StatusPill status={job.status} /></span>
                <span className="jr-prog muted">{progress(job)}</span>
                <span className="jr-time muted">{relTime(job.created_at)}</span>
              </button>)}
              {mode === 'list' && items.length < total && <div className="job-more"><button className="ghost-button" onClick={() => { const next = offset + PAGE; setOffset(next); void load(next, true) }}>Load more ({items.length}/{total})</button></div>}
            </>}
        </div>}
  </section>
}

import React from 'react'
import { listJobs } from '../../api/jobs'
import { activeRuns } from '../../api/runs'
import type { ChatSession, Job } from '../../types'
import { IconActivity } from './icons'

export type RunningTaskItem =
  | { kind: 'job'; id: string; jobId: number; title: string; status: string; engine?: string; project?: string | null }
  | { kind: 'session'; id: string; sessionId: number; title: string; status: string }

const statusLabel = (status: string) =>
  status.charAt(0).toUpperCase() + status.slice(1)

/** Build a compact, de-duplicated list of in-flight work from active runs + jobs. */
export function buildRunningItems(
  sessionIds: number[],
  jobs: Job[],
  sessions: ChatSession[],
): RunningTaskItem[] {
  const jobBySession = new Map<number, Job>()
  for (const job of jobs) {
    if (job.session_id != null) jobBySession.set(job.session_id, job)
  }
  const items: RunningTaskItem[] = []
  const seenJobs = new Set<number>()
  for (const job of jobs) {
    if (seenJobs.has(job.id)) continue
    seenJobs.add(job.id)
    items.push({
      kind: 'job',
      id: `job:${job.id}`,
      jobId: job.id,
      title: job.title || `Job #${job.id}`,
      status: job.status,
      engine: job.engine,
      project: job.project_slug,
    })
  }
  for (const sessionId of sessionIds) {
    if (jobBySession.has(sessionId)) continue
    const session = sessions.find(s => s.id === sessionId)
    items.push({
      kind: 'session',
      id: `session:${sessionId}`,
      sessionId,
      title: session?.title || `Chat #${sessionId}`,
      status: 'running',
    })
  }
  return items
}

export function RunningTasks({
  token,
  sessions = [],
  onOpenJob,
  onOpenSession,
  onOpenTasks,
}: {
  token: string
  sessions?: ChatSession[]
  onOpenJob?: (id: number, engine?: string) => void
  onOpenSession?: (sessionId: number) => void
  onOpenTasks?: () => void
}) {
  const [sessionIds, setSessionIds] = React.useState<number[]>([])
  const [jobs, setJobs] = React.useState<Job[]>([])
  const [open, setOpen] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState('')
  const root = React.useRef<HTMLDivElement>(null)

  const load = React.useCallback(async () => {
    try {
      const [runs, runningJobs] = await Promise.all([
        activeRuns(token),
        listJobs(token, { status: 'running', limit: 50 }),
      ])
      setSessionIds(runs.session_ids || [])
      setJobs(runningJobs.items || [])
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [token])

  React.useEffect(() => {
    void load()
    const id = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(id)
  }, [load])

  React.useEffect(() => {
    if (!open) return
    const dismiss = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', dismiss)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('mousedown', dismiss)
      window.removeEventListener('keydown', key)
    }
  }, [open])

  const items = React.useMemo(
    () => buildRunningItems(sessionIds, jobs, sessions),
    [sessionIds, jobs, sessions],
  )
  const count = items.length

  const go = (item: RunningTaskItem) => {
    setOpen(false)
    if (item.kind === 'job') {
      onOpenJob?.(item.jobId, item.engine)
      return
    }
    onOpenSession?.(item.sessionId)
  }

  return (
    <div className="running-tasks" ref={root}>
      <button
        type="button"
        className={`attention-trigger running-trigger ${open ? 'active' : ''} ${count > 0 ? 'has-work' : ''}`}
        onClick={() => setOpen(value => !value)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`${count} running task${count === 1 ? '' : 's'}`}
        title={count > 0 ? `${count} running` : 'No running tasks'}
      >
        <IconActivity size={15} />
        {count > 0 && <b>{count > 99 ? '99+' : count}</b>}
      </button>
      {open && (
        <section className="attention-popover running-popover" role="dialog" aria-modal="false" aria-label="Running tasks">
          <header>
            <div>
              <span className="eyebrow">In flight</span>
              <h2>Running</h2>
            </div>
            <div className="running-popover-actions">
              {onOpenTasks && (
                <button type="button" className="text-button" onClick={() => { setOpen(false); onOpenTasks() }}>
                  Tasks
                </button>
              )}
              <button type="button" className="text-button" disabled={loading} onClick={() => void load()}>
                {loading ? 'Refreshing…' : 'Refresh'}
              </button>
            </div>
          </header>
          {error && (
            <div className="attention-error" role="alert">
              <strong>Running list could not update</strong>
              <p>{error}</p>
              <button type="button" onClick={() => void load()}>Try again</button>
            </div>
          )}
          {loading && !items.length ? (
            <div className="attention-state" role="status">
              <span className="ui-spinner" /> Loading running work…
            </div>
          ) : !items.length ? (
            <div className="attention-state">
              <strong>Nothing is running</strong>
              <p>Active chat runs and tasks will appear here while they work.</p>
            </div>
          ) : (
            <ul className="attention-list">
              {items.map(item => (
                <li key={item.id}>
                  <button type="button" className="attention-main" onClick={() => go(item)}>
                    <span>{item.kind === 'job' ? 'Task' : 'Chat'}</span>
                    <strong>{item.title}</strong>
                    <small>
                      {statusLabel(item.status)}
                      {item.kind === 'job' && item.project ? ` · ${item.project}` : ''}
                      {' · '}
                      {item.kind === 'job' ? 'Open workspace' : 'Open chat'}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}

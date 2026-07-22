import React from 'react'
import type { AppFeatures, GraphJob, Job, JobStatus, JobStep, Profile, Project } from '../types'
import { listJobs } from '../api/jobs'
import { listGraphJobs, saveGraphTemplate } from '../api/graph'
import { GraphCanvas } from '../components/workflows/GraphCanvas'
import { SaveTemplateModal } from '../components/workflows/SaveTemplateModal'
import { orderedPlanJobs, planBranches, planProgress, targetBadge } from '../components/tasks/planProjection'
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
const StatusPill = ({ status }: { status: JobStatus | JobStep['status'] | string }) => <span className={`job-pill ${status}`}>{status}</span>
const BOARD: { key: JobStatus; label: string }[] = [
  { key: 'queued', label: 'Queued' },
  { key: 'running', label: 'Running' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' },
]
const STATUS_FILTERS: (JobStatus | 'all')[] = ['all', 'queued', 'running', 'review', 'done', 'failed', 'cancelled']

// Tasks = plans + their jobs (T2). A classic one-step task and a sliced plan are
// the same idea at different sizes, so they share one screen: plan rows expand
// into their ordered job list; the ones that branch also offer the canvas.
type Row =
  | { kind: 'task'; id: string; created: string; job: Job }
  | { kind: 'plan'; id: string; created: string; plan: GraphJob }

/** The compact chip saying where a job works, plus the repo marker (T1 tags). */
function TargetChip({ node }: { node: ReturnType<typeof orderedPlanJobs>[number]['node'] }) {
  const badge = targetBadge(node)
  if (!badge) return null
  const kind = node.target_ambiguous ? 'open' : node.touches_repo ? 'repo' : 'ops'
  const title = node.target_ambiguous
    ? node.target_question || 'This job still needs a work area.'
    : node.touches_repo
      ? `Works in the repo (${node.target}) — runs in an isolated copy you review before it lands`
      : 'Ops work — notes, reports, files in the project'
  return <span className={`plan-target is-${kind}`} title={title}>
    {node.touches_repo && <span className="plan-repo-mark" aria-hidden="true">⎇</span>}
    {badge}
  </span>
}

function PlanJobs({ plan, profiles, onOpenPlan }: {
  plan: GraphJob
  profiles: Profile[]
  onOpenPlan: (jobId: number) => void
}) {
  const branches = React.useMemo(() => planBranches(plan.graph), [plan.graph])
  // Two projections, one plan: the list is the default read; the canvas is the
  // same object drawn with its dependencies, offered only when they branch.
  const [projection, setProjection] = React.useState<'list' | 'graph'>('list')
  const rows = React.useMemo(() => orderedPlanJobs(plan), [plan])

  return <div className="plan-jobs">
    {branches && <div className="seg sm plan-projection">
      <button className={projection === 'list' ? 'active' : ''} onClick={() => setProjection('list')}>List</button>
      <button className={projection === 'graph' ? 'active' : ''} onClick={() => setProjection('graph')}>Graph</button>
    </div>}
    {projection === 'graph' && branches
      ? <div className="plan-canvas">
          <GraphCanvas
            job={plan}
            plan={plan.graph}
            profiles={profiles}
            selectedId={null}
            onSelect={() => onOpenPlan(plan.id)}
            onDeselect={() => undefined}
            editable={false}
            onMoveNode={() => undefined}
            onConnect={() => undefined}
            onDisconnect={() => undefined}
            onAddNode={() => undefined}
            onAddTrigger={() => undefined}
            hasTrigger={plan.graph.nodes.some(node => node.type === 'trigger')}
          />
        </div>
      : <ol className="plan-job-list">
          {rows.map(row => <li className="plan-job-row" key={row.node.id}>
            <span className="plan-job-name">{row.node.name}</span>
            <TargetChip node={row.node} />
            {row.error && <span className="plan-job-error" title={row.error}>!</span>}
            <StatusPill status={row.status} />
          </li>)}
        </ol>}
  </div>
}

export function ActivityScreen({ token, activeProject, features, profiles, onOpenTask, onOpenPlan }: {
  token: string
  activeProject: Project | null
  features: AppFeatures
  profiles: Profile[]
  onOpenTask: (jobId: number) => void
  /** Opens a plan where it can be acted on — the Workflows canvas. */
  onOpenPlan: (jobId: number) => void
}) {
  const [mode, setMode] = React.useState<'list' | 'board' | 'review'>('list')
  const [statusFilter, setStatusFilter] = React.useState<JobStatus | 'all'>('all')
  const [includeArchived, setIncludeArchived] = React.useState(false)
  const [items, setItems] = React.useState<Job[]>([])
  const [plans, setPlans] = React.useState<GraphJob[]>([])
  const [expanded, setExpanded] = React.useState<Set<number>>(() => new Set())
  const [savingPlan, setSavingPlan] = React.useState<GraphJob | null>(null)
  const [savingBusy, setSavingBusy] = React.useState(false)
  const [notice, setNotice] = React.useState('')
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
      const [page, planBody] = await Promise.all([
        listJobs(token, { status: effectiveStatus, project_slug: activeProject?.slug, include_archived: mode === 'list' ? includeArchived : false, limit: mode === 'board' ? 100 : PAGE, offset: nextOffset }),
        // Plans live on the graph engine; with the feature off the endpoint is
        // gated, so this screen simply shows classic tasks — exactly as before.
        features.workflowGraph ? listGraphJobs(token, activeProject?.slug) : Promise.resolve({ items: [] as GraphJob[] }),
      ])
      if (!mountedRef.current || seq !== loadSeq.current) return
      setError('')
      setTotal(page.total)
      setItems(current => append ? [...current, ...page.items] : page.items)
      setPlans(planBody.items)
    } catch (reason) {
      if (mountedRef.current && seq === loadSeq.current) setError(String(reason))
    }
  }, [token, effectiveStatus, activeProject?.slug, includeArchived, mode, features.workflowGraph])

  React.useEffect(() => { setOffset(0); void load(0, false) }, [load])
  const hasActiveJobs = items.some(job => job.status === 'queued' || job.status === 'running')
    || plans.some(plan => plan.status === 'queued' || plan.status === 'running')
  usePolling(() => load(0, false), 2500, { enabled: mode !== 'review' && hasActiveJobs, immediate: false })

  const toggleExpanded = (planId: number) => setExpanded(current => {
    const next = new Set(current)
    if (next.has(planId)) next.delete(planId)
    else next.add(planId)
    return next
  })

  async function saveRecipe(meta: { name: string; description: string; category: string; inputs: import('../types').WorkflowInput[] }) {
    if (!savingPlan || savingBusy) return
    setSavingBusy(true)
    setError('')
    try {
      const template = await saveGraphTemplate(token, savingPlan.id, meta)
      if (!mountedRef.current) return
      setSavingPlan(null)
      setNotice(`Saved “${template.name}” as a Recipe — run or schedule it from Workflows.`)
    } catch (cause) {
      if (mountedRef.current) setError(String(cause))
    } finally {
      if (mountedRef.current) setSavingBusy(false)
    }
  }

  const visiblePlans = plans.filter(plan =>
    mode === 'review' ? plan.status === 'review'
      : statusFilter === 'all' || plan.status === statusFilter)
  const rows: Row[] = [
    ...visiblePlans.map(plan => ({ kind: 'plan' as const, id: `plan-${plan.id}`, created: plan.created_at ?? '', plan })),
    ...items.map(job => ({ kind: 'task' as const, id: `task-${job.id}`, created: job.created_at ?? '', job })),
  ].sort((left, right) => right.created.localeCompare(left.created))

  const planCell = (plan: GraphJob) => <>
    {plan.title}
    <span className="job-pill plan">plan</span>
    {plan.graph.nodes.some(node => node.target_ambiguous) && <span className="plan-target is-open" title="A job in this plan still needs a work area before it can start.">where?</span>}
    {plan.worktree && <span className="plan-target is-repo" title={`Repo plan — branch ${plan.worktree.branch} (${plan.worktree.status})`}><span className="plan-repo-mark" aria-hidden="true">⎇</span>{plan.worktree.status}</span>}
  </>

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
    {notice && <div className="graph-notice">{notice}</div>}

    {mode === 'board'
      ? <div className="kanban">{BOARD.map(column => {
          const columnPlans = visiblePlans.filter(plan => plan.status === column.key)
          const columnItems = items.filter(job => job.status === column.key)
          return <div className="kanban-col" key={column.key}>
            <div className="kanban-col-head"><span>{column.label}</span><span className="kanban-count">{columnPlans.length + columnItems.length}</span></div>
            <div className="kanban-cards">
              {columnPlans.map((plan, index) => <button type="button" className="kanban-card stagger-item" style={{ ['--i' as string]: index } as React.CSSProperties} key={`plan-${plan.id}`} onClick={() => onOpenPlan(plan.id)}>
                <strong>{plan.title}<span className="job-pill plan">plan</span></strong>
                <small>{planProgress(plan)} jobs · {relTime(plan.created_at)}</small>
              </button>)}
              {columnItems.map((job, index) => <button type="button" className="kanban-card stagger-item" style={{ ['--i' as string]: columnPlans.length + index } as React.CSSProperties} key={job.id} onClick={() => onOpenTask(job.id)}>
                <strong>{job.title}{job.schedule_id != null && <span className="job-pill scheduled">scheduled</span>}</strong>
                <small>{job.workflow_id ? `${progress(job)} steps` : 'Task'} · {relTime(job.created_at)}</small>
              </button>)}
            </div>
          </div>
        })}</div>
      : <div className="job-list">
          {rows.length === 0
            ? <div className="placeholder-view"><div className="assistant-bubble compact"><p className="muted">{mode === 'review' ? 'Nothing waiting for review.' : 'No tasks yet. Sliced plans and one-off tasks land here.'}</p></div></div>
            : <>
              <div className="job-row job-row-head">
                <span className="jr-title">Task</span><span className="jr-wf">Type</span><span className="jr-status">Status</span><span className="jr-prog">Jobs</span><span className="jr-time">Created</span>
              </div>
              {rows.map((row, index) => row.kind === 'task'
                ? <button className="job-row stagger-item" style={{ ['--i' as string]: index } as React.CSSProperties} key={row.id} onClick={() => onOpenTask(row.job.id)}>
                    <span className="jr-title">{row.job.title}{row.job.schedule_id != null && <span className="job-pill scheduled">scheduled</span>}</span>
                    <span className="jr-wf muted">{row.job.workflow_id ? (row.job.schedule_id != null ? 'Scheduled' : 'Workflow') : 'Task'}</span>
                    <span className="jr-status"><StatusPill status={row.job.status} /></span>
                    <span className="jr-prog muted">{progress(row.job)}</span>
                    <span className="jr-time muted">{relTime(row.job.created_at)}</span>
                  </button>
                : <div className={`plan-row stagger-item${expanded.has(row.plan.id) ? ' open' : ''}`} style={{ ['--i' as string]: index } as React.CSSProperties} key={row.id}>
                    <button className="job-row plan-row-head" aria-expanded={expanded.has(row.plan.id)} onClick={() => toggleExpanded(row.plan.id)}>
                      <span className="jr-title"><span className={`chevron${expanded.has(row.plan.id) ? ' open' : ''}`}>▸</span>{planCell(row.plan)}</span>
                      <span className="jr-wf muted">Plan</span>
                      <span className="jr-status"><StatusPill status={row.plan.status} /></span>
                      <span className="jr-prog muted">{planProgress(row.plan)}</span>
                      <span className="jr-time muted">{relTime(row.plan.created_at)}</span>
                    </button>
                    {expanded.has(row.plan.id) && <div className="plan-detail">
                      <PlanJobs plan={row.plan} profiles={profiles} onOpenPlan={onOpenPlan} />
                      <div className="plan-actions">
                        <button className="ghost-button" onClick={() => onOpenPlan(row.plan.id)}>Open plan</button>
                        <button className="ghost-button" onClick={() => setSavingPlan(row.plan)}>Save as Recipe</button>
                      </div>
                    </div>}
                  </div>)}
              {mode === 'list' && items.length < total && <div className="job-more"><button className="ghost-button" onClick={() => { const next = offset + PAGE; setOffset(next); void load(next, true) }}>Load more ({items.length}/{total})</button></div>}
            </>}
        </div>}

    {savingPlan && <SaveTemplateModal
      title={savingPlan.title}
      busy={savingBusy}
      onCancel={() => setSavingPlan(null)}
      onSave={meta => void saveRecipe(meta)}
    />}
  </section>
}

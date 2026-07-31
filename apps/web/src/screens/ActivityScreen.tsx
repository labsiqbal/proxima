import React from 'react'
import type { AppFeatures, GraphJob, Job, JobStatus, JobStep, Profile, Project } from '../types'
import { listJobs } from '../api/jobs'
import { approveGraphJob, listGraphJobs, saveGraphTemplate } from '../api/graph'
import { GraphCanvas } from '../components/workflows/GraphCanvas'
import { SaveTemplateModal } from '../components/workflows/SaveTemplateModal'
import { ChangesReview } from '../components/tasks/ChangesReview'
import { worktreeStateLabel } from '../components/tasks/diff'
import { lastOutputLine, orderedPlanJobs, planBranches, planMergeBlockedNote, planProgress, targetBadge } from '../components/tasks/planProjection'
import { usePolling } from '../hooks/usePolling'
import { formatRunAge, projectRun } from '../lib/runProjection'

const PAGE = 25
const runAge = (job: GraphJob | Job): string => formatRunAge(projectRun(job), job.created_at)
const progress = (job: Job) => { const total = job.steps_state.length; const done = job.steps_state.filter(step => step.status === 'done').length; return total ? `${done}/${total}` : '—' }
const StatusPill = ({ status }: { status: JobStatus | JobStep['status'] | string }) => <span className={`job-pill ${status}`} aria-hidden="true">{' '}{status}</span>
/** Board columns keep the happy path left-to-right and park Failed at the end so owners still see failures without hunting List → Failed. */
export const TASK_BOARD_COLUMNS: { key: JobStatus; label: string }[] = [
  { key: 'queued', label: 'Queued' },
  { key: 'running', label: 'Running' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' },
  { key: 'failed', label: 'Failed' },
]
const BOARD = TASK_BOARD_COLUMNS
const STATUS_FILTERS: (JobStatus | 'all')[] = ['all', 'queued', 'running', 'review', 'done', 'failed', 'cancelled']

/** Spaced accessible name for a Tasks board plan card (title · Plan · progress · age). */
export function boardPlanCardAriaLabel(plan: Pick<GraphJob, 'title'>, progressLabel: string, age: string): string {
  return [plan.title, 'Plan', `${progressLabel} jobs`, age].filter(Boolean).join(' · ')
}

/** Spaced accessible name for a Tasks board classic-task card. */
export function boardTaskCardAriaLabel(
  job: Pick<Job, 'title' | 'schedule_id' | 'workflow_id'>,
  detail: string,
  age: string,
): string {
  const kind = job.schedule_id != null ? 'Scheduled' : job.workflow_id ? 'Workflow' : 'Task'
  return [job.title, kind, detail, age].filter(Boolean).join(' · ')
}

/** Spaced accessible name for a Tasks list plan row. */
export function listPlanRowAriaLabel(
  plan: Pick<GraphJob, 'title' | 'status' | 'worktree'>,
  status: string,
  progressLabel: string,
  age: string,
): string {
  const parts = [plan.title, 'Plan', status]
  if (plan.worktree) parts.push(worktreeStateLabel(plan.worktree.status))
  parts.push(progressLabel, age)
  return parts.filter(Boolean).join(' · ')
}

/** Spaced accessible name for a Tasks list classic-task row. */
export function listTaskRowAriaLabel(
  job: Pick<Job, 'title' | 'schedule_id' | 'workflow_id'>,
  status: string,
  progressLabel: string,
  age: string,
): string {
  const kind = job.schedule_id != null ? 'Scheduled' : job.workflow_id ? 'Workflow' : 'Task'
  return [job.title, kind, status, progressLabel, age].filter(Boolean).join(' · ')
}

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
      : 'Works outside the repo — notes, reports, files in the project'
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
            onAddScript={() => undefined}
            onAddTrigger={() => undefined}
            hasTrigger={plan.graph.nodes.some(node => node.type === 'trigger')}
          />
        </div>
      : <ol className="plan-job-list">
          {rows.map(row => <li className="plan-job-row" key={row.node.id}>
            <span className="plan-job-name">{row.node.name}</span>
            {/* A script step is a different kind of thing than an agent job —
                say what it runs, and once it ran, what it last printed (T6). */}
            {row.node.type === 'script' && <span className="plan-script" title="A saved script runs this step — no AI involved">⚡ scripts/{row.node.command}</span>}
            <TargetChip node={row.node} />
            {row.node.type === 'script' && lastOutputLine(row.output) && <span className="plan-script-out" title={lastOutputLine(row.output) ?? undefined}>{lastOutputLine(row.output)}</span>}
            {/* Decision-hold (slice 12): this job is waiting on the owner's
                answer — the rest of the plan may well still be running. */}
            {row.question && <span className="plan-decision" title={row.question}>needs your answer</span>}
            {row.error && <span className="plan-job-error" title={row.error}>!</span>}
            <StatusPill status={row.status} />
          </li>)}
        </ol>}
  </div>
}

export function ActivityScreen({ token, activeProject, features, profiles, onOpenTask, onOpenPlan, onNewTask }: {
  token: string
  activeProject: Project | null
  features: AppFeatures
  profiles: Profile[]
  onOpenTask: (jobId: number) => void
  /** Opens a plan where it can be acted on — the Workflows canvas. */
  onOpenPlan: (jobId: number) => void
  /** Opens the New task launcher — this screen is its home in the nav. */
  onNewTask?: () => void
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
  const hasActiveJobs = items.some(job => ['queued', 'running'].includes(projectRun(job).status))
    || plans.some(plan => ['queued', 'running'].includes(projectRun(plan).status))
  usePolling(() => load(0, false), 2500, { enabled: mode !== 'review' && hasActiveJobs, immediate: false })

  const toggleExpanded = (planId: number) => setExpanded(current => {
    const next = new Set(current)
    if (next.has(planId)) next.delete(planId)
    else next.add(planId)
    return next
  })

  async function saveRecipe(meta: { name: string; description: string; category: string }) {
    if (!savingPlan || savingBusy) return
    setSavingBusy(true)
    setError('')
    try {
      const template = await saveGraphTemplate(token, savingPlan.id, meta)
      if (!mountedRef.current) return
      setSavingPlan(null)
      setNotice(`Saved “${template.name}” as a Workflow — run or schedule it from Workflows.`)
    } catch (cause) {
      if (mountedRef.current) setError(String(cause))
    } finally {
      if (mountedRef.current) setSavingBusy(false)
    }
  }

  const visiblePlans = plans.filter(plan =>
    mode === 'review' ? projectRun(plan).status === 'review'
      : statusFilter === 'all' || projectRun(plan).status === statusFilter)
  const rows: Row[] = [
    ...visiblePlans.map(plan => ({ kind: 'plan' as const, id: `plan-${plan.id}`, created: plan.created_at ?? '', plan })),
    ...items.map(job => ({ kind: 'task' as const, id: `task-${job.id}`, created: job.created_at ?? '', job })),
  ].sort((left, right) => right.created.localeCompare(left.created))

  const planCell = (plan: GraphJob) => <>
    {plan.title}
    <span className="job-pill plan">{' '}plan</span>
    {plan.graph.nodes.some(node => node.target_ambiguous) && <span className="plan-target is-open" title="A job in this plan still needs a work area before it can start.">{' '}where?</span>}
    {plan.worktree && <span className="plan-target is-repo" title="This plan works in the repo — it ran in an isolated copy whose changes you review before they land."><span className="plan-repo-mark" aria-hidden="true">⎇</span>{' '}{worktreeStateLabel(plan.worktree.status)}</span>}
  </>

  return <section className="tasks-view">
    <div className="tasks-head">
      <div><h1>Tasks</h1></div>
      <div className="seg sm">
        <button className={mode === 'list' ? 'active' : ''} onClick={() => setMode('list')}>List</button>
        <button className={mode === 'board' ? 'active' : ''} onClick={() => setMode('board')}>Board</button>
        <button className={mode === 'review' ? 'active' : ''} onClick={() => setMode('review')}>Review</button>
      </div>
      {mode === 'list' && <>
        <div className="seg sm job-filter">{STATUS_FILTERS.map(status => <button key={status} className={statusFilter === status ? 'active' : ''} onClick={() => setStatusFilter(status)}>{status}</button>)}</div>
        <label className="job-archived-toggle"><input type="checkbox" checked={includeArchived} onChange={event => setIncludeArchived(event.target.checked)} /> Archived</label>
      </>}
      {onNewTask && <button className="primary-button" onClick={onNewTask}>+ New task</button>}
    </div>
    {error && <div className="error-bar">{error}</div>}
    {notice && <div className="graph-notice">{notice}</div>}

    {mode === 'board'
      ? <div className="kanban">{BOARD.map(column => {
          const columnPlans = visiblePlans.filter(plan => projectRun(plan).status === column.key)
          const columnItems = items.filter(job => projectRun(job).status === column.key)
          return <div className="kanban-col" key={column.key}>
            <div className="kanban-col-head"><span>{column.label}</span><span className="kanban-count">{columnPlans.length + columnItems.length}</span></div>
            <div className="kanban-cards">
              {columnPlans.map((plan, index) => {
                const age = runAge(plan)
                const prog = planProgress(plan)
                return <button type="button" className="kanban-card stagger-item" style={{ ['--i' as string]: index } as React.CSSProperties} key={`plan-${plan.id}`} aria-label={boardPlanCardAriaLabel(plan, prog, age)} onClick={() => onOpenPlan(plan.id)}>
                  <strong aria-hidden="true">{plan.title}<span className="job-pill plan">{' '}plan</span></strong>
                  <small aria-hidden="true">{prog} jobs · {age}</small>
                </button>
              })}
              {columnItems.map((job, index) => {
                const age = runAge(job)
                const detail = job.workflow_id ? `${progress(job)} steps` : 'Task'
                return <button type="button" className="kanban-card stagger-item" style={{ ['--i' as string]: columnPlans.length + index } as React.CSSProperties} key={job.id} aria-label={boardTaskCardAriaLabel(job, detail, age)} onClick={() => onOpenTask(job.id)}>
                  <strong aria-hidden="true">{job.title}{job.schedule_id != null && <span className="job-pill scheduled">{' '}scheduled</span>}</strong>
                  <small aria-hidden="true">{detail} · {age}</small>
                </button>
              })}
            </div>
          </div>
        })}</div>
      : <div className="job-list">
          {rows.length === 0
            ? <div className="placeholder-view teaching-empty" data-testid="teaching-empty">
                {mode === 'review' ? (
                  <>
                    <h3 className="teaching-empty-title">Nothing waiting for review</h3>
                    <p className="teaching-empty-lead">{activeProject
                      ? `No review items in ${activeProject.name} right now.`
                      : 'No review items right now.'}</p>
                    <ul className="teaching-empty-caps" aria-label="What you can do here">
                      <li>Approve or reject changes when a job reaches Review</li>
                      <li>Open a task from Attention when something needs you</li>
                    </ul>
                  </>
                ) : (
                  <>
                    <h3 className="teaching-empty-title">No tasks yet</h3>
                    <p className="teaching-empty-lead">
                      Tasks is where durable work runs and gets reviewed. Ad-hoc tasks, plans from Chat, Master jobs, and scheduled workflow runs all land here{activeProject ? ` for ${activeProject.name}` : ''}.
                    </p>
                    <ul className="teaching-empty-caps" aria-label="What you can do here">
                      <li>Watch queued, running, and review work in one list or board</li>
                      <li>Open a task for live progress, changes, and deliverables</li>
                      <li>Save a good plan as a Workflow when the pattern is worth keeping</li>
                    </ul>
                    <ol className="teaching-empty-steps" aria-label="Getting started">
                      <li><span className="teaching-empty-step-n" aria-hidden="true">1</span><span>Start from Chat (slice a plan) or press <strong>New task</strong></span></li>
                      <li><span className="teaching-empty-step-n" aria-hidden="true">2</span><span>Return here anytime — leave/return keeps this list mounted</span></li>
                      <li><span className="teaching-empty-step-n" aria-hidden="true">3</span><span>When a run finishes, open Archive for durable deliverables</span></li>
                    </ol>
                    {onNewTask && (
                      <button type="button" className="primary-button teaching-empty-cta" onClick={onNewTask}>New task</button>
                    )}
                  </>
                )}
              </div>
            : <>
              <div className="job-row job-row-head">
                <span className="jr-title">Task</span><span className="jr-wf">Type</span><span className="jr-status">Status</span><span className="jr-prog">Jobs</span><span className="jr-time">Created</span>
              </div>
              {rows.map((row, index) => row.kind === 'task'
                ? <button className="job-row stagger-item" style={{ ['--i' as string]: index } as React.CSSProperties} key={row.id} aria-label={listTaskRowAriaLabel(row.job, projectRun(row.job).status, progress(row.job), runAge(row.job))} onClick={() => onOpenTask(row.job.id)}>
                    <span className="jr-title" aria-hidden="true">{row.job.title}{row.job.schedule_id != null && <span className="job-pill scheduled">{' '}scheduled</span>}</span>
                    <span className="jr-wf muted" aria-hidden="true">{row.job.workflow_id ? (row.job.schedule_id != null ? 'Scheduled' : 'Workflow') : 'Task'}</span>
                    <span className="jr-status" aria-hidden="true"><StatusPill status={projectRun(row.job).status} /></span>
                    <span className="jr-prog muted" aria-hidden="true">{progress(row.job)}</span>
                    <span className="jr-time muted" aria-hidden="true">{runAge(row.job)}</span>
                  </button>
                : <div className={`plan-row stagger-item${expanded.has(row.plan.id) ? ' open' : ''}`} style={{ ['--i' as string]: index } as React.CSSProperties} key={row.id}>
                    <button className="job-row plan-row-head" aria-expanded={expanded.has(row.plan.id)} aria-label={listPlanRowAriaLabel(row.plan, projectRun(row.plan).status, planProgress(row.plan), runAge(row.plan))} onClick={() => toggleExpanded(row.plan.id)}>
                      <span className="jr-title" aria-hidden="true"><span className={`chevron${expanded.has(row.plan.id) ? ' open' : ''}`} aria-hidden="true">▸</span>{planCell(row.plan)}</span>
                      <span className="jr-wf muted" aria-hidden="true">Plan</span>
                      <span className="jr-status" aria-hidden="true"><StatusPill status={projectRun(row.plan).status} /></span>
                      <span className="jr-prog muted" aria-hidden="true">{planProgress(row.plan)}</span>
                      <span className="jr-time muted" aria-hidden="true">{runAge(row.plan)}</span>
                    </button>
                    {expanded.has(row.plan.id) && <div className="plan-detail">
                      <PlanJobs plan={row.plan} profiles={profiles} onOpenPlan={onOpenPlan} />
                      {row.plan.status === 'failed' && row.plan.rejected_reason && <p className="changes-note is-failed" role="status">{row.plan.rejected_reason}</p>}
                      {/* The repo-plan review surface (slice 4): the diff lives in
                          this EXPANDING row (T4 — no side panel, no popup), and the
                          plan's final approve here is the local merge point. */}
                      {row.plan.worktree && <ChangesReview
                        token={token}
                        jobId={row.plan.id}
                        jobStatus={row.plan.status}
                        worktree={row.plan.worktree}
                        rejectedReason={row.plan.rejected_reason}
                        canDecide={row.plan.node_states.every(node => node.status === 'done')}
                        decideBlockedNote={planMergeBlockedNote(row.plan)}
                        onApprove={() => approveGraphJob(token, row.plan.id)}
                        onChanged={() => void load(0, false)}
                      />}
                      {/* No-worktree plans (ops/text-only, or a repo run that never
                          bound an area) still need a Tasks-row final approve so the
                          owner is not forced into the canvas just to close review. */}
                      {!row.plan.worktree && row.plan.status === 'review' && row.plan.node_states.length > 0 && row.plan.node_states.every(node => node.status === 'done') && <div className="changes-review plan-final-approve">
                        <p className="changes-note">All steps finished. Approve to mark this plan done — no code changes to merge.</p>
                        <button className="primary-button" onClick={() => void approveGraphJob(token, row.plan.id).then(() => void load(0, false))}>Approve final result</button>
                      </div>}
                      <div className="plan-actions">
                        <button className="ghost-button" onClick={() => onOpenPlan(row.plan.id)}>Open plan</button>
                        <button className="ghost-button" onClick={() => setSavingPlan(row.plan)}>Save as Workflow</button>
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

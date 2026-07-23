import React from 'react'
import type { Job, JobStatus, JobStep } from '../types'
import { getJob, approveJob, deleteJob } from '../api/jobs'
import { ChangesReview } from '../components/tasks/ChangesReview'
import { SatpamCard } from '../components/tasks/SatpamCard'
import { MessageContent } from '../components/chat/MessageContent'
import { confirmDialog } from '../components/ui/Dialog'
import { IconTrash } from '../components/shell/icons'
import { BackButton } from '../components/ui/BackButton'
import type { Artifact } from '../api/files'
import { stripQuestionForms } from '../components/chat/questionForm'
import { usePolling } from '../hooks/usePolling'

const ART_ICON: Record<string, string> = { design: '🎨', image: '🖼', app: '▶', page: '🌐', doc: '📄', file: '📎' }
const StatusPill = ({ status }: { status: JobStatus | JobStep['status'] }) => <span className={`job-pill ${status}`}>{status}</span>

export function TaskWorkspace({ token, jobId, onBack, onChanged, designStudioEnabled = false, onOpenDesign, onOpenFile }: { token: string; jobId: number; onBack: () => void; onChanged?: () => void; designStudioEnabled?: boolean; onOpenDesign?: (id: string) => void; onOpenFile?: (slug: string, path: string) => void }) {
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
  usePolling(load, 1500, { enabled: job?.status === 'running', immediate: false })

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
    if (!(await confirmDialog({ title: 'Delete this task?', message: 'The task record and its agent thread are removed. Any designs or files it produced are kept.', confirmLabel: 'Delete', danger: true }))) return
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

  if (!job) return <div className="job-detail"><div className="task-detail-head"><BackButton label="Tasks" onClick={onBack} /></div>{error ? <div className="error-bar">{error}</div> : <p className="muted task-workspace-state">Loading…</p>}</div>

  const steps = job.steps_state
  if (!steps.length) return <div className="job-detail"><div className="task-detail-head"><BackButton label="Tasks" onClick={onBack} /><strong className="task-title">{job.title}</strong><StatusPill status={job.status} /></div><p className="muted task-workspace-state">This task has no steps.</p></div>
  const cur = steps[Math.min(sel, steps.length - 1)]
  const onReviewStep = isMidGate && sel === job.current_step_idx

  return <div className="job-detail">
    <div className="task-detail-head">
      <BackButton label="Tasks" onClick={onBack} />
      <strong className="task-title" title={job.title}>{job.title}</strong>
      <StatusPill status={job.status} />
      <button className="row-action danger jfd-delete" title="Delete task" aria-label="Delete task" onClick={() => void remove()} disabled={!!busyAction}><IconTrash size={15} /></button>
    </div>
    {isReview && (isMidGate
      ? <div className="task-review-bar wf-review-mid">
          <span>⏸ Paused for your review — step {job.current_step_idx + 1}{reviewStep ? `: ${reviewStep.name}` : ''}.</span>
          <button className="primary-button" onClick={() => void approve(reviewStep && edited.trim() !== (reviewStep.output_summary || '') ? { edited_output: edited } : undefined)} disabled={!!busyAction}>{busyAction === 'approve' ? 'Approving…' : '✓ Approve & continue'}</button>
        </div>
      : job.worktree
        // Repo job (slice 4): the verdict lives with the changes below — the
        // final approve here is also the local merge, so the two must be one act.
        ? <div className="task-review-bar">
            <span>✅ Ready for review — check the changes below.</span>
          </div>
        : <div className="task-review-bar">
            <span>✅ Ready for review.</span>
            <button className="primary-button" onClick={() => void approve()} disabled={!!busyAction}>{busyAction === 'approve' ? 'Approving…' : '✓ Approve → Done'}</button>
          </div>)}
    {error && <div className="error-bar">{error}</div>}
    <SatpamCard token={token} jobId={job.id} interventions={job.satpam} jobStatus={job.status} onChanged={updated => { setJob(updated); onChanged?.() }} />
    {job.worktree && <ChangesReview
      token={token}
      jobId={job.id}
      jobStatus={job.status}
      worktree={job.worktree}
      rejectedReason={job.rejected_reason}
      canDecide={isReview && !isMidGate}
      onApprove={() => approveJob(token, jobId)}
      onChanged={() => { void load(); onChanged?.() }}
    />}

    <div className="job-flow-wrap">
      {/* Left: the flow diagram — each step a node on a connected spine, colored by state */}
      <div className="job-flow">
        {typeof job.input?.brief === 'string' && job.input.brief && <p className="job-flow-brief" title={job.input.brief}>{job.input.brief}</p>}
        {steps.map((s, i) => <button key={s.id || i} className={`flow-node st-${s.status} ${i === sel ? 'sel' : ''}`} onClick={() => setSel(i)}>
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

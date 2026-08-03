import React from 'react'
import {
  previewCheckpointRestore,
  restoreCheckpoint,
  setCheckpointPinned,
  type MasterCheckpoint,
  type MasterJob,
} from '../../api/master'
import { confirmDialog } from '../ui/Dialog'
import { useMasterState } from '../../master/MasterStateProvider'
import { MasterDecisionCard } from './MasterDecisionCard'

const WORK_STATES: {
  status: MasterJob['status']
  label: string
  shortLabel: string
}[] = [
  { status: 'queued', label: 'Queued', shortLabel: 'Queued' },
  { status: 'running', label: 'Running', shortLabel: 'Running' },
  { status: 'review', label: 'Review and attention', shortLabel: 'Review' },
  { status: 'done', label: 'Completed', shortLabel: 'Done' },
  { status: 'failed', label: 'Failed', shortLabel: 'Failed' },
]

function displayStatus(status: string) {
  if (status === 'review') return 'Needs review'
  if (status === 'done') return 'Completed'
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function MasterPanel({
  eyebrow,
  title,
  count,
  children,
}: {
  eyebrow: string
  title: string
  count: number
  children: React.ReactNode
}) {
  const titleId = `master-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-title`
  return (
    <details className="master-side-section" open>
      <summary className="master-section-head" aria-labelledby={titleId}>
        <span className="eyebrow">{eyebrow}</span>
        <strong id={titleId}>{title}</strong>
        <span className="master-count">{count}</span>
        <i className="master-section-chevron" aria-hidden="true" />
      </summary>
      <div className="master-side-panel-body">{children}</div>
    </details>
  )
}

export function formatCheckpointTime(value: string): string {
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime()) ? 'Unknown time' : timestamp.toLocaleString()
}

function CheckpointTimeline({
  token,
  checkpoints,
}: {
  token: string
  checkpoints: MasterCheckpoint[]
}) {
  const { actions } = useMasterState()
  const [busyId, setBusyId] = React.useState<number | null>(null)
  const [error, setError] = React.useState('')

  const restore = async (checkpoint: MasterCheckpoint) => {
    if (busyId != null) return
    setBusyId(checkpoint.id)
    setError('')
    try {
      const impact = await previewCheckpointRestore(token, checkpoint.job_id, checkpoint.id)
      const resetPaths = impact.git_refs
        .filter(ref => ref.restore_strategy === 'worktree_reset')
        .map(ref => ref.worktree_path)
        .filter(Boolean)
      const referencePaths = impact.git_refs
        .filter(ref => ref.restore_strategy !== 'worktree_reset')
        .map(ref => ref.repo_path)
        .filter(Boolean)
      const details = [
        `Database: ${impact.database_scope.join(', ')}`,
        resetPaths.length
          ? `Task worktrees to reset: ${resetPaths.join(', ')}`
          : 'Task worktrees to reset: none',
        referencePaths.length
          ? `Reference only: ${referencePaths.join(', ')}`
          : '',
        impact.conflicts.length
          ? `Blocked by: ${impact.conflicts.map(item => item.title).join(', ')}`
          : '',
      ].filter(Boolean).join('\n')
      if (!impact.can_restore) {
        setError(
          `Restore is blocked while ${impact.conflicts.map(item => item.title).join(', ')} is running.`,
        )
        return
      }
      const confirmed = await confirmDialog({
        title: `Restore "${impact.job_title}"?`,
        message: details,
        confirmLabel: 'Restore checkpoint',
        danger: true,
      })
      if (!confirmed) return
      await restoreCheckpoint(token, checkpoint.job_id, checkpoint.id)
      await actions.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusyId(null)
    }
  }

  const pin = async (checkpoint: MasterCheckpoint) => {
    if (busyId != null) return
    setBusyId(checkpoint.id)
    setError('')
    try {
      await setCheckpointPinned(
        token,
        checkpoint.job_id,
        checkpoint.id,
        !checkpoint.pinned,
      )
      await actions.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <MasterPanel eyebrow="Safety" title="Checkpoints" count={checkpoints.length}>
      {!checkpoints.length ? (
        <p className="master-zero">
          No checkpoints yet - one is taken before each Master Task starts.
        </p>
      ) : (
        <ol className="master-checkpoints">
          {checkpoints.map(checkpoint => (
            <li key={checkpoint.id}>
              <span className="checkpoint-line" aria-hidden="true" />
              <div>
                <strong>Task #{checkpoint.job_id}</strong>
                <small>
                  {formatCheckpointTime(checkpoint.created_at)}
                </small>
              </div>
              <div className="checkpoint-actions">
                <button
                  type="button"
                  className="text-button"
                  disabled={busyId != null}
                  onClick={() => void pin(checkpoint)}
                >
                  {checkpoint.pinned ? 'Unpin' : 'Pin'}
                </button>
                <button
                  type="button"
                  className="text-button"
                  disabled={busyId != null}
                  onClick={() => void restore(checkpoint)}
                >
                  {busyId === checkpoint.id ? 'Working...' : 'Restore'}
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {error && <p className="master-inline-error" role="alert">{error}</p>}
    </MasterPanel>
  )
}

export function MasterWorkPanel({
  token,
  onOpenJob,
}: {
  token: string
  onOpenJob: (id: number, engine?: string) => void
}) {
  const { desk, actions } = useMasterState()
  if (!desk) return null
  const decisions = desk.decisions || []
  const otherAttention = desk.attention.filter(
    item => !(item.kind === 'master_decision' && item.decision),
  )
  const visibleJobs = desk.jobs.filter(job =>
    WORK_STATES.some(state => state.status === job.desk_status),
  )
  const counts = Object.fromEntries(
    WORK_STATES.map(state => [
      state.status,
      desk.jobs.filter(job => job.desk_status === state.status).length,
    ]),
  )

  return (
    <aside className="master-side" aria-label="Master work panel">
      <MasterPanel eyebrow="Fleet work" title="Master Tasks" count={visibleJobs.length}>
        <div className="master-work-counts" aria-label="Task status summary">
          {WORK_STATES.map(state => (
            <span
              className={`master-work-count ${state.status}`}
              key={state.status}
              data-empty={counts[state.status] === 0 ? 'true' : 'false'}
            >
              <b>{counts[state.status]}</b>
              <small>{state.shortLabel}</small>
            </span>
          ))}
        </div>
        {!visibleJobs.length ? (
          <p className="master-zero">
            No delegated Tasks yet - what Master delegates stays here.
          </p>
        ) : (
          <div className="master-job-groups">
            {WORK_STATES.map(state => {
              const jobs = visibleJobs.filter(job => job.desk_status === state.status)
              if (!jobs.length) return null
              return (
                <section className="master-job-group" key={state.status} aria-label={state.label}>
                  <h3>{state.label}</h3>
                  <div className="master-job-list">
                    {jobs.map(job => (
                      <button
                        type="button"
                        className="master-job"
                        key={job.id}
                        onClick={() => onOpenJob(job.id, job.engine)}
                      >
                        <span className={`master-job-status ${job.desk_status}`} aria-hidden="true" />
                        <span>
                          <strong>{job.title}</strong>
                          <small>
                            {job.project_name || job.project_slug || 'Project unavailable'}
                            {' · '}
                            {displayStatus(job.desk_status)}
                          </small>
                        </span>
                        <span aria-hidden="true">›</span>
                      </button>
                    ))}
                  </div>
                </section>
              )
            })}
          </div>
        )}
      </MasterPanel>

      <MasterPanel
        eyebrow="Decisions"
        title="Needs your attention"
        count={decisions.length + otherAttention.length}
      >
        {!decisions.length && !otherAttention.length ? (
          <p className="master-zero">
            Nothing needs a decision right now.
          </p>
        ) : (
          <>
            {!!decisions.length && (
              <div className="master-decision-list">
                {decisions.map(decision => (
                  <MasterDecisionCard
                    key={decision.id}
                    token={token}
                    decision={decision}
                    compact
                    onChanged={() => actions.refresh()}
                    onOpenJob={onOpenJob}
                    onOpenMaster={originMessageId => {
                      actions.setHistory({ kind: 'roving' })
                      window.requestAnimationFrame(() => {
                        const target = originMessageId == null
                          ? null
                          : document.querySelector(
                            `[data-message-id="${originMessageId}"]`,
                          )
                        target?.scrollIntoView({
                          block: 'center',
                          behavior: 'smooth',
                        })
                      })
                    }}
                  />
                ))}
              </div>
            )}
            {!!otherAttention.length && (
              <ul className="master-needs-list">
                {otherAttention.map(item => (
                  <li key={item.id}>
                    {item.target.job_id != null ? (
                      <button
                        type="button"
                        onClick={() => onOpenJob(item.target.job_id!, item.target.engine)}
                      >
                        <strong>{item.title}</strong>
                        <small>Open linked Task</small>
                      </button>
                    ) : (
                      <div>
                        <strong>{item.title}</strong>
                        <small>Open the global Attention inbox for details.</small>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </MasterPanel>

      <CheckpointTimeline token={token} checkpoints={desk.checkpoints} />
    </aside>
  )
}

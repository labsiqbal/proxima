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
    <section className="master-side-section" aria-labelledby="master-checkpoint-title">
      <div className="master-section-head">
        <div>
          <span className="eyebrow">Safety</span>
          <h2 id="master-checkpoint-title">Checkpoints</h2>
        </div>
        <span className="master-count">{checkpoints.length}</span>
      </div>
      {!checkpoints.length ? (
        <div className="master-zero">
          <strong>No checkpoints yet</strong>
          <p>A scoped restore point is created before each Master Task starts.</p>
        </div>
      ) : (
        <ol className="master-checkpoints">
          {checkpoints.slice(0, 10).map(checkpoint => (
            <li key={checkpoint.id}>
              <span className="checkpoint-line" aria-hidden="true" />
              <div>
                <strong>Task #{checkpoint.job_id}</strong>
                <small>
                  {new Date(`${checkpoint.created_at.replace(' ', 'T')}Z`).toLocaleString()}
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
    </section>
  )
}

export function MasterWorkPanel({
  token,
  onOpenJob,
}: {
  token: string
  onOpenJob: (id: number, engine?: string) => void
}) {
  const { desk } = useMasterState()
  if (!desk) return null
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
      <section className="master-side-section master-work-section" aria-labelledby="master-work-title">
        <div className="master-section-head">
          <div>
            <span className="eyebrow">Fleet work</span>
            <h2 id="master-work-title">Master Tasks</h2>
          </div>
          <span className="master-count">{visibleJobs.length}</span>
        </div>
        <div className="master-work-counts" aria-label="Task status summary">
          {WORK_STATES.map(state => (
            <span className={`master-work-count ${state.status}`} key={state.status}>
              <b>{counts[state.status]}</b>
              <small>{state.shortLabel}</small>
            </span>
          ))}
        </div>
        {!visibleJobs.length ? (
          <div className="master-zero">
            <strong>No delegated Tasks</strong>
            <p>Tasks Master creates will stay visible here through completion or failure.</p>
          </div>
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
                            {job.project_name || job.project_slug || 'Container unavailable'}
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
      </section>

      <section className="master-side-section" aria-labelledby="master-needs-title">
        <div className="master-section-head">
          <div>
            <span className="eyebrow">Decisions</span>
            <h2 id="master-needs-title">Needs your attention</h2>
          </div>
          <span className="master-count">{desk.attention.length}</span>
        </div>
        {!desk.attention.length ? (
          <div className="master-zero">
            <strong>Nothing needs a decision</strong>
            <p>Review, permission, and Satpam escalations will appear here.</p>
          </div>
        ) : (
          <ul className="master-needs-list">
            {desk.attention.map(item => (
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
      </section>

      <CheckpointTimeline token={token} checkpoints={desk.checkpoints} />
    </aside>
  )
}

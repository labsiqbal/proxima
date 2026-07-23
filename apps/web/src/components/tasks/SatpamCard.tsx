import React from 'react'
import type { Job, JobStatus, SatpamIntervention } from '../../types'
import { approveSatpamRestart, dismissSatpamRestart } from '../../api/jobs'

// The satpam's owner-facing surface (slice 12, T10 #5: no silent
// interventions). Two jobs in one card stack:
// - a PENDING restart is the approval card for a repo job's restart-clean -
//   destructive (it discards the worktree), so the satpam queued it and the
//   verdict is the owner's;
// - everything else renders as the plain supervision timeline (steered /
//   restarted / escalated), so every automatic action stays auditable.
// Renders nothing for jobs the satpam never touched.

const ACTION_LABEL: Record<SatpamIntervention['action'], string> = {
  steer: 'Steered',
  restart: 'Restart',
  escalate: 'Escalated',
}
const ACTION_ICON: Record<SatpamIntervention['action'], string> = {
  steer: '🧭',
  restart: '↺',
  escalate: '⛔',
}

// Terminal job statuses: an escalate reason written while the plan was paused
// must not still read as a live next-action once the job has moved on.
const TERMINAL_JOB: ReadonlySet<JobStatus> = new Set(['done', 'cancelled', 'failed'])

/** Present-tense pause claims in stored escalate reasons become past-tense on terminal jobs. */
export function satpamReasonForDisplay(reason: string, jobStatus?: JobStatus): string {
  if (!jobStatus || !TERMINAL_JOB.has(jobStatus)) return reason
  const scrubbed = reason
    .replace(/\bThe plan is paused:\s*/gi, '')
    .replace(/\bthe plan is paused\b/gi, 'the plan was paused')
    .replace(/\bis paused\b/gi, 'was paused')
    .replace(/\s{2,}/g, ' ')
    .trim()
  // Re-capitalize the sentence that used to follow the stripped clause.
  return scrubbed.replace(/([.!?]\s+)([a-z])/g, (_m, lead: string, ch: string) => lead + ch.toUpperCase())
}

function historyLabel(i: SatpamIntervention, jobStatus?: JobStatus): string {
  const bits = [ACTION_LABEL[i.action]]
  if (i.status === 'dismissed') bits.push('(dismissed)')
  else if (i.action === 'escalate' && jobStatus && TERMINAL_JOB.has(jobStatus)) bits.push('(earlier)')
  return `${bits.join(' ')} · ${i.detection}`
}

export function SatpamCard({ token, jobId, interventions, jobStatus, onChanged }: {
  token: string
  jobId: number
  interventions: SatpamIntervention[] | undefined
  /** When set, escalate log rows on finished jobs read as history, not a live hold. */
  jobStatus?: JobStatus
  onChanged?: (job: Job) => void
}) {
  const [busy, setBusy] = React.useState<number | null>(null)
  const [error, setError] = React.useState('')
  if (!interventions?.length) return null
  const pending = interventions.filter(i => i.action === 'restart' && i.status === 'pending')
  const history = interventions.filter(i => !(i.action === 'restart' && i.status === 'pending'))

  const decide = async (intervention: SatpamIntervention, approve: boolean) => {
    if (busy != null) return
    setBusy(intervention.id); setError('')
    try {
      const updated = approve
        ? await approveSatpamRestart(token, jobId, intervention.id)
        : await dismissSatpamRestart(token, jobId, intervention.id)
      onChanged?.(updated)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(null)
    }
  }

  return <div className="satpam-card">
    {pending.map(p => <div key={p.id} className="satpam-pending">
      <p className="satpam-kicker">Watchdog needs your call</p>
      <p className="satpam-reason">{p.reason}</p>
      <div className="satpam-actions">
        <button className="primary-button" disabled={busy != null} onClick={() => void decide(p, true)}>
          {busy === p.id ? 'Restarting…' : '↺ Restart clean'}
        </button>
        <button className="ghost-button" disabled={busy != null} onClick={() => void decide(p, false)}>
          Keep going as-is
        </button>
      </div>
    </div>)}
    {error && <p className="error-text">{error}</p>}
    {history.length > 0 && <div className="satpam-log" role="list" aria-label="Watchdog log">
      <p className="satpam-kicker">Watchdog log</p>
      {history.map(i => {
        const what = historyLabel(i, jobStatus)
        const why = satpamReasonForDisplay(i.reason, jobStatus)
        // Label the row explicitly — adjacent what/why spans smash to
        // "confusedThis job's…" in the accessibility tree without a gap.
        return <div
          key={i.id}
          className={`satpam-row ${i.action}`}
          role="listitem"
          aria-label={`${what}. ${why}`}
        >
          <span className="satpam-icon" aria-hidden>{ACTION_ICON[i.action]}</span>
          <span className="satpam-what" aria-hidden>{what}</span>
          <span className="satpam-why" title={why} aria-hidden>{why}</span>
        </div>
      })}
    </div>}
  </div>
}

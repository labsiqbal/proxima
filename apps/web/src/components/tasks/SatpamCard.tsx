import React from 'react'
import type { Job, SatpamIntervention } from '../../types'
import { approveSatpamRestart, dismissSatpamRestart } from '../../api/jobs'

// The satpam's owner-facing surface (slice 12, T10 #5: no silent
// interventions). Two jobs in one card stack:
// - a PENDING restart is the approval card for a repo job's restart-clean —
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

export function SatpamCard({ token, jobId, interventions, onChanged }: {
  token: string
  jobId: number
  interventions: SatpamIntervention[] | undefined
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
    {history.length > 0 && <div className="satpam-log">
      <p className="satpam-kicker">Watchdog log</p>
      {history.map(i => <div key={i.id} className={`satpam-row ${i.action}`}>
        <span className="satpam-icon" aria-hidden>{ACTION_ICON[i.action]}</span>
        <span className="satpam-what">{ACTION_LABEL[i.action]}{i.status === 'dismissed' ? ' (dismissed)' : ''} · {i.detection}</span>
        <span className="satpam-why" title={i.reason}>{i.reason}</span>
      </div>)}
    </div>}
  </div>
}

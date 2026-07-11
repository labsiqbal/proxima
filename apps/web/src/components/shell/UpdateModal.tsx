import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { UpdateStatus } from '../../api/updates'
import type { ApplyState } from '../../hooks/useUpdateStatus'
import { confirmDialog } from '../ui/Dialog'

export function UpdateModal(props: { status: UpdateStatus; onApply: () => Promise<void>; onClose: () => void }) {
  const [error, setError] = React.useState('')
  const [starting, setStarting] = React.useState(false)
  const latest = props.status.latest
  if (!latest) return null
  const start = async () => {
    const ok = await confirmDialog({
      title: `Update to v${latest.version}?`,
      message: 'Proxima will restart (about 1–2 minutes). Agent runs still going will be interrupted.',
      confirmLabel: 'Update now',
      danger: true,
    })
    if (!ok) return
    setStarting(true)
    setError('')
    try {
      await props.onApply()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setStarting(false)
    }
  }
  return <div className="modal-scrim" onClick={props.onClose}>
    <div className="modal-card update-modal" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
      <h3>Update available: v{latest.version}</h3>
      <p className="muted">You're on v{props.status.current_version}.</p>
      <div className="update-notes"><ReactMarkdown remarkPlugins={[remarkGfm]}>{latest.notes || '_No release notes._'}</ReactMarkdown></div>
      {latest.url && <a className="update-release-link" href={latest.url} target="_blank" rel="noreferrer">View release on GitHub ↗</a>}
      {!props.status.apply_supported && <p className="update-manual"><span className="muted">One-click update isn't available on this OS. Update manually:</span> <code>{props.status.manual_command}</code></p>}
      {error && <p className="update-error">{error}</p>}
      <div className="confirm-actions">
        <button type="button" className="ghost-button" onClick={props.onClose}>Later</button>
        {props.status.apply_supported && <button type="button" className="primary-button" onClick={() => void start()} disabled={starting}>{starting ? 'Starting…' : 'Update now'}</button>}
      </div>
    </div>
  </div>
}

export function UpdateOverlay(props: { applying: ApplyState; onDismiss: () => void }) {
  const failed = props.applying.failedLog !== undefined
  const timedOut = !!props.applying.timedOut
  return <div className="update-overlay" role="alertdialog" aria-modal="true">
    <div className="update-overlay-card">
      {!failed && !timedOut && <>
        <div className="update-spinner" aria-hidden="true" />
        <h3>Updating to v{props.applying.target}…</h3>
        <p className="muted">Pulling, rebuilding and restarting — about 1–2 minutes. Keep this tab open.</p>
      </>}
      {timedOut && <>
        <h3>Still not back…</h3>
        <p className="muted">The update is taking longer than expected. Check <code>proxima status</code> and <code>update.log</code> in the Proxima data folder, then reload.</p>
        <div className="confirm-actions">
          <button type="button" className="ghost-button" onClick={props.onDismiss}>Dismiss</button>
          <button type="button" className="primary-button" onClick={() => window.location.reload()}>Reload</button>
        </div>
      </>}
      {failed && !timedOut && <>
        <h3>Update failed</h3>
        <p className="muted">Proxima is still running the old version — nothing broke. The update log:</p>
        <pre className="update-log">{props.applying.failedLog || 'No log output captured.'}</pre>
        <div className="confirm-actions">
          <button type="button" className="primary-button" onClick={props.onDismiss}>Close</button>
        </div>
      </>}
    </div>
  </div>
}

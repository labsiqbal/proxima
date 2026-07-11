import React from 'react'
import type { Runner } from '../types'

export function RunnersScreen({ runners, onRefresh }: { token: string; runners: Runner[]; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const mountedRef = React.useRef(true)
  const actionSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      actionSeq.current += 1
    }
  }, [])

  async function rescan() {
    if (busy) return
    const seq = ++actionSeq.current
    setBusy(true)
    setError('')
    try {
      await onRefresh()
    } catch (e) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(e))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  return <section className="runners-view">
    <div className="panel runners-panel">
      <div className="panel-head">
        <div><h3>Runners</h3><p className="muted">Bring your own agent — Proxima drives whichever runners you have installed and ready. Runnable ones can back a profile; the rest are detected for future adapters.</p></div>
        <button onClick={() => void rescan()} disabled={busy}>{busy ? 'Scanning…' : 'Rescan'}</button>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="runner-grid">{runners.map(runner => <article className={`runner-card ${runner.runnable ? 'ready' : runner.installed ? 'detected' : 'missing'}`} key={runner.id}><div className="runner-card-head"><strong>{runner.displayName}</strong><span>{runner.runnable ? 'Runnable' : runner.installed ? 'Future adapter' : 'Missing'}</span></div><small>{runner.id}</small><p>{runner.notes || 'No notes.'}</p><code>{runner.path || runner.binary || 'no binary detected'}</code></article>)}</div>
    </div>
  </section>
}

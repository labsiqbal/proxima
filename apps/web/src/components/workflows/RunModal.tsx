import React from 'react'
import type { WorkflowInput } from '../../types'

// Collects the values for a workflow's declared {{inputs}} before a run is created.
// Shared by the linear recipes and the graph canvas: a graph node's {{var}} is filled
// from the job input exactly the way a linear step's is, so the same form asks the
// same question. Kept out of either screen so it outlives them.
export function RunModal({ title, inputs, confirmLabel = 'Run workflow', onCancel, onRun }: {
  title: string
  inputs: WorkflowInput[] | null | undefined
  confirmLabel?: string
  onCancel: () => void
  onRun: (input: Record<string, string> | undefined) => Promise<void>
}) {
  const declared = inputs || []
  const hasInputs = declared.length > 0
  const [brief, setBrief] = React.useState('')
  const [values, setValues] = React.useState<Record<string, string>>({})
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

  async function run(input: Record<string, string> | undefined) {
    const seq = ++actionSeq.current
    setBusy(true)
    setError('')
    try {
      await onRun(input)
    } catch (cause) {
      if (mountedRef.current && seq === actionSeq.current) setError(String(cause))
    } finally {
      if (mountedRef.current && seq === actionSeq.current) setBusy(false)
    }
  }

  async function submit() {
    if (busy) return
    if (!hasInputs) {
      const value = brief.trim()
      await run(value ? { brief: value } : undefined)
      return
    }
    const missing = declared.find(x => x.required && !(values[x.id] || '').trim())
    if (missing) { setError(`“${missing.label}” is required.`); return }
    const input: Record<string, string> = {}
    for (const x of declared) {
      const value = (values[x.id] || '').trim()
      if (value) input[x.id] = value
    }
    await run(Object.keys(input).length ? input : undefined)
  }

  const close = () => { if (!busy) onCancel() }

  return <div className="modal-scrim" onClick={close}><div className="modal-card" onClick={e => e.stopPropagation()}>
    <h3>Run “{title}”</h3>
    {error && <div className="error-bar">{error}</div>}
    {hasInputs
      ? declared.map((x, i) => <label key={x.id}>{x.label}{x.required && <span className="muted"> (required)</span>}
          <input autoFocus={i === 0} type={x.kind === 'number' ? 'number' : x.kind === 'url' ? 'url' : 'text'} value={values[x.id] || ''} onChange={e => setValues(v => ({ ...v, [x.id]: e.target.value }))} placeholder={x.kind === 'file' ? 'Path or URL' : x.label} />
        </label>)
      : <label>Brief <span className="muted">(context for this run)</span><textarea autoFocus rows={4} value={brief} onChange={e => setBrief(e.target.value)} placeholder="What should this run focus on?" /></label>}
    <div className="modal-actions">
      <button className="ghost-button" onClick={close} disabled={busy}>Cancel</button>
      <button className="primary-button" disabled={busy} onClick={() => void submit()}>{busy ? 'Starting…' : confirmLabel}</button>
    </div>
  </div></div>
}

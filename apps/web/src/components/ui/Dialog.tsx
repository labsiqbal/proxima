import React from 'react'

// Designed replacements for window.confirm / window.prompt. Imperative API so any
// call site can `await confirmDialog(...)` / `await promptDialog(...)` with no
// local state. A single <DialogHost/> (mounted once in App) renders the modal.

type ConfirmReq = { kind: 'confirm'; title: string; message?: string; confirmLabel?: string; danger?: boolean; resolve: (v: boolean) => void }
type PromptReq = { kind: 'prompt'; title: string; label?: string; defaultValue?: string; confirmLabel?: string; resolve: (v: string | null) => void }
type Req = ConfirmReq | PromptReq

let listener: ((r: Req) => void) | null = null

export function confirmDialog(o: { title: string; message?: string; confirmLabel?: string; danger?: boolean }): Promise<boolean> {
  return new Promise(resolve => { if (!listener) { resolve(false); return } listener({ kind: 'confirm', resolve, ...o }) })
}
export function promptDialog(o: { title: string; label?: string; defaultValue?: string; confirmLabel?: string }): Promise<string | null> {
  return new Promise(resolve => { if (!listener) { resolve(null); return } listener({ kind: 'prompt', resolve, ...o }) })
}

export function DialogHost() {
  const [req, setReq] = React.useState<Req | null>(null)
  const [val, setVal] = React.useState('')
  React.useEffect(() => {
    listener = r => { setReq(r); if (r.kind === 'prompt') setVal(r.defaultValue || '') }
    return () => { listener = null }
  }, [])
  if (!req) return null
  const cancel = () => { if (req.kind === 'confirm') req.resolve(false); else req.resolve(null); setReq(null) }
  const accept = () => {
    if (req.kind === 'confirm') req.resolve(true)
    else { const t = val.trim(); if (!t) return; req.resolve(t) }
    setReq(null)
  }
  const acceptDisabled = req.kind === 'prompt' && !val.trim()
  return <div className="modal-scrim" onClick={cancel}>
    <div className="modal-card confirm-card" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
      <h3>{req.title}</h3>
      {req.kind === 'confirm' && req.message && <p className="confirm-msg">{req.message}</p>}
      {req.kind === 'prompt' && <label>{req.label || 'Value'}
        <input className="ui-select" autoFocus value={val} onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') accept(); if (e.key === 'Escape') cancel() }} />
      </label>}
      <div className="confirm-actions">
        <button type="button" className="ghost-button" onClick={cancel}>Cancel</button>
        <button type="button" className={`primary-button ${req.kind === 'confirm' && req.danger ? 'danger' : ''}`} onClick={accept} disabled={acceptDisabled}>
          {req.confirmLabel || (req.kind === 'confirm' ? 'Confirm' : 'Save')}
        </button>
      </div>
    </div>
  </div>
}

import React from 'react'
import { trapModalTab } from './modalFocus'

// Designed replacements for window.confirm / window.prompt. Imperative API so any
// call site can `await confirmDialog(...)` / `await promptDialog(...)` with no
// local state. A single <DialogHost/> (mounted once in App) renders the modal.

type ConfirmReq = { kind: 'confirm'; title: string; message?: string; confirmLabel?: string; cancelLabel?: string; danger?: boolean; resolve: (v: boolean) => void }
type PromptReq = { kind: 'prompt'; title: string; label?: string; defaultValue?: string; confirmLabel?: string; resolve: (v: string | null) => void }
type Req = ConfirmReq | PromptReq

let listener: ((r: Req) => void) | null = null

export function confirmDialog(o: { title: string; message?: string; confirmLabel?: string; cancelLabel?: string; danger?: boolean }): Promise<boolean> {
  return new Promise(resolve => { if (!listener) { resolve(false); return } listener({ kind: 'confirm', resolve, ...o }) })
}
export function promptDialog(o: { title: string; label?: string; defaultValue?: string; confirmLabel?: string }): Promise<string | null> {
  return new Promise(resolve => { if (!listener) { resolve(null); return } listener({ kind: 'prompt', resolve, ...o }) })
}

export function DialogHost() {
  const [req, setReq] = React.useState<Req | null>(null)
  const [val, setVal] = React.useState('')
  const cardRef = React.useRef<HTMLDivElement>(null)
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const triggerRef = React.useRef<HTMLElement | null>(null)
  React.useEffect(() => {
    listener = r => {
      triggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      setReq(r)
      if (r.kind === 'prompt') setVal(r.defaultValue || '')
    }
    return () => { listener = null }
  }, [])
  React.useEffect(() => {
    if (req?.kind === 'confirm') cancelRef.current?.focus()
  }, [req])
  if (!req) return null
  const finish = (resolve: () => void) => {
    setReq(null)
    requestAnimationFrame(() => {
      triggerRef.current?.focus()
      resolve()
    })
  }
  const cancel = () => {
    finish(() => {
      if (req.kind === 'confirm') req.resolve(false)
      else req.resolve(null)
    })
  }
  const accept = () => {
    if (req.kind === 'confirm') {
      finish(() => req.resolve(true))
    } else {
      const t = val.trim()
      if (!t) return
      finish(() => req.resolve(t))
    }
  }
  const acceptDisabled = req.kind === 'prompt' && !val.trim()
  return <div className="modal-scrim" onClick={cancel}>
    <div className="modal-card confirm-card" ref={cardRef} tabIndex={-1} onClick={e => e.stopPropagation()} onKeyDown={e => {
      if (e.key === 'Escape') {
        e.preventDefault()
        cancel()
      } else if (cardRef.current) {
        trapModalTab(e, cardRef.current)
      }
    }} role="dialog" aria-modal="true" aria-labelledby="proxima-dialog-title" aria-describedby={req.kind === 'confirm' && req.message ? 'proxima-dialog-message' : undefined}>
      <h3 id="proxima-dialog-title">{req.title}</h3>
      {req.kind === 'confirm' && req.message && <p className="confirm-msg" id="proxima-dialog-message">{req.message}</p>}
      {req.kind === 'prompt' && <label>{req.label || 'Value'}
        <input className="ui-select" autoFocus value={val} onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') accept() }} />
      </label>}
      <div className="confirm-actions">
        <button type="button" className="ghost-button" ref={cancelRef} onClick={cancel}>{req.kind === 'confirm' ? req.cancelLabel || 'Cancel' : 'Cancel'}</button>
        <button type="button" className={`primary-button ${req.kind === 'confirm' && req.danger ? 'danger' : ''}`} onClick={accept} disabled={acceptDisabled}>
          {req.confirmLabel || (req.kind === 'confirm' ? 'Confirm' : 'Save')}
        </button>
      </div>
    </div>
  </div>
}

import React from 'react'
import type { WikiDraft } from '../../types'

// Validation-gate preview: shows the distilled note + conflict/overlap report,
// lets the user edit path/body and choose how it lands, before anything is saved.
export function WikiNotePreview({ draft, onCancel, onSave }: {
  draft: WikiDraft
  onCancel: () => void
  onSave: (path: string, content: string, mode: 'new' | 'append' | 'overwrite') => Promise<void>
}) {
  const [path, setPath] = React.useState(draft.path)
  const [body, setBody] = React.useState(draft.body)
  const [mode, setMode] = React.useState<'new' | 'append' | 'overwrite'>(draft.action === 'merge' ? 'append' : 'new')
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')
  const mountedRef = React.useRef(true)
  const saveSeq = React.useRef(0)

  React.useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      saveSeq.current += 1
    }
  }, [])

  async function save() {
    if (busy || !path.trim()) return
    const seq = ++saveSeq.current
    setBusy(true); setErr('')
    try { await onSave(path.trim(), body, mode) }
    catch (e) { if (mountedRef.current && seq === saveSeq.current) setErr(String(e)) }
    finally { if (mountedRef.current && seq === saveSeq.current) setBusy(false) }
  }

  return <div className="modal-scrim" onClick={() => { if (!busy) onCancel() }}><div className="modal-card wiki-preview" onClick={e => e.stopPropagation()}>
    <div className="wiki-preview-head"><strong>Save to wiki</strong><button className="icon-button" onClick={onCancel} aria-label="Close" disabled={busy}>✕</button></div>
    {draft.unparsed && <p className="muted">Couldn't structure this automatically — review and edit before saving.</p>}
    {draft.conflicts.length > 0 && <div className="wiki-conflicts">{draft.conflicts.map((c, i) => <div key={i} className="wiki-conflict">⚠ {c}</div>)}</div>}
    {draft.related.length > 0 && <p className="muted">Related: {draft.related.join(', ')}</p>}
    <label className="wiki-field">Path<input className="ui-select" value={path} onChange={e => setPath(e.target.value)} /></label>
    <label className="wiki-field">Note<textarea className="ui-select wiki-body" value={body} onChange={e => setBody(e.target.value)} rows={14} /></label>
    <div className="wiki-mode">
      <label><input type="radio" checked={mode === 'new'} onChange={() => setMode('new')} /> New / overwrite</label>
      <label><input type="radio" checked={mode === 'append'} onChange={() => setMode('append')} /> Append to existing</label>
    </div>
    {err && <p className="error-text">{err}</p>}
    <div className="wiki-preview-actions">
      <button className="ghost-button" onClick={onCancel} disabled={busy}>Cancel</button>
      <button className="primary-button" onClick={() => void save()} disabled={busy || !path.trim()}>{busy ? 'Saving…' : 'Approve & save'}</button>
    </div>
  </div></div>
}

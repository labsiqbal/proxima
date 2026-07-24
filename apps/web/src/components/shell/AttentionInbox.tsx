import React from 'react'
import { actAttention, getAttention, type AttentionItem } from '../../api/alpha'

const labelForKind = (kind: string) => ({
  job_review: 'Review', job_diff: 'Changes', satpam_restart: 'Watchdog', script_trust: 'Script',
  permission_job: 'Permission', alpha_decision: 'Alpha', alpha_budget: 'Alpha budget', settings_confirm: 'Settings',
}[kind] || 'Attention')

export function AttentionInbox({ token, onOpenTarget }: { token: string; onOpenTarget: (target: AttentionItem['target']) => void }) {
  const [items, setItems] = React.useState<AttentionItem[]>([])
  const [open, setOpen] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [busy, setBusy] = React.useState('')
  const [error, setError] = React.useState('')
  const root = React.useRef<HTMLDivElement>(null)

  const load = React.useCallback(async () => {
    try { const body = await getAttention(token); setItems(body.items); setError('') }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setLoading(false) }
  }, [token])
  React.useEffect(() => {
    void load()
    const id = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(id)
  }, [load])
  React.useEffect(() => {
    if (!open) return
    const dismiss = (event: MouseEvent) => { if (root.current && !root.current.contains(event.target as Node)) setOpen(false) }
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', dismiss); window.addEventListener('keydown', key)
    return () => { window.removeEventListener('mousedown', dismiss); window.removeEventListener('keydown', key) }
  }, [open])
  const act = async (item: AttentionItem, action: string) => {
    const key = `${item.id}:${action}`
    if (busy) return
    setBusy(key); setError('')
    try { await actAttention(token, item.id, action); await load() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }
  const go = (item: AttentionItem) => { setOpen(false); onOpenTarget(item.target) }

  return <div className="attention-inbox" ref={root}>
    <button type="button" className={`attention-trigger ${open ? 'active' : ''}`} onClick={() => setOpen(value => !value)} aria-haspopup="dialog" aria-expanded={open} aria-label={`${items.length} attention item${items.length === 1 ? '' : 's'}`}>
      <span aria-hidden="true">!</span>{items.length > 0 && <b>{items.length > 99 ? '99+' : items.length}</b>}
    </button>
    {open && <section className="attention-popover" role="dialog" aria-modal="false" aria-label="Attention inbox">
      <header><div><span className="eyebrow">Needs you</span><h2>Attention</h2></div><button type="button" className="text-button" disabled={loading} onClick={() => void load()}>{loading ? 'Refreshing…' : 'Refresh'}</button></header>
      {error && <div className="attention-error" role="alert"><strong>Inbox could not update</strong><p>{error}</p><button type="button" onClick={() => void load()}>Try again</button></div>}
      {loading && !items.length ? <div className="attention-state" role="status"><span className="ui-spinner" /> Loading attention…</div>
        : !items.length ? <div className="attention-state"><strong>Nothing needs you</strong><p>Reviews, watchdog decisions, and Alpha questions will collect here.</p></div>
          : <ul className="attention-list">{items.map(item => <li key={item.id}>
            <button type="button" className="attention-main" onClick={() => go(item)}><span>{labelForKind(item.kind)}</span><strong>{item.title}</strong><small>Open linked workspace</small></button>
            {item.inline_ok && item.actions.length > 0 && <div className="attention-actions">{item.actions.map(action => <button type="button" key={action} disabled={!!busy} className={action === 'approve' ? 'attention-approve' : ''} onClick={() => void act(item, action)}>{busy === `${item.id}:${action}` ? 'Working…' : action.charAt(0).toUpperCase() + action.slice(1)}</button>)}</div>}
          </li>)}</ul>}
    </section>}
  </div>
}

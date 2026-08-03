import React from 'react'
import { getAttention, type AttentionItem } from '../../api/master'
import { dismissAttention } from '../../api/inbox'
import { actionLabel, labelForKind, useAttentionActions } from '../../lib/notifications'
import { formatRunAge, runStatusLabel } from '../../lib/runProjection'
import { MasterDecisionCard } from '../master/MasterDecisionCard'

// Header notifications are ephemeral, like a phone's (#158). The list is the
// *unread* slice of the Inbox ledger, and touching an item - opening it,
// dismissing it, acting on it - marks it read so it leaves the header. Nothing
// is destroyed: the same row keeps its status, its actions and its full detail
// in the Inbox destination, which is one click away in the footer.
const helperForItem = (item: AttentionItem) => {
  if (item.kind === 'container_ops_migration') return 'Inspect Ops migration'
  if (item.run_projection) {
    return `${runStatusLabel(item.run_projection.status)} · ${formatRunAge(item.run_projection, item.created_at)}`
  }
  return 'Open linked workspace'
}

// The header shows the diagnosis only. The instruction that follows it is a
// second paragraph in the same body, and the Inbox is where it is read in full -
// a popover row that reflows into a paragraph stops being scannable.
const reasonForItem = (item: AttentionItem) => {
  if (typeof item.body === 'string' && item.body) return item.body.split('\n\n')[0]
  return item.kind === 'container_ops_migration' && typeof item.target.reason === 'string'
    ? item.target.reason
    : null
}

export function AttentionInbox({ token, onOpenTarget, onOpenInbox }: {
  token: string
  onOpenTarget: (target: AttentionItem['target']) => void
  onOpenInbox: () => void
}) {
  const [items, setItems] = React.useState<AttentionItem[]>([])
  const [count, setCount] = React.useState(0)
  const [open, setOpen] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState('')
  const root = React.useRef<HTMLDivElement>(null)

  const load = React.useCallback(async () => {
    try {
      const body = await getAttention(token)
      setItems(body.items); setCount(body.count ?? body.items.length); setError('')
    }
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

  // Optimistic: the row leaves the header immediately, then the server is told.
  // A failed dismiss surfaces as the ordinary retryable error and the next poll
  // brings the row back, so the badge can never lie for longer than one tick.
  const handled = React.useCallback(async (item: AttentionItem) => {
    setItems(current => current.filter(entry => entry.id !== item.id))
    setCount(current => Math.max(0, current - 1))
    try { await dismissAttention(token, item.id) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); await load() }
  }, [token, load])

  const { busy, act, isBusy } = useAttentionActions(token, load, setError)
  const go = (item: AttentionItem) => {
    setOpen(false)
    void handled(item)
    onOpenTarget(item.target)
  }

  // Hide when empty so a permanent "!" does not read as an alarm next to running work.
  if (count === 0 && items.length === 0) return null

  const badge = count || items.length
  // Alarm chrome only when something actually needs a decision. A pile of
  // "your Task finished" notifications must not paint the header red.
  const urgent = items.some(item => item.requires_action !== false && item.status === 'open')
  return <div className="attention-inbox" ref={root}>
    <button type="button" className={`attention-trigger ${urgent ? 'has-attention' : ''} ${open ? 'active' : ''}`} onClick={() => setOpen(value => !value)} aria-haspopup="dialog" aria-expanded={open} aria-label={`${badge} unread notification${badge === 1 ? '' : 's'}`}>
      <span aria-hidden="true">!</span><b>{badge > 99 ? '99+' : badge}</b>
    </button>
    {open && <section className="attention-popover" role="dialog" aria-modal="false" aria-label="Notifications">
      <header><div><span className="eyebrow">Unread</span><h2>Notifications</h2></div><button type="button" className="text-button" disabled={loading} onClick={() => void load()}>{loading ? 'Refreshing…' : 'Refresh'}</button></header>
      {error && <div className="attention-error" role="alert"><strong>Notifications could not update</strong><p>{error}</p><button type="button" onClick={() => void load()}>Try again</button></div>}
      {loading ? <div className="attention-state" role="status"><span className="ui-spinner" /> Loading notifications…</div>
        : items.length === 0 ? <div className="attention-state" role="status">You are all caught up.</div>
        : <ul className="attention-list">{items.map(item => <li key={item.id}>
            {item.kind === 'master_decision' && item.decision ? (
              <MasterDecisionCard
                token={token}
                decision={item.decision}
                compact
                onChanged={load}
                onOpenJob={(jobId, engine) => {
                  setOpen(false)
                  void handled(item)
                  onOpenTarget({ view: 'task', job_id: jobId, engine })
                }}
                onOpenMaster={originMessageId => {
                  setOpen(false)
                  void handled(item)
                  onOpenTarget({
                    view: 'master',
                    origin_message_id: originMessageId ?? undefined,
                  })
                }}
              />
            ) : (
              <>
                <button type="button" className="attention-main" onClick={() => go(item)}><span>{labelForKind(item.kind)}</span><strong>{item.title}</strong>{reasonForItem(item) && <small className="attention-reason">{reasonForItem(item)}</small>}<small>{helperForItem(item)}</small></button>
                <div className="attention-actions">
                  {item.inline_ok && item.actions.length > 0 && item.actions.map(action => <button type="button" key={action} disabled={!!busy} className={action === 'approve' ? 'attention-approve' : ''} onClick={() => void act(item, action)}>{actionLabel(action, isBusy(item, action))}</button>)}
                  <button type="button" className="attention-dismiss" onClick={() => void handled(item)}>Dismiss</button>
                </div>
              </>
            )}
          </li>)}</ul>}
      <footer className="attention-foot">
        <button type="button" className="text-button" onClick={() => { setOpen(false); onOpenInbox() }}>Open Inbox</button>
        {/* The badge counts every unread row; the popover is bounded. Say so
            rather than letting the two disagree in silence. */}
        <small>{count > items.length
          ? `Showing ${items.length} of ${count} unread.`
          : 'Dismissed notifications stay in the Inbox.'}</small>
      </footer>
    </section>}
  </div>
}

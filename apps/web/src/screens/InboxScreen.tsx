import React from 'react'
import {
  getInbox,
  readAllInbox,
  setInboxRead,
  type InboxItem,
} from '../api/inbox'
import type { AttentionItem } from '../api/master'
import { actionLabel, labelForKind, useAttentionActions } from '../lib/notifications'
import { MasterDecisionCard } from '../components/master/MasterDecisionCard'

/**
 * The Inbox destination (#158) - the persistent home for every notification.
 *
 * Why it is a destination and not a bigger popover: most of what the system
 * emits is *reading*, not deciding. A finished Task, a failed build with its
 * diagnostic, a Master budget stop - the owner wants those to be findable
 * later, at their own pace, with the full detail attached. The header is the
 * interruption; this is the record. Nothing dismissed from the header is lost,
 * so the header can afford to be ruthless.
 *
 * It appears in **both** Work and Delegate. Notifications are global (a Task
 * that finished, a Master budget that stopped, a workflow that failed) and the
 * header badge that produces them already renders in both modes, so an Inbox
 * that existed in only one would strand half the notifications behind a mode
 * switch.
 */

const SEVERITY_LABEL: Record<string, string> = {
  error: 'Error',
  warning: 'Warning',
  success: 'Done',
  info: 'Update',
  action: 'Needs you',
}

/** The severity eyebrow, unless the kind already said it (a client error). */
export function severityLabel(item: { kind: string; severity: string }): string {
  const label = SEVERITY_LABEL[item.severity] || 'Update'
  return label.toLowerCase() === labelForKind(item.kind).toLowerCase() ? '' : label
}

/** Recency in words - the exact stamp stays on the row's title attribute. */
export function inboxWhen(created: string | undefined, now: number): string {
  if (!created) return ''
  const parsed = Date.parse(created.endsWith('Z') ? created : `${created.replace(' ', 'T')}Z`)
  if (!Number.isFinite(parsed)) return ''
  const seconds = Math.max(0, Math.round((now - parsed) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function InboxScreen({ token, onOpenTarget }: {
  token: string
  onOpenTarget: (target: AttentionItem['target']) => void
}) {
  const [items, setItems] = React.useState<InboxItem[]>([])
  const [unread, setUnread] = React.useState(0)
  const [nextBefore, setNextBefore] = React.useState<number | null>(null)
  const [unreadOnly, setUnreadOnly] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState('')

  const load = React.useCallback(async (onlyUnread: boolean) => {
    setLoading(true)
    try {
      const page = await getInbox(token, { unread: onlyUnread })
      setItems(page.items); setUnread(page.unread); setNextBefore(page.next_before); setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [token])

  React.useEffect(() => { void load(unreadOnly) }, [load, unreadOnly])

  const loadOlder = async () => {
    if (nextBefore == null) return
    try {
      const page = await getInbox(token, { unread: unreadOnly, before: nextBefore })
      setItems(current => [...current, ...page.items])
      setNextBefore(page.next_before)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const markRead = async (item: InboxItem, read: boolean) => {
    setItems(current => current.map(entry => entry.id === item.id ? { ...entry, read } : entry))
    setUnread(current => Math.max(0, current + (read ? -1 : 1)))
    try { await setInboxRead(token, item.id, read) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); await load(unreadOnly) }
  }

  const markAll = async () => {
    setItems(current => current.map(entry => ({ ...entry, read: true })))
    setUnread(0)
    try { await readAllInbox(token) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { await load(unreadOnly) }
  }

  const open = (item: InboxItem) => {
    if (!item.read) void markRead(item, true)
    onOpenTarget(item.target)
  }

  const reload = React.useCallback(() => load(unreadOnly), [load, unreadOnly])
  const { busy, act, isBusy } = useAttentionActions(token, reload, setError)

  const now = Date.now()
  return <section className="inbox-view">
    <header className="inbox-head">
      <div className="inbox-head-title">
        <span className="eyebrow">Notifications</span>
        <h1>Inbox</h1>
      </div>
      <div className="inbox-head-actions">
        <div className="inbox-filter" role="group" aria-label="Filter notifications">
          <button type="button" aria-pressed={!unreadOnly} onClick={() => setUnreadOnly(false)}>All</button>
          <button type="button" aria-pressed={unreadOnly} onClick={() => setUnreadOnly(true)}>Unread{unread > 0 ? ` · ${unread}` : ''}</button>
        </div>
        <button type="button" className="text-button" disabled={unread === 0} onClick={() => void markAll()}>Mark all read</button>
      </div>
    </header>

    {error && <div className="inbox-error" role="alert">
      <strong>The Inbox could not load</strong>
      <p>{error}</p>
      <button type="button" onClick={() => void load(unreadOnly)}>Try again</button>
    </div>}

    {loading && items.length === 0
      ? <div className="inbox-state" role="status"><span className="ui-spinner" /> Loading notifications…</div>
      : items.length === 0
        ? <div className="inbox-state" role="status">
            <strong>Nothing here yet</strong>
            <p>{unreadOnly ? 'Everything is read.' : 'Errors, finished work and anything that needs you will land here.'}</p>
          </div>
        : <div className="inbox-body">
          <ul className="inbox-list">{items.map(item => {
            const actionable = item.requires_action && item.status === 'open'
            // A Master decision carries a real question with a bounded answer.
            // The header renders its full form; the Inbox is where it is meant
            // to be *answered*, so it must not degrade to an Open button here
            // (#158: actionable affordances live inside the Inbox entry).
            if (item.kind === 'master_decision' && item.decision) {
              return <li key={item.id} className={`inbox-item ${item.read ? 'is-read' : 'is-unread'} severity-${item.severity} is-decision`}>
                <MasterDecisionCard
                  token={token}
                  decision={item.decision}
                  onChanged={reload}
                  onOpenJob={(jobId, engine) => { void markRead(item, true); onOpenTarget({ view: 'task', job_id: jobId, engine }) }}
                  onOpenMaster={originMessageId => { void markRead(item, true); onOpenTarget({ view: 'master', origin_message_id: originMessageId ?? undefined }) }}
                />
              </li>
            }
            return <li key={item.id} className={`inbox-item ${item.read ? 'is-read' : 'is-unread'} severity-${item.severity}`}>
              <button type="button" className="inbox-entry" onClick={() => open(item)} title={item.created_at}>
                <span className="inbox-dot" aria-hidden="true" />
                <span className="inbox-entry-head">
                  <span className="inbox-kind">{labelForKind(item.kind)}</span>
                  {/* A client_error's kind and severity are the same word; say it once. */}
                  {severityLabel(item) && <span className="inbox-severity">{severityLabel(item)}</span>}
                  <span className="inbox-when">{inboxWhen(item.created_at, now)}</span>
                </span>
                <strong className="inbox-title">{item.title}</strong>
                {item.body && <p className="inbox-detail">{item.body}</p>}
              </button>
              <div className="inbox-item-actions">
                {actionable && item.inline_ok && item.actions.map(action =>
                  <button type="button" key={action} disabled={!!busy} className={action === 'approve' ? 'inbox-approve' : ''} onClick={() => void act(item, action)}>
                    {actionLabel(action, isBusy(item, action))}
                  </button>)}
                {actionable && !item.inline_ok && <button type="button" onClick={() => open(item)}>Open</button>}
                <button type="button" className="inbox-read-toggle" onClick={() => void markRead(item, !item.read)}>
                  {item.read ? 'Mark unread' : 'Mark read'}
                </button>
              </div>
            </li>
          })}</ul>
          {nextBefore != null && <button type="button" className="inbox-more text-button" onClick={() => void loadOlder()}>Load older</button>}
        </div>}
  </section>
}

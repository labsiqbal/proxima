import { api } from './client'
import type { AttentionItem } from './master'

/**
 * The Inbox is the persistent home for notifications; the header popover is the
 * ephemeral view of the same ledger (#158). Both read the same rows, so an item
 * dismissed from the header is never lost - it is merely marked read.
 */
export type InboxItem = AttentionItem & {
  /** Row id, used as the pagination cursor. */
  seq: number
  severity: 'info' | 'success' | 'warning' | 'error' | 'action'
  /** Full human detail: the diagnosis, and the step that clears it. */
  body: string
  detail: Record<string, unknown>
  requires_action: boolean
  read: boolean
  read_at?: string | null
  resolved_at?: string | null
}

export type InboxPage = {
  items: InboxItem[]
  unread: number
  next_before: number | null
}

const q = (value: string) => encodeURIComponent(value)

export const getInbox = (
  token: string,
  options: { unread?: boolean; limit?: number; before?: number | null } = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams()
  if (options.unread) params.set('unread', '1')
  if (options.limit) params.set('limit', String(options.limit))
  if (options.before) params.set('before', String(options.before))
  const query = params.toString()
  return api<InboxPage>(`/api/inbox${query ? `?${query}` : ''}`, token, { signal })
}

export const setInboxRead = (token: string, id: string, read: boolean) =>
  api<{ ok: boolean; id: string; read: boolean }>(
    `/api/inbox/${q(id)}/read`,
    token,
    { method: 'POST', body: JSON.stringify({ read }) },
  )

export const readAllInbox = (token: string) =>
  api<{ ok: boolean; read: number }>('/api/inbox/read-all', token, {
    method: 'POST',
    body: JSON.stringify({}),
  })

/** Acknowledge a header item: seen, not done. The Inbox copy stays. */
export const dismissAttention = (token: string, id: string) =>
  api<{ ok: boolean; id: string }>(`/api/attention/${q(id)}/dismiss`, token, {
    method: 'POST',
    body: JSON.stringify({}),
  })

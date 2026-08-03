import React from 'react'
import { actAttention, type AttentionItem } from '../api/master'

/**
 * Presentation and behaviour shared by the two surfaces over one notification
 * ledger (ADR-0044): the ephemeral header popover and the Inbox destination.
 * Neither owns it, so it lives here rather than one importing the other.
 */

export const labelForKind = (kind: string) => ({
  job_review: 'Review', job_diff: 'Changes', satpam_restart: 'Watchdog', script_trust: 'Script',
  permission_job: 'Permission', master_decision: 'Master', master_budget: 'Master budget',
  settings_confirm: 'Settings', container_ops_migration: 'Ops migration',
  task_outcome: 'Task', client_error: 'Error',
}[kind] || 'Attention')

/** Title-cased action label, with a busy state that reads as one word. */
export const actionLabel = (action: string, busy: boolean) =>
  busy ? 'Working…' : action.charAt(0).toUpperCase() + action.slice(1)

/**
 * Running a server-owned action on a notification, with the one-at-a-time guard
 * both surfaces need. `busy` is the `id:action` key so only the pressed button
 * shows its own progress.
 */
export function useAttentionActions(
  token: string,
  reload: () => Promise<void> | void,
  onError: (message: string) => void,
) {
  const [busy, setBusy] = React.useState('')
  const act = React.useCallback(async (item: AttentionItem, action: string) => {
    if (busy) return
    setBusy(`${item.id}:${action}`)
    try { await actAttention(token, item.id, action); await reload() }
    catch (err) { onError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }, [busy, token, reload, onError])
  return { busy, act, isBusy: (item: AttentionItem, action: string) => busy === `${item.id}:${action}` }
}

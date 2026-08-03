import { api } from './client'
import type { ChatMessage, ChatSession, Job, RunProjection } from '../types'

export type MasterMessageContext = {
  focus: {
    mode: 'fleet' | 'container'
    container_id?: number
  }
  target: {
    mode: 'auto' | 'explicit'
    container_id?: number
    area_id?: number
  }
}

export type MasterCapacity = { running: number; max: number; free: number; queued: number }
export type MasterFocusSnapshot = {
  current_epoch_id: number | null
  current_container_id: number | null
  pending_container_id: number | null
  pending: boolean
  version: number
}
export type MasterBudgets = {
  unattended: boolean
  budget_turns: number
  budget_wall_seconds: number
  budget_tokens: number | null
  tour_core_done: boolean
}
export type MasterCheckpoint = {
  id: number
  job_id: number
  pinned: boolean
  created_at: string
  payload: { job?: Record<string, unknown> }
  git_refs: { repo_path?: string; worktree_path?: string; sha?: string; restore_strategy?: 'worktree_reset' | 'reference_only' }[]
}
export type MasterJob = Job & {
  desk_status: string
  run_status?: string | null
  project_slug?: string | null
  project_name?: string | null
}
export type MasterDecisionChoice = {
  id: string
  label: string
  description?: string
}
export type MasterDecision = {
  id: number
  attention_item_id: number
  master_session_id: number
  origin_message_id: number | null
  requesting_job_id: number | null
  title: string
  prompt: string
  context: string
  response_shape:
    | { type: 'choice'; choices: MasterDecisionChoice[] }
    | { type: 'text'; max_length: number; placeholder: string }
  state: 'pending' | 'deferred' | 'resolved'
  response: { value: string; label: string } | null
  version: number
  deferred_by_user_id?: number | null
  deferred_at?: string | null
  resolved_by_user_id?: number | null
  resolved_at?: string | null
  task_message_id?: number | null
  continuation_run_id?: number | null
  created_at: string
  updated_at: string
  legacy_without_task: boolean
  task: {
    id: number
    title: string
    status: string
    engine: string
    project_id?: number | null
    project_name?: string | null
    project_slug?: string | null
  } | null
}
export type AttentionItem = {
  id: string
  kind: string
  title: string
  target: { view?: string; job_id?: number; engine?: string; section?: string; [key: string]: unknown }
  inline_ok: boolean
  actions: string[]
  status: string
  created_at?: string
  run_projection?: RunProjection
  decision?: MasterDecision
  // Inbox ledger fields (#158). Present on every row the server sends today;
  // optional so the Master desk's own attention list can stay a plain subset.
  severity?: 'info' | 'success' | 'warning' | 'error' | 'action'
  /** The diagnosis, and the step that clears it. */
  body?: string
  requires_action?: boolean
  read?: boolean
}
export type MasterDesk = {
  session: ChatSession
  master_run?: { id: number; status: string } | null
  event_cursor: number
  backing_runner: string
  jobs: MasterJob[]
  unattended: boolean
  budgets: MasterBudgets
  capacity: MasterCapacity
  attention: AttentionItem[]
  decisions: MasterDecision[]
  checkpoints: MasterCheckpoint[]
  focus: MasterFocusSnapshot
}
export type GraphPolicy = {
  semantic_egress_enabled: boolean
  local_only: boolean
  semantic_backend_default: string
  description: string
}
export type MasterSettings = MasterBudgets & {
  runner_id: string
  max_parallel: number
  graph_policy?: GraphPolicy
}

type LegacyMasterDesk = Omit<MasterDesk, 'master_run' | 'event_cursor' | 'jobs' | 'focus' | 'decisions'> & {
  master_run?: MasterDesk['master_run']
  alpha_run?: MasterDesk['master_run']
  event_cursor?: number
  focus?: MasterFocusSnapshot
  jobs: (MasterJob & { alpha_session_id?: number | null })[]
  decisions?: MasterDecision[]
}

export function normalizeMasterDesk(payload: LegacyMasterDesk): MasterDesk {
  const { alpha_run: legacyRun, ...canonical } = payload
  return {
    ...canonical,
    session: {
      ...payload.session,
      title: payload.session.title === 'Alpha' ? 'Master' : payload.session.title,
      mode: payload.session.mode === 'alpha' ? 'master' : payload.session.mode,
    },
    master_run: payload.master_run ?? legacyRun ?? null,
    event_cursor: Number.isSafeInteger(payload.event_cursor)
      ? Math.max(0, payload.event_cursor || 0)
      : 0,
    jobs: payload.jobs.map(job => {
      const legacy = job as MasterJob & { alpha_session_id?: number | null }
      const normalized = {
        ...legacy,
        origin_master_session_id:
          legacy.origin_master_session_id ?? legacy.alpha_session_id ?? null,
      }
      delete (normalized as { alpha_session_id?: number | null }).alpha_session_id
      return normalized
    }),
    focus: payload.focus ?? {
      current_epoch_id: null,
      current_container_id: null,
      pending_container_id: null,
      pending: false,
      version: 0,
    },
    attention: payload.attention.map(item => {
      const target = {
        ...item.target,
        view: item.target.view === 'alpha' ? 'master' : item.target.view,
        origin_master_session_id:
          item.target.origin_master_session_id ?? item.target.alpha_session_id,
      }
      delete (target as { alpha_session_id?: number | null }).alpha_session_id
      return {
        ...item,
        kind: item.kind.replace(/^alpha(?=_|$)/, 'master'),
        title: item.title.replace(/\bAlpha\b/g, 'Master'),
        target,
      }
    }),
    decisions: payload.decisions ?? [],
  }
}

export const getMasterDesk = async (token: string, signal?: AbortSignal): Promise<MasterDesk> =>
  normalizeMasterDesk(await api<LegacyMasterDesk>('/api/master/desk', token, { signal }))
export const sendMasterMessage = (
  token: string,
  content: string,
  context: MasterMessageContext,
  signal?: AbortSignal,
) =>
  api<{
    run_id: number
    session_id: number
    status: string
    message: ChatMessage
    focus: MasterFocusSnapshot
  }>('/api/master/messages', token, {
    method: 'POST',
    body: JSON.stringify({ content, ...context }),
    signal,
  })
export const updateMasterFocus = (
  token: string,
  containerId: number | null,
  version: number,
  signal?: AbortSignal,
) =>
  api<{
    focus: MasterFocusSnapshot
    pending: boolean
    changed: boolean
  }>('/api/master/focus', token, {
    method: 'PUT',
    body: JSON.stringify({ container_id: containerId, version }),
    signal,
  })
export const getMasterSettings = (token: string) => api<MasterSettings>('/api/settings/master', token)
export const saveMasterSettings = (
  token: string,
  body: Partial<MasterSettings>,
  signal?: AbortSignal,
) =>
  api<MasterSettings>('/api/settings/master', token, {
    method: 'PUT',
    body: JSON.stringify(body),
    signal,
  })
export const getAttention = (token: string) => api<{ items: AttentionItem[]; count: number }>('/api/attention', token)
export const actAttention = (token: string, id: string, action: string) =>
  api<{ ok: boolean; id: string; action: string }>(`/api/attention/${encodeURIComponent(id)}/act`, token, { method: 'POST', body: JSON.stringify({ action }) })
export const getMasterDecision = (token: string, decisionId: number) =>
  api<MasterDecision>(`/api/master/decisions/${decisionId}`, token)
export const deferMasterDecision = (
  token: string,
  decisionId: number,
  expectedVersion: number,
) =>
  api<MasterDecision>(`/api/master/decisions/${decisionId}/defer`, token, {
    method: 'POST',
    body: JSON.stringify({ expected_version: expectedVersion }),
  })
export const resolveMasterDecision = (
  token: string,
  decisionId: number,
  expectedVersion: number,
  response: string,
) =>
  api<MasterDecision>(`/api/master/decisions/${decisionId}/resolve`, token, {
    method: 'POST',
    body: JSON.stringify({ expected_version: expectedVersion, response }),
  })
export const previewCheckpointRestore = (token: string, jobId: number, checkpointId: number) =>
  api<{ checkpoint_id: number; job_id: number; job_title: string; database_scope: string[]; git_refs: MasterCheckpoint['git_refs']; conflicts: { id: number; title: string }[]; can_restore: boolean }>(`/api/jobs/${jobId}/checkpoint/${checkpointId}/restore`, token)
export const restoreCheckpoint = (token: string, jobId: number, checkpointId: number) =>
  api<{
    restored: string[]
    git_restored: string[]
    projection_repair: {
      outbox_id: number
      state: 'pending' | 'projected' | 'failed_attribution'
      failure_code: string | null
    } | null
  }>(`/api/jobs/${jobId}/checkpoint/restore`, token, { method: 'POST', body: JSON.stringify({ checkpoint_id: checkpointId, confirm: true }) })
export const setCheckpointPinned = (token: string, jobId: number, checkpointId: number, pinned: boolean) =>
  api<MasterCheckpoint>(`/api/jobs/${jobId}/checkpoint/${checkpointId}/pin`, token, { method: 'PUT', body: JSON.stringify({ pinned }) })

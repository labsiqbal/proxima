import { api } from './client'
import type { ChatSession, Job } from '../types'

export type AlphaCapacity = { running: number; max: number; free: number; queued: number }
export type AlphaBudgets = {
  unattended: boolean
  budget_turns: number
  budget_wall_seconds: number
  budget_tokens: number | null
  tour_core_done: boolean
}
export type AlphaCheckpoint = {
  id: number
  job_id: number
  pinned: boolean
  created_at: string
  payload: { job?: Record<string, unknown> }
  git_refs: { repo_path?: string; worktree_path?: string; sha?: string; restore_strategy?: 'worktree_reset' | 'reference_only' }[]
}
export type AlphaJob = Job & {
  desk_status: string
  run_status?: string | null
  project_slug?: string | null
  project_name?: string | null
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
}
export type AlphaDesk = {
  session: ChatSession
  alpha_run?: { id: number; status: string } | null
  backing_runner: string
  jobs: AlphaJob[]
  unattended: boolean
  budgets: AlphaBudgets
  capacity: AlphaCapacity
  attention: AttentionItem[]
  checkpoints: AlphaCheckpoint[]
}
export type AlphaSettings = AlphaBudgets & { runner_id: string; max_parallel: number }

export const getAlphaDesk = (token: string) => api<AlphaDesk>('/api/alpha/desk', token)
export const sendAlphaMessage = (token: string, content: string) =>
  api<{ run_id: number; session_id: number; status: string }>('/api/alpha/messages', token, { method: 'POST', body: JSON.stringify({ content }) })
export const getAlphaSettings = (token: string) => api<AlphaSettings>('/api/settings/alpha', token)
export const saveAlphaSettings = (token: string, body: Partial<AlphaSettings>) =>
  api<AlphaSettings>('/api/settings/alpha', token, { method: 'PUT', body: JSON.stringify(body) })
export const getAttention = (token: string) => api<{ items: AttentionItem[]; count: number }>('/api/attention', token)
export const actAttention = (token: string, id: string, action: string) =>
  api<{ ok: boolean; id: string; action: string }>(`/api/attention/${encodeURIComponent(id)}/act`, token, { method: 'POST', body: JSON.stringify({ action }) })
export const previewCheckpointRestore = (token: string, jobId: number, checkpointId: number) =>
  api<{ checkpoint_id: number; job_id: number; job_title: string; database_scope: string[]; git_refs: AlphaCheckpoint['git_refs']; conflicts: { id: number; title: string }[]; can_restore: boolean }>(`/api/jobs/${jobId}/checkpoint/${checkpointId}/restore`, token)
export const restoreCheckpoint = (token: string, jobId: number, checkpointId: number) =>
  api<{ restored: string[]; git_restored: string[] }>(`/api/jobs/${jobId}/checkpoint/restore`, token, { method: 'POST', body: JSON.stringify({ checkpoint_id: checkpointId, confirm: true }) })
export const setCheckpointPinned = (token: string, jobId: number, checkpointId: number, pinned: boolean) =>
  api<AlphaCheckpoint>(`/api/jobs/${jobId}/checkpoint/${checkpointId}/pin`, token, { method: 'PUT', body: JSON.stringify({ pinned }) })

import { api } from './client'
import type { ChatSession, Job } from '../types'

export type MasterCapacity = { running: number; max: number; free: number; queued: number }
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
export type MasterDesk = {
  session: ChatSession
  master_run?: { id: number; status: string } | null
  backing_runner: string
  jobs: MasterJob[]
  unattended: boolean
  budgets: MasterBudgets
  capacity: MasterCapacity
  attention: AttentionItem[]
  checkpoints: MasterCheckpoint[]
}
export type MasterSettings = MasterBudgets & { runner_id: string; max_parallel: number }

type LegacyMasterDesk = Omit<MasterDesk, 'master_run' | 'jobs'> & {
  master_run?: MasterDesk['master_run']
  alpha_run?: MasterDesk['master_run']
  jobs: (MasterJob & { alpha_session_id?: number | null })[]
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
  }
}

export const getMasterDesk = async (token: string): Promise<MasterDesk> =>
  normalizeMasterDesk(await api<LegacyMasterDesk>('/api/master/desk', token))
export const sendMasterMessage = (token: string, content: string) =>
  api<{ run_id: number; session_id: number; status: string }>('/api/master/messages', token, { method: 'POST', body: JSON.stringify({ content }) })
export const getMasterSettings = (token: string) => api<MasterSettings>('/api/settings/master', token)
export const saveMasterSettings = (token: string, body: Partial<MasterSettings>) =>
  api<MasterSettings>('/api/settings/master', token, { method: 'PUT', body: JSON.stringify(body) })
export const getAttention = (token: string) => api<{ items: AttentionItem[]; count: number }>('/api/attention', token)
export const actAttention = (token: string, id: string, action: string) =>
  api<{ ok: boolean; id: string; action: string }>(`/api/attention/${encodeURIComponent(id)}/act`, token, { method: 'POST', body: JSON.stringify({ action }) })
export const previewCheckpointRestore = (token: string, jobId: number, checkpointId: number) =>
  api<{ checkpoint_id: number; job_id: number; job_title: string; database_scope: string[]; git_refs: MasterCheckpoint['git_refs']; conflicts: { id: number; title: string }[]; can_restore: boolean }>(`/api/jobs/${jobId}/checkpoint/${checkpointId}/restore`, token)
export const restoreCheckpoint = (token: string, jobId: number, checkpointId: number) =>
  api<{ restored: string[]; git_restored: string[] }>(`/api/jobs/${jobId}/checkpoint/restore`, token, { method: 'POST', body: JSON.stringify({ checkpoint_id: checkpointId, confirm: true }) })
export const setCheckpointPinned = (token: string, jobId: number, checkpointId: number, pinned: boolean) =>
  api<MasterCheckpoint>(`/api/jobs/${jobId}/checkpoint/${checkpointId}/pin`, token, { method: 'PUT', body: JSON.stringify({ pinned }) })

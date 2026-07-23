import { api } from './client'

export type DebugRun = {
  id: number
  session_id: number
  status: string
  runner_id?: string | null
  kind?: string | null
  prompt?: string | null
  started_at?: string | null
  finished_at?: string | null
  heartbeat_at?: string | null
  created_at: string
  session_title?: string | null
  profile_name?: string | null
}

export type DebugJob = {
  id: number
  session_id?: number | null
  title: string
  status: string
  current_step_idx: number
  workflow_id?: number | null
  schedule_id?: number | null
  created_at: string
  updated_at: string
  session_title?: string | null
}

export type DebugLogs = {
  logs: string
  logError?: string
  /** Empty-journal guidance when the configured systemd unit has no entries. */
  logHint?: string
  /** systemd --user unit journalctl queried (e.g. proxima.service). */
  serviceUnit?: string
  runs: DebugRun[]
  rawActiveSessionIds: number[]
  activeRuns: DebugRun[]
  staleRuns: DebugRun[]
  orphanedJobs: DebugJob[]
}

/** Panel-head label for journal line counts ("1 line" / "N lines"). */
export function debugLogLineLabel(count: number): string {
  return count === 1 ? '1 line' : `${count} lines`
}

export const getDebugLogs = (token: string, limit = 240) =>
  api<DebugLogs>(`/api/debug/logs?limit=${limit}`, token)

export const reapOrphanedJobs = (token: string) =>
  api<{ ok: boolean; count: number }>('/api/debug/reap-orphaned-jobs', token, { method: 'POST' })

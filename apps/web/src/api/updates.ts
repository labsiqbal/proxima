import { api } from './client'

export type UpdateLatest = { version: string; notes: string; url: string; published_at: string | null }
export type UpdateStatus = {
  current_version: string
  latest: UpdateLatest | null
  update_available: boolean
  state: 'idle' | 'running' | 'failed'
  checked_at: string | null
  last_error: string | null
  log_tail: string | null
  apply_supported: boolean
  manual_command: string
}

export const getUpdateStatus = (token: string) => api<UpdateStatus>('/api/update/status', token)
export const checkForUpdate = (token: string) => api<UpdateStatus>('/api/update/check', token, { method: 'POST' })
export const applyUpdate = (token: string) => api<{ started: boolean; target: string }>('/api/update/apply', token, { method: 'POST' })

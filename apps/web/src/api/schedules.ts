import { api } from './client'
import type { Job, Schedule } from '../types'

export type ScheduleWrite = {
  cron?: string
  timezone?: string
  bindings?: Record<string, unknown>
  overlap_policy?: 'skip' | 'allow'
  enabled?: boolean
}

export const listSchedules = (token: string, workflowId?: number) =>
  api<Schedule[]>(`/api/schedules${workflowId != null ? `?workflow_id=${workflowId}` : ''}`, token)

export const createSchedule = (token: string, body: ScheduleWrite & { workflow_id: number; cron: string; project_id?: number | null }) =>
  api<Schedule>('/api/schedules', token, { method: 'POST', body: JSON.stringify(body) })

export const updateSchedule = (token: string, id: number, body: ScheduleWrite) =>
  api<Schedule>(`/api/schedules/${id}`, token, { method: 'PATCH', body: JSON.stringify(body) })

export const deleteSchedule = (token: string, id: number) =>
  api<{ ok: true; id: number }>(`/api/schedules/${id}`, token, { method: 'DELETE' })

// Fire a schedule without waiting for its cron. Returns the job it spawned, so the
// caller can open the task and watch what the cron would have run.
export const runScheduleNow = (token: string, id: number) =>
  api<Job>(`/api/schedules/${id}/run`, token, { method: 'POST' })

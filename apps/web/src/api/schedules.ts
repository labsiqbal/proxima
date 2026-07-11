import { api } from './client'
import type { Schedule } from '../types'

export const listSchedules = (token: string, workflowId?: number) =>
  api<Schedule[]>(`/api/schedules${workflowId != null ? `?workflow_id=${workflowId}` : ''}`, token)

export const createSchedule = (token: string, body: { workflow_id: number; cron: string; input?: any; overlap_policy?: 'skip' | 'allow'; project_id?: number | null; enabled?: boolean }) =>
  api<Schedule>('/api/schedules', token, { method: 'POST', body: JSON.stringify(body) })

export const updateSchedule = (token: string, id: number, body: { cron?: string; input?: any; overlap_policy?: 'skip' | 'allow'; enabled?: boolean }) =>
  api<Schedule>(`/api/schedules/${id}`, token, { method: 'PATCH', body: JSON.stringify(body) })

export const deleteSchedule = (token: string, id: number) =>
  api<{ ok: true; id: number }>(`/api/schedules/${id}`, token, { method: 'DELETE' })

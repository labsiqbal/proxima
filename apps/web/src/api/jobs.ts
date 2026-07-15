import { api } from './client'
import type { Job, JobStatus } from '../types'

// No workflow_id = ad-hoc 1-step job; put the task text in input.brief.
export const createJob = (token: string, body: { workflow_id?: number | null; project_id?: number | null; project_slug?: string | null; profile_id?: number | null; input?: any; title?: string }) =>
  api<Job>('/api/jobs', token, { method: 'POST', body: JSON.stringify(body) })

export const startJob = (token: string, id: number) => api<Job>(`/api/jobs/${id}/start`, token, { method: 'POST' })

export const linkJobRun = (token: string, id: number, runId: number) => api<Job>(`/api/jobs/${id}/link-run`, token, { method: 'POST', body: JSON.stringify({ run_id: runId }) })

export const getJob = (token: string, id: number) => api<Job>(`/api/jobs/${id}`, token)

export const deleteJob = (token: string, id: number) => api<{ ok: boolean }>(`/api/jobs/${id}`, token, { method: 'DELETE' })

// Approve a job at a review gate. A MID-workflow gate resumes (status:'running');
// the FINAL review finalizes (status:'done'). edited_output, when given, replaces
// the just-reviewed step's output_summary before resuming.
export const approveJob = (token: string, id: number, body?: { edited_output?: string }) =>
  api<Job>(`/api/jobs/${id}/approve`, token, { method: 'POST', body: body ? JSON.stringify(body) : undefined })

export const listJobs = (token: string, params: { status?: JobStatus; workflow_id?: number; project_id?: number; project_slug?: string | null; include_archived?: boolean; limit?: number; offset?: number } = {}) => {
  const q = new URLSearchParams()
  if (params.status) q.set('status', params.status)
  if (params.workflow_id != null) q.set('workflow_id', String(params.workflow_id))
  if (params.project_id != null) q.set('project_id', String(params.project_id))
  if (params.project_slug) q.set('project_slug', params.project_slug)
  if (params.include_archived) q.set('include_archived', 'true')
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return api<{ items: Job[]; total: number; limit: number; offset: number }>(`/api/jobs${qs ? `?${qs}` : ''}`, token)
}

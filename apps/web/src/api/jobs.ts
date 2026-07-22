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
// the just-reviewed step's output_summary before resuming. For a repo job the
// final approve is also the merge point: the reviewed changes land on the code
// area's own line, and a clash surfaces as an error while the job stays in review.
export const approveJob = (token: string, id: number, body?: { edited_output?: string }) =>
  api<Job>(`/api/jobs/${id}/approve`, token, { method: 'POST', body: body ? JSON.stringify(body) : undefined })

// Reject a job waiting for review (either engine — plans share the job row). The
// one-line reason is required; the job fails with it recorded, and a repo job's
// isolated copy is discarded without touching the project.
export const rejectJob = (token: string, id: number, reason: string) =>
  api<Job>(`/api/jobs/${id}/reject`, token, { method: 'POST', body: JSON.stringify({ reason }) })

// Satpam restart approval (slice 12): a repo job's restart-clean discards the
// worktree, so the satpam only queues it - these are the owner's two verdicts.
export const approveSatpamRestart = (token: string, jobId: number, interventionId: number) =>
  api<Job>(`/api/jobs/${jobId}/satpam/${interventionId}/approve`, token, { method: 'POST' })

export const dismissSatpamRestart = (token: string, jobId: number, interventionId: number) =>
  api<Job>(`/api/jobs/${jobId}/satpam/${interventionId}/dismiss`, token, { method: 'POST' })

// A repo job's reviewable change: per-file statuses plus one unified patch.
// Readable while the job works, at review, and after the merge (read off the
// code area's own history).
export type JobDiff = {
  job_id: number
  branch: string
  base_branch: string
  worktree_status: import('../types').JobWorktree['status']
  base_commit: string
  head_commit: string
  files: { path: string; old_path: string | null; status: string }[]
  patch: string
  patch_truncated: boolean
  summary: string
}

export const getJobDiff = (token: string, id: number) => api<JobDiff>(`/api/jobs/${id}/diff`, token)

// Retry the push-after-merge for a locally merged repo job (T9): a failed
// push never un-merged anything, so this only re-runs the host's own
// `git push` and reports the fresh outcome on the worktree.
export const retryJobPush = (token: string, id: number) =>
  api<{ job_id: number; status: 'pushed' | 'failed'; error?: string; worktree: import('../types').JobWorktree }>(`/api/jobs/${id}/push`, token, { method: 'POST' })

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

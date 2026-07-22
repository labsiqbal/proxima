import { api } from './client'

// The Archive registry (Phase-1 slice 8, T4): durable deliverable records with
// lineage and ONE approval status. The scanner feeds it; it never forgets.

export type ArchiveStatus = 'draft' | 'review' | 'approved' | 'superseded'

export type ArchiveRecord = {
  id: number
  slug: string
  name: string
  type: string
  path: string
  area: string
  size: number | null
  status: ArchiveStatus
  approved_at: string | null
  version: number
  superseded_by: number | null
  session_id: number | null
  job_id: number | null
  node_id: string | null
  run_id: number | null
  file_missing: boolean
  produced_at: string
  project_id: number
  project_slug: string
  project_name: string
  session_title: string | null
  job_title: string | null
  job_engine: string | null
}

export type ArchiveVersion = {
  id: number
  slug: string
  version: number
  status: ArchiveStatus
  produced_at: string
  approved_at: string | null
  superseded_by: number | null
}

export type ArchiveRecordDetail = ArchiveRecord & {
  versions: ArchiveVersion[]
  prev_slug: string | null
  next_slug: string | null
  superseded_by_slug: string | null
}

export type ArchiveCounts = { by_type: Record<string, number>; by_status: Record<string, number> }

export type ArchiveListParams = {
  project?: string
  type?: string
  status?: ArchiveStatus | ''
  q?: string
  days?: number
  path?: string
  limit?: number
  offset?: number
}

export const listArchive = (token: string, params: ArchiveListParams = {}) => {
  const q = new URLSearchParams()
  if (params.project) q.set('project', params.project)
  if (params.type) q.set('type', params.type)
  if (params.status) q.set('status', params.status)
  if (params.q) q.set('q', params.q)
  if (params.days) q.set('days', String(params.days))
  if (params.path) q.set('path', params.path)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return api<{ items: ArchiveRecord[]; total: number; limit: number; offset: number; counts: ArchiveCounts }>(
    `/api/archive${qs ? `?${qs}` : ''}`, token)
}

export const getArchiveRecord = (token: string, project: string, slug: string) =>
  api<ArchiveRecordDetail>(`/api/archive/${encodeURIComponent(project)}/${encodeURIComponent(slug)}`, token)

// The Archive door of the one two-door status (the other door is the job-review
// approve, which writes the same field on the backend).
export const setArchiveStatus = (token: string, id: number, status: ArchiveStatus) =>
  api<ArchiveRecord>(`/api/archive/records/${id}/status`, token, { method: 'POST', body: JSON.stringify({ status }) })

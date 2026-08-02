import { api } from './client'
import type { FileTarget } from '../types'

// The deliverable-record registry (Phase-1 slice 8, T4; surfaced as the Files
// "Deliverables" lens since prune Part D, #139): durable records with lineage
// and ONE approval status. Record paths are container-relative real paths -
// the same paths the Files tree browses. The scanner feeds the registry; it
// never forgets: records whose file is gone live under the history filter.

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
  target: FileTarget | null
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

export type ArchiveCounts = { by_type: Record<string, number>; by_status: Record<string, number>; missing?: number }

/** Badge data for the Files tree: the latest record per path in one project. */
export type ArchiveBadge = {
  id: number
  slug: string
  name: string
  type: string
  path: string
  status: ArchiveStatus
  version: number
  file_missing: boolean
}

export type ArchiveListParams = {
  project?: string
  type?: string
  status?: ArchiveStatus | ''
  q?: string
  days?: number
  path?: string
  /** The history filter (#139): 1 = only records whose file is gone, 0 = only present. */
  missing?: 0 | 1
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
  if (params.missing != null) q.set('missing', String(params.missing))
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return api<{ items: ArchiveRecord[]; total: number; limit: number; offset: number; counts: ArchiveCounts }>(
    `/api/archive${qs ? `?${qs}` : ''}`, token)
}

export const listArchiveBadges = (token: string, project: string) =>
  api<{ items: ArchiveBadge[] }>(`/api/archive/badges?project=${encodeURIComponent(project)}`, token)

export const getArchiveRecord = (token: string, project: string, slug: string) =>
  api<ArchiveRecordDetail>(`/api/archive/${encodeURIComponent(project)}/${encodeURIComponent(slug)}`, token)

// The Archive door of the one two-door status (the other door is the job-review
// approve, which writes the same field on the backend).
export const setArchiveStatus = (token: string, id: number, status: ArchiveStatus) =>
  api<ArchiveRecord>(`/api/archive/records/${id}/status`, token, { method: 'POST', body: JSON.stringify({ status }) })

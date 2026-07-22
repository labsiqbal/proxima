import { api } from './client'
import type { GraphJob, GraphTemplate, WorkflowGraph, WorkflowInput } from '../types'

export type GraphJobCreate = {
  title: string
  graph: WorkflowGraph
  input?: Record<string, unknown>
  project_id?: number | null
  project_slug?: string | null
  profile_id?: number | null
  workflow_id?: number | null
}

export const createGraphJob = (token: string, body: GraphJobCreate) =>
  api<GraphJob>('/api/graph/jobs', token, { method: 'POST', body: JSON.stringify(body) })

export const listGraphJobs = (token: string, projectSlug?: string | null) => {
  const query = projectSlug ? `?project_slug=${encodeURIComponent(projectSlug)}` : ''
  return api<{ items: GraphJob[] }>(`/api/graph/jobs${query}`, token)
}

export const listGraphTemplates = (token: string, projectSlug?: string | null) => {
  const query = projectSlug ? `?project_slug=${encodeURIComponent(projectSlug)}` : ''
  return api<{ items: GraphTemplate[] }>(`/api/graph/templates${query}`, token)
}

export const getGraphJob = (token: string, jobId: number) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}`, token)

export const updateGraphPlan = (token: string, jobId: number, graph: WorkflowGraph) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/graph`, token, {
    method: 'PATCH',
    body: JSON.stringify({ graph }),
  })

export const startGraphJob = (token: string, jobId: number) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/start`, token, { method: 'POST' })

export const editGraphNodeOutput = (token: string, jobId: number, nodeId: string, value: unknown) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/nodes/${encodeURIComponent(nodeId)}/output`, token, {
    method: 'PATCH',
    body: JSON.stringify({ value }),
  })

export const rerunGraphNode = (token: string, jobId: number, nodeId: string) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/nodes/${encodeURIComponent(nodeId)}/rerun`, token, { method: 'POST' })

export const approveGraphNode = (token: string, jobId: number, nodeId: string) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/nodes/${encodeURIComponent(nodeId)}/approve`, token, { method: 'POST' })

// Decision-hold (slice 12): answer a parked node's DECISION_NEEDED question.
// Works while the plan is running - independent branches never waited.
export const answerGraphNode = (token: string, jobId: number, nodeId: string, answer: string) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/nodes/${encodeURIComponent(nodeId)}/answer`, token, {
    method: 'POST',
    body: JSON.stringify({ answer }),
  })

// The one-time, hash-bound script approval (T6): trusts the script's CURRENT
// content and reruns the blocked step. Unchanged scripts never ask again.
export const approveGraphNodeScript = (token: string, jobId: number, nodeId: string) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/nodes/${encodeURIComponent(nodeId)}/approve-script`, token, { method: 'POST' })

export const approveGraphJob = (token: string, jobId: number) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/approve`, token, { method: 'POST' })

export const saveGraphTemplate = (
  token: string,
  jobId: number,
  body: { name?: string; description?: string; category?: string; inputs?: WorkflowInput[] },
) => api<GraphTemplate>(`/api/graph/jobs/${jobId}/save-template`, token, {
  method: 'POST',
  body: JSON.stringify(body),
})

// Deleting a plan is deleting its job row: node states cascade, and every session the
// job owns (the main thread plus one per executed node) is swept server-side.
export const deleteGraphJob = (token: string, jobId: number) =>
  api<{ ok: boolean }>(`/api/jobs/${jobId}`, token, { method: 'DELETE' })

// A template is a workflows row; the shared delete takes either engine. Schedules
// referencing it go with it — a schedule for a deleted workflow could never run.
export const deleteGraphTemplate = (token: string, templateId: number) =>
  api<{ ok: boolean }>(`/api/workflows/${templateId}`, token, { method: 'DELETE' })

// Lifecycle only: pausing (draft) takes a template out of the scheduler's rotation —
// it fires none but 'active' — and resuming puts it back. Authoring stays on the canvas.
export const setGraphTemplateStatus = (token: string, templateId: number, status: 'active' | 'draft' | 'archived') =>
  api<GraphTemplate>(`/api/workflows/${templateId}`, token, { method: 'PATCH', body: JSON.stringify({ status }) })

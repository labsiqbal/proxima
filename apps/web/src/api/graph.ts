import { api } from './client'
import type { GraphJob, GraphTemplate, WorkflowGraph } from '../types'

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

export const approveGraphJob = (token: string, jobId: number) =>
  api<GraphJob>(`/api/graph/jobs/${jobId}/approve`, token, { method: 'POST' })

export const saveGraphTemplate = (
  token: string,
  jobId: number,
  body: { name?: string; description?: string; category?: string },
) => api<{ id: number; name: string }>(`/api/graph/jobs/${jobId}/save-template`, token, {
  method: 'POST',
  body: JSON.stringify(body),
})

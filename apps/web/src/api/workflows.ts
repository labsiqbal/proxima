import { api } from './client'
import type { Workflow, WorkflowInput } from '../types'

// Per-step input when creating/updating a workflow — the server fills id and
// depends_on. Steps may optionally carry rules, skill_ids, review_required.
export type StepInput = { name: string; instruction: string; expected_output?: string; type?: string; rules?: string | null; skill_ids?: string[] | null; review_required?: boolean }

export const listWorkflows = (token: string, params: { project_id?: number | null; project_slug?: string | null } = {}) => {
  const q = new URLSearchParams()
  if (params.project_id != null) q.set('project_id', String(params.project_id))
  if (params.project_slug) q.set('project_slug', params.project_slug)
  return api<Workflow[]>(`/api/workflows${q.toString() ? `?${q.toString()}` : ''}`, token)
}

export const createWorkflow = (token: string, body: { name: string; description?: string; category?: string; project_id?: number | null; project_slug?: string | null; inputs?: WorkflowInput[]; steps: StepInput[] }) =>
  api<Workflow>('/api/workflows', token, { method: 'POST', body: JSON.stringify(body) })

export const getWorkflow = (token: string, id: number) => api<Workflow>(`/api/workflows/${id}`, token)

export const updateWorkflow = (token: string, id: number, body: { name?: string; description?: string; category?: string; status?: 'active' | 'draft' | 'archived'; inputs?: WorkflowInput[]; steps?: StepInput[] }) =>
  api<Workflow>(`/api/workflows/${id}`, token, { method: 'PATCH', body: JSON.stringify(body) })

// Archive = soft-delete (PATCH status:'archived').
export const archiveWorkflow = (token: string, id: number) =>
  api<Workflow>(`/api/workflows/${id}`, token, { method: 'PATCH', body: JSON.stringify({ status: 'archived' }) })

export const deleteWorkflow = (token: string, id: number) =>
  api<{ ok: boolean }>(`/api/workflows/${id}`, token, { method: 'DELETE' })

import type { ChatSession } from '../types'
// Get-or-create the workflow's iterate/test chat (a sandbox session linked to it).
export const iterateWorkflow = (token: string, id: number) =>
  api<ChatSession>(`/api/workflows/${id}/iterate`, token, { method: 'POST' })

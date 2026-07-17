import { api } from './client'
import type { Workflow, WorkflowInput } from '../types'

// Per-step input when updating a linear workflow — the server fills id and depends_on.
// Only IterateStage (the legacy linear surface, reachable from pre-existing sessions)
// still edits these; new workflows are authored as graphs on the canvas.
export type StepInput = { name: string; instruction: string; expected_output?: string; type?: string; rules?: string | null; skill_ids?: string[] | null; review_required?: boolean }

export const getWorkflow = (token: string, id: number) => api<Workflow>(`/api/workflows/${id}`, token)

export const updateWorkflow = (token: string, id: number, body: { name?: string; description?: string; category?: string; status?: 'active' | 'draft' | 'archived'; inputs?: WorkflowInput[]; steps?: StepInput[] }) =>
  api<Workflow>(`/api/workflows/${id}`, token, { method: 'PATCH', body: JSON.stringify(body) })

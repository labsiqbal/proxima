import { api } from './client'
import type { RunEvent } from '../types'

export type PromptMode = 'chat' | 'brainstorm' | 'debate'
export const createRun = (token: string, sessionId: number, body: { message: string; display_message?: string; instant_result?: string; profile_id?: number | null; participant_profile_ids?: number[]; model?: string | null; prompt_mode?: PromptMode; project_slug?: string | null }) => api<{ run_id: number; session_id: number; status: string }>(`/api/sessions/${sessionId}/runs`, token, { method: 'POST', body: JSON.stringify(body) })
export const cancelRun = (token: string, runId: number) => api<{ ok: boolean; run_id: number; status: string }>(`/api/runs/${runId}/cancel`, token, { method: 'POST' })
export const deleteRun = (token: string, runId: number) => api<{ ok: boolean; run_id: number }>(`/api/runs/${runId}`, token, { method: 'DELETE' })
export const listEvents = (token: string, sessionId: number, afterId = 0) => api<{ events: RunEvent[] }>(`/api/sessions/${sessionId}/events?after_id=${afterId}`, token)
// Deliver an interactive card choice (AskUserQuestion / approval) back to the agent.
export const respondPermission = (token: string, runId: number, request_id: string, option_id: string) =>
  api<{ ok: boolean; run_id: number }>(`/api/runs/${runId}/permission`, token, { method: 'POST', body: JSON.stringify({ request_id, option_id }) })
// Sessions with an in-flight run (for the sidebar thinking indicator).
export const activeRuns = (token: string) => api<{ session_ids: number[] }>(`/api/runs/active`, token)
// Autonomous goal loop: agent works across turns until done/blocked/capped.
export const startGoal = (token: string, sessionId: number, body: { objective: string; profile_id?: number | null; model?: string | null; max_iter?: number }) =>
  api<{ run_id: number; session_id: number; status: string }>(`/api/sessions/${sessionId}/goal`, token, { method: 'POST', body: JSON.stringify(body) })
export const cancelGoal = (token: string, sessionId: number) => api<{ status: string }>(`/api/sessions/${sessionId}/goal/cancel`, token, { method: 'POST' })

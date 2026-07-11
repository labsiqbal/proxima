import { api } from './client'
import type { ChatMessage, ChatSession, GoalState } from '../types'

export const listSessions = (token: string) => api<{ sessions: ChatSession[] }>('/api/sessions', token)
export const createSession = (token: string, body: { title?: string; project_slug?: string | null; profile_id?: number | null; visibility?: 'private' | 'project'; mode?: 'chat' | 'design' }) => api<ChatSession>('/api/sessions', token, { method: 'POST', body: JSON.stringify(body) })
export const listMessages = (token: string, sessionId: number) => api<{ messages: ChatMessage[]; goal: GoalState | null }>(`/api/sessions/${sessionId}/messages`, token)
export const renameSession = (token: string, sessionId: number, title: string) => api<ChatSession>(`/api/sessions/${sessionId}`, token, { method: 'PATCH', body: JSON.stringify({ title }) })
// Move a chat into a project (slug) or detach it (null). project_slug must be
// sent explicitly so the backend knows to reassign vs leave it untouched.
export const moveSessionToProject = (token: string, sessionId: number, projectSlug: string | null) => api<ChatSession>(`/api/sessions/${sessionId}`, token, { method: 'PATCH', body: JSON.stringify({ project_slug: projectSlug }) })
// Persist which agent profile runs this chat so it survives a reload instead of
// reverting to the account default.
export const setSessionProfile = (token: string, sessionId: number, profileId: number) => api<ChatSession>(`/api/sessions/${sessionId}`, token, { method: 'PATCH', body: JSON.stringify({ profile_id: profileId }) })
export const deleteSession = (token: string, sessionId: number) => api<{ ok: boolean }>(`/api/sessions/${sessionId}`, token, { method: 'DELETE' })
// Promote a chat into a reusable workflow draft. Enqueues a run (202); the
// result arrives async as a `workflow.draft` event on the session's stream.
export const promoteWorkflow = (token: string, sessionId: number, profileId?: number | null) => api<{ run_id: number; session_id: number; status: string }>(`/api/sessions/${sessionId}/promote-workflow`, token, { method: 'POST', body: JSON.stringify({ profile_id: profileId ?? null }) })

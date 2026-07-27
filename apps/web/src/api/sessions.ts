import { api } from './client'
import type { ChatMessage, ChatSession, GoalState } from '../types'

export const listSessions = (token: string) => api<{ sessions: ChatSession[] }>('/api/sessions', token)
export const getSession = (token: string, sessionId: number) => api<ChatSession>(`/api/sessions/${sessionId}`, token)
export const createSession = (token: string, body: { title?: string; project_slug?: string | null; profile_id?: number | null; visibility?: 'private' | 'project'; mode?: 'chat' | 'design' }) => api<ChatSession>('/api/sessions', token, { method: 'POST', body: JSON.stringify(body) })
export const listMessages = (token: string, sessionId: number) => api<{ messages: ChatMessage[]; goal: GoalState | null }>(`/api/sessions/${sessionId}/messages`, token)
export const renameSession = (token: string, sessionId: number, title: string) => api<ChatSession>(`/api/sessions/${sessionId}`, token, { method: 'PATCH', body: JSON.stringify({ title }) })
// Persist which agent profile runs this chat so it survives a reload instead of
// reverting to the account default.
export const setSessionProfile = (token: string, sessionId: number, profileId: number) => api<ChatSession>(`/api/sessions/${sessionId}`, token, { method: 'PATCH', body: JSON.stringify({ profile_id: profileId }) })
export const deleteSession = (token: string, sessionId: number) => api<{ ok: boolean }>(`/api/sessions/${sessionId}`, token, { method: 'DELETE' })
export type TurnRestorePreview = { message_id: number; paths: string[]; warning: string | null; active_master_tasks: { id: number; title: string }[] }
type LegacyTurnRestorePreview = Omit<TurnRestorePreview, 'active_master_tasks'> & {
  active_alpha_jobs?: { id: number; title: string }[]
  active_master_tasks?: { id: number; title: string }[]
}
export const previewTurnRestore = async (token: string, messageId: number): Promise<TurnRestorePreview> => {
  const payload = await api<LegacyTurnRestorePreview>(`/api/chat/messages/${messageId}/restore-turn`, token)
  return {
    ...payload,
    active_master_tasks: payload.active_master_tasks ?? payload.active_alpha_jobs ?? [],
  }
}
export const restoreTurn = (token: string, messageId: number, acceptActiveMaster = false) => api<{ paths: string[]; restored: number; warning?: string | null }>(`/api/chat/messages/${messageId}/restore-turn`, token, { method: 'POST', body: JSON.stringify({ confirm: true, accept_active_master: acceptActiveMaster }) })
// Promote a chat into a reusable workflow draft. Enqueues a run (202); the
// result arrives async as a `workflow.draft` event on the session's stream.
export const promoteWorkflow = (token: string, sessionId: number, profileId?: number | null, engine: 'auto' | 'linear' | 'graph' = 'auto') => api<{ run_id: number; session_id: number; status: string }>(`/api/sessions/${sessionId}/promote-workflow`, token, { method: 'POST', body: JSON.stringify({ profile_id: profileId ?? null, engine }) })

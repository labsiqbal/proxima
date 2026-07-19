import { api } from './client'

export type WikiNoteRaw = { path: string; content: string }

// Save-to-wiki: kick off a wiki_draft run; the draft arrives via the session's
// event stream as a `wiki.draft` event, then commit writes the approved note.
export const draftWikiNote = (token: string, sessionId: number, profileId?: number | null) =>
  api<{ run_id: number }>(`/api/sessions/${sessionId}/wiki-note/draft`, token, { method: 'POST', body: JSON.stringify({ profile_id: profileId ?? null }) })

export const commitWikiNote = (token: string, sessionId: number, path: string, content: string, mode: 'new' | 'append' | 'overwrite') =>
  api<{ ok: boolean; path: string }>(`/api/sessions/${sessionId}/wiki-note/commit`, token, { method: 'POST', body: JSON.stringify({ path, content, mode }) })

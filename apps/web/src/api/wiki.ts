import { api } from './client'
import type { FileEntry } from '../types'

const q = (s: string) => encodeURIComponent(s)

export type WikiNoteRaw = { path: string; content: string }

// Personal per-user wiki (server jails it to users/<username>/wiki).
export const listAll = (token: string) =>
  api<{ notes: WikiNoteRaw[] }>(`/api/wiki/all`, token)

export const listTree = (token: string, path = '') =>
  api<{ path: string; entries: FileEntry[] }>(`/api/wiki/tree?path=${q(path)}`, token)

export const readFile = (token: string, path: string) =>
  api<{ path: string; content: string }>(`/api/wiki/file?path=${q(path)}`, token)

export const writeFile = (token: string, path: string, content: string) =>
  api<{ ok: boolean }>(`/api/wiki/file?path=${q(path)}`, token, { method: 'PUT', body: JSON.stringify({ content }) })

export const mkdir = (token: string, path: string) =>
  api<{ ok: boolean }>(`/api/wiki/fs/mkdir`, token, { method: 'POST', body: JSON.stringify({ path }) })

export const renamePath = (token: string, from: string, to: string) =>
  api<{ ok: boolean }>(`/api/wiki/fs/rename`, token, { method: 'POST', body: JSON.stringify({ from, to }) })

export const deletePath = (token: string, path: string) =>
  api<{ ok: boolean }>(`/api/wiki/fs?path=${q(path)}`, token, { method: 'DELETE' })

// Save-to-wiki: kick off a wiki_draft run; the draft arrives via the session's
// event stream as a `wiki.draft` event, then commit writes the approved note.
export const draftWikiNote = (token: string, sessionId: number, profileId?: number | null) =>
  api<{ run_id: number }>(`/api/sessions/${sessionId}/wiki-note/draft`, token, { method: 'POST', body: JSON.stringify({ profile_id: profileId ?? null }) })

export const commitWikiNote = (token: string, sessionId: number, path: string, content: string, mode: 'new' | 'append' | 'overwrite') =>
  api<{ ok: boolean; path: string }>(`/api/sessions/${sessionId}/wiki-note/commit`, token, { method: 'POST', body: JSON.stringify({ path, content, mode }) })

import { api } from './client'

export type SearchResults = {
  projects: { slug: string; name: string }[]
  chats: { id: number; title: string }[]
  messages: { session_id: number; role: string; snippet: string; session_title: string }[]
}

export const search = (token: string, q: string) =>
  api<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`, token)

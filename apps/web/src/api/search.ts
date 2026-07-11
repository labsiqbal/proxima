import { api } from './client'

export type SearchResults = {
  projects: { slug: string; name: string }[]
  chats: { id: number; title: string; task_id: number | null }[]
  tasks: { id: number; title: string; status: string; project_slug: string }[]
  messages: { session_id: number; role: string; snippet: string; session_title: string; task_id: number | null }[]
}

export const search = (token: string, q: string) =>
  api<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`, token)

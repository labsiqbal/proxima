import { api } from './client'

export type SearchChatHit = {
  id: number
  title: string
  mode?: string | null
  project_slug?: string | null
  project_name?: string | null
}

export type SearchMessageHit = {
  session_id: number
  role: string
  snippet: string
  session_title: string
  mode?: string | null
  project_slug?: string | null
  project_name?: string | null
}

export type SearchResults = {
  projects: { slug: string; name: string }[]
  chats: SearchChatHit[]
  messages: SearchMessageHit[]
}

export const search = (token: string, q: string) =>
  api<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`, token)

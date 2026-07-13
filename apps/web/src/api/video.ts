import { api } from './client'

export type VideoProject = { id: string; title: string; path: string; width?: number; height?: number; updated_at?: number }

export const videoStudioProjectId = (slug: string, id: string) => `proxima-video__${slug}__${id}`

export const listVideos = (token: string, slug: string) =>
  api<{ videos: VideoProject[] }>(`/api/projects/${encodeURIComponent(slug)}/videos`, token)

export const createVideo = (token: string, slug: string, body: { title: string; brief?: string }) =>
  api<VideoProject>(`/api/projects/${encodeURIComponent(slug)}/videos`, token, { method: 'POST', body: JSON.stringify(body) })

export const deleteVideo = (token: string, slug: string, id: string) =>
  api<{ ok: boolean; id: string; path: string }>(`/api/projects/${encodeURIComponent(slug)}/videos/${encodeURIComponent(id)}`, token, { method: 'DELETE' })

export const lintVideo = (token: string, slug: string, id: string) =>
  api<{ ok: boolean; log?: string }>(`/api/projects/${encodeURIComponent(slug)}/videos/${encodeURIComponent(id)}/lint`, token, { method: 'POST' })

// Copy an existing project media file into the video project's assets/ folder
// (the studio only sees its own directory).
export const videoImportFile = (token: string, slug: string, id: string, path: string) =>
  api<{ ok: boolean; video_id: string; path: string }>(`/api/projects/${encodeURIComponent(slug)}/videos/${encodeURIComponent(id)}/import-file`, token, { method: 'POST', body: JSON.stringify({ path }) })

export const startVideoStudio = (token: string, slug: string, id: string) =>
  api<{ ok: boolean; port: number; path: string }>(`/api/projects/${encodeURIComponent(slug)}/videos/${encodeURIComponent(id)}/studio/start`, token, { method: 'POST' })

export const renderVideo = (token: string, slug: string, id: string, settings: { quality?: string; fps?: number; format?: 'mp4' | 'webm' } = {}) =>
  api<{ ok: boolean; path: string; log?: string }>(`/api/projects/${encodeURIComponent(slug)}/videos/${encodeURIComponent(id)}/render`, token, { method: 'POST', body: JSON.stringify(settings) })

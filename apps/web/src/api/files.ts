import { api } from './client'
import type { FileEntry } from '../types'

const q = (s: string) => encodeURIComponent(s)

async function responseError(res: Response, fallback: string): Promise<Error> {
  let detail = ''
  try {
    const body = await res.clone().json()
    detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
  } catch {
    try { detail = await res.text() } catch { detail = '' }
  }
  return new Error(`${fallback} (${res.status}${res.statusText ? ` ${res.statusText}` : ''})${detail ? `: ${detail}` : ''}`)
}

// Token-authenticated raw-file URL usable directly as <img>/<video> src.
export const previewUrl = (token: string, slug: string, path: string) =>
  `/api/preview/${q(token)}/${q(slug)}/${path.split('/').map(q).join('/')}`

// Seed a new Design Studio scene containing an existing project image (full-bleed).
export const designFromImage = (token: string, slug: string, path: string, title?: string) =>
  api<{ ok: boolean; id: string; title: string; path: string }>(`/api/projects/${q(slug)}/designs/from-image`, token, { method: 'POST', body: JSON.stringify({ path, title }) })

export const listTree = (token: string, slug: string, path = '') =>
  api<{ path: string; entries: FileEntry[] }>(`/api/projects/${slug}/tree?path=${q(path)}`, token)

export const projectWikiAll = (token: string, slug: string) =>
  api<{ notes: { path: string; content: string }[] }>(`/api/projects/${slug}/wiki/all`, token)

// Fetch raw file bytes (any type) as an object URL — for image preview / download.
export async function fetchRawBlob(token: string, slug: string, path: string): Promise<string> {
  const res = await fetch(`/api/projects/${slug}/raw?path=${q(path)}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) throw await responseError(res, `Could not download ${path}`)
  return URL.createObjectURL(await res.blob())
}

// Upload a user-attached file (image/doc) into the project's uploads/ folder.
export async function uploadFile(token: string, slug: string, file: File, dir?: string): Promise<{ path: string; name: string }> {
  const form = new FormData()
  form.append('file', file)
  const url = `/api/projects/${slug}/upload${dir ? `?dir=${q(dir)}` : ''}`
  const res = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form })
  if (!res.ok) throw await responseError(res, `Could not upload ${file.name}`)
  return res.json()
}

// Generate (text→image) or edit (image+prompt→image) a design asset via 9router.
export const genDesignImage = (token: string, slug: string, body: { prompt: string; size?: string; model?: string; image?: string }, signal?: AbortSignal) =>
  api<{ path: string; name: string }>(`/api/projects/${slug}/design/image`, token, { method: 'POST', body: JSON.stringify(body), signal })
export const designImageModels = (token: string, slug: string) =>
  api<{ models: string[]; configured: boolean }>(`/api/projects/${slug}/design/image-models`, token)

// URL for inline preview/download of a project file (token in path for <img>/<a>).
export const fileUrl = (token: string, slug: string, path: string) =>
  `/api/preview/${encodeURIComponent(token)}/${encodeURIComponent(slug)}/${path.split('/').map(encodeURIComponent).join('/')}`

// Run & preview a project app (managed dev server).
export type AppStatus = { running: boolean; ready?: boolean; port?: number; command?: string; log?: string[]; exited?: boolean }
export const appStart = (token: string, slug: string, command: string, port: number, dir = '') =>
  api<{ ok: boolean }>(`/api/projects/${slug}/app/start`, token, { method: 'POST', body: JSON.stringify({ command, port, dir }) })
export const appStop = (token: string, slug: string) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/app/stop`, token, { method: 'POST' })
export const appStatus = (token: string, slug: string) =>
  api<AppStatus>(`/api/projects/${slug}/app/status`, token)
export const appViewUrl = (token: string, slug: string) =>
  `/api/appview/${encodeURIComponent(token)}/${encodeURIComponent(slug)}/`
// Deployment config the SPA needs early — apps_domain enables per-app remote
// preview subdomains (<slug>.<apps_domain>); null ⇒ local-only preview.
export const getPublicConfig = (token: string) =>
  api<{ apps_domain: string | null }>(`/api/config`, token)
// Mint the domain-wide cookie that gates preview subdomains, so the preview iframe
// (same-site) carries it and loads without a Cloudflare Access login.
export const previewAuth = (token: string) =>
  api<{ ok: boolean }>(`/api/preview-auth`, token, { method: 'POST' })
export type DetectedApp = { dir: string; command: string; kind: string }
export const detectApps = (token: string, slug: string) =>
  api<{ apps: DetectedApp[] }>(`/api/projects/${slug}/apps`, token)

// Typed artifacts a project produced (design/app/page/doc/file) — powers the iterate Result.
export type Artifact = { type: 'design' | 'video' | 'video-file' | 'app' | 'page' | 'doc' | 'file' | 'image'; title: string; path: string; id?: string; dir?: string; command?: string; project_slug?: string | null }
export const listArtifacts = (token: string, slug: string, sinceMinutes = 1440) =>
  api<{ artifacts: Artifact[] }>(`/api/projects/${slug}/artifacts?since_minutes=${sinceMinutes}`, token)

// Artifacts produced BY a session's own runs (the iterate Result — scoped, relevant).
export const listSessionArtifacts = (token: string, sessionId: number) =>
  api<{ artifacts: Artifact[] }>(`/api/sessions/${sessionId}/artifacts`, token)

export const deleteSessionArtifact = (token: string, sessionId: number, path: string) =>
  api<{ ok: boolean; path: string }>(`/api/sessions/${sessionId}/artifacts?path=${q(path)}`, token, { method: 'DELETE' })

export const readFile = (token: string, slug: string, path: string) =>
  api<{ path: string; content: string }>(`/api/projects/${slug}/file?path=${q(path)}`, token)

export const writeFile = (token: string, slug: string, path: string, content: string) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/file?path=${q(path)}`, token, { method: 'PUT', body: JSON.stringify({ content }) })

export const mkdir = (token: string, slug: string, path: string) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/fs/mkdir`, token, { method: 'POST', body: JSON.stringify({ path }) })

export const renamePath = (token: string, slug: string, from: string, to: string) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/fs/rename`, token, { method: 'POST', body: JSON.stringify({ from, to }) })

export const deletePath = (token: string, slug: string, path: string) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/fs?path=${q(path)}`, token, { method: 'DELETE' })

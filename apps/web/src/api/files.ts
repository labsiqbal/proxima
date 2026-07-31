import { api } from './client'
import type { FileEntry, FileTarget } from '../types'

const q = (s: string) => encodeURIComponent(s)
export type FileRef = string | FileTarget
export const fileRefPath = (ref: FileRef) => typeof ref === 'string' ? ref : ref.path
const targetParam = (target: FileTarget) => q(JSON.stringify(target))
const refQuery = (ref: FileRef) => typeof ref === 'string'
  ? `path=${q(ref)}`
  : `target=${targetParam(ref)}`
export const retargetFile = (target: FileTarget, path: string): FileTarget => ({
  ...target,
  path,
})

const encodedPath = (path: string) => path.split('/').filter(Boolean).map(q).join('/')

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

// Raw-file URL usable directly as <img>/<video> src. Path-only previews use the
// owner session. Canonical previews enter through the API and redirect to an
// Area-bound capability origin.
export type ActivePreviewAuthority = {
  previewSession: string
  generation: string
}

export const previewUrl = (
  slug: string,
  path: string,
  target?: FileTarget,
  active?: ActivePreviewAuthority,
) => {
  const base = target
    ? `/api/target-preview/${q(slug)}/${q(target.area.kind)}/${target.area.id ?? 'root'}/${encodedPath(target.path)}`
    : `/api/preview/${q(slug)}/${encodedPath(path)}`
  if (!target || !active) return base
  const query = new URLSearchParams({
    __proxima_mode: 'active',
    __proxima_preview_session: active.previewSession,
    __proxima_preview_generation: active.generation,
  })
  return `${base}?${query.toString()}`
}

export type PreviewModeResponse = {
  active: boolean
  generation: string | null
}

export const setTargetPreviewMode = (
  token: string,
  slug: string,
  target: FileTarget,
  previewSession: string,
  active: boolean,
  generation?: string | null,
  keepalive = false,
) => api<PreviewModeResponse>(
  `/api/projects/${q(slug)}/preview-mode?target=${targetParam(target)}`,
  token,
  {
    method: 'POST',
    keepalive,
    body: JSON.stringify({
      active,
      preview_session: previewSession,
      ...(generation ? { generation } : {}),
    }),
  },
)

export function relativeFileUrl(
  slug: string,
  reference: string,
  sourcePath: string,
  target?: FileTarget,
): string {
  const suffixIndex = reference.search(/[?#]/)
  const relative = suffixIndex >= 0 ? reference.slice(0, suffixIndex) : reference
  const suffix = suffixIndex >= 0 ? reference.slice(suffixIndex) : ''
  const parts = sourcePath.split('/').filter(Boolean)
  if (relative) {
    parts.pop()
    for (const part of relative.replaceAll('\\', '/').split('/')) {
      if (!part || part === '.') continue
      if (part === '..') {
        if (!parts.length) return ''
        parts.pop()
      } else {
        parts.push(part)
      }
    }
  }
  const resolvedPath = parts.join('/')
  if (!resolvedPath) return ''
  return `${previewUrl(
    slug,
    resolvedPath,
    target ? retargetFile(target, resolvedPath) : undefined,
  )}${suffix}`
}

// Seed a new Design Studio scene containing an existing project image (full-bleed).
export const designFromImage = (token: string, slug: string, path: string, title?: string, target?: FileTarget) =>
  api<{ ok: boolean; id: string; title: string; path: string }>(`/api/projects/${q(slug)}/designs/from-image`, token, { method: 'POST', body: JSON.stringify({ path, title, ...(target ? { target } : {}) }) })

export const listTree = (token: string, slug: string, ref: FileRef = '') =>
  api<{ path: string; target?: FileTarget; entries: FileEntry[] }>(`/api/projects/${slug}/tree?${refQuery(ref)}`, token)

export type ReferenceFile = { path: string }
export type ReferenceFilesResponse = { files: ReferenceFile[]; truncated: boolean }
export const listReferenceFiles = (token: string, slug: string) =>
  api<ReferenceFilesResponse>(`/api/projects/${q(slug)}/reference-files`, token)

export const projectWikiAll = (token: string, slug: string) =>
  api<{ notes: { path: string; content: string }[] }>(`/api/projects/${slug}/wiki/all`, token)

export const rawUrl = (slug: string, path: string, target?: FileTarget) => {
  const query = target ? `target=${targetParam(target)}` : `path=${q(path)}`
  return `/api/projects/${q(slug)}/raw?${query}`
}

export async function fetchRawFile(token: string, slug: string, path: string, target?: FileTarget): Promise<Blob> {
  const res = await fetch(rawUrl(slug, path, target), { headers: { Authorization: `Bearer ${token}` } })
  if (!res.ok) throw await responseError(res, `Could not download ${path}`)
  return res.blob()
}

export async function fetchRawBlob(token: string, slug: string, path: string, target?: FileTarget): Promise<string> {
  return URL.createObjectURL(await fetchRawFile(token, slug, path, target))
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
export const genDesignImage = (token: string, slug: string, body: { prompt: string; size?: string; model?: string; image?: string; images?: string[] }, signal?: AbortSignal) =>
  api<{ path: string; name: string }>(`/api/projects/${slug}/design/image`, token, { method: 'POST', body: JSON.stringify(body), signal })

// Kick off an agent run that writes <project>/design.md brand guidelines from reference
// URLs (crawled server-side), uploaded reference images (vision), and free-text notes.
export const generateBrandGuide = (token: string, slug: string, body: { urls?: string[]; imagePaths?: string[]; notes?: string }) =>
  api<{ run_id: number; session_id: number; urls: { url: string; ok: boolean }[] }>(`/api/projects/${slug}/design/brand-guide`, token, { method: 'POST', body: JSON.stringify(body) })

export type MoodboardItem = {
  id: string
  kind: 'link' | 'upload'
  url: string | null
  imagePath: string | null
  title: string
  siteName: string
  faviconUrl: string | null
  note: string
  tags: string[]
  useAsReference: boolean
  createdAt: string
  updatedAt: string
}

export const listMoodboard = (token: string, slug: string) =>
  api<{ items: MoodboardItem[] }>(`/api/projects/${q(slug)}/design/moodboard`, token)

export const addMoodboardItem = (token: string, slug: string, body: { url?: string; imagePath?: string; title?: string; siteName?: string; note?: string; tags?: string[]; useAsReference?: boolean }) =>
  api<{ item: MoodboardItem; warning: string | null }>(`/api/projects/${q(slug)}/design/moodboard`, token, { method: 'POST', body: JSON.stringify(body) })

export const updateMoodboardItem = (token: string, slug: string, itemId: string, body: { note?: string; tags?: string[]; useAsReference?: boolean }) =>
  api<{ item: MoodboardItem }>(`/api/projects/${q(slug)}/design/moodboard/${q(itemId)}`, token, { method: 'PATCH', body: JSON.stringify(body) })

export const deleteMoodboardItem = (token: string, slug: string, itemId: string) =>
  api<{ ok: boolean; id: string }>(`/api/projects/${q(slug)}/design/moodboard/${q(itemId)}`, token, { method: 'DELETE' })

// URL for inline preview/download of a project file (<img>/<a>); cookie-authed.
export const fileUrl = (slug: string, path: string, target?: FileTarget) =>
  previewUrl(slug, path, target)

// Run & preview a project app (managed dev server). preview_port is the app's
// credential-stripping relay listener - the isolated preview origin for local
// and remote clients when no apps-domain subdomain applies.
export type AppLifecycleState =
  | 'stopped'
  | 'starting'
  | 'ready'
  | 'port_conflict'
  | 'ownership_unknown'
  | 'exited'

type AppStatusBase = {
  state: AppLifecycleState
  running: boolean
  ready: boolean
  requested_port?: number
  port?: number
  preview_port?: number
  command?: string
  log?: string[]
  message?: string
  reason?: 'output_sink_unavailable'
  /** Present when the managed process self-exited (sticky until next start). */
  exited?: boolean
  exit_code?: number
  broad_bind?: boolean
}

export type AppStatus = AppStatusBase & (
  | { state: 'stopped'; running: false; ready: false }
  | { state: 'starting'; running: true; ready: false; prolonged_start: boolean }
  | { state: 'ready'; running: true; ready: true; port: number }
  | { state: 'port_conflict'; running: false; ready: false; requested_port: number }
  | { state: 'ownership_unknown'; running: true; ready: false }
  | { state: 'exited'; running: false; ready: false; exited: true; exit_code: number }
)

export type AppPortConflictDetail = {
  state: 'port_conflict'
  port: number
  message: string
}

/** Plain-language summary after a managed app process self-exits without staying up. */
export function appExitSummary(status: Pick<AppStatus, 'exit_code' | 'command'>): {
  tone: 'ok' | 'fail'
  title: string
  hint: string
} {
  const code = typeof status.exit_code === 'number' ? status.exit_code : 0
  if (code !== 0) {
    return {
      tone: 'fail',
      title: `Command failed (exit ${code})`,
      hint: 'Check the log below, fix the command or folder, and Run again.',
    }
  }
  return {
    tone: 'ok',
    title: 'Command finished (exit 0)',
    hint: 'No preview server came up. For live preview, run a long-lived server such as npm run dev or python3 -m http.server $PORT.',
  }
}
export const appStart = (token: string, slug: string, command: string, port: number, dir = '') =>
  api<{ ok: boolean }>(`/api/projects/${slug}/app/start`, token, { method: 'POST', body: JSON.stringify({ command, port, dir }) })
export const appStop = (token: string, slug: string) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/app/stop`, token, { method: 'POST' })
export const appStatus = (token: string, slug: string) =>
  api<AppStatus>(`/api/projects/${slug}/app/status`, token)
export const appViewUrl = (slug: string) =>
  `/api/appview/${encodeURIComponent(slug)}/`
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
export type Artifact = { type: 'design' | 'video' | 'video-file' | 'app' | 'page' | 'doc' | 'file' | 'image'; title: string; path: string; id?: string; dir?: string; command?: string; project_slug?: string | null; target?: FileTarget }
export const listArtifacts = (token: string, slug: string, sinceMinutes = 1440) =>
  api<{ artifacts: Artifact[] }>(`/api/projects/${slug}/artifacts?since_minutes=${sinceMinutes}`, token)

// Artifacts produced BY a session's own runs (the iterate Result — scoped, relevant).
export const listSessionArtifacts = (token: string, sessionId: number) =>
  api<{ artifacts: Artifact[] }>(`/api/sessions/${sessionId}/artifacts`, token)

export const deleteSessionArtifact = (token: string, sessionId: number, ref: FileRef) =>
  api<{ ok: boolean; path: string; target?: FileTarget | null }>(`/api/sessions/${sessionId}/artifacts?${refQuery(ref)}`, token, { method: 'DELETE' })

export const readFile = (token: string, slug: string, ref: FileRef) =>
  api<{ path: string; target?: FileTarget; content: string }>(`/api/projects/${slug}/file?${refQuery(ref)}`, token)

export const writeFile = (token: string, slug: string, ref: FileRef, content: string) =>
  api<{ ok: boolean; target?: FileTarget }>(`/api/projects/${slug}/file?${refQuery(ref)}`, token, { method: 'PUT', body: JSON.stringify({ content }) })

export const mkdir = (token: string, slug: string, ref: FileRef) =>
  api<{ ok: boolean; target?: FileTarget }>(`/api/projects/${slug}/fs/mkdir`, token, {
    method: 'POST',
    body: JSON.stringify({
      path: fileRefPath(ref),
      ...(typeof ref === 'string' ? {} : { target: ref }),
    }),
  })

export const renamePath = (token: string, slug: string, from: FileRef, to: FileRef) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/fs/rename`, token, {
    method: 'POST',
    body: JSON.stringify({
      from: fileRefPath(from),
      to: fileRefPath(to),
      ...(typeof from === 'string' ? {} : { from_target: from }),
      ...(typeof to === 'string' ? {} : { to_target: to }),
    }),
  })

export const deletePath = (token: string, slug: string, ref: FileRef) =>
  api<{ ok: boolean }>(`/api/projects/${slug}/fs?${refQuery(ref)}`, token, { method: 'DELETE' })

import { api, ApiError } from './client'
import type {
  AreaRemote,
  OpsMigrationDetail,
  Project,
  ProjectAreas,
  ProjectIdentityComparison,
  ProjectLayout,
  ProjectLocation,
  ProjectRebindResult,
} from '../types'

export const listProjects = (token: string) => api<{ projects: Project[] }>('/api/projects', token)
export const listProjectAreas = (token: string, slug: string) => api<ProjectAreas>(`/api/projects/${slug}/areas`, token)
// Manually register a code area (folder need not be a git repo yet).
export const addProjectArea = (token: string, slug: string, body: { rel_path: string }) =>
  api<{ id: number; rel_path: string; source: string }>(`/api/projects/${slug}/areas`, token, { method: 'POST', body: JSON.stringify(body) })
// Re-scan the project folder for git repos and refresh auto-detected areas.
export const detectProjectAreas = (token: string, slug: string) =>
  api<ProjectAreas & { detect: { detected: string[]; added: string[]; removed: string[] } }>(`/api/projects/${slug}/areas/detect`, token, { method: 'POST' })
// The T9 per-code-area push-after-merge toggle. Enabling needs a detected git
// remote - the server refuses otherwise (the UI never offers it then).
export const updateProjectArea = (token: string, slug: string, areaId: number, body: { push_on_merge: boolean }) =>
  api<{ id: number; rel_path: string; push_on_merge: boolean; remote: AreaRemote | null }>(`/api/projects/${slug}/areas/${areaId}`, token, { method: 'PATCH', body: JSON.stringify(body) })
export const createProject = (token: string, body: { slug: string; name: string }) => api<Project>('/api/projects', token, { method: 'POST', body: JSON.stringify(body) })
export type DirectoryBrowse = {
  path: string
  parent: string | null
  dirs: { name: string; path: string }[]
  roots: string[]
  root_id: string
}
export const browseDirs = (token: string, path = '', rootId = '') => {
  const query = new URLSearchParams({ path })
  if (rootId) query.set('root_id', rootId)
  return api<DirectoryBrowse>(`/api/fs/dirs?${query}`, token)
}
// ops_path is the per-project Ops folder chosen at link time (prune C3);
// omit it to keep the server-detected default (existing ops/ -> "ops", else ".").
export const linkProject = (token: string, body: { path: string; root_id: string; name?: string; slug?: string; mkdir?: boolean; ops_path?: string }) => api<Project>('/api/projects/link', token, { method: 'POST', body: JSON.stringify(body) })
export const apiErrorDetail = (error: unknown): string => {
  if (error instanceof ApiError && error.detail) return error.detail
  if (error instanceof Error) return error.message
  return String(error)
}
export const linkProjectErrorField = (error: unknown): 'path' | 'folder' | 'name' | 'ops' | null => {
  if (!(error instanceof ApiError)) return null
  if (error.field === 'name' || error.field === 'slug') return 'name'
  if (error.field === 'folder') return 'folder'
  if (error.field === 'ops_path') return 'ops'
  if (error.field === 'path' || error.field === 'parent' || error.field === 'mkdir') return 'path'
  return null
}
// Relocate/rebind (prune C6): where the project's folder is now, and re-pinning
// the record to it. The picker supplies path + root_id, so the target is jailed
// to the configured link roots exactly like a link; `confirm` is the owner's
// override for a location whose identity does not match the stored projection.
export const getProjectLocation = (token: string, slug: string) =>
  api<ProjectLocation & { identity: ProjectIdentityComparison['stored']; actions: string[] }>(
    `/api/projects/${encodeURIComponent(slug)}/location`, token)
export const rebindProject = (
  token: string,
  slug: string,
  body: { path: string; root_id: string; confirm: boolean },
) => api<ProjectRebindResult>(`/api/projects/${encodeURIComponent(slug)}/rebind`, token, {
  method: 'POST',
  body: JSON.stringify(body),
})
// A rebind refusal the owner may override says so in its body; everything else
// (an already-linked folder, an unusable layout) is a plain refusal.
export const rebindIsConfirmable = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 409 && error.body?.confirmable === true
export const renameProject = (token: string, slug: string, name: string) => api<Project>(`/api/projects/${slug}`, token, { method: 'PATCH', body: JSON.stringify({ name }) })
export const deleteProject = (token: string, slug: string) => api<{ ok: boolean }>(`/api/projects/${slug}`, token, { method: 'DELETE' })
// The per-project layout map + memory-writes toggle (prune C4/C5).
export const getProjectLayout = (token: string, slug: string) =>
  api<ProjectLayout>(`/api/projects/${encodeURIComponent(slug)}/layout`, token)
// Adaptive memory writes (prune C5): default ON; OFF fully disables the
// automatic log.md append + wiki index regeneration for this project.
export const setMemoryWrites = (token: string, slug: string, enabled: boolean) =>
  api<{ enabled: boolean }>(`/api/projects/${encodeURIComponent(slug)}/memory-writes`, token, { method: 'PUT', body: JSON.stringify({ enabled }) })
export const getOpsMigration = (token: string, slug: string) =>
  api<OpsMigrationDetail>(`/api/projects/${encodeURIComponent(slug)}/ops-migration`, token)
export const validateOpsMigration = (token: string, slug: string) =>
  api<OpsMigrationDetail>(`/api/projects/${encodeURIComponent(slug)}/ops-migration/validate`, token, { method: 'POST' })
export const retryOpsMigration = (token: string, slug: string) =>
  api<OpsMigrationDetail>(`/api/projects/${encodeURIComponent(slug)}/ops-migration/retry`, token, { method: 'POST' })

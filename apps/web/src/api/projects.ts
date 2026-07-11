import { api } from './client'
import type { Project } from '../types'

export const listProjects = (token: string) => api<{ projects: Project[] }>('/api/projects', token)
export const createProject = (token: string, body: { slug: string; name: string }) => api<Project>('/api/projects', token, { method: 'POST', body: JSON.stringify(body) })
export const browseDirs = (token: string, path = '') => api<{ path: string; parent: string | null; dirs: { name: string; path: string }[]; roots: string[] }>(`/api/fs/dirs?path=${encodeURIComponent(path)}`, token)
export const linkProject = (token: string, body: { path: string; name?: string; slug?: string }) => api<Project>('/api/projects/link', token, { method: 'POST', body: JSON.stringify(body) })
export const renameProject = (token: string, slug: string, name: string) => api<Project>(`/api/projects/${slug}`, token, { method: 'PATCH', body: JSON.stringify({ name }) })
export const deleteProject = (token: string, slug: string) => api<{ ok: boolean }>(`/api/projects/${slug}`, token, { method: 'DELETE' })

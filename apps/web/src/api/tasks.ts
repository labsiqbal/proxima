import { api } from './client'
import type { Task, TaskStatus } from '../types'

export const listTasks = (token: string, slug: string) => api<{ tasks: Task[] }>(`/api/projects/${slug}/tasks`, token)

export const createTask = (token: string, slug: string, body: { title: string; description?: string; assignee?: string | null }) =>
  api<Task>(`/api/projects/${slug}/tasks`, token, { method: 'POST', body: JSON.stringify(body) })

export const getTask = (token: string, id: number) => api<Task>(`/api/tasks/${id}`, token)

export const updateTask = (token: string, id: number, body: { title?: string; description?: string; status?: TaskStatus; assignee?: string | null }) =>
  api<Task>(`/api/tasks/${id}`, token, { method: 'PATCH', body: JSON.stringify(body) })

export const deleteTask = (token: string, id: number) => api<{ ok: boolean }>(`/api/tasks/${id}`, token, { method: 'DELETE' })

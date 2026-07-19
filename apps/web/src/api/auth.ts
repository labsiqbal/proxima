import { api } from './client'
import type { User } from '../types'

type Session = { token: string; user: User }

// Boot resume via the HttpOnly cookie; echoes the session token for in-memory use.
export const resume = () => api<Session>('/auth/resume', undefined, { method: 'POST' })
export const setupStatus = () => api<{ password_set: boolean; single_user: boolean }>('/api/setup/status')
export const setPassword = (password: string) => api<Session>('/auth/set-password', undefined, { method: 'POST', body: JSON.stringify({ password }) })
export const login = (password: string) => api<Session>('/auth/login', undefined, { method: 'POST', body: JSON.stringify({ password }) })
export const changePassword = (token: string, currentPassword: string, newPassword: string) =>
  api<Session>('/auth/change-password', token, { method: 'POST', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) })
export const logout = (token: string) => api<{ ok: boolean }>('/auth/logout', token, { method: 'POST' })

import { api } from './client'
import type { User } from '../types'

type Session = { token: string; user: User }

// Single-user cockpit. Passwordless auto-login is only accepted before a password
// is set (network-only mode); once set, use login().
export const autoLogin = () => api<Session>('/auth/auto', undefined, { method: 'POST' })
export const me = (token: string) => api<User>('/api/me', token)

export const setupStatus = () => api<{ password_set: boolean; single_user: boolean }>('/api/setup/status')
export const setPassword = (password: string) => api<Session>('/auth/set-password', undefined, { method: 'POST', body: JSON.stringify({ password }) })
export const login = (password: string) => api<Session>('/auth/login', undefined, { method: 'POST', body: JSON.stringify({ password }) })
export const logout = (token: string) => api<{ ok: boolean }>('/auth/logout', token, { method: 'POST' })

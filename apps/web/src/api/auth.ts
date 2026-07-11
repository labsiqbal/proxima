import { api } from './client'
import type { User } from '../types'

// Single-user cockpit: no credentials, returns the owner's token + user.
export const autoLogin = () => api<{ token: string; user: User }>('/auth/auto', undefined, { method: 'POST' })
export const me = (token: string) => api<User>('/api/me', token)

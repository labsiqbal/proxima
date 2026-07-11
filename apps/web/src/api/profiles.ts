import { api } from './client'
import type { Profile, RunnerCapabilities, CapabilitySelection } from '../types'

export const listProfiles = (token: string) => api<{ profiles: Profile[] }>('/api/profiles', token)
export const createProfile = (token: string, body: { name: string; runner_id?: string; instructions?: string }) => api<Profile>('/api/profiles', token, { method: 'POST', body: JSON.stringify(body) })
export const updateProfile = (token: string, id: number, body: Partial<{ name: string; default_model: string; is_default: boolean; runner_id: string; instructions: string; capabilities: CapabilitySelection }>) => api<Profile>(`/api/profiles/${id}`, token, { method: 'PATCH', body: JSON.stringify(body) })
export const runnerCapabilities = (token: string, runnerId: string) => api<RunnerCapabilities>(`/api/runners/${runnerId}/capabilities`, token)
export const deleteProfile = (token: string, id: number) => api<{ ok: boolean }>(`/api/profiles/${id}`, token, { method: 'DELETE' })

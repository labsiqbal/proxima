import { api } from './client'
import type { AppFeatures } from '../types'
import { resolveAppFeatures } from '../features'

export async function getAppFeatures(): Promise<AppFeatures> {
  return resolveAppFeatures(() => api<unknown>('/api/config'))
}

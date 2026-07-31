import { api } from './client'

export type PlatformTier = 'supported' | 'experimental' | 'unsupported'

export type PlatformSupportEntry = {
  key: string
  label: string
  tier: PlatformTier
  summary: string
}

export type PlatformSupport = {
  claim: 'linux-first-daily-driver'
  server: PlatformSupportEntry
  platforms: PlatformSupportEntry[]
  reference: string
}

export async function getPlatformSupport(): Promise<PlatformSupport> {
  const config = await api<{ platform_support: PlatformSupport }>('/api/config')
  return config.platform_support
}

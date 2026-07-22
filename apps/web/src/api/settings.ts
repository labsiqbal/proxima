import { api } from './client'

export type MediaCapabilities = Record<string, boolean>

export type ImageProviderMeta = {
  id: string
  displayName: string
  requiresKey: boolean
  kind: 'auto' | 'codex' | 'oauth' | 'higgsfield' | 'http'
  note?: string
  defaultBaseUrl?: string
  capabilities?: MediaCapabilities
}

export type CodexReady = { ready: boolean; detail: string; binary?: string }
export type HiggsfieldStatus = {
  installed: boolean
  authenticated: boolean
  workspaceSelected: boolean
  ready: boolean
  detail: string
  binary?: string
  account?: unknown
  workspace?: unknown
}

export type ImageGenSettings = {
  provider: string
  model?: string | null
  baseUrl?: string | null
  hasApiKey: boolean
  providers: ImageProviderMeta[]
  defaultProvider: string
  codexReady?: CodexReady | null
  higgsfieldReady?: HiggsfieldStatus | null
  xaiOauthReady?: { ready: boolean; detail?: string } | null
}

export type ImageGenSettingsUpdate = {
  provider: string
  model?: string | null
  baseUrl?: string | null
  apiKey?: string | null
}

export const getPermissionSettings = (token: string) =>
  api<{ auto_approve: boolean }>(`/api/settings/permissions`, token)
export const savePermissionSettings = (token: string, auto_approve: boolean) =>
  api<{ auto_approve: boolean }>(`/api/settings/permissions`, token, { method: 'PUT', body: JSON.stringify({ auto_approve }) })

export type RunSettings = {
  run_timeout_seconds: number
  default_run_timeout_seconds: number
  min_seconds: number
  max_seconds: number
  continuation_limit: number
}

export const getRunSettings = (token: string) =>
  api<RunSettings>('/api/settings/runs', token)
export const saveRunSettings = (token: string, run_timeout_seconds: number) =>
  api<{ run_timeout_seconds: number; continuation_limit: number }>('/api/settings/runs', token, { method: 'PUT', body: JSON.stringify({ run_timeout_seconds }) })

export type CollaborationSettings = {
  brainstorm_agents: 2 | 3
  debate_rounds: 2 | 3 | 4
}

export const getCollaborationSettings = (token: string) =>
  api<CollaborationSettings>('/api/settings/collaboration', token)
export const saveCollaborationSettings = (token: string, body: CollaborationSettings) =>
  api<CollaborationSettings>('/api/settings/collaboration', token, { method: 'PUT', body: JSON.stringify(body) })

export const getImageGenSettings = (token: string) =>
  api<ImageGenSettings>('/api/settings/image-gen', token)

export const saveImageGenSettings = (token: string, body: ImageGenSettingsUpdate) =>
  api<{ ok: boolean; provider: string; model?: string | null; hasApiKey: boolean }>('/api/settings/image-gen', token, { method: 'PUT', body: JSON.stringify(body) })

export const testImageGenSettings = (token: string, body: ImageGenSettingsUpdate) =>
  api<{ ok?: boolean; ready?: boolean; detail: string; higgsfield?: HiggsfieldStatus; codex?: CodexReady }>('/api/settings/image-gen/test', token, { method: 'POST', body: JSON.stringify(body) })

// Capability bundle (T8): recommended host tools, PATH-probed server-side.
// Advisory only - Proxima never installs binaries.
export type RecommendedTool = { bin: string; use: string; install: string; present: boolean }

export const getRecommendedTools = (token: string) =>
  api<{ tools: RecommendedTool[] }>('/api/tools/recommended', token)

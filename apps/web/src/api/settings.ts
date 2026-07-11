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

export type HiggsfieldSettings = {
  imagePolicy: 'zero-credit-only' | 'ask-before-credits'
  imageModel: string
  videoPolicy: 'confirm-credits' | 'allow-with-limit' | 'disabled'
  videoModel: string
  maxVideoCredits: number
}

export type HiggsfieldConfig = {
  settings: HiggsfieldSettings
  status: HiggsfieldStatus
}

export type VideoProviderMeta = {
  id: string
  displayName: string
  requiresKey: boolean
  kind: 'oauth' | 'higgsfield'
  note?: string
  capabilities?: MediaCapabilities
}

export type VideoGenSettings = {
  provider: string
  model: string
  videoPolicy: HiggsfieldSettings['videoPolicy']
  maxVideoCredits: number
  providers: VideoProviderMeta[]
  defaultProvider: string
  status?: { ok?: boolean; ready?: boolean; detail: string; higgsfield?: HiggsfieldStatus }
}

export type VideoGenSettingsUpdate = {
  provider: string
  model?: string | null
  videoPolicy?: HiggsfieldSettings['videoPolicy']
  maxVideoCredits?: number
}

export const getVideoGenSettings = (token: string) =>
  api<VideoGenSettings>('/api/settings/video-gen', token)

export const saveVideoGenSettings = (token: string, body: VideoGenSettingsUpdate) =>
  api<{ ok: boolean } & Pick<VideoGenSettings, 'provider' | 'model' | 'videoPolicy' | 'maxVideoCredits' | 'status'>>('/api/settings/video-gen', token, { method: 'PUT', body: JSON.stringify(body) })

export const testVideoGenSettings = (token: string, body: Pick<VideoGenSettingsUpdate, 'provider'>) =>
  api<{ ok?: boolean; ready?: boolean; detail: string; higgsfield?: HiggsfieldStatus }>('/api/settings/video-gen/test', token, { method: 'POST', body: JSON.stringify(body) })

export const getHiggsfieldSettings = (token: string) =>
  api<HiggsfieldConfig>('/api/settings/higgsfield', token)

export const saveHiggsfieldSettings = (token: string, body: HiggsfieldSettings) =>
  api<{ ok: boolean; settings: HiggsfieldSettings; status: HiggsfieldStatus }>('/api/settings/higgsfield', token, { method: 'PUT', body: JSON.stringify(body) })

export const testHiggsfieldSettings = (token: string) =>
  api<HiggsfieldStatus>('/api/settings/higgsfield/test', token, { method: 'POST' })

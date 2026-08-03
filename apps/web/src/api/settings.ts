import { api } from './client'

export type MediaCapabilities = Record<string, boolean>

/** Image and video generation are one provider family - same metadata shape. */
export type MediaProviderMeta = {
  id: string
  displayName: string
  requiresKey: boolean
  kind: 'auto' | 'codex' | 'oauth' | 'higgsfield' | 'http'
  note?: string
  defaultBaseUrl?: string
  capabilities?: MediaCapabilities
}

export type ImageProviderMeta = MediaProviderMeta

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

/** The settings row every media provider family shares (never carries the key). */
export type MediaGenSettings = {
  provider: string
  model?: string | null
  baseUrl?: string | null
  hasApiKey: boolean
  providers: MediaProviderMeta[]
  defaultProvider: string
}

export type ImageGenSettings = MediaGenSettings & {
  codexReady?: CodexReady | null
  higgsfieldReady?: HiggsfieldStatus | null
  xaiOauthReady?: { ready: boolean; detail?: string } | null
}

export type VideoGenSettings = MediaGenSettings

export type MediaGenSettingsUpdate = {
  provider: string
  model?: string | null
  baseUrl?: string | null
  apiKey?: string | null
}

export type ImageGenSettingsUpdate = MediaGenSettingsUpdate

export type MediaGenTestResult = {
  ok?: boolean
  ready?: boolean
  detail: string
  higgsfield?: HiggsfieldStatus
  codex?: CodexReady
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

// Satpam supervision thresholds (slice 12, T10): N consecutive no-progress
// continuation turns before the watchman acts, and its sweep cadence.
export type SatpamSettings = {
  stall_turns: number
  check_seconds: number
  default_stall_turns: number
  min_stall_turns: number
  max_stall_turns: number
  default_check_seconds: number
  min_check_seconds: number
  max_check_seconds: number
}

export const getSatpamSettings = (token: string) =>
  api<SatpamSettings>('/api/settings/satpam', token)
export const saveSatpamSettings = (token: string, body: { stall_turns: number; check_seconds: number }) =>
  api<{ stall_turns: number; check_seconds: number }>('/api/settings/satpam', token, { method: 'PUT', body: JSON.stringify(body) })

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
  api<MediaGenTestResult>('/api/settings/image-gen/test', token, { method: 'POST', body: JSON.stringify(body) })

// Video generation: the sibling row of image-gen. Same base-URL semantics -
// the API root, no endpoint path; the server appends /videos/generations.
export const getVideoGenSettings = (token: string) =>
  api<VideoGenSettings>('/api/settings/video-gen', token)

export const saveVideoGenSettings = (token: string, body: MediaGenSettingsUpdate) =>
  api<{ ok: boolean; provider: string; model?: string | null; hasApiKey: boolean }>('/api/settings/video-gen', token, { method: 'PUT', body: JSON.stringify(body) })

export const testVideoGenSettings = (token: string, body: MediaGenSettingsUpdate) =>
  api<MediaGenTestResult>('/api/settings/video-gen/test', token, { method: 'POST', body: JSON.stringify(body) })

// Capability bundle (T8): recommended host tools, PATH-probed server-side.
// Advisory only - Proxima never installs binaries.
export type RecommendedTool = {
  bin: string
  use: string
  install: string
  present: boolean
  alts?: string[]
  detectedBin?: string
}

export const getRecommendedTools = (token: string) =>
  api<{ tools: RecommendedTool[] }>('/api/tools/recommended', token)

/** Global custom skill directories included in multi-root skill scan (not per-profile). */
export const getSkillRoots = (token: string) =>
  api<{ roots: string[] }>('/api/settings/skill-roots', token)

export const saveSkillRoots = (token: string, roots: string[]) =>
  api<{ roots: string[] }>('/api/settings/skill-roots', token, {
    method: 'PUT',
    body: JSON.stringify({ roots }),
  })

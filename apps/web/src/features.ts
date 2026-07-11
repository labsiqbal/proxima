import type { AppFeatures, View } from './types'

export const DEFAULT_FEATURES: AppFeatures = { video: false, designStudio: false }

export function parseAppFeatures(value: unknown): AppFeatures {
  const features = value && typeof value === 'object' && 'features' in value
    ? (value as { features?: unknown }).features
    : null
  const source = features && typeof features === 'object'
    ? features as { video?: unknown; design_studio?: unknown }
    : {}
  return {
    video: source.video === true,
    designStudio: source.design_studio === true,
  }
}

export async function resolveAppFeatures(load: () => Promise<unknown>): Promise<AppFeatures> {
  try {
    return parseAppFeatures(await load())
  } catch {
    return DEFAULT_FEATURES
  }
}

export function isFeatureViewEnabled(view: View, features: AppFeatures): boolean {
  return (view !== 'video' || features.video) && (view !== 'design' || features.designStudio)
}

export function isFeatureSessionEnabled(session: { title?: string | null; mode?: string | null }, features: AppFeatures): boolean {
  const title = session.title || ''
  if (!features.designStudio && (session.mode === 'design' || /^(Design:|Design System:)/i.test(title))) return false
  if (!features.video && (session.mode === 'video' || /^Video:/i.test(title))) return false
  return true
}

export function isDisabledFeatureHash(hash: string, features: AppFeatures): boolean {
  return !features.video && hash.startsWith('#project/proxima-video__')
}

export function studioBridgeAvailability(type: string, features: AppFeatures) {
  return {
    design: features.designStudio && type === 'image',
    video: features.video && (type === 'image' || type === 'video-file'),
  }
}

export function isFeatureCommandEnabled(command: { name: string; surface: string }, features: AppFeatures): boolean {
  const name = command.name.toLowerCase()
  const surface = command.surface.toLowerCase()
  if (!features.video && (name.includes('video') || surface.includes('video'))) return false
  if (!features.designStudio && (/\/(design-studio|image-studio)/.test(name) || surface.includes('design studio'))) return false
  return true
}

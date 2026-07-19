import type { AppFeatures, View } from './types'

export const DEFAULT_FEATURES: AppFeatures = { designStudio: false, workflowGraph: false }

export function parseAppFeatures(value: unknown): AppFeatures {
  const features = value && typeof value === 'object' && 'features' in value
    ? (value as { features?: unknown }).features
    : null
  const source = features && typeof features === 'object'
    ? features as { design_studio?: unknown; workflow_graph?: unknown }
    : {}
  return {
    designStudio: source.design_studio === true,
    workflowGraph: source.workflow_graph === true,
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
  return (view !== 'design' || features.designStudio)
    && (view !== 'graph' || features.workflowGraph)
}

export function isFeatureSessionEnabled(session: { title?: string | null; mode?: string | null }, features: AppFeatures): boolean {
  const title = session.title || ''
  if (!features.designStudio && (session.mode === 'design' || /^(Design:|Design System:)/i.test(title))) return false
  return true
}

export function studioBridgeAvailability(type: string, features: AppFeatures) {
  return {
    design: features.designStudio && type === 'image',
  }
}

export function isFeatureCommandEnabled(command: { name: string; surface: string }, features: AppFeatures): boolean {
  const name = command.name.toLowerCase()
  const surface = command.surface.toLowerCase()
  if (!features.designStudio && (/\/(design-studio|image-studio)/.test(name) || surface.includes('design studio'))) return false
  return true
}

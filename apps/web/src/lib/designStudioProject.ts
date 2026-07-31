import type { Project } from '../types'

/**
 * Resolve which Project Design Studio should bind to.
 * A Task-linked ownership slug wins so cross-Project Task designs open without
 * adopting that Project as the user's Work selection.
 */
export function resolveDesignStudioProject(
  projects: Project[],
  designProjectSlug: string | null | undefined,
  workProject: Project | null,
): Project | null {
  if (designProjectSlug) {
    const owned = projects.find(project => project.slug === designProjectSlug)
    if (owned) return owned
  }
  return workProject
}

/** Prefer an explicit Task owning slug, then fall back to Task context. */
export function taskLinkedDesignProjectSlug(
  explicitSlug: string | null | undefined,
  taskContextSlug: string | null | undefined,
): string | null {
  return explicitSlug || taskContextSlug || null
}

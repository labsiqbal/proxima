import type { Project } from '../types'

export type WorkProjectPreference = {
  slug: string
  name: string
}

export type WorkProjectResolution = {
  project: Project | null
  missingPreference: WorkProjectPreference | null
}

export const workProjectPreferenceKey = (ownerId: number) =>
  `proxima.workProject.owner:${ownerId}`

export function readWorkProjectPreference(
  ownerId: number,
  storage: Pick<Storage, 'getItem'> = localStorage,
): WorkProjectPreference | null {
  try {
    const value = JSON.parse(storage.getItem(workProjectPreferenceKey(ownerId)) || 'null')
    return value
      && typeof value.slug === 'string'
      && typeof value.name === 'string'
      ? value
      : null
  } catch {
    return null
  }
}

export function persistWorkProjectPreference(
  ownerId: number,
  project: Pick<Project, 'slug' | 'name'>,
  storage: Pick<Storage, 'setItem'> = localStorage,
): void {
  try {
    storage.setItem(
      workProjectPreferenceKey(ownerId),
      JSON.stringify({ slug: project.slug, name: project.name }),
    )
  } catch {
    // A blocked browser store must not make the current Work Project unusable.
  }
}

export function resolveWorkProject(
  projects: Project[],
  preference: WorkProjectPreference | null,
  current: Project | null,
): WorkProjectResolution {
  const preferred = preference
    ? projects.find(project => project.slug === preference.slug)
    : null
  const stillCurrent = current
    ? projects.find(project => project.slug === current.slug)
    : null
  const fallback = projects.find(project => project.visibility === 'private')
    || projects[0]
    || null
  return {
    project: preferred || stillCurrent || fallback,
    missingPreference: preference && !preferred ? preference : null,
  }
}

import type { Container, ContainerAreas } from '../../types'

type NamedProject = Pick<Container, 'name' | 'identity_label'>

export function projectOptionLabel(project: NamedProject): string {
  return project.identity_label && project.identity_label !== project.name
    ? `${project.name} (${project.identity_label})`
    : project.name
}

export function projectIdentityLabel(project: NamedProject | null | undefined): string | null {
  if (!project?.identity_label || project.identity_label === project.name) return null
  return `Identity: ${project.identity_label}`
}

export function areaDisplayLabel(
  areas: ContainerAreas | null | undefined,
  areaId: number | null | undefined,
): string {
  if (areaId == null) return 'Master chooses'
  const area = areas
    ? [areas.ops_area, ...areas.code_areas].find(candidate => candidate.id === areaId)
    : null
  if (!area) return `#${areaId}`
  if (area.kind === 'ops') return 'Operations'
  return area.rel_path === '.' ? 'Code: repository root' : `Code: ${area.rel_path}`
}

export function projectSecondaryContext(
  project: NamedProject | null | undefined,
  areaLabel: string,
): string {
  return [projectIdentityLabel(project), `Area: ${areaLabel}`].filter(Boolean).join(' · ')
}

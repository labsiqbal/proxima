import type { FileEntry } from '../../types'

/** Shared project component library filename under artifacts/design/. */
export const DESIGN_COMPONENTS_FILE = '_components.json'

/** True when the design root listing already has a components library file. */
export function hasDesignComponentsFile(entries: Pick<FileEntry, 'type' | 'name'>[] | undefined | null): boolean {
  return (entries || []).some(e => e.type === 'file' && e.name === DESIGN_COMPONENTS_FILE)
}

/**
 * Parse artifacts/design/_components.json content into the components array.
 * Missing/invalid JSON or a non-array components field yields [].
 */
export function parseProjectComponentsJson(content: string): { id?: string; name?: string; [k: string]: unknown }[] {
  try {
    const parsed = JSON.parse(content) as { components?: unknown }
    return Array.isArray(parsed?.components) ? parsed.components as { id?: string; name?: string; [k: string]: unknown }[] : []
  } catch {
    return []
  }
}

/** Accessible name for a Design Studio layers-panel row. */
export function layerRowAriaLabel(opts: {
  name: string
  selected?: boolean
  locked?: boolean
  kind?: 'layer' | 'group'
  artboardIndex?: number
  artboardCount?: number
}): string {
  const kind = opts.kind === 'group' ? 'Group' : 'Layer'
  const parts = [kind, opts.name.trim() || 'Untitled']
  if (opts.locked) parts.push('locked')
  if (opts.selected) parts.push('selected')
  if ((opts.artboardCount || 1) > 1 && opts.artboardIndex != null) {
    parts.push(`artboard ${opts.artboardIndex + 1}`)
  }
  return parts.join(', ')
}

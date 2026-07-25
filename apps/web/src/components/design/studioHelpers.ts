import type { CSSProperties } from 'react'
import type { FileEntry } from '../../types'

/** Shared project component library filename under artifacts/design/. */
export const DESIGN_COMPONENTS_FILE = '_components.json'

/** localStorage keys for Design Studio desktop panel widths (Graph uses proxima.graph.*). */
export const DESIGN_LEFT_WIDTH_KEY = 'proxima.design.leftWidth'
export const DESIGN_INSPECTOR_WIDTH_KEY = 'proxima.design.inspectorWidth'

/** Default / clamp range for Design left rail and inspector (keeps canvas usable). */
export const DESIGN_PANEL_WIDTH = { fallback: 280, min: 240, max: 520 } as const

/** CSS vars for desktop Design panels; mobile sheets ignore fixed widths. */
export function designStudioPanelStyle(
  isMobile: boolean,
  leftWidth: number,
  inspectorWidth: number,
): CSSProperties | undefined {
  if (isMobile) return undefined
  return {
    ['--ds-left-width' as string]: `${leftWidth}px`,
    ['--ds-inspector-width' as string]: `${inspectorWidth}px`,
  }
}

/** When desktop resize handles should mount (hidden if collapsed or on mobile). */
export function designStudioResizeHandles(
  isMobile: boolean,
  leftCollapsed: boolean,
  rightCollapsed: boolean,
): { left: boolean; right: boolean } {
  return {
    left: !isMobile && !leftCollapsed,
    right: !isMobile && !rightCollapsed,
  }
}

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

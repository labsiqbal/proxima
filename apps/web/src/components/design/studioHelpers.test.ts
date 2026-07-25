import { describe, expect, it } from 'vitest'
import {
  DESIGN_COMPONENTS_FILE,
  DESIGN_INSPECTOR_WIDTH_KEY,
  DESIGN_LEFT_WIDTH_KEY,
  DESIGN_PANEL_WIDTH,
  designStudioPanelStyle,
  designStudioResizeHandles,
  hasDesignComponentsFile,
  layerRowAriaLabel,
  parseProjectComponentsJson,
} from './studioHelpers'

describe('hasDesignComponentsFile', () => {
  it('is false for empty or missing listings', () => {
    expect(hasDesignComponentsFile(undefined)).toBe(false)
    expect(hasDesignComponentsFile([])).toBe(false)
    expect(hasDesignComponentsFile([{ name: 'd8_abc', type: 'dir', size: 0 }])).toBe(false)
  })

  it('is true only for the components library file', () => {
    expect(hasDesignComponentsFile([
      { name: '_assets', type: 'dir', size: 0 },
      { name: DESIGN_COMPONENTS_FILE, type: 'file', size: 12 },
    ])).toBe(true)
    expect(hasDesignComponentsFile([
      { name: DESIGN_COMPONENTS_FILE, type: 'dir', size: 0 },
    ])).toBe(false)
  })
})

describe('parseProjectComponentsJson', () => {
  it('returns the components array from a valid library file', () => {
    expect(parseProjectComponentsJson(JSON.stringify({
      version: 1,
      components: [{ id: 'c1', name: 'Button' }],
    }))).toEqual([{ id: 'c1', name: 'Button' }])
  })

  it('returns [] for invalid JSON or missing components', () => {
    expect(parseProjectComponentsJson('not-json')).toEqual([])
    expect(parseProjectComponentsJson('{}')).toEqual([])
    expect(parseProjectComponentsJson(JSON.stringify({ components: 'nope' }))).toEqual([])
  })
})

describe('designStudioPanelStyle', () => {
  it('sets CSS width vars on desktop and skips on mobile', () => {
    expect(designStudioPanelStyle(true, 320, 300)).toBeUndefined()
    expect(designStudioPanelStyle(false, 320, 300)).toEqual({
      '--ds-left-width': '320px',
      '--ds-inspector-width': '300px',
    })
  })
})

describe('designStudioResizeHandles', () => {
  it('hides handles when collapsed or on mobile', () => {
    expect(designStudioResizeHandles(false, false, false)).toEqual({ left: true, right: true })
    expect(designStudioResizeHandles(false, true, false)).toEqual({ left: false, right: true })
    expect(designStudioResizeHandles(false, false, true)).toEqual({ left: true, right: false })
    expect(designStudioResizeHandles(true, false, false)).toEqual({ left: false, right: false })
  })

  it('uses stable storage keys and clamp range', () => {
    expect(DESIGN_LEFT_WIDTH_KEY).toBe('proxima.design.leftWidth')
    expect(DESIGN_INSPECTOR_WIDTH_KEY).toBe('proxima.design.inspectorWidth')
    expect(DESIGN_PANEL_WIDTH.min).toBeLessThan(DESIGN_PANEL_WIDTH.fallback)
    expect(DESIGN_PANEL_WIDTH.max).toBeGreaterThan(DESIGN_PANEL_WIDTH.fallback)
  })
})

describe('layerRowAriaLabel', () => {
  it('names plain layers with state', () => {
    expect(layerRowAriaLabel({ name: 'Shop now' })).toBe('Layer, Shop now')
    expect(layerRowAriaLabel({ name: 'Shop now', selected: true, locked: true }))
      .toBe('Layer, Shop now, locked, selected')
  })

  it('names groups and multi-artboard rows', () => {
    expect(layerRowAriaLabel({ name: 'Hero', kind: 'group' })).toBe('Group, Hero')
    expect(layerRowAriaLabel({
      name: 'Shape',
      artboardIndex: 1,
      artboardCount: 3,
    })).toBe('Layer, Shape, artboard 2')
  })

  it('falls back when the name is blank', () => {
    expect(layerRowAriaLabel({ name: '  ' })).toBe('Layer, Untitled')
  })
})

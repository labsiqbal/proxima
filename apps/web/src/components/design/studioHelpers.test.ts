import { describe, expect, it } from 'vitest'
import {
  DESIGN_COMPONENTS_FILE,
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

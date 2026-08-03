import { describe, expect, it } from 'vitest'
import {
  CLEARANCE_GAP,
  COMPOSER_DOCK_SELECTOR,
  masterTriggerClearance,
  type ClearanceRect,
} from './triggerClearance'

const VIEWPORT = 800

// A 48px trigger resting 20px above the floor, 62px in from the right edge of a
// 390px phone: the geometry the stylesheet gives it.
const trigger: ClearanceRect = {
  top: VIEWPORT - 68,
  bottom: VIEWPORT - 20,
  left: 280,
  right: 328,
  height: 48,
}

const rect = (over: Partial<ClearanceRect>): ClearanceRect => ({
  top: VIEWPORT - 120,
  bottom: VIEWPORT - 9,
  left: 9,
  right: 381,
  height: 111,
  ...over,
})

describe('masterTriggerClearance', () => {
  it('is zero with no trigger on screen', () => {
    expect(masterTriggerClearance({
      trigger: null,
      docks: [rect({})],
      viewportHeight: VIEWPORT,
    })).toBe(0)
  })

  it('is zero on a surface with no dock, so the trigger keeps its corner', () => {
    expect(masterTriggerClearance({ trigger, docks: [], viewportHeight: VIEWPORT })).toBe(0)
  })

  it('clears a bottom-docked composer by the gap', () => {
    // Dock top is 120 above the floor, so the trigger's bottom edge must sit at
    // 120 + gap.
    expect(masterTriggerClearance({ trigger, docks: [rect({})], viewportHeight: VIEWPORT }))
      .toBe(120 + CLEARANCE_GAP)
  })

  it('takes the tallest of several docks', () => {
    const clearance = masterTriggerClearance({
      trigger,
      docks: [rect({}), rect({ top: VIEWPORT - 240, height: 231 })],
      viewportHeight: VIEWPORT,
    })
    expect(clearance).toBe(240 + CLEARANCE_GAP)
  })

  it('ignores a dock that is not anchored to the bottom of the viewport', () => {
    // A composer that scrolled up the page is not in the corner's way, and
    // lifting for it would fling the trigger into the middle of the screen.
    expect(masterTriggerClearance({
      trigger,
      docks: [rect({ top: 100, bottom: 220, height: 120 })],
      viewportHeight: VIEWPORT,
    })).toBe(0)
  })

  it('ignores a dock in another column', () => {
    // Design Studio and the workflow author put their chat on the left; the
    // bottom-right corner stays free there.
    expect(masterTriggerClearance({
      trigger,
      docks: [rect({ left: 0, right: 280 })],
      viewportHeight: VIEWPORT,
    })).toBe(0)
    // Touching edges do not overlap either.
    expect(masterTriggerClearance({
      trigger,
      docks: [rect({ left: 328, right: 700 })],
      viewportHeight: VIEWPORT,
    })).toBe(0)
  })

  it('ignores a collapsed dock', () => {
    expect(masterTriggerClearance({
      trigger,
      docks: [rect({ height: 0, top: VIEWPORT - 9, bottom: VIEWPORT - 9 })],
      viewportHeight: VIEWPORT,
    })).toBe(0)
  })

  it('is zero without a measurable viewport (server / pre-layout)', () => {
    expect(masterTriggerClearance({ trigger, docks: [rect({})], viewportHeight: 0 })).toBe(0)
  })

  it('names the attribute the composers are marked with', () => {
    expect(COMPOSER_DOCK_SELECTOR).toBe('[data-composer-dock]')
  })
})
